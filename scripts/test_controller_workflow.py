#!/usr/bin/env python3
"""Tests for controller_workflow's deterministic, registry-derived cross-lane forbidden set (#48).

A goal's deliverable boundary is orthogonal to infra roles' standing authority: a write-role must never
LAND nor be BLOCKED BY another role's lane. The classic failure (observed live): a `mocks/ ONLY` goal whose
implementer repair blocked because a CI-writer's legitimate `.github/workflows/*` sat in the shared repair
worktree and read as the implementer's out-of-scope deviation. The fix derives each role's cross-lane
forbidden set from the registry (not the LLM contract), so the stray is reverted before the scope check.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import controller_workflow as cw
import carrier_harness
import controller_pipeline as cp

REPO = str(Path(__file__).resolve().parents[1])   # org_root resolves the runtime registry under here


def test_deliverable_role_auto_forbids_the_ci_lane():
    cross = cw._cross_lane_forbidden(REPO, ["mocks/**"])
    assert ".github/workflows/**" in cross, ("a deliverable role must auto-forbid the CI-writer lane so a "
                                             "stray/left-over .github is reverted, not blocked on", cross)
    print("ok  deliverable role auto-forbids the CI-writer lane (.github/workflows/**)")


def test_ci_writer_keeps_its_own_lane():
    cross = cw._cross_lane_forbidden(REPO, [".github/workflows/**"])
    assert ".github/workflows/**" not in cross, ("a role must NOT strip its OWN lane — the CI-writer keeps "
                                                 "its .github authority", cross)
    print("ok  CI-writer keeps its own lane (does not strip its own .github)")


def test_non_path_lanes_are_filtered():
    # virtual output channels (e.g. aufheben's "implementation-contract") are not real paths and must not
    # become a strip glob that could shadow a real deliverable file.
    cross = cw._cross_lane_forbidden(REPO, ["mocks/**"])
    assert all("/" in g for g in cross), ("only path-like lanes are forbidden; virtual channels filtered", cross)
    print("ok  non-path (virtual-channel) lanes are filtered out")


def test_unreadable_registry_is_fail_soft():
    cross = cw._cross_lane_forbidden("/nonexistent-repo-xyz", ["mocks/**"])
    assert cross == (), ("an unreadable registry yields no cross-lane forbidden — the LLM-provided "
                         "forbidden_paths still apply; observability never breaks a run", cross)
    print("ok  unreadable registry is fail-soft (empty, not an exception)")


def test_extract_session_id():
    stream = '{"type":"thread.started","thread_id":"abc-123"}\n{"type":"token_count"}'
    assert carrier_harness.extract_session_id(stream) == "abc-123", "must parse thread_id from thread.started"
    assert carrier_harness.extract_session_id('{"type":"token_count"}\nnot json') is None, "None when absent"
    print("ok  extract_session_id parses thread_id (and None when absent)")


def test_build_codex_resume_argv_order():
    argv = carrier_harness.build_codex_resume_argv(Path("/tmp/r"), "workspace-write", "sess-9")
    # global flags BEFORE the resume subcommand, then `resume --json <id>` (the VERIFIED order)
    assert "resume" in argv and "--json" in argv and "sess-9" in argv, argv
    assert "--sandbox" in argv and "workspace-write" in argv, argv
    assert "-C" in argv and "/tmp/r" in argv, argv
    assert argv.index("resume") < argv.index("--json") < argv.index("sess-9"), argv
    assert argv.index("--sandbox") < argv.index("resume"), "global flags precede the resume subcommand"
    assert argv.index("-C") < argv.index("resume"), "global flags precede the resume subcommand"
    argv_m = carrier_harness.build_codex_resume_argv(Path("/tmp/r"), "read-only", "s", model="m")
    assert "--model" in argv_m and argv_m.index("--model") < argv_m.index("resume"), argv_m
    print("ok  build_codex_resume_argv has the verified flag order (globals, then resume --json <id>)")


def test_repair_session_reuse_gating():
    """The repair loop's gate: only the four SESSION_REUSE_ROLES resume on a repair iteration, with the
    session id their initial run returned; aufheben/linon resume None. Unit-tests the map-update + gating
    logic the loop introduces (a full pipeline test needs a real registry + carrier)."""
    assert set(cp.SESSION_REUSE_ROLES) == {"aggressive-designer", "conservative-designer",
                                           "genius", "implementer"}, cp.SESSION_REUSE_ROLES
    sessions = {"aggressive-designer": "s-agg", "conservative-designer": "s-con",
                "genius": "s-gen", "implementer": "s-impl",
                "aufheben-designer": "s-auf", "linon": "s-lin"}

    def resume_for(role):
        return sessions.get(role) if role in cp.SESSION_REUSE_ROLES else None

    for role in cp.SESSION_REUSE_ROLES:
        assert resume_for(role) == sessions[role], (role, resume_for(role))
    assert resume_for("aufheben-designer") is None, "aufheben must stay fresh (no resume)"
    assert resume_for("linon") is None, "linon must stay an independent adversary (no resume)"

    # chaining: a repair iteration UPDATES the map from the new session id, so N+1 resumes N's session
    report_dict = {"session_id": "s-agg-r1"}
    if report_dict.get("session_id"):
        sessions["aggressive-designer"] = report_dict["session_id"]
    assert resume_for("aggressive-designer") == "s-agg-r1", "next repair resumes this iteration's session"
    print("ok  repair session-reuse gating (4 roles resume, aufheben/linon fresh, chained)")


def test_repair_resume_uses_a_delta_prompt():
    # a resumed repair turn must send the DELTA prompt (so the model emits only the correction, not a full
    # regeneration — the token win of session-reuse); the initial/fresh turn keeps the full prompt.
    import json
    delta = cp._delta_prompt("implementer", "do x", {"linon": {"findings": ["fix Y"]}})
    full = cp._prompt("implementer", "do x", {"linon": {"findings": ["fix Y"]}})
    d = json.loads(delta)
    assert d.get("mode") == "repair-continuation", d
    assert "CONTINUING your own previous turn" in d.get("instruction", ""), d
    assert "repair-continuation" not in full, "the fresh/initial prompt must NOT be the delta prompt"
    # the contract picks the delta only when a session is being resumed
    entry = cp._entries(REPO)["implementer"]
    assert "repair-continuation" in cp._contract(entry, "do x", {}, resume_session="sid-1")["prompt"]
    assert "repair-continuation" not in cp._contract(entry, "do x", {})["prompt"]
    print("ok  repair resume sends a delta prompt; fresh/initial sends the full prompt")


def test_severity_weighted_repair_cap():
    # ADR-0008 addendum: the per-leaf repair allowance scales to the worst finding's severity (budget follows
    # the information's importance) — a critical finding earns more rounds; unknown severity keeps the base.
    assert cp._severity_repair_cap([{"severity": "critical"}], 3) == 6
    assert cp._severity_repair_cap([{"severity": "minor"}], 3) == 1
    assert cp._severity_repair_cap([{"severity": "minor"}, {"severity": "critical"}], 3) == 6
    assert cp._severity_repair_cap([], 3) == 3 and cp._severity_repair_cap([{"x": 1}], 3) == 3
    print("ok  severity-weighted repair cap (critical->6, minor->1, unknown/none->base)")


def test_own_deliverable_survives_a_blanket_forbidden_strip():
    # REGRESSION: a self-overlapping contract (files_allowed=["jsonpick.py"] AND files_not_allowed=["*"]) made
    # the coordination strip revert the deliverable — the blanket "*" matched jsonpick.py — leaving enforce
    # with changed:[] and the artifact silently lost. The role's OWN allowed file must win over coord_forbidden.
    import tempfile
    import subprocess
    d = Path(tempfile.mkdtemp(prefix="cw-strip-"))
    def git(*a):
        subprocess.run(["git", "-C", str(d), *a], capture_output=True)
    git("init", "-q"); git("config", "user.email", "d@l"); git("config", "user.name", "d")
    (d / "x.md").write_text("x"); git("add", "-A"); git("commit", "-qm", "init")

    contract = {
        "role": "implementer", "prompt": "build it", "sandbox": "workspace-write",
        "files_allowed_to_change": ["jsonpick.py"],
        "forbidden_paths": ["*", ".agent-runs/**"],   # the blanket "*" (aufheben's files_not_allowed) matches it
        "timeout": 60, "retries": 0,
    }

    def stub_carrier(repo, prompt, sandbox, **kw):
        (Path(repo) / "jsonpick.py").write_text("print('hi')\n")  # the legitimate deliverable
        return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}

    rep = cw.run_contract(d, contract, "t-strip", carrier_runner=stub_carrier, include_builtin_gates=False)
    assert "jsonpick.py" in (rep.changed_files or []), \
        ("the deliverable must survive the self-overlapping coordination strip", rep.to_dict())
    assert (d / "jsonpick.py").exists(), "jsonpick.py must not be reverted by the coord strip"
    print("ok  own deliverable survives a self-overlapping (allowed + blanket-forbidden) contract")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
