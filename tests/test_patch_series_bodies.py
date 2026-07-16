from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import subprocess

import pytest

from ai_org import git_wrapper, patch_series_bodies
from ai_org.body_codec import (
    BodyCodecClient,
    CodecArtifact,
    CodecFailure,
    PreparedBodySet,
)
from ai_org.patchwork_queue import patch_series_gate, receive, review
from ai_org.patchwork_queue.field_registry import (
    STRING_ARRAY_FIELDS,
    STRING_FIELDS,
    empty_tech_stack,
    empty_user_experience_requirements,
)


def _git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def _repo(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "README").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "README")
    subprocess.run(
        ["git", "-C", str(tmp_path), *git_wrapper.identity_config_args(), "commit", "-m", "base"],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return tmp_path


def _cover():
    value = {field: "x" for field in STRING_FIELDS}
    value.update({field: [] for field in STRING_ARRAY_FIELDS})
    value["working_title"] = "Carrier cohort"
    value["request_type"] = "feature"
    value["tech_stack"] = {
        **empty_tech_stack(),
        "build_strategy": "engine_based",
        "engine": "AI Org engine",
        "language": "Python; Go",
        "platform": "Git",
        "rationale": "codec boundary",
        "provenance": "requester_specified",
    }
    value["user_experience_requirements"] = empty_user_experience_requirements()
    return value


def _provenance():
    return {
        "request_id": "request-1",
        "payload_sha256": "a" * 64,
        "raw_request": "ship it",
        "request_payload": {"raw_request": "ship it"},
        "memento": "off-Git intake is ingress only",
    }


def _artifact(data: bytes, media_type: str = "application/cue") -> CodecArtifact:
    return CodecArtifact(
        media_type,
        data,
        len(data),
        hashlib.sha256(data).hexdigest(),
    )


class _PreviewCodec:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def prepare(self, context, value, *, expected):
        self.calls.append(("prepare", context, expected))
        marker = str(value.get("marker", context)).encode("utf-8")
        return PreparedBodySet(context, expected, _artifact(marker))

    def project(self, context, data, *, profile, expected):
        self.calls.append(("project", context, (profile, expected)))
        return _artifact(
            b'{"context":"' + context.encode("utf-8") + b'"}',
            f"application/json; profile={profile}",
        )


class _LifecycleReaderCodec:
    def __init__(self) -> None:
        self.calls = []

    def parse(self, context, data, *, expected):
        self.calls.append((context, data, expected))
        return {"context": context, "validated": True}


@pytest.mark.parametrize(
    ("reader_name", "context_name", "contract_name"),
    [
        ("read_producer_promise", "PRODUCER_PROMISE_CONTEXT", "PRODUCER_PROMISE_CONTRACT"),
        ("read_producer_task_binding", "PRODUCER_TASK_BINDING_CONTEXT", "PRODUCER_TASK_BINDING_CONTRACT"),
        (
            "read_producer_completion_assertion",
            "PRODUCER_COMPLETION_ASSERTION_CONTEXT",
            "PRODUCER_COMPLETION_ASSERTION_CONTRACT",
        ),
        ("read_claim_admission", "CLAIM_ADMISSION_CONTEXT", "CLAIM_ADMISSION_CONTRACT"),
        (
            "read_functional_acceptance_verdict",
            "FUNCTIONAL_ACCEPTANCE_CONTEXT",
            "FUNCTIONAL_ACCEPTANCE_CONTRACT",
        ),
        (
            "read_acceptance_authority_seal",
            "ACCEPTANCE_AUTHORITY_SEAL_CONTEXT",
            "ACCEPTANCE_AUTHORITY_SEAL_CONTRACT",
        ),
    ],
)
def test_dormant_producer_lifecycle_readers_use_exact_pinned_contract(
    reader_name, context_name, contract_name
):
    codec = _LifecycleReaderCodec()
    reader = getattr(patch_series_bodies, reader_name)
    context = getattr(patch_series_bodies, context_name)
    contract = getattr(patch_series_bodies, contract_name)

    assert reader("canonical body", client=codec) == {
        "context": context,
        "validated": True,
    }
    assert codec.calls == [(context, b"canonical body", contract)]


def test_dormant_producer_lifecycle_reader_matrix_is_closed_and_immutable():
    matrix = patch_series_bodies.PRODUCER_LIFECYCLE_CONTRACT_MATRIX

    assert (
        patch_series_bodies.PRODUCER_LIFECYCLE_CONTRACT_MATRIX_MANIFEST
        == patch_series_bodies.PRODUCER_LIFECYCLE_MANIFEST
        == "producer-lifecycle-contract-matrix-v1"
    )
    assert matrix == (
        (
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        ),
        (
            patch_series_bodies.PRODUCER_TASK_BINDING_CONTEXT,
            patch_series_bodies.PRODUCER_TASK_BINDING_CONTRACT,
        ),
        (
            patch_series_bodies.PRODUCER_COMPLETION_ASSERTION_CONTEXT,
            patch_series_bodies.PRODUCER_COMPLETION_ASSERTION_CONTRACT,
        ),
        (
            patch_series_bodies.CLAIM_ADMISSION_CONTEXT,
            patch_series_bodies.CLAIM_ADMISSION_CONTRACT,
        ),
        (
            patch_series_bodies.FUNCTIONAL_ACCEPTANCE_CONTEXT,
            patch_series_bodies.FUNCTIONAL_ACCEPTANCE_CONTRACT,
        ),
        (
            patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTEXT,
            patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTRACT,
        ),
    )
    assert len({context for context, _contract in matrix}) == 6
    assert tuple(
        (context, contract.api_version, contract.kind, contract.variant)
        for context, contract in matrix
    ) == patch_series_bodies.PINNED_PRODUCER_LIFECYCLE_IDENTITIES
    with pytest.raises(TypeError):
        matrix[0] = matrix[-1]

    original = patch_series_bodies._PRODUCER_LIFECYCLE_CONTRACTS[
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT
    ]
    with pytest.raises(TypeError):
        patch_series_bodies._PRODUCER_LIFECYCLE_CONTRACTS[
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT
        ] = patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTRACT
    assert (
        patch_series_bodies._PRODUCER_LIFECYCLE_CONTRACTS[
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT
        ]
        is original
    )


def test_dormant_producer_lifecycle_reader_rejects_context_outside_matrix():
    with pytest.raises(
        ValueError, match="unregistered producer lifecycle context"
    ):
        patch_series_bodies._read_producer_lifecycle_body(
            "canonical body", "foreign-producer-body-v1", client=_LifecycleReaderCodec()
        )


class _ScopeProjectionCodec:
    def __init__(self, root, ledger) -> None:
        self.root = root
        self.ledger = ledger
        self.calls = []

    def parse(self, context, data, *, expected):
        self.calls.append(("parse", context, expected))
        return self.root if context == patch_series_bodies.ROOT_APPROACH_CONTEXT else self.ledger

    def prepare(self, context, value, *, expected):
        self.calls.append(("prepare", context, expected))
        return PreparedBodySet(context, expected, _artifact(b"scope-decomposition\n"))


class _PersistedScopeProjectionCodec(_ScopeProjectionCodec):
    def __init__(self, root, ledger, persisted) -> None:
        super().__init__(root, ledger)
        self.persisted = persisted

    def parse(self, context, data, *, expected):
        self.calls.append(("parse", context, expected))
        if context == patch_series_bodies.ROOT_APPROACH_CONTEXT:
            return self.root
        if context == patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT:
            return self.persisted
        return self.ledger


def _scope_root():
    return {
        "problem": {
            "goals": [
                {"id": "goal:preview", "requires_deliverable": True},
            ],
            "deliverable_requirements": [
                {
                    "id": "requirement:preview",
                    "referee_goal_id": "goal:preview",
                    "production_obligation_id": "obligation:preview",
                    "deliverable": "technical-approach-plan.cue",
                }
            ],
            "production_obligations": [
                {
                    "id": "obligation:preview",
                    "referee_goal_id": "goal:preview",
                    "deliverable_requirement_id": "requirement:preview",
                    "deliverable": "technical-approach-plan.cue",
                    "eligibility_predicate": "Can produce and test the canonical root.",
                }
            ],
            "patch_plan": [
                {
                    "item_id": "patch_plan:canonical-root#follow-up-03",
                    "production_obligation_ids": ["obligation:preview"],
                }
            ],
        }
    }


def _scope_ledger():
    return {
        "schema": "series-coverage-ledger-v1",
        "ledger_revision": 2,
        "supersedes_ledger_commit": "b" * 40,
        "rebaselined_from_escalation": {
            "review_round": 3,
            "child_branch": "ai-org/contrib/preview",
            "review_record_path": "patch-series-review-rounds/round-0003-direction-review-record.cue",
            "git_result_commit": "c" * 40,
        },
        "scope_items": [
            {
                "id": "outcome:preview",
                "kind": "desired_outcome",
                "text": "The final scope is visible.",
            },
            {
                "id": "goal:preview",
                "kind": "referee_goal",
                "text": "Preview the root.",
            },
            {
                "id": "ux:preview",
                "kind": "ux_acceptance_test",
                "text": "Inspect the preview.",
            },
            {
                "id": "patch_plan:follow_up:3",
                "kind": "patch_plan",
                "text": "Add the final scope decomposition.",
            },
            {
                "id": "risk:preview",
                "kind": "must_address_risk",
                "text": "Reject stale root bindings.",
            },
            {
                "id": "domain:preview",
                "kind": "domain_specification",
                "text": "Preserve historical allocation.",
            },
        ],
        "coverage": [
            {
                "scope_item_id": "outcome:preview",
                "owner": "root",
                "owner_node_path": ".",
            },
            {"scope_item_id": "goal:preview", "owner": "root", "owner_node_path": "."},
            {"scope_item_id": "ux:preview", "owner": "root", "owner_node_path": "."},
            {
                "scope_item_id": "patch_plan:follow_up:3",
                "owner": "preview",
                "owner_node_path": "sub/preview",
            },
            {"scope_item_id": "risk:preview", "owner": "root", "owner_node_path": "."},
            {"scope_item_id": "domain:preview", "owner": "root", "owner_node_path": "."},
        ],
        "parent_retained_scope_ids": ["outcome:preview"],
    }


def test_historical_writer_keeps_canonical_cohort_dormant(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/carrier"

    written = receive._write_patch_series_branch(
        repo, branch, "main", _cover(),
        extra_files={patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance()},
    )

    paths = set(git_wrapper.tree_files(repo, branch))
    assert {
        patch_series_bodies.LEGACY_COVER_PATH,
        patch_series_bodies.LEGACY_PROVENANCE_PATH,
    } <= paths
    assert patch_series_bodies.COVER_PATH not in paths
    assert patch_series_bodies.PROVENANCE_PATH not in paths
    assert patch_series_bodies.ROOT_APPROACH_PATH not in paths
    assert int(_git(repo, "rev-list", "--count", "main.." + branch)) == 1
    paired = patch_series_bodies.read_patch_series_cohort(repo, branch)
    assert paired is not None
    assert paired.publication.commit_oid == _git(repo, "rev-parse", branch)
    assert paired.publication.tree_oid == _git(repo, "rev-parse", branch + "^{tree}")
    assert [member.context for member in paired.recipe.sources] == [
        patch_series_bodies.COVER_CONTEXT,
        patch_series_bodies.PROVENANCE_CONTEXT,
    ]
    assert [member.profile for member in paired.recipe.sources] == [
        "consumer-json-v1",
        "consumer-json-v1",
    ]
    assert paired.cover_letter == _cover()
    assert paired.request_provenance == _provenance()
    assert patch_series_bodies.read_cover_letter(repo, branch) == _cover()
    assert written["branch"] == branch
    assert paired.recipe.target_context == patch_series_bodies.COVER_CONTEXT
    assert paired.recipe.target_contract == patch_series_bodies.COVER_CONTRACT
    assert paired.recipe.schema_profile == patch_series_bodies.SCHEMA_PROFILE
    cover_raw = git_wrapper.show_file(
        repo, branch, patch_series_bodies.LEGACY_COVER_PATH
    ).encode()
    provenance_raw = git_wrapper.show_file(
        repo, branch, patch_series_bodies.LEGACY_PROVENANCE_PATH
    ).encode()
    codec = BodyCodecClient()
    for member, raw in zip(paired.recipe.sources, (cover_raw, provenance_raw), strict=True):
        projected = codec.project(
            member.context,
            raw,
            profile=member.profile,
            expected=member.contract,
        )
        assert projected.media_type.endswith("profile=" + member.profile)
    exact_schema = codec.schema(
        paired.recipe.target_context,
        profile=paired.recipe.schema_profile,
        expected=paired.recipe.target_contract,
    )
    assert paired.recipe.schema_sha256 == exact_schema.sha256
    assert paired.exact_schema.data == exact_schema.data
    assert paired.exact_schema.sha256 == paired.recipe.schema_sha256
    assert written["commit"] == _git(repo, "rev-parse", branch)


def test_root_technical_approach_preview_prepares_exact_dormant_v2_members():
    codec = _PreviewCodec()

    preview = patch_series_bodies.prepare_root_technical_approach_preview(
        {"marker": "cover-cue"},
        {"marker": "provenance-cue"},
        {"marker": "approach-cue", "problem": {}, "cross_links": {}},
        client=codec,
    )

    assert preview.lifecycle_status == "preview_only"
    assert preview.authorable is False
    assert preview.canonical_cue == b"approach-cue"
    assert preview.body_sha256 == hashlib.sha256(b"approach-cue").hexdigest()
    assert preview.recipe.cohort_manifest == "patch-series-root-cohort-v2"
    assert [member.context for member in preview.recipe.members] == [
        patch_series_bodies.COVER_CONTEXT,
        patch_series_bodies.PROVENANCE_CONTEXT,
        "root-technical-approach-tree-v1",
    ]
    assert [member.contract for member in preview.recipe.members] == [
        patch_series_bodies.COVER_CONTRACT,
        patch_series_bodies.PROVENANCE_CONTRACT,
        patch_series_bodies.ROOT_APPROACH_CONTRACT,
    ]
    assert [member.blob_sha256 for member in preview.recipe.members] == [
        preview.cover_letter.canonical.sha256,
        preview.request_provenance.canonical.sha256,
        preview.body_sha256,
    ]
    assert set(preview.files()) == {
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.PROVENANCE_PATH,
        "technical-approach-plan.cue",
    }
    with pytest.raises(ValueError, match="context and contract triple"):
        replace(preview.recipe, members=tuple(reversed(preview.recipe.members)))
    with pytest.raises(ValueError, match="manifest"):
        replace(preview.recipe, cohort_manifest="patch-series-root-cohort-v1")
    assert codec.calls == [
        (
            "prepare",
            patch_series_bodies.COVER_CONTEXT,
            patch_series_bodies.COVER_CONTRACT,
        ),
        (
            "prepare",
            patch_series_bodies.PROVENANCE_CONTEXT,
            patch_series_bodies.PROVENANCE_CONTRACT,
        ),
        (
            "prepare",
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            patch_series_bodies.ROOT_APPROACH_CONTRACT,
        ),
        (
            "project",
            patch_series_bodies.COVER_CONTEXT,
            (
                patch_series_bodies.PROJECTION_PROFILE,
                patch_series_bodies.COVER_CONTRACT,
            ),
        ),
        (
            "project",
            patch_series_bodies.PROVENANCE_CONTEXT,
            (
                patch_series_bodies.PROJECTION_PROFILE,
                patch_series_bodies.PROVENANCE_CONTRACT,
            ),
        ),
        (
            "project",
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            (
                patch_series_bodies.PROJECTION_PROFILE,
                patch_series_bodies.ROOT_APPROACH_CONTRACT,
            ),
        ),
    ]


def test_root_technical_approach_selector_propagates_structured_codec_failure():
    failure = CodecFailure(
        "VALIDATION",
        "emit",
        patch_series_bodies.ROOT_APPROACH_CONTEXT,
        json_pointer="/problem/goals/0/requires_deliverable",
        rule_id="required-field",
    )

    class FailingCodec(_PreviewCodec):
        def prepare(self, context, value, *, expected):
            if context == patch_series_bodies.ROOT_APPROACH_CONTEXT:
                raise failure
            return super().prepare(context, value, expected=expected)

    with pytest.raises(CodecFailure) as caught:
        patch_series_bodies.prepare_root_technical_approach_preview(
            {"marker": "cover-cue"},
            {"marker": "provenance-cue"},
            {"problem": {}, "cross_links": {}},
            client=FailingCodec(),
        )

    assert caught.value is failure
    assert caught.value.as_dict() == {
        "code": "VALIDATION",
        "operation": "emit",
        "context_id": "root-technical-approach-tree-v1",
        "cue_path": "",
        "json_pointer": "/problem/goals/0/requires_deliverable",
        "rule_id": "required-field",
    }


def test_scope_decomposition_projects_one_frozen_root_without_git_mutation(tmp_path):
    repo = _repo(tmp_path)
    for path, content in {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: "canonical-root\n",
        patch_series_bodies.LEGACY_COVERAGE_LEDGER_PATH: json.dumps(_scope_ledger()),
    }.items():
        (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "frozen canonical root",
    )
    before = (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    )
    codec = _ScopeProjectionCodec(_scope_root(), _scope_ledger())

    preview = patch_series_bodies.project_series_scope_decomposition(
        repo, "main", client=codec
    )

    after = (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    )
    assert after == before
    assert preview.lifecycle_status == "preview_only"
    assert preview.authorable is False
    assert preview.frozen_oid == before[0]
    assert preview.canonical_root_sha256 == hashlib.sha256(
        b"canonical-root\n"
    ).hexdigest()
    assert list(preview.files()) == [
        patch_series_bodies.SCOPE_DECOMPOSITION_PATH
    ]
    assert [item["kind"] for item in preview.body["scope_items"]] == [
        "desired_outcome",
        "referee_goal",
        "ux_acceptance_test",
        "patch_plan",
        "must_address_risk",
        "domain_specification",
        "production_obligation",
    ]
    obligation = preview.body["scope_items"][-1]
    assert obligation["production_obligation_id"] == "obligation:preview"
    assert obligation["referee_goal_id"] == "goal:preview"
    assert obligation["deliverable_requirement_id"] == "requirement:preview"
    assert obligation["patch_plan_item_id"].endswith("#follow-up-03")
    assert preview.body["ownership"][-1] == {
        "scope_item_id": "obligation:preview",
        "owner": "preview",
        "owner_node_path": "sub/preview",
    }
    assert preview.body["ledger_revision"] == 2
    assert preview.body["supersedes_ledger_commit"] == "b" * 40
    assert preview.body["rebaselined_from_escalation"] == {
        "review_round": 3,
        "child_branch": "ai-org/contrib/preview",
        "git_result_commit": "c" * 40,
    }
    assert codec.calls == [
        (
            "parse",
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            patch_series_bodies.ROOT_APPROACH_CONTRACT,
        ),
        (
            "parse",
            "series-coverage-ledger-v1",
            patch_series_bodies.COVERAGE_LEDGER_CONTRACT,
        ),
        (
            "prepare",
            patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
            patch_series_bodies.SCOPE_DECOMPOSITION_CONTRACT,
        ),
    ]


def test_scope_decomposition_replays_reachable_byte_identical_persisted_source(
    tmp_path,
):
    repo = _repo(tmp_path)
    root_raw = b"canonical-root\n"
    root = _scope_root()
    ledger = _scope_ledger()
    for path, content in {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: root_raw.decode(),
        patch_series_bodies.LEGACY_COVERAGE_LEDGER_PATH: json.dumps(ledger),
    }.items():
        (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "canonical scope source",
    )
    source_oid = _git(repo, "rev-parse", "HEAD")
    persisted = patch_series_bodies._series_scope_decomposition_body(
        source_oid,
        hashlib.sha256(root_raw).hexdigest(),
        root,
        ledger,
    )
    (repo / patch_series_bodies.SCOPE_DECOMPOSITION_PATH).write_text(
        "scope-decomposition\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "persist canonical scope",
    )
    snapshot_oid = _git(repo, "rev-parse", "HEAD")
    codec = _PersistedScopeProjectionCodec(root, ledger, persisted)

    preview = patch_series_bodies.project_series_scope_decomposition(
        repo, "main", client=codec
    )

    assert preview.frozen_oid == snapshot_oid
    assert preview.body["frozen_root_oid"] == source_oid
    assert preview.body == persisted
    assert _git(repo, "status", "--porcelain") == ""


def test_scope_decomposition_rejects_unreachable_persisted_source_without_mutation(
    tmp_path,
):
    repo = _repo(tmp_path)
    for path, content in {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: "canonical-root\n",
        patch_series_bodies.SCOPE_DECOMPOSITION_PATH: "persisted-scope\n",
        patch_series_bodies.LEGACY_COVERAGE_LEDGER_PATH: json.dumps(
            _scope_ledger()
        ),
    }.items():
        (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "persisted scope with unreachable source",
    )
    before = (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    )
    codec = _PersistedScopeProjectionCodec(
        _scope_root(),
        _scope_ledger(),
        {"frozen_root_oid": "f" * 40},
    )

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies.project_series_scope_decomposition(
            repo, "main", client=codec
        )

    assert caught.value.rule_id == "frozen-root-lineage"
    assert caught.value.coordinate.endswith("/frozen_root_oid")
    assert (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    ) == before
    assert all(call[0] != "prepare" for call in codec.calls)


def test_scope_decomposition_rejects_changed_root_bytes_at_persisted_source(
    tmp_path,
):
    repo = _repo(tmp_path)
    for path, content in {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: "predecessor-root\n",
        patch_series_bodies.LEGACY_COVERAGE_LEDGER_PATH: json.dumps(
            _scope_ledger()
        ),
    }.items():
        (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "canonical source",
    )
    source_oid = _git(repo, "rev-parse", "HEAD")
    (repo / patch_series_bodies.ROOT_APPROACH_PATH).write_text(
        "changed-root\n", encoding="utf-8"
    )
    (repo / patch_series_bodies.SCOPE_DECOMPOSITION_PATH).write_text(
        "persisted-scope\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "scope detached from source bytes",
    )
    before = _git(repo, "status", "--porcelain")
    codec = _PersistedScopeProjectionCodec(
        _scope_root(),
        _scope_ledger(),
        {"frozen_root_oid": source_oid},
    )

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies.project_series_scope_decomposition(
            repo, "main", client=codec
        )

    assert caught.value.rule_id == "canonical-root-source-bytes"
    assert caught.value.coordinate.endswith("/canonical_root_sha256")
    assert _git(repo, "status", "--porcelain") == before
    assert all(call[0] != "prepare" for call in codec.calls)


def test_scope_decomposition_reads_canonical_historical_ledger_without_rewrite(
    tmp_path,
):
    repo = _repo(tmp_path)
    ledger = _scope_ledger()
    for path, content in {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: "canonical-root\n",
        patch_series_bodies.CANONICAL_COVERAGE_LEDGER_PATH: "canonical-ledger\n",
    }.items():
        (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "canonical historical ledger input",
    )
    before = (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    )
    ledger_before = json.loads(json.dumps(ledger))
    codec = _ScopeProjectionCodec(_scope_root(), ledger)

    preview = patch_series_bodies.project_series_scope_decomposition(
        repo, "main", client=codec
    )

    assert ledger == ledger_before
    assert preview.body["scope_items"][:-1] == ledger_before["scope_items"]
    assert preview.body["ownership"][:-1] == ledger_before["coverage"]
    assert (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    ) == before


def test_scope_decomposition_interprets_legacy_escalation_path_without_rewrite():
    ledger = _scope_ledger()
    ledger["rebaselined_from_escalation"]["review_record_path"] = (
        "patch-series-review-rounds/round-0003-direction-review-record.cue"
    )
    ledger_before = json.loads(json.dumps(ledger))

    body = patch_series_bodies._series_scope_decomposition_body(
        "a" * 40,
        "d" * 64,
        _scope_root(),
        ledger,
    )

    assert tuple(body["rebaselined_from_escalation"]) == (
        patch_series_bodies.ESCALATION_CONSUMPTION_FIELDS
    )
    assert body["rebaselined_from_escalation"] == {
        "review_round": 3,
        "child_branch": "ai-org/contrib/preview",
        "git_result_commit": "c" * 40,
    }
    assert ledger == ledger_before


def test_scope_decomposition_rejects_ambiguous_ledger_sources_without_mutation(
    tmp_path,
):
    repo = _repo(tmp_path)
    for path, content in {
        patch_series_bodies.COVER_PATH: "cover\n",
        patch_series_bodies.PROVENANCE_PATH: "provenance\n",
        patch_series_bodies.ROOT_APPROACH_PATH: "canonical-root\n",
        patch_series_bodies.CANONICAL_COVERAGE_LEDGER_PATH: "canonical-ledger\n",
        patch_series_bodies.LEGACY_COVERAGE_LEDGER_PATH: "{}\n",
    }.items():
        (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        *git_wrapper.identity_config_args(),
        "commit",
        "-m",
        "ambiguous historical ledger inputs",
    )
    before = (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    )
    codec = _ScopeProjectionCodec(_scope_root(), _scope_ledger())

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies.project_series_scope_decomposition(
            repo, "main", client=codec
        )

    assert caught.value.rule_id == "ambiguous-ledger"
    assert codec.calls == []
    assert (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-tree", "-r", "HEAD"),
        _git(repo, "status", "--porcelain"),
    ) == before


@pytest.mark.parametrize(
    ("frozen_oid", "root_sha256", "rule_id"),
    [
        ("not-a-frozen-oid", "d" * 64, "frozen-root-identity"),
        ("a" * 40, "not-a-root-digest", "canonical-root-digest"),
    ],
)
def test_scope_decomposition_transition_requires_exact_source_identity(
    frozen_oid, root_sha256, rule_id
):
    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies._series_scope_decomposition_body(
            frozen_oid,
            root_sha256,
            _scope_root(),
            _scope_ledger(),
        )

    assert caught.value.rule_id == rule_id


def test_scope_decomposition_transition_preserves_initial_revision_lineage():
    ledger = _scope_ledger()
    ledger.update(ledger_revision=1, supersedes_ledger_commit="")
    ledger_before = json.loads(json.dumps(ledger))

    body = patch_series_bodies._series_scope_decomposition_body(
        "a" * 40,
        "d" * 64,
        _scope_root(),
        ledger,
    )

    assert body["ledger_revision"] == 1
    assert body["supersedes_ledger_commit"] == ""
    assert ledger == ledger_before


def test_scope_decomposition_preparation_projects_closed_initial_transition():
    root = _scope_root()
    ledger = _scope_ledger()
    ledger.update(
        ledger_revision=1,
        supersedes_ledger_commit="",
        rebaselined_from_escalation={},
    )
    root_before = json.loads(json.dumps(root))
    ledger_before = json.loads(json.dumps(ledger))
    root_raw = b"frozen canonical root\n"
    codec = _ScopeProjectionCodec(root, ledger)

    body, prepared = patch_series_bodies.prepare_series_scope_decomposition(
        "a" * 40,
        root_raw,
        root,
        ledger,
        client=codec,
    )

    assert prepared.canonical.data == b"scope-decomposition\n"
    assert body["frozen_root_oid"] == "a" * 40
    assert body["canonical_root_sha256"] == hashlib.sha256(root_raw).hexdigest()
    assert body["ledger_revision"] == 1
    assert body["supersedes_ledger_commit"] == ""
    assert body["rebaselined_from_escalation"] == {}
    assert body["scope_items"][:-1] == ledger_before["scope_items"]
    assert body["ownership"][:-1] == ledger_before["coverage"]
    assert body["scope_items"][-1]["kind"] == "production_obligation"
    assert body["ownership"][-1] == {
        "scope_item_id": body["scope_items"][-1]["id"],
        "owner": "preview",
        "owner_node_path": "sub/preview",
    }
    assert root == root_before
    assert ledger == ledger_before
    assert codec.calls == [
        (
            "prepare",
            patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
            patch_series_bodies.SCOPE_DECOMPOSITION_CONTRACT,
        )
    ]


def test_scope_decomposition_transition_projects_each_obligation_once_without_rewrite():
    root = _scope_root()
    problem = root["problem"]
    problem["goals"].extend(
        [
            {"id": "goal:artifact", "requires_deliverable": True},
            {"id": "goal:wording", "requires_deliverable": False},
        ]
    )
    problem["deliverable_requirements"].append(
        {
            "id": "requirement:artifact",
            "referee_goal_id": "goal:artifact",
            "production_obligation_id": "obligation:artifact",
            "deliverable": "series-scope-decomposition.cue",
        }
    )
    problem["production_obligations"].append(
        {
            "id": "obligation:artifact",
            "referee_goal_id": "goal:artifact",
            "deliverable_requirement_id": "requirement:artifact",
            "deliverable": "series-scope-decomposition.cue",
            "eligibility_predicate": "Can produce and vet the scope body.",
        }
    )
    problem["patch_plan"][0]["production_obligation_ids"].append(
        "obligation:artifact"
    )
    ledger = _scope_ledger()
    ledger["scope_items"].append(
        {
            "id": "goal:artifact",
            "kind": "referee_goal",
            "text": "Verify the emitted scope artifact.",
        }
    )
    ledger["coverage"].append(
        {
            "scope_item_id": "goal:artifact",
            "owner": "root",
            "owner_node_path": ".",
        }
    )
    root_before = json.loads(json.dumps(root))
    ledger_before = json.loads(json.dumps(ledger))

    body = patch_series_bodies._series_scope_decomposition_body(
        "a" * 40,
        "d" * 64,
        root,
        ledger,
    )

    obligation_rows = [
        item for item in body["scope_items"]
        if item["kind"] == "production_obligation"
    ]
    assert [item["id"] for item in obligation_rows] == [
        "obligation:preview",
        "obligation:artifact",
    ]
    assert "goal:wording" not in {
        item["referee_goal_id"] for item in obligation_rows
    }
    for obligation in obligation_rows:
        assert [
            owner for owner in body["ownership"]
            if owner["scope_item_id"] == obligation["id"]
        ] == [
            {
                "scope_item_id": obligation["id"],
                "owner": "preview",
                "owner_node_path": "sub/preview",
            }
        ]
    assert root == root_before
    assert ledger == ledger_before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "invented-owner"),
        ("owner_node_path", "sub/invented"),
        ("patch_plan_item_id", "patch_plan:invented#follow-up-99"),
    ],
)
def test_scope_decomposition_rejects_obligation_side_assignment_authority(
    field, value
):
    root = _scope_root()
    root["problem"]["production_obligations"][0][field] = value

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies._series_scope_decomposition_body(
            "a" * 40,
            "d" * 64,
            root,
            _scope_ledger(),
        )

    assert caught.value.rule_id == "obligation-shape"
    assert caught.value.coordinate == "root/problem/production_obligations/0"


@pytest.mark.parametrize(
    ("coordinate", "mutation", "rule_id"),
    [
        (
            "root/problem/deliverable_requirements/0",
            lambda root: root["problem"]["deliverable_requirements"][0].update(
                owner="invented-owner"
            ),
            "requirement-shape",
        ),
        (
            "root/problem/patch_plan/0",
            lambda root: root["problem"]["patch_plan"][0].update(
                owner_node_path="sub/invented"
            ),
            "patch-plan-assignment-shape",
        ),
    ],
)
def test_scope_decomposition_rejects_competing_root_assignment_authority(
    coordinate, mutation, rule_id
):
    root = _scope_root()
    mutation(root)

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies._series_scope_decomposition_body(
            "a" * 40,
            "d" * 64,
            root,
            _scope_ledger(),
        )

    assert caught.value.rule_id == rule_id
    assert caught.value.coordinate == coordinate


def test_scope_decomposition_requires_historical_referee_goal_continuity():
    ledger = _scope_ledger()
    ledger["scope_items"][1]["kind"] = "desired_outcome"

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies._series_scope_decomposition_body(
            "a" * 40,
            "d" * 64,
            _scope_root(),
            ledger,
        )

    assert caught.value.rule_id == "obligation-referee-association"
    assert caught.value.coordinate == "root/problem/goals/0"


def test_scope_decomposition_preserves_historical_goal_spelling_without_rewrite():
    ledger = _scope_ledger()
    historical_goal = ledger["scope_items"][1]
    historical_goal["kind"] = "goal"
    ledger_before = json.loads(json.dumps(ledger))

    body = patch_series_bodies._series_scope_decomposition_body(
        "a" * 40,
        "d" * 64,
        _scope_root(),
        ledger,
    )

    assert body["scope_items"][1] == historical_goal
    assert body["scope_items"][1]["kind"] == "goal"
    assert ledger == ledger_before


def test_scope_decomposition_preserves_historical_goal_category_verbatim():
    ledger = _scope_ledger()
    historical_goal = {
        "id": "goal:historical",
        "kind": "goal",
        "text": "Preserve the pre-cutover goal category.",
    }
    ledger["scope_items"].append(historical_goal)
    ledger["coverage"].append(
        {
            "scope_item_id": historical_goal["id"],
            "owner": "root",
            "owner_node_path": ".",
        }
    )

    body = patch_series_bodies._series_scope_decomposition_body(
        "a" * 40,
        "d" * 64,
        _scope_root(),
        ledger,
    )

    assert [
        item for item in body["scope_items"] if item["id"] == historical_goal["id"]
    ] == [historical_goal]
    assert patch_series_bodies.ESTABLISHED_SERIES_SCOPE_CATEGORIES == (
        "desired_outcome",
        "referee_goal",
        "goal",
        "ux_acceptance_test",
        "patch_plan",
        "must_address_risk",
        "domain_specification",
    )


@pytest.mark.parametrize(
    ("mutation", "rule_id"),
    [
        (
            lambda ledger: ledger["scope_items"][0].update(alias="rewritten"),
            "established-scope-shape",
        ),
        (
            lambda ledger: ledger["coverage"][0].update(weight=1),
            "ownership-shape",
        ),
    ],
)
def test_scope_decomposition_rejects_surplus_historical_row_fields(
    mutation, rule_id
):
    ledger = _scope_ledger()
    mutation(ledger)

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies._series_scope_decomposition_body(
            "a" * 40,
            "d" * 64,
            _scope_root(),
            ledger,
        )

    assert caught.value.rule_id == rule_id


@pytest.mark.parametrize(
    ("mutation", "rule_id"),
    [
        (
            lambda ledger: ledger["coverage"].append(dict(ledger["coverage"][0])),
            "duplicate-ownership",
        ),
        (
            lambda ledger: ledger["rebaselined_from_escalation"].pop(
                "git_result_commit"
            ),
            "escalation-consumption",
        ),
        (
            lambda ledger: ledger["rebaselined_from_escalation"].update(
                unexpected_evidence="surplus"
            ),
            "escalation-consumption",
        ),
        (
            lambda ledger: ledger.update(supersedes_ledger_commit=""),
            "ledger-lineage",
        ),
        (
            lambda ledger: ledger.update(ledger_revision=1),
            "ledger-lineage",
        ),
        (
            lambda ledger: ledger["parent_retained_scope_ids"].append(
                ledger["parent_retained_scope_ids"][0]
            ),
            "duplicate-parent-retained-scope",
        ),
    ],
)
def test_scope_decomposition_rejects_inexact_transition_state(mutation, rule_id):
    ledger = _scope_ledger()
    mutation(ledger)

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies._series_scope_decomposition_body(
            "a" * 40,
            "d" * 64,
            _scope_root(),
            ledger,
        )

    assert caught.value.rule_id == rule_id


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.update(ledger_revision=1),
        lambda body: body.update(supersedes_ledger_commit=""),
    ],
)
def test_scope_decomposition_registered_contract_rejects_invalid_revision_lineage(
    mutation,
):
    body = patch_series_bodies._series_scope_decomposition_body(
        "a" * 40,
        "d" * 64,
        _scope_root(),
        _scope_ledger(),
    )
    mutation(body)

    with pytest.raises(CodecFailure) as caught:
        BodyCodecClient().prepare(
            patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
            body,
            expected=patch_series_bodies.SCOPE_DECOMPOSITION_CONTRACT,
        )

    assert caught.value.code == "SCHEMA_VALIDATION"
    assert caught.value.context_id == patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT


@pytest.mark.parametrize(
    "mutation",
    [
        lambda root: root["problem"]["production_obligations"][0].update(
            referee_goal_id="goal:fabricated"
        ),
        lambda root: root["problem"]["deliverable_requirements"][0].update(
            production_obligation_id="obligation:fabricated"
        ),
    ],
)
def test_scope_decomposition_rejects_retargeted_obligation_association(mutation):
    root = _scope_root()
    mutation(root)

    with pytest.raises(
        patch_series_bodies.SeriesScopeDecompositionError
    ) as caught:
        patch_series_bodies._series_scope_decomposition_body(
            "a" * 40,
            "d" * 64,
            root,
            _scope_ledger(),
        )

    assert caught.value.rule_id == "obligation-referee-association"


def test_reject_preflight_leaves_branch_and_worktree_untouched(tmp_path):
    repo = _repo(tmp_path)
    invalid = _cover()
    del invalid["working_title"]

    with pytest.raises(ValueError, match="missing working_title"):
        receive._write_patch_series_branch(
            repo, "ai-org/patch-series/rejected", "main", invalid,
            extra_files={patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance()},
        )

    assert not git_wrapper.branch_exists(repo, "ai-org/patch-series/rejected")
    assert _git(repo, "status", "--porcelain") == ""


def test_root_cohort_preflight_rejects_invalid_member_before_git_mutation(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/rejected-root-cohort"

    with pytest.raises(Exception):
        receive._write_patch_series_branch(
            repo,
            branch,
            "main",
            _cover(),
            patch_series_path=patch_series_bodies.COVER_PATH,
            extra_files={
                patch_series_bodies.PROVENANCE_PATH: _provenance(),
                patch_series_bodies.ROOT_APPROACH_PATH: "not canonical CUE\n",
            },
        )

    assert not git_wrapper.branch_exists(repo, branch)
    assert _git(repo, "status", "--porcelain") == ""


def test_explicit_canonical_coordinates_publish_only_the_complete_pair(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/explicit-canonical"

    receive._write_patch_series_branch(
        repo,
        branch,
        "main",
        _cover(),
        patch_series_path=patch_series_bodies.COVER_PATH,
        extra_files={patch_series_bodies.PROVENANCE_PATH: _provenance()},
    )

    paths = set(git_wrapper.tree_files(repo, branch))
    assert {patch_series_bodies.COVER_PATH, patch_series_bodies.PROVENANCE_PATH} <= paths
    assert patch_series_bodies.LEGACY_COVER_PATH not in paths
    assert patch_series_bodies.LEGACY_PROVENANCE_PATH not in paths
    assert patch_series_bodies.read_patch_series_cohort(repo, branch) is not None


def test_explicit_canonical_cover_without_provenance_rejects_before_mutation(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/partial-canonical"

    with pytest.raises(ValueError, match="canonical patch-series cover requires request provenance"):
        receive._write_patch_series_branch(
            repo,
            branch,
            "main",
            _cover(),
            patch_series_path=patch_series_bodies.COVER_PATH,
        )

    assert not git_wrapper.branch_exists(repo, branch)
    assert _git(repo, "status", "--porcelain") == ""


def test_promotion_rejects_duplicate_provenance_representations_before_mutation(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/duplicate-provenance"

    with pytest.raises(ValueError, match="provenance must have exactly one representation"):
        receive._write_patch_series_branch(
            repo,
            branch,
            "main",
            _cover(),
            extra_files={
                patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance(),
                patch_series_bodies.PROVENANCE_PATH: _provenance(),
            },
        )

    assert not git_wrapper.branch_exists(repo, branch)
    assert _git(repo, "status", "--porcelain") == ""


def test_incomplete_canonical_cohort_is_rejected_by_both_member_readers(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/incomplete"
    canonical = patch_series_bodies.prepare_request_provenance(_provenance()).canonical.data.decode()
    git_wrapper.create_branch_with_files(
        repo, branch, "main", {patch_series_bodies.PROVENANCE_PATH: canonical},
        commit_message="incomplete cohort",
    )

    with pytest.raises(ValueError, match="cohort is incomplete"):
        patch_series_bodies.read_cover_letter(repo, branch)
    with pytest.raises(ValueError, match="cohort is incomplete"):
        patch_series_bodies.read_request_provenance(repo, branch)


def test_historical_cohort_import_is_read_only(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/historical"
    git_wrapper.create_branch_with_files(
        repo, branch, "main",
        {
            patch_series_bodies.LEGACY_COVER_PATH: _cover(),
            patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance(),
        },
        commit_message="historical cohort",
    )
    before = _git(repo, "rev-parse", branch)
    before_tree = _git(repo, "ls-tree", "-r", "--name-only", branch)

    paired = patch_series_bodies.read_patch_series_cohort(repo, branch)

    assert paired is not None
    assert paired.cover_letter["working_title"] == "Carrier cohort"
    assert paired.request_provenance["request_id"] == "request-1"
    assert _git(repo, "rev-parse", branch) == before
    assert _git(repo, "ls-tree", "-r", "--name-only", branch) == before_tree


def test_legacy_member_read_uses_one_frozen_tree_when_branch_moves(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/moving"
    original = _cover()
    original["working_title"] = "Frozen cover"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {patch_series_bodies.LEGACY_COVER_PATH: original},
        commit_message="historical cover",
    )
    frozen = _git(repo, "rev-parse", branch)

    replacement = _cover()
    replacement["working_title"] = "Moved cover"
    moved = git_wrapper.commit_files(
        repo,
        branch,
        {patch_series_bodies.LEGACY_COVER_PATH: replacement},
        subject="move branch after read starts",
    )["commit"]
    _git(repo, "update-ref", f"refs/heads/{branch}", frozen)

    real_head_sha = git_wrapper.head_sha
    resolutions = 0

    def move_after_resolution(repo_path, ref):
        nonlocal resolutions
        resolved = real_head_sha(repo_path, ref)
        resolutions += 1
        if resolutions == 1:
            _git(repo, "update-ref", f"refs/heads/{branch}", moved, frozen)
        return resolved

    monkeypatch.setattr(git_wrapper, "head_sha", move_after_resolution)

    loaded = patch_series_bodies.read_cover_letter(repo, branch)

    assert loaded is not None
    assert loaded["working_title"] == "Frozen cover"
    assert resolutions == 1
    assert _git(repo, "rev-parse", branch) == moved


def test_carrier_preserves_frozen_publication_when_branch_moves(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/moving-carrier"
    receive._write_patch_series_branch(
        repo,
        branch,
        "main",
        _cover(),
        extra_files={patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance()},
    )
    frozen = _git(repo, "rev-parse", branch)
    frozen_tree = _git(repo, "rev-parse", branch + "^{tree}")
    moved = git_wrapper.commit_files(
        repo,
        branch,
        {"after-freeze.txt": "new tree\n"},
        subject="move branch after carrier read starts",
    )["commit"]
    _git(repo, "update-ref", f"refs/heads/{branch}", frozen)

    real_head_sha = git_wrapper.head_sha

    def move_after_resolution(repo_path, ref):
        resolved = real_head_sha(repo_path, ref)
        _git(repo, "update-ref", f"refs/heads/{branch}", moved, frozen)
        return resolved

    monkeypatch.setattr(git_wrapper, "head_sha", move_after_resolution)

    paired = patch_series_bodies.read_patch_series_cohort(repo, branch)

    assert paired is not None
    assert paired.publication == patch_series_bodies.ImmutableGitTree(
        commit_oid=frozen,
        tree_oid=frozen_tree,
    )
    assert _git(repo, "rev-parse", branch) == moved


def test_non_root_representation_keeps_provenance_on_legacy_representation(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/pending"

    receive._write_patch_series_branch(
        repo, branch, "main", _cover(),
        patch_series_path="pending-cover.json",
        extra_files={patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance()},
    )

    paths = set(git_wrapper.tree_files(repo, branch))
    assert "pending-cover.json" in paths
    assert patch_series_bodies.LEGACY_PROVENANCE_PATH in paths
    assert patch_series_bodies.COVER_PATH not in paths
    assert patch_series_bodies.PROVENANCE_PATH not in paths


@pytest.mark.parametrize(
    ("patch_series_path", "extra_files", "message"),
    [
        (
            patch_series_bodies.LEGACY_COVER_PATH,
            {
                patch_series_bodies.PROVENANCE_PATH:
                    patch_series_bodies.prepare_request_provenance(_provenance()).canonical.data.decode()
            },
            "must be produced by paired cohort preflight",
        ),
        (
            patch_series_bodies.COVER_PATH,
            {},
            "requires paired provenance",
        ),
    ],
)
def test_rejects_canonical_member_ingress_before_git_mutation(
    tmp_path, patch_series_path, extra_files, message
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/unpaired-canonical-ingress"

    with pytest.raises(ValueError, match=message):
        receive._write_patch_series_branch(
            repo,
            branch,
            "main",
            _cover(),
            patch_series_path=patch_series_path,
            extra_files=extra_files,
        )

    assert not git_wrapper.branch_exists(repo, branch)
    assert _git(repo, "status", "--porcelain") == ""


def test_mixed_canonical_and_legacy_members_are_not_a_cohort(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/mixed"
    canonical_cover = patch_series_bodies.prepare_cover_letter(_cover()).canonical.data.decode()
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.COVER_PATH: canonical_cover,
            patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance(),
        },
        commit_message="mixed cohort",
    )

    with pytest.raises(ValueError, match="cohort is incomplete"):
        patch_series_bodies.read_patch_series_cohort(repo, branch)
    with pytest.raises(ValueError, match="cohort is incomplete"):
        patch_series_bodies.read_cover_letter(repo, branch)


def test_duplicate_canonical_alias_is_rejected_beside_historical_writer_output(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/duplicate"
    receive._write_patch_series_branch(
        repo,
        branch,
        "main",
        _cover(),
        extra_files={patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance()},
    )
    git_wrapper.commit_files(
        repo,
        branch,
        {patch_series_bodies.COVER_PATH: _cover()},
        subject="introduce forbidden canonical alias",
    )

    with pytest.raises(ValueError, match="exactly one representation"):
        patch_series_bodies.read_patch_series_cohort(repo, branch)


def test_cover_update_preflights_and_reemits_the_complete_pair(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/update"
    receive._write_patch_series_branch(
        repo,
        branch,
        "main",
        _cover(),
        extra_files={patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance()},
    )
    revised = {**_cover(), "working_title": "Revised carrier cohort"}

    prepared = patch_series_bodies.prepare_patch_series_update(repo, branch, revised)

    assert set(prepared.files()) == {
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.PROVENANCE_PATH,
    }
    assert prepared.cover_projection.sha256
    assert prepared.provenance_projection.sha256
    assert prepared.exact_schema.sha256 == prepared.recipe.schema_sha256


def test_grounding_carrier_rejects_mismatched_or_reordered_contract_coordinates():
    recipe = patch_series_bodies.prepare_patch_series_cohort(
        _cover(), _provenance()
    ).recipe

    with pytest.raises(ValueError, match="ordered source cohort"):
        replace(recipe, sources=tuple(reversed(recipe.sources)))

    wrong_contract = replace(
        recipe.sources[0], contract=patch_series_bodies.PROVENANCE_CONTRACT
    )
    with pytest.raises(ValueError, match="context and contract triple"):
        replace(recipe, sources=(wrong_contract, recipe.sources[1]))

    with pytest.raises(ValueError, match="target schema.*exact sha256"):
        replace(recipe, schema_sha256="0" * 63)


def test_preflight_result_rejects_member_digest_and_profile_substitution():
    prepared = patch_series_bodies.prepare_patch_series_cohort(_cover(), _provenance())

    with pytest.raises(ValueError, match="member identity does not match"):
        replace(prepared, cover_letter=prepared.request_provenance)

    forged_cover = replace(
        prepared.cover_letter,
        canonical=prepared.request_provenance.canonical,
    )
    with pytest.raises(ValueError, match="source blob digest does not match preflight"):
        replace(prepared, cover_letter=forged_cover)

    forged_projection = CodecArtifact(
        "application/json; profile=unregistered-projection",
        prepared.cover_projection.data,
        prepared.cover_projection.byte_length,
        prepared.cover_projection.sha256,
    )
    with pytest.raises(ValueError, match="source projection artifact does not match bound profile"):
        replace(prepared, cover_projection=forged_projection)

    forged_schema = CodecArtifact(
        "application/schema+json; profile=unregistered-schema",
        prepared.exact_schema.data,
        prepared.exact_schema.byte_length,
        prepared.exact_schema.sha256,
    )
    with pytest.raises(ValueError, match="target schema artifact does not match bound profile"):
        replace(prepared, exact_schema=forged_schema)


def test_root_consumers_accept_explicit_canonical_or_logical_legacy_path(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/consumer-paths"
    receive._write_patch_series_branch(
        repo,
        branch,
        "main",
        _cover(),
        extra_files={patch_series_bodies.LEGACY_PROVENANCE_PATH: _provenance()},
    )

    for path in (patch_series_bodies.COVER_PATH, patch_series_bodies.LEGACY_COVER_PATH):
        assert patch_series_bodies.is_root_cover_path(path)
        assert receive._read_json_from_branch(repo, branch, path)["working_title"] == "Carrier cohort"
        reviewed, error = review._read_patch_series_from_git(repo, branch, path)
        assert error == ""
        assert reviewed is not None and reviewed["working_title"] == "Carrier cohort"
        assert patch_series_gate._read_json(repo, branch, path)["working_title"] == "Carrier cohort"

    assert not patch_series_bodies.is_root_cover_path("sub/child/patch-series-cover-letter.json")


def test_root_member_projections_reuse_the_supplied_generation_snapshot(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/frozen-member-projection"
    cover = patch_series_bodies.prepare_cover_letter(_cover()).canonical.data.decode()
    provenance = (
        patch_series_bodies.prepare_request_provenance(_provenance())
        .canonical.data.decode()
    )
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.COVER_PATH: cover,
            patch_series_bodies.PROVENANCE_PATH: provenance,
        },
        commit_message="canonical member-only cohort",
    )
    snapshot = patch_series_bodies.classify_root_generation(repo, branch)
    assert snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_INVALID

    def unexpected_reclassification(*_args, **_kwargs):
        raise AssertionError("the frozen root snapshot must be reused")

    monkeypatch.setattr(
        patch_series_bodies,
        "_classify_root_generation_at_oid",
        unexpected_reclassification,
    )

    reviewed, error = review._read_patch_series_from_git(
        repo,
        branch,
        patch_series_bodies.LEGACY_COVER_PATH,
        root_snapshot=snapshot,
    )
    paired = patch_series_bodies.read_patch_series_cohort(
        repo, branch, root_snapshot=snapshot
    )
    is_canonical = patch_series_bodies.is_canonical_cohort(
        repo, branch, root_snapshot=snapshot
    )
    loaded_provenance = patch_series_bodies.read_request_provenance(
        repo, branch, root_snapshot=snapshot
    )

    assert error == ""
    assert reviewed is not None and reviewed["working_title"] == "Carrier cohort"
    assert paired is not None
    assert paired.cover_letter["working_title"] == "Carrier cohort"
    assert is_canonical is True
    assert loaded_provenance is not None
    assert loaded_provenance["request_id"] == "request-1"


def test_closure_cover_projection_classifies_the_frozen_root_once(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/single-classification"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {patch_series_bodies.LEGACY_COVER_PATH: _cover()},
        commit_message="historical cover",
    )
    real_classifier = patch_series_bodies._classify_root_generation_at_oid
    classified_oids: list[str] = []

    def recording_classifier(repo_path, frozen_oid):
        classified_oids.append(frozen_oid)
        return real_classifier(repo_path, frozen_oid)

    monkeypatch.setattr(
        patch_series_bodies,
        "_classify_root_generation_at_oid",
        recording_classifier,
    )

    loaded = patch_series_gate._read_json(
        repo, branch, patch_series_bodies.LEGACY_COVER_PATH
    )

    assert loaded["working_title"] == "Carrier cohort"
    assert classified_oids == [_git(repo, "rev-parse", branch)]
