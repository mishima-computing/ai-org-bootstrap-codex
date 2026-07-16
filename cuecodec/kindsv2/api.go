// Package kindsv2 is the AI Org engine-body registry and codec boundary.  It
// is intentionally a sibling package: the frozen github.com/mishima-computing/cuecodec
// root-v1 API, authority roots, error table, corpus, and golden bytes are not
// changed by engine-body admission.
package kindsv2

import (
	"context"
	"encoding/base64"
	"io/fs"

	"cuelang.org/go/cue"
)

const (
	ProtocolV1                  = "cuecodec-process-v1"
	PublicContractV1            = "engine-codec-public-contract-v1"
	CanonicalFormatV1           = "final-cue-text-v1"
	ConsumerJSONProfileV1       = "consumer-json-v1"
	CodexSchemaProfileV1        = "codex-structured-output-v1"
	SemanticContextV1           = "semantic-status-note-v1"
	SemanticAPIVersionV1        = "ai-org-cue-body-v1"
	SemanticKindV1              = "SemanticStatus"
	SemanticVariantV1           = "git-note"
	DefaultMaxDepth             = 64
	DefaultMaxBytes             = 8 << 20
	ModulePath                  = "github.com/mishima-computing/cuecodec"
	CUEGoModuleVersion          = "v0.17.0"
	CUELanguageVersion          = "v0.17.0"
	ProducerLifecycleManifestV1 = "producer-lifecycle-contract-matrix-v1"
	SemanticManifestV1          = "semantic-status-cohort-v1"
)

type Operation string

const (
	OperationHandshake Operation = "handshake"
	OperationInspect   Operation = "inspect"
	OperationEmit      Operation = "emit"
	OperationParse     Operation = "parse"
	OperationVet       Operation = "vet"
	OperationProject   Operation = "project"
	OperationSchema    Operation = "schema"
)

type ContractIdentity struct {
	APIVersion string `json:"api_version"`
	Kind       string `json:"kind"`
	Variant    string `json:"variant"`
}

func semanticContractV1() ContractIdentity {
	return ContractIdentity{
		APIVersion: SemanticAPIVersionV1,
		Kind:       SemanticKindV1,
		Variant:    SemanticVariantV1,
	}
}

type CatalogEntry struct {
	SiteID         string           `json:"site_id"`
	ContextID      string           `json:"context_id"`
	Cohort         string           `json:"cohort"`
	Disposition    string           `json:"disposition"`
	Executable     bool             `json:"executable"`
	Contract       *CatalogContract `json:"contract"`
	SourceSelector SourceSelector   `json:"source_selector"`
}

type CatalogContract struct {
	APIVersion string `json:"apiVersion"`
	Kind       string `json:"kind"`
	Variant    string `json:"variant"`
}

type SourceSelector struct {
	Path       string `json:"path"`
	Scope      string `json:"scope"`
	NodeKind   string `json:"node_kind"`
	Operation  string `json:"operation"`
	Occurrence int    `json:"occurrence"`
}

type CatalogReconstruction struct {
	OriginalGroundTruth string `json:"original_ground_truth"`
	Basis               string `json:"basis"`
	Scanner             string `json:"scanner"`
	ExpectedSites       int    `json:"expected_sites"`
	AdmittedSites       int    `json:"admitted_sites"`
	PendingSites        int    `json:"pending_sites"`
	Policy              string `json:"policy"`
}

type CoverageReport struct {
	RegisteredSites       int `json:"registered_sites"`
	MappedSites           int `json:"mapped_sites"`
	AdmittedSites         int `json:"admitted_sites"`
	PendingSites          int `json:"pending_sites"`
	MissingRows           int `json:"missing_rows"`
	AmbiguousRows         int `json:"ambiguous_rows"`
	OrphanedSites         int `json:"orphaned_sites"`
	AdmittedScopeBypasses int `json:"admitted_scope_bypasses"`
}

type LimitPolicy struct {
	Name              string `json:"name"`
	Value             int    `json:"value"`
	MeasurementDomain string `json:"measurement_domain"`
	SourceKind        string `json:"source_kind"`
	SourceCitation    string `json:"source_citation"`
	CheckedSHA256     string `json:"checked_sha256"`
}

type ContractInfo struct {
	ContextID           string           `json:"context_id"`
	Contract            ContractIdentity `json:"contract"`
	CanonicalFormat     string           `json:"canonical_format"`
	CanonicalCoordinate string           `json:"canonical_coordinate"`
	NoteTargetSource    string           `json:"note_target_source"`
	IdentitySource      string           `json:"identity_source"`
	DefinitionPath      string           `json:"definition_path"`
	DefinitionSHA256    string           `json:"definition_sha256"`
	ProjectionProfiles  []string         `json:"projection_profiles"`
	SchemaProfiles      []string         `json:"schema_profiles"`
	HistoricalWriters   []string         `json:"historical_writers"`
	HistoricalAliases   []string         `json:"historical_aliases"`
	Limits              []LimitPolicy    `json:"limits"`
	Manifest            string           `json:"manifest"`
}

type RegistryInspection struct {
	PublicContract       string                `json:"public_contract"`
	PublicContractSHA256 string                `json:"public_contract_sha256"`
	AuthoritySHA256      string                `json:"authority_sha256"`
	CatalogSHA256        string                `json:"catalog_sha256"`
	ManifestSHA256       string                `json:"manifest_sha256"`
	CatalogSchema        string                `json:"catalog_schema"`
	Reconstruction       CatalogReconstruction `json:"reconstruction"`
	Catalog              []CatalogEntry        `json:"catalog"`
	Coverage             CoverageReport        `json:"coverage"`
	Contracts            []ContractInfo        `json:"contracts"`
}

type HandshakeInfo struct {
	Protocol             string `json:"protocol"`
	PublicContract       string `json:"public_contract"`
	PublicContractSHA256 string `json:"public_contract_sha256"`
	Module               string `json:"module"`
	GoRelease            string `json:"go_release"`
	CUEGo                string `json:"cuelang_go"`
	CUELanguage          string `json:"cue_language"`
	AuthoritySHA256      string `json:"authority_sha256"`
	CatalogSHA256        string `json:"catalog_sha256"`
	ManifestSHA256       string `json:"manifest_sha256"`
}

// Artifact is an immutable protocol artifact encoded explicitly rather than
// relying on encoding/json's implicit []byte behavior.
type Artifact struct {
	MediaType  string `json:"media_type"`
	ByteLength int    `json:"byte_length"`
	DataBase64 string `json:"data_base64"`
	SHA256     string `json:"sha256"`
}

func (a Artifact) Bytes() ([]byte, error) {
	return base64.StdEncoding.DecodeString(a.DataBase64)
}

type Options struct {
	// Authority replaces the embedded authority only for conformance tests and
	// verified bundles.  A lifecycle target repository is never accepted here.
	Authority fs.FS
	MaxDepth  int
	MaxBytes  int
}

// Registry is an immutable admitted snapshot of the embedded engine
// authority.  Its fields remain private so pending catalog rows cannot become
// executable through caller mutation.
type Registry struct {
	authority          fs.FS
	authorityFiles     map[string][]byte
	authoritySHA256    string
	contractSHA256     string
	catalogSHA256      string
	manifestSHA256     string
	definitionSHA256   string
	maxDepth           int
	maxBytes           int
	ctx                *cue.Context
	semanticSchema     cue.Value
	semanticEnvelope   cue.Value
	bodySchemas        map[string]cue.Value
	envelopes          map[string]cue.Value
	contractsByContext map[string]ContractIdentity
	catalog            []CatalogEntry
	contexts           map[string]string
	inspection         RegistryInspection
}

func (r *Registry) Catalog() []CatalogEntry {
	if r == nil {
		return nil
	}
	return cloneCatalog(r.catalog)
}

func (r *Registry) Coverage() CoverageReport {
	if r == nil {
		return CoverageReport{}
	}
	return r.inspection.Coverage
}

func (r *Registry) Inspect(ctx context.Context) (RegistryInspection, error) {
	if err := r.validContext(ctx, OperationInspect, ""); err != nil {
		return RegistryInspection{}, err
	}
	if err := r.verifyAuthority(OperationInspect, ""); err != nil {
		return RegistryInspection{}, err
	}
	return cloneInspection(r.inspection), nil
}

func (r *Registry) Handshake(ctx context.Context) (HandshakeInfo, error) {
	if err := r.validContext(ctx, OperationHandshake, ""); err != nil {
		return HandshakeInfo{}, err
	}
	if err := r.verifyAuthority(OperationHandshake, ""); err != nil {
		return HandshakeInfo{}, err
	}
	return r.handshake(), nil
}
