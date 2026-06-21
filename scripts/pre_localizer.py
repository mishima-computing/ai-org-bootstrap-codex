"""Deterministic pre-localizer (PLAN A, ADR-0014).

At design-time the write-scope is unknown (aufheben decides files_allowed_to_change downstream), so the
guard-map's input cannot be files_allowed_to_change. This module maps an OBJECTIVE string + a cheaply-built
RepoIndex of the TARGET repo into a ranked set of CANDIDATE touched files (the blast-radius), with a reason
per candidate. No LLM. Deterministic over sorted `git ls-files` output.

The load-bearing case: "add a live chat view to the seller dashboard" names NO literal path, yet must surface
cockpit/clay/index.html. It does via reference-graph propagation: a symbol/stem hit on seller-dashboard.js
propagates to the index.html that references it.
"""
from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", ".agent-runs", "__pycache__", ".venv", "dist", "build"}
_INDEX_CACHE: dict = {}   # (repo, HEAD) -> RepoIndex, process-local (see RepoIndex.cached)
_TEST_NAME_RE = re.compile(r"(\.test\.[jt]sx?$)|(^test_.*\.py$)|(_test\.py$)|(\.spec\.[jt]sx?$)")
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# cheap per-language symbol declarations (name capture in group 1)
_SYMBOL_RES = {
    ".js": [re.compile(r"(?:function|class)\s+([A-Za-z_$][\w$]*)"),
            re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
            re.compile(r"(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*=")],
    ".py": [re.compile(r"(?:def|class)\s+([A-Za-z_]\w*)")],
}
_SYMBOL_RES[".jsx"] = _SYMBOL_RES[".ts"] = _SYMBOL_RES[".tsx"] = _SYMBOL_RES[".mjs"] = _SYMBOL_RES[".cjs"] = _SYMBOL_RES[".js"]
# reference edges: HTML <script src>, JS import/require
_REF_RES = [re.compile(r"""<script[^>]*\bsrc\s*=\s*['"]([^'"]+)['"]"""),
            re.compile(r"""(?:import[^'"]*from|require\(|import)\s*['"]([^'"]+)['"]""")]


def _split_tokens(s: str) -> set[str]:
    out: set[str] = set()
    for raw in _TOKEN_SPLIT_RE.split(s or ""):
        if not raw:
            continue
        for piece in _CAMEL_RE.split(raw):
            p = piece.lower()
            if len(p) >= 3:
                out.add(p)
    return out


@dataclass
class Candidate:
    path: str
    score: float
    reasons: list[str] = field(default_factory=list)


class RepoIndex:
    """Cheap deterministic index of a repo: paths, per-file symbols, dir membership, reference edges."""

    def __init__(self, repo):
        self.repo = Path(repo).resolve()
        self.paths: list[str] = []
        self.basename_to_paths: dict[str, list[str]] = {}
        self.stem_to_paths: dict[str, list[str]] = {}
        self.symbol_to_paths: dict[str, list[str]] = {}
        self.dir_token_to_paths: dict[str, set[str]] = {}
        self.dir_size: dict[str, int] = {}
        self.refs_out: dict[str, set[str]] = {}   # path -> paths it references
        self.refs_in: dict[str, set[str]] = {}    # path -> paths that reference it

    @classmethod
    def build(cls, repo) -> "RepoIndex":
        idx = cls(repo)
        idx._collect_paths()
        idx._scan()
        return idx

    @classmethod
    def cached(cls, repo) -> "RepoIndex":
        """Memoised per (repo, HEAD, working-tree state) so the three design roles in one wave share ONE
        full-tree scan instead of three, WITHOUT serving a stale index when a later leaf in the same process
        has dirtied the tree under an unchanged HEAD (the index scans working-tree content, not HEAD). The
        porcelain digest is part of the key, so any tracked/untracked change forces a rebuild. Non-git trees
        are rebuilt each call. Process-local."""
        repo = Path(repo).resolve()
        try:
            head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
            porcelain = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1"],
                                       capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            head = ""
            porcelain = ""
        if not head:
            return cls.build(repo)
        import hashlib
        key = (str(repo), head, hashlib.sha256(porcelain.encode("utf-8", "replace")).hexdigest())
        if key not in _INDEX_CACHE:
            _INDEX_CACHE[key] = cls.build(repo)
        return _INDEX_CACHE[key]

    def _collect_paths(self):
        repo = self.repo
        files = []
        try:
            out = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True,
                                 text=True, timeout=30)
            if out.returncode == 0:
                files = [l for l in out.stdout.splitlines() if l]
        except (OSError, subprocess.SubprocessError):
            files = []
        if not files:   # not a git repo / git unavailable — deterministic sorted walk
            for p in sorted(repo.rglob("*")):
                if p.is_file() and not any(part in _SKIP_DIRS for part in p.relative_to(repo).parts):
                    files.append(p.relative_to(repo).as_posix())
        files = sorted(set(f for f in files if not any(part in _SKIP_DIRS for part in Path(f).parts)))
        self.paths = files
        for f in files:
            name = Path(f).name
            self.basename_to_paths.setdefault(name, []).append(f)
            for tok in _split_tokens(Path(f).stem):   # filename tokens: "seller-dashboard" -> seller, dashboard
                self.stem_to_paths.setdefault(tok, []).append(f)
            parent = str(Path(f).parent)
            self.dir_size[parent] = self.dir_size.get(parent, 0) + 1
            for tok in _split_tokens(str(Path(f).parent)):
                self.dir_token_to_paths.setdefault(tok, set()).add(f)

    def _scan(self):
        for f in self.paths:
            p = self.repo / f
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            ext = p.suffix.lower()
            for rx in _SYMBOL_RES.get(ext, []):
                for m in rx.finditer(text):
                    for tok in _split_tokens(m.group(1)):
                        self.symbol_to_paths.setdefault(tok, []).append(f)
            for rx in _REF_RES:
                for m in rx.finditer(text):
                    target = self._resolve_ref(f, m.group(1))
                    if target:
                        self.refs_out.setdefault(f, set()).add(target)
                        self.refs_in.setdefault(target, set()).add(f)

    def _resolve_ref(self, src: str, raw: str) -> str | None:
        raw = (raw or "").split("?")[0].split("#")[0].strip()
        if not raw or raw.startswith(("http://", "https://", "//")):
            return None
        base = Path(src).parent
        cand = (base / raw).as_posix().lstrip("./")
        if cand in self.paths:
            return cand
        # bare module ref (e.g. './seller-dashboard') — try basename match
        name = Path(raw).name
        for ext in ("", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
            hit = self.basename_to_paths.get(name + ext)
            if hit:
                return hit[0]
        if raw.endswith("/") or "." not in name:   # directory / bare ref -> its index.* (resolved vs base)
            dirpath = (base / raw).as_posix().strip("/").lstrip("./")
            for idxname in ("index.js", "index.ts", "index.jsx", "index.tsx", "index.html"):
                cand_idx = f"{dirpath}/{idxname}" if dirpath else idxname
                if cand_idx in self.paths:
                    return cand_idx
        return None


class PreLocalizer:
    def __init__(self, repo, index: RepoIndex | None = None):
        self.repo = Path(repo).resolve()
        self.index = index or RepoIndex.cached(self.repo)

    def candidates(self, objective: str, *, k: int = 12) -> list[Candidate]:
        idx = self.index
        toks = _split_tokens(objective)
        # literal path/filename tokens (whole paths or basenames appearing verbatim)
        literal_paths = {p for p in idx.paths if p in (objective or "")}
        literal_names = set()
        for name, paths in idx.basename_to_paths.items():
            if name in (objective or ""):
                literal_names.update(paths)

        scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}

        def add(path, pts, why):
            scores[path] = scores.get(path, 0.0) + pts
            reasons.setdefault(path, []).append(why)

        for p in literal_paths:
            add(p, 100, "literal path in objective")
        for p in literal_names - literal_paths:
            add(p, 100, "literal filename in objective")
        for tok in toks:
            for p in idx.symbol_to_paths.get(tok, []):
                add(p, 60, f"symbol '{tok}'")
            for p in idx.stem_to_paths.get(tok, []):
                add(p, 45, f"filename stem '{tok}'")
            members = idx.dir_token_to_paths.get(tok)
            if members:
                decay = 1.0 / math.sqrt(max(1, len(members)))
                for p in members:
                    add(p, 30 * decay, f"dir keyword '{tok}'")

        # reference-graph propagation: a strongly-matched file lends weight to files that reference it
        # or that it references (siblings), so a referenced index.html surfaces without a literal path.
        seeds = sorted(scores.items(), key=lambda kv: -kv[1])[:8]
        for path, _ in seeds:
            for nbr in (idx.refs_in.get(path, set()) | idx.refs_out.get(path, set())):
                add(nbr, 20, f"references/used-by {Path(path).name}")

        # tests are guards, not implementation targets: de-prioritise as a candidate but keep available
        for p in list(scores):
            if _TEST_NAME_RE.search(Path(p).name):
                add(p, -20, "test file (guard, not target)")

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        chosen = [p for p, s in ranked if s > 0][:k]

        # per-matched-dir floor: ensure the top-scoring file of each matched dir is present, so GuardScan
        # can reach a dir's guarding test even when the implementation file itself ranks below K.
        matched_dirs = {str(Path(p).parent) for p in chosen}
        for d in matched_dirs:
            in_dir = [(p, s) for p, s in ranked if str(Path(p).parent) == d and s > 0]
            if in_dir and in_dir[0][0] not in chosen:
                chosen.append(in_dir[0][0])

        return [Candidate(p, round(scores[p], 2), reasons.get(p, [])) for p in chosen]
