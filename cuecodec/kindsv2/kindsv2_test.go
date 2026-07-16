package kindsv2

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"
	"testing/fstest"

	engineauthority "github.com/mishima-computing/cuecodec/cue/engine"
)

var semanticProjection = []byte(`{"working_state":"ready","subsystem":"notes","owner":"Ada","change_kind":"feature"}`)

const canonicalProjection = `{"change_kind":"feature","owner":"Ada","subsystem":"notes","working_state":"ready"}`

func newTestRegistry(t *testing.T) *Registry {
	t.Helper()
	registry, err := New()
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return registry
}

func requireFailure(t *testing.T, err error, code ErrorCode, rule string) *Failure {
	t.Helper()
	failure, ok := err.(*Failure)
	if !ok {
		t.Fatalf("error type = %T, want *Failure (%v)", err, err)
	}
	if failure.Code != code || failure.RuleID != rule {
		t.Fatalf("failure = %#v, want code=%s rule=%s", failure, code, rule)
	}
	return failure
}

func artifactBytes(t *testing.T, artifact Artifact) []byte {
	t.Helper()
	data, err := artifact.Bytes()
	if err != nil {
		t.Fatalf("artifact bytes: %v", err)
	}
	if len(data) != artifact.ByteLength {
		t.Fatalf("artifact length = %d, metadata = %d", len(data), artifact.ByteLength)
	}
	return data
}

func TestRegistryCoverageMigrationIsClosed(t *testing.T) {
	registry := newTestRegistry(t)
	report := registry.Coverage()
	if report != (CoverageReport{RegisteredSites: 138, MappedSites: 138, AdmittedSites: 138, PendingSites: 0}) {
		t.Fatalf("coverage = %#v", report)
	}
	if got := len(registry.Catalog()); got != 138 {
		t.Fatalf("catalog rows = %d", got)
	}
	inspection, err := registry.Inspect(context.Background())
	if err != nil {
		t.Fatalf("Inspect: %v", err)
	}
	if inspection.CatalogSHA256 != "c440e12519d6d6ea578dc1b2c6457b2b5a56acf5aa79ac6802fdc34348b14a43" {
		t.Fatalf("catalog digest = %s", inspection.CatalogSHA256)
	}
	if inspection.Coverage.MissingRows != 0 || inspection.Coverage.AmbiguousRows != 0 ||
		inspection.Coverage.OrphanedSites != 0 || inspection.Coverage.AdmittedScopeBypasses != 0 {
		t.Fatalf("coverage gaps = %#v", inspection.Coverage)
	}
	_, err = registry.Emit(context.Background(), "unregistered-context-v1", semanticProjection)
	requireFailure(t, err, CodeInvalidArgument, "context-unknown")
	if _, err := registry.Emit(context.Background(), "semantic-status-read", semanticProjection); err == nil {
		t.Fatal("catalog site ID unexpectedly acted as an executable context alias")
	} else {
		requireFailure(t, err, CodeInvalidArgument, "context-unknown")
	}
}

func TestRootTechnicalApproachPreviewContractTransitions(t *testing.T) {
	registry := newTestRegistry(t)
	body := []byte(`{"problem":{"id":"problem","problem":"prove the preview","affected":"planning","current_inadequacy":"no producer pairing","goals":[{"id":"goal:1","requires_deliverable":true,"actor":"maintainer","capability":{"action":"inspect preview","preconditions":[]},"verifiable_outcome":{"expected_state":"paired","evidence":"codec projection"},"verification":{"method":"automated_test","check":"go test ./..."},"ux_trace":"none"},{"id":"goal:2","requires_deliverable":false,"actor":"maintainer","capability":{"action":"retain scope","preconditions":[]},"verifiable_outcome":{"expected_state":"unchanged","evidence":"tree"},"verification":{"method":"manual_check","check":"inspect tree"},"ux_trace":"none"}],"non_goals":[],"constraints":{"hard":[],"soft":[]},"prior_art":[],"question":{"id":"question:approach"},"open_questions":[],"deliverable_requirements":[{"id":"deliverable-requirement:1","referee_goal_id":"goal:1","production_obligation_id":"production-obligation:1","deliverable":"technical-approach-plan.cue"}],"production_obligations":[{"id":"production-obligation:1","referee_goal_id":"goal:1","deliverable_requirement_id":"deliverable-requirement:1","deliverable":"technical-approach-plan.cue","eligibility_predicate":"can edit registry","replacement_links":[]}],"patch_plan":[{"item_id":"patch_plan:root#first_proof","production_obligation_ids":["production-obligation:1"]}]},"cross_links":[]}`)

	canonical, err := registry.Emit(context.Background(), "root-technical-approach-tree-v1", body)
	if err != nil {
		t.Fatalf("Emit root approach: %v", err)
	}
	canonicalBytes := artifactBytes(t, canonical)
	if err := registry.Vet(context.Background(), "root-technical-approach-tree-v1", canonicalBytes); err != nil {
		t.Fatalf("Vet root approach: %v", err)
	}
	projected, err := registry.Parse(context.Background(), "root-technical-approach-tree-v1", canonicalBytes)
	if err != nil {
		t.Fatalf("Parse root approach: %v", err)
	}
	var got, want any
	if err := json.Unmarshal(artifactBytes(t, projected), &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(body, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("root projection changed: got %#v want %#v", got, want)
	}

	inspection, err := registry.Inspect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	var contract *ContractInfo
	for i := range inspection.Contracts {
		if inspection.Contracts[i].ContextID == "root-technical-approach-tree-v1" {
			contract = &inspection.Contracts[i]
			break
		}
	}
	if contract == nil {
		t.Fatal("root Technical Approach contract not inspected")
	}
	if !reflect.DeepEqual(contract.ProjectionProfiles, []string{ConsumerJSONProfileV1}) ||
		!reflect.DeepEqual(contract.SchemaProfiles, []string{}) ||
		contract.Manifest != "patch-series-root-cohort-v2" ||
		contract.DefinitionPath != schemaAuthorityPath+":#RootTechnicalApproachTree" {
		t.Fatalf("root contract = %#v", contract)
	}
	encodedInspection, err := json.Marshal(inspection)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(encodedInspection, []byte(`"context_id":"root-technical-approach-tree-v1"`)) ||
		!bytes.Contains(encodedInspection, []byte(`"schema_profiles":[]`)) {
		t.Fatalf("root contract profile advertisement = %s", encodedInspection)
	}

	missingDetermination := bytes.Replace(body, []byte(`"requires_deliverable":true,`), nil, 1)
	_, err = registry.Emit(context.Background(), "root-technical-approach-tree-v1", missingDetermination)
	requireFailure(t, err, CodeIncomplete, "recursive-concreteness")

	requireRelationalRejection := func(name string, invalid []byte) {
		t.Helper()
		t.Run(name, func(t *testing.T) {
			_, err := registry.Emit(context.Background(), "root-technical-approach-tree-v1", invalid)
			requireFailure(t, err, CodeSchemaValidation, "schema-unification")
		})
	}
	requirement := []byte(`{"id":"deliverable-requirement:1","referee_goal_id":"goal:1","production_obligation_id":"production-obligation:1","deliverable":"technical-approach-plan.cue"}`)
	requireRelationalRejection(
		"missing producer pair",
		bytes.Replace(body, append(append([]byte(`"deliverable_requirements":[`), requirement...), ']'), []byte(`"deliverable_requirements":[]`), 1),
	)
	requireRelationalRejection(
		"duplicate producer pair",
		bytes.Replace(body, append(append([]byte(`"deliverable_requirements":[`), requirement...), ']'), append(append(append(append([]byte(`"deliverable_requirements":[`), requirement...), ','), requirement...), ']'), 1),
	)
	requireRelationalRejection(
		"mismatched pair deliverable",
		bytes.Replace(body, []byte(`"deliverable":"technical-approach-plan.cue","eligibility_predicate"`), []byte(`"deliverable":"other.cue","eligibility_predicate"`), 1),
	)
	requireRelationalRejection(
		"mismatched reverse pair link",
		bytes.Replace(body, []byte(`"production_obligation_id":"production-obligation:1"`), []byte(`"production_obligation_id":"production-obligation:fabricated"`), 1),
	)
	requireRelationalRejection(
		"partial patch plan partition",
		bytes.Replace(body, []byte(`"production_obligation_ids":["production-obligation:1"]`), []byte(`"production_obligation_ids":[]`), 1),
	)
}

func TestRegistryRejectsReopeningClosedMigration(t *testing.T) {
	registry := newTestRegistry(t)
	catalog := registry.Catalog()
	catalog[0].Disposition = "pending"
	catalog[0].Executable = false
	catalog[0].Contract = nil
	_, _, err := validateCatalog(catalog)
	requireFailure(t, err, CodeAuthority, "catalog-pending")
}

func TestPatchworkCheckEventStreamCodecAdmission(t *testing.T) {
	registry := newTestRegistry(t)
	for _, sourcePath := range []string{
		"patchwork-check-event-stream.cue",
		"patchwork-check-events.jsonl",
		"sub/a/patchwork-check-event-stream.cue",
		"sub/a/patchwork-check-events.jsonl",
	} {
		t.Run(sourcePath, func(t *testing.T) {
			body := patchworkStreamBody(
				"IHtcInZhclwiOlwiY291bnRlclwiLFwib3BcIjpcImluY1wifVxyXG4Ke2JhZH0K",
				strings.Repeat("1", 40),
				sourcePath,
			)
			artifact, err := registry.Emit(context.Background(), "patchwork-check-event-stream-v1", body)
			if err != nil {
				t.Fatalf("Emit stream: %v", err)
			}
			canonical := artifactBytes(t, artifact)
			projected, err := registry.Parse(context.Background(), "patchwork-check-event-stream-v1", canonical)
			if err != nil {
				t.Fatalf("Parse stream: %v", err)
			}
			if got := artifactBytes(t, projected); !bytes.Equal(got, body) {
				t.Fatalf("projection = %s", got)
			}
		})
	}
}

func TestPatchworkCheckEventStreamRejectsInvalidEncodingAnchorsAndDurableProjectionFields(t *testing.T) {
	registry := newTestRegistry(t)
	tests := []struct {
		name string
		body []byte
		code ErrorCode
		rule string
	}{
		{
			name: "invalid base64",
			body: patchworkStreamBody("A=", strings.Repeat("1", 40), "patchwork-check-events.jsonl"),
			code: CodeEncoding,
			rule: "rfc4648-base64",
		},
		{
			name: "non canonical base64",
			body: patchworkStreamBody("Zh==", strings.Repeat("1", 40), "patchwork-check-events.jsonl"),
			code: CodeNonCanonical,
			rule: "rfc4648-base64",
		},
		{
			name: "intermediate oid length",
			body: patchworkStreamBody("", strings.Repeat("1", 41), "patchwork-check-events.jsonl"),
			code: CodeIdentity,
			rule: "source-oid",
		},
		{
			name: "unregistered source path",
			body: patchworkStreamBody("", strings.Repeat("1", 40), "sub/a/../patchwork-check-events.jsonl"),
			code: CodeIdentity,
			rule: "source-path",
		},
		{
			name: "durable events list",
			body: []byte(`{"events":[],"raw_stream_base64":"","source_oid":"1111111111111111111111111111111111111111","source_path":"patchwork-check-events.jsonl"}`),
			code: CodeSchemaValidation,
			rule: "additional-field",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := registry.Emit(context.Background(), patchworkCheckContextV1, test.body)
			requireFailure(t, err, test.code, test.rule)
		})
	}
}

func TestPatchworkCheckEventStreamDecodedLimitBelowExactAndAbove(t *testing.T) {
	registry := newTestRegistry(t)
	for _, test := range []struct {
		name    string
		size    int
		wantErr bool
	}{
		{name: "below", size: maxPatchworkStreamBytes - 1},
		{name: "exact", size: maxPatchworkStreamBytes},
		{name: "above", size: maxPatchworkStreamBytes + 1, wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			raw := bytes.Repeat([]byte{' '}, test.size)
			body := patchworkStreamBody(
				base64.StdEncoding.EncodeToString(raw),
				strings.Repeat("1", 64),
				"sub/a/patchwork-check-events.jsonl",
			)
			artifact, err := registry.Emit(context.Background(), patchworkCheckContextV1, body)
			if test.wantErr {
				requireFailure(t, err, CodeInputLimit, "decoded-stream-4-mib")
				return
			}
			if err != nil {
				t.Fatalf("Emit: %v", err)
			}
			projected, err := registry.Parse(context.Background(), patchworkCheckContextV1, artifactBytes(t, artifact))
			if err != nil {
				t.Fatalf("Parse: %v", err)
			}
			if !bytes.Equal(artifactBytes(t, projected), body) {
				t.Fatal("stream projection changed at decoded limit boundary")
			}
		})
	}
}

func TestPatchworkCheckEventStreamKeepsDeepMalformedRecordsOpaque(t *testing.T) {
	registry := newTestRegistry(t)
	raw := []byte(strings.Repeat("[", 65) + "0" + strings.Repeat("]", 65) + "\r\n{bad}\n")
	body := patchworkStreamBody(
		base64.StdEncoding.EncodeToString(raw),
		strings.Repeat("1", 40),
		"patchwork-check-events.jsonl",
	)
	canonical, err := registry.Emit(context.Background(), patchworkCheckContextV1, body)
	if err != nil {
		t.Fatalf("Emit opaque stream: %v", err)
	}
	projected, err := registry.Parse(context.Background(), patchworkCheckContextV1, artifactBytes(t, canonical))
	if err != nil {
		t.Fatalf("Parse opaque stream: %v", err)
	}
	if !bytes.Equal(artifactBytes(t, projected), body) {
		t.Fatal("opaque deep/malformed records changed")
	}
}

func patchworkStreamBody(encoded, oid, sourcePath string) []byte {
	body, err := json.Marshal(map[string]string{
		"raw_stream_base64": encoded,
		"source_oid":        oid,
		"source_path":       sourcePath,
	})
	if err != nil {
		panic(err)
	}
	return body
}

func TestProducerLifecycleContractMatrixTransitions(t *testing.T) {
	registry := newTestRegistry(t)
	oid := "1111111111111111111111111111111111111111"
	sha := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	producer := `{"name":"Ada","email":"ada@users.noreply.github.com"}`
	binding := `{"obligation_id":"obligation:1","evidence_coordinate":"producer-evidence-v1:obligation:1","promise_commit_oid":"` + oid + `","promise_body_sha256":"` + sha + `"}`
	snapshot := `{"series_snapshot_oid":"` + oid + `","canonical_root_body_sha256":"` + sha + `","scope_decomposition_commit_oid":"` + oid + `","scope_decomposition_body_sha256":"` + sha + `"}`
	projections := map[string][]byte{
		"producer-promise-v1":              []byte(`{"series_snapshot_oid":"` + oid + `","series_branch":"ai-org/patch-series/demo","node_path":".","contribution_branch":"ai-org/contrib/demo","canonical_root_body_sha256":"` + sha + `","referee_goal_id":"goal:1","obligation_id":"obligation:1","deliverable":"feature.txt","eligibility_predicate":"can produce feature.txt","producer":` + producer + `,"eligibility_decision":"eligible","eligibility_basis":"repository capability","commitment_slot":{},"completion_claim_slot":{},"previous_promise_commit_oid":null,"authored_at":"2026-07-14T00:00:00Z"}`),
		"producer-task-binding-v1":         []byte(`{"binding_id":"binding:1","series_branch":"ai-org/patch-series/demo","node_path":".","contribution_branch":"ai-org/contrib/demo","series_snapshot_oid":"` + oid + `","canonical_root_body_sha256":"` + sha + `","scope_decomposition_commit_oid":"` + oid + `","scope_decomposition_body_sha256":"` + sha + `","producer":` + producer + `,"tasks":[{"item_id":"patch_plan:demo#first","production_obligation_ids":["obligation:1"]}],"promise_bindings":[` + binding + `]}`),
		"producer-completion-assertion-v1": []byte(`{"series_branch":"ai-org/patch-series/demo","node_path":".","contribution_branch":"ai-org/contrib/demo","producer":` + producer + `,"series_snapshot_oid":"` + oid + `","canonical_root_body_sha256":"` + sha + `","scope_decomposition_commit_oid":"` + oid + `","scope_decomposition_body_sha256":"` + sha + `","task_binding_commit_oid":"` + oid + `","task_binding_body_sha256":"` + sha + `","implementation_oid":"` + oid + `","promise_bindings":[` + binding + `],"assertions":[{"obligation_id":"obligation:1","referee_goal_id":"goal:1","claim":"feature is complete","means":"implemented and tested","evidence":[{"description":"test output","artifact_anchor":"tests/test_feature.py","artifact_blob_oid":"` + oid + `","sha256":"` + sha + `"}]}],"asserted_at":"2026-07-14T00:00:00Z"}`),
		"claim-admission-v1":               []byte(`{"evaluated_contribution_oid":"` + oid + `","evaluated_at":"2026-07-14T00:00:00Z","source_snapshot":` + snapshot + `,"authority_notes_ref_oid":"` + oid + `","required_obligation_ids":["obligation:1"],"promise_bindings":[` + binding + `],"task_binding_body_sha256":"` + sha + `","assertions":[{"obligation_id":"obligation:1","assertion_commit_oid":"` + oid + `","assertion_body_sha256":"` + sha + `"}],"implementation_result_body_sha256":"` + sha + `","admitted":true}`),
		"functional-acceptance-v1":         []byte(`{"evaluated_contribution_oid":"` + oid + `","claim_admission_body_sha256":"` + sha + `","required_obligation_ids":["obligation:1"],"source_snapshot":` + snapshot + `,"authority_notes_ref_oid":"` + oid + `","referee_evaluations":[{"referee_goal_id":"goal:1","accepted":true,"finding":"criterion satisfied","evidence":"test output"}],"reachable":true,"notes":"independent checks passed","issued_at":"2026-07-14T00:00:00Z"}`),
		"acceptance-authority-seal-v1":     []byte(`{"authority_contract_version":"acceptance-authority-v1","verifier_role":"independent-functional-acceptance","contribution_ref":"refs/heads/ai-org/contrib/demo","target_verdict_commit_oid":"` + oid + `","verdict_body_sha256":"` + sha + `","claim_admission_body_sha256":"` + sha + `","source_snapshot":` + snapshot + `,"authority_notes_ref_oid":"` + oid + `","issued_at":"2026-07-14T00:00:00Z"}`),
	}

	for contextID, projection := range projections {
		t.Run(contextID, func(t *testing.T) {
			canonical, err := registry.Emit(context.Background(), contextID, projection)
			if err != nil {
				t.Fatalf("emit: %v", err)
			}
			parsed, err := registry.Parse(context.Background(), contextID, artifactBytes(t, canonical))
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			var got, want any
			if err := json.Unmarshal(artifactBytes(t, parsed), &got); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(projection, &want); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("round trip changed projection: %s", artifactBytes(t, parsed))
			}
		})
	}

	committedPromise := bytes.Replace(
		projections["producer-promise-v1"],
		[]byte(`"commitment_slot":{}`),
		[]byte(`"commitment_slot":{"task_binding_path":"producer-task-binding.cue","task_binding_body_sha256":"`+sha+`"}`),
		1,
	)
	completedPromise := bytes.Replace(
		committedPromise,
		[]byte(`"completion_claim_slot":{}`),
		[]byte(`"completion_claim_slot":{"assertion_path":"producer-completion-assertion.cue","assertion_body_sha256":"`+sha+`"}`),
		1,
	)
	if _, err := registry.Emit(context.Background(), "producer-promise-v1", completedPromise); err != nil {
		t.Fatalf("promise empty-to-complete transition: %v", err)
	}
	partialCommitment := bytes.Replace(
		projections["producer-promise-v1"],
		[]byte(`"commitment_slot":{}`),
		[]byte(`"commitment_slot":{"task_binding_path":"producer-task-binding.cue"}`),
		1,
	)
	_, err := registry.Emit(context.Background(), "producer-promise-v1", partialCommitment)
	requireFailure(t, err, CodeSchemaValidation, "schema-unification")

	rejectField := func(contextID, field string) {
		t.Helper()
		body := projections[contextID]
		invalid := append(append([]byte{}, body[:len(body)-1]...), []byte(`,"`+field+`":"`+sha+`"}`)...)
		_, err := registry.Emit(context.Background(), contextID, invalid)
		requireFailure(t, err, CodeSchemaValidation, "schema-unification")
	}
	for _, field := range []string{"binding_sha256", "task_binding_body_sha256", "containing_commit_oid"} {
		rejectField("producer-task-binding-v1", field)
	}
	for _, field := range []string{"verdict_commit_oid", "containing_verdict_oid"} {
		rejectField("functional-acceptance-v1", field)
	}
	for contextID := range projections {
		for _, field := range []string{"work_type", "worker_role", "producer_kind", "child_kind", "node_kind"} {
			rejectField(contextID, field)
		}
	}

	incompleteClaim := bytes.Replace(projections["producer-completion-assertion-v1"], []byte(`"evidence":[{"description"`), []byte(`"evidence":[],"discarded":[{"description"`), 1)
	_, err = registry.Emit(context.Background(), "producer-completion-assertion-v1", incompleteClaim)
	requireFailure(t, err, CodeSchemaValidation, "schema-unification")

	inspection, err := registry.Inspect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	registered := map[string]ContractInfo{}
	for _, contract := range inspection.Contracts {
		if contract.Manifest == ProducerLifecycleManifestV1 {
			registered[contract.ContextID] = contract
		}
	}
	if len(registered) != len(projections) {
		t.Fatalf("producer contract snapshots = %d, want %d", len(registered), len(projections))
	}
	if len(producerLifecycleCompatibilityMatrix) != len(projections) {
		t.Fatalf("producer compatibility matrix = %d, want %d", len(producerLifecycleCompatibilityMatrix), len(projections))
	}
	for _, registration := range producerLifecycleCompatibilityMatrix {
		contextID := registration.definition.contextID
		if _, ok := projections[contextID]; !ok || !producerLifecycleContext(contextID) {
			t.Fatalf("producer compatibility registration = %#v", registration)
		}
		if got := contractsByContextForTest(t, registry, contextID); got != registration.contract {
			t.Fatalf("producer compatibility identity for %s = %#v, want %#v", contextID, got, registration.contract)
		}
	}
	for contextID := range projections {
		contract := registered[contextID]
		if len(contract.ProjectionProfiles) != 1 || len(contract.SchemaProfiles) != 0 || len(contract.HistoricalAliases) != 0 {
			t.Fatalf("dormant contract %s profiles/aliases = %#v", contextID, contract)
		}
		if contract.SchemaProfiles == nil || contract.HistoricalWriters == nil || contract.HistoricalAliases == nil {
			t.Fatalf("dormant contract %s empty snapshot fields must be arrays: %#v", contextID, contract)
		}
		if contract.DefinitionPath != schemaAuthorityPath+":#"+contract.Contract.Kind || contract.DefinitionSHA256 == "" {
			t.Fatalf("dormant contract %s snapshot = %#v", contextID, contract)
		}
	}
}

func contractsByContextForTest(t *testing.T, registry *Registry, contextID string) ContractIdentity {
	t.Helper()
	identity, ok := registry.contractsByContext[contextID]
	if !ok {
		t.Fatalf("missing contract identity for %s", contextID)
	}
	return identity
}

func TestProducerLifecycleSchemaAllowsOnlyPromiseSlotEmptyStates(t *testing.T) {
	schema, err := fs.ReadFile(engineauthority.Authority, schemaAuthorityPath)
	if err != nil {
		t.Fatal(err)
	}
	marker := []byte("// The producer lifecycle is registered as one dormant compatibility matrix.")
	parts := bytes.SplitN(schema, marker, 2)
	if len(parts) != 2 {
		t.Fatal("producer lifecycle schema marker not found")
	}
	lifecycle := parts[1]
	if got := bytes.Count(lifecycle, []byte("*close({})")); got != 2 {
		t.Fatalf("intentional producer lifecycle empty states = %d, want 2", got)
	}
	for _, declaration := range []string{
		"#ProducerCommitmentSlot: *close({}) | close({",
		"#ProducerCompletionClaimSlot: *close({}) | close({",
	} {
		if !bytes.Contains(lifecycle, []byte(declaration)) {
			t.Fatalf("missing intentional empty-state declaration %q", declaration)
		}
	}
}

func TestProducerLifecycleCatalogMatrixRejectsMissingContext(t *testing.T) {
	registry := newTestRegistry(t)
	catalog := registry.Catalog()
	replaced := false
	for index := range catalog {
		if catalog[index].ContextID != "claim-admission-v1" {
			continue
		}
		registration, ok := producerLifecycleRegistrationFor("producer-promise-v1")
		if !ok {
			t.Fatal("producer promise registration not found")
		}
		catalog[index].ContextID = registration.definition.contextID
		catalog[index].Contract = &CatalogContract{
			APIVersion: registration.contract.APIVersion,
			Kind:       registration.contract.Kind,
			Variant:    registration.contract.Variant,
		}
		replaced = true
		break
	}
	if !replaced {
		t.Fatal("claim admission catalog row not found")
	}

	_, _, err := validateCatalog(catalog)
	requireFailure(t, err, CodeAuthority, "producer-lifecycle-matrix")
}

func TestProducerLifecycleCatalogMatrixRejectsForeignContext(t *testing.T) {
	registry := newTestRegistry(t)
	catalog := registry.Catalog()
	for index := range catalog {
		if catalog[index].ContextID == "patchwork-check-event-stream-v1" {
			catalog[index].Cohort = producerLifecycleCohort
			_, _, err := validateCatalog(catalog)
			requireFailure(t, err, CodeAuthority, "producer-lifecycle-matrix")
			return
		}
	}
	t.Fatal("patchwork check stream catalog row not found")
}

func TestProducerLifecycleCompatibilityMatrixRejectsRegistrationDrift(t *testing.T) {
	registry := newTestRegistry(t)
	contexts := make(map[string]string)
	cohorts := make(map[string]string)
	for _, row := range registry.Catalog() {
		contexts[row.ContextID] = row.Disposition
		cohorts[row.ContextID] = row.Cohort
	}

	tests := []struct {
		name   string
		mutate func([]producerLifecycleRegistration) []producerLifecycleRegistration
	}{
		{
			name: "missing registration",
			mutate: func(matrix []producerLifecycleRegistration) []producerLifecycleRegistration {
				return matrix[:len(matrix)-1]
			},
		},
		{
			name: "duplicate registration",
			mutate: func(matrix []producerLifecycleRegistration) []producerLifecycleRegistration {
				matrix[len(matrix)-1] = matrix[0]
				return matrix
			},
		},
		{
			name: "contract kind drift",
			mutate: func(matrix []producerLifecycleRegistration) []producerLifecycleRegistration {
				matrix[0].contract.Kind = "RetargetedProducerPromise"
				return matrix
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			matrix := append([]producerLifecycleRegistration(nil), producerLifecycleCompatibilityMatrix[:]...)
			err := validateProducerLifecycleCompatibilityMatrix(test.mutate(matrix), contexts, cohorts)
			requireFailure(t, err, CodeAuthority, "producer-lifecycle-matrix")
		})
	}
}

func TestRegistryCatalogAndInspectionAreDefensiveCopies(t *testing.T) {
	registry := newTestRegistry(t)
	catalog := registry.Catalog()
	admitted := -1
	for i := range catalog {
		if catalog[i].Contract != nil {
			admitted = i
			break
		}
	}
	if admitted < 0 {
		t.Fatal("admitted catalog row not found")
	}
	catalog[admitted].Contract.Kind = "Mutated"
	inspection, err := registry.Inspect(context.Background())
	if err != nil {
		t.Fatalf("Inspect: %v", err)
	}
	for _, row := range inspection.Catalog {
		if row.Contract != nil && row.Contract.Kind == "Mutated" {
			t.Fatalf("registry contract was mutated: %#v", row.Contract)
		}
	}
}

func TestRegistryRejectsUnprovenLimitOverrides(t *testing.T) {
	for _, options := range []Options{
		{MaxBytes: DefaultMaxBytes - 1},
		{MaxBytes: DefaultMaxBytes + 1},
		{MaxDepth: DefaultMaxDepth - 1},
		{MaxDepth: DefaultMaxDepth + 1},
	} {
		if registry, err := New(options); registry != nil || err == nil {
			t.Fatalf("New(%#v) = (%#v, %v), want provenance rejection", options, registry, err)
		}
	}
}

func TestPublicContractHasNoAccidentalExports(t *testing.T) {
	document, _, _, err := readPublicContract()
	if err != nil {
		t.Fatalf("readPublicContract: %v", err)
	}
	packages, err := parser.ParseDir(token.NewFileSet(), ".", func(info os.FileInfo) bool {
		return filepath.Ext(info.Name()) == ".go" && !strings.HasSuffix(info.Name(), "_test.go")
	}, 0)
	if err != nil {
		t.Fatal(err)
	}
	pkg := packages["kindsv2"]
	if pkg == nil {
		t.Fatal("kindsv2 package not found")
	}
	var got []string
	for _, file := range pkg.Files {
		for _, declaration := range file.Decls {
			switch declaration := declaration.(type) {
			case *ast.FuncDecl:
				if declaration.Recv == nil && ast.IsExported(declaration.Name.Name) {
					got = append(got, "func "+declaration.Name.Name)
				}
			case *ast.GenDecl:
				for _, specification := range declaration.Specs {
					switch specification := specification.(type) {
					case *ast.TypeSpec:
						if ast.IsExported(specification.Name.Name) {
							got = append(got, "type "+specification.Name.Name)
						}
					case *ast.ValueSpec:
						for _, name := range specification.Names {
							if ast.IsExported(name.Name) {
								got = append(got, declaration.Tok.String()+" "+name.Name)
							}
						}
					}
				}
			}
		}
	}
	var want []string
	for _, name := range document.Exports.Constants {
		want = append(want, "const "+name)
	}
	for _, name := range document.Exports.Types {
		want = append(want, "type "+name)
	}
	for _, signature := range document.Exports.Functions {
		want = append(want, "func "+strings.SplitN(signature, "(", 2)[0])
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("exported declarations = %#v, want %#v", got, want)
	}
	assertExportedMethods(t, reflect.TypeOf((*Registry)(nil)), []string{
		"Catalog", "Coverage", "Emit", "Handle", "Handshake", "Inspect", "Parse", "Project", "Schema", "ValidateProjection", "Vet",
	})
	assertExportedMethods(t, reflect.TypeOf(Artifact{}), []string{"Bytes"})
	assertExportedMethods(t, reflect.TypeOf((*Failure)(nil)), []string{"Error"})
}

func assertExportedMethods(t *testing.T, value reflect.Type, want []string) {
	t.Helper()
	got := make([]string, value.NumMethod())
	for index := range got {
		got[index] = value.Method(index).Name
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%v methods = %#v, want %#v", value, got, want)
	}
}

func TestEmitParseVetProjectAndHistoricalTransition(t *testing.T) {
	registry := newTestRegistry(t)
	canonicalArtifact, err := registry.Emit(context.Background(), SemanticContextV1, semanticProjection)
	if err != nil {
		t.Fatalf("Emit: %v", err)
	}
	canonical := artifactBytes(t, canonicalArtifact)
	wantCanonical := []byte(`{"apiVersion": "ai-org-cue-body-v1", "body": {"change_kind": "feature", "owner": "Ada", "subsystem": "notes", "working_state": "ready"}, "kind": "SemanticStatus", "variant": "git-note"}` + "\n")
	if !bytes.Equal(canonical, wantCanonical) {
		t.Fatalf("canonical bytes:\n%s\nwant:\n%s", canonical, wantCanonical)
	}
	if bytes.Contains(canonical, []byte("\r")) || canonical[len(canonical)-1] != '\n' || canonical[len(canonical)-2] == '\n' {
		t.Fatalf("canonical newline contract: %q", canonical)
	}

	parsed, err := registry.Parse(context.Background(), SemanticContextV1, canonical)
	if err != nil {
		t.Fatalf("Parse emitted canonical: %v", err)
	}
	if got := string(artifactBytes(t, parsed)); got != canonicalProjection {
		t.Fatalf("parsed projection = %s", got)
	}
	if err := registry.Vet(context.Background(), SemanticContextV1, canonical); err != nil {
		t.Fatalf("Vet emitted canonical: %v", err)
	}
	projected, err := registry.Project(context.Background(), SemanticContextV1, ConsumerJSONProfileV1, canonical)
	if err != nil {
		t.Fatalf("Project emitted canonical: %v", err)
	}
	if !bytes.Equal(artifactBytes(t, parsed), artifactBytes(t, projected)) {
		t.Fatal("Parse and Project differ")
	}

	historical := []byte(" {\n  \"working_state\": \"ready\", \"owner\": \"Ada\", \"change_kind\": \"feature\", \"subsystem\": \"notes\"\n}\n")
	legacy, err := registry.Parse(context.Background(), SemanticContextV1, historical)
	if err != nil {
		t.Fatalf("Parse historical JSON: %v", err)
	}
	if !bytes.Equal(artifactBytes(t, parsed), artifactBytes(t, legacy)) {
		t.Fatal("historical and canonical projections differ")
	}
}

func TestPatchSeriesRootCohortTransitions(t *testing.T) {
	registry := newTestRegistry(t)
	stringsOf := func(names ...string) map[string]any {
		value := map[string]any{}
		for _, name := range names {
			value[name] = ""
		}
		return value
	}
	ux := map[string]any{
		"applicability":               stringsOf("applicability", "not_user_facing_reason"),
		"experience_identity":         stringsOf("named_reference", "genre_conventions", "must_resemble", "must_not_resemble"),
		"presentation_model":          stringsOf("camera_and_view", "world_readability", "ui_taxonomy_notes"),
		"core_status_surfaces":        stringsOf("player_status", "opposition_status", "inventory_resources", "objective_progress", "location_identity"),
		"entity_affordances":          stringsOf("interactive_entities", "exits_and_transitions", "gates_and_locks", "hazards_and_bosses", "collectibles", "decorative_elements"),
		"action_feedback_matrix":      []any{},
		"progression_legibility":      stringsOf("current_goal_visibility", "locked_state_feedback", "unlocked_state_feedback", "flag_observability", "ending_state_consistency"),
		"hud_and_ui_flow":             stringsOf("primary_hud", "secondary_screens", "menu_flow", "dialog_flow", "failure_and_recovery"),
		"visual_language_constraints": stringsOf("contrast", "palette_role", "silhouette_readability", "labels_and_markers", "animation_minimums"),
		"accessibility_baseline":      stringsOf("controls", "text_readability", "color_independence", "audio_independence", "pacing"),
		"acceptance_tests":            map[string]any{"screenshot_checks": []any{}, "interaction_checks": []any{}, "playtest_checks": []any{}},
	}
	cover, marshalErr := json.Marshal(map[string]any{
		"raw_request": "ship it", "working_title": "Ship", "request_type": "feature", "problem_or_motivation": "p", "intended_users_or_jobs": "u", "desired_outcomes_success": "s", "affected_area_platform": "a",
		"tech_stack": stringsOf("build_strategy", "engine", "framework", "language", "platform", "rationale", "provenance"), "user_experience_requirements": ux,
		"background_facts": "b", "constraints_assumptions": []any{}, "references": []any{}, "grounding_provenance": "g", "open_questions": []any{}, "non_goals_out_of_scope": []any{}, "proposal_hint": "h", "alternatives_considered": []any{},
	})
	if marshalErr != nil {
		t.Fatal(marshalErr)
	}
	canonical, err := registry.Emit(context.Background(), "patch-series-cover-letter-v1", cover)
	if err != nil {
		t.Fatalf("emit cover: %v", err)
	}
	current, err := registry.Parse(context.Background(), "patch-series-cover-letter-v1", artifactBytes(t, canonical))
	if err != nil {
		t.Fatalf("parse canonical cover: %v", err)
	}
	historical, err := registry.Parse(context.Background(), "patch-series-cover-letter-v1", cover)
	if err != nil {
		t.Fatalf("parse historical cover: %v", err)
	}
	if !bytes.Equal(artifactBytes(t, current), artifactBytes(t, historical)) {
		t.Fatal("canonical and historical cover projections differ")
	}
	provenance := []byte(`{"request_id":"r1","payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","raw_request":"ship it","request_payload":{"raw_request":"ship it"},"memento":"ingress only"}`)
	if _, err := registry.Emit(context.Background(), "request-provenance-branch-v1", provenance); err != nil {
		t.Fatalf("emit provenance: %v", err)
	}
	var coverValue map[string]any
	if err := json.Unmarshal(cover, &coverValue); err != nil {
		t.Fatal(err)
	}
	commission := map[string]any{
		"schema": "ai-org-commission-request-v1", "spine_contract_refs": []any{},
		"identity_manifest_fields": []any{}, "acceptance_checks": []any{}, "contract_fields": []any{},
	}
	if _, err := registry.Emit(context.Background(), "child-patch-series-cover-letter-v1", cover); err != nil {
		t.Fatalf("emit child cover variant: %v", err)
	}
	maintainerRequest, err := json.Marshal(map[string]any{
		"id": "series-child", "submitted_at": "", "request": coverValue, "commission_request": commission,
		"provenance": map[string]any{"requester": "parent", "parent_branch": "ai-org/patch-series/demo", "parent_node_path": ".", "child_key": "child"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := registry.Emit(context.Background(), "maintainer-series-request-v1", maintainerRequest); err != nil {
		t.Fatalf("emit maintainer request: %v", err)
	}
	if _, err := registry.Schema(context.Background(), "patch-series-cover-letter-v1", CodexSchemaProfileV1); err != nil {
		t.Fatalf("cover schema: %v", err)
	}
	projected, err := registry.Project(
		context.Background(), "patch-series-cover-letter-v1", ConsumerJSONProfileV1, artifactBytes(t, canonical),
	)
	if err != nil {
		t.Fatalf("project canonical cover through guarded schema pair: %v", err)
	}
	if !bytes.Equal(artifactBytes(t, projected), cover) {
		t.Fatal("guarded cover projection changed bytes")
	}
	negative := []byte(`{"raw_request":"ship it"}`)
	requireFailure(t, registry.ValidateProjection(context.Background(), "patch-series-cover-letter-v1", negative), CodeSchemaValidation, "required")
}

func TestNetworkPublicationCohortTransitions(t *testing.T) {
	registry := newTestRegistry(t)
	projections := map[string][]byte{
		"root-network-node-manifest-v1":    []byte(`{"schema":"patch_series-network-node-v1","identity_stage":"serialized-parent","node_path":".","branch":"ai-org/patch-series/demo","relation_from_parent":"root","lifecycle_status":"active","ownership":{"request_owner":"requester","interior_owner":"parent"},"write_scope":{"allowed_subtree":"."},"scope_item_ids":["goal"],"children":[]}`),
		"child-network-node-manifest-v1":   []byte(`{"schema":"patch_series-network-node-v1","identity_stage":"forming","id":"demo:sub/child","child_key":"child","node_path":"sub/child","parent_branch":"ai-org/patch-series/demo","relation_from_parent":"split-into","split_operator":"AND","edges":[],"scope_item_ids":["goal"],"lifecycle_status":"ready_for_patch_authoring","ownership":{"request_owner":"parent","interior_owner":"child"},"write_scope":{"allowed_subtree":"sub/child/"},"contrib_branch":"ai-org/contrib/demo-child"}`),
		"series-coverage-ledger-v1":        []byte(`{"schema":"series-coverage-ledger-v1","ledger_revision":7,"supersedes_ledger_commit":"","rebaselined_from_escalation":{},"parent_branch":"ai-org/patch-series/demo","relation":"split-into","split_operator":"AND","scope_items":[],"coverage":[],"parent_retained_scope_ids":[],"children":[]}`),
		"series-scope-decomposition-v1":    []byte(`{"schema":"series-scope-decomposition-v1","frozen_root_oid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","canonical_root_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","historical_ledger_schema":"series-coverage-ledger-v1","ledger_revision":2,"supersedes_ledger_commit":"cccccccccccccccccccccccccccccccccccccccc","rebaselined_from_escalation":{"review_round":3,"child_branch":"ai-org/contrib/child","git_result_commit":"dddddddddddddddddddddddddddddddddddddddd"},"scope_items":[{"id":"goal:preview","kind":"referee_goal","text":"verify the root"},{"id":"patch_plan:root#follow-up-03","kind":"patch_plan","text":"project scope"},{"id":"obligation:preview","kind":"production_obligation","text":"technical-approach-plan.cue","production_obligation_id":"obligation:preview","referee_goal_id":"goal:preview","deliverable_requirement_id":"requirement:preview","patch_plan_item_id":"patch_plan:root#follow-up-03","deliverable":"technical-approach-plan.cue","eligibility_predicate":"can produce the root"}],"ownership":[{"scope_item_id":"goal:preview","owner":"root","owner_node_path":"."},{"scope_item_id":"patch_plan:root#follow-up-03","owner":"child","owner_node_path":"sub/child"},{"scope_item_id":"obligation:preview","owner":"child","owner_node_path":"sub/child"}],"parent_retained_scope_ids":[]}`),
		"patch-queue-status-rollup-v1":     []byte(`{"schema":"patch-queue-status-rollup-v1","generated":true,"generated_from":"patch-series-manifest.json","ledger_schema":"series-coverage-ledger-v1","node_path":".","children":[],"coverage":[]}`),
		"patch-series-metadata-v1":         []byte(`{"schema":"patch_series-network-node-v1","lifecycle_status":"ready_for_patch_authoring","parent_branch":"ai-org/patch-series/demo","child_key":"child","edges":[]}`),
		"child-technical-approach-tree-v1": []byte(`{"lineage_child":{"id":"demo:sub/child","lifecycle_status":"ready_for_patch_authoring","summary":"child","systems":["codec"],"acceptance_criteria":["passes"],"functional_check":"go test ./...","ux_acceptance_tests":[],"risks":[]}}`),
		"lineage-carrier-recipe-v1":        []byte(`{"source_context":"child-network-node-manifest-v1","source_kind":"NetworkNodeManifest","target_context":"series-coverage-ledger-v1","target_kind":"SeriesCoverageLedger","projection_profile":"consumer-json-v1","schema_profile":"codex-structured-output-v1","blob_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","schema_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}`),
	}
	for contextID, projection := range projections {
		t.Run(contextID, func(t *testing.T) {
			canonical, err := registry.Emit(context.Background(), contextID, projection)
			if err != nil {
				t.Fatalf("emit: %v", err)
			}
			current, err := registry.Parse(context.Background(), contextID, artifactBytes(t, canonical))
			if err != nil {
				t.Fatalf("parse canonical: %v", err)
			}
			historical, err := registry.Parse(context.Background(), contextID, projection)
			if err != nil {
				t.Fatalf("parse historical: %v", err)
			}
			if !bytes.Equal(artifactBytes(t, current), artifactBytes(t, historical)) {
				t.Fatal("canonical and historical projections differ")
			}
			if contextID == "lineage-carrier-recipe-v1" {
				if _, err := registry.Schema(context.Background(), contextID, CodexSchemaProfileV1); err != nil {
					t.Fatalf("schema: %v", err)
				}
			}
		})
	}

	// ledger_revision is mutable rebaseline state, not an identity selector.
	for _, revision := range []string{"7", "8"} {
		projection := bytes.Replace(projections["series-coverage-ledger-v1"], []byte(`"ledger_revision":7`), []byte(`"ledger_revision":`+revision), 1)
		if _, err := registry.Emit(context.Background(), "series-coverage-ledger-v1", projection); err != nil {
			t.Fatalf("ledger revision %s: %v", revision, err)
		}
	}
	negative := []byte(`{"schema":"series-coverage-ledger-v1","ledger_revision":"v2"}`)
	_, err := registry.Emit(context.Background(), "series-coverage-ledger-v1", negative)
	requireFailure(t, err, CodeSchemaValidation, "schema-unification")

	inexactOwnership := bytes.Replace(
		projections["series-scope-decomposition-v1"],
		[]byte(`,{"scope_item_id":"obligation:preview","owner":"child","owner_node_path":"sub/child"}`),
		[]byte{},
		1,
	)
	_, err = registry.Emit(context.Background(), "series-scope-decomposition-v1", inexactOwnership)
	requireFailure(t, err, CodeSchemaValidation, "schema-unification")

	duplicateRetainedScope := bytes.Replace(
		projections["series-scope-decomposition-v1"],
		[]byte(`"parent_retained_scope_ids":[]`),
		[]byte(`"parent_retained_scope_ids":["goal:preview","goal:preview"]`),
		1,
	)
	_, err = registry.Emit(context.Background(), "series-scope-decomposition-v1", duplicateRetainedScope)
	requireFailure(t, err, CodeSchemaValidation, "schema-unification")
}

func TestCanonicalFailuresDoNotReleaseArtifact(t *testing.T) {
	registry := newTestRegistry(t)
	canonicalArtifact, err := registry.Emit(context.Background(), SemanticContextV1, semanticProjection)
	if err != nil {
		t.Fatalf("Emit: %v", err)
	}
	canonical := artifactBytes(t, canonicalArtifact)
	wrongKind := bytes.Replace(canonical, []byte(`"SemanticStatus"`), []byte(`"OtherStatus"`), 1)
	_, err = registry.Parse(context.Background(), SemanticContextV1, wrongKind)
	failure := requireFailure(t, err, CodeIdentity, "kind-mismatch")
	if failure.CUEPath != "kind" || failure.JSONPointer != "/kind" {
		t.Fatalf("wrong-kind location = %#v", failure)
	}

	compact := bytes.ReplaceAll(canonical, []byte(" "), nil)
	_, err = registry.Parse(context.Background(), SemanticContextV1, compact)
	requireFailure(t, err, CodeNonCanonical, CanonicalFormatV1)

	duplicate := []byte(`{"change_kind":"feature","owner":"Ada","owner":"Grace","subsystem":"notes","working_state":"ready"}`)
	_, err = registry.Parse(context.Background(), SemanticContextV1, duplicate)
	requireFailure(t, err, CodeDuplicateKey, "duplicate-key")

	response := registry.Handle(context.Background(), Request{
		Protocol: ProtocolV1, Operation: OperationParse, ContextID: SemanticContextV1,
		InputBase64: encodeBase64(wrongKind),
	})
	if response.OK || response.Artifact != nil || response.Failure == nil || response.Failure.Code != CodeIdentity {
		t.Fatalf("failure response released payload: %#v", response)
	}
}

func TestSchemaFromDefinitionAcceptsPositiveRejectsNegative(t *testing.T) {
	registry := newTestRegistry(t)
	schemaArtifact, err := registry.Schema(context.Background(), SemanticContextV1, CodexSchemaProfileV1)
	if err != nil {
		t.Fatalf("Schema without instance: %v", err)
	}
	if schemaArtifact.SHA256 != "65df728b8c61b98b77fc9ac024a904ba2b8b0539c49ef3581a02edea7afc83fe" {
		t.Fatalf("schema digest = %s", schemaArtifact.SHA256)
	}
	if err := registry.ValidateProjection(context.Background(), SemanticContextV1, []byte(canonicalProjection)); err != nil {
		t.Fatalf("positive projection: %v", err)
	}
	negative := []byte(`{"change_kind":"feature","owner":17,"subsystem":"notes","working_state":"ready"}`)
	err = registry.ValidateProjection(context.Background(), SemanticContextV1, negative)
	requireFailure(t, err, CodeSchemaValidation, "type")

	empty := []byte(`{"change_kind":"","owner":"","subsystem":"","working_state":""}`)
	if err := registry.ValidateProjection(context.Background(), SemanticContextV1, empty); err != nil {
		t.Fatalf("empty-string schema projection: %v", err)
	}
	if _, err := registry.Emit(context.Background(), SemanticContextV1, empty); err != nil {
		t.Fatalf("empty-string codec projection: %v", err)
	}
}

func TestHandshakeAndAuthorityDrift(t *testing.T) {
	registry := newTestRegistry(t)
	handshake, err := registry.Handshake(context.Background())
	if err != nil {
		t.Fatalf("Handshake: %v", err)
	}
	if handshake.GoRelease != "1.26.5" || handshake.GoRelease != strings.TrimPrefix(runtime.Version(), "go") {
		t.Fatalf("go release = %q", handshake.GoRelease)
	}
	if _, err := hex.DecodeString(handshake.PublicContractSHA256); err != nil || len(handshake.PublicContractSHA256) != 64 {
		t.Fatalf("public contract digest = %q", handshake.PublicContractSHA256)
	}

	authority := fstest.MapFS{}
	for _, name := range authorityPaths {
		data, err := fs.ReadFile(engineauthority.Authority, name)
		if err != nil {
			t.Fatalf("read authority %s: %v", name, err)
		}
		authority[name] = &fstest.MapFile{Data: data, Mode: 0o444}
	}
	mutable, err := New(Options{Authority: authority})
	if err != nil {
		t.Fatalf("New mutable authority: %v", err)
	}
	authority[schemaAuthorityPath].Data = append(authority[schemaAuthorityPath].Data, []byte("\n// drift\n")...)
	_, err = mutable.Handshake(context.Background())
	requireFailure(t, err, CodeAuthority, "authority-changed")
}

func TestGroundedCodecByteLimitBelowExactAndAbove(t *testing.T) {
	registry := newTestRegistry(t)
	base := append([]byte(canonicalProjection), '\n')
	for _, test := range []struct {
		name    string
		size    int
		wantErr bool
	}{
		{name: "below", size: DefaultMaxBytes - 1},
		{name: "exact", size: DefaultMaxBytes},
		{name: "above", size: DefaultMaxBytes + 1, wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			source := append([]byte(nil), base...)
			source = append(source, bytes.Repeat([]byte{' '}, test.size-len(source))...)
			artifact, err := registry.Parse(context.Background(), SemanticContextV1, source)
			if test.wantErr {
				requireFailure(t, err, CodeInputLimit, "max-bytes")
				return
			}
			if err != nil {
				t.Fatalf("Parse: %v", err)
			}
			if got := string(artifactBytes(t, artifact)); got != canonicalProjection {
				t.Fatalf("projection = %s", got)
			}
		})
	}
}

func TestGroundedCodecDepthLimitBelowExactAndAbove(t *testing.T) {
	registry := newTestRegistry(t)
	for _, test := range []struct {
		name       string
		depth      int
		structural bool
	}{
		{name: "below", depth: DefaultMaxDepth - 1},
		{name: "exact", depth: DefaultMaxDepth},
		{name: "above", depth: DefaultMaxDepth + 1, structural: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			source := semanticProjectionAtDepth(test.depth)
			_, err := registry.Parse(context.Background(), SemanticContextV1, source)
			if test.structural {
				requireFailure(t, err, CodeStructuralLimit, "max-depth")
			} else {
				// Depths at or below the grounded boundary reach ordinary schema
				// validation; the synthetic extra field is intentionally inadmissible.
				requireFailure(t, err, CodeSchemaValidation, "additional-field")
			}

			_, canonicalErr := registry.Parse(
				context.Background(), SemanticContextV1, semanticEnvelopeAtDepth(test.depth),
			)
			if test.structural {
				requireFailure(t, canonicalErr, CodeStructuralLimit, "max-depth")
			} else {
				requireFailure(t, canonicalErr, CodeSchemaValidation, "schema-unification")
			}
		})
	}
}

func TestGroundedCodecPreservesEarlierDuplicateOverLaterDepthLimit(t *testing.T) {
	registry := newTestRegistry(t)
	nested := strings.Repeat("[", DefaultMaxDepth) + "0" + strings.Repeat("]", DefaultMaxDepth)
	source := []byte(`{"z":1,"z":2,"a":` + nested + `}`)

	_, err := registry.Parse(context.Background(), SemanticContextV1, source)
	failure := requireFailure(t, err, CodeDuplicateKey, "duplicate-key")
	if failure.JSONPointer != "/z" {
		t.Fatalf("duplicate pointer = %q, want /z", failure.JSONPointer)
	}
}

func TestGroundedCodecPreservesLaterParseFailuresOverEarlierDepthLimit(t *testing.T) {
	registry := newTestRegistry(t)
	nested := strings.Repeat("[", DefaultMaxDepth) + "0" + strings.Repeat("]", DefaultMaxDepth)
	tests := []struct {
		name    string
		suffix  string
		code    ErrorCode
		rule    string
		pointer string
	}{
		{name: "duplicate", suffix: `,"z":1,"z":2}`, code: CodeDuplicateKey, rule: "duplicate-key", pointer: "/z"},
		{name: "syntax", suffix: `,"z":[}`, code: CodeSyntax, rule: "json-syntax", pointer: "/z/0"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			source := []byte(`{"a":` + nested + test.suffix)
			_, err := registry.Parse(context.Background(), SemanticContextV1, source)
			failure := requireFailure(t, err, test.code, test.rule)
			if failure.JSONPointer != test.pointer {
				t.Fatalf("failure pointer = %q, want %q", failure.JSONPointer, test.pointer)
			}
		})
	}
}

func semanticProjectionAtDepth(depth int) []byte {
	nested := []byte(`"leaf"`)
	for current := 2; current < depth; current++ {
		nested = append(append([]byte{'['}, nested...), ']')
	}
	return []byte(`{"change_kind":"feature","owner":"Ada","subsystem":"notes","working_state":"ready","extra":` + string(nested) + `}`)
}

func semanticEnvelopeAtDepth(depth int) []byte {
	nested := []byte(`"leaf"`)
	for current := 3; current < depth; current++ {
		nested = append(append([]byte{'['}, nested...), ']')
	}
	body := `{"change_kind":"feature","owner":"Ada","subsystem":"notes","working_state":"ready","extra":` + string(nested) + `}`
	return []byte(`{"apiVersion":"` + SemanticAPIVersionV1 + `","body":` + body + `,"kind":"` + SemanticKindV1 + `","variant":"` + SemanticVariantV1 + `"}`)
}
