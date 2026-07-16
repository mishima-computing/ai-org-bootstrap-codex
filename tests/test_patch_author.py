from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_org import git_wrapper, network_bodies, patch_series_bodies
from ai_org.patch_author import announcements, discovery, functional_check, submission, work
from ai_org.patchwork_queue import patch_series_gate


AUTHOR_A = {"name": "Author A", "email": "101+author-a@users.noreply.github.com"}
AUTHOR_B = {"name": "Author B", "email": "102+author-b@users.noreply.github.com"}
SERIES_BRANCH = "ai-org/patch-series/demo-series"


def test_two_authors_announce_one_series_both_succeed_and_series_stays_open(tmp_path):
    """The announcement-competition law: no lock anywhere.

    家族間重複=競争 — parent A and parent B taking the same task is intended
    competition. Both announcements succeed, both are visible, and discovery
    still lists the series as OPEN (task status only).
    """
    canonical = _canonical_with_series(tmp_path)
    clone_a = _clone(canonical, tmp_path / "clone-a")
    clone_b = _clone(canonical, tmp_path / "clone-b")

    first = announcements.announce(
        clone_a, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert first["ok"] is True and first["status"] == "authoring_announced"
    assert first["ref"] == announcements.canonical_announcement_ref(SERIES_BRANCH, ".", AUTHOR_A["email"])
    assert first["record"]["contrib_branch"] == "ai-org/contrib/demo-series"

    second = announcements.announce(
        clone_b, "origin", SERIES_BRANCH, author_name=AUTHOR_B["name"], author_email=AUTHOR_B["email"]
    )
    assert second["ok"] is True and second["status"] == "authoring_announced"
    assert second["ref"] == announcements.canonical_announcement_ref(SERIES_BRANCH, ".", AUTHOR_B["email"])
    # Visibility-informed naming: B sees A's announced branch and picks a
    # sibling name — output deconfliction, not admission control.
    assert second["record"]["contrib_branch"] == "ai-org/contrib/demo-series-r2"

    # BOTH refs are on the canonical remote (pure visibility, no winner).
    remote_refs = git_wrapper.ls_remote(clone_a, "origin", "refs/ai-org/authoring-announcements/*")["refs"]
    assert set(remote_refs) == {first["ref"], second["ref"]}
    series_head = git_wrapper.head_sha(clone_a, f"refs/remotes/origin/{SERIES_BRANCH}")
    assert git_wrapper.parent_commits(clone_a, first["commit"]) == [series_head]

    # Discovery still lists the series as OPEN: taking does not change "open".
    listing = discovery.list_open(clone_a)
    assert listing["ok"] is True
    row = {row["branch"]: row for row in listing["rows"]}[SERIES_BRANCH]
    assert row["open"] is True and row["reasons"] == []
    assert sorted(entry["author"]["email"] for entry in row["announcements"]) == [
        AUTHOR_A["email"],
        AUTHOR_B["email"],
    ]


def test_naming_race_resolves_at_submission_fail_closed_without_arbitration(tmp_path, monkeypatch):
    """Two families blind to each other pick the same branch name.

    Both announcements still succeed (own refs). The name race resolves at the
    create-only push: the second family gets a typed branch_name_taken and the
    first family's published branch is never touched. No arbitration code runs.
    """
    canonical = _canonical_with_series(tmp_path)
    clone_a = _clone(canonical, tmp_path / "clone-a")
    clone_b = _clone(canonical, tmp_path / "clone-b")

    first = announcements.announce(
        clone_a, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert first["ok"] is True

    # Blind B to A's announcement so B picks the same base branch name.
    real_ls_remote = git_wrapper.ls_remote

    def blind_ls_remote(repo, remote, *patterns):
        result = real_ls_remote(repo, remote, *patterns)
        if result.get("ok"):
            hidden = announcements.canonical_announcement_ref(SERIES_BRANCH, ".", AUTHOR_A["email"])
            result["refs"] = {
                ref: sha for ref, sha in result["refs"].items() if ref != hidden
            }
        return result

    monkeypatch.setattr(git_wrapper, "ls_remote", blind_ls_remote)
    second = announcements.announce(
        clone_b, "origin", SERIES_BRANCH, author_name=AUTHOR_B["name"], author_email=AUTHOR_B["email"]
    )
    monkeypatch.undo()
    assert second["ok"] is True and second["status"] == "authoring_announced"
    assert second["record"]["contrib_branch"] == "ai-org/contrib/demo-series"

    _author_contrib_branch(clone_a, "ai-org/contrib/demo-series", AUTHOR_A)
    assert submission.submit(clone_a, first["ref"])["ok"] is True
    published = git_wrapper.ls_remote(clone_a, "origin", "refs/heads/ai-org/contrib/demo-series")["refs"][
        "refs/heads/ai-org/contrib/demo-series"
    ]

    _author_contrib_branch(clone_b, "ai-org/contrib/demo-series", AUTHOR_B)
    lost = submission.submit(clone_b, second["ref"])
    assert lost["ok"] is False and lost["status"] == "branch_name_taken"
    # A's published branch is untouched: never contested, never rewritten.
    still = git_wrapper.ls_remote(clone_b, "origin", "refs/heads/ai-org/contrib/demo-series")["refs"][
        "refs/heads/ai-org/contrib/demo-series"
    ]
    assert still == published


def test_withdraw_authoring_intent_removes_the_announcement_ref(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    wrong_author = announcements.withdraw(clone, "origin", announced["ref"], author_email=AUTHOR_B["email"])
    assert wrong_author == {
        "ok": False,
        "status": "author_mismatch",
        "ref": announced["ref"],
        "holder": AUTHOR_A,
    }

    withdrawn = announcements.withdraw(clone, "origin", announced["ref"], author_email=AUTHOR_A["email"])
    assert withdrawn["ok"] is True and withdrawn["status"] == "authoring_intent_withdrawn"
    assert withdrawn["publication"].expected_ref_oids == ((announced["ref"], announced["commit"]),)
    # Withdrawal is REMOVAL: the ref is gone on the remote and locally.
    assert git_wrapper.ls_remote(clone, "origin", announced["ref"])["refs"] == {}
    assert git_wrapper.head_sha(clone, announced["ref"]) is None
    assert announcements.withdraw(clone, "origin", announced["ref"], author_email=AUTHOR_A["email"])["status"] == "announcement_missing"


def test_announcement_coordinate_state_covers_publication_transitions():
    canonical = announcements.canonical_announcement_ref(
        SERIES_BRANCH, ".", AUTHOR_A["email"]
    )
    legacy = announcements.announcement_ref(
        "demo-series", "", announcements.author_slug(AUTHOR_A["email"])
    )
    states = {
        "absent": {},
        "legacy_only": {legacy: "1" * 40},
        "canonical_only": {canonical: "2" * 40},
        "coexisting": {legacy: "1" * 40, canonical: "2" * 40},
    }

    observed = {
        name: announcements.announcement_coordinate_state(
            refs, canonical_ref=canonical, legacy_ref=legacy
        )
        for name, refs in states.items()
    }

    assert {name: value.state for name, value in observed.items()} == {
        name: name for name in states
    }
    assert observed["legacy_only"].releases_body is True
    assert observed["legacy_only"].sole_ref == legacy
    assert observed["canonical_only"].releases_body is True
    assert observed["canonical_only"].sole_ref == canonical
    assert observed["absent"].releases_body is False
    assert observed["coexisting"].releases_body is False
    assert observed["coexisting"].sole_ref == ""


def test_legacy_only_announcement_transfers_to_canonical_ref_atomically(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    series_head = git_wrapper.head_sha(clone, f"origin/{SERIES_BRANCH}")
    legacy_ref = announcements.announcement_ref("demo-series", "", announcements.author_slug(AUTHOR_A["email"]))
    legacy_record = {
        "schema": announcements.LEGACY_ANNOUNCEMENT_SCHEMA,
        "series_branch": SERIES_BRANCH,
        "node_path": ".",
        "series_head_at_announcement": series_head,
        "contrib_branch": "ai-org/contrib/demo-series",
        "author": AUTHOR_A,
        "status": "authoring",
    }
    legacy = git_wrapper.create_ref_with_files(
        clone, legacy_ref, {announcements.LEGACY_ANNOUNCEMENT_RECORD_PATH: legacy_record},
        subject="authoring-intent: legacy", parent=series_head, identity=AUTHOR_A,
    )
    assert git_wrapper.push_create(clone, "origin", legacy_ref, legacy["commit"])["ok"] is True

    migrated = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert migrated["ok"] is True and migrated["status"] == "authoring_announcement_migrated"
    assert migrated["publication"].status == "migrated" and migrated["publication"].atomic is True
    assert migrated["publication"].expected_ref_oids == (
        (migrated["ref"], ""),
        (legacy_ref, legacy["commit"]),
    )
    remote = git_wrapper.ls_remote(clone, "origin", legacy_ref, migrated["ref"])["refs"]
    assert legacy_ref not in remote and remote[migrated["ref"]] == migrated["commit"]
    assert git_wrapper.tree_files(clone, migrated["commit"]) == [announcements.ANNOUNCEMENT_RECORD_PATH]


def test_legacy_transfer_requires_advertised_atomic_receive(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    subprocess.run(
        ["git", "-C", str(canonical), "config", "receive.advertiseAtomic", "false"],
        check=True,
    )
    clone = _clone(canonical, tmp_path / "clone-a")
    series_head = git_wrapper.head_sha(clone, f"origin/{SERIES_BRANCH}")
    legacy_ref = announcements.announcement_ref(
        "demo-series", "", announcements.author_slug(AUTHOR_A["email"])
    )
    legacy = git_wrapper.create_ref_with_files(
        clone,
        legacy_ref,
        {
            announcements.LEGACY_ANNOUNCEMENT_RECORD_PATH: {
                "schema": announcements.LEGACY_ANNOUNCEMENT_SCHEMA,
                "series_branch": SERIES_BRANCH,
                "node_path": ".",
                "series_head_at_announcement": series_head,
                "contrib_branch": "ai-org/contrib/demo-series",
                "author": AUTHOR_A,
                "status": "authoring",
            }
        },
        subject="authoring-intent: legacy",
        parent=series_head,
        identity=AUTHOR_A,
    )
    assert git_wrapper.push_create(clone, "origin", legacy_ref, legacy["commit"])["ok"] is True

    rejected = announcements.announce(
        clone,
        "origin",
        SERIES_BRANCH,
        author_name=AUTHOR_A["name"],
        author_email=AUTHOR_A["email"],
    )

    canonical_ref = announcements.canonical_announcement_ref(
        SERIES_BRANCH, ".", AUTHOR_A["email"]
    )
    assert rejected["status"] == "atomic_transfer_rejected"
    assert rejected["publication"].failure.rule == "atomic-receive-not-advertised"
    assert rejected["publication"].expected_ref_oids == (
        (canonical_ref, ""),
        (legacy_ref, legacy["commit"]),
    )
    assert rejected["record"] is None
    assert git_wrapper.ls_remote(clone, "origin", legacy_ref, canonical_ref)["refs"] == {
        legacy_ref: legacy["commit"]
    }
    assert git_wrapper.head_sha(clone, canonical_ref) is None


def test_failed_announcement_publication_releases_no_local_body(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    ref = announcements.canonical_announcement_ref(SERIES_BRANCH, ".", AUTHOR_A["email"])

    monkeypatch.setattr(
        git_wrapper,
        "push_create",
        lambda *_args, **_kwargs: {
            "ok": False, "status": "rejected", "detail": "injected publication failure"
        },
    )
    rejected = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )

    assert rejected["ok"] is False and rejected["status"] == "same_author_conflict"
    assert rejected["publication"].status == "rejected"
    assert git_wrapper.head_sha(clone, ref) is None
    assert git_wrapper.ls_remote(clone, "origin", ref)["refs"] == {}


def test_canonical_announcement_update_uses_observed_tip_as_cas(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    first = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    observed: dict[str, str] = {}
    real_push_cas = git_wrapper.push_cas

    def capture_cas(repo, remote, ref, commit, expected):
        observed.update({"ref": ref, "commit": commit, "expected": expected})
        return real_push_cas(repo, remote, ref, commit, expected)

    monkeypatch.setattr(git_wrapper, "push_cas", capture_cas)
    updated = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )

    assert updated["ok"] is True
    assert updated["publication"].status == "updated"
    assert updated["publication"].expected_oid == first["commit"]
    assert updated["publication"].expected_ref_oids == ((first["ref"], first["commit"]),)
    assert observed == {
        "ref": first["ref"], "commit": updated["commit"], "expected": first["commit"]
    }
    assert git_wrapper.head_sha(clone, first["ref"]) == updated["commit"]


def test_canonical_announcement_cas_loss_preserves_observed_remote(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    first = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    monkeypatch.setattr(
        git_wrapper,
        "push_cas",
        lambda *_args, **_kwargs: {
            "ok": False, "status": "rejected", "detail": "injected CAS loss"
        },
    )

    lost = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )

    assert lost["ok"] is False and lost["status"] == "same_author_conflict"
    assert lost["publication"].expected_ref_oids == ((first["ref"], first["commit"]),)
    assert git_wrapper.ls_remote(clone, "origin", first["ref"])["refs"] == {
        first["ref"]: first["commit"]
    }
    assert git_wrapper.head_sha(clone, first["ref"]) == first["commit"]


def test_remote_create_with_local_refresh_failure_reports_published_coordinate(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    ref = announcements.canonical_announcement_ref(SERIES_BRANCH, ".", AUTHOR_A["email"])
    monkeypatch.setattr(
        git_wrapper,
        "update_ref",
        lambda *_args, **_kwargs: {
            "ok": False, "error_type": "update_ref_failed", "detail": "injected refresh failure"
        },
    )

    result = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )

    assert result["ok"] is False and result["status"] == "local_refresh_failed"
    assert result["record"] is None and result["publication"].ok is True
    assert git_wrapper.ls_remote(clone, "origin", ref)["refs"] == {
        ref: result["publication"].commit_oid
    }
    assert git_wrapper.head_sha(clone, ref) is None


def test_announcement_remote_outage_stops_before_publication(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    ref = announcements.canonical_announcement_ref(SERIES_BRANCH, ".", AUTHOR_A["email"])
    monkeypatch.setattr(
        git_wrapper,
        "ls_remote",
        lambda *_args, **_kwargs: {"ok": False, "status": "error", "detail": "offline"},
    )

    result = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )

    assert result["ok"] is False and result["status"] == "remote_unavailable"
    assert git_wrapper.head_sha(clone, ref) is None


def test_full_identity_keeps_legacy_slug_collisions_on_distinct_canonical_refs():
    first = "a.b@users.noreply.github.com"
    second = "a-b@users.noreply.github.com"
    assert announcements.author_slug(first) == announcements.author_slug(second) == "a-b"
    assert announcements.canonical_announcement_ref(SERIES_BRANCH, ".", first) != (
        announcements.canonical_announcement_ref(SERIES_BRANCH, ".", second)
    )


def test_legacy_slug_collision_cannot_delete_another_full_identity(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    first = {"name": "Dotted Author", "email": "a.b@users.noreply.github.com"}
    second = {"name": "Dashed Author", "email": "a-b@users.noreply.github.com"}
    series_head = git_wrapper.head_sha(clone, f"origin/{SERIES_BRANCH}")
    legacy_ref = announcements.announcement_ref("demo-series", "", "a-b")
    legacy = git_wrapper.create_ref_with_files(
        clone,
        legacy_ref,
        {
            announcements.LEGACY_ANNOUNCEMENT_RECORD_PATH: {
                "schema": announcements.LEGACY_ANNOUNCEMENT_SCHEMA,
                "series_branch": SERIES_BRANCH,
                "node_path": ".",
                "series_head_at_announcement": series_head,
                "contrib_branch": "ai-org/contrib/demo-series",
                "author": first,
                "status": "authoring",
            }
        },
        subject="authoring-intent: colliding legacy coordinate",
        parent=series_head,
        identity=first,
    )
    assert git_wrapper.push_create(clone, "origin", legacy_ref, legacy["commit"])["ok"] is True

    rejected = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=second["name"], author_email=second["email"],
    )

    second_ref = announcements.canonical_announcement_ref(SERIES_BRANCH, ".", second["email"])
    assert rejected["status"] == "announcement_identity_collision"
    assert rejected["holder"] == first and rejected["record"] is None
    assert git_wrapper.ls_remote(clone, "origin", legacy_ref, second_ref)["refs"] == {
        legacy_ref: legacy["commit"]
    }
    assert git_wrapper.head_sha(clone, second_ref) is None


def test_registered_child_manifest_projection_routes_announcement_work(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    reads: list[tuple[str, str]] = []

    def registered_read(repo, frozen, node_path):
        reads.append((frozen, node_path))
        if node_path == "sub/codec":
            return network_bodies.ManifestInput(
                "registered", frozen, node_path, {
                "node_path": "sub/codec",
                "contrib_branch": "ai-org/contrib/registered-codec",
                },
            )
        raise AssertionError(node_path)

    monkeypatch.setattr(network_bodies, "resolve_manifest_input", registered_read)
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, node_path="sub/codec",
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )

    assert announced["ok"] is True
    assert announced["record"]["contrib_branch"] == "ai-org/contrib/registered-codec"
    assert announcements.evaluate(clone, announced["ref"])["state"] == "authoring"
    assert reads == [
        (announced["record"]["series_head_at_announcement"], "sub/codec"),
        (announced["record"]["series_head_at_announcement"], "sub/codec"),
    ]


def test_invalid_registered_manifest_stops_before_announcement_publication(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    pushed = False

    def invalid_manifest(_repo, frozen, node_path):
        return network_bodies.ManifestInput(
            "invalid", frozen, node_path,
            detail="registered manifest rejected by codec",
        )

    def unexpected_push(*_args, **_kwargs):
        nonlocal pushed
        pushed = True
        raise AssertionError("publication must not be attempted")

    monkeypatch.setattr(network_bodies, "resolve_manifest_input", invalid_manifest)
    monkeypatch.setattr(git_wrapper, "push_create", unexpected_push)
    rejected = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )

    assert rejected["ok"] is False
    assert rejected["status"] == "announcement_manifest_invalid"
    assert "registered manifest rejected by codec" in rejected["detail"]
    assert pushed is False


def test_discovery_consumes_child_through_the_registered_root_child_transition(monkeypatch):
    selected = network_bodies.ManifestInput(
        "registered", "frozen-series-head", "sub/codec",
        {"node_path": "sub/codec", "lifecycle_status": "ready_for_patch_authoring"},
    )
    calls = []

    def resolve(repo, ref, node_path):
        calls.append((repo, ref, node_path))
        return selected

    monkeypatch.setattr(network_bodies, "resolve_manifest_input", resolve)
    monkeypatch.setattr(
        network_bodies, "read_network_body",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no independent child read")),
    )

    body = discovery._read_committed_json(
        "repo", "frozen-series-head", "sub/codec/patch-series-manifest.cue"
    )

    assert body["node_path"] == "sub/codec"
    assert calls == [("repo", "frozen-series-head", "sub/codec")]


def test_withdraw_rejects_a_body_at_an_unbound_coordinate(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    unrelated_ref = "refs/ai-org/authoring-announcements/unrelated"
    git_wrapper.update_ref(clone, unrelated_ref, announced["commit"])
    assert git_wrapper.push_create(clone, "origin", unrelated_ref, announced["commit"])["ok"] is True

    rejected = announcements.withdraw(
        clone, "origin", unrelated_ref, author_email=AUTHOR_A["email"]
    )
    assert rejected["status"] == "announcement_ref_binding_invalid"
    assert git_wrapper.ls_remote(clone, "origin", unrelated_ref)["refs"][unrelated_ref] == announced["commit"]


def test_withdraw_validates_commit_author_before_remote_delete(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    body = git_wrapper.show_file(clone, announced["commit"], announcements.ANNOUNCEMENT_RECORD_PATH)
    forged = git_wrapper.create_ref_with_files(
        clone,
        announced["ref"],
        {announcements.ANNOUNCEMENT_RECORD_PATH: body},
        subject="authoring-intent: forged author",
        parent=announced["record"]["parent"],
        identity=AUTHOR_B,
    )
    assert git_wrapper.push_cas(
        clone, "origin", announced["ref"], forged["commit"], announced["commit"]
    )["ok"] is True

    rejected = announcements.withdraw(
        clone, "origin", announced["ref"], author_email=AUTHOR_A["email"]
    )

    assert rejected["status"] == "announcement_record_invalid"
    assert rejected["errors"] == [{"type": "announcement_commit_binding_invalid"}]
    assert git_wrapper.ls_remote(clone, "origin", announced["ref"])["refs"] == {
        announced["ref"]: forged["commit"]
    }


def test_evaluate_rejects_contribution_branch_outside_bound_route(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    series_head = git_wrapper.head_sha(clone, f"origin/{SERIES_BRANCH}")
    record = announcements.build_record(
        series_branch=SERIES_BRANCH,
        node_path=".",
        series_head_at_announcement=series_head,
        contrib_branch="ai-org/contrib/not-the-series-route",
        author_name=AUTHOR_A["name"],
        author_email=AUTHOR_A["email"],
    )
    canonical_body = announcements.BodyCodecClient().prepare(
        announcements.ROOT_CONTEXT, record, expected=announcements.ROOT_CONTRACT
    ).canonical.data.decode("utf-8")
    written = git_wrapper.create_ref_with_files(
        clone,
        record["canonical_ref"],
        {announcements.ANNOUNCEMENT_RECORD_PATH: canonical_body},
        subject="authoring-intent: wrong route",
        parent=series_head,
        identity=AUTHOR_A,
    )

    evaluation = announcements.evaluate(clone, record["canonical_ref"])

    assert evaluation["tip"] == written["commit"] and evaluation["state"] == "invalid"
    assert evaluation["errors"] == [{"type": "announcement_contrib_branch_binding_invalid"}]


def test_coexisting_announcement_coordinates_release_no_body_and_do_not_block(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True
    legacy_ref = announcements.announcement_ref("demo-series", "", announcements.author_slug(AUTHOR_A["email"]))
    legacy_record = {
        "schema": announcements.LEGACY_ANNOUNCEMENT_SCHEMA,
        "series_branch": SERIES_BRANCH,
        "node_path": ".",
        "series_head_at_announcement": announced["record"]["series_head_at_announcement"],
        "contrib_branch": announced["record"]["contrib_branch"],
        "author": AUTHOR_A,
        "status": "authoring",
    }
    legacy = git_wrapper.create_ref_with_files(
        clone, legacy_ref, {announcements.LEGACY_ANNOUNCEMENT_RECORD_PATH: legacy_record},
        subject="authoring-intent: coexistence", parent=announced["record"]["parent"], identity=AUTHOR_A,
    )
    assert git_wrapper.push_create(clone, "origin", legacy_ref, legacy["commit"])["ok"] is True

    evaluation = announcements.evaluate(clone, announced["ref"])
    assert evaluation["state"] == "coexistence" and evaluation["record"] is None
    repeated = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert repeated["status"] == "announcement_coordinate_coexistence" and repeated["record"] is None
    assert announcements.withdraw(clone, "origin", announced["ref"], author_email=AUTHOR_A["email"])["status"] == "announcement_coordinate_coexistence"
    row = {row["branch"]: row for row in discovery.list_open(clone)["rows"]}[SERIES_BRANCH]
    assert row["open"] is True and row["announcements"] == []

    # Work routing is part of the same cutover. Neither ambiguous coordinate
    # may leak A's body through the branch-name projection, so an independent
    # author still receives the unsuffixed contribution branch.
    competitor = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_B["name"], author_email=AUTHOR_B["email"],
    )
    assert competitor["ok"] is True
    assert competitor["record"]["contrib_branch"] == "ai-org/contrib/demo-series"


def test_submission_observes_remote_coordinate_coexistence_from_stale_clone(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    author_clone = _clone(canonical, tmp_path / "author-clone")
    other_clone = _clone(canonical, tmp_path / "other-clone")
    announced = announcements.announce(
        author_clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    _author_contrib_branch(
        author_clone, announced["record"]["contrib_branch"], AUTHOR_A
    )

    # A different process publishes the compatibility alias after the author
    # clone's last sync. Local-only evaluation still sees canonical-only, but
    # the remote publication boundary must suppress the body.
    legacy_ref = announcements.announcement_ref(
        "demo-series", "", announcements.author_slug(AUTHOR_A["email"])
    )
    legacy_record = {
        "schema": announcements.LEGACY_ANNOUNCEMENT_SCHEMA,
        "series_branch": SERIES_BRANCH,
        "node_path": ".",
        "series_head_at_announcement": announced["record"]["series_head_at_announcement"],
        "contrib_branch": announced["record"]["contrib_branch"],
        "author": AUTHOR_A,
        "status": "authoring",
    }
    legacy = git_wrapper.create_ref_with_files(
        other_clone, legacy_ref,
        {announcements.LEGACY_ANNOUNCEMENT_RECORD_PATH: legacy_record},
        subject="authoring-intent: late compatibility alias",
        parent=announced["record"]["parent"], identity=AUTHOR_A,
    )
    assert git_wrapper.push_create(
        other_clone, "origin", legacy_ref, legacy["commit"]
    )["ok"] is True
    assert announcements.evaluate(author_clone, announced["ref"])["state"] == "authoring"

    result = submission.submit(author_clone, announced["ref"])

    assert result["ok"] is False and result["status"] == "announcement_unusable"
    assert result["evaluation"]["state"] == "coexistence"
    remote_branch = f"refs/heads/{announced['record']['contrib_branch']}"
    assert git_wrapper.ls_remote(author_clone, "origin", remote_branch)["refs"] == {}


def test_discovery_open_predicate_is_task_status_only(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    _push_extra_series_without_direction_ok(tmp_path, canonical, "ai-org/patch-series/undecided")
    clone = _clone(canonical, tmp_path / "clone-a")

    listing = discovery.list_open(clone)
    assert listing["ok"] is True
    by_branch = {row["branch"]: row for row in listing["rows"]}
    assert by_branch[SERIES_BRANCH]["open"] is True
    assert by_branch[SERIES_BRANCH]["announcements"] == []
    assert by_branch[SERIES_BRANCH]["contrib_branch"] == "ai-org/contrib/demo-series"
    assert by_branch["ai-org/patch-series/undecided"]["open"] is False
    assert "not_direction_ok" in by_branch["ai-org/patch-series/undecided"]["reasons"]

    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True
    _author_contrib_branch(clone, "ai-org/contrib/demo-series", AUTHOR_A)
    assert submission.submit(clone, announced["ref"])["ok"] is True

    # Announced AND submitted: the series still lists OPEN — announcements and
    # published branches are visibility, never open-predicate inputs.
    listing = discovery.list_open(clone)
    row = {row["branch"]: row for row in listing["rows"]}[SERIES_BRANCH]
    assert row["open"] is True and row["reasons"] == []
    assert [entry["author"]["email"] for entry in row["announcements"]] == [AUTHOR_A["email"]]
    assert row["published_contrib_branches"] == ["ai-org/contrib/demo-series"]

    # Withdrawn announcement disappears from visibility after a re-sync.
    assert announcements.withdraw(clone, "origin", announced["ref"], author_email=AUTHOR_A["email"])["ok"] is True
    listing = discovery.list_open(clone)
    row = {row["branch"]: row for row in listing["rows"]}[SERIES_BRANCH]
    assert row["announcements"] == [] and row["open"] is True


def test_discovery_vets_one_frozen_series_before_rows_and_preserves_diagnostic(
    tmp_path, monkeypatch
):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    synced = discovery.sync(clone)
    assert synced["ok"] is True
    series_ref = f"refs/remotes/origin/{SERIES_BRANCH}"
    frozen = git_wrapper.head_sha(clone, series_ref)
    assert frozen is not None

    events = []
    source = patch_series_gate.FrozenNetworkSourceVector(
        route="no_network_discovery",
        branch=SERIES_BRANCH,
        frozen_root_oid=frozen,
        root_generation=patch_series_bodies.ROOT_GENERATION_V2,
        root_sha256="a" * 64,
        scope_decomposition_sha256="b" * 64,
        source_path=patch_series_bodies.ROOT_APPROACH_PATH,
        context=patch_series_bodies.ROOT_APPROACH_CONTEXT,
        canonical_digest="a" * 64,
    )
    diagnostic = patch_series_gate.PublicationDiagnostic(
        route="no_network_discovery",
        frozen_root_oid=frozen,
        cue_location="root.problem.patch_plan.0.production_obligation_ids",
        rule="production-obligation-exact-partition",
        field="production_obligation_ids",
        goal_id="goal:preview",
        obligation_id="obligation:preview",
        detail="the frozen scope omits its production obligation",
        context=patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
    )
    rejected = patch_series_gate.AuthorabilityDecision(
        False, False, source, diagnostic
    )

    def fake_vet(repo, branch, route, *, frozen_root_oid=None, **kwargs):
        events.append(("vet", repo, branch, route, frozen_root_oid, kwargs))
        return rejected

    def fake_nodes(repo, ref, series_id, *, root_snapshot=None):
        events.append(("nodes", repo, ref, series_id, root_snapshot.frozen_oid))
        return [
            (".", [], "ai-org/contrib/demo-series"),
            (
                "sub/stale_child",
                ["lifecycle:stale"],
                "ai-org/contrib/demo-series-stale_child",
            ),
        ]

    monkeypatch.setattr(discovery, "sync", lambda _repo, _remote="origin": synced)
    monkeypatch.setattr(
        discovery.patch_series_gate, "vet_producer_pair_closure", fake_vet
    )
    monkeypatch.setattr(discovery, "_authorable_nodes", fake_nodes)

    before = (
        _git(clone, "show-ref").stdout,
        _git(clone, "ls-tree", "-r", frozen).stdout,
        _git(clone, "status", "--porcelain").stdout,
    )
    listing = discovery.list_open(clone)
    after = (
        _git(clone, "show-ref").stdout,
        _git(clone, "ls-tree", "-r", frozen).stdout,
        _git(clone, "status", "--porcelain").stdout,
    )

    assert listing["ok"] is True
    assert len(listing["rows"]) == 2
    assert events[0] == (
        "vet",
        clone,
        SERIES_BRANCH,
        "no_network_discovery",
        frozen,
        {},
    )
    assert events[1][0] == "nodes"
    assert [event[0] for event in events].count("vet") == 1
    assert listing["rows"][0]["reasons"] == ["producer-pair-vet-failed"]
    assert listing["rows"][1]["reasons"] == [
        "lifecycle:stale",
        "producer-pair-vet-failed",
    ]
    assert all(row["open"] is False for row in listing["rows"])

    public = listing["rows"][0]["authorability_decision"]
    assert public["vet_passed"] is False
    assert public["authorable"] is False
    assert public["source"]["route"] == "no_network_discovery"
    assert public["source"]["frozen_root_oid"] == frozen
    assert public["source"]["context"] == patch_series_bodies.ROOT_APPROACH_CONTEXT
    assert public["diagnostic"] == {
        "route": "no_network_discovery",
        "frozen_root_oid": frozen,
        "cue_location": "root.problem.patch_plan.0.production_obligation_ids",
        "rule": "production-obligation-exact-partition",
        "goal_id": "goal:preview",
        "obligation_id": "obligation:preview",
        "field": "production_obligation_ids",
        "detail": "the frozen scope omits its production obligation",
        "context": patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
    }
    assert after == before


def test_no_network_discovery_vets_before_route_predicates(monkeypatch):
    frozen = "a" * 40
    series_ref = f"refs/remotes/origin/{SERIES_BRANCH}"
    events = []

    class Snapshot:
        frozen_oid = frozen
        generation = patch_series_bodies.ROOT_GENERATION_V2
        source_path = ""
        context = ""
        canonical_digest = ""

        @staticmethod
        def identity():
            return {"generation": patch_series_bodies.ROOT_GENERATION_V2}

    snapshot = Snapshot()
    source = patch_series_gate.FrozenNetworkSourceVector(
        route="no_network_discovery",
        branch=SERIES_BRANCH,
        frozen_root_oid=frozen,
        root_generation=patch_series_bodies.ROOT_GENERATION_V2,
        root_snapshot=snapshot,
    )

    monkeypatch.setattr(
        discovery.git_wrapper,
        "head_sha",
        lambda _repo, ref: events.append(("freeze", ref)) or frozen,
    )

    def fake_vet(_repo, branch, route, *, frozen_root_oid=None, **_kwargs):
        events.append(("vet", route, frozen_root_oid))
        assert branch == SERIES_BRANCH
        return patch_series_gate.AuthorabilityDecision(True, True, source)

    monkeypatch.setattr(
        discovery.patch_series_gate, "vet_producer_pair_closure", fake_vet
    )

    def has_subject(_repo, ref, subject):
        events.append(("subject", ref, subject))
        return subject == "patch_series: direction-ok"

    monkeypatch.setattr(discovery.git_wrapper, "has_subject", has_subject)
    monkeypatch.setattr(
        discovery,
        "_default_ref",
        lambda *_args: events.append(("default",)) or "main",
    )
    monkeypatch.setattr(
        discovery.git_wrapper,
        "is_ancestor",
        lambda _repo, ref, default: (
            events.append(("ancestor", ref, default)) or False
        ),
    )

    def authorable_nodes(_repo, ref, series_id, *, root_snapshot=None):
        events.append(("nodes", ref, series_id, root_snapshot.frozen_oid))
        return [(".", [], "ai-org/contrib/demo-series")]

    monkeypatch.setattr(discovery, "_authorable_nodes", authorable_nodes)
    monkeypatch.setattr(discovery.announcements, "list_for_address", lambda *_args: [])

    rows = discovery._series_rows(
        "repo",
        "origin",
        {f"refs/heads/{SERIES_BRANCH}": frozen},
        SERIES_BRANCH,
    )

    assert rows[0]["open"] is True
    assert events[:2] == [
        ("freeze", series_ref),
        ("vet", "no_network_discovery", frozen),
    ]
    assert all(
        event[1] == frozen
        for event in events
        if event[0] in {"subject", "ancestor", "nodes"}
    )


def test_submission_requires_author_identity_and_fast_forward_only(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    missing = submission.submit(clone, announced["ref"])
    assert missing["ok"] is False and missing["status"] == "contrib_branch_missing"

    _author_contrib_branch(clone, "ai-org/contrib/demo-series", AUTHOR_B)
    imposter = submission.submit(clone, announced["ref"])
    assert imposter["ok"] is False and imposter["status"] == "author_identity_mismatch"

    _author_contrib_branch(clone, "ai-org/contrib/demo-series", AUTHOR_A, subject="patch: fix authorship")
    submitted = submission.submit(clone, announced["ref"])
    assert submitted["ok"] is True and submitted["status"] == "submitted_for_maintainer_review"
    assert submission.submit(clone, announced["ref"])["status"] == "already_submitted"

    # Follow-up commits push fast-forward (the cross-clone re-implement loop).
    git_wrapper.commit_files(
        clone, "ai-org/contrib/demo-series", {"fix.txt": "follow-up\n"}, subject="patch: follow-up", identity=AUTHOR_A
    )
    resubmitted = submission.submit(clone, announced["ref"])
    assert resubmitted["ok"] is True and resubmitted["status"] == "resubmitted_for_maintainer_review"


def test_submission_binds_full_normalized_author_identity(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    same_email_wrong_name = {"name": "Not Announced A", "email": AUTHOR_A["email"]}
    _author_contrib_branch(
        clone, "ai-org/contrib/demo-series", same_email_wrong_name
    )

    rejected = submission.submit(clone, announced["ref"])

    assert rejected["ok"] is False
    assert rejected["status"] == "author_identity_mismatch"
    assert rejected["commit_author"] == same_email_wrong_name


def test_parent_routes_announced_record_into_code_worker(tmp_path, monkeypatch):
    """work.implement_announced is the PARENT: it holds the author identity
    and hands the announced record to the code worker (fresh drive) or to the
    feedback repair path — never to a whole-brief single shot."""
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True

    captured: dict[str, object] = {}

    def fake_drive(repo, record, *, attempt):
        captured.update({"mode": "drive", "repo": repo, "record": record, "attempt": attempt})
        return {"ok": True, "branch": record["contrib_branch"]}

    def fake_feedback(repo, record, *, feedback, attempt):
        captured.update({"mode": "feedback", "record": record, "feedback": feedback, "attempt": attempt})
        return {"ok": True, "branch": record["contrib_branch"]}

    monkeypatch.setattr(work.code_worker, "drive", fake_drive)
    monkeypatch.setattr(work.code_worker, "address_feedback", fake_feedback)

    result = work.implement_announced(clone, announced["ref"])
    assert result["ok"] is True
    assert captured["mode"] == "drive"
    assert captured["record"]["series_branch"] == SERIES_BRANCH
    assert captured["record"]["contrib_branch"] == "ai-org/contrib/demo-series"
    assert captured["record"]["author"] == AUTHOR_A
    # The local series branch was materialized at the fetched head.
    assert git_wrapper.head_sha(clone, SERIES_BRANCH) == git_wrapper.head_sha(clone, f"origin/{SERIES_BRANCH}")

    blockers = [{"where": "app", "why": "broken"}]
    result = work.implement_announced(clone, announced["ref"], feedback=blockers, attempt=2)
    assert result["ok"] is True
    assert captured["mode"] == "feedback" and captured["feedback"] == blockers and captured["attempt"] == 2

    gone = work.implement_announced(clone, "refs/ai-org/authoring-announcements/demo-series/nobody")
    assert gone["ok"] is False and gone["status"] == "announcement_unusable"


def test_acceptance_pull_judges_submitted_contribution_with_announcement_anchors(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"]
    )
    assert announced["ok"] is True
    _author_contrib_branch(clone, "ai-org/contrib/demo-series", AUTHOR_A)
    assert submission.submit(clone, announced["ref"])["ok"] is True

    captured: dict[str, object] = {}

    def fake_check(
        repo,
        branch,
        *,
        patch_series_branch,
        node_key,
        root_generation_snapshot,
    ):
        captured.update(
            {
                "branch": branch,
                "patch_series_branch": patch_series_branch,
                "node_key": node_key,
                "root_generation_oid": root_generation_snapshot.frozen_oid,
            }
        )
        # Install the verdict marker the way functional_check does (bare-safe).
        head = git_wrapper.head_sha(repo, branch)
        tree = _git(Path(repo), "rev-parse", f"{head}^{{tree}}").stdout.strip()
        marker = _git(
            Path(repo),
            "-c",
            "user.name=Judge",
            "-c",
            "user.email=judge@users.noreply.github.com",
            "commit-tree",
            tree,
            "-p",
            head,
            "-m",
            "acceptance: reachable",
        ).stdout.strip()
        _git(Path(repo), "update-ref", f"refs/heads/{branch}", marker)
        return {"ok": True, "reachable": True, "blockers": [], "notes": ""}

    monkeypatch.setattr(functional_check, "check", fake_check)
    result = functional_check.acceptance_pull(canonical)
    assert result == {"ok": True, "reachable": True, "blockers": [], "notes": ""}
    assert captured == {
        "branch": "ai-org/contrib/demo-series",
        "patch_series_branch": SERIES_BRANCH,
        "node_key": "root",
        "root_generation_oid": git_wrapper.head_sha(canonical, SERIES_BRANCH),
    }
    # Once the verdict marker is installed, the branch leaves the queue.
    assert functional_check.acceptance_pull(canonical) is None


def test_acceptance_pull_never_treats_v2_subject_as_historical_verdict(monkeypatch):
    branch = "ai-org/contrib/v2"
    series_branch = "ai-org/patch-series/v2"
    calls = []

    monkeypatch.setattr(
        functional_check.git_wrapper, "branches", lambda *_args, **_kwargs: [branch]
    )
    monkeypatch.setattr(
        functional_check.producer_lifecycle,
        "has_implementation_submission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        functional_check.announcements,
        "find_for_contrib",
        lambda *_args, **_kwargs: {
            "record": {"series_branch": series_branch, "node_path": "."}
        },
    )
    monkeypatch.setattr(
        functional_check.patch_series_bodies,
        "classify_root_generation",
        lambda *_args, **_kwargs: type(
            "V2Snapshot",
            (),
            {
                "generation": patch_series_bodies.ROOT_GENERATION_V2,
                "lifecycle_ready": True,
                "frozen_oid": "f" * 40,
            },
        )(),
    )
    monkeypatch.setattr(
        functional_check.git_wrapper,
        "log_subjects",
        lambda *_args, **_kwargs: ["acceptance: reachable"],
    )
    monkeypatch.setattr(
        functional_check.patch_series_gate,
        "vet_producer_pair_closure",
        lambda *_args, **_kwargs: type("OpenGate", (), {"blocked": False})(),
    )
    monkeypatch.setattr(
        functional_check,
        "current_tip_has_terminal_verdict",
        lambda *_args, **_kwargs: False,
    )

    def check(
        _repo,
        selected,
        *,
        patch_series_branch,
        node_key,
        root_generation_snapshot,
    ):
        calls.append(
            (
                selected,
                patch_series_branch,
                node_key,
                root_generation_snapshot.frozen_oid,
            )
        )
        return {"ok": False, "reachable": False, "blockers": [], "notes": "checked"}

    monkeypatch.setattr(functional_check, "check", check)

    result = functional_check.acceptance_pull("repo")

    assert result["notes"] == "checked"
    assert calls == [(branch, series_branch, "root", "f" * 40)]


def test_acceptance_pull_vets_v2_before_the_terminal_verdict_shortcut(monkeypatch):
    branch = "ai-org/contrib/v2-gate-closed"
    series_branch = "ai-org/patch-series/v2-gate-closed"
    frozen_oid = "f" * 40
    events = []
    source = patch_series_gate.FrozenNetworkSourceVector(
        route="producer_functional_acceptance",
        branch=series_branch,
        frozen_root_oid=frozen_oid,
        root_generation=patch_series_bodies.ROOT_GENERATION_V2,
        context=patch_series_bodies.ROOT_APPROACH_CONTEXT,
    )
    diagnostic = patch_series_gate.PublicationDiagnostic(
        route="producer_functional_acceptance",
        frozen_root_oid=frozen_oid,
        cue_location="root.problem.production_obligations[0]",
        rule="producer-pair-required",
        goal_id="goal:one",
        obligation_id="obligation:one",
        detail="producer pair is incomplete",
        context=patch_series_bodies.ROOT_APPROACH_CONTEXT,
    )
    blocked = patch_series_gate.AuthorabilityDecision(
        False, False, source, diagnostic
    )

    monkeypatch.setattr(
        functional_check.git_wrapper, "branches", lambda *_args, **_kwargs: [branch]
    )
    monkeypatch.setattr(
        functional_check.producer_lifecycle,
        "has_implementation_submission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        functional_check.announcements,
        "find_for_contrib",
        lambda *_args, **_kwargs: {
            "record": {"series_branch": series_branch, "node_path": "."}
        },
    )
    monkeypatch.setattr(
        functional_check.patch_series_bodies,
        "classify_root_generation",
        lambda *_args, **_kwargs: type(
            "V2Snapshot",
            (),
            {
                "generation": patch_series_bodies.ROOT_GENERATION_V2,
                "lifecycle_ready": True,
                "frozen_oid": frozen_oid,
            },
        )(),
    )

    def close_gate(_repo, selected, route, *, frozen_root_oid):
        events.append(("gate", selected, route, frozen_root_oid))
        return blocked

    def forbidden_terminal(*_args, **_kwargs):
        raise AssertionError("terminal shortcut ran before the common vet")

    def forbidden_judge(*_args, **_kwargs):
        raise AssertionError("judge ran after the common vet rejected the source")

    monkeypatch.setattr(
        functional_check.patch_series_gate, "vet_producer_pair_closure", close_gate
    )
    monkeypatch.setattr(
        functional_check, "current_tip_has_terminal_verdict", forbidden_terminal
    )
    monkeypatch.setattr(functional_check, "check", forbidden_judge)

    result = functional_check.acceptance_pull("repo")

    assert result["ok"] is False
    assert result["status"] == "producer_functional_acceptance_gate_closed"
    assert result["authorability"]["diagnostic"] == diagnostic.as_dict()
    assert events == [(
        "gate",
        series_branch,
        "producer_functional_acceptance",
        frozen_oid,
    )]


def test_gate_and_acceptance_reject_contribution_with_wrong_full_identity(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    same_email_wrong_name = {"name": "Not Announced A", "email": AUTHOR_A["email"]}
    _author_contrib_branch(
        clone, "ai-org/contrib/demo-series", same_email_wrong_name
    )
    assert git_wrapper.push_create(
        clone,
        "origin",
        "refs/heads/ai-org/contrib/demo-series",
        git_wrapper.head_sha(clone, "ai-org/contrib/demo-series"),
    )["ok"] is True

    projected = patch_series_gate._authoring_projected_lifecycle(
        canonical,
        SERIES_BRANCH,
        ".",
        {"lifecycle_status": "ready_for_patch_authoring"},
    )

    assert projected == "claimed_by_patch_author"
    assert functional_check.acceptance_pull(canonical) is None
    assert announcements.find_for_contrib(
        canonical, "ai-org/contrib/demo-series"
    ) is None


def test_full_identity_binding_looks_through_independent_acceptance_marker(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    _author_contrib_branch(clone, "ai-org/contrib/demo-series", AUTHOR_A)
    head = git_wrapper.head_sha(clone, "ai-org/contrib/demo-series")
    tree = _git(clone, "rev-parse", f"{head}^{{tree}}").stdout.strip()
    marker = _git(
        clone,
        "-c", "user.name=Judge",
        "-c", "user.email=judge@users.noreply.github.com",
        "commit-tree", tree,
        "-p", head,
        "-m", "acceptance: reachable",
    ).stdout.strip()
    _git(clone, "update-ref", "refs/heads/ai-org/contrib/demo-series", marker)

    assert announcements.contribution_author_matches(
        clone, "ai-org/contrib/demo-series", announced["record"]
    ) is True


def test_full_identity_binding_only_looks_through_registered_acceptance_subjects(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    branch = "ai-org/contrib/demo-series"
    _author_contrib_branch(clone, branch, AUTHOR_A)
    judge = {"name": "Judge", "email": "judge@users.noreply.github.com"}
    git_wrapper.commit_files(
        clone, branch, {"verdict.txt": "not a verdict\n"},
        subject="acceptance: pending", identity=judge,
    )

    assert announcements.contribution_author_matches(
        clone, branch, announced["record"]
    ) is False

    exact_branch = f"{branch}-r2"
    _author_contrib_branch(clone, exact_branch, AUTHOR_A)
    git_wrapper.commit_files(
        clone, exact_branch, {"verdict.txt": "blocked\n"},
        subject="acceptance: blocked", identity=judge,
    )
    exact_record = dict(announced["record"], contrib_branch=exact_branch)
    assert announcements.contribution_author_matches(
        clone, exact_branch, exact_record
    ) is True


def test_acceptance_pull_does_not_skip_acceptance_like_contributor_subject(tmp_path, monkeypatch):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    announced = announcements.announce(
        clone, "origin", SERIES_BRANCH,
        author_name=AUTHOR_A["name"], author_email=AUTHOR_A["email"],
    )
    _author_contrib_branch(
        clone, "ai-org/contrib/demo-series", AUTHOR_A,
        subject="acceptance: pending",
    )
    assert submission.submit(clone, announced["ref"])["ok"] is True
    checked: list[str] = []

    def fake_check(repo, branch, *, patch_series_branch, node_key):
        checked.append(branch)
        return {"ok": False, "reachable": False, "blockers": [], "notes": ""}

    monkeypatch.setattr(functional_check, "check", fake_check)

    functional_check.acceptance_pull(canonical)

    assert checked == ["ai-org/contrib/demo-series"]


def test_announcement_record_parsing_is_fail_closed_with_typed_errors():
    record, errors = announcements.parse_announcement_record("not json {")
    assert record is None and errors[0]["type"] == "announcement_record_unparseable"

    record, errors = announcements.parse_announcement_record(json.dumps({"schema": announcements.ANNOUNCEMENT_SCHEMA}))
    assert record is None and all(error["type"] == "announcement_record_field_missing" for error in errors)

    valid = announcements.build_record(
        series_branch=SERIES_BRANCH,
        node_path=".",
        series_head_at_announcement="a" * 40,
        contrib_branch="ai-org/contrib/demo-series",
        author_name=AUTHOR_A["name"],
        author_email=AUTHOR_A["email"],
    )
    record, errors = announcements.parse_announcement_record(json.dumps(valid))
    assert errors == [] and record["status"] == "authoring"

    real_email = dict(valid, author={"name": "X", "email": "requester@example.com"})
    record, errors = announcements.parse_announcement_record(json.dumps(real_email))
    assert record is None and {"type": "announcement_record_field_invalid", "field": "author"} in errors

    unknown = dict(valid, lease={"renew_within": "P7D"})
    record, errors = announcements.parse_announcement_record(json.dumps(unknown))
    # The retired reservation vocabulary (lease) is an UNKNOWN field now:
    # nothing is held, so nothing can carry a lease.
    assert record is None and {"type": "announcement_record_unknown_field", "field": "lease"} in errors

    withdrawn_status = dict(valid, status="withdrawn")
    record, errors = announcements.parse_announcement_record(json.dumps(withdrawn_status))
    # Withdrawal is ref REMOVAL, never a status value.
    assert record is None and {"type": "announcement_record_field_invalid", "field": "status"} in errors


def test_author_slug_is_deterministic_and_ref_safe():
    assert announcements.author_slug("101+author-a@users.noreply.github.com") == "101-author-a"
    assert announcements.author_slug("0+fixture-user@users.noreply.github.com") == "0-fixture-user"
    assert announcements.author_slug("Mixed.Case+X@users.noreply.github.com") == "mixed-case-x"
    assert announcements.author_slug("@users.noreply.github.com") == ""


def test_announcement_identity_normalization_rejects_embedded_controls():
    assert announcements.normalize_noreply_identity(
        "  Author A  ", "  101+AUTHOR-A@users.noreply.github.com  "
    ) == AUTHOR_A
    assert announcements.normalize_noreply_identity(
        "Author A\nInjected", AUTHOR_A["email"]
    ) is None
    assert announcements.normalize_noreply_identity(
        AUTHOR_A["name"], "101+author-a\n@users.noreply.github.com"
    ) is None


def test_invalid_announcement_is_ignored_and_never_blocks_anything(tmp_path):
    canonical = _canonical_with_series(tmp_path)
    clone = _clone(canonical, tmp_path / "clone-a")
    series_head = git_wrapper.head_sha(clone, f"origin/{SERIES_BRANCH}")
    ref = announcements.announcement_ref("demo-series", "", "broken-author")
    broken = git_wrapper.create_ref_with_files(
        clone,
        ref,
        {"announcement.json": {"schema": "wrong"}},
        subject="authoring-intent: broken",
        parent=series_head,
        identity=AUTHOR_A,
    )
    assert git_wrapper.push_create(clone, "origin", ref, broken["commit"])["ok"] is True

    evaluation = announcements.evaluate(clone, ref)
    assert evaluation["state"] == "invalid" and evaluation["errors"]

    # An invalid announcement blocks nothing: the series is open, another
    # author announces normally, and the invalid ref is not in visibility.
    listing = discovery.list_open(clone)
    row = {row["branch"]: row for row in listing["rows"]}[SERIES_BRANCH]
    assert row["open"] is True and row["announcements"] == []
    second = announcements.announce(
        clone, "origin", SERIES_BRANCH, author_name=AUTHOR_B["name"], author_email=AUTHOR_B["email"]
    )
    assert second["ok"] is True and second["status"] == "authoring_announced"


def test_gate_projects_claimed_and_submitted_from_announcement_refs(tmp_path):
    from ai_org.patchwork_queue import patch_series_gate as network

    repo = tmp_path / "canonical-local"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _seed_commit(repo, "base")
    _git(repo, "branch", "-M", "main")
    branch = "ai-org/patch-series/proj"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "identity_stage": "serialized-parent",
                "node_path": ".",
                "branch": branch,
                "relation_from_parent": "root",
                "lifecycle_status": "posted_to_mailing_list",
                "ownership": {"request_owner": "requester", "interior_owner": "parent"},
                "write_scope": {"allowed_subtree": "."},
                "scope_item_ids": [],
                "children": [{
                    "child_key": "api",
                    "node_path": "sub/api",
                    "lifecycle_status": "ready_for_patch_authoring",
                    "edges": [],
                }],
                "declared_patchwork_checks": [],
            },
            "sub/api/patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "identity_stage": "forming",
                "id": "proj:sub/api",
                "child_key": "api",
                "node_path": "sub/api",
                "parent_branch": branch,
                "relation_from_parent": "split-into",
                "split_operator": "AND",
                "lifecycle_status": "ready_for_patch_authoring",
                "contrib_branch": "ai-org/contrib/proj-api",
                "edges": [],
                "scope_item_ids": [],
                "ownership": {"request_owner": "parent", "interior_owner": "child"},
                "write_scope": {"allowed_subtree": "sub/api/"},
            },
        },
        commit_message="network: projection fixture",
    )

    manifest = {
        "schema": "patch_series-network-node-v1",
        "lifecycle_status": "ready_for_patch_authoring",
        "contrib_branch": "ai-org/contrib/proj-api",
        "edges": [],
    }

    def api_lifecycle() -> str:
        return network._authoring_projected_lifecycle(repo, branch, "sub/api", manifest) or (
            "ready_for_patch_authoring"
        )

    # No announcement ref: the stored ready state passes through unchanged.
    assert api_lifecycle() == "ready_for_patch_authoring"

    # Authoring announcement, no contribution branch: claimed_by_patch_author
    # is a pure ref-derived projection (kernel vocabulary KEPT; its no-lock
    # meaning is "some author announced and began authoring").
    series_head = git_wrapper.head_sha(repo, branch)
    slug = announcements.author_slug(AUTHOR_A["email"])
    ref = announcements.announcement_ref("proj", "api", slug)
    record = announcements.build_record(
        series_branch=branch,
        node_path="sub/api",
        series_head_at_announcement=series_head,
        contrib_branch="ai-org/contrib/proj-api",
        author_name=AUTHOR_A["name"],
        author_email=AUTHOR_A["email"],
    )
    git_wrapper.create_ref_with_files(
        repo, ref, {"announcement.json": record}, subject="authoring-intent: proj api", parent=series_head, identity=AUTHOR_A
    )
    assert api_lifecycle() == "claimed_by_patch_author"
    stored = json.loads(_git(repo, "show", f"{branch}:sub/api/patch-series-manifest.json").stdout)
    assert stored["lifecycle_status"] == "ready_for_patch_authoring"

    # A second family announcing on the same address changes nothing: the
    # projection reports authoring activity, never a single owner.
    ref_b = announcements.announcement_ref("proj", "api", announcements.author_slug(AUTHOR_B["email"]))
    record_b = announcements.build_record(
        series_branch=branch,
        node_path="sub/api",
        series_head_at_announcement=series_head,
        contrib_branch="ai-org/contrib/proj-api-r2",
        author_name=AUTHOR_B["name"],
        author_email=AUTHOR_B["email"],
    )
    git_wrapper.create_ref_with_files(
        repo, ref_b, {"announcement.json": record_b}, subject="authoring-intent: proj api", parent=series_head, identity=AUTHOR_B
    )
    assert api_lifecycle() == "claimed_by_patch_author"

    # Any announcing family's contribution branch pushed: submitted projection.
    _author_contrib_branch(repo, "ai-org/contrib/proj-api-r2", AUTHOR_B)
    assert api_lifecycle() == "submitted_for_maintainer_review"

    # Withdrawal is removal: with B's announcement gone, B's branch no longer
    # counts; A's family is still authoring (no branch yet).
    _git(repo, "update-ref", "-d", ref_b)
    assert api_lifecycle() == "claimed_by_patch_author"
    _git(repo, "update-ref", "-d", ref)
    assert api_lifecycle() == "ready_for_patch_authoring"


def _canonical_with_series(tmp_path: Path) -> Path:
    canonical = tmp_path / "canonical.git"
    subprocess.run(["git", "init", "--bare", str(canonical)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(canonical), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _seed_commit(seed, "base")
    _git(seed, "branch", "-M", "main")
    _git(seed, "checkout", "-B", SERIES_BRANCH, "main")
    (seed / "patch-series-cover-letter.json").write_text(json.dumps({"working_title": "Demo series"}) + "\n", encoding="utf-8")
    _git(seed, "add", "patch-series-cover-letter.json")
    _seed_commit(seed, "patch_series: receive Demo series")
    _seed_commit(seed, "patch_series: direction-ok", allow_empty=True)
    _git(seed, "checkout", "main")
    _git(seed, "remote", "add", "origin", str(canonical))
    _git(seed, "push", "origin", "main", SERIES_BRANCH)
    return canonical


def _push_extra_series_without_direction_ok(tmp_path: Path, canonical: Path, branch: str) -> None:
    seed = tmp_path / "seed"
    _git(seed, "checkout", "-B", branch, "main")
    _seed_commit(seed, f"patch_series: receive {branch.rsplit('/', 1)[-1]}", allow_empty=True)
    _git(seed, "checkout", "main")
    _git(seed, "push", "origin", branch)


def _author_contrib_branch(repo: Path, branch: str, author: dict[str, str], *, subject: str = "patch: demo") -> None:
    base = "origin/main" if git_wrapper.head_sha(repo, "origin/main") else "main"
    if git_wrapper.branch_exists(repo, branch):
        git_wrapper.commit_files(repo, branch, {"work.txt": f"{subject}\n"}, subject=subject, identity=author)
        return
    git_wrapper.create_branch_with_files(
        repo, branch, base, {"work.txt": "work\n"}, commit_message=subject, identity=author
    )


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
