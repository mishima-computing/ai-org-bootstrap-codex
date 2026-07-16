from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess

import pytest

from ai_org import maintainer_merge, patch_series_bodies
from ai_org.maintainer_merge import evidence, subsystem
from ai_org.patch_author import announcements
from ai_org.patchwork_queue import patch_series_gate


def test_sealed_code_only_subsystem_and_verified_mainline_transitions(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    base_oid = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", evidence.AUTHORITY_NOTES_REF, base_oid)
    _git(repo, "checkout", "-b", "ai-org/contrib/family-a")
    (repo / "producer-task-binding.cue").write_text("binding: true\n", encoding="utf-8")
    evidence_path = repo / "producer-evidence" / ("a" * 64) / "producer-promise.cue"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text("promise: true\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "producer-task-binding: materialize fixture")
    binding_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()

    (repo / "feature.py").write_text("VALUE = 'sealed implementation'\n", encoding="utf-8")
    (repo / "implementation-result.cue").write_text("result: true\n", encoding="utf-8")
    (repo / patch_series_bodies.SCOPE_DECOMPOSITION_PATH).write_text(
        "scope: producer-side rewrite\n", encoding="utf-8"
    )
    (repo / announcements.ANNOUNCEMENT_RECORD_PATH).write_text(
        "announcement: producer lifecycle\n", encoding="utf-8"
    )
    frame_path = (
        repo
        / evidence.IMPLEMENTATION_CUE_DIRECTORY
        / "patch_plan-demo-follow-up-11"
        / "frame.cue"
    )
    frame_path.parent.mkdir(parents=True)
    frame_path.write_text("#StateSpace: {ready: true}\n", encoding="utf-8")
    evidence_path.write_text("promise: updated\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "implement: family a")
    implementation_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "acceptance: reachable",
        "-m",
        "parallel family a",
    )
    verdict_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-b", "ai-org/contrib/family-b", implementation_oid)
    _git(repo, "commit", "--allow-empty", "-m", "acceptance: reachable")
    family_b_verdict_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")

    link = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "b" * 64,
        verdict_oid,
        implementation_oid,
    )
    candidate = evidence.SealedContribution(
        (link,),
        "ai-org/contrib/family-a",
        binding_oid,
        evidence.changed_code_paths(repo, binding_oid, implementation_oid),
        base_oid,
    )
    family_b_link = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "c" * 64,
        family_b_verdict_oid,
        implementation_oid,
    )
    family_b_candidate = evidence.SealedContribution(
        (family_b_link,),
        "ai-org/contrib/family-b",
        binding_oid,
        candidate.code_paths,
        base_oid,
    )
    assert candidate.code_paths == ("feature.py",)

    monkeypatch.setattr(evidence, "is_migrated_tree", lambda *_args: True)
    monkeypatch.setattr(
        evidence,
        "sealed_contribution",
        lambda _repo, ref: family_b_candidate if ref.endswith("family-b") else candidate,
    )
    monkeypatch.setattr(
        evidence,
        "sealed_contribution_from_verdict",
        lambda _repo, oid, **_kwargs: (
            family_b_candidate if oid == family_b_verdict_oid else candidate
        ),
    )
    _write_fake_codex(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    subsystem_result = subsystem.review_and_integrate(
        repo, "ai-org/contrib/family-a", base="main"
    )

    assert subsystem_result["accept"] is True
    subsystem_ref = "refs/heads/ai-org/subsystem"
    assert subsystem_result["expected_ref_oids"] == {
        subsystem_ref: "",
        "refs/heads/ai-org/contrib/family-a": verdict_oid,
        "refs/heads/main": base_oid,
        evidence.AUTHORITY_NOTES_REF: base_oid,
    }
    assert subsystem_result["resulting_ref_oids"] == {
        subsystem_ref: _git(repo, "rev-parse", subsystem_ref).stdout.strip(),
        "refs/heads/ai-org/contrib/family-a": verdict_oid,
        "refs/heads/main": base_oid,
        evidence.AUTHORITY_NOTES_REF: base_oid,
    }
    assert _show(repo, "ai-org/subsystem", "feature.py") == "VALUE = 'sealed implementation'\n"
    assert _show(repo, "ai-org/subsystem", "implementation-result.cue") is None
    assert (
        _show(repo, "ai-org/subsystem", patch_series_bodies.SCOPE_DECOMPOSITION_PATH)
        is None
    )
    assert _show(repo, "ai-org/subsystem", announcements.ANNOUNCEMENT_RECORD_PATH) is None
    assert _show(repo, "ai-org/subsystem", str(frame_path.relative_to(repo))) is None
    assert _show(repo, "ai-org/subsystem", str(evidence_path.relative_to(repo))) is None
    assert not _is_ancestor(repo, implementation_oid, "ai-org/subsystem")
    subsystem_message = _git(
        repo, "show", "-s", "--format=%B", "ai-org/subsystem"
    ).stdout
    assert (
        f"{evidence.EXCLUSION_POLICY_TRAILER}: {evidence.EXCLUSION_POLICY_VERSION}"
        in subsystem_message
    )
    assert subsystem_message.count(f"{evidence.EVIDENCE_LINK_TRAILER}:") == 1
    assert link.trailer_value() in subsystem_message

    first_subsystem_oid = _git(repo, "rev-parse", subsystem_ref).stdout.strip()
    second_result = subsystem.review_and_integrate(
        repo, "ai-org/contrib/family-b", base="main"
    )

    assert second_result["accept"] is True, second_result
    assert second_result["expected_ref_oids"] == {
        subsystem_ref: first_subsystem_oid,
        "refs/heads/ai-org/contrib/family-b": family_b_verdict_oid,
        evidence.AUTHORITY_NOTES_REF: base_oid,
    }
    assert _show(repo, "ai-org/subsystem", "feature.py") == "VALUE = 'sealed implementation'\n"
    assert not _is_ancestor(repo, implementation_oid, "ai-org/subsystem")
    subsystem_history = _git(
        repo, "log", "--format=%B", "ai-org/subsystem"
    ).stdout
    assert link.trailer_value() in subsystem_history
    assert family_b_link.trailer_value() in subsystem_history

    mainline_result = maintainer_merge.pull(repo)

    assert mainline_result["accept"] is True, mainline_result
    mainline_ref = "refs/heads/ai-org/mainline"
    current_subsystem_oid = _git(repo, "rev-parse", subsystem_ref).stdout.strip()
    assert mainline_result["expected_ref_oids"] == {
        mainline_ref: "",
        "refs/heads/ai-org/subsystem": current_subsystem_oid,
        "refs/heads/main": base_oid,
        evidence.AUTHORITY_NOTES_REF: base_oid,
    }
    assert mainline_result["resulting_ref_oids"] == {
        mainline_ref: _git(repo, "rev-parse", mainline_ref).stdout.strip(),
        "refs/heads/ai-org/subsystem": current_subsystem_oid,
        "refs/heads/main": base_oid,
        evidence.AUTHORITY_NOTES_REF: base_oid,
    }
    assert _show(repo, "ai-org/mainline", "feature.py") == "VALUE = 'sealed implementation'\n"
    assert _show(repo, "ai-org/mainline", "implementation-result.cue") is None
    assert (
        _show(repo, "ai-org/mainline", patch_series_bodies.SCOPE_DECOMPOSITION_PATH)
        is None
    )
    assert _show(repo, "ai-org/mainline", announcements.ANNOUNCEMENT_RECORD_PATH) is None
    assert _show(repo, "ai-org/mainline", str(frame_path.relative_to(repo))) is None
    assert not _is_ancestor(repo, implementation_oid, "ai-org/mainline")
    mainline_message = _git(
        repo, "show", "-s", "--format=%B", "ai-org/mainline"
    ).stdout
    assert mainline_message.count(f"{evidence.EVIDENCE_LINK_TRAILER}:") == 2
    assert link.trailer_value() in mainline_message
    assert family_b_link.trailer_value() in mainline_message


def test_evidence_only_implementation_records_links_without_widening_projection(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    base_oid = _git(repo, "rev-parse", "main").stdout.strip()
    base_tree = _git(repo, "rev-parse", "main^{tree}").stdout.strip()
    _git(repo, "update-ref", evidence.AUTHORITY_NOTES_REF, base_oid)
    branch = "ai-org/contrib/evidence-only"
    _git(repo, "checkout", "-b", branch)
    lifecycle_path = repo / evidence.contributor_handoff.RESULT_PATH
    lifecycle_path.write_text("sealed producer evidence\n", encoding="utf-8")
    _git(repo, "add", str(lifecycle_path.relative_to(repo)))
    _git(repo, "commit", "-m", "implement: evidence only")
    implementation_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "commit", "--allow-empty", "-m", "acceptance: reachable")
    verdict_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")

    link = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "e" * 64,
        verdict_oid,
        implementation_oid,
    )
    candidate = evidence.SealedContribution(
        (link,), branch, base_oid, (), base_oid
    )
    assert evidence.changed_code_paths(repo, base_oid, implementation_oid) == ()
    monkeypatch.setattr(evidence, "is_migrated_tree", lambda *_args: True)
    monkeypatch.setattr(evidence, "sealed_contribution", lambda *_args: candidate)
    monkeypatch.setattr(
        evidence, "sealed_contribution_from_verdict", lambda *_args, **_kwargs: candidate
    )
    _write_fake_codex(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    subsystem_result = subsystem.review_and_integrate(repo, branch, base="main")

    assert subsystem_result["accept"] is True, subsystem_result
    assert (
        _git(repo, "rev-parse", "ai-org/subsystem^{tree}").stdout.strip()
        == base_tree
    )
    assert _show(repo, "ai-org/subsystem", str(lifecycle_path.relative_to(repo))) is None
    subsystem_message = _git(
        repo, "show", "-s", "--format=%B", "ai-org/subsystem"
    ).stdout
    assert link.trailer_value() in subsystem_message

    mainline_result = maintainer_merge.mainline.review_and_integrate(repo, base="main")

    assert mainline_result["accept"] is True, mainline_result
    assert _git(repo, "rev-parse", "ai-org/mainline^{tree}").stdout.strip() == base_tree
    assert _show(repo, "ai-org/mainline", str(lifecycle_path.relative_to(repo))) is None
    mainline_message = _git(
        repo, "show", "-s", "--format=%B", "ai-org/mainline"
    ).stdout
    assert link.trailer_value() in mainline_message


def test_migrated_subsystem_fails_closed_before_review_without_seal(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "ai-org/contrib/unsealed", "main")
    monkeypatch.setattr(evidence, "is_migrated_tree", lambda *_args: True)
    monkeypatch.setattr(
        evidence,
        "sealed_contribution",
        lambda *_args: (_ for _ in ()).throw(evidence.EvidenceError("not sealed")),
    )
    monkeypatch.setattr(
        subsystem,
        "_codex_verdict",
        lambda *_args: (_ for _ in ()).throw(AssertionError("review ran before seal gate")),
    )

    result = subsystem.review_and_integrate(repo, "ai-org/contrib/unsealed", base="main")

    assert result == {"accept": False, "ref": None, "reasons": ["not sealed"]}
    assert (
        _git_optional(repo, "show-ref", "--verify", "refs/heads/ai-org/subsystem").returncode
        != 0
    )


def test_migrated_subsystem_never_falls_back_after_preparation_failure(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "ai-org/contrib/preparation-failure", "main")
    candidate = evidence.SealedContribution(
        (
            evidence.ProducerEvidenceLink(
                "integrated-contribution-v1:" + "d" * 64,
                _git(
                    repo, "rev-parse", "ai-org/contrib/preparation-failure"
                ).stdout.strip(),
                "2" * 40,
            ),
        ),
        "ai-org/contrib/preparation-failure",
        "1" * 40,
        ("feature.py",),
        "9" * 40,
    )
    monkeypatch.setattr(evidence, "is_migrated_tree", lambda *_args: True)
    monkeypatch.setattr(evidence, "sealed_contribution", lambda *_args: candidate)
    monkeypatch.setattr(subsystem, "_add_read_worktree", lambda *_args: True)
    monkeypatch.setattr(
        subsystem,
        "_codex_verdict",
        lambda *_args: {"accept": True, "reasons": ["verified"]},
    )
    monkeypatch.setattr(
        subsystem, "_integrate_sealed_contribution", lambda *_args: None
    )
    monkeypatch.setattr(
        subsystem,
        "_merge_contribution",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("migrated preparation fell back to ancestry merge")
        ),
    )

    result = subsystem.review_and_integrate(
        repo, "ai-org/contrib/preparation-failure", base="main"
    )

    assert result == {
        "accept": False,
        "ref": None,
        "reasons": ["git merge failed"],
    }
    assert _git_optional(
        repo, "rev-parse", "--verify", "refs/heads/ai-org/subsystem"
    ).returncode != 0


def test_mainline_fails_closed_before_review_on_partial_current_handshake(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "ai-org/subsystem")
    (repo / "feature.py").write_text("UNLINKED = True\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", evidence.SUBSYSTEM_INTEGRATION_SUBJECT)
    _git(repo, "checkout", "main")
    monkeypatch.setattr(
        maintainer_merge.mainline,
        "_codex_verdict",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("review ran before the mainline evidence gate")
        ),
    )

    result = maintainer_merge.mainline.review_and_integrate(repo, base="main")

    assert result == {
        "accept": False,
        "ref": None,
        "reasons": ["integration exclusion policy is missing or unsupported"],
    }
    assert _git_optional(
        repo, "show-ref", "--verify", "refs/heads/ai-org/mainline"
    ).returncode != 0


def test_current_mainline_never_downgrades_an_unlinked_commit_to_legacy(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    link = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "a" * 64,
        "1" * 40,
        "2" * 40,
    )
    _git(repo, "checkout", "-b", "ai-org/mainline")
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        evidence.integration_message(evidence.MAINLINE_INTEGRATION_SUBJECT, [link]),
    )
    mainline_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-b", "ai-org/subsystem")
    (repo / "unlinked.py").write_text("UNLINKED = True\n", encoding="utf-8")
    _git(repo, "add", "unlinked.py")
    _git(repo, "commit", "-m", "subsystem: unlinked current change")
    _git(repo, "checkout", "main")
    monkeypatch.setattr(
        maintainer_merge.mainline,
        "_codex_verdict",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("review ran after the current handshake was downgraded")
        ),
    )

    result = maintainer_merge.mainline.review_and_integrate(repo, base="main")

    assert result == {
        "accept": False,
        "ref": None,
        "reasons": [
            "current subsystem history contains a commit without producer evidence"
        ],
    }
    assert _git(repo, "rev-parse", "ai-org/mainline").stdout.strip() == mainline_before


def test_mainline_revalidation_sorts_and_deduplicates_exact_links(monkeypatch):
    link_a = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "a" * 64,
        "1" * 40,
        "2" * 40,
    )
    link_b = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "b" * 64,
        "3" * 40,
        "4" * 40,
    )
    messages = {
        "commit-b": evidence.integration_message(
            evidence.SUBSYSTEM_INTEGRATION_SUBJECT, [link_b]
        ),
        "commit-a": evidence.integration_message(
            evidence.SUBSYSTEM_INTEGRATION_SUBJECT, [link_a]
        ),
        "commit-a-repeat": evidence.integration_message(
            evidence.SUBSYSTEM_INTEGRATION_SUBJECT, [link_a]
        ),
    }
    by_verdict = {link.verdict_oid: link for link in (link_a, link_b)}
    gate_routes = []
    monkeypatch.setattr(
        evidence.git_wrapper,
        "commit_message",
        lambda _repo, commit: messages[commit],
    )
    monkeypatch.setattr(
        evidence,
        "sealed_contribution_from_verdict",
        lambda _repo, verdict_oid, **kwargs: (
            gate_routes.append(kwargs.get("gate_route"))
            or evidence.SealedContribution(
                (by_verdict[verdict_oid],),
                "branch",
                "0" * 40,
                ("feature.py",),
                "9" * 40,
            )
        ),
    )

    links = evidence.verified_links(
        ".", ("commit-b", "commit-a", "commit-a-repeat"), require_current=True
    )

    assert links == (link_a, link_b)
    assert gate_routes == [
        "mainline_integration",
        "mainline_integration",
        "mainline_integration",
    ]


def test_verified_leaf_link_fails_closed_independent_of_commit_order(monkeypatch):
    exact = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "a" * 64,
        "1" * 40,
        "2" * 40,
    )
    conflicting = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "b" * 64,
        exact.verdict_oid,
        exact.implementation_oid,
    )
    messages = {
        "exact": evidence.integration_message(
            evidence.SUBSYSTEM_INTEGRATION_SUBJECT, [exact]
        ),
        "conflicting": evidence.integration_message(
            evidence.MAINLINE_INTEGRATION_SUBJECT, [conflicting]
        ),
    }
    commits = ["exact"]
    monkeypatch.setattr(evidence, "_commits_for_refs", lambda *_args: tuple(commits))
    monkeypatch.setattr(
        evidence.git_wrapper,
        "commit_message",
        lambda _repo, commit: messages[commit],
    )
    monkeypatch.setattr(
        evidence.git_wrapper,
        "head_sha",
        lambda _repo, ref: "9" * 40 if ref == evidence.AUTHORITY_NOTES_REF else None,
    )
    monkeypatch.setattr(
        evidence,
        "sealed_contribution_from_verdict",
        lambda *_args, **_kwargs: evidence.SealedContribution(
            (exact,), "branch", "0" * 40, ("feature.py",), "9" * 40
        ),
    )

    assert evidence.has_verified_link(".", exact.verdict_oid, ("main",)) is True
    for order in (["exact", "conflicting"], ["conflicting", "exact"]):
        commits[:] = order
        assert evidence.has_verified_link(".", exact.verdict_oid, ("main",)) is False

    commits[:] = ["exact"]
    authority_reads = iter(("9" * 40, "8" * 40))
    monkeypatch.setattr(
        evidence.git_wrapper,
        "head_sha",
        lambda _repo, ref: (
            next(authority_reads) if ref == evidence.AUTHORITY_NOTES_REF else None
        ),
    )
    assert evidence.has_verified_link(".", exact.verdict_oid, ("main",)) is False


def test_sealed_contribution_freezes_authority_notes_for_resolution(monkeypatch):
    verdict_oid = "1" * 40
    authority_notes_oid = "9" * 40
    observed = []
    candidate = evidence.SealedContribution(
        (
            evidence.ProducerEvidenceLink(
                "integrated-contribution-v1:" + "a" * 64,
                verdict_oid,
                "2" * 40,
            ),
        ),
        "ai-org/contrib/frozen",
        "0" * 40,
        ("feature.py",),
        authority_notes_oid,
    )
    monkeypatch.setattr(
        evidence.git_wrapper,
        "head_sha",
        lambda _repo, ref: (
            authority_notes_oid if ref == evidence.AUTHORITY_NOTES_REF else verdict_oid
        ),
    )

    def accepted(
        _repo,
        oid,
        *,
        frozen_authority_notes_oid=None,
        expected_contribution_branch=None,
    ):
        observed.append(
            (
                "accepted",
                oid,
                frozen_authority_notes_oid,
                expected_contribution_branch,
            )
        )
        return True

    def resolve(_repo, oid, **kwargs):
        observed.append(("resolved", oid, kwargs["frozen_authority_notes_oid"]))
        return candidate

    monkeypatch.setattr(
        evidence.functional_check, "sealed_verdict_accepted", accepted
    )
    monkeypatch.setattr(evidence, "sealed_contribution_from_verdict", resolve)

    assert evidence.sealed_contribution(".", "ai-org/contrib/frozen") is candidate
    assert observed == [
        (
            "accepted",
            verdict_oid,
            authority_notes_oid,
            "ai-org/contrib/frozen",
        ),
        ("resolved", verdict_oid, authority_notes_oid),
    ]


def test_sealed_contribution_rejects_a_verdict_copied_to_another_ref(monkeypatch):
    verdict_oid = "1" * 40
    authority_notes_oid = "9" * 40
    sealed = evidence.SealedContribution(
        (
            evidence.ProducerEvidenceLink(
                "integrated-contribution-v1:" + "a" * 64,
                verdict_oid,
                "3" * 40,
            ),
        ),
        "ai-org/contrib/original",
        "2" * 40,
        ("feature.py",),
        authority_notes_oid,
    )
    monkeypatch.setattr(
        evidence.git_wrapper,
        "head_sha",
        lambda _repo, ref: (
            authority_notes_oid if ref == evidence.AUTHORITY_NOTES_REF else verdict_oid
        ),
    )
    monkeypatch.setattr(
        evidence.functional_check,
        "sealed_verdict_accepted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        evidence,
        "sealed_contribution_from_verdict",
        lambda *_args, **_kwargs: sealed,
    )

    with pytest.raises(
        evidence.EvidenceError,
        match="sealed verdict belongs to a different contribution ref",
    ):
        evidence.sealed_contribution(".", "ai-org/contrib/copied")


def test_prepared_integration_publication_failure_and_cas_loss_preserve_refs(
    tmp_path,
):
    repo = _init_repo(tmp_path)
    base_oid = _git(repo, "rev-parse", "main").stdout.strip()
    tree_oid = _git(repo, "rev-parse", "main^{tree}").stdout.strip()
    prepared_oid = _git(
        repo,
        "commit-tree",
        tree_oid,
        "-p",
        base_oid,
        "-m",
        evidence.SUBSYSTEM_INTEGRATION_SUBJECT,
    ).stdout.strip()
    subsystem_ref = "refs/heads/ai-org/subsystem"

    injected = evidence.git_wrapper.publish_prepared_integration_commit(
        repo, subsystem_ref, prepared_oid, "", inject_failure=True
    )

    assert injected.ok is False
    assert injected.expected_ref_oids == ((subsystem_ref, ""),)
    assert injected.resulting_ref_oids == ()
    assert _git_optional(repo, "rev-parse", "--verify", subsystem_ref).returncode != 0

    _git(repo, "update-ref", subsystem_ref, base_oid)
    competitor_oid = _git(
        repo,
        "commit-tree",
        tree_oid,
        "-p",
        base_oid,
        "-m",
        "subsystem: concurrent winner",
    ).stdout.strip()
    _git(repo, "update-ref", subsystem_ref, competitor_oid, base_oid)

    lost = evidence.git_wrapper.publish_prepared_integration_commit(
        repo, subsystem_ref, prepared_oid, base_oid
    )

    assert lost.ok is False
    assert lost.expected_ref_oids == ((subsystem_ref, base_oid),)
    assert lost.resulting_ref_oids == ()
    assert _git(repo, "rev-parse", subsystem_ref).stdout.strip() == competitor_oid

    _git(repo, "update-ref", "refs/heads/main", competitor_oid, base_oid)
    mainline_ref = "refs/heads/ai-org/mainline"
    source_lost = evidence.git_wrapper.publish_prepared_integration_commit(
        repo,
        mainline_ref,
        prepared_oid,
        "",
        source_ref_oids={"refs/heads/main": base_oid},
    )

    assert source_lost.ok is False
    assert source_lost.expected_ref_oids == (
        (mainline_ref, ""),
        ("refs/heads/main", base_oid),
    )
    assert source_lost.resulting_ref_oids == ()
    assert _git_optional(repo, "rev-parse", "--verify", mainline_ref).returncode != 0
    assert _git(repo, "rev-parse", "refs/heads/main").stdout.strip() == competitor_oid


def test_mainline_revalidation_rejects_an_incomplete_obligation_link_cohort(
    monkeypatch,
):
    first = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "a" * 64,
        "1" * 40,
        "2" * 40,
    )
    second = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "b" * 64,
        "1" * 40,
        "2" * 40,
    )
    monkeypatch.setattr(
        evidence.git_wrapper,
        "commit_message",
        lambda *_args: evidence.integration_message(
            evidence.SUBSYSTEM_INTEGRATION_SUBJECT, [first]
        ),
    )
    monkeypatch.setattr(
        evidence,
        "sealed_contribution_from_verdict",
        lambda *_args, **_kwargs: evidence.SealedContribution(
            (first, second), "branch", "0" * 40, ("feature.py",), "9" * 40
        ),
    )

    with pytest.raises(
        evidence.EvidenceError,
        match="links changed their sealed source",
    ):
        evidence.verified_links(".", ("incomplete",), require_current=True)


def test_sealed_transition_rejects_duplicate_admission_obligation_rows(monkeypatch):
    verdict_oid = "a" * 40
    evaluated_oid = "b" * 40
    implementation_oid = "c" * 40
    binding_oid = "d" * 40
    obligation_ids = ["obligation:one", "obligation:two"]
    binding_rows = [{"obligation_id": item} for item in obligation_ids]
    assertion_rows = [{"obligation_id": item} for item in obligation_ids]
    binding_raw = b"binding"
    assertion_raw = b"assertion"
    admission_raw = b"admission"
    bodies = {
        evidence.producer_lifecycle.COMPLETION_ASSERTION_PATH: assertion_raw,
        evidence.producer_lifecycle.TASK_BINDING_PATH: binding_raw,
        evidence.functional_check.CLAIM_ADMISSION_PATH: admission_raw,
    }
    binding = {
        "series_branch": "ai-org/patch-series/demo",
        "node_path": "sub/leaf",
        "contribution_branch": "ai-org/contrib/demo",
        "canonical_root_body_sha256": "e" * 64,
        "producer": {"name": "Producer", "email": "producer@example.invalid"},
        "promise_bindings": binding_rows,
    }
    assertion = {
        "implementation_oid": implementation_oid,
        "task_binding_commit_oid": binding_oid,
        "contribution_branch": binding["contribution_branch"],
        "task_binding_body_sha256": hashlib.sha256(binding_raw).hexdigest(),
        "promise_bindings": binding_rows,
        "assertions": assertion_rows,
    }
    admission = {
        "evaluated_contribution_oid": evaluated_oid,
        "required_obligation_ids": obligation_ids,
        "promise_bindings": binding_rows,
        "task_binding_body_sha256": hashlib.sha256(binding_raw).hexdigest(),
        "assertions": [
            {
                "obligation_id": "obligation:one",
                "assertion_commit_oid": "f" * 40,
                "assertion_body_sha256": hashlib.sha256(assertion_raw).hexdigest(),
            },
            {
                "obligation_id": "obligation:one",
                "assertion_commit_oid": "f" * 40,
                "assertion_body_sha256": hashlib.sha256(assertion_raw).hexdigest(),
            },
        ],
    }

    monkeypatch.setattr(
        evidence.functional_check,
        "sealed_verdict_accepted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        evidence.git_wrapper,
        "head_sha",
        lambda _repo, ref: "9" * 40 if ref == evidence.AUTHORITY_NOTES_REF else None,
    )
    monkeypatch.setattr(
        evidence.git_wrapper,
        "show_file_bytes",
        lambda _repo, _oid, path: bodies.get(path),
    )
    monkeypatch.setattr(
        evidence.patch_series_bodies,
        "read_producer_completion_assertion",
        lambda *_args, **_kwargs: assertion,
    )
    monkeypatch.setattr(
        evidence.patch_series_bodies,
        "read_producer_task_binding",
        lambda *_args, **_kwargs: binding,
    )
    monkeypatch.setattr(
        evidence.patch_series_bodies,
        "read_claim_admission",
        lambda *_args, **_kwargs: admission,
    )
    monkeypatch.setattr(
        evidence,
        "contribution_root_generation",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        evidence.git_wrapper, "parent_commits", lambda *_args: [evaluated_oid]
    )
    monkeypatch.setattr(
        evidence.git_wrapper,
        "path_last_commit",
        lambda _repo, oid, path: (
            "f" * 40
            if oid == evaluated_oid
            and path == evidence.producer_lifecycle.COMPLETION_ASSERTION_PATH
            else binding_oid
        ),
    )
    monkeypatch.setattr(evidence.git_wrapper, "is_ancestor", lambda *_args: True)

    with pytest.raises(
        evidence.EvidenceError, match="sealed obligation evidence cohort is not exact"
    ):
        evidence.sealed_contribution_from_verdict(".", verdict_oid)


def test_incomplete_producer_cohort_never_falls_back_to_legacy_merge(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "ai-org/contrib/incomplete")
    (repo / evidence.producer_lifecycle.COMPLETION_ASSERTION_PATH).write_text(
        "incomplete producer fixture\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "acceptance: reachable")
    _git(repo, "checkout", "main")
    monkeypatch.setattr(
        subsystem,
        "_codex_verdict",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("review ran for incomplete producer cohort")
        ),
    )

    result = subsystem.review_and_integrate(
        repo, "ai-org/contrib/incomplete", base="main"
    )

    assert result == {
        "accept": False,
        "ref": None,
        "reasons": [
            "producer lifecycle cohort is incomplete: task binding is missing"
        ],
    }
    assert _git_optional(
        repo, "show-ref", "--verify", "refs/heads/ai-org/subsystem"
    ).returncode != 0


def test_parallel_families_sharing_an_item_keep_distinct_coordinates():
    shared = {
        "binding_id": "producer-task-binding:shared-item",
        "series_branch": "ai-org/patch-series/demo",
        "node_path": "sub/leaf",
        "canonical_root_body_sha256": "c" * 64,
    }
    family_a = {
        **shared,
        "contribution_branch": "ai-org/contrib/family-a",
        "producer": {"name": "Family A", "email": "A@example.invalid"},
    }
    family_b = {
        **shared,
        "contribution_branch": "ai-org/contrib/family-b",
        "producer": {"name": "Family B", "email": "b@example.invalid"},
    }

    coordinate_a = evidence.integrated_contribution_coordinate(
        family_a, "obligation:shared"
    )
    coordinate_a_normalized = evidence.integrated_contribution_coordinate(
        {**family_a, "producer": {"name": "Family A", "email": "a@example.invalid"}},
        "obligation:shared",
    )
    coordinate_b = evidence.integrated_contribution_coordinate(
        family_b, "obligation:shared"
    )

    assert coordinate_a != coordinate_b
    assert coordinate_a == coordinate_a_normalized
    assert coordinate_a.startswith("integrated-contribution-v1:")
    assert coordinate_b.startswith("integrated-contribution-v1:")


def test_integrated_coordinate_is_obligation_and_root_specific_not_item_specific():
    binding = {
        "binding_id": "producer-task-binding:first",
        "series_branch": "ai-org/patch-series/demo",
        "node_path": "sub/leaf",
        "contribution_branch": "ai-org/contrib/family-a",
        "producer": {"name": "Family A", "email": "a@example.invalid"},
        "canonical_root_body_sha256": "c" * 64,
    }

    first = evidence.integrated_contribution_coordinate(binding, "obligation:first")
    different_item = evidence.integrated_contribution_coordinate(
        {**binding, "binding_id": "producer-task-binding:second"},
        "obligation:first",
    )
    different_obligation = evidence.integrated_contribution_coordinate(
        binding, "obligation:second"
    )
    different_root = evidence.integrated_contribution_coordinate(
        {**binding, "canonical_root_body_sha256": "d" * 64},
        "obligation:first",
    )

    assert first == different_item
    assert len({first, different_obligation, different_root}) == 3


def test_sealed_contribution_retains_one_distinct_link_per_obligation():
    links = (
        evidence.ProducerEvidenceLink(
            "integrated-contribution-v1:" + "a" * 64, "1" * 40, "2" * 40
        ),
        evidence.ProducerEvidenceLink(
            "integrated-contribution-v1:" + "b" * 64, "1" * 40, "2" * 40
        ),
    )

    candidate = evidence.SealedContribution(
        tuple(reversed(links)),
        "ai-org/contrib/family-a",
        "0" * 40,
        ("feature.py",),
        "9" * 40,
    )
    message = evidence.integration_message(
        evidence.SUBSYSTEM_INTEGRATION_SUBJECT, candidate.links
    )

    assert candidate.links == links
    assert message.count(f"{evidence.EVIDENCE_LINK_TRAILER}:") == 2


def test_code_only_projection_excludes_both_ends_of_lifecycle_rename(monkeypatch):
    monkeypatch.setattr(
        evidence.git_wrapper,
        "changed_paths",
        lambda *_args: [
            {
                "status": "R",
                "paths": ["implementation-result.cue", "src/disguised.py"],
            },
            {"status": "M", "paths": ["src/feature.py"]},
        ],
    )

    assert evidence.changed_code_paths(".", "base", "implementation") == (
        "src/feature.py",
    )


def test_code_only_projection_detects_and_excludes_real_lifecycle_rename(tmp_path):
    repo = _init_repo(tmp_path)
    lifecycle_path = repo / evidence.contributor_handoff.RESULT_PATH
    lifecycle_path.write_text("result: sealed producer evidence\n", encoding="utf-8")
    _git(repo, "add", str(lifecycle_path.relative_to(repo)))
    _git(repo, "commit", "-m", "producer: record harness result")
    binding_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()

    disguised = repo / "src" / "disguised.py"
    disguised.parent.mkdir()
    lifecycle_path.rename(disguised)
    (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "implement: attempt lifecycle rename")
    implementation_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()

    changes = evidence.git_wrapper.changed_paths(
        repo, binding_oid, implementation_oid
    )

    assert any(
        change["status"].startswith("R")
        and change["paths"]
        == [evidence.contributor_handoff.RESULT_PATH, "src/disguised.py"]
        for change in changes
    )
    assert evidence.changed_code_paths(repo, binding_oid, implementation_oid) == (
        "src/feature.py",
    )


def test_integration_record_sorts_and_deduplicates_resolvable_links():
    first = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "a" * 64,
        "1" * 40,
        "2" * 40,
    )
    second = evidence.ProducerEvidenceLink(
        "integrated-contribution-v1:" + "b" * 64,
        "3" * 40,
        "4" * 40,
    )

    message = evidence.integration_message(
        evidence.SUBSYSTEM_INTEGRATION_SUBJECT,
        [second, first, second],
    )

    assert evidence._validated_integration_links(
        message, evidence.SUBSYSTEM_INTEGRATION_SUBJECT
    ) == (first, second)
    assert message.count(f"{evidence.EVIDENCE_LINK_TRAILER}:") == 2
    assert message.count(f"{evidence.COORDINATE_TRAILER}:") == 2


def test_code_only_policy_excludes_every_root_and_announcement_representation():
    lifecycle_paths = {
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.PROVENANCE_PATH,
        patch_series_bodies.ROOT_APPROACH_PATH,
        patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
        patch_series_bodies.LEGACY_COVER_PATH,
        patch_series_bodies.LEGACY_PROVENANCE_PATH,
        patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
        patch_series_bodies.LEGACY_COVERAGE_LEDGER_PATH,
        patch_series_bodies.CANONICAL_COVERAGE_LEDGER_PATH,
        announcements.ANNOUNCEMENT_RECORD_PATH,
        announcements.LEGACY_ANNOUNCEMENT_RECORD_PATH,
    }

    assert all(evidence.is_excluded_path(path) for path in lifecycle_paths)


def test_contribution_generation_uses_complete_frozen_root_identity(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    root = "ai-org/patch-series/migrated"
    git_files = {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: "approach\n",
        patch_series_bodies.SCOPE_DECOMPOSITION_PATH: "scope\n",
    }
    for path, content in git_files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "root: complete v2")
    _git(repo, "branch", root)
    frozen = _git(repo, "rev-parse", root).stdout.strip()
    canonical_digest = hashlib.sha256(b"approach\n").hexdigest()
    scope_digest = hashlib.sha256(b"scope\n").hexdigest()
    vetted_snapshot = patch_series_bodies.classify_root_generation(repo, frozen)

    _git(repo, "checkout", "-b", "ai-org/contrib/migrated")
    (repo / evidence.producer_lifecycle.TASK_BINDING_PATH).write_text(
        "binding fixture\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "producer-task-binding: fixture")
    monkeypatch.setattr(
        patch_series_bodies,
        "read_producer_task_binding",
        lambda *_args, **_kwargs: {
            "series_branch": root,
            "series_snapshot_oid": frozen,
            "canonical_root_body_sha256": canonical_digest,
            "scope_decomposition_commit_oid": frozen,
            "scope_decomposition_body_sha256": scope_digest,
        },
    )
    gate_calls = []

    def open_gate(_repo, branch, route, *, frozen_root_oid):
        gate_calls.append((branch, route, frozen_root_oid))
        return patch_series_gate.AuthorabilityDecision(
            True,
            True,
            patch_series_gate.FrozenNetworkSourceVector(
                route=route,
                branch=branch,
                frozen_root_oid=frozen_root_oid,
                root_generation=patch_series_bodies.ROOT_GENERATION_V2,
                root_sha256=canonical_digest,
                scope_decomposition_sha256=scope_digest,
                source_path=patch_series_bodies.ROOT_APPROACH_PATH,
                context=patch_series_bodies.ROOT_APPROACH_CONTEXT,
                canonical_digest=canonical_digest,
                root_snapshot=vetted_snapshot,
            ),
        )

    monkeypatch.setattr(
        patch_series_gate, "vet_producer_pair_closure", open_gate
    )
    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("integration must reuse the vetted root snapshot")
        ),
    )

    snapshot = evidence.contribution_root_generation(
        repo, "ai-org/contrib/migrated"
    )

    assert snapshot is vetted_snapshot
    assert snapshot.identity() == {
        "representation": patch_series_bodies.ROOT_GENERATION_V2,
        "source_path": patch_series_bodies.ROOT_APPROACH_PATH,
        "source_oid": frozen,
        "context": patch_series_bodies.ROOT_APPROACH_CONTEXT,
        "canonical_digest": canonical_digest,
    }
    assert gate_calls == [(root, "subsystem_integration", frozen)]


@pytest.mark.parametrize(
    "gate_route", ["subsystem_integration", "mainline_integration"]
)
def test_migrated_integration_transitions_require_the_complete_activation(
    monkeypatch, gate_route
):
    snapshot = object()
    monkeypatch.setattr(
        patch_series_gate,
        "migrated_lifecycle_activation",
        lambda _snapshot: patch_series_gate.MigratedLifecycleActivation(
            "blocked", False, False, ()
        ),
    )

    with pytest.raises(
        evidence.EvidenceError,
        match="migrated lifecycle integration gate is closed",
    ):
        evidence._require_migrated_integration_gate(snapshot, gate_route)

    monkeypatch.setattr(
        patch_series_gate,
        "migrated_lifecycle_activation",
        lambda _snapshot: patch_series_gate.MigratedLifecycleActivation(
            "producer-aware-v2",
            True,
            True,
            (
                "discovery",
                "production",
                "handoff",
                "acceptance",
                "resolution",
                "integration",
            ),
        ),
    )

    assert evidence._require_migrated_integration_gate(snapshot, gate_route) is None

    monkeypatch.setattr(
        patch_series_gate,
        "migrated_lifecycle_activation",
        lambda _snapshot: patch_series_gate.MigratedLifecycleActivation(
            "producer-aware-v2",
            True,
            True,
            ("discovery", "production", "handoff", "acceptance", "resolution"),
        ),
    )
    with pytest.raises(
        evidence.EvidenceError,
        match="migrated lifecycle integration gate is closed",
    ):
        evidence._require_migrated_integration_gate(snapshot, gate_route)


def test_migrated_leaf_resolution_uses_verified_links_instead_of_ancestry(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "ai-org/contrib/migrated")
    _git(repo, "commit", "--allow-empty", "-m", "acceptance: reachable")
    verdict_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")
    _git(repo, "branch", "ai-org/subsystem", "ai-org/contrib/migrated")
    observed: list[tuple[str, tuple[str, ...]]] = []

    def verified(_repo, oid, refs):
        observed.append((oid, tuple(refs)))
        return False

    monkeypatch.setattr(evidence, "has_verified_link", verified)

    assert patch_series_gate._contrib_merged_into_subsystem_or_mainline(
        repo,
        "ai-org/contrib/migrated",
        root_generation=patch_series_bodies.ROOT_GENERATION_V2,
    ) is False
    assert observed == [
        (verdict_oid, ("ai-org/subsystem", "ai-org/mainline", "main"))
    ]
    assert patch_series_gate._contrib_merged_into_subsystem_or_mainline(
        repo,
        "ai-org/contrib/migrated",
        root_generation=patch_series_bodies.ROOT_GENERATION_HISTORICAL,
    ) is True


def test_blocked_root_generation_prevents_mainline_promotion(tmp_path):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "ai-org/patch-series/blocked")
    for path, content in {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: "approach\n",
    }.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "root: complete v2 pending lifecycle")
    _git(repo, "checkout", "-b", "ai-org/contrib/blocked", "main")
    _git(repo, "commit", "--allow-empty", "-m", "acceptance: reachable")
    _git(repo, "checkout", "main")
    _git(repo, "branch", "ai-org/subsystem", "ai-org/contrib/blocked")
    subsystem_before = _git(repo, "rev-parse", "ai-org/subsystem").stdout.strip()

    result = maintainer_merge.pull(repo)

    assert result["status"] == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    assert result["root_generation"] == {
        "representation": patch_series_bodies.ROOT_GENERATION_V2,
        "source_path": patch_series_bodies.ROOT_APPROACH_PATH,
        "source_oid": _git(
            repo, "rev-parse", "ai-org/patch-series/blocked"
        ).stdout.strip(),
        "context": patch_series_bodies.ROOT_APPROACH_CONTEXT,
        "canonical_digest": hashlib.sha256(b"approach\n").hexdigest(),
    }
    assert _git(repo, "rev-parse", "ai-org/subsystem").stdout.strip() == subsystem_before
    assert _git_optional(
        repo, "show-ref", "--verify", "refs/heads/ai-org/mainline"
    ).returncode != 0


def _write_fake_codex(tmp_path: Path) -> None:
    path = tmp_path / "codex"
    path.write_text(
        """#!/usr/bin/env python3
import json
import pathlib
import sys
argv = sys.argv[1:]
pathlib.Path(argv[argv.index('-o') + 1]).write_text(json.dumps({'accept': True, 'reasons': ['verified']}))
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Integration Test")
    _git(repo, "config", "user.email", "integration@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _show(repo: Path, ref: str, path: str) -> str | None:
    result = _git_optional(repo, "show", f"{ref}:{path}")
    return result.stdout if result.returncode == 0 else None


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git_optional(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _git_optional(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
