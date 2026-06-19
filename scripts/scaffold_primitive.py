#!/usr/bin/env python3
"""Deterministic scaffold primitive — a TRUSTED, LLM-free project skeleton (ADR-0008).

A scaffold leaf (greenfield bootstrap: a new module whose interdependent files only work when mutually
consistent) is the highest-centrality node in a build — a dominator in the dependency DAG, so a single
point of failure, and greenfield is "all-or-nothing". The fix is to NOT generate the skeleton with the
LLM: instantiate a versioned template deterministically and PROVE it with an acceptance gate (import ->
smoke) BEFORE the dialectic builds the real logic on top. This is the git_ops move at the project-bootstrap
level (settledness-not-dumbing): remove the LLM from the path it makes brittle. The registry grows; the LLM
is the fallback only for an unsupported stack.

SCOPE-AWARE: our leaves build a SUB-package inside an existing repo (e.g. scope `marketplace/packaging/`),
not a new repo — so the skeleton is placed UNDER the scope dir and acceptance imports the dotted path; we
never write a root build file that would clash with the host repo's own.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def _declared_dir(text: str) -> str | None:
    """The build-target directory a text EXPLICITLY names, e.g. "a NEW directory engagement/ ONLY" or
    "under marketplace/packaging/". Returns the repo-relative path (no trailing slash) or None. This is
    authoritative over the objective's leading verb: the goal SAYS where to build, so "Create … directory
    engagement/" must scaffold into `engagement/`, never into `create/`."""
    m = re.search(r"(?:new\s+directory|directory|under|inside)\s+([A-Za-z][\w-]*(?:/[\w-]+)*)/",
                  text or "", re.IGNORECASE)
    return m.group(1).strip("/") if m else None


def _scope_base(objective: str, scope=None, goal_text: str | None = None) -> str:
    """The package directory (repo-relative, no trailing slash) to scaffold into. Priority, most specific
    first: (1) a directory named in the leaf's scope; (2) a directory the GOAL text explicitly declares
    ("NEW directory X/ ONLY") — authoritative over a leading verb; (3) a directory the objective declares;
    (4) a name derived from the objective's first word, last resort only. Deterministic — no LLM, no
    randomness. The verb fallback once turned "Create/Implement/Replace …" into `create/`/`implement/`/
    `replace/` directories; the declared-dir lookups above exist to prevent exactly that."""
    for s in (scope or []):
        s = str(s).strip().strip("/")
        if s and not s.endswith((".py", ".md", ".json", ".txt", ".toml")):   # a directory scope
            return s
    for txt in (goal_text, objective):                                       # the goal/objective's declared dir
        d = _declared_dir(txt)
        if d:
            return d
    m = re.search(r"[A-Za-z][A-Za-z0-9_]{2,}", objective or "")
    name = (m.group(0) if m else "app").lower()
    return re.sub(r"[^a-z0-9_]", "_", name).strip("_") or "app"


def _dotted(base: str) -> str:
    """Import path for a base dir, identifier-safe per segment (so `import marketplace.packaging` works)."""
    parts = []
    for seg in base.split("/"):
        seg = re.sub(r"[^a-zA-Z0-9_]", "_", seg).strip("_") or "pkg"
        parts.append(("p_" + seg) if seg[0].isdigit() else seg)
    return ".".join(parts)


# template_id -> {match: keywords, files: {relpath(with {base}/{dotted}): content}, acceptance: [argv]}
TEMPLATES: dict = {
    "python-package": {
        "match": ("python", "package", "module", "scaffold", "skeleton", "library", "cli", "harness"),
        "files": {
            "{base}/__init__.py": '"""{dotted} — deterministic scaffold (ADR-0008); fill in the real impl."""\n\n__all__: list = []\n__version__ = "0.0.0"\n',
            "{base}/__main__.py": 'import sys\n\n\ndef main(argv=None) -> int:\n    print("{dotted} ok")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
            "{base}/_smoke.py": 'import importlib\n\n\ndef check() -> bool:\n    importlib.import_module("{dotted}")\n    assert importlib.import_module("{dotted}.__main__").main([]) == 0\n    return True\n\n\nif __name__ == "__main__":\n    check()\n    print("smoke ok")\n',
        },
        "acceptance": [
            [sys.executable, "-c", "import {dotted}"],          # import (build)
            [sys.executable, "-m", "{dotted}._smoke"],          # smoke
        ],
    },
}


def match_template(objective: str, scope=None) -> str | None:
    """Deterministic stack match: the template whose keywords the objective/scope hit most (>=1). None = no
    supported template -> the caller falls back to the LLM path. No LLM, no network."""
    text = ((objective or "") + " " + " ".join(str(s) for s in (scope or []))).lower()
    best, best_hits = None, 0
    for tid, spec in TEMPLATES.items():
        hits = sum(1 for k in spec["match"] if k in text)
        if hits > best_hits:
            best, best_hits = tid, hits
    return best if best_hits >= 1 else None


def instantiate(template_id: str, target_dir, base: str) -> list[str]:
    """Materialize the template under `base` in target_dir, deterministically. Never overwrites an existing
    non-empty file (idempotent). Returns the relative paths written."""
    spec = TEMPLATES[template_id]
    params = {"base": base, "dotted": _dotted(base)}
    target = Path(target_dir)
    written = []
    for rel, content in spec["files"].items():
        p = target / rel.format(**params)
        if p.exists() and p.read_text(encoding="utf-8").strip():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content.format(**params), encoding="utf-8")
        written.append(rel.format(**params))
    return sorted(written)


def acceptance(template_id: str, target_dir, base: str) -> dict:
    """Run the template's acceptance gate (import -> smoke) at the worktree root, LLM-free. PYTHONPATH=root
    so the freshly-scaffolded package imports without an install. Returns {ok, steps:[{cmd,exit,out}]}."""
    spec = TEMPLATES[template_id]
    params = {"base": base, "dotted": _dotted(base)}
    target = Path(target_dir)
    env = {**os.environ, "PYTHONPATH": str(target) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    steps, ok = [], True
    for argv in spec["acceptance"]:
        cmd = [a.format(**params) for a in argv]
        try:
            r = subprocess.run(cmd, cwd=str(target), env=env, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as e:
            steps.append({"cmd": cmd, "exit": None, "out": f"{type(e).__name__}: {e}"})
            ok = False
            break
        steps.append({"cmd": cmd, "exit": r.returncode, "out": (r.stdout + r.stderr).strip()[-300:]})
        if r.returncode != 0:
            ok = False
            break
    return {"ok": ok, "steps": steps}


def scaffold(objective: str, target_dir, task: dict | None = None) -> dict | None:
    """The primitive: match -> instantiate-under-scope -> acceptance, deterministically (NO LLM). Returns
    {ok, template, base, dotted, files, acceptance} on a match, or None when no template fits (caller uses
    the LLM fallback). `ok` is the acceptance result — a matched template whose gate fails returns ok=False
    so the caller falls back rather than build on a broken skeleton."""
    task = task or {}
    scope = task.get("scope") or []
    tid = match_template(objective, scope)
    if tid is None:
        return None
    base = _scope_base(objective, scope, task.get("goal_text"))
    files = instantiate(tid, target_dir, base)
    gate = acceptance(tid, target_dir, base)
    return {"ok": gate["ok"], "template": tid, "base": base, "dotted": _dotted(base),
            "files": files, "acceptance": gate}


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        # a sub-package scaffold leaf -> matched, instantiated UNDER scope, acceptance PASSES (LLM-free)
        res = scaffold("scaffold the mocks harness python package", d, {"scope": ["marketplace/packaging/"]})
        assert res and res["template"] == "python-package", res
        assert res["base"] == "marketplace/packaging" and res["dotted"] == "marketplace.packaging", res
        assert res["ok"], ("acceptance gate must PASS on a fresh deterministic skeleton", res)
        assert (Path(d) / "marketplace" / "packaging" / "__init__.py").is_file()
        assert not (Path(d) / "pyproject.toml").exists(), "no root build file (would clash with host repo)"
        assert instantiate("python-package", d, "marketplace/packaging") == [], "instantiate is idempotent"
        # no supported template -> None (caller falls back to the LLM path)
        assert scaffold("integrate the stripe billing webhook with live keys", d + "/x") is None
        assert _scope_base("", ["mocks/"]) == "mocks"
        assert _scope_base("build the parser", ["a/b.py"]) == "build", "a file scope is skipped -> objective"
        # the verb-fallback bug: a verb-first leaf objective must NOT become a `create/`/`implement/` dir
        # when the goal text declares the real target directory (regression: engagement/mocks -> create/).
        assert _declared_dir("Create and own a NEW directory engagement/ ONLY.") == "engagement"
        assert _declared_dir("...a NEW directory marketplace/packaging/ ONLY.") == "marketplace/packaging"
        assert _scope_base("Create the core create package contracts.", None,
                           goal_text="...Create and own a NEW directory engagement/ ONLY.") == "engagement", \
            "goal-declared dir must win over the objective's leading verb"
        assert _scope_base("Implement the wallet and gacha engine.", ["create/__init__.py"],
                           goal_text="...a NEW directory mocks/ ONLY.") == "mocks", \
            "a file-only leaf scope falls through to the goal-declared dir, not the verb"
        assert _dotted("marketplace/2packaging") == "marketplace.p_2packaging"
    print("scaffold_primitive self-test passed (scope-aware match + deterministic instantiate + gate, no LLM).")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
