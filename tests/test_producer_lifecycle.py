from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from ai_org import git_wrapper, patch_series_bodies
from ai_org.body_codec import BodyCodecClient
from ai_org.patch_author import announcements, producer_lifecycle


AUTHOR = {"name": "Author A", "email": "101+author-a@users.noreply.github.com"}
OID = "a" * 40
SHA256 = "b" * 64


def test_initial_pair_is_atomic_and_family_obligation_coordinates_are_distinct(tmp_path):
    remote, clone = _remote_clone(tmp_path)
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    announcement_ref = "refs/ai-org/authoring-announcements/v2/" + "1" * 64
    contribution_ref = "refs/heads/ai-org/contrib/demo"
    announcement = git_wrapper.create_ref_with_files(
        clone,
        announcement_ref,
        {"announcement.cue": "visibility: true\n"},
        subject="authoring-intent: demo",
        parent=parent,
        identity=AUTHOR,
        update_ref=False,
    )
    promise = git_wrapper.create_ref_with_files(
        clone,
        contribution_ref,
        {"producer-evidence/" + "2" * 64 + "/producer-promise.cue": "promise: true\n"},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} demo",
        parent=parent,
        identity=AUTHOR,
        update_ref=False,
        inherit_parent_tree=True,
    )

    publication = git_wrapper.push_atomic_create_refs(
        clone,
        "origin",
        {announcement_ref: announcement["commit"], contribution_ref: promise["commit"]},
    )

    assert publication.ok is True and publication.atomic is True
    assert dict(publication.resulting_ref_oids) == {
        announcement_ref: announcement["commit"],
        contribution_ref: promise["commit"],
    }
    assert dict(publication.expected_ref_oids) == {
        announcement_ref: "",
        contribution_ref: "",
    }
    assert set(git_wrapper.ls_remote(clone, "origin", announcement_ref, contribution_ref)["refs"]) == {
        announcement_ref,
        contribution_ref,
    }

    # If either coordinate is occupied, the other create is not partially released.
    unused_ref = "refs/heads/ai-org/contrib/must-not-exist"
    rejected = git_wrapper.push_atomic_create_refs(
        clone,
        "origin",
        {announcement_ref: promise["commit"], unused_ref: promise["commit"]},
    )
    assert rejected.ok is False and rejected.atomic is True
    assert rejected.resulting_ref_oids == ()
    assert git_wrapper.ls_remote(clone, "origin", unused_ref)["refs"] == {}

    coordinate_a = producer_lifecycle.evidence_coordinate(
        "ai-org/patch-series/demo", ".", "ai-org/contrib/demo", AUTHOR, SHA256, "obligation:one"
    )
    coordinate_b = producer_lifecycle.evidence_coordinate(
        "ai-org/patch-series/demo", ".", "ai-org/contrib/demo-r2",
        {"name": "Author B", "email": "102+author-b@users.noreply.github.com"},
        SHA256, "obligation:one",
    )
    coordinate_c = producer_lifecycle.evidence_coordinate(
        "ai-org/patch-series/demo", ".", "ai-org/contrib/demo",
        AUTHOR, SHA256, "obligation:two",
    )
    coordinate_d = producer_lifecycle.evidence_coordinate(
        "ai-org/patch-series/demo", ".", "ai-org/contrib/demo",
        AUTHOR, "c" * 64, "obligation:one",
    )
    normalized_a = producer_lifecycle.evidence_coordinate(
        "ai-org/patch-series/demo", ".", "ai-org/contrib/demo",
        {"name": "  Author A  ", "email": AUTHOR["email"].upper()},
        SHA256, "obligation:one",
    )
    assert producer_lifecycle.EVIDENCE_COORDINATE_VERSION == (
        "producer-evidence-coordinate-v1"
    )
    assert len({coordinate_a, coordinate_b, coordinate_c, coordinate_d}) == 4
    assert normalized_a == coordinate_a
    assert producer_lifecycle.is_evidence_path(coordinate_a)


@pytest.mark.parametrize(
    ("overrides", "detail"),
    (
        ({"series_branch": "ai-org/patch-series/"}, "series branch"),
        ({"node_path": "sub/NOT-NORMALIZED"}, "node path"),
        ({"contribution_branch": "ai-org/contrib/"}, "contribution branch"),
        (
            {"producer": {"name": "Author A", "email": "author@example.com"}},
            "identity",
        ),
        (
            {"producer": {"name": None, "email": AUTHOR["email"]}},
            "identity",
        ),
        ({"canonical_root_body_sha256": "B" * 64}, "root digest"),
        ({"obligation_id": ""}, "obligation id"),
    ),
)
def test_evidence_coordinate_rejects_invalid_inputs_before_hashing(
    overrides, detail
):
    inputs = {
        "series_branch": "ai-org/patch-series/demo",
        "node_path": ".",
        "contribution_branch": "ai-org/contrib/demo",
        "producer": AUTHOR,
        "canonical_root_body_sha256": SHA256,
        "obligation_id": "obligation:one",
    }
    inputs.update(overrides)

    with pytest.raises(ValueError, match=detail):
        producer_lifecycle.evidence_coordinate(**inputs)


def test_inherited_competitor_evidence_is_not_a_family_or_submission_input(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    codec = BodyCodecClient()
    competitor = _promise_body("ai-org/contrib/competitor")
    competitor["producer"] = {
        "name": "Author B",
        "email": "102+author-b@users.noreply.github.com",
    }
    competitor_path = producer_lifecycle.evidence_coordinate(
        str(competitor["series_branch"]),
        str(competitor["node_path"]),
        str(competitor["contribution_branch"]),
        competitor["producer"],
        str(competitor["canonical_root_body_sha256"]),
        str(competitor["obligation_id"]),
    )
    competitor_prepared = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        competitor,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    base = git_wrapper.commit_files(
        clone,
        "main",
        {competitor_path: competitor_prepared.canonical.data.decode("utf-8")},
        subject="accepted historical producer evidence",
        identity=AUTHOR,
    )["commit"]

    branch = "ai-org/contrib/current-family"
    remote_ref = f"refs/heads/{branch}"
    bodies = []
    files = {}
    for suffix in ("one", "two"):
        body = _promise_body(branch)
        body["obligation_id"] = f"obligation:{suffix}"
        body["referee_goal_id"] = f"goal:{suffix}"
        path = producer_lifecycle.evidence_coordinate(
            str(body["series_branch"]),
            str(body["node_path"]),
            branch,
            AUTHOR,
            str(body["canonical_root_body_sha256"]),
            str(body["obligation_id"]),
        )
        prepared = codec.prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            body,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        bodies.append((body, path))
        files[path] = prepared.canonical.data.decode("utf-8")
    initialized = git_wrapper.create_ref_with_files(
        clone,
        remote_ref,
        files,
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} current-family",
        parent=base,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert git_wrapper.push_create(
        clone, "origin", remote_ref, initialized["commit"]
    )["ok"]

    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True
    assert producer_lifecycle.has_implementation_submission(clone, branch) is False
    transitioned = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "commitment",
        {
            str(body["obligation_id"]): {
                "task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
                "task_binding_body_sha256": "c" * 64,
            }
            for body, _path in bodies
        },
    )

    assert transitioned["ok"] is True
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True
    assert git_wrapper.show_file(clone, branch, competitor_path) == (
        competitor_prepared.canonical.data.decode("utf-8")
    )


def test_announcement_initializes_the_promise_only_contribution_ref_atomically(tmp_path, monkeypatch):
    _remote, clone = _remote_clone(tmp_path)
    series_branch = "ai-org/patch-series/demo"
    series = git_wrapper.create_branch_with_files(
        clone,
        series_branch,
        "main",
        {"patch-series-cover-letter.json": {"working_title": "Demo"}},
        commit_message="patch_series: direction-ok",
    )
    assert git_wrapper.push_create(
        clone, "origin", f"refs/heads/{series_branch}", series["commit"]
    )["ok"] is True

    def prepared_initialization(_repo, series_head, record):
        body = _promise_body(str(record["contrib_branch"]))
        body["series_snapshot_oid"] = series_head
        body["series_branch"] = series_branch
        prepared = BodyCodecClient().prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            body,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        path = producer_lifecycle.evidence_coordinate(
            series_branch, ".", str(record["contrib_branch"]),
            AUTHOR, body["canonical_root_body_sha256"], "obligation:one",
        )
        return producer_lifecycle.PromiseInitialization(
            "ready",
            {path: prepared.canonical.data.decode("utf-8")},
            {"obligation:one": path},
        )

    monkeypatch.setattr(
        producer_lifecycle, "prepare_initialization", prepared_initialization
    )
    initialized = announcements.announce(
        clone,
        "origin",
        series_branch,
        author_name=AUTHOR["name"],
        author_email=AUTHOR["email"],
    )

    assert initialized["ok"] is True
    assert initialized["status"] == "authoring_promise_initialized"
    assert initialized["publication"].atomic is True
    remote_refs = git_wrapper.ls_remote(
        clone, "origin", initialized["ref"], initialized["contribution_ref"]
    )["refs"]
    assert set(remote_refs) == {initialized["ref"], initialized["contribution_ref"]}
    assert producer_lifecycle.is_promise_only_contribution(
        clone, initialized["record"]["contrib_branch"]
    ) is True


def test_announcement_rejects_an_aliased_promise_cohort_before_publication(
    tmp_path, monkeypatch
):
    _remote, clone = _remote_clone(tmp_path)
    series_branch = "ai-org/patch-series/demo"
    series = git_wrapper.create_branch_with_files(
        clone,
        series_branch,
        "main",
        {"patch-series-cover-letter.json": {"working_title": "Demo"}},
        commit_message="patch_series: direction-ok",
    )
    assert git_wrapper.push_create(
        clone, "origin", f"refs/heads/{series_branch}", series["commit"]
    )["ok"] is True

    def aliased_initialization(_repo, series_head, record):
        body = _promise_body(str(record["contrib_branch"]))
        body["series_snapshot_oid"] = series_head
        body["series_branch"] = series_branch
        prepared = BodyCodecClient().prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            body,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        path = producer_lifecycle.evidence_coordinate(
            series_branch,
            ".",
            str(record["contrib_branch"]),
            AUTHOR,
            str(body["canonical_root_body_sha256"]),
            "obligation:one",
        )
        return producer_lifecycle.PromiseInitialization(
            "ready",
            {path: prepared.canonical.data.decode("utf-8")},
            {"obligation:one": path, "obligation:two": path},
        )

    monkeypatch.setattr(
        producer_lifecycle, "prepare_initialization", aliased_initialization
    )
    rejected = announcements.announce(
        clone,
        "origin",
        series_branch,
        author_name=AUTHOR["name"],
        author_email=AUTHOR["email"],
    )

    assert rejected["ok"] is False
    assert rejected["status"] == "producer_promise_invalid"
    assert "not distinct" in rejected["detail"]
    assert git_wrapper.ls_remote(
        clone, "origin", rejected["ref"], "refs/heads/ai-org/contrib/demo"
    )["refs"] == {}


def test_initialization_rejects_prebound_promise_slots():
    body = _promise_body("ai-org/contrib/prebound")
    body["commitment_slot"] = {
        "task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
        "task_binding_body_sha256": "c" * 64,
    }
    path = producer_lifecycle.evidence_coordinate(
        str(body["series_branch"]),
        str(body["node_path"]),
        str(body["contribution_branch"]),
        AUTHOR,
        str(body["canonical_root_body_sha256"]),
        str(body["obligation_id"]),
    )
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    initialization = producer_lifecycle.PromiseInitialization(
        "ready",
        {path: prepared.canonical.data.decode("utf-8")},
        {str(body["obligation_id"]): path},
    )

    with pytest.raises(ValueError, match="commitment slot is already bound"):
        producer_lifecycle.validate_initialization_cohort(initialization)


def test_promise_slot_transitions_use_cas_and_branch_presence_is_not_submission(tmp_path, monkeypatch):
    _remote, clone = _remote_clone(tmp_path)
    branch = "ai-org/contrib/demo"
    remote_ref = f"refs/heads/{branch}"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    body = _promise_body(branch)
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    path = producer_lifecycle.evidence_coordinate(
        body["series_branch"], body["node_path"], branch,
        AUTHOR, body["canonical_root_body_sha256"], body["obligation_id"],
    )
    initial = git_wrapper.create_ref_with_files(
        clone,
        remote_ref,
        {path: prepared.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} demo",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert git_wrapper.push_create(clone, "origin", remote_ref, initial["commit"])["ok"] is True
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True
    assert producer_lifecycle.has_implementation_submission(clone, branch) is False

    observed: list[dict[str, str]] = []
    reject_next = True
    real_push_cas = git_wrapper.push_cas

    def recording_push_cas(repo, remote, ref, commit, expected):
        nonlocal reject_next
        observed.append({"ref": ref, "expected": expected})
        if reject_next:
            reject_next = False
            return {"ok": False, "status": "rejected", "detail": "simulated CAS loss"}
        return real_push_cas(repo, remote, ref, commit, expected)

    monkeypatch.setattr(git_wrapper, "push_cas", recording_push_cas)
    lost = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "commitment",
        {
            "obligation:one": {
                "task_binding_path": "producer-task-binding.cue",
                "task_binding_body_sha256": "c" * 64,
            }
        },
    )

    assert lost["ok"] is False and lost["status"] == "promise_transition_conflict"
    assert git_wrapper.ls_remote(clone, "origin", remote_ref)["refs"][remote_ref] == initial["commit"]
    assert git_wrapper.head_sha(clone, branch) == initial["commit"]

    transitioned = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "commitment",
        {
            "obligation:one": {
                "task_binding_path": "producer-task-binding.cue",
                "task_binding_body_sha256": "c" * 64,
            }
        },
        authored_at="2026-07-14T01:00:00Z",
    )

    assert transitioned["ok"] is True
    assert transitioned["previous_commit"] == initial["commit"]
    assert transitioned["expected_ref_oids"] == {remote_ref: initial["commit"]}
    assert transitioned["resulting_ref_oids"] == {
        remote_ref: transitioned["commit"]
    }
    assert observed == [
        {"ref": remote_ref, "expected": initial["commit"]},
        {"ref": remote_ref, "expected": initial["commit"]},
    ]
    current_raw = git_wrapper.show_file(clone, branch, path)
    assert current_raw is not None
    current = patch_series_bodies.read_producer_promise(current_raw)
    assert current["previous_promise_commit_oid"] == initial["commit"]
    assert current["commitment_slot"]["task_binding_path"] == "producer-task-binding.cue"
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True

    completed = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "completion_claim",
        {
            "obligation:one": {
                "assertion_path": "producer-completion-assertion.cue",
                "assertion_body_sha256": "d" * 64,
            }
        },
        authored_at="2026-07-14T02:00:00Z",
    )

    assert completed["ok"] is True
    assert completed["previous_commit"] == transitioned["commit"]
    assert completed["expected_ref_oids"] == {
        remote_ref: transitioned["commit"]
    }
    assert completed["resulting_ref_oids"] == {remote_ref: completed["commit"]}
    assert observed[-1] == {
        "ref": remote_ref,
        "expected": transitioned["commit"],
    }
    completed_raw = git_wrapper.show_file(clone, branch, path)
    assert completed_raw is not None
    completed_promise = patch_series_bodies.read_producer_promise(completed_raw)
    assert completed_promise["previous_promise_commit_oid"] == transitioned["commit"]
    assert completed_promise["completion_claim_slot"]["assertion_path"] == (
        "producer-completion-assertion.cue"
    )
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True

    replayed = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "completion_claim",
        {
            "obligation:one": {
                "assertion_path": "producer-completion-assertion.cue",
                "assertion_body_sha256": "e" * 64,
            }
        },
    )

    assert replayed["ok"] is False
    assert replayed["status"] == "promise_transition_invalid"
    assert "already bound" in replayed["detail"]
    assert len(observed) == 3

    # A lifecycle-looking subject cannot conceal implementation content.
    git_wrapper.commit_files(
        clone,
        branch,
        {"feature.txt": "implemented\n"},
        subject=f"{producer_lifecycle.TRANSITION_SUBJECT_PREFIX} disguised-implementation",
        identity=AUTHOR,
    )
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is False
    assert producer_lifecycle.has_implementation_submission(clone, branch) is True


@pytest.mark.parametrize(
    ("tamper", "detail"),
    (("coordinate", "coordinate mismatch"), ("eligibility", "is ineligible")),
)
def test_promise_transition_rejects_invalid_family_evidence(
    tmp_path, tamper, detail
):
    _remote, clone = _remote_clone(tmp_path)
    branch = f"ai-org/contrib/invalid-{tamper}"
    remote_ref = f"refs/heads/{branch}"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    body = _promise_body(branch)
    if tamper == "eligibility":
        body["eligibility_decision"] = "ineligible"
        body["eligibility_basis"] = "family self-evaluation failed"
    coordinate = producer_lifecycle.evidence_coordinate(
        str(body["series_branch"]),
        str(body["node_path"]),
        branch,
        AUTHOR,
        str(body["canonical_root_body_sha256"]),
        str(body["obligation_id"]),
    )
    if tamper == "coordinate":
        coordinate = f"producer-evidence/{'f' * 64}/producer-promise.cue"
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    initialized = git_wrapper.create_ref_with_files(
        clone,
        remote_ref,
        {coordinate: prepared.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} invalid",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert git_wrapper.push_create(
        clone, "origin", remote_ref, initialized["commit"]
    )["ok"]

    rejected = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "commitment",
        {
            "obligation:one": {
                "task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
                "task_binding_body_sha256": "c" * 64,
            }
        },
    )

    assert rejected["ok"] is False
    assert rejected["status"] == "promise_transition_invalid"
    assert detail in rejected["detail"]
    assert (
        git_wrapper.ls_remote(clone, "origin", remote_ref)["refs"][remote_ref]
        == initialized["commit"]
    )


def test_promise_transition_cas_loss_reports_no_resulting_oid_and_changes_no_ref(
    tmp_path, monkeypatch
):
    _remote, clone = _remote_clone(tmp_path)
    branch = "ai-org/contrib/cas-loss"
    remote_ref = f"refs/heads/{branch}"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    body = _promise_body(branch)
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    path = producer_lifecycle.evidence_coordinate(
        str(body["series_branch"]),
        str(body["node_path"]),
        branch,
        AUTHOR,
        str(body["canonical_root_body_sha256"]),
        str(body["obligation_id"]),
    )
    initial = git_wrapper.create_ref_with_files(
        clone,
        remote_ref,
        {path: prepared.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} cas-loss",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert git_wrapper.push_create(
        clone, "origin", remote_ref, initial["commit"]
    )["ok"]
    refs_before = git_wrapper.list_refs(clone, "refs")

    monkeypatch.setattr(
        git_wrapper,
        "push_cas",
        lambda *_args, **_kwargs: {
            "ok": False,
            "status": "rejected",
            "detail": "injected CAS loss",
        },
    )
    lost = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "commitment",
        {
            "obligation:one": {
                "task_binding_path": "producer-task-binding.cue",
                "task_binding_body_sha256": "c" * 64,
            }
        },
    )

    assert lost["ok"] is False
    assert lost["status"] == "promise_transition_conflict"
    assert lost["expected_ref_oids"] == {remote_ref: initial["commit"]}
    assert lost["resulting_ref_oids"] == {}
    assert git_wrapper.list_refs(clone, "refs") == refs_before
    assert git_wrapper.ls_remote(clone, "origin", remote_ref)["refs"] == {
        remote_ref: initial["commit"]
    }


def test_promise_only_transitions_cannot_remove_creation_sealed_coordinates(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    branch = "ai-org/contrib/sealed-promise"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    body = _promise_body(branch)
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    coordinate = producer_lifecycle.evidence_coordinate(
        str(body["series_branch"]),
        str(body["node_path"]),
        branch,
        AUTHOR,
        str(body["canonical_root_body_sha256"]),
        str(body["obligation_id"]),
    )
    initialized = git_wrapper.create_ref_with_files(
        clone,
        f"refs/heads/{branch}",
        {coordinate: prepared.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} sealed",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True

    removed = git_wrapper.commit_files(
        clone,
        branch,
        {},
        subject=f"{producer_lifecycle.TRANSITION_SUBJECT_PREFIX} delete",
        identity=AUTHOR,
        remove_paths=(coordinate,),
    )

    assert initialized["commit"] != removed["commit"]
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is False
    assert producer_lifecycle.has_implementation_submission(clone, branch) is True


def test_partial_promise_transition_is_an_implementation_submission(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    branch = "ai-org/contrib/partial-promise-transition"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    codec = BodyCodecClient()
    files = {}
    promises = []
    for suffix in ("one", "two"):
        body = _promise_body(branch)
        body["obligation_id"] = f"obligation:{suffix}"
        body["referee_goal_id"] = f"goal:{suffix}"
        path = producer_lifecycle.evidence_coordinate(
            str(body["series_branch"]), str(body["node_path"]), branch,
            AUTHOR, str(body["canonical_root_body_sha256"]),
            str(body["obligation_id"]),
        )
        prepared = codec.prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            body,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        files[path] = prepared.canonical.data.decode("utf-8")
        promises.append((body, path))
    initialized = git_wrapper.create_ref_with_files(
        clone,
        f"refs/heads/{branch}",
        files,
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} partial",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True

    changed, path = promises[0]
    changed["commitment_slot"] = {
        "task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
        "task_binding_body_sha256": "c" * 64,
    }
    changed["previous_promise_commit_oid"] = initialized["commit"]
    prepared_changed = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        changed,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    git_wrapper.commit_files(
        clone,
        branch,
        {path: prepared_changed.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.TRANSITION_SUBJECT_PREFIX} partial",
        identity=AUTHOR,
    )

    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is False
    assert producer_lifecycle.has_implementation_submission(clone, branch) is True


def test_exact_cohort_body_tampering_is_an_implementation_submission(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    branch = "ai-org/contrib/tampered-promise-transition"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    body = _promise_body(branch)
    path = producer_lifecycle.evidence_coordinate(
        str(body["series_branch"]),
        str(body["node_path"]),
        branch,
        AUTHOR,
        str(body["canonical_root_body_sha256"]),
        str(body["obligation_id"]),
    )
    codec = BodyCodecClient()
    initial = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    initialized = git_wrapper.create_ref_with_files(
        clone,
        f"refs/heads/{branch}",
        {path: initial.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} tamper",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True

    body["deliverable"] = "silently-retargeted.txt"
    body["commitment_slot"] = {
        "task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
        "task_binding_body_sha256": "c" * 64,
    }
    body["previous_promise_commit_oid"] = initialized["commit"]
    tampered = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    git_wrapper.commit_files(
        clone,
        branch,
        {path: tampered.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.TRANSITION_SUBJECT_PREFIX} commitment",
        identity=AUTHOR,
    )

    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is False
    assert producer_lifecycle.has_implementation_submission(clone, branch) is True


def test_local_promise_ref_refresh_uses_compare_and_swap(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    ref = "refs/heads/ai-org/contrib/local-cas"
    original = git_wrapper.head_sha(clone, "main")
    assert original is not None
    replacement = git_wrapper.create_ref_with_files(
        clone,
        "refs/heads/prepared-local-cas",
        {"prepared.txt": "prepared\n"},
        subject="prepare local CAS replacement",
        parent=original,
        identity=AUTHOR,
        update_ref=False,
        inherit_parent_tree=True,
    )["commit"]
    assert git_wrapper.update_ref(clone, ref, original)["ok"] is True

    stale = git_wrapper.update_ref(clone, ref, replacement, expected="f" * 40)

    assert stale["ok"] is False
    assert git_wrapper.head_sha(clone, ref) == original
    assert git_wrapper.update_ref(
        clone, ref, replacement, expected=original
    )["ok"] is True
    assert git_wrapper.head_sha(clone, ref) == replacement


def test_later_initialization_cannot_reopen_creation_sealed_promise_cohort(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    branch = "ai-org/contrib/sealed-promise"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    body = _promise_body(branch)
    initial_path = producer_lifecycle.evidence_coordinate(
        str(body["series_branch"]),
        str(body["node_path"]),
        branch,
        AUTHOR,
        str(body["canonical_root_body_sha256"]),
        str(body["obligation_id"]),
    )
    initial = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    git_wrapper.create_ref_with_files(
        clone,
        f"refs/heads/{branch}",
        {initial_path: initial.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} sealed",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is True

    added_body = _promise_body(branch)
    added_body["obligation_id"] = "obligation:two"
    added_path = producer_lifecycle.evidence_coordinate(
        str(added_body["series_branch"]),
        str(added_body["node_path"]),
        branch,
        AUTHOR,
        str(added_body["canonical_root_body_sha256"]),
        str(added_body["obligation_id"]),
    )
    added = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        added_body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    git_wrapper.commit_files(
        clone,
        branch,
        {added_path: added.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} reopen",
        identity=AUTHOR,
    )

    assert producer_lifecycle.is_promise_only_contribution(clone, branch) is False
    assert producer_lifecycle.has_implementation_submission(clone, branch) is True


def test_promise_transition_rejects_out_of_order_and_foreign_family_state(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    branch = "ai-org/contrib/demo"
    remote_ref = f"refs/heads/{branch}"
    parent = git_wrapper.head_sha(clone, "origin/main")
    assert parent is not None
    body = _promise_body(branch)
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    coordinate = producer_lifecycle.evidence_coordinate(
        str(body["series_branch"]),
        str(body["node_path"]),
        branch,
        AUTHOR,
        str(body["canonical_root_body_sha256"]),
        str(body["obligation_id"]),
    )
    initial = git_wrapper.create_ref_with_files(
        clone,
        remote_ref,
        {coordinate: prepared.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} demo",
        parent=parent,
        identity=AUTHOR,
        inherit_parent_tree=True,
    )
    assert git_wrapper.push_create(clone, "origin", remote_ref, initial["commit"])["ok"]

    out_of_order = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "completion_claim",
        {
            "obligation:one": {
                "assertion_path": "producer-completion-assertion.cue",
                "assertion_body_sha256": "d" * 64,
            }
        },
    )

    assert out_of_order["ok"] is False
    assert out_of_order["status"] == "promise_transition_invalid"
    assert "commitment is missing" in out_of_order["detail"]
    assert git_wrapper.ls_remote(clone, "origin", remote_ref)["refs"][remote_ref] == initial["commit"]

    foreign_author = {
        "name": "Author B",
        "email": "102+author-b@users.noreply.github.com",
    }
    foreign_body = _promise_body(branch)
    foreign_body["obligation_id"] = "obligation:two"
    foreign_body["producer"] = foreign_author
    foreign_prepared = BodyCodecClient().prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        foreign_body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    foreign_path = producer_lifecycle.evidence_coordinate(
        str(foreign_body["series_branch"]),
        str(foreign_body["node_path"]),
        branch,
        foreign_author,
        str(foreign_body["canonical_root_body_sha256"]),
        str(foreign_body["obligation_id"]),
    )
    foreign = git_wrapper.create_ref_with_files(
        clone,
        remote_ref,
        {foreign_path: foreign_prepared.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.TRANSITION_SUBJECT_PREFIX} tamper",
        parent=initial["commit"],
        identity=AUTHOR,
        update_ref=False,
        inherit_parent_tree=True,
    )
    assert git_wrapper.push_cas(
        clone, "origin", remote_ref, foreign["commit"], initial["commit"]
    )["ok"]

    crossed_family = producer_lifecycle.transition_slots(
        clone,
        "origin",
        branch,
        "commitment",
        {
            "obligation:one": {
                "task_binding_path": "producer-task-binding.cue",
                "task_binding_body_sha256": "c" * 64,
            },
            "obligation:two": {
                "task_binding_path": "producer-task-binding.cue",
                "task_binding_body_sha256": "c" * 64,
            },
        },
    )

    assert crossed_family["ok"] is False
    assert crossed_family["status"] == "promise_transition_invalid"
    assert "crosses producer families" in crossed_family["detail"]
    assert (
        git_wrapper.ls_remote(clone, "origin", remote_ref)["refs"][remote_ref]
        == foreign["commit"]
    )


def test_canonical_promise_initialization_stays_behind_closed_readiness_gate(tmp_path, monkeypatch):
    _remote, clone = _remote_clone(tmp_path)

    class Decision:
        blocked = True
        diagnostic = SimpleNamespace(detail="preview-only readiness gate")
        source = SimpleNamespace(
            route=producer_lifecycle.PROMISE_READINESS_ROUTE,
            frozen_root_oid=OID,
        )

        def as_dict(self):
            return {"vet_passed": True, "authorable": False}

    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda _repo, _ref: SimpleNamespace(
            generation=patch_series_bodies.ROOT_GENERATION_V2
        ),
    )
    from ai_org.patchwork_queue import patch_series_gate

    monkeypatch.setattr(
        patch_series_gate,
        "vet_producer_pair_closure",
        lambda *_args, **_kwargs: Decision(),
    )
    record = {
        "series_branch": "ai-org/patch-series/demo",
        "node_path": ".",
        "contrib_branch": "ai-org/contrib/demo",
        "author": AUTHOR,
    }

    initialization = producer_lifecycle.prepare_initialization(clone, OID, record)

    assert initialization.status == "blocked"
    assert initialization.files == {}
    assert initialization.obligation_coordinates == {}


@pytest.mark.parametrize(
    ("route", "frozen_root_oid"),
    (
        (producer_lifecycle.PROMISE_READINESS_ROUTE, "c" * 40),
        ("producer_task_binding", OID),
    ),
)
def test_promise_initialization_rejects_a_gate_decision_for_another_transition(
    tmp_path, monkeypatch, route, frozen_root_oid
):
    decision = SimpleNamespace(
        blocked=False,
        diagnostic=None,
        source=SimpleNamespace(route=route, frozen_root_oid=frozen_root_oid),
    )
    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("a stale readiness decision reached root projection")
        ),
    )
    record = {
        "series_branch": "ai-org/patch-series/demo",
        "node_path": ".",
        "contrib_branch": "ai-org/contrib/demo",
        "author": AUTHOR,
    }

    initialization = producer_lifecycle.prepare_initialization(
        tmp_path,
        OID,
        record,
        readiness_decision=decision,
    )

    assert initialization.status == "invalid"
    assert initialization.files == {}
    assert initialization.obligation_coordinates == {}
    assert initialization.decision is decision
    assert initialization.detail == (
        "producer readiness decision does not match the gated series snapshot"
    )


def test_announcement_vets_before_naming_reads_and_preserves_typed_failure(tmp_path, monkeypatch):
    _remote, clone = _remote_clone(tmp_path)
    series_branch = "ai-org/patch-series/closed"
    series = git_wrapper.create_branch_with_files(
        clone,
        series_branch,
        "main",
        {"patch-series-cover-letter.json": {"working_title": "Closed"}},
        commit_message="patch_series: direction-ok",
    )
    assert git_wrapper.push_create(
        clone, "origin", f"refs/heads/{series_branch}", series["commit"]
    )["ok"] is True

    class Decision:
        blocked = True
        diagnostic = SimpleNamespace(detail="exact scope row is missing")

        def as_dict(self):
            return {
                "vet_passed": False,
                "authorable": False,
                "source": {
                    "route": "producer_promise_initialization",
                    "frozen_root_oid": series["commit"],
                },
                "diagnostic": {
                    "route": "producer_promise_initialization",
                    "frozen_root_oid": series["commit"],
                    "context": "series-scope-decomposition-v1",
                    "cue_location": "ownership.0",
                    "rule": "exact-scope-row-missing",
                    "goal_id": "goal:one",
                    "obligation_id": "obligation:one",
                },
                "transition_allowed": False,
            }

    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda _repo, _ref: SimpleNamespace(
            generation=patch_series_bodies.ROOT_GENERATION_INVALID,
            has_generation_members=True,
        ),
    )
    from ai_org.patchwork_queue import patch_series_gate

    monkeypatch.setattr(
        patch_series_gate,
        "vet_producer_pair_closure",
        lambda *_args, **_kwargs: Decision(),
    )
    monkeypatch.setattr(
        announcements,
        "_contrib_branch_for",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("branch naming ran before readiness vet")
        ),
    )
    refs_before = git_wrapper.list_refs(clone, "refs")

    result = announcements.announce(
        clone,
        "origin",
        series_branch,
        author_name=AUTHOR["name"],
        author_email=AUTHOR["email"],
    )

    assert result["ok"] is False
    assert result["status"] == "producer_lifecycle_not_ready"
    assert result["authorability_decision"]["source"] == {
        "route": "producer_promise_initialization",
        "frozen_root_oid": series["commit"],
    }
    assert result["authorability_decision"]["diagnostic"]["rule"] == (
        "exact-scope-row-missing"
    )
    assert git_wrapper.list_refs(clone, "refs") == refs_before


def test_binding_item_trace_and_completion_assertion_follow_git_transition(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    branch = "main"
    promise_body = _promise_body("ai-org/contrib/demo")
    promise_path = producer_lifecycle.evidence_coordinate(
        str(promise_body["series_branch"]),
        str(promise_body["node_path"]),
        str(promise_body["contribution_branch"]),
        AUTHOR,
        str(promise_body["canonical_root_body_sha256"]),
        str(promise_body["obligation_id"]),
    )
    codec = BodyCodecClient()
    promise_prepared = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        promise_body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    promise_commit = git_wrapper.commit_files(
        clone,
        branch,
        {promise_path: promise_prepared.canonical.data.decode("utf-8")},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} demo",
        identity=AUTHOR,
    )["commit"]
    binding_body = {
        "binding_id": "binding:demo",
        "series_branch": "ai-org/patch-series/demo",
        "node_path": ".",
        "contribution_branch": "ai-org/contrib/demo",
        "series_snapshot_oid": OID,
        "canonical_root_body_sha256": SHA256,
        "scope_decomposition_commit_oid": "c" * 40,
        "scope_decomposition_body_sha256": "d" * 64,
        "producer": AUTHOR,
        "tasks": [
            {
                "item_id": "patch_plan:demo#first",
                "production_obligation_ids": ["obligation:one"],
            }
        ],
        "promise_bindings": [
            {
                "obligation_id": "obligation:one",
                "evidence_coordinate": promise_path,
                "promise_commit_oid": promise_commit,
                "promise_body_sha256": promise_prepared.canonical.sha256,
            }
        ],
    }
    binding_prepared = codec.prepare(
        patch_series_bodies.PRODUCER_TASK_BINDING_CONTEXT,
        binding_body,
        expected=patch_series_bodies.PRODUCER_TASK_BINDING_CONTRACT,
    )
    promise_body["commitment_slot"] = {
        "task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
        "task_binding_body_sha256": binding_prepared.canonical.sha256,
    }
    promise_body["previous_promise_commit_oid"] = promise_commit
    committed_promise = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        promise_body,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    binding_commit = git_wrapper.commit_files(
        clone,
        branch,
        {
            producer_lifecycle.TASK_BINDING_PATH: binding_prepared.canonical.data.decode("utf-8"),
            promise_path: committed_promise.canonical.data.decode("utf-8"),
        },
        subject=f"{producer_lifecycle.TASK_BINDING_SUBJECT_PREFIX} root",
        identity=AUTHOR,
    )["commit"]
    item_commit = git_wrapper.commit_files(
        clone,
        branch,
        {"feature.txt": "implemented\n"},
        subject="patch: demo [patch_plan:demo#first]",
        body=(
            f"{producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: "
            f"{binding_prepared.canonical.sha256}\n"
            f"{producer_lifecycle.OBLIGATION_ID_TRAILER}: obligation:one"
        ),
        identity=AUTHOR,
    )["commit"]

    trace = producer_lifecycle.item_commit_trace(
        clone, binding_commit, item_commit, binding_prepared.canonical.sha256
    )
    assertion = producer_lifecycle.prepare_completion_assertion(
        clone, item_commit, asserted_at="2026-07-14T02:00:00Z"
    )

    assert trace == [
        {
            "item_id": "patch_plan:demo#first",
            "commit_oid": item_commit,
            "task_binding_body_sha256": binding_prepared.canonical.sha256,
            "production_obligation_ids": ["obligation:one"],
        }
    ]
    assert assertion.body["task_binding_commit_oid"] == binding_commit
    assert assertion.body["task_binding_body_sha256"] == binding_prepared.canonical.sha256
    assert assertion.body["implementation_oid"] == item_commit
    assert [claim["obligation_id"] for claim in assertion.body["assertions"]] == [
        "obligation:one"
    ]
    item_message = git_wrapper.commit_message(clone, item_commit)
    assert binding_commit not in item_message
    assert binding_prepared.canonical.sha256 in item_message


def test_item_commit_trace_requires_a_real_git_trailer_block(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    binding_commit = git_wrapper.commit_files(
        clone,
        "main",
        {"producer-task-binding.cue": "binding\n"},
        subject="producer-task-binding: materialize root",
        identity=AUTHOR,
    )["commit"]
    digest = "b" * 64
    prose_commit = git_wrapper.commit_files(
        clone,
        "main",
        {"feature.txt": "prose only\n"},
        subject="patch: prose is not trace metadata [item:prose]",
        body=(
            f"The text {producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: "
            f"{digest} is explanatory prose, not a trailer."
        ),
        identity=AUTHOR,
    )["commit"]

    assert producer_lifecycle.item_commit_trace(
        clone, binding_commit, prose_commit, digest
    ) == []

    trailer_commit = git_wrapper.commit_files(
        clone,
        "main",
        {"feature.txt": "trailer bound\n"},
        subject="patch: real trace metadata [item:trailer]",
        body=(
            "The implementation is complete.\n\n"
            f"{producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: {digest}\n"
            f"{producer_lifecycle.OBLIGATION_ID_TRAILER}: obligation:one"
        ),
        identity=AUTHOR,
    )["commit"]

    assert producer_lifecycle.item_commit_trace(
        clone, binding_commit, trailer_commit, digest
    ) == [
        {
            "item_id": "item:trailer",
            "commit_oid": trailer_commit,
            "task_binding_body_sha256": digest,
            "production_obligation_ids": ["obligation:one"],
        }
    ]


def test_item_commit_trace_rejects_binding_digest_without_required_claim(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    binding_commit = git_wrapper.commit_files(
        clone,
        "main",
        {producer_lifecycle.TASK_BINDING_PATH: "binding\n"},
        subject="producer-task-binding: materialize root",
        identity=AUTHOR,
    )["commit"]
    digest = "b" * 64
    digest_only_commit = git_wrapper.commit_files(
        clone,
        "main",
        {"feature.txt": "unclaimed implementation\n"},
        subject="patch: unclaimed [item:unclaimed]",
        body=f"{producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: {digest}",
        identity=AUTHOR,
    )["commit"]

    with pytest.raises(
        ValueError,
        match="item binding obligation ids are missing",
    ):
        producer_lifecycle.item_commit_trace(
            clone,
            binding_commit,
            digest_only_commit,
            digest,
        )


def test_item_commit_trace_rejects_trailers_outside_the_binding_contract(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    binding_commit = git_wrapper.commit_files(
        clone,
        "main",
        {producer_lifecycle.TASK_BINDING_PATH: "binding\n"},
        subject="producer-task-binding: materialize root",
        identity=AUTHOR,
    )["commit"]
    digest = "b" * 64
    commit = git_wrapper.commit_files(
        clone,
        "main",
        {"feature.txt": "implemented with extra metadata\n"},
        subject="patch: implementation [item:extra-trailer]",
        body=(
            f"{producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: {digest}\n"
            f"{producer_lifecycle.OBLIGATION_ID_TRAILER}: obligation:one\n"
            "Reviewed-by: unrelated-reviewer"
        ),
        identity=AUTHOR,
    )["commit"]

    with pytest.raises(
        ValueError,
        match="stores unsupported trailers.*Reviewed-by",
    ):
        producer_lifecycle.item_commit_trace(
            clone,
            binding_commit,
            commit,
            digest,
        )


def test_task_binding_expands_plural_assignment_into_exact_promise_edges(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    codec = BodyCodecClient()
    files = {}
    for suffix in ("one", "two"):
        promise = _promise_body("ai-org/contrib/demo")
        promise["obligation_id"] = f"obligation:{suffix}"
        promise["referee_goal_id"] = f"goal:{suffix}"
        path = producer_lifecycle.evidence_coordinate(
            "ai-org/patch-series/demo",
            ".",
            "ai-org/contrib/demo",
            AUTHOR,
            str(promise["canonical_root_body_sha256"]),
            str(promise["obligation_id"]),
        )
        prepared = codec.prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            promise,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        files[path] = prepared.canonical.data.decode()
    git_wrapper.commit_files(
        clone,
        "main",
        files,
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} demo",
        identity=AUTHOR,
    )
    decomposition = SimpleNamespace(
        body={
            "ownership": [
                {"scope_item_id": "obligation:one", "owner_node_path": "."},
                {"scope_item_id": "obligation:two", "owner_node_path": "."},
            ]
        },
        canonical_root_sha256=SHA256,
        frozen_oid=OID,
        body_sha256="d" * 64,
    )
    root = {
        "problem": {
            "patch_plan": [
                {
                    "item_id": "item:plural",
                    "production_obligation_ids": ["obligation:one", "obligation:two"],
                }
            ]
        }
    }
    record = {
        "series_branch": "ai-org/patch-series/demo",
        "node_path": ".",
        "contrib_branch": "ai-org/contrib/demo",
        "author": AUTHOR,
    }

    prepared = producer_lifecycle._prepare_task_binding_files(
        clone, "main", record, OID, decomposition, root, decision=None
    )

    assert prepared.active is True
    assert prepared.body["tasks"] == [
        {"item_id": "item:plural", "production_obligation_ids": ["obligation:one"]},
        {"item_id": "item:plural", "production_obligation_ids": ["obligation:two"]},
    ]
    assert prepared.obligation_ids_by_item == {
        "item:plural": ("obligation:one", "obligation:two")
    }
    assert producer_lifecycle.required_claim_set(prepared.body) == (
        "obligation:one",
        "obligation:two",
    )
    assert set(prepared.files) == {
        producer_lifecycle.TASK_BINDING_PATH,
        *files,
    }


@pytest.mark.parametrize(
    ("promise_obligation_ids", "detail"),
    (
        (("obligation:one", "obligation:one"), "not unique"),
        (("obligation:one", "obligation:extra"), "required claim set"),
        (("obligation:one",), "required claim set"),
    ),
)
def test_promise_binding_cohort_must_exactly_match_required_claim_set(
    promise_obligation_ids, detail
):
    binding = {
        "tasks": [
            {"item_id": "item:one", "production_obligation_ids": ["obligation:one"]},
            {"item_id": "item:two", "production_obligation_ids": ["obligation:two"]},
        ],
        "promise_bindings": [
            {"obligation_id": obligation_id}
            for obligation_id in promise_obligation_ids
        ],
    }

    with pytest.raises(ValueError, match=detail):
        producer_lifecycle.validate_promise_bindings(binding)


def test_task_binding_reuses_exact_common_gate_decision(tmp_path, monkeypatch):
    decision = SimpleNamespace(
        blocked=True,
        diagnostic=SimpleNamespace(detail="common readiness gate is closed"),
        source=SimpleNamespace(
            route=producer_lifecycle.CODE_AUTHORING_ROUTE,
            frozen_root_oid=OID,
        ),
    )
    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda *_args: SimpleNamespace(
            generation=patch_series_bodies.ROOT_GENERATION_V2
        ),
    )

    # Importing/evaluating the gate again would turn one authoring transition
    # into two potentially divergent decisions. The supplied frozen decision
    # is the sole input to binding materialization.
    from ai_org.patchwork_queue import patch_series_gate

    monkeypatch.setattr(
        patch_series_gate,
        "vet_producer_pair_closure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("task binding independently re-vetted the common gate")
        ),
    )

    prepared = producer_lifecycle.prepare_task_binding(
        tmp_path,
        OID,
        "ai-org/contrib/demo",
        {"series_branch": "ai-org/patch-series/demo"},
        readiness_decision=decision,
        expected_readiness_route=producer_lifecycle.CODE_AUTHORING_ROUTE,
    )

    assert prepared.status == "blocked"
    assert prepared.decision is decision
    assert prepared.detail == "common readiness gate is closed"


def test_task_binding_rejects_gate_decision_for_another_snapshot(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda *_args: SimpleNamespace(
            generation=patch_series_bodies.ROOT_GENERATION_V2
        ),
    )
    decision = SimpleNamespace(
        blocked=False,
        source=SimpleNamespace(frozen_root_oid="c" * 40),
    )

    prepared = producer_lifecycle.prepare_task_binding(
        tmp_path,
        OID,
        "ai-org/contrib/demo",
        {"series_branch": "ai-org/patch-series/demo"},
        readiness_decision=decision,
    )

    assert prepared.status == "invalid"
    assert prepared.decision is decision
    assert prepared.detail == (
        "producer readiness decision does not match the gated series snapshot"
    )


def test_task_binding_rejects_gate_decision_for_another_authoring_route(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda *_args: SimpleNamespace(
            generation=patch_series_bodies.ROOT_GENERATION_V2
        ),
    )
    decision = SimpleNamespace(
        blocked=False,
        source=SimpleNamespace(
            route=producer_lifecycle.FEEDBACK_AUTHORING_ROUTE,
            frozen_root_oid=OID,
        ),
    )

    prepared = producer_lifecycle.prepare_task_binding(
        tmp_path,
        OID,
        "ai-org/contrib/demo",
        {"series_branch": "ai-org/patch-series/demo"},
        readiness_decision=decision,
        expected_readiness_route=producer_lifecycle.CODE_AUTHORING_ROUTE,
    )

    assert prepared.status == "invalid"
    assert prepared.decision is decision
    assert prepared.detail == (
        "producer readiness decision does not match the authoring route"
    )


def test_initial_task_binding_rejects_a_gate_owned_digest_mismatch(
    tmp_path, monkeypatch
):
    decision = {
        "vet_passed": True,
        "authorable": True,
        "transition_allowed": False,
        "source": {
            "route": producer_lifecycle.CODE_AUTHORING_ROUTE,
            "branch": "ai-org/patch-series/demo",
            "frozen_root_oid": OID,
            "root_sha256": "e" * 64,
            "scope_decomposition_sha256": "d" * 64,
        },
    }
    snapshot = SimpleNamespace(
        generation=patch_series_bodies.ROOT_GENERATION_V2,
        raw=lambda _path: b"canonical-root",
    )
    monkeypatch.setattr(
        patch_series_bodies, "classify_root_generation", lambda *_args: snapshot
    )
    monkeypatch.setattr(
        patch_series_bodies,
        "project_series_scope_decomposition",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(
        producer_lifecycle.BodyCodecClient,
        "parse",
        lambda *_args, **_kwargs: {"problem": {}},
    )
    monkeypatch.setattr(
        producer_lifecycle,
        "_prepare_task_binding_files",
        lambda *_args, **_kwargs: producer_lifecycle.PreparedTaskBinding(
            "ready",
            {},
            {
                "series_branch": "ai-org/patch-series/demo",
                "node_path": ".",
                "contribution_branch": "ai-org/contrib/demo",
                "series_snapshot_oid": OID,
                "producer": AUTHOR,
                "canonical_root_body_sha256": "f" * 64,
                "scope_decomposition_commit_oid": OID,
                "scope_decomposition_body_sha256": "d" * 64,
            },
        ),
    )

    prepared = producer_lifecycle.prepare_task_binding(
        tmp_path,
        OID,
        "ai-org/contrib/demo",
        {
            "series_branch": "ai-org/patch-series/demo",
            "node_path": ".",
            "contrib_branch": "ai-org/contrib/demo",
            "author": AUTHOR,
        },
        readiness_decision=decision,
        expected_readiness_route=producer_lifecycle.CODE_AUTHORING_ROUTE,
    )

    assert prepared.status == "invalid"
    assert prepared.detail == (
        "producer task binding does not match the gated "
        "canonical_root_body_sha256"
    )


def test_feedback_regenerates_completion_assertion_for_new_tip(tmp_path):
    _remote, clone = _remote_clone(tmp_path)
    codec = BodyCodecClient()
    promise = _promise_body("ai-org/contrib/demo")
    promise_path = producer_lifecycle.evidence_coordinate(
        str(promise["series_branch"]), ".", "ai-org/contrib/demo", AUTHOR,
        str(promise["canonical_root_body_sha256"]), "obligation:one"
    )
    initial_promise = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        promise,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    promise_commit = git_wrapper.commit_files(
        clone, "main", {promise_path: initial_promise.canonical.data.decode()},
        subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} demo", identity=AUTHOR,
    )["commit"]
    binding = {
        "binding_id": "binding:feedback",
        "series_branch": "ai-org/patch-series/demo", "node_path": ".",
        "contribution_branch": "ai-org/contrib/demo", "series_snapshot_oid": OID,
        "canonical_root_body_sha256": SHA256, "scope_decomposition_commit_oid": "c" * 40,
        "scope_decomposition_body_sha256": "d" * 64, "producer": AUTHOR,
        "tasks": [{"item_id": "item:one", "production_obligation_ids": ["obligation:one"]}],
        "promise_bindings": [{"obligation_id": "obligation:one", "evidence_coordinate": promise_path,
                              "promise_commit_oid": promise_commit,
                              "promise_body_sha256": initial_promise.canonical.sha256}],
    }
    prepared_binding = codec.prepare(
        patch_series_bodies.PRODUCER_TASK_BINDING_CONTEXT, binding,
        expected=patch_series_bodies.PRODUCER_TASK_BINDING_CONTRACT,
    )
    promise["commitment_slot"] = {"task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
                                  "task_binding_body_sha256": prepared_binding.canonical.sha256}
    promise["previous_promise_commit_oid"] = promise_commit
    bound_promise = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT, promise,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    binding_commit = git_wrapper.commit_files(
        clone, "main", {producer_lifecycle.TASK_BINDING_PATH: prepared_binding.canonical.data.decode(),
                         promise_path: bound_promise.canonical.data.decode()},
        subject="producer-task-binding: materialize root", identity=AUTHOR,
    )["commit"]
    item_commit = git_wrapper.commit_files(
        clone, "main", {"feature.txt": "one\n"}, subject="patch: one [item:one]",
        body=(f"{producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: {prepared_binding.canonical.sha256}\n"
              f"{producer_lifecycle.OBLIGATION_ID_TRAILER}: obligation:one"), identity=AUTHOR,
    )["commit"]
    first = producer_lifecycle.prepare_completion_assertion(clone, item_commit)
    assertion_commit = git_wrapper.commit_files(
        clone, "main", first.files, subject="producer-completion: assert first", identity=AUTHOR,
    )["commit"]
    feedback_tip = git_wrapper.commit_files(
        clone,
        "main",
        {"feature.txt": "two\n"},
        subject="patch: address blockers [item:acceptance-feedback-2]",
        body=(
            f"{producer_lifecycle.TASK_BINDING_DIGEST_TRAILER}: "
            f"{prepared_binding.canonical.sha256}\n"
            f"{producer_lifecycle.OBLIGATION_ID_TRAILER}: obligation:one"
        ),
        identity=AUTHOR,
    )["commit"]

    regenerated = producer_lifecycle.prepare_completion_assertion(clone, feedback_tip)
    trace = producer_lifecycle.item_commit_trace(
        clone, binding_commit, feedback_tip, prepared_binding.canonical.sha256
    )

    assert regenerated.body["implementation_oid"] == feedback_tip
    assert regenerated.body["implementation_oid"] != first.body["implementation_oid"]
    assert regenerated.body["task_binding_commit_oid"] == binding_commit
    assert regenerated.body_sha256 != first.body_sha256
    assert assertion_commit != feedback_tip
    assert trace[-1]["item_id"] == "item:acceptance-feedback-2"
    assert trace[-1]["commit_oid"] == feedback_tip
    assert binding_commit not in git_wrapper.commit_message(clone, feedback_tip)


def _promise_body(branch: str) -> dict[str, object]:
    return {
        "series_snapshot_oid": OID,
        "series_branch": "ai-org/patch-series/demo",
        "node_path": ".",
        "contribution_branch": branch,
        "canonical_root_body_sha256": SHA256,
        "referee_goal_id": "goal:one",
        "obligation_id": "obligation:one",
        "deliverable": "feature.txt",
        "eligibility_predicate": "can produce feature.txt",
        "producer": AUTHOR,
        "eligibility_decision": "eligible",
        "eligibility_basis": "exact scope ownership at .",
        "commitment_slot": {},
        "completion_claim_slot": {},
        "previous_promise_commit_oid": None,
        "authored_at": "2026-07-14T00:00:00Z",
    }


def _remote_clone(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "-m", "base")
    _git(seed, "branch", "-M", "main")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    clone = tmp_path / "clone"
    assert git_wrapper.clone_repository(remote, clone)["ok"] is True
    return remote, clone


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
