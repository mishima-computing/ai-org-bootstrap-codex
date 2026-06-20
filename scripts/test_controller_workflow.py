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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
