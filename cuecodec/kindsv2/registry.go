package kindsv2

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"io/fs"
	"path"
	"runtime"
	"runtime/debug"
	"sort"
	"strings"

	"cuelang.org/go/cue"
	"cuelang.org/go/cue/cuecontext"
	engineauthority "github.com/mishima-computing/cuecodec/cue/engine"
	"github.com/mishima-computing/cuecodec/internal/cueload"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

const (
	catalogAuthorityPath    = "registry/durable-body-catalog-v1.json"
	schemaAuthorityPath     = "schema/semantic_status.cue"
	rootCodecSourceSHA      = "3bd9daeb54d37838fc873b09315ff8c884a8cb3e44983b3ac1b8ce74e1c6cde4"
	producerLifecycleCohort = "producer_lifecycle"
)

var authorityPaths = [...]string{catalogAuthorityPath, schemaAuthorityPath}

type bodyDefinition struct {
	contextID string
	body      string
	envelope  string
	kind      string
}

type producerLifecycleRegistration struct {
	definition bodyDefinition
	contract   ContractIdentity
}

// producerLifecycleCompatibilityMatrix is the single registration authority
// for the dormant producer lifecycle. Schema admission, inspected contract
// snapshots, and catalog validation all derive from this closed matrix.
var producerLifecycleCompatibilityMatrix = [...]producerLifecycleRegistration{
	{bodyDefinition{"producer-promise-v1", "ProducerPromise", "ProducerPromiseEnvelope", "ProducerPromise"}, ContractIdentity{SemanticAPIVersionV1, "ProducerPromise", "producer-commitment"}},
	{bodyDefinition{"producer-task-binding-v1", "ProducerTaskBinding", "ProducerTaskBindingEnvelope", "ProducerTaskBinding"}, ContractIdentity{SemanticAPIVersionV1, "ProducerTaskBinding", "contribution-task"}},
	{bodyDefinition{"producer-completion-assertion-v1", "ProducerCompletionAssertion", "ProducerCompletionAssertionEnvelope", "ProducerCompletionAssertion"}, ContractIdentity{SemanticAPIVersionV1, "ProducerCompletionAssertion", "producer-claim"}},
	{bodyDefinition{"claim-admission-v1", "ClaimAdmission", "ClaimAdmissionEnvelope", "ClaimAdmission"}, ContractIdentity{SemanticAPIVersionV1, "ClaimAdmission", "functional-acceptance"}},
	{bodyDefinition{"functional-acceptance-v1", "FunctionalAcceptanceVerdict", "FunctionalAcceptanceVerdictEnvelope", "FunctionalAcceptanceVerdict"}, ContractIdentity{SemanticAPIVersionV1, "FunctionalAcceptanceVerdict", "contribution"}},
	{bodyDefinition{"acceptance-authority-seal-v1", "AcceptanceAuthoritySeal", "AcceptanceAuthoritySealEnvelope", "AcceptanceAuthoritySeal"}, ContractIdentity{SemanticAPIVersionV1, "AcceptanceAuthoritySeal", "verifier-authority"}},
}

// New captures and admits an immutable engine authority.  The variadic form
// keeps both New() and New(Options{...}) available without a second constructor
// spelling.
func New(options ...Options) (*Registry, error) {
	if len(options) > 1 {
		return nil, failure(CodeInvalidArgument, OperationHandshake, "", "", "", "options-cardinality")
	}
	var opts Options
	if len(options) == 1 {
		opts = options[0]
	}
	if opts.MaxBytes != 0 && opts.MaxBytes != DefaultMaxBytes {
		return nil, failure(CodeInvalidArgument, OperationHandshake, "", "", "", "max-bytes-authority")
	}
	if opts.MaxDepth != 0 && opts.MaxDepth != DefaultMaxDepth {
		return nil, failure(CodeInvalidArgument, OperationHandshake, "", "", "", "max-depth-authority")
	}
	if opts.MaxBytes == 0 {
		opts.MaxBytes = DefaultMaxBytes
	}
	if opts.MaxDepth == 0 {
		opts.MaxDepth = DefaultMaxDepth
	}
	if strings.TrimPrefix(runtime.Version(), "go") != "1.26.5" {
		return nil, failure(CodeAuthority, OperationHandshake, "", "public-contract-v1.json", "", "pinned-go-toolchain")
	}
	if linkedCUEGoVersion() != CUEGoModuleVersion {
		return nil, failure(CodeAuthority, OperationHandshake, "", "public-contract-v1.json", "", "pinned-cue-toolchain")
	}
	contractDocument, contractBytes, contractSHA256, err := readPublicContract()
	if err != nil || validatePublicContract(contractDocument) != nil {
		return nil, failure(CodeAuthority, OperationHandshake, "", "public-contract-v1.json", "", "public-contract")
	}
	authority := opts.Authority
	if authority == nil {
		authority = engineauthority.Authority
	}
	snapshot, files, digest, err := captureAuthority(authority)
	if err != nil {
		return nil, err
	}

	ctx := cuecontext.New()
	var catalogDocument struct {
		Schema         string                `json:"schema"`
		Reconstruction CatalogReconstruction `json:"reconstruction"`
		Rows           []CatalogEntry        `json:"rows"`
	}
	if err := json.Unmarshal(files[catalogAuthorityPath], &catalogDocument); err != nil {
		return nil, failure(CodeAuthority, OperationHandshake, "", "catalog", "", "catalog-decode")
	}
	if catalogDocument.Schema != "ai-org-durable-body-coverage-catalog-v1" {
		return nil, failure(CodeAuthority, OperationHandshake, "", "catalog", "", "catalog-schema")
	}
	if catalogDocument.Reconstruction.ExpectedSites != 138 || catalogDocument.Reconstruction.AdmittedSites != 138 || catalogDocument.Reconstruction.PendingSites != 0 {
		return nil, failure(CodeAuthority, OperationHandshake, "", "catalog", "", "catalog-reconstruction")
	}
	catalog := catalogDocument.Rows
	contexts, report, err := validateCatalog(catalog)
	if err != nil {
		return nil, err
	}
	sort.Slice(catalog, func(i, j int) bool { return catalog[i].SiteID < catalog[j].SiteID })

	schemaValue, buildErr := cueload.BuildEngineFileV2(ctx, snapshot, schemaAuthorityPath)
	if buildErr != nil {
		return nil, failure(CodeAuthority, OperationHandshake, SemanticContextV1, "", "", "schema-cue")
	}
	semantic := schemaValue.LookupPath(cue.MakePath(cue.Def("SemanticStatus")))
	envelope := schemaValue.LookupPath(cue.MakePath(cue.Def("SemanticStatusEnvelope")))
	if !semantic.Exists() || semantic.Err() != nil || !envelope.Exists() || envelope.Err() != nil {
		return nil, failure(CodeAuthority, OperationHandshake, SemanticContextV1, "", "", "schema-definition")
	}
	bodySchemas := map[string]cue.Value{SemanticContextV1: semantic}
	envelopes := map[string]cue.Value{SemanticContextV1: envelope}
	contractsByContext := map[string]ContractIdentity{SemanticContextV1: semanticContractV1()}
	definitions := []bodyDefinition{
		{"request-provenance-custom-ref-v1", "RequestProvenanceRecord", "RequestProvenanceEnvelope", "RequestProvenanceRecord"},
		{"request-outcome-custom-ref-v1", "RequestOutcome", "RequestOutcomeEnvelope", "RequestOutcome"},
		{"patch-series-cover-letter-v1", "PatchSeriesCoverLetter", "PatchSeriesCoverLetterEnvelope", "PatchSeriesCoverLetter"},
		{"root-technical-approach-tree-v1", "RootTechnicalApproachTree", "RootTechnicalApproachTreeEnvelope", "TechnicalApproachTree"},
		{"request-provenance-branch-v1", "BranchRequestProvenanceRecord", "BranchRequestProvenanceEnvelope", "RequestProvenanceRecord"},
		{"direction-review-round-v1", "DirectionReviewRound", "DirectionReviewRoundEnvelope", "DirectionReviewRound"},
		{"synthetic-network-review-record-v1", "DirectionReviewRound", "DirectionReviewRoundEnvelope", "DirectionReviewRound"},
		{"author-response-record-v1", "AuthorResponseRecord", "AuthorResponseRecordEnvelope", "AuthorResponseRecord"},
		{"revision-delta-record-v1", "RevisionDeltaRecord", "RevisionDeltaRecordEnvelope", "RevisionDeltaRecord"},
		{"root-network-node-manifest-v1", "RootNetworkNodeManifest", "RootNetworkNodeManifestEnvelope", "NetworkNodeManifest"},
		{"child-network-node-manifest-v1", "ChildNetworkNodeManifest", "ChildNetworkNodeManifestEnvelope", "NetworkNodeManifest"},
		{"series-coverage-ledger-v1", "SeriesCoverageLedger", "SeriesCoverageLedgerEnvelope", "SeriesCoverageLedger"},
		{"series-scope-decomposition-v1", "SeriesScopeDecomposition", "SeriesScopeDecompositionEnvelope", "SeriesScopeDecomposition"},
		{"patch-queue-status-rollup-v1", "PatchQueueStatusRollup", "PatchQueueStatusRollupEnvelope", "PatchQueueStatusRollup"},
		{"maintainer-series-request-v1", "MaintainerSeriesRequest", "MaintainerSeriesRequestEnvelope", "MaintainerSeriesRequest"},
		{"patch-series-metadata-v1", "PatchSeriesMetadata", "PatchSeriesMetadataEnvelope", "PatchSeriesMetadata"},
		{"child-patch-series-cover-letter-v1", "ChildPatchSeriesCoverLetter", "ChildPatchSeriesCoverLetterEnvelope", "PatchSeriesCoverLetter"},
		{"child-technical-approach-tree-v1", "ChildTechnicalApproachTree", "ChildTechnicalApproachTreeEnvelope", "TechnicalApproachTree"},
		{"lineage-carrier-recipe-v1", "LineageCarrierRecipe", "LineageCarrierRecipeEnvelope", "LineageCarrierRecipe"},
		{"authoring-announcement-root-v1", "AuthoringAnnouncement", "RootAnnouncementEnvelope", "AuthoringAnnouncement"},
		{"authoring-announcement-child-v1", "AuthoringAnnouncement", "ChildAnnouncementEnvelope", "AuthoringAnnouncement"},
		{"patchwork-check-event-stream-v1", "PatchworkCheckEventStream", "PatchworkCheckEventStreamEnvelope", "PatchworkCheckEventStream"},
		{"questions-patch-series-v1", "QuestionsThePatchMustAnswer", "SeriesQuestionsEnvelope", "QuestionsThePatchMustAnswer"},
		{"questions-contribution-v1", "QuestionsThePatchMustAnswer", "ContributionQuestionsEnvelope", "QuestionsThePatchMustAnswer"},
		{"implementation-experience-report-v1", "ImplementationExperienceReport", "ImplementationExperienceReportEnvelope", "ImplementationExperienceReport"},
		{"implementation-result-contract-v1", "ImplementationResultContract", "ImplementationResultContractEnvelope", "ImplementationResultContract"},
		{"implementation-result-v1", "ImplementationResult", "ImplementationResultEnvelope", "ImplementationResult"},
		{"functional-check-carrier-recipe-v1", "FunctionalCheckCarrierRecipe", "FunctionalCheckCarrierRecipeEnvelope", "FunctionalCheckCarrierRecipe"},
	}
	for _, registration := range producerLifecycleCompatibilityMatrix {
		definitions = append(definitions, registration.definition)
	}
	for _, definition := range definitions {
		body := schemaValue.LookupPath(cue.MakePath(cue.Def(definition.body)))
		envelope := schemaValue.LookupPath(cue.MakePath(cue.Def(definition.envelope)))
		bodyValid := body.Err() == nil
		if producerLifecycleContext(definition.contextID) {
			// Required non-empty lifecycle lists intentionally report incomplete
			// through Value.Err before a body is supplied. Validate still rejects
			// real schema bottoms while preserving those required transitions.
			bodyValid = body.Validate() == nil
		}
		if !body.Exists() || !bodyValid || !envelope.Exists() || envelope.Err() != nil {
			return nil, failure(CodeAuthority, OperationHandshake, definition.contextID, "", "", "schema-definition")
		}
		bodySchemas[definition.contextID] = body
		envelopes[definition.contextID] = envelope
		variant := "request-outcome-custom-ref"
		if definition.contextID == "patch-series-cover-letter-v1" || definition.contextID == "root-technical-approach-tree-v1" {
			variant = "patch-series-root"
		}
		if definition.contextID == "request-provenance-branch-v1" {
			variant = "patch-series-branch"
		}
		if strings.Contains(definition.contextID, "review") || strings.Contains(definition.contextID, "response") || strings.Contains(definition.contextID, "revision-delta") {
			variant = "review-reform-cohort"
		}
		switch definition.contextID {
		case "root-network-node-manifest-v1", "series-coverage-ledger-v1":
			variant = "network-root"
		case "series-scope-decomposition-v1":
			variant = "patch-series-root-preview"
		case "child-network-node-manifest-v1", "maintainer-series-request-v1":
			variant = "network-child"
		case "patch-queue-status-rollup-v1":
			variant = "network-control-read"
		case "patch-series-metadata-v1":
			variant = "network-root-and-child"
		case "child-patch-series-cover-letter-v1", "child-technical-approach-tree-v1":
			variant = "patch-series-child"
		case "lineage-carrier-recipe-v1":
			variant = "network-publication"
		case "authoring-announcement-root-v1":
			variant = "root-announcement"
		case "authoring-announcement-child-v1":
			variant = "child-announcement"
		case "patchwork-check-event-stream-v1":
			variant = "network-node"
		case "questions-patch-series-v1":
			variant = "patch-series"
		case "questions-contribution-v1":
			variant = "contribution"
		case "implementation-experience-report-v1":
			variant = "patch-series"
		case "implementation-result-contract-v1":
			variant = "contributor-contract"
		case "implementation-result-v1":
			variant = "harness-authored"
		case "functional-check-carrier-recipe-v1":
			variant = "contributor-handoff"
		}
		identity := ContractIdentity{APIVersion: SemanticAPIVersionV1, Kind: definition.kind, Variant: variant}
		if registration, ok := producerLifecycleRegistrationFor(definition.contextID); ok {
			identity = registration.contract
		}
		contractsByContext[definition.contextID] = identity
	}

	catalogDigest := sha256.Sum256(files[catalogAuthorityPath])
	definitionDigest := sha256.Sum256(files[schemaAuthorityPath])
	contractDigest := sha256.Sum256(contractBytes)
	manifestDigest := hashManifest(digest, catalogDigest, definitionDigest, contractDigest)
	limits := []LimitPolicy{
		{
			Name: "max_depth", Value: opts.MaxDepth,
			MeasurementDomain: "recursively concrete decoded CUE value depth from a depth-one root",
			SourceKind:        "permitted-production-constant",
			SourceCitation:    "cuecodec/codec.go:23 defaultMaxDepth = 64",
			CheckedSHA256:     rootCodecSourceSHA,
		},
		{
			Name: "max_bytes", Value: opts.MaxBytes,
			MeasurementDomain: "decoded codec input and emitted artifact bytes, excluding process base64 framing",
			SourceKind:        "permitted-production-constant",
			SourceCitation:    "cuecodec/codec.go:24 defaultMaxBytes = 8 << 20",
			CheckedSHA256:     rootCodecSourceSHA,
		},
	}
	contract := ContractInfo{
		ContextID: SemanticContextV1, Contract: semanticContractV1(),
		CanonicalFormat:     CanonicalFormatV1,
		CanonicalCoordinate: "refs/notes/ai-org/semantic-status/{target_oid}",
		NoteTargetSource:    "frozen branch head object ID", IdentitySource: "context",
		DefinitionPath:     schemaAuthorityPath + ":#SemanticStatus",
		DefinitionSHA256:   hex.EncodeToString(definitionDigest[:]),
		ProjectionProfiles: []string{ConsumerJSONProfileV1},
		SchemaProfiles:     []string{CodexSchemaProfileV1},
		HistoricalWriters:  []string{"semantic-status-json-v0"},
		HistoricalAliases:  []string{"semantic-status-json-v0@refs/notes/ai-org/semantic-status/{target_oid}"},
		Limits:             limits, Manifest: SemanticManifestV1,
	}
	contracts := []ContractInfo{contract}
	for _, contextID := range []string{"request-provenance-custom-ref-v1", "request-outcome-custom-ref-v1"} {
		identity := contractsByContext[contextID]
		contracts = append(contracts, ContractInfo{
			ContextID: contextID, Contract: identity, CanonicalFormat: CanonicalFormatV1,
			CanonicalCoordinate: "refs/ai-org/request-outcomes/{request_id}",
			IdentitySource:      "context", DefinitionPath: schemaAuthorityPath,
			DefinitionSHA256:   hex.EncodeToString(definitionDigest[:]),
			ProjectionProfiles: []string{ConsumerJSONProfileV1},
			SchemaProfiles:     []string{CodexSchemaProfileV1},
			HistoricalWriters:  []string{"request-outcome-json-v0"},
			HistoricalAliases:  []string{"patch-series-request-provenance.json", "patch-series-request-outcome.json"},
			Limits:             limits, Manifest: "request-outcome-cohort-v1",
		})
	}
	for _, contextID := range []string{"patch-series-cover-letter-v1", "request-provenance-branch-v1"} {
		identity := contractsByContext[contextID]
		contracts = append(contracts, ContractInfo{
			ContextID: contextID, Contract: identity, CanonicalFormat: CanonicalFormatV1,
			CanonicalCoordinate: "refs/heads/ai-org/patch-series/{patch_series_id}",
			IdentitySource:      "context", DefinitionPath: schemaAuthorityPath,
			DefinitionSHA256:   hex.EncodeToString(definitionDigest[:]),
			ProjectionProfiles: []string{ConsumerJSONProfileV1}, SchemaProfiles: []string{CodexSchemaProfileV1},
			HistoricalWriters: []string{"patch-series-json-v0"},
			HistoricalAliases: []string{"patch-series-cover-letter.json", "patch-series-request-provenance.json"},
			Limits:            limits, Manifest: "patch-series-root-cohort-v1",
		})
	}
	rootApproachIdentity := contractsByContext["root-technical-approach-tree-v1"]
	contracts = append(contracts, ContractInfo{
		ContextID: "root-technical-approach-tree-v1", Contract: rootApproachIdentity,
		CanonicalFormat:     CanonicalFormatV1,
		CanonicalCoordinate: "refs/heads/ai-org/patch-series/{patch_series_id}:technical-approach-plan.cue",
		IdentitySource:      "context",
		DefinitionPath:      schemaAuthorityPath + ":#RootTechnicalApproachTree",
		DefinitionSHA256:    hex.EncodeToString(definitionDigest[:]),
		ProjectionProfiles:  []string{ConsumerJSONProfileV1},
		SchemaProfiles:      []string{},
		HistoricalWriters:   []string{"technical-approach-json-v0"},
		HistoricalAliases:   []string{"technical-approach-plan.json"},
		Limits:              limits,
		Manifest:            "patch-series-root-cohort-v2",
	})
	scopeIdentity := contractsByContext["series-scope-decomposition-v1"]
	contracts = append(contracts, ContractInfo{
		ContextID: "series-scope-decomposition-v1", Contract: scopeIdentity,
		CanonicalFormat:     CanonicalFormatV1,
		CanonicalCoordinate: "refs/heads/ai-org/patch-series/{patch_series_id}:series-scope-decomposition.cue",
		IdentitySource:      "context",
		DefinitionPath:      schemaAuthorityPath + ":#SeriesScopeDecomposition",
		DefinitionSHA256:    hex.EncodeToString(definitionDigest[:]),
		ProjectionProfiles:  []string{ConsumerJSONProfileV1},
		SchemaProfiles:      []string{},
		HistoricalWriters:   []string{},
		HistoricalAliases:   []string{},
		Limits:              limits,
		Manifest:            "series-scope-decomposition-preview-v1",
	})
	for _, contextID := range []string{"direction-review-round-v1", "synthetic-network-review-record-v1", "author-response-record-v1", "revision-delta-record-v1"} {
		identity := contractsByContext[contextID]
		contracts = append(contracts, ContractInfo{
			ContextID: contextID, Contract: identity, CanonicalFormat: CanonicalFormatV1,
			CanonicalCoordinate: "refs/heads/ai-org/patch-series/{patch_series_id}/patch-series-review-rounds",
			IdentitySource:      "context", DefinitionPath: schemaAuthorityPath,
			DefinitionSHA256:   hex.EncodeToString(definitionDigest[:]),
			ProjectionProfiles: []string{ConsumerJSONProfileV1}, SchemaProfiles: []string{CodexSchemaProfileV1},
			HistoricalWriters: []string{"review-reform-json-v0"},
			HistoricalAliases: []string{"patch-series-review-rounds/*.json"},
			Limits:            limits, Manifest: "review-reform-cohort-v1",
		})
	}
	for _, contextID := range []string{"root-network-node-manifest-v1", "child-network-node-manifest-v1", "series-coverage-ledger-v1", "patch-queue-status-rollup-v1", "maintainer-series-request-v1", "patch-series-metadata-v1", "child-patch-series-cover-letter-v1", "child-technical-approach-tree-v1", "lineage-carrier-recipe-v1"} {
		identity := contractsByContext[contextID]
		schemaProfiles := []string{CodexSchemaProfileV1}
		if contextID == "child-technical-approach-tree-v1" {
			// Child approaches may contain a nested patch-plan slice that exceeds
			// the strict Codex structured-output depth budget. They remain fully
			// admitted for emit/parse/project, while Splitter owns its shallower
			// CUE-native generation schema.
			schemaProfiles = []string{}
		}
		contracts = append(contracts, ContractInfo{
			ContextID: contextID, Contract: identity, CanonicalFormat: CanonicalFormatV1,
			CanonicalCoordinate: "refs/heads/ai-org/patch-series/{patch_series_id}",
			IdentitySource:      "context", DefinitionPath: schemaAuthorityPath,
			DefinitionSHA256:   hex.EncodeToString(definitionDigest[:]),
			ProjectionProfiles: []string{ConsumerJSONProfileV1}, SchemaProfiles: schemaProfiles,
			HistoricalWriters: []string{"network-json-v0"},
			HistoricalAliases: []string{"series-coverage-ledger.json", "patch-queue-status-rollup.json", "patch-series-manifest.json", "sub/*/*.json"},
			Limits:            limits, Manifest: "network-publication-cohort-v1",
		})
	}
	for _, contextID := range []string{"authoring-announcement-root-v1", "authoring-announcement-child-v1"} {
		identity := contractsByContext[contextID]
		contracts = append(contracts, ContractInfo{
			ContextID: contextID, Contract: identity, CanonicalFormat: CanonicalFormatV1,
			CanonicalCoordinate: "refs/ai-org/authoring-announcements/v2/{coordinate_sha256}",
			IdentitySource:      "context", DefinitionPath: schemaAuthorityPath,
			DefinitionSHA256:   hex.EncodeToString(definitionDigest[:]),
			ProjectionProfiles: []string{ConsumerJSONProfileV1}, SchemaProfiles: []string{CodexSchemaProfileV1},
			HistoricalWriters: []string{"authoring-announcement-json-v1"},
			HistoricalAliases: []string{"refs/ai-org/authoring-announcements/{series_id}/{author_slug}"},
			Limits:            limits, Manifest: "authoring-announcement-cohort-v1",
		})
	}
	identity := contractsByContext["patchwork-check-event-stream-v1"]
	contracts = append(contracts, ContractInfo{
		ContextID: "patchwork-check-event-stream-v1", Contract: identity, CanonicalFormat: CanonicalFormatV1,
		CanonicalCoordinate: "refs/heads/ai-org/patch-series/{patch_series_id}:{node_path}/patchwork-check-event-stream.cue",
		IdentitySource:      "context", DefinitionPath: schemaAuthorityPath,
		DefinitionSHA256:   hex.EncodeToString(definitionDigest[:]),
		ProjectionProfiles: []string{ConsumerJSONProfileV1}, SchemaProfiles: []string{CodexSchemaProfileV1},
		HistoricalWriters: []string{"legacy-jsonl-event-import"}, HistoricalAliases: []string{"patchwork-check-events.jsonl"},
		Limits: limits, Manifest: "patchwork-check-event-stream-cohort-v1",
	})
	for _, registration := range producerLifecycleCompatibilityMatrix {
		contextID := registration.definition.contextID
		identity := registration.contract
		coordinate := "refs/heads/ai-org/contrib/{producer_coordinate}"
		if contextID == "acceptance-authority-seal-v1" {
			coordinate = "refs/notes/ai-org/acceptance-authority/{verdict_oid}"
		}
		contracts = append(contracts, ContractInfo{
			ContextID: contextID, Contract: identity, CanonicalFormat: CanonicalFormatV1,
			CanonicalCoordinate: coordinate, IdentitySource: "context",
			DefinitionPath: schemaAuthorityPath + ":#" + identity.Kind, DefinitionSHA256: hex.EncodeToString(definitionDigest[:]),
			ProjectionProfiles: []string{ConsumerJSONProfileV1}, SchemaProfiles: []string{},
			HistoricalWriters: []string{}, HistoricalAliases: []string{},
			Limits: limits, Manifest: ProducerLifecycleManifestV1,
		})
	}
	inspection := RegistryInspection{
		PublicContract:       PublicContractV1,
		PublicContractSHA256: contractSHA256,
		AuthoritySHA256:      hex.EncodeToString(digest[:]),
		CatalogSHA256:        hex.EncodeToString(catalogDigest[:]),
		ManifestSHA256:       hex.EncodeToString(manifestDigest[:]),
		CatalogSchema:        catalogDocument.Schema,
		Reconstruction:       catalogDocument.Reconstruction,
		Catalog:              cloneCatalog(catalog), Coverage: report,
		Contracts: contracts,
	}
	return &Registry{
		authority: authority, authorityFiles: files,
		authoritySHA256:  inspection.AuthoritySHA256,
		contractSHA256:   inspection.PublicContractSHA256,
		catalogSHA256:    inspection.CatalogSHA256,
		manifestSHA256:   inspection.ManifestSHA256,
		definitionSHA256: contract.DefinitionSHA256,
		maxDepth:         opts.MaxDepth, maxBytes: opts.MaxBytes,
		ctx: ctx, semanticSchema: semantic, semanticEnvelope: envelope,
		bodySchemas: bodySchemas, envelopes: envelopes, contractsByContext: contractsByContext,
		catalog: catalog, contexts: contexts, inspection: inspection,
	}, nil
}

func producerLifecycleContext(contextID string) bool {
	_, ok := producerLifecycleRegistrationFor(contextID)
	return ok
}

func producerLifecycleRegistrationFor(contextID string) (producerLifecycleRegistration, bool) {
	for _, registration := range producerLifecycleCompatibilityMatrix {
		if registration.definition.contextID == contextID {
			return registration, true
		}
	}
	return producerLifecycleRegistration{}, false
}

func validateProducerLifecycleCompatibilityMatrix(
	matrix []producerLifecycleRegistration,
	contexts map[string]string,
	contextCohorts map[string]string,
) error {
	if len(matrix) != 6 {
		return failure(CodeAuthority, OperationHandshake, "", "catalog", "", "producer-lifecycle-matrix")
	}
	registeredContexts := make(map[string]bool, len(matrix))
	registeredBodies := make(map[string]bool, len(matrix))
	registeredEnvelopes := make(map[string]bool, len(matrix))
	registeredKinds := make(map[string]bool, len(matrix))
	for _, registration := range matrix {
		definition := registration.definition
		contract := registration.contract
		if definition.contextID == "" || definition.body == "" || definition.envelope == "" || definition.kind == "" ||
			contract.APIVersion != SemanticAPIVersionV1 || contract.Kind != definition.kind || contract.Variant == "" ||
			registeredContexts[definition.contextID] || registeredBodies[definition.body] ||
			registeredEnvelopes[definition.envelope] || registeredKinds[definition.kind] {
			return failure(CodeAuthority, OperationHandshake, definition.contextID, "catalog", "", "producer-lifecycle-matrix")
		}
		registeredContexts[definition.contextID] = true
		registeredBodies[definition.body] = true
		registeredEnvelopes[definition.envelope] = true
		registeredKinds[definition.kind] = true
		if contexts[definition.contextID] != "admitted" || contextCohorts[definition.contextID] != producerLifecycleCohort {
			return failure(CodeAuthority, OperationHandshake, definition.contextID, "catalog", "", "producer-lifecycle-matrix")
		}
	}
	for contextID, cohort := range contextCohorts {
		if cohort == producerLifecycleCohort && !registeredContexts[contextID] {
			return failure(CodeAuthority, OperationHandshake, contextID, "catalog", "", "producer-lifecycle-matrix")
		}
	}
	return nil
}

func captureAuthority(authority fs.FS) (repository.EngineSourceSnapshotV2, map[string][]byte, [sha256.Size]byte, error) {
	snapshot, err := repository.CaptureEngineSourceV2(authority, authorityPaths[:])
	if err != nil {
		return repository.EngineSourceSnapshotV2{}, nil, [sha256.Size]byte{}, failure(CodeAuthority, OperationHandshake, "", "", "", "authority-unavailable")
	}
	files := make(map[string][]byte, len(authorityPaths))
	for _, name := range authorityPaths {
		content, ok := snapshot.Content(name)
		if !ok {
			return repository.EngineSourceSnapshotV2{}, nil, [sha256.Size]byte{}, failure(CodeAuthority, OperationHandshake, "", name, "", "authority-unavailable")
		}
		files[name] = content
	}
	return snapshot, files, [sha256.Size]byte(snapshot.SHA256), nil
}

func validateCatalog(catalog []CatalogEntry) (map[string]string, CoverageReport, error) {
	if len(catalog) != 138 {
		return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, "", "catalog", "", "catalog-cardinality")
	}
	contexts := make(map[string]string)
	contextCohorts := make(map[string]string)
	sites := make(map[string]bool)
	selectors := make(map[SourceSelector]bool)
	report := CoverageReport{RegisteredSites: len(catalog), MappedSites: len(catalog)}
	for _, row := range catalog {
		if row.SiteID == "" || row.ContextID == "" || row.Cohort == "" || row.SourceSelector.Path == "" ||
			path.Clean(row.SourceSelector.Path) != row.SourceSelector.Path || !strings.HasPrefix(row.SourceSelector.Path, "ai_org/") ||
			!strings.HasSuffix(row.SourceSelector.Path, ".py") || row.SourceSelector.Scope == "" ||
			(row.SourceSelector.NodeKind != "call" && row.SourceSelector.NodeKind != "function") ||
			row.SourceSelector.Operation == "" || row.SourceSelector.Occurrence < 1 || sites[row.SiteID] {
			return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, row.ContextID, "catalog", "", "catalog-row")
		}
		sites[row.SiteID] = true
		if selectors[row.SourceSelector] {
			return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, row.ContextID, "catalog", "", "catalog-ambiguous")
		}
		selectors[row.SourceSelector] = true
		if row.Disposition != "admitted" && row.Disposition != "pending" {
			return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, row.ContextID, "catalog", "", "catalog-disposition")
		}
		if existing, ok := contexts[row.ContextID]; ok && existing != row.Disposition {
			return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, row.ContextID, "catalog", "", "catalog-ambiguous")
		}
		if existing, ok := contextCohorts[row.ContextID]; ok && existing != row.Cohort {
			return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, row.ContextID, "catalog", "", "catalog-ambiguous")
		}
		contexts[row.ContextID] = row.Disposition
		contextCohorts[row.ContextID] = row.Cohort
		if row.Disposition == "admitted" {
			report.AdmittedSites++
			allowed := map[string]CatalogContract{
				SemanticContextV1:                     {APIVersion: SemanticAPIVersionV1, Kind: SemanticKindV1, Variant: SemanticVariantV1},
				"request-provenance-custom-ref-v1":    {APIVersion: SemanticAPIVersionV1, Kind: "RequestProvenanceRecord", Variant: "request-outcome-custom-ref"},
				"request-outcome-custom-ref-v1":       {APIVersion: SemanticAPIVersionV1, Kind: "RequestOutcome", Variant: "request-outcome-custom-ref"},
				"patch-series-cover-letter-v1":        {APIVersion: SemanticAPIVersionV1, Kind: "PatchSeriesCoverLetter", Variant: "patch-series-root"},
				"root-technical-approach-tree-v1":     {APIVersion: SemanticAPIVersionV1, Kind: "TechnicalApproachTree", Variant: "patch-series-root"},
				"request-provenance-branch-v1":        {APIVersion: SemanticAPIVersionV1, Kind: "RequestProvenanceRecord", Variant: "patch-series-branch"},
				"direction-review-round-v1":           {APIVersion: SemanticAPIVersionV1, Kind: "DirectionReviewRound", Variant: "review-reform-cohort"},
				"synthetic-network-review-record-v1":  {APIVersion: SemanticAPIVersionV1, Kind: "DirectionReviewRound", Variant: "review-reform-cohort"},
				"author-response-record-v1":           {APIVersion: SemanticAPIVersionV1, Kind: "AuthorResponseRecord", Variant: "review-reform-cohort"},
				"revision-delta-record-v1":            {APIVersion: SemanticAPIVersionV1, Kind: "RevisionDeltaRecord", Variant: "review-reform-cohort"},
				"root-network-node-manifest-v1":       {APIVersion: SemanticAPIVersionV1, Kind: "NetworkNodeManifest", Variant: "network-root"},
				"child-network-node-manifest-v1":      {APIVersion: SemanticAPIVersionV1, Kind: "NetworkNodeManifest", Variant: "network-child"},
				"series-coverage-ledger-v1":           {APIVersion: SemanticAPIVersionV1, Kind: "SeriesCoverageLedger", Variant: "network-root"},
				"series-scope-decomposition-v1":       {APIVersion: SemanticAPIVersionV1, Kind: "SeriesScopeDecomposition", Variant: "patch-series-root-preview"},
				"patch-queue-status-rollup-v1":        {APIVersion: SemanticAPIVersionV1, Kind: "PatchQueueStatusRollup", Variant: "network-control-read"},
				"maintainer-series-request-v1":        {APIVersion: SemanticAPIVersionV1, Kind: "MaintainerSeriesRequest", Variant: "network-child"},
				"patch-series-metadata-v1":            {APIVersion: SemanticAPIVersionV1, Kind: "PatchSeriesMetadata", Variant: "network-root-and-child"},
				"child-patch-series-cover-letter-v1":  {APIVersion: SemanticAPIVersionV1, Kind: "PatchSeriesCoverLetter", Variant: "patch-series-child"},
				"child-technical-approach-tree-v1":    {APIVersion: SemanticAPIVersionV1, Kind: "TechnicalApproachTree", Variant: "patch-series-child"},
				"lineage-carrier-recipe-v1":           {APIVersion: SemanticAPIVersionV1, Kind: "LineageCarrierRecipe", Variant: "network-publication"},
				"authoring-announcement-root-v1":      {APIVersion: SemanticAPIVersionV1, Kind: "AuthoringAnnouncement", Variant: "root-announcement"},
				"authoring-announcement-child-v1":     {APIVersion: SemanticAPIVersionV1, Kind: "AuthoringAnnouncement", Variant: "child-announcement"},
				"patchwork-check-event-stream-v1":     {APIVersion: SemanticAPIVersionV1, Kind: "PatchworkCheckEventStream", Variant: "network-node"},
				"questions-patch-series-v1":           {APIVersion: SemanticAPIVersionV1, Kind: "QuestionsThePatchMustAnswer", Variant: "patch-series"},
				"questions-contribution-v1":           {APIVersion: SemanticAPIVersionV1, Kind: "QuestionsThePatchMustAnswer", Variant: "contribution"},
				"implementation-experience-report-v1": {APIVersion: SemanticAPIVersionV1, Kind: "ImplementationExperienceReport", Variant: "patch-series"},
				"implementation-result-contract-v1":   {APIVersion: SemanticAPIVersionV1, Kind: "ImplementationResultContract", Variant: "contributor-contract"},
				"implementation-result-v1":            {APIVersion: SemanticAPIVersionV1, Kind: "ImplementationResult", Variant: "harness-authored"},
				"functional-check-carrier-recipe-v1":  {APIVersion: SemanticAPIVersionV1, Kind: "FunctionalCheckCarrierRecipe", Variant: "contributor-handoff"},
			}
			for _, registration := range producerLifecycleCompatibilityMatrix {
				identity := registration.contract
				allowed[registration.definition.contextID] = CatalogContract{
					APIVersion: identity.APIVersion,
					Kind:       identity.Kind,
					Variant:    identity.Variant,
				}
			}
			want, admittedContext := allowed[row.ContextID]
			if !admittedContext || !row.Executable || row.Contract == nil ||
				row.Contract.APIVersion != want.APIVersion || row.Contract.Kind != want.Kind || row.Contract.Variant != want.Variant {
				return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, row.ContextID, "catalog", "", "catalog-admission")
			}
		} else {
			return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, row.ContextID, "catalog", "", "catalog-pending")
		}
	}
	if report.AdmittedSites != 138 || report.PendingSites != 0 {
		return nil, CoverageReport{}, failure(CodeAuthority, OperationHandshake, "", "catalog", "", "catalog-cohort-count")
	}
	if err := validateProducerLifecycleCompatibilityMatrix(
		producerLifecycleCompatibilityMatrix[:], contexts, contextCohorts,
	); err != nil {
		return nil, CoverageReport{}, err
	}
	return contexts, report, nil
}

func hashManifest(authority, catalog, definition, publicContract [sha256.Size]byte) [sha256.Size]byte {
	h := sha256.New()
	_, _ = io.WriteString(h, "cuecodec/semantic-status-cohort-manifest/v1\x00")
	_, _ = h.Write(authority[:])
	_, _ = h.Write(catalog[:])
	_, _ = h.Write(definition[:])
	_, _ = h.Write(publicContract[:])
	_, _ = io.WriteString(h, SemanticContextV1+"\x00"+SemanticAPIVersionV1+"\x00"+SemanticKindV1+"\x00"+SemanticVariantV1)
	var digest [sha256.Size]byte
	copy(digest[:], h.Sum(nil))
	return digest
}

func (r *Registry) verifyAuthority(operation Operation, contextID string) error {
	if r == nil || r.authority == nil {
		return failure(CodeInvalidArgument, operation, contextID, "", "", "nil-registry")
	}
	_, files, digest, err := captureAuthority(r.authority)
	if err != nil {
		return failure(CodeAuthority, operation, contextID, "", "", "authority-changed")
	}
	if hex.EncodeToString(digest[:]) != r.authoritySHA256 {
		return failure(CodeAuthority, operation, contextID, "", "", "authority-changed")
	}
	for name, expected := range r.authorityFiles {
		if !bytes.Equal(files[name], expected) {
			return failure(CodeAuthority, operation, contextID, name, "", "authority-changed")
		}
	}
	return nil
}

func (r *Registry) validContext(ctx context.Context, operation Operation, contextID string) error {
	if r == nil {
		return failure(CodeInvalidArgument, operation, contextID, "", "", "nil-registry")
	}
	if ctx == nil || ctx.Err() != nil {
		return failure(CodeInvalidArgument, operation, contextID, "", "", "context")
	}
	return nil
}

func (r *Registry) resolve(ctx context.Context, operation Operation, contextID string) error {
	if err := r.validContext(ctx, operation, contextID); err != nil {
		return err
	}
	if contextID == "" {
		return failure(CodeInvalidArgument, operation, contextID, "", "", "context-id")
	}
	disposition, ok := r.contexts[contextID]
	if !ok {
		return failure(CodeInvalidArgument, operation, contextID, "", "", "context-unknown")
	}
	if disposition != "admitted" {
		return failure(CodeNotAdmitted, operation, contextID, "", "", "context-not-admitted")
	}
	if err := r.verifyAuthority(operation, contextID); err != nil {
		return err
	}
	return nil
}

func (r *Registry) handshake() HandshakeInfo {
	return HandshakeInfo{
		Protocol: ProtocolV1, PublicContract: PublicContractV1,
		PublicContractSHA256: r.contractSHA256,
		Module:               ModulePath, GoRelease: strings.TrimPrefix(runtime.Version(), "go"),
		CUEGo: linkedCUEGoVersion(), CUELanguage: CUELanguageVersion,
		AuthoritySHA256: r.authoritySHA256, CatalogSHA256: r.catalogSHA256,
		ManifestSHA256: r.manifestSHA256,
	}
}

func linkedCUEGoVersion() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return ""
	}
	// Go test binaries intentionally omit dependency module records from their
	// build info. The production command is still verified from its real module
	// record; tests separately pin go.mod and the executable handshake.
	if strings.HasSuffix(info.Path, ".test") {
		return CUEGoModuleVersion
	}
	for _, dependency := range info.Deps {
		if dependency.Path != "cuelang.org/go" {
			continue
		}
		if dependency.Replace != nil {
			return dependency.Replace.Version
		}
		return dependency.Version
	}
	return ""
}

func cloneInspection(in RegistryInspection) RegistryInspection {
	out := in
	out.Catalog = cloneCatalog(in.Catalog)
	out.Contracts = append([]ContractInfo(nil), in.Contracts...)
	for i := range out.Contracts {
		out.Contracts[i].ProjectionProfiles = append([]string{}, in.Contracts[i].ProjectionProfiles...)
		out.Contracts[i].SchemaProfiles = append([]string{}, in.Contracts[i].SchemaProfiles...)
		out.Contracts[i].HistoricalWriters = append([]string{}, in.Contracts[i].HistoricalWriters...)
		out.Contracts[i].HistoricalAliases = append([]string{}, in.Contracts[i].HistoricalAliases...)
		out.Contracts[i].Limits = append([]LimitPolicy(nil), in.Contracts[i].Limits...)
	}
	return out
}

func cloneCatalog(in []CatalogEntry) []CatalogEntry {
	out := append([]CatalogEntry(nil), in...)
	for i := range out {
		if in[i].Contract != nil {
			contract := *in[i].Contract
			out[i].Contract = &contract
		}
	}
	return out
}

func jsonArtifact(mediaType string, value any) (Artifact, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return Artifact{}, err
	}
	return newArtifact(mediaType, encoded), nil
}

func newArtifact(mediaType string, data []byte) Artifact {
	digest := sha256.Sum256(data)
	return Artifact{
		MediaType: mediaType, ByteLength: len(data),
		DataBase64: encodeBase64(data), SHA256: hex.EncodeToString(digest[:]),
	}
}
