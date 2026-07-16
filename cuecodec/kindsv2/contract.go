package kindsv2

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"slices"
)

// publicContractFS keeps the sibling API and protocol contract in the built
// bundle. It is deliberately separate from the engine authority snapshot:
// schema/catalog drift and executable-contract drift have distinct digests.
//
//go:embed public-contract-v1.json
var publicContractFS embed.FS

type publicContractDocument struct {
	Contract   string `json:"contract"`
	ModulePath string `json:"module_path"`
	Package    string `json:"package"`
	GoRelease  string `json:"go_release"`
	Protocol   struct {
		Name       string   `json:"name"`
		Operations []string `json:"operations"`
	} `json:"protocol"`
	Authority struct {
		Snapshot string   `json:"snapshot"`
		Roots    []string `json:"roots"`
	} `json:"authority"`
	AdmittedContract struct {
		ContextID           string   `json:"context_id"`
		APIVersion          string   `json:"api_version"`
		Kind                string   `json:"kind"`
		Variant             string   `json:"variant"`
		CanonicalCoordinate string   `json:"canonical_coordinate"`
		NoteTargetSource    string   `json:"note_target_source"`
		HistoricalAliases   []string `json:"historical_aliases"`
	} `json:"admitted_contract"`
	Limits []struct {
		Name          string `json:"name"`
		Value         int    `json:"value"`
		CheckedSHA256 string `json:"checked_sha256"`
	} `json:"limits"`
	Exports struct {
		Constants []string `json:"constants"`
		Types     []string `json:"types"`
		Functions []string `json:"functions"`
		Methods   []string `json:"methods"`
	} `json:"exports"`
}

func readPublicContract() (publicContractDocument, []byte, string, error) {
	data, err := publicContractFS.ReadFile("public-contract-v1.json")
	if err != nil {
		return publicContractDocument{}, nil, "", err
	}
	var document publicContractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		return publicContractDocument{}, nil, "", err
	}
	digest := sha256.Sum256(data)
	return document, data, hex.EncodeToString(digest[:]), nil
}

func validatePublicContract(document publicContractDocument) error {
	if document.Contract != PublicContractV1 || document.ModulePath != ModulePath || document.Package != "kindsv2" || document.GoRelease != "1.26.5" {
		return fmt.Errorf("contract identity")
	}
	wantOperations := []string{"handshake", "inspect", "emit", "parse", "vet", "project", "schema"}
	if document.Protocol.Name != ProtocolV1 || !slices.Equal(document.Protocol.Operations, wantOperations) {
		return fmt.Errorf("protocol")
	}
	wantRoots := []string{
		"cue/engine/registry/durable-body-catalog-v1.json",
		"cue/engine/schema/semantic_status.cue",
	}
	if document.Authority.Snapshot != "engine-authority-snapshot-v2" || !slices.Equal(document.Authority.Roots, wantRoots) {
		return fmt.Errorf("authority")
	}
	if document.AdmittedContract.ContextID != SemanticContextV1 ||
		document.AdmittedContract.APIVersion != SemanticAPIVersionV1 ||
		document.AdmittedContract.Kind != SemanticKindV1 ||
		document.AdmittedContract.Variant != SemanticVariantV1 ||
		document.AdmittedContract.CanonicalCoordinate != "refs/notes/ai-org/semantic-status/{target_oid}" ||
		document.AdmittedContract.NoteTargetSource != "frozen branch head object ID" ||
		!slices.Equal(document.AdmittedContract.HistoricalAliases, []string{"semantic-status-json-v0@refs/notes/ai-org/semantic-status/{target_oid}"}) {
		return fmt.Errorf("admitted contract")
	}
	wantLimits := map[string]int{"max_bytes": DefaultMaxBytes, "max_depth": DefaultMaxDepth}
	if len(document.Limits) != len(wantLimits) {
		return fmt.Errorf("limits")
	}
	for _, limit := range document.Limits {
		if want, ok := wantLimits[limit.Name]; !ok || limit.Value != want || limit.CheckedSHA256 != rootCodecSourceSHA {
			return fmt.Errorf("limit %q", limit.Name)
		}
		delete(wantLimits, limit.Name)
	}
	if len(wantLimits) != 0 {
		return fmt.Errorf("missing limits")
	}
	wantConstants := []string{
		"CUEGoModuleVersion", "CUELanguageVersion", "CanonicalFormatV1",
		"CodeAuthority", "CodeDuplicateKey", "CodeEncoding", "CodeFraming", "CodeIdentity",
		"CodeIncomplete", "CodeInputLimit", "CodeInvalidArgument", "CodeNonCanonical",
		"CodeNotAdmitted", "CodeProjection", "CodeSchemaGeneration", "CodeSchemaGuard",
		"CodeSchemaValidation", "CodeStructuralLimit", "CodeSyntax", "CodexSchemaProfileV1",
		"ConsumerJSONProfileV1", "DefaultMaxBytes", "DefaultMaxDepth", "ModulePath",
		"OperationEmit", "OperationHandshake", "OperationInspect", "OperationParse",
		"OperationProject", "OperationSchema", "OperationVet", "ProducerLifecycleManifestV1",
		"ProtocolV1", "PublicContractV1",
		"SemanticAPIVersionV1", "SemanticContextV1", "SemanticKindV1", "SemanticManifestV1", "SemanticVariantV1",
	}
	wantTypes := []string{
		"Artifact", "CatalogContract", "CatalogEntry", "CatalogReconstruction", "CodecFailure",
		"ContractInfo", "ContractIdentity", "CoverageReport", "ErrorCode", "Failure", "HandshakeInfo",
		"LimitPolicy", "Operation", "Options", "Registry", "RegistryInspection", "Request", "Response", "SourceSelector",
	}
	wantFunctions := []string{
		"New(...Options) (*Registry, error)",
		"DecodeRequest([]byte) (Request, *Failure)",
	}
	wantMethods := []string{
		"(*Registry).Catalog() []CatalogEntry",
		"(*Registry).Coverage() CoverageReport",
		"(*Registry).Emit(context.Context, string, []byte) (Artifact, error)",
		"(*Registry).Handshake(context.Context) (HandshakeInfo, error)",
		"(*Registry).Handle(context.Context, Request) Response",
		"(*Registry).Inspect(context.Context) (RegistryInspection, error)",
		"(*Registry).Parse(context.Context, string, []byte) (Artifact, error)",
		"(*Registry).Project(context.Context, string, string, []byte) (Artifact, error)",
		"(*Registry).Schema(context.Context, string, string) (Artifact, error)",
		"(*Registry).ValidateProjection(context.Context, string, []byte) error",
		"(*Registry).Vet(context.Context, string, []byte) error",
		"(Artifact).Bytes() ([]byte, error)",
		"(*Failure).Error() string",
	}
	if !slices.Equal(document.Exports.Constants, wantConstants) ||
		!slices.Equal(document.Exports.Types, wantTypes) ||
		!slices.Equal(document.Exports.Functions, wantFunctions) ||
		!slices.Equal(document.Exports.Methods, wantMethods) {
		return fmt.Errorf("exports")
	}
	return nil
}
