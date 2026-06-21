"""Python-cored design-role host (PLAN A, ADR-0014) — the shared spine for genius / aggressive-designer /
conservative-designer.

"Codex on genius.py", not "Codex as genius": Python owns a deterministic spine (pre-localize -> guard-scan
-> guard-map carriage -> validate), the carrier (Codex) is called for JUDGMENT only. The host is wired as a
`carrier_runner` into controller_workflow.run_contract, so workflow keeps owning journaling, scope, the
output-schema gate, and ControllerRunReport (the controller's producer contract is unchanged).

Determinism is at INVOCATION: the guard-map is built and folded into the carrier prompt BEFORE the carrier
runs — never a discretionary tool call. The host re-gates internally only to drive its schema-retry; the
authoritative gate remains workflow's output_gate over the same repo/result.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pre_localizer

OUTPUT_FILE = "result.json"
DESIGN_ROLES = ("genius", "aggressive-designer", "conservative-designer")
GUARD_MAP_HEADER = "## GUARD-MAP (deterministic — the existing law that binds the files in scope)"
_ASSERT_HINT_RE = re.compile(r"assert|indexOf|\.match\(|expect\(|\.ok\(|toBe|toEqual|toContain")
_ALIAS_RE_TMPL = r"([A-Za-z_$][\w$]*)\s*=\s*[^=\n]*['\"][^'\"]*{name}['\"]"
_EXPORT_RES = [
    re.compile(r"\bmodule\.exports(?:\.[A-Za-z_$][\w$]*)?\s*="),
    re.compile(r"\bexports\.([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"\b(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"^\s*export\s+(?:default|const|function|class|\{)", re.MULTILINE),
    re.compile(r"^\s*__all__\s*=", re.MULTILINE),
]


# --------------------------------------------------------------------------- deterministic guard scan
class GuardScan:
    def __init__(self, repo, candidate_paths):
        self.repo = Path(repo).resolve()
        self.targets = sorted(set(candidate_paths))

    def build(self) -> dict:
        tests = self._guarding_tests()
        adrs = self._governing_adrs()
        exports = self._protected_exports()
        return {
            "target_files": self.targets,
            "guarding_tests": tests,
            "governing_adrs": adrs,
            "protected_exports": exports,
            "summary": (f"{len(tests)} test(s) pin these files; {len(adrs)} ADR/doc(s) govern them; "
                        f"{sum(len(e['symbols']) for e in exports)} protected export(s)."),
        }

    def _iter_test_files(self):
        for p in sorted(self.repo.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.repo)
            if any(part in pre_localizer._SKIP_DIRS for part in rel.parts):
                continue
            if pre_localizer._TEST_NAME_RE.search(p.name):
                yield p, rel.as_posix()

    def _guarding_tests(self):
        out = []
        for p, rel in self._iter_test_files():
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lines = text.splitlines()
            for target in self.targets:
                base = Path(target).name
                # a test guards the target if it references the target's FULL path or (fallback) basename
                full = target in text
                if not full and base not in text:
                    continue
                aliases = set(re.findall(_ALIAS_RE_TMPL.format(name=re.escape(base)), text))
                needles = {base, target} | aliases
                pins = []
                for i, ln in enumerate(lines):
                    if _ASSERT_HINT_RE.search(ln) and any(n in ln for n in needles):
                        pins.append(f"{i+1}: {ln.strip()[:180]}")
                out.append({"test": rel, "guards": target,
                            "match": "full-path" if full else "basename",
                            "pins": pins[:8]})
        return sorted(out, key=lambda d: (d["test"], d["guards"]))

    def _governing_adrs(self):
        out = []
        docs = self.repo / "docs"
        if not docs.exists():
            return out
        cand_needles = set(self.targets) | {str(Path(t).parent) for t in self.targets if "/" in t}
        for p in sorted(docs.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hits = sorted({n for n in cand_needles if n and n in text})
            # relevance-scoped: require a CANDIDATE path/dir hit, not a bare basename anywhere
            if hits:
                out.append({"doc": p.relative_to(self.repo).as_posix(), "governs": hits[:5]})
        return sorted(out, key=lambda d: d["doc"])

    def _protected_exports(self):
        out = []
        for target in self.targets:
            p = self.repo / target
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            syms = []
            for i, ln in enumerate(text.splitlines()):
                if any(rx.search(ln) for rx in _EXPORT_RES):
                    syms.append(f"{i+1}: {ln.strip()[:120]}")
            if syms:
                out.append({"file": target, "symbols": syms[:8]})
        return out


# --------------------------------------------------------------------------- prompt + schema-valid carriage
def format_guard_section(guard_map: dict) -> str:
    return "\n\n".join([
        GUARD_MAP_HEADER,
        "These existing tests/ADRs already bind the files this objective touches. Honor them or the "
        "reviewer (Linon) rejects the result: do NOT reformat or reorder what a test pins, do NOT clobber "
        "a protected export, do NOT violate a governing ADR.",
        "```json\n" + json.dumps(guard_map, indent=2, ensure_ascii=False) + "\n```",
    ])


def inject_guard_evidence(packet: dict, guard_map: dict, guard_rel_path: str, role_id: str,
                          *, cap: int = 8) -> dict:
    """Carry the guard-map into the packet as SCHEMA-VALID evidence (D5/E). Hybrid: the full map lives on
    disk (guard_rel_path); the packet gets one artifact pointer + capped distilled pointers. Genius uses
    repo_evidence items {ref_type,locator,summary}; the designers use the string arrays they actually have
    (constraints / things_to_avoid). Never adds an unknown key, so the packet stays schema-valid."""
    if not isinstance(packet, dict):
        return packet
    findings = _distill(guard_map, cap)
    if role_id == "genius":
        ev = packet.get("repo_evidence")
        if not isinstance(ev, list):
            ev = packet["repo_evidence"] = []
        if not any(isinstance(x, dict) and x.get("locator") == guard_rel_path for x in ev):
            ev.append({"ref_type": "run_artifact", "locator": guard_rel_path,
                       "summary": "deterministic pre-localizer + guard scan: " + guard_map.get("summary", "")[:340]})
        for f in findings:
            ev.append({"ref_type": f["ref_type"], "locator": f["locator"][:400], "summary": f["summary"][:400]})
    else:  # aggressive/conservative-designer: string arrays, no generic evidence array
        ta = packet.get("things_to_avoid")
        if not isinstance(ta, list):
            ta = packet["things_to_avoid"] = []
        ta.append(f"GUARD (see {guard_rel_path}): {guard_map.get('summary','')}"[:600])
        for f in findings:
            ta.append(f"GUARD: {f['summary']}"[:600])
    return packet


def _distill(guard_map: dict, cap: int) -> list[dict]:
    out = []
    for t in guard_map.get("guarding_tests", []):
        pin = (t.get("pins") or [""])[0]
        out.append({"ref_type": "run_artifact", "locator": t["test"],
                    "summary": f"guards {t['guards']} ({t['match']}); pin {pin}"})
    for a in guard_map.get("governing_adrs", []):
        out.append({"ref_type": "official_spec", "locator": a["doc"],
                    "summary": "governs " + ", ".join(a.get("governs", []))})
    for e in guard_map.get("protected_exports", []):
        sym = (e.get("symbols") or [""])[0]
        out.append({"ref_type": "repo_pointer", "locator": e["file"],
                    "summary": f"protected export — do not clobber: {sym}"})
    return out[:cap]


# --------------------------------------------------------------------------- carrier seam + the runner
def _default_carrier(repo, prompt, sandbox, *, timeout, retries, out_dir, output_file, resume_session):
    import carrier_harness
    return carrier_harness.run_carrier(repo, prompt, sandbox, timeout=timeout, retries=retries,
                                       out_dir=out_dir, output_file=output_file, resume_session=resume_session)


def make_design_carrier_runner(repo, role_id, schema_path, objective, *, carrier=None, max_attempts=3):
    """Return a carrier_runner with the signature controller_workflow.run_contract expects:
        runner(repo, prompt, sandbox, *, timeout, retries, out_dir, resume_session) -> carrier dict.
    `objective` is the RAW task text (captured before role.md injection) so pre-localization is not polluted
    by the role description. `carrier` is injectable for tests; defaults to carrier_harness.run_carrier."""
    import controller_pipeline  # lazy: for _read_result / _salvage_json salvage
    carrier = carrier or _default_carrier
    schema_path = str(schema_path)

    def runner(rp, prompt, sandbox, *, timeout=600, retries=1, out_dir=None, resume_session=None):
        rp = Path(rp).resolve()
        out_dir = Path(out_dir) if out_dir else (rp / ".agent-runs" / "carrier")
        out_dir.mkdir(parents=True, exist_ok=True)
        result_file = rp / OUTPUT_FILE

        candidates = pre_localizer.PreLocalizer(rp).candidates(objective)
        guard_map = GuardScan(rp, [c.path for c in candidates]).build()
        guard_map["candidates"] = [{"path": c.path, "score": c.score, "reasons": c.reasons} for c in candidates]
        guard_file = out_dir / "guard-map.json"
        guard_file.write_text(json.dumps(guard_map, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            guard_rel = guard_file.resolve().relative_to(rp).as_posix()
        except ValueError:
            guard_rel = str(guard_file)

        base_prompt = format_guard_section(guard_map) + "\n\n---\n\n" + prompt
        # ONE loop absorbs transport-empty AND schema-fail (each launch uses retries=0 so the harness
        # transport-retry does not compound with this schema-retry). attempts are aggregated; logs go to
        # per-attempt subdirs so they do not overwrite. A final failure returns ok=False so a rejected
        # packet is never cache-stored as a success (run_contract's output_gate is still the authority).
        session = resume_session
        all_attempts: list = []
        last_reason = None
        cr: dict = {"ok": False}
        for attempt in range(max_attempts):
            try:
                result_file.unlink()              # no stale-output pass
            except FileNotFoundError:
                pass
            send = base_prompt if attempt == 0 else (base_prompt + _repair_note(last_reason))
            cr = carrier(rp, send, sandbox, timeout=timeout, retries=0,
                         out_dir=out_dir / f"attempt{attempt}", output_file=result_file,
                         resume_session=session)
            session = cr.get("session_id") or session
            for a in (cr.get("attempts") or []):   # tag with the outer schema attempt so indices don't collide
                all_attempts.append({**a, "schema_attempt": attempt} if isinstance(a, dict) else a)
            if not cr.get("ok"):
                last_reason = ["carrier produced no usable output (transport/timeout)"]
                continue                          # transient — re-launch (absorbs the empty-output case)
            packet = controller_pipeline._read_result(result_file)
            if packet is None:
                last_reason = ["output was empty or unsalvageable JSON"]
                continue
            packet = inject_guard_evidence(packet, guard_map, guard_rel, role_id)
            result_file.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            verdict = _gate(json.dumps(packet), schema_path)
            if verdict.get("output_ok"):
                return {**cr, "ok": True, "attempts": all_attempts, "session_id": session}
            last_reason = verdict.get("errors", [])
        return {**cr, "ok": False, "attempts": all_attempts, "session_id": session,
                "schema_errors": last_reason or []}

    return runner


def _gate(text, schema_path) -> dict:
    import controller_output
    return controller_output.gate_output(text, schema_path)


def _repair_note(errors) -> str:
    return ("\n\n## PRIOR OUTPUT REJECTED (deterministic schema gate)\nRe-emit the FULL packet, fixing "
            "exactly these and keeping the guard-map honored:\n- " + "\n- ".join(errors or []))
