from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from ai_org import migration_closure_ci, registered_body_io


ROOT = Path(__file__).resolve().parents[1]
PYTHON_CATALOG = ROOT / "ai_org" / "data" / "durable-body-catalog-v1.json"
ENGINE_CATALOG = ROOT / "cuecodec" / "cue" / "engine" / "registry" / "durable-body-catalog-v1.json"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_reconstructed_catalog_is_explicit_complete_and_single_valued():
    catalog = registered_body_io.load_catalog()
    rows = catalog.rows

    assert catalog.reconstruction == {
        "original_ground_truth": "unavailable_in_repository",
        "basis": "stdlib AST reconstruction over durable Git-body gateway calls and direct JSON boundaries",
        "scanner": "registered-body-io-stdlib-ast-v1",
        "expected_sites": 138,
        "admitted_sites": 138,
        "pending_sites": 0,
        "policy": (
            "The migration is closed: all reconstructed durable Git-body sites are assigned "
            "to an admitted, executable context in their established lifecycle cohort; pending coverage is forbidden."
        ),
    }
    assert catalog.sha256 == registered_body_io.EXPECTED_CATALOG_SHA256
    assert len(rows) == registered_body_io.EXPECTED_SITE_COUNT == 138
    assert len({row.site_id for row in rows}) == 138
    assert len({row.source_selector.key for row in rows}) == 138

    semantic = [row for row in rows if row.context_id == registered_body_io.SEMANTIC_CONTEXT_ID]
    assert {row.site_id for row in semantic} == {"semantic-status-read", "semantic-status-write"}
    assert {row.source_selector.scope for row in semantic} == {"read_semantic", "write_semantic"}
    assert {row.cohort for row in semantic} == {"semantic_status"}
    assert {row.disposition for row in semantic} == {"admitted"}
    assert all(row.executable for row in semantic)
    assert {
        (row.contract.api_version, row.contract.kind, row.contract.variant)
        for row in semantic
        if row.contract is not None
    } == {("ai-org-cue-body-v1", "SemanticStatus", "git-note")}

    assert all(row.disposition == "admitted" for row in rows)
    assert all(row.executable and row.contract is not None for row in rows)


def test_engine_bundle_and_python_adapter_bind_identical_catalog_bytes():
    assert PYTHON_CATALOG.read_bytes() == ENGINE_CATALOG.read_bytes()
    assert registered_body_io.load_catalog(PYTHON_CATALOG).sha256 == registered_body_io.load_catalog(ENGINE_CATALOG).sha256


def test_catalog_registers_one_exact_dormant_producer_lifecycle_matrix():
    rows = registered_body_io.load_catalog().rows
    actual = {
        (
            row.context_id,
            row.contract.api_version,
            row.contract.kind,
            row.contract.variant,
        )
        for row in rows
        if row.cohort == registered_body_io.PRODUCER_LIFECYCLE_COHORT
        and row.contract is not None
    }

    assert actual == set(registered_body_io.PINNED_PRODUCER_LIFECYCLE_IDENTITIES)
    assert all(
        row.disposition == "admitted" and row.executable
        for row in rows
        if row.cohort == registered_body_io.PRODUCER_LIFECYCLE_COHORT
    )


def test_catalog_rejects_partial_or_retargeted_producer_lifecycle_matrix(tmp_path):
    payload = json.loads(PYTHON_CATALOG.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        if row["context_id"] == "acceptance-authority-seal-v1":
            row["cohort"] = "foreign_lifecycle"
    invalid = tmp_path / "catalog.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        registered_body_io.CatalogError,
        match="catalog_producer_lifecycle_matrix",
    ):
        registered_body_io.load_catalog(invalid)


class _LifecycleInspectionCodec:
    def __init__(self, inspection):
        self.inspection = inspection
        self.calls = []

    def request(self, operation):
        self.calls.append(operation)
        artifact = type(
            "InspectionArtifact",
            (),
            {"data": json.dumps(self.inspection).encode("utf-8")},
        )()
        return type("InspectionResult", (), {"artifact": artifact})()


def _producer_lifecycle_inspection():
    return {
        "authority_sha256": registered_body_io.EXPECTED_AUTHORITY_SHA256,
        "catalog_sha256": registered_body_io.EXPECTED_CATALOG_SHA256,
        "manifest_sha256": registered_body_io.EXPECTED_MANIFEST_SHA256,
        "public_contract_sha256": (
            registered_body_io.EXPECTED_PUBLIC_CONTRACT_SHA256
        ),
        "contracts": [
            {
                "context_id": context_id,
                "contract": {
                    "api_version": api_version,
                    "kind": kind,
                    "variant": variant,
                },
                "manifest": registered_body_io.PRODUCER_LIFECYCLE_MANIFEST,
                "definition_path": f"schema/semantic_status.cue:#{kind}",
                "definition_sha256": "a" * 64,
                "projection_profiles": ["consumer-json-v1"],
                "schema_profiles": [],
                "historical_writers": [],
                "historical_aliases": [],
            }
            for context_id, api_version, kind, variant in (
                registered_body_io.PINNED_PRODUCER_LIFECYCLE_IDENTITIES
            )
        ],
    }


def test_dormant_producer_lifecycle_matrix_transitions_to_verified():
    codec = _LifecycleInspectionCodec(_producer_lifecycle_inspection())

    verification = (
        registered_body_io.verify_producer_lifecycle_compatibility_matrix(
            client=codec
        )
    )

    assert verification == (
        registered_body_io.ProducerLifecycleCompatibilityVerification(
            status="verified",
            manifest=registered_body_io.PRODUCER_LIFECYCLE_MANIFEST,
            authority_sha256=registered_body_io.EXPECTED_AUTHORITY_SHA256,
            catalog_sha256=registered_body_io.EXPECTED_CATALOG_SHA256,
            manifest_sha256=registered_body_io.EXPECTED_MANIFEST_SHA256,
            public_contract_sha256=(
                registered_body_io.EXPECTED_PUBLIC_CONTRACT_SHA256
            ),
            registrations=(
                registered_body_io.PINNED_PRODUCER_LIFECYCLE_IDENTITIES
            ),
            mode="dormant",
            writers_activated=False,
        )
    )
    assert verification.mode == "dormant"
    assert verification.writers_activated is False
    assert codec.calls == ["inspect"]


def test_checked_in_dormant_producer_lifecycle_matrix_transitions_to_verified():
    verification = registered_body_io.verify_producer_lifecycle_compatibility_matrix()

    assert verification.status == "verified"
    assert verification.catalog_sha256 == registered_body_io.EXPECTED_CATALOG_SHA256
    assert verification.registrations == (
        registered_body_io.PINNED_PRODUCER_LIFECYCLE_IDENTITIES
    )
    assert verification.mode == "dormant"
    assert verification.writers_activated is False


def test_dormant_producer_lifecycle_matrix_rejects_catalog_mirror_drift(tmp_path):
    catalog = tmp_path / "catalog.json"
    mirror = tmp_path / "mirror.json"
    catalog.write_bytes(PYTHON_CATALOG.read_bytes())
    mirror.write_bytes(PYTHON_CATALOG.read_bytes() + b"\n")
    codec = _LifecycleInspectionCodec(_producer_lifecycle_inspection())

    with pytest.raises(
        registered_body_io.CatalogError,
        match="producer_lifecycle_catalog_mirror",
    ):
        registered_body_io.verify_producer_lifecycle_compatibility_matrix(
            client=codec,
            catalog_path=catalog,
            mirrored_catalog_path=mirror,
        )

    assert codec.calls == []


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        (
            "contract",
            {
                "api_version": "ai-org-cue-body-v1",
                "kind": "RetargetedProducerPromise",
                "variant": "producer-commitment",
            },
            "producer_lifecycle_contract_snapshot_identity",
        ),
        (
            "historical_writers",
            ["invented-writer"],
            "producer_lifecycle_contract_snapshot_policy",
        ),
    ],
)
def test_dormant_producer_lifecycle_matrix_rejects_snapshot_drift(
    field, value, rule
):
    inspection = _producer_lifecycle_inspection()
    inspection["contracts"][0][field] = value

    with pytest.raises(registered_body_io.CatalogError, match=rule):
        registered_body_io.verify_producer_lifecycle_compatibility_matrix(
            client=_LifecycleInspectionCodec(inspection)
        )


@pytest.mark.parametrize(
    ("field", "rule"),
    [
        ("authority_sha256", "authority"),
        ("catalog_sha256", "catalog"),
        ("manifest_sha256", "manifest"),
        ("public_contract_sha256", "public_contract"),
    ],
)
def test_dormant_producer_lifecycle_matrix_rejects_codec_pin_drift(field, rule):
    inspection = _producer_lifecycle_inspection()
    inspection[field] = "f" * 64

    with pytest.raises(
        registered_body_io.CatalogError,
        match=rf"producer_lifecycle_contract_snapshot_{rule}",
    ):
        registered_body_io.verify_producer_lifecycle_compatibility_matrix(
            client=_LifecycleInspectionCodec(inspection)
        )


def test_scoped_stdlib_ast_report_has_no_catalog_gap_or_admitted_bypass():
    report = registered_body_io.durable_body_coverage_report(ROOT)

    assert report["result"] == "pass"
    assert report["registered_sites"] == report["mapped_sites"] == 138
    assert report["admitted_sites"] == 138
    assert report["registered_contexts"] == 35
    assert report["registered_body_kinds"] == 28
    assert report["pending_sites"] == 0
    for count in (
        "missing_rows",
        "ambiguous_rows",
        "orphaned_sites",
        "admitted_scope_bypasses",
        "pending_executable_rows",
        "admitted_non_executable_rows",
        "newly_admitted_contexts",
        "newly_admitted_body_kinds",
    ):
        assert report[count] == 0, report
    for details in (
        "missing_site_ids",
        "ambiguous_site_ids",
        "orphaned_source_selectors",
        "admitted_scope_bypass_selectors",
        "pending_executable_site_ids",
        "admitted_non_executable_site_ids",
        "newly_admitted_context_ids",
        "newly_admitted_body_kind_names",
    ):
        assert report[details] == [], report


def test_zero_gap_gate_closes_the_complete_catalog():
    report = registered_body_io.zero_gap_coverage_gate(ROOT)

    assert report["result"] == "pass"
    assert report["registered_sites"] == report["mapped_sites"] == report["admitted_sites"] == 138
    assert report["registered_contexts"] == 35
    assert report["registered_body_kinds"] == 28
    assert report["catalog_schema"] == registered_body_io.CATALOG_SCHEMA
    assert report["catalog_sha256"] == registered_body_io.EXPECTED_CATALOG_SHA256


def test_pending_aware_report_transitions_to_fail_closed_gate(tmp_path):
    payload = json.loads(PYTHON_CATALOG.read_text(encoding="utf-8"))
    row = next(
        row for row in payload["rows"]
        if row["context_id"] == "patchwork-check-event-stream-v1"
    )
    row.update(disposition="pending", executable=False, contract=None)
    payload["reconstruction"].update(admitted_sites=137, pending_sites=1)
    pending_catalog = tmp_path / "pending-catalog.json"
    pending_catalog.write_text(json.dumps(payload), encoding="utf-8")

    report = registered_body_io.durable_body_coverage_report(
        ROOT,
        catalog_path=pending_catalog,
    )

    assert report["result"] == "fail"
    assert report["pending_sites"] == 1
    assert report["admitted_sites"] == 137
    with pytest.raises(
        registered_body_io.CatalogError,
        match=r"catalog_zero_gap_required:.*pending_sites=1",
    ):
        registered_body_io.require_zero_gap_coverage(report)


def test_ordinary_catalog_consumers_cannot_bypass_pending_closure(tmp_path):
    payload = json.loads(PYTHON_CATALOG.read_text(encoding="utf-8"))
    row = next(
        row for row in payload["rows"]
        if row["context_id"] == "patchwork-check-event-stream-v1"
    )
    row.update(disposition="pending", executable=False, contract=None)
    payload["reconstruction"].update(admitted_sites=137, pending_sites=1)
    pending_catalog = tmp_path / "pending-catalog.json"
    pending_catalog.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(registered_body_io.CatalogError, match="catalog_pending_rows"):
        registered_body_io.load_catalog(pending_catalog)


def test_ci_admission_keeps_every_migration_closure_proof_mandatory():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "run: python -m ai_org.migration_closure_ci" in workflow
    migration_closure_ci.require_migration_closure_ci(workflow)


@pytest.mark.parametrize("proof", migration_closure_ci.REQUIRED_PROOFS)
def test_ci_closure_transition_rejects_each_missing_executable_proof(proof):
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    broken = workflow.replace(f"      - name: {proof.name}", f"      - name: disabled {proof.name}", 1)

    with pytest.raises(migration_closure_ci.CIContractError, match=proof.name):
        migration_closure_ci.require_migration_closure_ci(broken)


def test_ci_closure_transition_rejects_out_of_order_proofs():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    first_start = workflow.index("      - name: Verify zero-gap durable-body coverage")
    second_start = workflow.index("      - name: Verify frozen root codec contract")
    third_start = workflow.index("      - name: Verify frozen and sibling codec packages")
    first_block = workflow[first_start:second_start]
    second_block = workflow[second_start:third_start]
    reordered = workflow[:first_start] + second_block + first_block + workflow[third_start:]

    with pytest.raises(migration_closure_ci.CIContractError, match="proof-order"):
        migration_closure_ci.require_migration_closure_ci(reordered)


def test_ci_closure_ignores_commented_decoy_commands():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    broken = workflow.replace("        run: python -m pytest -q", "        # run: python -m pytest -q", 1)

    with pytest.raises(migration_closure_ci.CIContractError, match="Run tests:non-executable"):
        migration_closure_ci.require_migration_closure_ci(broken)


@pytest.mark.parametrize("proof", migration_closure_ci.REQUIRED_PROOFS)
def test_ci_closure_rejects_shell_decoys_containing_required_command_text(proof):
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    # Keep every required fragment present while forcing the relevant shell
    # command to report success. A substring-only authority check accepted
    # this bypass before exact run bodies became part of the contract.
    last_fragment = proof.commands[-1]
    broken = workflow.replace(last_fragment, f"{last_fragment} || true", 1)

    with pytest.raises(
        migration_closure_ci.CIContractError,
        match=rf"{proof.name}:non-canonical-command",
    ):
        migration_closure_ci.require_migration_closure_ci(broken)


def test_ci_closure_rejects_success_forcing_shell_override():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    broken = workflow.replace(
        "        run: python -m pytest -q",
        "        run: python -m pytest -q || true",
        1,
    )

    with pytest.raises(
        migration_closure_ci.CIContractError,
        match="Run tests:non-canonical-command",
    ):
        migration_closure_ci.require_migration_closure_ci(broken)


@pytest.mark.parametrize(
    ("directive", "message"),
    [
        ("if: ${{ false }}", "Run tests:conditional"),
        ("continue-on-error: true", "Run tests:continue-on-error"),
    ],
)
def test_ci_closure_rejects_non_mandatory_proof_steps(directive, message):
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    marker = "      - name: Run tests\n"
    broken = workflow.replace(marker, marker + f"        {directive}\n", 1)

    with pytest.raises(migration_closure_ci.CIContractError, match=message):
        migration_closure_ci.require_migration_closure_ci(broken)


def test_ci_closure_rejects_proofs_split_across_jobs():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    marker = "      - name: Run tests\n"
    split = workflow.replace(
        marker,
        "  detached-proof:\n    runs-on: ubuntu-latest\n    steps:\n" + marker,
        1,
    )

    with pytest.raises(migration_closure_ci.CIContractError, match="proof-job-boundary"):
        migration_closure_ci.require_migration_closure_ci(split)


def test_ci_closure_rejects_conditionally_disabled_proof_job():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    broken = workflow.replace(
        "  test:\n    name: pytest\n",
        "  test:\n    if: ${{ false }}\n    name: pytest\n",
        1,
    )

    with pytest.raises(migration_closure_ci.CIContractError, match="job-conditional"):
        migration_closure_ci.require_migration_closure_ci(broken)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pending_sites", 1),
        ("missing_rows", 1),
        ("ambiguous_rows", 1),
        ("orphaned_sites", 1),
        ("admitted_scope_bypasses", 1),
        ("pending_executable_rows", 1),
        ("admitted_non_executable_rows", 1),
        ("registered_sites", 137),
        ("mapped_sites", 137),
        ("admitted_sites", 137),
        ("registered_contexts", 34),
        ("registered_body_kinds", 27),
    ],
)
def test_zero_gap_gate_rejects_every_catalog_gap(field, value):
    report = registered_body_io.durable_body_coverage_report(ROOT)
    report[field] = value

    with pytest.raises(registered_body_io.CatalogError, match=rf"catalog_zero_gap_required:.*{field}"):
        registered_body_io.require_zero_gap_coverage(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("catalog_schema", "ai-org-durable-body-coverage-catalog-v2"),
        ("catalog_sha256", "0" * 64),
    ],
)
def test_zero_gap_gate_rejects_non_frozen_registered_kind_corpus(field, value):
    report = registered_body_io.durable_body_coverage_report(ROOT)
    report[field] = value

    with pytest.raises(registered_body_io.CatalogError, match=rf"catalog_zero_gap_required:.*{field}"):
        registered_body_io.require_zero_gap_coverage(report)


@pytest.mark.parametrize("value", [0.0, False, "0", None])
def test_zero_gap_gate_requires_exact_integer_counter_evidence(value):
    report = registered_body_io.durable_body_coverage_report(ROOT)
    report["pending_sites"] = value

    with pytest.raises(
        registered_body_io.CatalogError,
        match=r"catalog_zero_gap_required:.*pending_sites",
    ):
        registered_body_io.require_zero_gap_coverage(report)


def test_zero_gap_gate_rejects_hidden_gap_identity_behind_zero_counter():
    report = registered_body_io.durable_body_coverage_report(ROOT)
    report["admitted_scope_bypass_selectors"] = [
        {
            "path": "ai_org/example.py",
            "scope": "bypass",
            "node_kind": "call",
            "operation": "json.loads",
            "occurrence": 1,
            "line": 1,
            "column": 0,
        }
    ]

    with pytest.raises(
        registered_body_io.CatalogError,
        match=r"catalog_zero_gap_required:.*admitted_scope_bypass_selectors",
    ):
        registered_body_io.require_zero_gap_coverage(report)


@pytest.mark.parametrize(
    ("count_field", "detail_field", "detail"),
    [
        ("newly_admitted_contexts", "newly_admitted_context_ids", "new-context-v1"),
        ("newly_admitted_body_kinds", "newly_admitted_body_kind_names", "NewBodyKind"),
    ],
)
def test_zero_gap_gate_rejects_first_time_admission_in_closure(
    count_field, detail_field, detail
):
    report = registered_body_io.durable_body_coverage_report(ROOT)
    report[count_field] = 1
    report[detail_field] = [detail]

    with pytest.raises(
        registered_body_io.CatalogError,
        match=rf"catalog_zero_gap_required:.*{count_field}",
    ):
        registered_body_io.require_zero_gap_coverage(report)


def test_unknown_lookup_remains_stable_non_executable_and_contains_no_body():
    context_id = "unregistered-context-v1"
    first = registered_body_io.lookup_context(context_id)
    second = registered_body_io.lookup_context(context_id)

    assert first == second == {
        "ok": False,
        "status": "not_admitted",
        "context_id": context_id,
        "cohort": "",
        "disposition": "unknown",
        "executable": False,
        "code": "context_not_admitted",
    }
    assert not ({"body", "payload", "artifact"} & first.keys())


def test_semantic_context_lookup_selects_the_frozen_contract_triple():
    assert registered_body_io.lookup_context("semantic-status-note-v1") == {
        "ok": True,
        "status": "admitted",
        "context_id": "semantic-status-note-v1",
        "cohort": "semantic_status",
        "disposition": "admitted",
        "executable": True,
        "contract": {
            "apiVersion": "ai-org-cue-body-v1",
            "kind": "SemanticStatus",
            "variant": "git-note",
        },
    }


def test_root_technical_approach_preview_context_is_admitted_once():
    assert registered_body_io.lookup_context("root-technical-approach-tree-v1") == {
        "ok": True,
        "status": "admitted",
        "context_id": "root-technical-approach-tree-v1",
        "cohort": "patch_series_root",
        "disposition": "admitted",
        "executable": True,
        "contract": {
            "apiVersion": "ai-org-cue-body-v1",
            "kind": "TechnicalApproachTree",
            "variant": "patch-series-root",
        },
    }


def test_series_scope_decomposition_preview_context_is_admitted_once():
    assert registered_body_io.lookup_context("series-scope-decomposition-v1") == {
        "ok": True,
        "status": "admitted",
        "context_id": "series-scope-decomposition-v1",
        "cohort": "patch_series_root",
        "disposition": "admitted",
        "executable": True,
        "contract": {
            "apiVersion": "ai-org-cue-body-v1",
            "kind": "SeriesScopeDecomposition",
            "variant": "patch-series-root-preview",
        },
    }


def test_source_selector_identity_does_not_depend_on_line_numbers(tmp_path):
    reconstructed = _copy_catalog_sources(tmp_path)
    source = reconstructed / "ai_org" / "patch_author" / "announcements.py"
    source.write_text("\n\n" + source.read_text(encoding="utf-8"), encoding="utf-8")

    report = registered_body_io.registered_body_io_source_scan(reconstructed)

    assert report["result"] == "pass", report
    assert report["mapped_sites"] == 138


def test_source_scan_fails_closed_when_catalog_source_disappears(tmp_path):
    reconstructed = _copy_catalog_sources(tmp_path)
    (reconstructed / "ai_org" / "git_wrapper.py").unlink()

    report = registered_body_io.registered_body_io_source_scan(reconstructed)

    assert report["result"] == "fail"
    assert report["mapped_sites"] < 138
    assert report["missing_rows"] >= 2
    assert {"semantic-status-read", "semantic-status-write"} <= set(report["missing_site_ids"])


@pytest.mark.parametrize(
    ("scope", "operation", "statement"),
    [
        ("read_semantic", "json.loads", "json.loads('{}')"),
        ("write_semantic", "json.dumps", "json.dumps({})"),
    ],
)
def test_source_scan_detects_a_new_direct_operation_inside_admitted_scope(
    tmp_path, scope, operation, statement
):
    reconstructed = _copy_catalog_sources(tmp_path)
    source = reconstructed / "ai_org" / "git_wrapper.py"
    lines = source.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"def {scope}("):
            insertion = index
            while not lines[insertion].rstrip().endswith(":"):
                insertion += 1
            lines.insert(insertion + 1, f"    {statement}  # injected admitted-scope bypass")
            break
    else:  # pragma: no cover - the catalog test above provides the normal path
        pytest.fail(f"{scope} definition not found")
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = registered_body_io.registered_body_io_source_scan(reconstructed)

    assert report["result"] == "fail"
    assert report["admitted_scope_bypasses"] == 1
    bypass = report["admitted_scope_bypass_selectors"][0]
    assert (bypass["path"], bypass["scope"], bypass["operation"]) == (
        "ai_org/git_wrapper.py",
        scope,
        operation,
    )


def test_source_scan_rejects_new_transport_operation_inside_publication_unit(tmp_path):
    reconstructed = _copy_catalog_sources(tmp_path)
    source = reconstructed / "ai_org" / "patchwork_queue" / "patch_series_gate.py"
    lines = source.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("def _commit_network_files("):
            insertion = index
            while not lines[insertion].rstrip().endswith(":"):
                insertion += 1
            lines.insert(insertion + 1, "    json.loads('{}')  # injected transport bypass")
            break
    else:  # pragma: no cover - the catalog test above provides the normal path
        pytest.fail("_commit_network_files definition not found")
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = registered_body_io.registered_body_io_source_scan(reconstructed)

    assert report["result"] == "fail"
    assert report["orphaned_sites"] == 1
    orphan = report["orphaned_source_selectors"][0]
    assert (orphan["scope"], orphan["operation"]) == (
        "_commit_network_files",
        "json.loads",
    )


@pytest.mark.parametrize("scope", ["read_network_body", "resolve_manifest_input"])
def test_registered_network_transport_is_not_recounted_as_an_orphaned_body_site(scope):
    report = registered_body_io.registered_body_io_source_scan(ROOT)

    assert not any(
        selector["path"] == "ai_org/network_bodies.py" and selector["scope"] == scope
        for selector in report["orphaned_source_selectors"]
    )


@pytest.mark.parametrize(
    ("path", "scope", "operation", "occurrence"),
    [
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "_single_promise_family_branch",
            "git_wrapper.show_file",
            1,
        ),
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "_tree_has_promise_for_branch",
            "git_wrapper.tree_files",
            1,
        ),
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "_tree_has_promise_for_branch",
            "git_wrapper.show_file",
            1,
        ),
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "transition_slots",
            "git_wrapper.show_file",
            2,
        ),
        (
            "ai_org/patchwork_queue/patch_series_gate.py",
            "_read_json",
            "git_wrapper.file_exists",
            1,
        ),
        (
            "ai_org/patchwork_queue/patch_series_gate.py",
            "_read_json",
            "git_wrapper.file_exists",
            2,
        ),
    ],
)
def test_remaining_registered_reader_transports_are_not_catalog_sites(
    path, scope, operation, occurrence
):
    report = registered_body_io.registered_body_io_source_scan(ROOT)

    assert not any(
        (
            selector["path"],
            selector["scope"],
            selector["operation"],
            selector["occurrence"],
        )
        == (path, scope, operation, occurrence)
        for selector in report["orphaned_source_selectors"]
    )


@pytest.mark.parametrize(
    ("path", "scope", "operation", "statement", "expected_occurrence"),
    [
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "is_promise_only_contribution",
            "git_wrapper.tree_files",
            "git_wrapper.tree_files(repo, current)",
            1,
        ),
        (
            "ai_org/patch_author/functional_check.py",
            "producer_gate_rejection",
            "json.dumps",
            "json.dumps({})",
            2,
        ),
    ],
)
def test_producer_lifecycle_transport_allowlist_is_exact_and_fail_closed(
    tmp_path, path, scope, operation, statement, expected_occurrence
):
    reconstructed = _copy_catalog_sources(tmp_path)
    source = reconstructed / path
    lines = source.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"def {scope}("):
            insertion = index
            while not lines[insertion].rstrip().endswith(":"):
                insertion += 1
            lines.insert(insertion + 1, f"    {statement}  # injected second transport")
            break
    else:  # pragma: no cover - the live source supplies both named boundaries
        pytest.fail(f"{scope} definition not found")
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = registered_body_io.registered_body_io_source_scan(reconstructed)

    assert report["result"] == "fail"
    assert report["orphaned_sites"] == 1
    orphan = report["orphaned_source_selectors"][0]
    assert (orphan["path"], orphan["scope"], orphan["operation"], orphan["occurrence"]) == (
        path,
        scope,
        operation,
        expected_occurrence,
    )


def test_catalog_loader_rejects_reopening_the_closed_migration(tmp_path):
    payload = json.loads(PYTHON_CATALOG.read_text(encoding="utf-8"))
    row = payload["rows"][0]
    row.update(disposition="pending", executable=False, contract=None)
    payload["reconstruction"].update(admitted_sites=137, pending_sites=1)
    invalid = tmp_path / "catalog.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(registered_body_io.CatalogError, match="catalog_pending_rows"):
        registered_body_io.load_catalog(invalid)


def test_catalog_loader_rejects_conflicting_contracts_for_one_context(tmp_path):
    payload = json.loads(PYTHON_CATALOG.read_text(encoding="utf-8"))
    semantic = [row for row in payload["rows"] if row["context_id"] == "semantic-status-note-v1"]
    assert len(semantic) == 2
    semantic[1]["contract"]["kind"] = "ConflictingStatus"
    invalid = tmp_path / "catalog.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(registered_body_io.CatalogError, match="catalog_context_contract_ambiguous"):
        registered_body_io.load_catalog(invalid)


def _copy_catalog_sources(tmp_path: Path) -> Path:
    destination = tmp_path / "reconstructed"
    paths = {row.source_selector.path for row in registered_body_io.catalog_rows()} | {
        selector[0] for selector in registered_body_io.REPLACED_SELECTOR_ALIASES.values()
    }
    for rel_path in paths:
        source = ROOT / rel_path
        target = destination / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination
