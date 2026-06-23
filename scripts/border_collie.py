#!/usr/bin/env python3
"""Border Collie: cheap advisory smell detectors for running AI Org goals.

This is deliberately outside controller_goal's leaf loop. It reads the org-owned
store root and stream, reconstructs branch context from stream events, and appends
targeted steering notes through GoalStore.steer.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller_goal  # noqa: E402
import goal_store  # noqa: E402


SCENT_SCAFFOLD = "scaffold-infinite-split"
SCENT_CHURN = "churn-on-underdetermined"
SCENT_GOLD = "gold-plating-the-prerequisite"
SCENT_DUAL = "dual-language-duplication"
SCENT_DOC = "documented-absence"

CHURN_THRESHOLD = 3
SCAFFOLD_THRESHOLD = 2
THETA0 = 0.5
DELTA_THETA = 1.0
TAU_MIN = 30.0

_LANG_EXTS = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".kt", ".mjs",
    ".php", ".py", ".rb", ".rs", ".ts", ".tsx",
}


@dataclass(frozen=True)
class Bark:
    goal_id: str
    node: str
    scent: str
    text: str
    strength: float = 1.0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.goal_id, self.node, self.scent)


@dataclass(frozen=True)
class Detector:
    scent: str
    scan: Callable[[list[dict], dict, Callable], list[Bark]]


PARAMS = {
    SCENT_SCAFFOLD: {"theta0": THETA0, "delta": DELTA_THETA, "tau": TAU_MIN},
    SCENT_CHURN: {"theta0": THETA0, "delta": DELTA_THETA, "tau": TAU_MIN},
    SCENT_GOLD: {"theta0": THETA0, "delta": DELTA_THETA, "tau": TAU_MIN},
    SCENT_DUAL: {"theta0": THETA0, "delta": DELTA_THETA, "tau": TAU_MIN},
    SCENT_DOC: {"theta0": THETA0, "delta": DELTA_THETA, "tau": TAU_MIN},
}


class BranchView:
    def __init__(self, events: list[dict], goal_id: str):
        self.goal_id = goal_id
        self.events = self._goal_events(events, goal_id)
        self.parent: dict[str, str] = {}
        self.children: dict[str, set[str]] = {}
        self.nodes: set[str] = set()
        self._reconstruct_tree()

    @staticmethod
    def _goal_events(events: list[dict], goal_id: str) -> list[dict]:
        direct = []
        known = {goal_id}
        for event in events:
            if event.get("goal_id") == goal_id:
                direct.append(event)
                if event.get("id"):
                    known.add(str(event.get("id")))
        changed = True
        while changed:
            changed = False
            for event in events:
                if event in direct:
                    continue
                if event.get("type") != "agent_message" or event.get("source") != "splitter":
                    continue
                parent = str(event.get("run_id") or "")
                if parent not in known:
                    continue
                direct.append(event)
                changed = True
                for child in _speech_child_ids(event.get("speech")):
                    if child not in known:
                        known.add(child)
        return direct

    def _reconstruct_tree(self) -> None:
        for e in self.events:
            typ = e.get("type")
            if typ == "agent_message" and e.get("source") == "splitter":
                parent = str(e.get("run_id") or "")
                for child in _speech_child_ids(e.get("speech")):
                    if parent and child:
                        self._add_edge(parent, child)
            elif typ in {
                "leaf_start", "leaf_split", "leaf_done", "leaf_underdetermined",
                "leaf_failed_floor", "leaf_failed_resplit_budget", "self_steer",
            }:
                node = str(e.get("id") or "")
                if node:
                    self.nodes.add(node)

    def _add_edge(self, parent: str, child: str) -> None:
        self.parent.setdefault(child, parent)
        self.children.setdefault(parent, set()).add(child)
        self.nodes.add(parent)
        self.nodes.add(child)

    def descendants(self, node: str) -> set[str]:
        out: set[str] = set()
        stack = list(self.children.get(node, set()))
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(self.children.get(cur, set()))
        return out

    def branch_nodes(self, node: str) -> set[str]:
        return {node} | self.descendants(node)

    def landed_in_branch(self, node: str) -> bool:
        branch = self.branch_nodes(node)
        return any(e.get("type") == "leaf_done" and e.get("id") in branch for e in self.events)

    def failed_terminal(self, node: str) -> bool:
        branch = self.branch_nodes(node)
        return any(
            e.get("type") in {"leaf_failed_floor", "leaf_failed_resplit_budget"} and e.get("id") in branch
            for e in self.events
        )

    def lca(self, nodes: Iterable[str]) -> str | None:
        chains = [self._chain(n) for n in nodes if n]
        if not chains:
            return None
        common = set(chains[0])
        for chain in chains[1:]:
            common &= set(chain)
        if not common:
            return None
        for node in chains[0]:
            if node in common and node != self.goal_id:
                return node
        return None

    def _chain(self, node: str) -> list[str]:
        out = [node]
        seen = {node}
        while node in self.parent:
            node = self.parent[node]
            if node in seen:
                break
            seen.add(node)
            out.append(node)
        return out


def scan(events: list[dict], goal_record: dict, git_show_fn: Callable, scents: set[str] | None = None) -> list[Bark]:
    """Pure detector entry point used by the daemon and by replay tests."""
    owned = set(scents) if scents else None
    out: list[Bark] = []
    for detector in CATALOG:
        if owned is not None and detector.scent not in owned:
            continue
        out.extend(detector.scan(events, goal_record, git_show_fn))
    return _dedupe_barks(out)


def _scan_scaffold(events: list[dict], goal_record: dict, git_show_fn: Callable) -> list[Bark]:
    del git_show_fn
    goal_id = str(goal_record.get("goal_id") or "")
    raw_goal = str(goal_record.get("goal") or "")
    view = BranchView(events, goal_id)
    if not re.search(r"\b(scaffold|minimal|skeleton)\b", raw_goal, re.I):
        candidate_ids = [n for n in view.nodes if re.search(r"\b(scaffold|minimal|skeleton)\b", n, re.I)]
    else:
        candidate_ids = sorted(view.nodes)
    barks: list[Bark] = []
    for node in candidate_ids:
        split_count = sum(1 for e in view.events if e.get("type") == "leaf_split" and e.get("id") == node)
        if split_count >= SCAFFOLD_THRESHOLD and not view.landed_in_branch(node):
            barks.append(_bark(
                goal_id, node, SCENT_SCAFFOLD,
                "This branch keeps re-splitting around a scaffold/minimal/skeleton shape without landing. "
                "Treat the current floor as implementation work: build the smallest real slice or fail "
                "honestly instead of re-declaring a skeleton.",
                strength=split_count - SCAFFOLD_THRESHOLD + 1,
            ))
    return barks


def _scan_churn(events: list[dict], goal_record: dict, git_show_fn: Callable) -> list[Bark]:
    del git_show_fn
    goal_id = str(goal_record.get("goal_id") or "")
    view = BranchView(events, goal_id)
    barks: list[Bark] = []
    for node in sorted(view.nodes):
        raw_splits = [i for i, e in enumerate(view.events) if e.get("type") == "leaf_split" and e.get("id") == node]
        sanctioned = 0
        for idx in raw_splits:
            prev = view.events[idx - 1] if idx > 0 else {}
            if prev.get("type") == "self_steer" and prev.get("id") == node:
                sanctioned += 1
        real_churn = len(raw_splits) - sanctioned
        if real_churn >= CHURN_THRESHOLD and not view.landed_in_branch(node) and not view.failed_terminal(node):
            barks.append(_bark(
                goal_id, node, SCENT_CHURN,
                "This branch is re-splitting repeatedly without a landed leaf. If the work is "
                "underdetermined, park it with a concrete ask; otherwise land one narrow child before "
                "splitting again.",
                strength=real_churn - CHURN_THRESHOLD + 1,
            ))
    return barks


def _scan_gold(events: list[dict], goal_record: dict, git_show_fn: Callable) -> list[Bark]:
    goal_id = str(goal_record.get("goal_id") or "")
    view = BranchView(events, goal_id)
    tokens = _deliverable_tokens(str(goal_record.get("goal") or ""))
    if not tokens:
        return []
    lands = _landed_touch_sets(view.events, git_show_fn)
    if len(lands) < 2:
        return []
    all_touched = {p for land in lands for p in land["paths"]}
    if _touches_deliverable(all_touched, tokens):
        return []
    involved: set[str] = set()
    for i, left in enumerate(lands):
        for right in lands[i + 1:]:
            if left["paths"] and right["paths"] and left["paths"] & right["paths"]:
                involved.update([left["node"], right["node"]])
    if len(involved) < 2:
        return []
    latest = next((land["node"] for land in reversed(lands) if land["node"] in involved), sorted(involved)[-1])
    target = view.lca(involved) or latest
    return [_bark(
        goal_id, target, SCENT_GOLD,
        "Multiple landed leaves are revisiting the same prerequisite files while the raw goal's named "
        f"deliverable area ({', '.join(sorted(tokens))}) has not been touched. Land the deliverable slice "
        "before polishing prerequisite structure further.",
        strength=len(involved) - 1,
    )]


def _scan_dual_language(events: list[dict], goal_record: dict, git_show_fn: Callable) -> list[Bark]:
    goal_id = str(goal_record.get("goal_id") or "")
    view = BranchView(events, goal_id)
    known: set[str] = set()
    first_bark: Bark | None = None
    mirror_stems: set[str] = set()
    for e in view.events:
        if e.get("type") != "leaf_done" or not e.get("commit"):
            continue
        info = _git_info(git_show_fn(e.get("commit")))
        existing = set(info["existing"]) | known
        for path in sorted(info["added"] or info["paths"]):
            if _mirror_language_path(path, existing):
                mirror_stems.add(Path(path).stem.lower())
                if first_bark is None:
                    first_bark = _bark(
                        goal_id, str(e.get("id")), SCENT_DUAL,
                        f"`{path}` mirrors an existing file stem in another language. Reuse or route through "
                        "the existing implementation path instead of maintaining duplicated language copies.",
                    )
        known.update(info["paths"])
    if first_bark is None:
        return []
    return [_with_strength(first_bark, len(mirror_stems))]


def _scan_documented_absence(events: list[dict], goal_record: dict, git_show_fn: Callable) -> list[Bark]:
    goal_id = str(goal_record.get("goal_id") or "")
    view = BranchView(events, goal_id)
    first_bark: Bark | None = None
    absent_symbols: set[str] = set()
    for e in view.events:
        if e.get("type") != "leaf_done" or not e.get("commit"):
            continue
        info = _git_info(git_show_fn(e.get("commit")))
        for path, content in sorted(info["contents"].items()):
            for line in str(content).splitlines():
                if not re.search(r"(forward work|todo)", line, re.I) or not re.search(r"\bgate\b", line, re.I):
                    continue
                symbol = _claimed_gate_symbol(line)
                if symbol and not _symbol_present(symbol, info):
                    absent_symbols.add(symbol)
                    if first_bark is None:
                        first_bark = _bark(
                            goal_id, str(e.get("id")), SCENT_DOC,
                            f"`{path}` documents a future gate `{symbol}`, but the symbol is absent from code. "
                            "Wire the gate now or remove the claim so the run does not ship documented absence.",
                        )
    if first_bark is None:
        return []
    return [_with_strength(first_bark, len(absent_symbols))]


CATALOG = [
    Detector(SCENT_SCAFFOLD, _scan_scaffold),
    Detector(SCENT_CHURN, _scan_churn),
    Detector(SCENT_GOLD, _scan_gold),
    Detector(SCENT_DUAL, _scan_dual_language),
    Detector(SCENT_DOC, _scan_documented_absence),
]


def run_once(root: str | Path, *, scents: set[str] | None = None, instance: str = "default") -> list[Bark]:
    root = normalize_root(root)
    return patrol_events(root, read_stream(root), scents=scents, instance=instance)


def patrol_events(root: str | Path, events: list[dict], *, scents: set[str] | None = None,
                  instance: str = "default") -> list[Bark]:
    root = normalize_root(root)
    store = goal_store.GoalStore(str(root), emit=controller_goal.stream_emit(str(root)))
    applied: list[Bark] = []
    selected = owned_scents(scents, instance)
    for rec in store.find(status="running"):
        barks = scan(events, rec, git_query(root), selected)
        applied.extend(apply_barks(root, store, barks, instance=instance))
    return applied


def apply_barks(root: str | Path, store, barks: list[Bark], *, instance: str,
                now: _dt.datetime | None = None) -> list[Bark]:
    root = normalize_root(root)
    now = _coerce_time(now) if now is not None else _dt.datetime.now(_dt.timezone.utc)
    history = read_bark_history(root)
    applied: list[Bark] = []
    for bark in barks:
        unit_history = history.get(bark.key, [])
        params = PARAMS.get(bark.scent, {"theta0": THETA0, "delta": DELTA_THETA, "tau": TAU_MIN})
        threshold = current_threshold(unit_history, now, **params)
        if float(bark.strength) < threshold:
            continue
        text = escalate_text(bark, unit_history, now=now)
        applied_bark = Bark(bark.goal_id, bark.node, bark.scent, text, bark.strength)
        entry = store.steer(bark.goal_id, text, target=bark.node)
        if entry is None:
            continue
        append_bark(root, applied_bark, instance=instance, seq=entry.get("seq"), now=now)
        history.setdefault(bark.key, []).append((now, float(bark.strength)))
        applied.append(applied_bark)
    return applied


def read_stream(root: str | Path) -> list[dict]:
    root = normalize_root(root)
    path = root / ".agent-runs" / "stream.jsonl"
    events: list[dict] = []
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except json.JSONDecodeError:
            continue
    return events


def read_stream_since(root: str | Path, offset: int) -> tuple[list[dict], int]:
    root = normalize_root(root)
    path = root / ".agent-runs" / "stream.jsonl"
    if not path.is_file():
        return [], offset
    size = path.stat().st_size
    if offset > size:
        offset = 0
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        f.seek(offset)
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except json.JSONDecodeError:
                continue
        new_offset = f.tell()
    return events, new_offset


def current_threshold(bark_times_strengths, now, *, theta0, delta, tau) -> float:
    theta = float(theta0)
    prev = None
    now = _coerce_time(now)
    for ts, _strength in sorted(_history_entries(bark_times_strengths), key=lambda item: item[0]):
        if prev is not None:
            theta = _decay_threshold(theta, prev, ts, theta0=float(theta0), tau=float(tau))
        theta += float(delta)
        prev = ts
    if prev is not None:
        theta = _decay_threshold(theta, prev, now, theta0=float(theta0), tau=float(tau))
    return theta


def read_bark_history(root: str | Path) -> dict[tuple[str, str, str], list[tuple[_dt.datetime, float]]]:
    sidecar = normalize_root(root) / ".agent-runs" / "border_collie"
    history: dict[tuple[str, str, str], list[tuple[_dt.datetime, float]]] = {}
    for path in sorted(sidecar.glob("barks.*.jsonl")) if sidecar.is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            goal = rec.get("goal")
            node = rec.get("node")
            scent = rec.get("scent")
            ts = _parse_time(rec.get("ts"))
            if not goal or not node or not scent or ts is None:
                continue
            try:
                strength = float(rec.get("strength", 1.0))
            except (TypeError, ValueError):
                strength = 1.0
            history.setdefault((str(goal), str(node), str(scent)), []).append((ts, strength))
    for entries in history.values():
        entries.sort(key=lambda item: item[0])
    return history


def read_bark_keys(root: str | Path) -> set[tuple[str, str, str]]:
    return set(read_bark_history(root))


def escalate_text(bark: Bark, history: list[tuple[_dt.datetime, float]],
                  now: _dt.datetime | None = None) -> str:
    if not history:
        return bark.text
    now = _coerce_time(now) if now is not None else _dt.datetime.now(_dt.timezone.utc)
    previous_ts, previous_strength = sorted(history, key=lambda item: item[0])[-1]
    alert_no = len(history) + 1
    elapsed = _format_elapsed(now - previous_ts)
    verb = "intensified" if float(bark.strength) > previous_strength else "still unresolved"
    body = _strip_scent_prefix(bark.text, bark.scent)
    return (
        f"[{bark.scent}] ESCALATION (alert #{alert_no}, ~{elapsed} unaddressed, "
        f"{verb} {_format_strength(previous_strength)}->{_format_strength(bark.strength)}): "
        f"{body} This has now persisted/worsened through prior steering - park it with a concrete ask "
        "or fail honestly; do not re-split again."
    )


def append_bark(root: str | Path, bark: Bark, *, instance: str, seq=None,
                now: _dt.datetime | None = None) -> Path:
    sidecar = normalize_root(root) / ".agent-runs" / "border_collie"
    sidecar.mkdir(parents=True, exist_ok=True)
    path = sidecar / f"barks.{_partition(instance)}.jsonl"
    now = _coerce_time(now) if now is not None else _dt.datetime.now(_dt.timezone.utc)
    rec = {
        "ts": _format_time(now),
        "goal": bark.goal_id,
        "node": bark.node,
        "scent": bark.scent,
        "strength": bark.strength,
        "seq": seq,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def git_query(root: Path) -> Callable[[str], dict]:
    cache: dict[str, dict] = {}

    def query(commit: str) -> dict:
        if not commit:
            return {"paths": [], "added": [], "existing": [], "contents": {}, "repo_symbols": set()}
        if commit in cache:
            return cache[commit]
        paths = _git_lines(root, "show", "--name-only", "--format=", str(commit))
        status = _git_lines(root, "diff-tree", "--no-commit-id", "--name-status", "-r", str(commit))
        added = []
        for line in status:
            parts = line.split("\t")
            if parts and parts[0] == "A" and len(parts) >= 2:
                added.append(parts[-1])
        existing = _git_lines(root, "ls-tree", "-r", "--name-only", f"{commit}^")
        contents = {}
        for path in paths[:100]:
            if _is_textish(path):
                cp = subprocess.run(
                    ["git", "-C", str(root), "show", f"{commit}:{path}"],
                    capture_output=True, text=True, errors="replace",
                )
                if cp.returncode == 0:
                    contents[path] = cp.stdout
        repo_symbols = _repo_symbols(root, commit)
        cache[commit] = {"paths": paths, "added": added, "existing": existing,
                         "contents": contents, "repo_symbols": repo_symbols}
        return cache[commit]

    return query


def owned_scents(scents: set[str] | None, instance: str) -> set[str]:
    catalog = {d.scent for d in CATALOG}
    if scents:
        return set(scents) & catalog
    m = re.fullmatch(r"(\d+)/(\d+)", str(instance).strip())
    if not m:
        return catalog
    idx, total = int(m.group(1)), int(m.group(2))
    if total <= 0 or idx < 0 or idx >= total:
        raise ValueError("--instance hash form must be i/N with 0 <= i < N")
    return {s for s in catalog if _stable_hash(s) % total == idx}


def normalize_root(root: str | Path | None = None) -> Path:
    if root is None:
        stream = os.environ.get("STREAM_LOG")
        if stream:
            return normalize_root(stream)
        return Path.cwd().resolve()
    p = Path(root).expanduser().resolve()
    if p.name == "stream.jsonl" and p.parent.name == ".agent-runs":
        return p.parent.parent
    if p.name == ".agent-runs":
        return p.parent
    return p


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the Border Collie advisory smell-detector pack.")
    parser.add_argument("--repo", default=None, help="store root/repo containing .agent-runs")
    parser.add_argument("--store-root", default=None, help="alias for --repo; may also point at .agent-runs")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--scents", default="", help="comma-separated scent subset")
    parser.add_argument("--instance", default="default", help="sidecar partition name, or i/N hash shard")
    parser.add_argument("--once", action="store_true", help="scan once and exit")
    args = parser.parse_args(argv)
    root = normalize_root(args.store_root or args.repo)
    scents = {s.strip() for s in args.scents.split(",") if s.strip()} or None
    events: list[dict] = []
    cursor = 0
    while True:
        new_events, cursor = read_stream_since(root, cursor)
        events.extend(new_events)
        patrol_events(root, events, scents=scents, instance=args.instance)
        if args.once:
            return 0
        time.sleep(max(args.interval, 0.1))


def _speech_child_ids(speech) -> list[str]:
    if isinstance(speech, dict) and isinstance(speech.get("_preview"), str):
        try:
            speech = json.loads(speech["_preview"])
        except json.JSONDecodeError:
            return []
    if not isinstance(speech, list):
        return []
    return [str(t.get("id")) for t in speech if isinstance(t, dict) and t.get("id")]


def _bark(goal_id: str, node: str, scent: str, text: str, *, strength: float = 1.0) -> Bark:
    return Bark(goal_id=str(goal_id), node=str(node), scent=scent, text=f"[{scent}] {text}",
                strength=float(strength))


def _with_strength(bark: Bark, strength: float) -> Bark:
    return Bark(bark.goal_id, bark.node, bark.scent, bark.text, float(strength))


def _dedupe_barks(barks: list[Bark]) -> list[Bark]:
    out: dict[tuple[str, str, str], Bark] = {}
    order: list[tuple[str, str, str]] = []
    for bark in barks:
        if bark.key not in out:
            order.append(bark.key)
            out[bark.key] = bark
            continue
        if float(bark.strength) > float(out[bark.key].strength):
            out[bark.key] = bark
    return [out[key] for key in order]


def _history_entries(values) -> list[tuple[_dt.datetime, float]]:
    entries: list[tuple[_dt.datetime, float]] = []
    for item in values or []:
        if isinstance(item, (list, tuple)) and item:
            ts = _parse_time(item[0])
            strength = item[1] if len(item) > 1 else 1.0
        else:
            ts = _parse_time(item)
            strength = 1.0
        if ts is None:
            continue
        try:
            strength = float(strength)
        except (TypeError, ValueError):
            strength = 1.0
        entries.append((ts, strength))
    return entries


def _decay_threshold(theta: float, previous: _dt.datetime, current: _dt.datetime, *,
                     theta0: float, tau: float) -> float:
    if math.isinf(tau):
        return theta
    if tau <= 0:
        return theta0
    elapsed_min = (current - previous).total_seconds() / 60.0
    return theta0 + (theta - theta0) * math.exp(-(elapsed_min / tau))


def _parse_time(value) -> _dt.datetime | None:
    if isinstance(value, _dt.datetime):
        return _coerce_time(value)
    if not isinstance(value, str) or not value:
        return None
    raw = value
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return _coerce_time(_dt.datetime.fromisoformat(raw))
    except ValueError:
        return None


def _coerce_time(value: _dt.datetime) -> _dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc)


def _format_time(value: _dt.datetime) -> str:
    return _coerce_time(value).isoformat().replace("+00:00", "Z")


def _format_elapsed(delta: _dt.timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    minutes = seconds // 60
    if minutes < 1:
        return f"{seconds}s"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem = minutes % 60
    if rem == 0:
        return f"{hours}h"
    return f"{hours}h {rem}m"


def _format_strength(value: float) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _strip_scent_prefix(text: str, scent: str) -> str:
    prefix = f"[{scent}] "
    if text.startswith(prefix):
        return text[len(prefix):]
    return text


def _landed_touch_sets(events: list[dict], git_show_fn: Callable) -> list[dict]:
    lands = []
    for e in events:
        if e.get("type") != "leaf_done":
            continue
        commit = e.get("commit")
        paths = set()
        if commit:
            paths = set(_git_info(git_show_fn(commit))["paths"])
        lands.append({"node": str(e.get("id")), "paths": paths})
    return lands


def _git_info(raw) -> dict:
    if raw is None:
        raw = {}
    if isinstance(raw, dict):
        paths = _string_list(raw.get("paths") or raw.get("name_only") or [])
        return {
            "paths": paths,
            "added": _string_list(raw.get("added") or []),
            "existing": _string_list(raw.get("existing") or []),
            "contents": dict(raw.get("contents") or {}),
            "repo_symbols": set(raw.get("repo_symbols") or raw.get("defined_symbols") or []),
        }
    paths = _string_list(raw)
    return {"paths": paths, "added": paths, "existing": [], "contents": {}, "repo_symbols": set()}


def _string_list(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v)]
    if isinstance(value, str):
        return [value] if value else []
    return []


def _deliverable_tokens(goal: str) -> set[str]:
    tokens: set[str] = set()
    for tok in re.findall(r"[\w./-]+\.[A-Za-z0-9]+", goal):
        tokens.add(tok.strip("`'\""))
    for tok in re.findall(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"", goal):
        val = next((x for x in tok if x), "")
        if val and len(val) >= 3:
            tokens.add(val)
    m = re.search(r"\b(?:add|build|implement|create|update)\s+(?:an?\s+)?([A-Z][A-Za-z0-9_-]+)", goal)
    if m:
        tokens.add(m.group(1))
    return {t for t in tokens if t and t.lower() not in {"the", "goal"}}


def _touches_deliverable(paths: set[str], tokens: set[str]) -> bool:
    lowered = [p.lower() for p in paths]
    for token in tokens:
        t = token.lower().strip()
        if not t:
            continue
        if any(t in p or Path(p).name.lower() == t for p in lowered):
            return True
    return False


def _mirror_language_path(path: str, existing_paths: set[str]) -> bool:
    p = Path(path)
    if p.suffix.lower() not in _LANG_EXTS:
        return False
    stem = p.stem.lower()
    for other in existing_paths:
        q = Path(other)
        if q == p or q.suffix.lower() == p.suffix.lower() or q.suffix.lower() not in _LANG_EXTS:
            continue
        if q.stem.lower() == stem:
            return True
    return False


def _claimed_gate_symbol(line: str) -> str | None:
    for pattern in (r"`([A-Za-z_][\w.-]*)`", r"\b([A-Za-z_][A-Za-z0-9_]*Gate)\b",
                    r"\b([A-Za-z_][A-Za-z0-9_]*_gate)\b", r"\bgate\s+([A-Za-z_][\w.-]*)"):
        m = re.search(pattern, line)
        if m and m.group(1).lower() not in {"gate", "the", "a"}:
            return m.group(1)
    return None


def _symbol_present(symbol: str, info: dict) -> bool:
    if symbol in info["repo_symbols"]:
        return True
    needle = re.compile(rf"\b{re.escape(symbol)}\b")
    return any(needle.search(str(content)) and not re.search(r"(forward work|todo)", str(content), re.I)
               for content in info["contents"].values())


def _git_lines(root: Path, *args: str) -> list[str]:
    cp = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if cp.returncode != 0:
        return []
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def _repo_symbols(root: Path, commit: str) -> set[str]:
    cp = subprocess.run(["git", "-C", str(root), "grep", "-nE", r"(def |class |function |const |let |var ).*",
                         str(commit), "--", "*.py", "*.js", "*.ts", "*.tsx"],
                        capture_output=True, text=True)
    if cp.returncode not in (0, 1):
        return set()
    symbols: set[str] = set()
    for line in cp.stdout.splitlines():
        m = re.search(r"\b(?:def|class|function|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            symbols.add(m.group(1))
    return symbols


def _is_textish(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in {"", ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml"}


def _stable_hash(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)


def _partition(instance: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(instance)).strip(".-") or "default"


if __name__ == "__main__":
    raise SystemExit(main())
