from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

import ai_org.codex_reset as codex_reset
from ai_org import contributor_handoff, engineering_precedent_store
from ai_org import git_wrapper, network_bodies, patch_series_bodies
from ai_org.patch_author import announcements, code_worker, discovery, submission, work
from ai_org.patch_author import contract as patch_author_contract
from ai_org.patchwork_queue.field_registry import empty_user_experience_requirements


def test_codex_reset_boundary_uses_short_retry_instead_of_tomorrow():
    local_tz = datetime.now().astimezone().tzinfo
    started_at = datetime(2026, 7, 12, 22, 18, 6, tzinfo=local_tz)

    not_before = datetime.fromisoformat(code_worker._codex_not_before("10:18 PM", started_at))

    assert not_before == started_at + timedelta(seconds=120)


def test_codex_reset_time_keeps_same_day_and_next_day_law():
    local_tz = datetime.now().astimezone().tzinfo
    morning = datetime(2026, 7, 12, 10, 0, tzinfo=local_tz)
    afternoon = datetime(2026, 7, 12, 15, 0, tzinfo=local_tz)

    same_day = datetime.fromisoformat(code_worker._codex_not_before("1:00 PM", morning))
    next_day = datetime.fromisoformat(code_worker._codex_not_before("1:00 PM", afternoon))

    assert same_day == datetime(2026, 7, 12, 13, 0, tzinfo=local_tz)
    assert next_day == datetime(2026, 7, 13, 13, 0, tzinfo=local_tz)


def test_run_codex_resumes_transient_failure_when_session_survives(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    session_id = "019f0000-aaaa-7000-8000-000000000001"
    calls: list[list[str]] = []

    def fake_logged_subprocess(cmd, **_kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr=(
                    f"session id: {session_id}\n"
                    "ERROR: Selected model is at capacity. Please try again later.\n"
                ),
            )
        Path(cmd[cmd.index("-o") + 1]).write_text("done\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(code_worker.org_log, "logged_subprocess", fake_logged_subprocess)
    monkeypatch.setattr(codex_reset, "wait_for_transient", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_reset.org_log, "emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        code_worker,
        "_git_run",
        lambda *_args: subprocess.CompletedProcess([], 0, stdout=" M feature.py\n", stderr=""),
    )

    result = code_worker._run_codex(
        worktree,
        "implement the item",
        ctx=code_worker.org_log.RunContext(repo=tmp_path, run_id="code-worker-transient-resume"),
    )

    assert result == {"failure": None}
    assert len(calls) == 2
    first, resumed = calls
    assert "resume" not in first
    resume_index = resumed.index("resume")
    assert resumed[resume_index : resume_index + 3] == ["resume", "--json", session_id]
    assert resumed.index("--sandbox") < resume_index
    assert resumed.index("-C") < resume_index
    assert resumed.index("-o") > resume_index
    assert resumed[-1] == codex_reset.RESUME_PROMPT


def test_run_codex_restarts_transient_failure_without_session_id(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    calls: list[list[str]] = []

    def fake_logged_subprocess(cmd, **_kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr="ERROR: Selected model is at capacity. Please try again later.\n",
            )
        Path(cmd[cmd.index("-o") + 1]).write_text("done\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(code_worker.org_log, "logged_subprocess", fake_logged_subprocess)
    monkeypatch.setattr(codex_reset, "wait_for_transient", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_reset.org_log, "emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        code_worker,
        "_git_run",
        lambda *_args: subprocess.CompletedProcess([], 0, stdout=" M feature.py\n", stderr=""),
    )

    result = code_worker._run_codex(
        worktree,
        "implement the item",
        ctx=code_worker.org_log.RunContext(repo=tmp_path, run_id="code-worker-transient-restart"),
    )

    assert result == {"failure": None}
    assert len(calls) == 2
    assert calls[1] == calls[0]
    assert "resume" not in calls[1]


def test_run_codex_marks_exhausted_transient_retries(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    calls: list[list[str]] = []

    def fake_logged_subprocess(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="ERROR: HTTP status 503 Service Unavailable.\n",
        )

    monkeypatch.setattr(code_worker.org_log, "logged_subprocess", fake_logged_subprocess)
    monkeypatch.setattr(codex_reset, "wait_for_transient", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_reset.org_log, "emit", lambda *_args, **_kwargs: None)

    result = code_worker._run_codex(
        worktree,
        "implement the item",
        ctx=code_worker.org_log.RunContext(repo=tmp_path, run_id="code-worker-transient-exhausted"),
    )

    assert len(calls) == codex_reset.MAX_TRANSIENT_RETRIES + 1
    assert result["failure"]["type"] == "transient-retries-exhausted"
    assert "transient-retries-exhausted" in json.dumps(result, sort_keys=True)


@pytest.fixture(autouse=True)
def _isolated_precedent_store(monkeypatch, tmp_path_factory):
    """Never the live canonical store, never live research.

    Every test in this module points AI_ORG_REFERENCE_STORE at a per-test
    fixture path (absent by default, so lookups report StoreUnavailable and
    facets degrade typed) and guards expand() so an accidental miss can never
    fire real codex/network research. Facets tests seed the fixture store via
    the store's own add_preheld writer and replace the expand guard locally.

    Memento (flake root cause, coordinator report 2026-07-05): the store has a
    process-wide background-build executor other suite tests exercise; a
    straggler task resolves _database_path() at WRITE time, i.e. against
    whatever AI_ORG_REFERENCE_STORE happens to be current — which during this
    module is OUR per-test fixture path. Drain the executor BEFORE and AFTER
    each test so no cross-test thread can write into (or hold sqlite locks
    on) the fixture store mid-test. Isolation must be temporal, not just
    spatial.
    """
    engineering_precedent_store.await_background_builds(timeout=30)
    store_dir = tmp_path_factory.mktemp("precedent-store")
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(store_dir / "org.sqlite3"))
    monkeypatch.delenv("AI_ORG_PRECEDENT_READ_DISABLED", raising=False)

    def guarded_expand(*args, **kwargs):
        raise AssertionError(f"unexpected live expand call: {args} {kwargs}")

    def guarded_background_build(*args, **kwargs):
        raise AssertionError(f"unexpected background build: {args} {kwargs}")

    monkeypatch.setattr(engineering_precedent_store, "expand", guarded_expand)
    monkeypatch.setattr(engineering_precedent_store, "start_background_build", guarded_background_build)
    yield
    # Nothing this module started may leak into the next test's store either.
    engineering_precedent_store.await_background_builds(timeout=30)


@pytest.fixture(autouse=True)
def _reap_preserved_worktrees(monkeypatch):
    """Success-only cleanup preserves failed trees OUTSIDE pytest's tmp_path;
    record every mkdtemp and reap after the test so failing-drive tests do not
    litter the system temp dir. Tests may request this fixture to observe the
    temp dirs the worker created."""
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)
    yield created
    for path in created:
        shutil.rmtree(path, ignore_errors=True)


def _impl_facet(snippet: str = "PRECEDENT-SNIPPET: write the marker atomically") -> dict[str, str]:
    return {
        "snippet": snippet,
        "summary": "Write the marker file once, atomically.",
        "pitfalls": "Do not leave partial writes behind.",
        "source_url": "ai-org-bootstrap-codex@c2b8113:ai_org/patch_author/code_worker.py",
    }


def _design_facet() -> dict[str, str]:
    return {
        "structure": "Single marker artifact per proof moment.",
        "rationale": "One file per observable claim keeps checks executable.",
        "when_to_use": "Deliverables proven by file presence.",
        "when_not_to_use": "Stateful services needing runtime probes.",
        "tradeoffs": "Coarse; no behavioral coverage.",
        "alternatives": "Runtime smoke probe.",
        "implementation_hooks": "test -f <marker>",
        "quality_attributes": "determinism, auditability",
        "evidence": "ai-org-bootstrap-codex@c2b8113:tests/test_patch_author_code_worker.py",
        "delta_claim": "Markers make acceptance checks deterministic.",
        "source_url": "ai-org-bootstrap-codex@c2b8113:tests/test_patch_author_code_worker.py",
    }


AUTHOR_A = {"name": "Author A", "email": "101+author-a@users.noreply.github.com"}
AUTHOR_B = {"name": "Author B", "email": "102+author-b@users.noreply.github.com"}
SERIES_ID = "add-feature-file"
SERIES_BRANCH = f"ai-org/patch-series/{SERIES_ID}"
PLAN_ID = "patch_plan:candidate:files"
UNRELATED_CANARY = "UNRELATED-SUBTREE-CANARY"
EXTRA_ITEM_CANARY = "EXTRA-ITEM-CANARY"
DEFAULT_FIRST_PROOF_CHECK = object()


def _patch_series_view(title: str = "Add Feature File") -> dict[str, object]:
    return {
        "raw_request": f"{title}: create feature.txt with the implemented marker.",
        "working_title": title,
        "request_type": "feature",
        "problem_or_motivation": "The repo lacks a feature marker.",
        "intended_users_or_jobs": "Repository users need a visible implemented marker.",
        "desired_outcomes_success": "A marker file appears on the contribution branch.",
        "affected_area_platform": "feature.txt",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native files",
            "language": "text",
            "platform": "repository",
            "rationale": "Use the existing repository file layout.",
            "provenance": "requester_specified",
        },
        "user_experience_requirements": empty_user_experience_requirements(),
        "background_facts": "Keep the change focused.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "Test fixture grounding.",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": "Create feature.txt with the implemented marker.",
        "alternatives_considered": ["Leave the repo without a feature marker."],
    }


def _approach_with_plan(
    *,
    follow_up_check: list[str] | None = None,
    first_proof_check: object = DEFAULT_FIRST_PROOF_CHECK,
) -> dict[str, object]:
    follow_up: dict[str, object] = {
        "adds": f"second marker extra.txt ({EXTRA_ITEM_CANARY})",
        "named_content": [{"name": "extra.txt", "kind": "file"}],
    }
    if follow_up_check is not None:
        follow_up["functional_check"] = {"command": follow_up_check}
    first_proof_moment: dict[str, object] = {
        "user_can": ["see feature.txt"],
        "named_content": [{"name": "feature.txt", "kind": "file"}],
        "presentation_baseline": "plain text file",
        "win_or_progress_condition": "feature.txt exists with the implemented marker",
        "how_verified": "open feature.txt",
    }
    if first_proof_check is DEFAULT_FIRST_PROOF_CHECK:
        first_proof_moment["functional_check"] = {"command": ["test", "-f", "feature.txt"]}
    elif first_proof_check is not None:
        first_proof_moment["functional_check"] = first_proof_check
    return {
        "problem": {
            "id": "problem",
            "statement": "The repo lacks a feature marker.",
            "prior_art": [
                {"id": "prior_art:markers", "pattern": "marker files", "summary": "Plain marker files prove presence."}
            ],
            "question": {
                "id": "question:approach",
                "text": "Which approach?",
                "candidates": [
                    {
                        "id": "candidate:files",
                        "name": "Repo-native files",
                        "summary": "Write marker files directly.",
                    },
                    {
                        "id": "candidate:unrelated",
                        "name": "Unrelated generator",
                        "summary": f"Never in any item window: {UNRELATED_CANARY}",
                    },
                ],
                "decision": {
                    "id": "decision:candidate:files",
                    "selected_candidate_id": "candidate:files",
                    "implementation": {
                        "id": "implementation:candidate:files",
                        "modules": ["feature"],
                        "patch_plan": {
                            "id": PLAN_ID,
                            "first_proof_moment": first_proof_moment,
                            "follow_ups": [follow_up],
                            "deferred": [],
                            "open_questions": [],
                        },
                    },
                },
            },
        },
        "cross_links": [
            {"from": PLAN_ID, "to": "prior_art:markers", "type": "derived_from"},
        ],
    }


def _brief(approach: dict[str, object] | None = None) -> dict[str, object]:
    tree = approach or _approach_with_plan()
    plan = tree["problem"]["question"]["decision"]["implementation"]["patch_plan"]  # type: ignore[index]
    return {
        "ok": True,
        "series_branch": SERIES_BRANCH,
        "node_path": ".",
        "node_key": "root",
        "cover_letter": _patch_series_view(),
        "approach": tree,
        "patch_plan": plan,
        "plan_id": PLAN_ID,
        "contract_schema": patch_author_contract.implementation_result_schema(),
        "spine_paths": [],
        "acceptance_criteria": ["feature.txt carries the implemented marker"],
        "functional_check_description": "",
        "facts": {"checks": {}, "lifecycle_status": "ready_for_patch_authoring"},
    }


# ---------------------------------------------------------------------------
# Unit: plan items, partition, bounded window, prompt, result schema.
# ---------------------------------------------------------------------------

def test_plan_items_are_ordered_with_deterministic_ids():
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])
    assert [item["item_id"] for item in items] == [
        f"{PLAN_ID}#first_proof_moment",
        f"{PLAN_ID}#follow-up-01",
    ]
    assert [item["kind"] for item in items] == ["first_proof_moment", "follow_up"]


def test_canonical_producer_plan_preserves_exact_assignment_ids():
    assignments = [
        {"item_id": "patch:one", "production_obligation_ids": ["obligation:one"]},
        {"item_id": "patch:diagnostics", "production_obligation_ids": []},
    ]

    items = code_worker.plan_items(assignments, "root.problem.patch_plan")

    assert [item["item_id"] for item in items] == ["patch:one", "patch:diagnostics"]
    assert [item["body"] for item in items] == assignments
    assert {item["kind"] for item in items} == {"patch_plan_assignment"}


def test_plan_items_enumerates_only_node_scoped_graph_slice():
    leaf_slice = {
        "id": "patch_plan:graph",
        "items": [
            {
                "id": "component:api",
                "objective": "Implement the API component.",
                "named_content": [],
                "acceptance_criteria": ["API test passes."],
                "how_verified": "pytest API",
                "depends_on": [],
            }
        ],
        "deferred": [],
        "open_questions": [],
    }

    items = code_worker.plan_items(leaf_slice, "patch_plan:graph")

    assert [item["item_id"] for item in items] == ["component:api"]
    assert [item["kind"] for item in items] == ["work_item"]
    assert items[0]["body"] == leaf_slice["items"][0]


@pytest.mark.parametrize(
    "route", ["producer_code_authoring", "producer_feedback_authoring"]
)
def test_producer_authoring_paths_stop_at_the_common_closed_gate(
    tmp_path, monkeypatch, route
):
    observed = []
    snapshot = SimpleNamespace(
        generation=code_worker.patch_series_bodies.ROOT_GENERATION_V2,
        lifecycle_ready=False,
        disposition=code_worker.patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
        frozen_oid="a" * 40,
        detail="preview only",
    )

    class Decision:
        blocked = True
        diagnostic = SimpleNamespace(detail="common readiness gate is closed")

        def as_dict(self):
            return {
                "vet_passed": True,
                "authorable": False,
                "source": {
                    "route": route,
                    "frozen_root_oid": "a" * 40,
                },
            }

    monkeypatch.setattr(code_worker.git_wrapper, "head_sha", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        code_worker.patch_series_bodies,
        "classify_root_generation",
        lambda *_args: snapshot,
    )

    def vet(_repo, _branch, selected_route, **_kwargs):
        observed.append(selected_route)
        return Decision()

    monkeypatch.setattr(code_worker.patch_series_gate, "vet_producer_pair_closure", vet)

    result = code_worker.load_brief(
        tmp_path,
        SERIES_BRANCH,
        ".",
        producer_gate_route=route,
    )

    assert result["ok"] is False
    assert result["status"] == code_worker.patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    assert result["detail"] == "common readiness gate is closed"
    assert observed == [route]


@pytest.mark.parametrize(
    "route", ["producer_code_authoring", "producer_feedback_authoring"]
)
def test_ready_producer_authoring_paths_still_pass_through_the_common_gate(
    tmp_path, monkeypatch, route
):
    observed = []
    snapshot = SimpleNamespace(
        generation=code_worker.patch_series_bodies.ROOT_GENERATION_V2,
        lifecycle_ready=True,
        disposition=code_worker.patch_series_bodies.ROOT_DISPOSITION_V2_READY,
        frozen_oid="a" * 40,
        detail="",
        identity=lambda: {
            "representation": "v2",
            "source_oid": "a" * 40,
        },
        cover_letter=lambda: (_ for _ in ()).throw(RuntimeError("after gate")),
    )

    monkeypatch.setattr(code_worker.git_wrapper, "head_sha", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        code_worker.patch_series_bodies,
        "classify_root_generation",
        lambda *_args: snapshot,
    )

    class Decision:
        blocked = False

        def as_dict(self):
            return {
                "vet_passed": True,
                "authorable": True,
                "source": {
                    "route": route,
                    "frozen_root_oid": "a" * 40,
                },
            }

    def vet(_repo, _branch, selected_route, **kwargs):
        observed.append((selected_route, kwargs["frozen_root_oid"]))
        return Decision()

    monkeypatch.setattr(code_worker.patch_series_gate, "vet_producer_pair_closure", vet)

    result = code_worker.load_brief(
        tmp_path,
        SERIES_BRANCH,
        ".",
        producer_gate_route=route,
    )

    assert result["status"] == "cover_letter_unparseable"
    assert result["detail"] == "after gate"
    assert observed == [(route, "a" * 40)]


@pytest.mark.parametrize(
    ("route", "wrong_route"),
    [
        ("producer_code_authoring", "producer_feedback_authoring"),
        ("producer_feedback_authoring", "producer_code_authoring"),
    ],
)
def test_producer_authoring_rejects_a_decision_for_the_other_transition(
    tmp_path, monkeypatch, route, wrong_route
):
    snapshot = SimpleNamespace(
        generation=code_worker.patch_series_bodies.ROOT_GENERATION_V2,
        lifecycle_ready=True,
        disposition=code_worker.patch_series_bodies.ROOT_DISPOSITION_V2_READY,
        frozen_oid="a" * 40,
        detail="",
        identity=lambda: {"representation": "v2", "source_oid": "a" * 40},
        cover_letter=lambda: (_ for _ in ()).throw(
            AssertionError("authoring reads continued after a route mismatch")
        ),
    )

    class Decision:
        blocked = False

        def as_dict(self):
            return {
                "vet_passed": True,
                "authorable": True,
                "source": {
                    "route": wrong_route,
                    "frozen_root_oid": "a" * 40,
                },
            }

    monkeypatch.setattr(code_worker.git_wrapper, "head_sha", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        code_worker.patch_series_bodies,
        "classify_root_generation",
        lambda *_args: snapshot,
    )
    monkeypatch.setattr(
        code_worker.patch_series_gate,
        "vet_producer_pair_closure",
        lambda *_args, **_kwargs: Decision(),
    )

    result = code_worker.load_brief(
        tmp_path,
        SERIES_BRANCH,
        ".",
        producer_gate_route=route,
    )

    assert result["ok"] is False
    assert result["status"] == "producer_authoring_decision_invalid"
    assert result["authorability_decision"]["source"]["route"] == wrong_route


def test_task_binding_reuses_the_snapshot_frozen_by_the_authoring_gate(
    tmp_path, monkeypatch
):
    frozen = "a" * 40
    gate_decision = {
        "vet_passed": True,
        "authorable": True,
        "transition_allowed": False,
        "source": {"frozen_root_oid": frozen},
        "diagnostic": None,
    }
    observed = []

    def prepare(
        _repo,
        series_head,
        contribution_ref,
        record,
        *,
        readiness_decision,
        expected_readiness_route,
    ):
        observed.append(
            (
                series_head,
                contribution_ref,
                record["series_branch"],
                readiness_decision,
                expected_readiness_route,
            )
        )
        return code_worker.producer_lifecycle.PreparedTaskBinding(
            "inactive", {}, {}
        )

    monkeypatch.setattr(
        code_worker.git_wrapper,
        "head_sha",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("binding materialization re-resolved the live branch")
        ),
    )
    monkeypatch.setattr(
        code_worker.producer_lifecycle, "prepare_task_binding", prepare
    )

    result = code_worker._materialize_producer_task_binding(
        tmp_path,
        tmp_path,
        {
            "series_branch": SERIES_BRANCH,
            "series_snapshot_oid": frozen,
            "node_path": ".",
            "node_key": "root",
            "producer_authorability_decision": gate_decision,
            "producer_gate_route": "producer_code_authoring",
        },
        branch="ai-org/contrib/demo-series",
        identity=AUTHOR_A,
    )

    assert result == {"ok": True, "active": False}
    assert observed == [
        (
            frozen,
            "ai-org/contrib/demo-series",
            SERIES_BRANCH,
            gate_decision,
            "producer_code_authoring",
        )
    ]


def test_task_binding_closed_gate_preserves_typed_decision_at_public_boundary(
    tmp_path, monkeypatch
):
    frozen = "b" * 40

    class Decision:
        def as_dict(self):
            return {
                "vet_passed": False,
                "authorable": False,
                "source": {
                    "route": "producer_task_binding",
                    "frozen_root_oid": frozen,
                },
                "diagnostic": {
                    "rule": "exact-scope-row-missing",
                    "field": "scope_item_id",
                },
            }

    monkeypatch.setattr(
        code_worker.producer_lifecycle,
        "prepare_task_binding",
        lambda *_args, **_kwargs: code_worker.producer_lifecycle.PreparedTaskBinding(
            "blocked",
            {},
            {},
            decision=Decision(),
            detail="the frozen scope row is missing",
        ),
    )

    result = code_worker._materialize_producer_task_binding(
        tmp_path,
        tmp_path,
        {
            "series_branch": SERIES_BRANCH,
            "series_snapshot_oid": frozen,
            "node_path": ".",
            "node_key": "root",
        },
        branch="ai-org/contrib/demo-series",
        identity=AUTHOR_A,
    )

    assert result == {
        "ok": False,
        "status": "producer_task_binding_blocked",
        "detail": "the frozen scope row is missing",
        "frozen_oid": frozen,
        "authorability_decision": {
            "vet_passed": False,
            "authorable": False,
            "source": {
                "route": "producer_task_binding",
                "frozen_root_oid": frozen,
            },
            "diagnostic": {
                "rule": "exact-scope-row-missing",
                "field": "scope_item_id",
            },
        },
    }


def test_migrated_initial_authoring_cannot_fall_through_without_task_binding(
    tmp_path, monkeypatch
):
    frozen = "a" * 40
    monkeypatch.setattr(
        code_worker.producer_lifecycle,
        "prepare_task_binding",
        lambda *_args, **_kwargs: code_worker.producer_lifecycle.PreparedTaskBinding(
            "inactive", {}, {}
        ),
    )

    result = code_worker._materialize_producer_task_binding(
        tmp_path,
        tmp_path,
        {
            "series_branch": SERIES_BRANCH,
            "series_snapshot_oid": frozen,
            "node_path": ".",
            "node_key": "root",
            "canonical_producer_plan": True,
        },
        branch="ai-org/contrib/demo-series",
        identity=AUTHOR_A,
    )

    assert result == {
        "ok": False,
        "status": "producer_task_binding_missing",
        "detail": (
            "migrated producer authoring cannot continue without "
            "materializing its task binding"
        ),
        "frozen_oid": frozen,
    }


def test_migrated_feedback_cannot_fall_through_without_task_binding(
    tmp_path, monkeypatch
):
    frozen = "a" * 40
    monkeypatch.setattr(code_worker, "_branch_exists", lambda *_args: True)
    monkeypatch.setattr(
        code_worker,
        "load_brief",
        lambda *_args, **_kwargs: {
            "ok": True,
            "series_snapshot_oid": frozen,
            "canonical_producer_plan": True,
        },
    )
    monkeypatch.setattr(
        code_worker.git_wrapper,
        "show_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        code_worker,
        "_run_codex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("feedback code ran without the materialized binding")
        ),
    )

    result = code_worker.address_feedback(
        tmp_path,
        {
            "series_branch": SERIES_BRANCH,
            "node_path": ".",
            "contrib_branch": "ai-org/contrib/demo-series",
            "author": AUTHOR_A,
        },
        feedback={"blocker": "retry"},
    )

    assert result == {
        "ok": False,
        "status": "producer_task_binding_missing",
        "detail": (
            "migrated producer feedback cannot continue without its "
            "materialized task binding"
        ),
    }


def test_feedback_rejects_a_binding_from_a_different_gated_snapshot(
    tmp_path, monkeypatch
):
    frozen = "a" * 40
    stale = "b" * 40
    monkeypatch.setattr(code_worker, "_branch_exists", lambda *_args: True)
    monkeypatch.setattr(
        code_worker,
        "load_brief",
        lambda *_args, **_kwargs: {
            "ok": True,
            "series_snapshot_oid": frozen,
        },
    )
    monkeypatch.setattr(
        code_worker.git_wrapper,
        "show_file",
        lambda *_args, **_kwargs: "binding-present",
    )
    monkeypatch.setattr(
        code_worker.git_wrapper,
        "show_file_bytes",
        lambda *_args, **_kwargs: b"stale-binding",
    )
    monkeypatch.setattr(
        code_worker.patch_series_bodies,
        "read_producer_task_binding",
        lambda *_args, **_kwargs: {"series_snapshot_oid": stale},
    )
    monkeypatch.setattr(
        code_worker,
        "_run_codex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("feedback code ran after a frozen-source mismatch")
        ),
    )

    result = code_worker.address_feedback(
        tmp_path,
        {
            "series_branch": SERIES_BRANCH,
            "node_path": ".",
            "contrib_branch": "ai-org/contrib/demo-series",
            "author": AUTHOR_A,
        },
        feedback={"blocker": "retry"},
    )

    assert result == {
        "ok": False,
        "status": "producer_task_binding_invalid",
        "detail": "producer task binding does not match the gated series snapshot",
    }


def test_feedback_rejects_inexact_promise_bindings_before_code_dispatch(
    tmp_path, monkeypatch
):
    frozen = "a" * 40
    monkeypatch.setattr(code_worker, "_branch_exists", lambda *_args: True)
    monkeypatch.setattr(
        code_worker,
        "load_brief",
        lambda *_args, **_kwargs: {
            "ok": True,
            "series_snapshot_oid": frozen,
            "canonical_producer_plan": True,
        },
    )
    monkeypatch.setattr(
        code_worker.git_wrapper,
        "show_file",
        lambda *_args, **_kwargs: "binding-present",
    )
    monkeypatch.setattr(
        code_worker.git_wrapper,
        "show_file_bytes",
        lambda *_args, **_kwargs: b"binding-bytes",
    )
    monkeypatch.setattr(
        code_worker.patch_series_bodies,
        "read_producer_task_binding",
        lambda *_args, **_kwargs: {
            "series_snapshot_oid": frozen,
            "tasks": [
                {
                    "item_id": "item:one",
                    "production_obligation_ids": ["obligation:one"],
                }
            ],
            "promise_bindings": [
                {"obligation_id": "obligation:one"},
                {"obligation_id": "obligation:one"},
            ],
        },
    )
    monkeypatch.setattr(
        code_worker,
        "_run_codex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("feedback code ran with an inexact Promise Binding cohort")
        ),
    )

    result = code_worker.address_feedback(
        tmp_path,
        {
            "series_branch": SERIES_BRANCH,
            "node_path": ".",
            "contrib_branch": "ai-org/contrib/demo-series",
            "author": AUTHOR_A,
        },
        feedback={"blocker": "retry"},
    )

    assert result == {
        "ok": False,
        "status": "producer_task_binding_invalid",
        "detail": "producer task binding promise obligations are not unique",
    }


def test_migrated_feedback_rejects_a_binding_from_another_producer_family(
    tmp_path, monkeypatch
):
    frozen = "a" * 40
    root_sha256 = "b" * 64
    scope_sha256 = "c" * 64
    branch = "ai-org/contrib/demo-series"
    monkeypatch.setattr(code_worker, "_branch_exists", lambda *_args: True)
    monkeypatch.setattr(
        code_worker,
        "load_brief",
        lambda *_args, **_kwargs: {
            "ok": True,
            "series_snapshot_oid": frozen,
            "canonical_producer_plan": True,
            "producer_authorability_decision": {
                "vet_passed": True,
                "authorable": True,
                "transition_allowed": False,
                "source": {
                    "route": "producer_feedback_authoring",
                    "branch": SERIES_BRANCH,
                    "frozen_root_oid": frozen,
                    "root_sha256": root_sha256,
                    "scope_decomposition_sha256": scope_sha256,
                },
            },
        },
    )
    monkeypatch.setattr(
        code_worker.git_wrapper,
        "show_file",
        lambda *_args, **_kwargs: "binding-present",
    )
    monkeypatch.setattr(
        code_worker.git_wrapper,
        "show_file_bytes",
        lambda *_args, **_kwargs: b"binding-bytes",
    )
    monkeypatch.setattr(
        code_worker.patch_series_bodies,
        "read_producer_task_binding",
        lambda *_args, **_kwargs: {
            "series_branch": SERIES_BRANCH,
            "node_path": ".",
            "contribution_branch": "ai-org/contrib/another-family",
            "series_snapshot_oid": frozen,
            "canonical_root_body_sha256": root_sha256,
            "scope_decomposition_commit_oid": frozen,
            "scope_decomposition_body_sha256": scope_sha256,
            "producer": AUTHOR_A,
            "tasks": [
                {
                    "item_id": "item:one",
                    "production_obligation_ids": ["obligation:one"],
                }
            ],
            "promise_bindings": [{"obligation_id": "obligation:one"}],
        },
    )
    monkeypatch.setattr(
        code_worker,
        "_run_codex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("feedback code ran with a cross-family binding")
        ),
    )

    result = code_worker.address_feedback(
        tmp_path,
        {
            "series_branch": SERIES_BRANCH,
            "node_path": ".",
            "contrib_branch": branch,
            "author": AUTHOR_A,
        },
        feedback={"blocker": "retry"},
    )

    assert result == {
        "ok": False,
        "status": "producer_task_binding_invalid",
        "detail": (
            "producer task binding does not match the gated "
            "contribution_branch"
        ),
    }


def test_implementation_result_commit_is_harness_authored_but_keeps_producer_identity(
    tmp_path
):
    repo = tmp_path / "harness-result"
    repo.mkdir()
    _git(repo, "init")
    (repo / "feature.txt").write_text("implemented\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(
        repo,
        *git_wrapper.identity_config_args(AUTHOR_A),
        "commit",
        "-m",
        "patch: producer implementation [item:one]",
    )

    written = code_worker._write_result_artifact(
        repo,
        repo,
        {
            "series_branch": SERIES_BRANCH,
            "node_key": "root",
            "facts": {"checks": {}},
            "must_answer_questions": [],
            "contract_schema": patch_author_contract.implementation_result_schema(),
        },
        identity=AUTHOR_A,
    )

    assert written["ok"] is True
    assert git_wrapper.commit_author(repo, written["commit"]) == (
        git_wrapper.engine_identity()
    )
    assert announcements.contribution_author_matches(
        repo,
        "HEAD",
        {"author": AUTHOR_A},
    ) is True


def test_partition_items_is_disjoint_covering_ordered_and_intra_family_only():
    items = [{"item_id": f"i{index}"} for index in range(5)]
    # Default: one child per item (maximum partition).
    default = code_worker.partition_items(items)
    assert default == [[item] for item in items]
    # Coarser partitions stay contiguous, disjoint, and covering.
    for child_count in (1, 2, 3, 5, 9):
        partitions = code_worker.partition_items(items, child_count)
        flattened = [item for partition in partitions for item in partition]
        assert flattened == items  # order preserved, every item exactly once
        assert all(partition for partition in partitions)  # no idle child
    assert code_worker.partition_items([]) == []


def test_bounded_window_contains_item_refs_and_touched_files_only():
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])

    window = code_worker.build_item_window(brief, items[0], ["prior.txt", "prior.txt"])
    assert window["item_id"] == f"{PLAN_ID}#first_proof_moment"
    assert window["item"]["win_or_progress_condition"].startswith("feature.txt exists")
    # 1-hop referenced node over id-typed edges (cross_link to prior_art).
    assert "prior_art:markers" in window["referenced_nodes"]
    assert window["files_touched_so_far"] == ["prior.txt"]

    dumped = json.dumps(window, sort_keys=True)
    # Unrelated subtrees are ABSENT: the non-selected candidate never appears.
    assert UNRELATED_CANARY not in dumped
    # Other plan items are ABSENT: the patch_plan node body is excluded from
    # the referenced nodes and replaced by exactly this item.
    assert EXTRA_ITEM_CANARY not in dumped
    assert PLAN_ID not in window["referenced_nodes"]

    # The second item's window carries the second item, not the first.
    window_two = code_worker.build_item_window(brief, items[1], ["feature.txt"])
    assert EXTRA_ITEM_CANARY in json.dumps(window_two["item"], sort_keys=True)
    assert UNRELATED_CANARY not in json.dumps(window_two, sort_keys=True)
    assert window_two["files_touched_so_far"] == ["feature.txt"]


def test_item_prompt_is_free_form_first_and_skeleton_only_in_repair():
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])
    window = code_worker.build_item_window(brief, items[0], [])

    initial = code_worker.render_item_prompt(window)
    assert "REPAIR ROUND" not in initial
    assert f"Implement ONLY this item ({PLAN_ID}#first_proof_moment)" in initial
    assert "Do NOT write implementation-result.json" in initial
    assert "implementation-result.cue" in initial
    assert UNRELATED_CANARY not in initial and EXTRA_ITEM_CANARY not in initial

    repair = code_worker.render_item_prompt(
        window, repair={"typed_error": {"type": "item_check_failed", "detail": "exit=1"}, "failed_round": 1}
    )
    assert "REPAIR ROUND" in repair and "item_check_failed" in repair


@pytest.mark.parametrize(
    "reserved_path",
    [contributor_handoff.RESULT_PATH, contributor_handoff.LEGACY_RESULT_PATH],
)
def test_contributor_cannot_write_harness_reserved_result(tmp_path, reserved_path):
    repo = tmp_path / "worktree"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / reserved_path).write_text("{}\n", encoding="utf-8")

    failure = code_worker._reserved_result_write_failure(repo)

    assert failure is not None
    assert failure["type"] == "implementation_result_written_by_contributor"
    assert failure["paths"] == [reserved_path]


def test_result_artifact_validates_against_the_committed_schema():
    schema = patch_author_contract.implementation_result_schema()
    good = {
        "patch_series_branch": SERIES_BRANCH,
        "node_key": "root",
        "acknowledged_patchwork_checks": {},
    }
    assert code_worker.validate_result_against_schema(good, schema) == []
    assert code_worker.validate_result_against_schema({}, schema) == [
        "required field missing: patch_series_branch",
        "required field missing: node_key",
        "required field missing: acknowledged_patchwork_checks",
    ]
    extra = dict(good, invented=True)
    assert code_worker.validate_result_against_schema(extra, schema) == ["unknown field: invented"]
    wrong = dict(good, node_key=7)
    assert code_worker.validate_result_against_schema(wrong, schema) == ["field node_key must be a string"]
    # Gap #1 and #2 travel WITH the committed contract document itself.
    assert "implementation-result.cue" in schema["x-committed-location"]
    assert "child_key" in schema["x-node-key-convention"]


# ---------------------------------------------------------------------------
# E2E (hermetic, mocked codex): announce -> implement -> submit.
# ---------------------------------------------------------------------------

def _mock_codex(monkeypatch, edits_by_marker: dict[str, dict[str, str]], calls: list[str]):
    """Intercept codex exec; route edits by which plan item the prompt names."""
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if not (isinstance(cmd, list) and cmd[:2] == ["codex", "exec"]):
            return real_run(cmd, *args, **kwargs)
        prompt = cmd[-1]
        calls.append(prompt)
        worktree = Path(cmd[cmd.index("-C") + 1])
        out_file = Path(cmd[cmd.index("-o") + 1])
        for marker, edits in edits_by_marker.items():
            if marker in prompt:
                item_id = marker.removeprefix("#")
                if not marker.startswith("#"):
                    item_id = marker
                else:
                    item_id = next(
                        line.split("(", 1)[1].split(")", 1)[0]
                        for line in prompt.splitlines()
                        if line.startswith("Implement ONLY this item (")
                    )
                for rel_path, content in edits.items():
                    target = worktree / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                break
        out_file.write_text("done\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(code_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(code_worker, "_run_cue_frame_validation", lambda *_args, **_kwargs: {"ok": True})


def test_drive_commits_per_item_writes_result_artifact_and_submission_flips(tmp_path, monkeypatch):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    calls: list[str] = []
    _mock_codex(
        monkeypatch,
        {
            "#first_proof_moment": {"feature.txt": "implemented\n"},
            "#follow-up-01": {"extra.txt": "extra\n"},
        },
        calls,
    )
    result = work.implement_announced(clone, announced["ref"])
    assert result["ok"] is True, result
    assert result["branch"] == f"ai-org/contrib/{SERIES_ID}"
    assert result["patch_series_branch"] == SERIES_BRANCH and result["node_key"] == "root"
    assert [entry["item_id"] for entry in result["item_commits"]] == [
        f"{PLAN_ID}#first_proof_moment",
        f"{PLAN_ID}#follow-up-01",
    ]
    assert len(calls) == 2  # one bounded window per item, no repairs needed
    # Drive record carries the per-item precedent consumption summary; under
    # this test's absent fixture store it records the typed degradation.
    for entry in result["item_commits"]:
        assert entry["precedent"]["store_unavailable"] != ""
        assert entry["precedent"]["terms_hit"] == []

    # Commit-per-item discipline: newest-first subjects cite the item ids.
    subjects = git_wrapper.log_subjects(clone, result["branch"])
    assert subjects[0] == "patch: implementation result for root"
    assert f"[{PLAN_ID}#follow-up-01]" in subjects[1]
    assert f"[{PLAN_ID}#first_proof_moment]" in subjects[2]

    # Second item's window listed the first item's files (bounded continuity).
    assert "feature.txt" in calls[1] and "Files earlier items already touched" in calls[1]
    # The model was never asked for the result artifact.
    assert all("Do NOT write implementation-result.json" in prompt for prompt in calls)

    # Result artifact: harness-computed, schema-conformant, committed at root.
    result_text = git_wrapper.show_file(clone, result["branch"], contributor_handoff.RESULT_PATH)
    parsed = contributor_handoff.parse_result(result_text)
    schema = json.loads(git_wrapper.show_file(clone, SERIES_BRANCH, patch_author_contract.IMPLEMENTATION_RESULT_SCHEMA_PATH))
    assert code_worker.validate_result_against_schema(parsed, schema) == []
    assert parsed["patch_series_branch"] == SERIES_BRANCH and parsed["node_key"] == "root"

    # Submission projection flips: the branch is published on the canonical remote.
    submitted = submission.submit(clone, announced["ref"])
    assert submitted["ok"] is True and submitted["status"] == "submitted_for_maintainer_review"
    listing = discovery.list_open(clone)
    row = {row["branch"]: row for row in listing["rows"]}[SERIES_BRANCH]
    assert row["published_contrib_branches"] == [f"ai-org/contrib/{SERIES_ID}"]
    assert row["open"] is True  # no-lock: submission never closes the task


def test_drive_forks_from_origins_current_default_when_tracking_ref_is_stale(tmp_path, monkeypatch):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    stale_base = _git(clone, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    seed = tmp_path / "seed"
    (seed / "origin-only.txt").write_text("current origin base\n", encoding="utf-8")
    _git(seed, "add", "origin-only.txt")
    _seed_commit(seed, "base: advance origin after author clone")
    current_base = _git(seed, "rev-parse", "HEAD").stdout.strip()
    _git(seed, "push", "origin", "main")
    assert current_base != stale_base
    assert _git(clone, "rev-parse", "refs/remotes/origin/main").stdout.strip() == stale_base

    calls: list[str] = []
    _mock_codex(
        monkeypatch,
        {
            "#first_proof_moment": {"feature.txt": "implemented\n"},
            "#follow-up-01": {"extra.txt": "extra\n"},
        },
        calls,
    )

    result = work.implement_announced(clone, announced["ref"])

    assert result["ok"] is True, result
    first_item = result["item_commits"][0]["commit"]
    assert _git(clone, "rev-parse", f"{first_item}^").stdout.strip() == current_base
    assert _git(clone, "rev-parse", "refs/remotes/origin/main").stdout.strip() == current_base
    assert git_wrapper.show_file(clone, result["branch"], "origin-only.txt") == "current origin base\n"


def test_drive_refuses_unverifiable_base_without_creating_worktree_or_branch(tmp_path, monkeypatch):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True
    before_worktrees = _git(clone, "worktree", "list", "--porcelain").stdout

    calls: list[str] = []
    _mock_codex(
        monkeypatch,
        {"#first_proof_moment": {"feature.txt": "must not be written\n"}},
        calls,
    )
    real_git_run = code_worker._git_run
    fetches: list[tuple[str, ...]] = []

    def failing_fetch(repo, *args):
        if args[:2] == ("fetch", "origin"):
            fetches.append(args)
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="injected fetch failure")
        return real_git_run(repo, *args)

    monkeypatch.setattr(code_worker, "_git_run", failing_fetch)

    result = work.implement_announced(clone, announced["ref"])

    assert result["ok"] is False
    assert result["status"] == "base_unverifiable"
    assert result["detail"]
    assert "preserved_worktree" not in result
    assert calls == []
    assert fetches == [
        ("fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    ]
    assert _git(clone, "worktree", "list", "--porcelain").stdout == before_worktrees
    assert git_wrapper.branch_exists(clone, f"ai-org/contrib/{SERIES_ID}") is False


def test_drive_restores_unset_origin_head_before_resolving_base(tmp_path, monkeypatch):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True
    _git(clone, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

    calls: list[str] = []
    _mock_codex(
        monkeypatch,
        {
            "#first_proof_moment": {"feature.txt": "implemented\n"},
            "#follow-up-01": {"extra.txt": "extra\n"},
        },
        calls,
    )

    result = work.implement_announced(clone, announced["ref"])

    assert result["ok"] is True, result
    restored = _git(clone, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    assert restored.stdout.strip() == "origin/main"


def test_drive_uses_origins_renamed_default_instead_of_stale_origin_head(tmp_path, monkeypatch):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True
    assert _git(clone, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip() == "origin/main"

    seed = tmp_path / "seed"
    _git(seed, "checkout", "-b", "trunk")
    (seed / "trunk-only.txt").write_text("origin renamed default\n", encoding="utf-8")
    _git(seed, "add", "trunk-only.txt")
    _seed_commit(seed, "base: move origin default to trunk")
    trunk_base = _git(seed, "rev-parse", "HEAD").stdout.strip()
    _git(seed, "push", "origin", "trunk")
    _git(canonical, "symbolic-ref", "HEAD", "refs/heads/trunk")

    calls: list[str] = []
    _mock_codex(
        monkeypatch,
        {
            "#first_proof_moment": {"feature.txt": "implemented\n"},
            "#follow-up-01": {"extra.txt": "extra\n"},
        },
        calls,
    )

    result = work.implement_announced(clone, announced["ref"])

    assert result["ok"] is True, result
    first_item = result["item_commits"][0]["commit"]
    assert _git(clone, "rev-parse", f"{first_item}^").stdout.strip() == trunk_base
    assert _git(clone, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip() == "origin/trunk"
    assert git_wrapper.show_file(clone, result["branch"], "trunk-only.txt") == "origin renamed default\n"


def test_two_families_implement_the_same_series_without_arbitration(tmp_path, monkeypatch):
    """家族間重複=競争: both families complete and publish sibling branches."""
    canonical = _canonical_with_full_series(tmp_path)
    clone_a = _clone(canonical, tmp_path / "clone-a")
    clone_b = _clone(canonical, tmp_path / "clone-b")

    calls: list[str] = []
    _mock_codex(
        monkeypatch,
        {
            "#first_proof_moment": {"feature.txt": "implemented\n"},
            "#follow-up-01": {"extra.txt": "extra\n"},
        },
        calls,
    )

    announced_a = announcements.announce(
        clone_a, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced_a["ok"] is True
    assert work.implement_announced(clone_a, announced_a["ref"])["ok"] is True
    assert submission.submit(clone_a, announced_a["ref"])["ok"] is True

    announced_b = announcements.announce(
        clone_b, "origin", SERIES_BRANCH, author_name=AUTHOR_B["name"], author_email=AUTHOR_B["email"]
    )
    assert announced_b["ok"] is True
    assert announced_b["record"]["contrib_branch"] == f"ai-org/contrib/{SERIES_ID}-r2"
    assert work.implement_announced(clone_b, announced_b["ref"])["ok"] is True
    assert submission.submit(clone_b, announced_b["ref"])["ok"] is True

    remote_branches = git_wrapper.ls_remote(clone_a, "origin", "refs/heads/ai-org/contrib/*")["refs"]
    assert set(remote_branches) == {
        f"refs/heads/ai-org/contrib/{SERIES_ID}",
        f"refs/heads/ai-org/contrib/{SERIES_ID}-r2",
    }


def test_no_edits_is_a_typed_item_failure(tmp_path, monkeypatch):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    calls: list[str] = []
    _mock_codex(monkeypatch, {}, calls)  # codex "succeeds" but edits nothing
    result = work.implement_announced(clone, announced["ref"])
    assert result["ok"] is False and result["status"] == "item_failed"
    assert result["item_id"] == f"{PLAN_ID}#first_proof_moment"
    assert result["failure"]["type"] == "item_no_edits"
    assert result["permanent"] is True and len(calls) == 2


def test_successful_drive_still_cleans_up_temp_worktree(tmp_path, monkeypatch, _reap_preserved_worktrees):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    calls: list[str] = []
    _mock_codex(
        monkeypatch,
        {
            "#first_proof_moment": {"feature.txt": "implemented\n"},
            "#follow-up-01": {"extra.txt": "extra\n"},
        },
        calls,
    )
    result = work.implement_announced(clone, announced["ref"])
    assert result["ok"] is True, result
    assert "preserved_worktree" not in result

    worker_dirs = [d for d in _reap_preserved_worktrees if d.name.startswith("ai-org-patch-author-")]
    assert len(worker_dirs) == 1
    assert not worker_dirs[0].exists()


def test_failed_feedback_round_preserves_worktree_and_frees_the_branch(tmp_path, monkeypatch, _reap_preserved_worktrees):
    canonical = _canonical_with_full_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    calls: list[str] = []
    edits_by_marker = {
        "#first_proof_moment": {"feature.txt": "implemented\n"},
        "#follow-up-01": {"extra.txt": "extra\n"},
    }
    _mock_codex(monkeypatch, edits_by_marker, calls)
    initial = work.implement_announced(clone, announced["ref"])
    assert initial["ok"] is True, initial

    # Feedback round fails (no edits is a typed codex failure): the temp tree
    # is preserved and the failure result names it.
    failed = work.implement_announced(clone, announced["ref"], feedback={"blocker": "add marker"}, attempt=2)
    assert failed["ok"] is False and failed["status"] == "feedback_round_failed"
    preserved = Path(failed["preserved_worktree"])
    assert preserved.exists() and preserved.name == "worktree"

    # The preserved checkout is detached, so the published branch is free for
    # the next feedback round's `worktree add` — the retry must succeed.
    edits_by_marker["#acceptance-feedback"] = {"feedback.txt": "addressed\n"}
    retried = work.implement_announced(clone, announced["ref"], feedback={"blocker": "add marker"}, attempt=3)
    assert retried["ok"] is True, retried
    assert git_wrapper.show_file(clone, retried["branch"], "feedback.txt") == "addressed\n"


def test_missing_patch_plan_and_missing_contract_fail_closed(tmp_path):
    canonical = _canonical_with_full_series(tmp_path, omit_plan=True)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True
    result = work.implement_announced(clone, announced["ref"])
    assert result["ok"] is False and result["status"] == "patch_plan_missing"

    canonical_two = _canonical_with_full_series(tmp_path / "second", omit_contract=True)
    clone_two = _clone(canonical_two, tmp_path / "clone-b")
    announced_two = announcements.announce(
        clone_two, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced_two["ok"] is True
    result_two = work.implement_announced(clone_two, announced_two["ref"])
    assert result_two["ok"] is False and result_two["status"] == "committed_contract_missing"


def test_child_brief_reads_canonical_cue_network_bodies(tmp_path, monkeypatch):
    canonical = _canonical_with_child_series(tmp_path, artifact_style="cue")
    monkeypatch.setattr(
        code_worker.patch_series_gate,
        "computed_patchwork_check_facts",
        lambda *_args: {"checks": {}, "lifecycle_status": "ready_for_patch_authoring"},
    )

    result = code_worker.load_brief(canonical, SERIES_BRANCH, "sub/codec")

    expected = _brief()
    expected.update(
        node_path="sub/codec",
        node_key="codec",
        cover_letter=_patch_series_view("Child Feature"),
        acceptance_criteria=[],
        must_answer_questions=[],
    )
    assert result == expected
    tree = set(git_wrapper.tree_files(canonical, SERIES_BRANCH))
    assert patch_series_bodies.COVER_PATH in tree
    assert patch_series_bodies.LEGACY_COVER_PATH not in tree
    for name in (
        "maintainer-series-request",
        "patch-series-cover-letter",
        "technical-approach-plan",
    ):
        assert f"sub/codec/{name}.cue" in tree
        assert f"sub/codec/{name}.json" not in tree


def test_child_brief_legacy_json_result_is_unchanged(tmp_path, monkeypatch):
    canonical = _canonical_with_child_series(tmp_path, artifact_style="json")
    monkeypatch.setattr(
        code_worker.patch_series_gate,
        "computed_patchwork_check_facts",
        lambda *_args: {"checks": {}, "lifecycle_status": "ready_for_patch_authoring"},
    )

    result = code_worker.load_brief(canonical, SERIES_BRANCH, "sub/codec")

    expected = _brief()
    expected.update(
        node_path="sub/codec",
        node_key="codec",
        cover_letter=_patch_series_view("Child Feature"),
        acceptance_criteria=[],
        must_answer_questions=[],
    )
    assert result == expected
    tree = set(git_wrapper.tree_files(canonical, SERIES_BRANCH))
    assert patch_series_bodies.COVER_PATH in tree
    assert patch_series_bodies.LEGACY_COVER_PATH not in tree
    assert "sub/codec/patch-series-cover-letter.json" in tree
    assert "sub/codec/patch-series-cover-letter.cue" not in tree


def test_child_brief_missing_both_cover_representations_stays_typed(tmp_path):
    canonical = _canonical_with_full_series(tmp_path)

    result = code_worker.load_brief(canonical, SERIES_BRANCH, "sub/codec")

    assert result == {
        "ok": False,
        "status": "cover_letter_missing",
        "path": "sub/codec/patch-series-cover-letter.json",
        "series_branch": SERIES_BRANCH,
    }


def test_cold_clone_brief_reads_one_snapshot_and_rejects_branch_movement(
    tmp_path, monkeypatch
):
    canonical = _canonical_with_full_series(tmp_path)
    real_head_sha = git_wrapper.head_sha
    real_show_file = git_wrapper.show_file
    snapshot = real_head_sha(canonical, SERIES_BRANCH)
    assert snapshot is not None
    moved_snapshot = "f" * len(snapshot)
    branch_resolutions = 0
    observed_read_refs: list[str] = []

    def moving_head_sha(repo, ref):
        nonlocal branch_resolutions
        if ref == SERIES_BRANCH:
            branch_resolutions += 1
            return snapshot if branch_resolutions == 1 else moved_snapshot
        return real_head_sha(repo, ref)

    def recording_show_file(repo, ref, path):
        observed_read_refs.append(ref)
        return real_show_file(repo, ref, path)

    monkeypatch.setattr(git_wrapper, "head_sha", moving_head_sha)
    monkeypatch.setattr(git_wrapper, "show_file", recording_show_file)
    monkeypatch.setattr(
        code_worker.patch_series_gate,
        "computed_patchwork_check_facts",
        lambda *_args: {"checks": {}, "lifecycle_status": "ready_for_patch_authoring"},
    )

    result = code_worker.load_brief(canonical, SERIES_BRANCH, ".")

    assert result == {
        "ok": False,
        "status": "series_snapshot_changed",
        "series_branch": SERIES_BRANCH,
        "expected_snapshot": snapshot,
        "actual_snapshot": moved_snapshot,
    }
    assert observed_read_refs
    assert set(observed_read_refs) == {snapshot}


# ---------------------------------------------------------------------------
# Engineering precedent facets (Reference timing ② leaf READ / ③ top-up).
# ---------------------------------------------------------------------------

def test_derive_item_terms_is_mechanical_and_deduped_by_store_key():
    item = {
        "item_id": "x",
        "kind": "follow_up",
        "body": {
            "named_content": [
                {"name": "Feature.txt", "kind": "file"},
                {"name": "feature.txt", "kind": "file"},
                {"name": "", "kind": "file"},
            ],
            "adds": "second marker",
            "how_verified": "open the file and read it end to end",
        },
    }
    # named names + adds only (prose condition fields stay out); dedup uses
    # the store's own term-key normalization (case-insensitive).
    assert code_worker.derive_item_terms(item) == ["Feature.txt", "second marker"]


def test_precedent_facets_injected_with_consumption_fields_only():
    engineering_precedent_store.add_preheld("feature.txt", "implementation", _impl_facet())
    engineering_precedent_store.add_preheld("feature.txt", "design", _design_facet())
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])

    facets = code_worker.precedent_facets_for_item(items[0])
    assert facets["store_unavailable"] == "" and facets["read_disabled"] is False
    candidates = facets["terms"]["feature.txt"]
    kinds = sorted(candidate["kind"] for candidate in candidates)
    assert kinds == ["design", "implementation"]
    for candidate in candidates:
        # READ separation canon: consumption fields ONLY, no management tags.
        assert "found_via" not in candidate
        assert "evidence_class" not in candidate
        if candidate["kind"] == "implementation":
            assert set(candidate) == {
                "kind", "snippet", "summary", "pitfalls", "lang_env_version", "author_level", "source_url",
            }
        else:
            assert set(candidate) == {
                "kind", "structure", "rationale", "when_to_use", "when_not_to_use", "tradeoffs",
                "alternatives", "implementation_hooks", "quality_attributes", "evidence",
                "delta_claim", "lang_env_version", "author_level", "source_url",
            }

    window = code_worker.build_item_window(brief, items[0], [], precedent_facets=facets)
    prompt = code_worker.render_item_prompt(window)
    assert "ENGINEERING PRECEDENT FACETS" in prompt
    assert "PRECEDENT-SNIPPET: write the marker atomically" in prompt
    assert "capped by the window law" not in prompt  # nothing truncated here
    assert "org-preheld" not in prompt  # management provenance never leaks


def test_precedent_cap_is_honored_and_recorded_never_silent():
    total = code_worker.PRECEDENT_FACETS_PER_TERM + 2
    for index in range(total):
        engineering_precedent_store.add_preheld(
            "feature.txt", "implementation", _impl_facet(f"PRECEDENT-SNIPPET-{index}")
        )
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])
    facets = code_worker.precedent_facets_for_item(items[0])
    assert len(facets["terms"]["feature.txt"]) == code_worker.PRECEDENT_FACETS_PER_TERM
    assert facets["capped"]["feature.txt"] == {
        "total": total,
        "kept": code_worker.PRECEDENT_FACETS_PER_TERM,
    }
    window = code_worker.build_item_window(brief, items[0], [], precedent_facets=facets)
    prompt = code_worker.render_item_prompt(window)
    assert f"showing {code_worker.PRECEDENT_FACETS_PER_TERM} of {total} stored candidates" in prompt


def test_precedent_miss_tops_up_via_store_expand_and_rereads_the_banked_write(monkeypatch):
    # The store EXISTS (an unrelated term is seeded); the item's terms miss.
    engineering_precedent_store.add_preheld("unrelated seeding term", "implementation", _impl_facet())
    expand_calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_expand(term, context=None, force=False, kinds=None, ctx=None):
        expand_calls.append((term, tuple(kinds or ())))
        if term == "extra.txt":
            # Write-always canon: expand PERSISTS what it learns (simulated
            # here through the store's own writer, as the real expand does).
            engineering_precedent_store.add_preheld("extra.txt", "implementation", _impl_facet("TOPPED-UP"))
        return {"term": term, "candidates": []}

    monkeypatch.setattr(engineering_precedent_store, "expand", fake_expand)
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])
    facets = code_worker.precedent_facets_for_item(items[1])

    adds_term = items[1]["body"]["adds"]
    assert ("extra.txt", ("implementation", "design")) in expand_calls
    assert (adds_term, ("implementation", "design")) in expand_calls
    # Timing ③ proven end to end: the miss expanded, the write banked, and
    # the RE-READ drank it into the same item window.
    assert [candidate["snippet"] for candidate in facets["terms"]["extra.txt"]] == ["TOPPED-UP"]
    # The term expand could not fill stays a recorded miss (drive record).
    assert {"term": adds_term} in facets["misses"]


def test_read_disabled_absent_section_no_topup_and_writes_still_bank(monkeypatch):
    engineering_precedent_store.add_preheld("feature.txt", "implementation", _impl_facet())
    monkeypatch.setenv("AI_ORG_PRECEDENT_READ_DISABLED", "1")
    # The autouse expand guard stays active: any top-up attempt would fail
    # the test — read-disabled misses are the switch reporting, not gaps.
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])
    facets = code_worker.precedent_facets_for_item(items[0])
    assert facets["read_disabled"] is True and facets["terms"] == {} and facets["misses"] == []

    window = code_worker.build_item_window(brief, items[0], [], precedent_facets=facets)
    assert "ENGINEERING PRECEDENT FACETS" not in code_worker.render_item_prompt(window)

    # Write-always through the store's own switch semantics: a write banked
    # while reads are off is served the moment reads come back.
    engineering_precedent_store.add_preheld("banked under switch", "implementation", _impl_facet("BANKED"))
    assert engineering_precedent_store.lookup("banked under switch")["candidates"] == []
    monkeypatch.delenv("AI_ORG_PRECEDENT_READ_DISABLED")
    banked = engineering_precedent_store.lookup("banked under switch")["candidates"]
    assert [candidate["snippet"] for candidate in banked] == ["BANKED"]


def test_missing_store_degrades_typed_without_topup():
    # Autouse fixture points at an absent store file and guards expand: the
    # facets degrade to a typed store_unavailable record, and no top-up runs
    # (expand against a missing store would CREATE one as a side effect).
    brief = _brief()
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])
    facets = code_worker.precedent_facets_for_item(items[0])
    assert facets["terms"] == {} and facets["store_unavailable"] != ""


def test_feedback_item_trailers_keep_binding_oid_git_derived(tmp_path):
    repo = tmp_path / "feedback-trace"
    repo.mkdir()
    _git(repo, "init")
    (repo / "feature.txt").write_text("before\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _seed_commit(repo, "producer-task-binding: materialize root")
    binding_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()

    digest = "b" * 64
    obligation_ids = ("obligation:one", "obligation:two")
    item_id = "patch_plan:demo#acceptance-feedback-2"
    (repo / "feature.txt").write_text("after feedback\n", encoding="utf-8")

    committed = code_worker._commit_feedback(
        repo,
        identity=AUTHOR_A,
        attempt=2,
        item_id=item_id,
        producer_binding={
            "body_sha256": digest,
            "obligation_ids": obligation_ids,
        },
    )

    assert committed["ok"] is True
    message = git_wrapper.commit_message(repo, committed["commit"])
    assert f"{code_worker.producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: {digest}" in message
    assert message.count(f"{code_worker.producer_lifecycle.OBLIGATION_ID_TRAILER}:") == 2
    assert binding_commit not in message
    assert code_worker.producer_lifecycle.item_commit_trace(
        repo, binding_commit, committed["commit"], digest
    ) == [
        {
            "item_id": item_id,
            "commit_oid": committed["commit"],
            "task_binding_body_sha256": digest,
            "production_obligation_ids": list(obligation_ids),
        }
    ]

    (repo / "feature.txt").write_text("invalid self-reference\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(
        repo,
        *git_wrapper.identity_config_args(AUTHOR_A),
        "commit",
        "-m",
        "patch: invalid trace [item:invalid]",
        "-m",
        "\n".join(
            [
                f"{code_worker.producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: {digest}",
                f"{code_worker.producer_lifecycle.OBLIGATION_ID_TRAILER}: obligation:one",
                f"{code_worker.producer_lifecycle.TASK_BINDING_COMMIT_OID_TRAILER}: {binding_commit}",
            ]
        ),
    )
    invalid_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(ValueError, match="stores the binding commit OID"):
        code_worker.producer_lifecycle.item_commit_trace(
            repo, binding_commit, invalid_commit, digest
        )


def test_initial_and_feedback_item_writers_share_exact_binding_trailers(tmp_path):
    repo = tmp_path / "shared-trailer-policy"
    repo.mkdir()
    _git(repo, "init")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _seed_commit(repo, "producer-task-binding: materialize root")
    binding_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    digest = "d" * 64
    obligations = ("obligation:one", "obligation:two")
    item_id = "patch_plan:demo#first"

    (repo / "feature.txt").write_text("initial\n", encoding="utf-8")
    initial = code_worker._commit_item(
        repo,
        repo,
        {
            "cover_letter": {"working_title": "Demo"},
            "series_branch": "ai-org/patch-series/demo",
        },
        {"item_id": item_id, "kind": "follow_up"},
        {"referenced_node_ids": [], "files_touched_so_far": []},
        identity=AUTHOR_A,
        repair_rounds=0,
        producer_binding={
            "body_sha256": digest,
            "obligation_ids_by_item": {item_id: obligations},
        },
    )
    assert initial["ok"] is True

    (repo / "feature.txt").write_text("feedback\n", encoding="utf-8")
    feedback = code_worker._commit_feedback(
        repo,
        identity=AUTHOR_A,
        attempt=2,
        item_id="patch_plan:demo#acceptance-feedback-2",
        producer_binding={
            "body_sha256": digest,
            "obligation_ids": obligations,
        },
    )
    assert feedback["ok"] is True

    trace = code_worker.producer_lifecycle.item_commit_trace(
        repo, binding_commit, feedback["commit"], digest
    )
    assert [row["commit_oid"] for row in trace] == [
        initial["commit"],
        feedback["commit"],
    ]
    assert all(row["production_obligation_ids"] == list(obligations) for row in trace)
    for commit in (initial["commit"], feedback["commit"]):
        message = git_wrapper.commit_message(repo, commit)
        assert binding_commit not in message
        assert message.count(
            f"{code_worker.producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}:"
        ) == 1
        assert message.count(
            f"{code_worker.producer_lifecycle.OBLIGATION_ID_TRAILER}:"
        ) == 2


def test_initial_item_writer_keeps_unbound_item_out_of_commit_trace(tmp_path):
    repo = tmp_path / "unbound-item"
    repo.mkdir()
    _git(repo, "init")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _seed_commit(repo, "producer-task-binding: materialize root")
    binding_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    digest = "d" * 64

    (repo / "diagnostics.txt").write_text("diagnostics\n", encoding="utf-8")
    committed = code_worker._commit_item(
        repo,
        repo,
        {
            "cover_letter": {"working_title": "Demo"},
            "series_branch": "ai-org/patch-series/demo",
        },
        {"item_id": "patch_plan:demo#diagnostics", "kind": "follow_up"},
        {"referenced_node_ids": [], "files_touched_so_far": []},
        identity=AUTHOR_A,
        repair_rounds=0,
        producer_binding={
            "body_sha256": digest,
            "obligation_ids_by_item": {
                "patch_plan:demo#bound": ("obligation:one",),
            },
        },
    )

    assert committed["ok"] is True
    message = git_wrapper.commit_message(repo, committed["commit"])
    assert code_worker.producer_lifecycle.TASK_BINDING_DIGEST_TRAILER not in message
    assert code_worker.producer_lifecycle.OBLIGATION_ID_TRAILER not in message
    assert code_worker.producer_lifecycle.item_commit_trace(
        repo,
        binding_commit,
        committed["commit"],
        digest,
    ) == []


def test_initial_item_writer_rejects_empty_required_claim_set(tmp_path):
    repo = tmp_path / "empty-required-claim"
    repo.mkdir()
    _git(repo, "init")
    (repo / "feature.txt").write_text("implementation\n", encoding="utf-8")

    committed = code_worker._commit_item(
        repo,
        repo,
        {
            "cover_letter": {"working_title": "Demo"},
            "series_branch": "ai-org/patch-series/demo",
        },
        {"item_id": "patch_plan:demo#bound", "kind": "follow_up"},
        {"referenced_node_ids": [], "files_touched_so_far": []},
        identity=AUTHOR_A,
        repair_rounds=0,
        producer_binding={
            "body_sha256": "d" * 64,
            "obligation_ids_by_item": {"patch_plan:demo#bound": ()},
        },
    )

    assert committed == {
        "ok": False,
        "status": "producer_task_binding_invalid",
        "detail": "item binding obligation ids are missing",
    }


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

def _canonical_with_full_series(
    base_dir: Path,
    *,
    follow_up_check: list[str] | None = None,
    first_proof_check: object = DEFAULT_FIRST_PROOF_CHECK,
    omit_plan: bool = False,
    omit_contract: bool = False,
) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    canonical = base_dir / "canonical.git"
    subprocess.run(["git", "init", "--bare", str(canonical)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(canonical), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    )
    seed = base_dir / "seed"
    seed.mkdir()
    _git(seed, "init")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _seed_commit(seed, "base")
    _git(seed, "branch", "-M", "main")
    _git(seed, "checkout", "-B", SERIES_BRANCH, "main")

    approach = _approach_with_plan(follow_up_check=follow_up_check, first_proof_check=first_proof_check)
    if omit_plan:
        del approach["problem"]["question"]["decision"]["implementation"]["patch_plan"]  # type: ignore[index]
    files: dict[str, object] = {
        "patch-series-cover-letter.json": _patch_series_view(),
        "technical-approach-plan.json": approach,
        "patch-series-manifest.json": {
            "schema": "patch_series-network-node-v1",
            "lifecycle_status": "ready_for_patch_authoring",
            "edges": [],
            "declared_patchwork_checks": [],
            "acceptance_criteria": ["feature.txt carries the implemented marker"],
        },
    }
    if not omit_contract:
        files[patch_author_contract.IMPLEMENTATION_RESULT_SCHEMA_PATH] = (
            patch_author_contract.implementation_result_schema()
        )
    for rel_path, payload in files.items():
        path = seed / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        _git(seed, "add", rel_path)
    _seed_commit(seed, "patch_series: receive Add Feature File")
    _seed_commit(seed, "patch_series: direction-ok", allow_empty=True)
    _git(seed, "checkout", "main")
    _git(seed, "remote", "add", "origin", str(canonical))
    _git(seed, "push", "origin", "main", SERIES_BRANCH)
    return canonical


def _canonical_with_child_series(base_dir: Path, *, artifact_style: str) -> Path:
    import test_network_bodies as network_harness

    canonical = _canonical_with_full_series(base_dir)
    parent = git_wrapper.head_sha(canonical, SERIES_BRANCH)
    assert parent is not None
    files: dict[str, object] = {
        path: content
        for path in git_wrapper.tree_files(canonical, parent)
        if (content := git_wrapper.show_file(canonical, parent, path)) is not None
    }
    network_files = network_harness._files()
    child_cover = _patch_series_view("Child Feature")
    network_files["sub/codec/patch-series-cover-letter.json"] = child_cover
    request = network_files["sub/codec/maintainer-series-request.json"]
    network_files["sub/codec/maintainer-series-request.json"] = {
        **request,
        "request": child_cover,
    }
    if artifact_style == "cue":
        prepared = network_bodies.prepare_network_publication(network_files)
        for alias in prepared.aliases:
            files.pop(alias, None)
        files.update(prepared.files)
    elif artifact_style == "json":
        files.update(network_files)
    else:  # pragma: no cover - fixture callers use the two admitted styles
        raise ValueError(f"unknown child artifact style: {artifact_style}")
    root_cover = _patch_series_view()
    root_cohort = patch_series_bodies.prepare_patch_series_cohort(
        root_cover,
        {
            "request_id": "code-worker-child-fixture",
            "payload_sha256": "a" * 64,
            "raw_request": root_cover["raw_request"],
            "request_payload": {"raw_request": root_cover["raw_request"]},
            "memento": "off-Git intake is ingress only",
        },
    )
    files.pop(patch_series_bodies.LEGACY_COVER_PATH, None)
    files.pop(patch_series_bodies.LEGACY_PROVENANCE_PATH, None)
    files.update(root_cohort.files())
    git_wrapper.create_ref_with_files(
        canonical,
        f"refs/heads/{SERIES_BRANCH}",
        files,
        subject=f"fixture: add {artifact_style} child network",
        parent=parent,
        identity={"name": "Seed", "email": "seed@example.invalid"},
    )
    return canonical


def _clone(canonical: Path, dest: Path) -> Path:
    result = git_wrapper.clone_repository(canonical, dest)
    assert result["ok"] is True
    return dest


def _seed_commit(repo: Path, message: str, *, allow_empty: bool = False) -> None:
    args = ["-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "-m", message]
    if allow_empty:
        args.insert(5, "--allow-empty")
    _git(repo, *args)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
