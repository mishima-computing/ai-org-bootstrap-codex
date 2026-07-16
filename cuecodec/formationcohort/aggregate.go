// Package formationcohort assembles the citation-bearing Technical Approach
// formation cohort. It is intentionally separate from kindsv2 so the frozen
// root codec API and its authority roots remain unchanged.
package formationcohort

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"

	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

const (
	APIVersion = "ai-org-cue-body-v1"
	BuilderID  = "ai-org-go-formation-v1"
)

var carrierOrder = [...]string{
	"technical-approach",
	"domain-specification",
	"spine-art-bible",
	"asset-manifest",
}

var stageOrder = [...]string{
	"promotion",
	"targeted_reform",
	"review",
	"network",
	"contributor_loading",
}

var carrierKinds = map[string]string{
	"technical-approach":   "TechnicalApproachTree",
	"domain-specification": "DomainSpecificationTable",
	"spine-art-bible":      "SpineArtBible",
	"asset-manifest":       "AssetManifestContract",
}

var carrierContextStems = map[string]string{
	"technical-approach":   "technical-approach-tree",
	"domain-specification": "domain-specification-table",
	"spine-art-bible":      "spine-art-bible",
	"asset-manifest":       "asset-manifest-contract",
}

// Contracts returns the independent source and target contract registry in
// cohort order. The returned slice is detached from package state.
func Contracts() []Contract {
	contracts := make([]Contract, 0, len(carrierOrder)*2)
	for _, id := range carrierOrder {
		kind := carrierKinds[id]
		stem := carrierContextStems[id]
		contracts = append(contracts,
			Contract{Context: stem + "-source-v1", APIVersion: APIVersion, Kind: kind, Variant: "formation-source"},
			Contract{Context: stem + "-target-v1", APIVersion: APIVersion, Kind: kind, Variant: "formation-target"},
		)
	}
	return contracts
}

// ContractsFor returns the independently registered source and target
// contracts for one formation carrier. Keeping this lookup at the registry
// boundary prevents lifecycle consumers from deriving contract pairing from
// flat-slice offsets or from a carrier's untrusted input fields.
func ContractsFor(carrierID string) (Contract, Contract, error) {
	index := indexOfCarrier(carrierID)
	if index < 0 {
		return Contract{}, Contract{}, &Error{CarrierID: carrierID, Rule: "contract-unknown"}
	}
	contracts := Contracts()
	return contracts[index*2], contracts[index*2+1], nil
}

type Contract struct {
	Context    string `json:"context"`
	APIVersion string `json:"apiVersion"`
	Kind       string `json:"kind"`
	Variant    string `json:"variant"`
}

type Citation struct {
	ID  string `json:"id"`
	URL string `json:"url"`
}

type GuardResult struct {
	Measured          bool     `json:"measured"`
	RequiresFragments bool     `json:"requires_fragments"`
	FragmentIDs       []string `json:"fragment_ids"`
}

// CarrierInvocationRecipe is the independently typed source-to-target
// contract for one carrier invocation.  It binds the complete established
// value, regardless of whether that value crosses the process boundary whole
// or as measured shallow fragments.
type CarrierInvocationRecipe struct {
	CarrierID     string   `json:"carrier_id"`
	Source        Contract `json:"source"`
	Target        Contract `json:"target"`
	ContentSHA256 string   `json:"content_sha256"`
}

// CarrierFragmentRecipe is the independently typed projection recipe.  It is
// deliberately separate from CarrierInvocationRecipe so fragment transport
// cannot weaken or replace the source-to-target contract.
type CarrierFragmentRecipe struct {
	CarrierID string            `json:"carrier_id"`
	Mode      string            `json:"mode"`
	Guard     GuardResult       `json:"guard"`
	Fragments []json.RawMessage `json:"fragments,omitempty"`
}

type Carrier struct {
	ID           string     `json:"id"`
	Source       Contract   `json:"source"`
	Target       Contract   `json:"target"`
	DerivationID string     `json:"derivation_id"`
	Links        []string   `json:"links"`
	Citations    []Citation `json:"citations"`
	BuilderID    string     `json:"builder_id"`
	// ContentSHA256 binds this invocation to the complete carrier value that
	// assembly must establish. For ordered fragments it is the digest of the
	// assembled object, not a digest of the transport framing or one fragment.
	ContentSHA256 string            `json:"content_sha256"`
	FragmentMode  string            `json:"fragment_mode"`
	Guard         GuardResult       `json:"guard"`
	Content       json.RawMessage   `json:"content,omitempty"`
	Fragments     []json.RawMessage `json:"fragments,omitempty"`
}

// InvocationRecipe returns a detached, independently typed invocation
// contract.  Callers may retain or modify the returned value without changing
// the carrier later assembled by Go.
func (c Carrier) InvocationRecipe() CarrierInvocationRecipe {
	return CarrierInvocationRecipe{
		CarrierID: c.ID, Source: c.Source, Target: c.Target,
		ContentSHA256: c.ContentSHA256,
	}
}

// FragmentRecipe returns a detached projection recipe, including detached
// guard identities and fragment bytes.
func (c Carrier) FragmentRecipe() CarrierFragmentRecipe {
	fragments := make([]json.RawMessage, len(c.Fragments))
	for index, fragment := range c.Fragments {
		fragments[index] = append(json.RawMessage(nil), fragment...)
	}
	return CarrierFragmentRecipe{
		CarrierID: c.ID,
		Mode:      c.FragmentMode,
		Guard: GuardResult{
			Measured: c.Guard.Measured, RequiresFragments: c.Guard.RequiresFragments,
			FragmentIDs: append([]string(nil), c.Guard.FragmentIDs...),
		},
		Fragments: fragments,
	}
}

type StageReceipt struct {
	Stage      string   `json:"stage"`
	CarrierIDs []string `json:"carrier_ids"`
	BuilderID  string   `json:"builder_id"`
}

// StageProjection is the validated, detached cohort presented to one
// lifecycle consumer. Every stage receives the complete carrier set; only
// LoadContributor is allowed to select one carrier from the aggregate.
type StageProjection struct {
	Stage     string               `json:"stage"`
	BuilderID string               `json:"builder_id"`
	Carriers  []EstablishedCarrier `json:"carriers"`
}

type Request struct {
	Carriers []Carrier      `json:"carriers"`
	Stages   []StageReceipt `json:"stages"`
}

type EstablishedCarrier struct {
	ID            string          `json:"id"`
	Source        Contract        `json:"source"`
	Target        Contract        `json:"target"`
	DerivationID  string          `json:"derivation_id"`
	Links         []string        `json:"links"`
	Citations     []Citation      `json:"citations"`
	BuilderID     string          `json:"builder_id"`
	Content       json.RawMessage `json:"content"`
	ContentSHA256 string          `json:"content_sha256"`
	FragmentMode  string          `json:"fragment_mode"`
	Guard         GuardResult     `json:"schema_guard_result"`
}

type Aggregate struct {
	Schema             string               `json:"schema"`
	BuilderID          string               `json:"builder_id"`
	Carriers           []EstablishedCarrier `json:"carriers"`
	CarrierSHA256      []string             `json:"carrier_sha256"`
	SchemaGuardResults []GuardResult        `json:"schema_guard_results"`
	Stages             []StageReceipt       `json:"stages"`
	Validated          bool                 `json:"validated"`
	SHA256             string               `json:"sha256"`
	TemporaryArtifacts []string             `json:"temporary_artifacts"`
}

type Error struct {
	CarrierID string `json:"carrier_id,omitempty"`
	Rule      string `json:"rule"`
}

func (e *Error) Error() string {
	if e.CarrierID == "" {
		return "formation-cohort:" + e.Rule
	}
	return fmt.Sprintf("formation-cohort:%s:%s", e.CarrierID, e.Rule)
}

// AssembleJSON is the closed process boundary for a serialized formation
// cohort request. Parsing is deliberately separate from Assemble: strict JSON
// rejects duplicate coordinates, while DisallowUnknownFields keeps the CUE
// frame's closed carrier and contract definitions closed at the Go boundary.
// No aggregate is returned unless both decoding and complete assembly pass.
func AssembleJSON(source []byte) (Aggregate, error) {
	root, err := strictjson.Parse(source)
	if err != nil || root.Kind != strictjson.Object {
		return Aggregate{}, &Error{Rule: "request-json"}
	}
	decoder := json.NewDecoder(bytes.NewReader(source))
	decoder.DisallowUnknownFields()
	var request Request
	if err := decoder.Decode(&request); err != nil {
		return Aggregate{}, &Error{Rule: "request-contract"}
	}
	return Assemble(request)
}

// Assemble validates the entire cohort before returning any aggregate. All
// assembly is in-memory, so success and failure leave no temporary artifact.
func Assemble(request Request) (Aggregate, error) {
	if len(request.Carriers) != len(carrierOrder) {
		return Aggregate{}, &Error{Rule: "cohort-complete"}
	}
	if err := validateStages(request.Stages); err != nil {
		return Aggregate{}, err
	}
	established := make([]EstablishedCarrier, 0, len(carrierOrder))
	for index, id := range carrierOrder {
		carrier := request.Carriers[index]
		if err := validateCarrier(carrier, id); err != nil {
			return Aggregate{}, err
		}
		content, err := assembleContent(carrier)
		if err != nil {
			return Aggregate{}, err
		}
		digest := sha256.Sum256(content)
		contentSHA256 := hex.EncodeToString(digest[:])
		if carrier.ContentSHA256 != contentSHA256 {
			return Aggregate{}, &Error{CarrierID: carrier.ID, Rule: "source-digest"}
		}
		established = append(established, EstablishedCarrier{
			ID: carrier.ID, Source: carrier.Source, Target: carrier.Target,
			DerivationID: carrier.DerivationID, Links: append([]string(nil), carrier.Links...),
			Citations: append([]Citation(nil), carrier.Citations...), BuilderID: carrier.BuilderID,
			Content: content, ContentSHA256: contentSHA256, FragmentMode: carrier.FragmentMode,
			Guard: cloneGuardResult(carrier.Guard),
		})
	}
	aggregate := Aggregate{
		Schema: "ai-org-formation-carrier-aggregate-v1", BuilderID: BuilderID,
		Carriers: established, CarrierSHA256: carrierDigests(established),
		SchemaGuardResults: carrierGuardResults(established),
		Stages:             cloneStages(request.Stages), Validated: true, TemporaryArtifacts: []string{},
	}
	digest, err := aggregateDigest(aggregate)
	if err != nil {
		return Aggregate{}, &Error{Rule: "aggregate-json"}
	}
	aggregate.SHA256 = digest
	return aggregate, nil
}

func carrierGuardResults(carriers []EstablishedCarrier) []GuardResult {
	result := make([]GuardResult, len(carriers))
	for index, carrier := range carriers {
		result[index] = cloneGuardResult(carrier.Guard)
	}
	return result
}

func cloneGuardResult(guard GuardResult) GuardResult {
	guard.FragmentIDs = append([]string(nil), guard.FragmentIDs...)
	return guard
}

func carrierDigests(carriers []EstablishedCarrier) []string {
	result := make([]string, len(carriers))
	for index, carrier := range carriers {
		result[index] = carrier.ContentSHA256
	}
	return result
}

func aggregateDigest(aggregate Aggregate) (string, error) {
	aggregate.SHA256 = ""
	encoded, err := json.Marshal(aggregate)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}

func validateStages(stages []StageReceipt) error {
	if len(stages) != len(stageOrder) {
		return &Error{Rule: "lifecycle-complete"}
	}
	wantIDs := carrierOrder[:]
	for index, wantStage := range stageOrder {
		receipt := stages[index]
		if receipt.Stage != wantStage || receipt.BuilderID != BuilderID || len(receipt.CarrierIDs) != len(wantIDs) {
			return &Error{Rule: "lifecycle-order"}
		}
		for carrierIndex, wantID := range wantIDs {
			if receipt.CarrierIDs[carrierIndex] != wantID {
				return &Error{Rule: "lifecycle-cohort-split"}
			}
		}
	}
	return nil
}

func cloneStages(stages []StageReceipt) []StageReceipt {
	result := make([]StageReceipt, len(stages))
	for index, stage := range stages {
		result[index] = stage
		result[index].CarrierIDs = append([]string(nil), stage.CarrierIDs...)
	}
	return result
}

// ProjectStage validates the established aggregate before projecting the
// complete cohort to promotion, targeted reform, review, network, or
// contributor-loading orchestration. The returned carriers do not alias the
// established aggregate.
func ProjectStage(aggregate Aggregate, stage string) (StageProjection, error) {
	if err := ValidateAggregate(aggregate); err != nil {
		return StageProjection{}, err
	}
	known := false
	for _, candidate := range stageOrder {
		if candidate == stage {
			known = true
			break
		}
	}
	if !known {
		return StageProjection{}, &Error{Rule: "lifecycle-stage"}
	}
	carriers := make([]EstablishedCarrier, len(aggregate.Carriers))
	for index, carrier := range aggregate.Carriers {
		carriers[index] = cloneEstablishedCarrier(carrier)
	}
	return StageProjection{Stage: stage, BuilderID: BuilderID, Carriers: carriers}, nil
}

// LoadContributor returns one independently validated carrier from an already
// established aggregate without reconstructing or mutating the cohort.
func LoadContributor(aggregate Aggregate, id string) (EstablishedCarrier, error) {
	if err := ValidateAggregate(aggregate); err != nil {
		return EstablishedCarrier{}, err
	}
	for _, carrier := range aggregate.Carriers {
		if carrier.ID == id {
			return cloneEstablishedCarrier(carrier), nil
		}
	}
	return EstablishedCarrier{}, &Error{CarrierID: id, Rule: "contributor-unknown"}
}

// ValidateAggregate preflights the complete established cohort without
// selecting a contributor. Review and network transitions use this boundary
// to verify the same aggregate that contributor loading later consumes.
func ValidateAggregate(aggregate Aggregate) error {
	if aggregate.Schema != "ai-org-formation-carrier-aggregate-v1" || aggregate.BuilderID != BuilderID ||
		!aggregate.Validated || len(aggregate.Carriers) != len(carrierOrder) ||
		len(aggregate.CarrierSHA256) != len(carrierOrder) || len(aggregate.SchemaGuardResults) != len(carrierOrder) ||
		aggregate.TemporaryArtifacts == nil ||
		len(aggregate.TemporaryArtifacts) != 0 {
		return &Error{Rule: "aggregate-not-established"}
	}
	if err := validateStages(aggregate.Stages); err != nil {
		return err
	}
	contracts := Contracts()
	for index, carrier := range aggregate.Carriers {
		if carrier.ID != carrierOrder[index] || carrier.Source != contracts[index*2] || carrier.Target != contracts[index*2+1] ||
			carrier.DerivationID == "" || len(carrier.Links) == 0 || len(carrier.Citations) == 0 || carrier.BuilderID != BuilderID {
			return &Error{CarrierID: carrier.ID, Rule: "aggregate-carrier"}
		}
		for _, link := range carrier.Links {
			if link == "" {
				return &Error{CarrierID: carrier.ID, Rule: "provenance"}
			}
		}
		for _, citation := range carrier.Citations {
			if citation.ID == "" || len(citation.URL) < len("https://") || citation.URL[:len("https://")] != "https://" {
				return &Error{CarrierID: carrier.ID, Rule: "citation"}
			}
		}
		content, err := canonicalObject(carrier.ID, carrier.Content)
		if err != nil || !bytes.Equal(content, carrier.Content) {
			return &Error{CarrierID: carrier.ID, Rule: "aggregate-content"}
		}
		digest := sha256.Sum256(content)
		wantDigest := hex.EncodeToString(digest[:])
		if carrier.ContentSHA256 != wantDigest || aggregate.CarrierSHA256[index] != wantDigest {
			return &Error{CarrierID: carrier.ID, Rule: "aggregate-digest"}
		}
		if err := validateEstablishedGuard(carrier.ID, carrier.FragmentMode, carrier.Guard); err != nil {
			return err
		}
		if !equalGuardResult(carrier.Guard, aggregate.SchemaGuardResults[index]) {
			return &Error{CarrierID: carrier.ID, Rule: "schema-guard-evidence"}
		}
	}
	wantDigest, err := aggregateDigest(aggregate)
	if err != nil || aggregate.SHA256 != wantDigest {
		return &Error{Rule: "aggregate-digest"}
	}
	return nil
}

func cloneEstablishedCarrier(carrier EstablishedCarrier) EstablishedCarrier {
	carrier.Links = append([]string(nil), carrier.Links...)
	carrier.Citations = append([]Citation(nil), carrier.Citations...)
	carrier.Content = append(json.RawMessage(nil), carrier.Content...)
	carrier.Guard = cloneGuardResult(carrier.Guard)
	return carrier
}

func validateEstablishedGuard(id, mode string, guard GuardResult) error {
	switch mode {
	case "complete":
		if guard.RequiresFragments || len(guard.FragmentIDs) != 0 {
			return &Error{CarrierID: id, Rule: "schema-guard-evidence"}
		}
	case "ordered_shallow":
		if !guard.Measured || !guard.RequiresFragments || len(guard.FragmentIDs) == 0 {
			return &Error{CarrierID: id, Rule: "schema-guard-evidence"}
		}
		seen := make(map[string]struct{}, len(guard.FragmentIDs))
		for _, fragmentID := range guard.FragmentIDs {
			if fragmentID == "" {
				return &Error{CarrierID: id, Rule: "schema-guard-evidence"}
			}
			if _, duplicate := seen[fragmentID]; duplicate {
				return &Error{CarrierID: id, Rule: "schema-guard-evidence"}
			}
			seen[fragmentID] = struct{}{}
		}
	default:
		return &Error{CarrierID: id, Rule: "schema-guard-evidence"}
	}
	return nil
}

func equalGuardResult(left, right GuardResult) bool {
	if left.Measured != right.Measured || left.RequiresFragments != right.RequiresFragments ||
		len(left.FragmentIDs) != len(right.FragmentIDs) {
		return false
	}
	for index := range left.FragmentIDs {
		if left.FragmentIDs[index] != right.FragmentIDs[index] {
			return false
		}
	}
	return true
}

func validateCarrier(carrier Carrier, wantID string) error {
	if carrier.ID != wantID {
		return &Error{CarrierID: carrier.ID, Rule: "carrier-order"}
	}
	if err := validateInvocationRecipe(carrier.InvocationRecipe(), wantID); err != nil {
		return err
	}
	if carrier.DerivationID == "" || carrier.BuilderID != BuilderID || len(carrier.Links) == 0 || len(carrier.Citations) == 0 {
		return &Error{CarrierID: carrier.ID, Rule: "provenance"}
	}
	for _, link := range carrier.Links {
		if link == "" {
			return &Error{CarrierID: carrier.ID, Rule: "provenance"}
		}
	}
	for _, citation := range carrier.Citations {
		if citation.ID == "" || len(citation.URL) < len("https://") || citation.URL[:len("https://")] != "https://" {
			return &Error{CarrierID: carrier.ID, Rule: "citation"}
		}
	}
	return nil
}

func validateInvocationRecipe(recipe CarrierInvocationRecipe, wantID string) error {
	if recipe.CarrierID != wantID {
		return &Error{CarrierID: recipe.CarrierID, Rule: "carrier-order"}
	}
	source, target, err := ContractsFor(wantID)
	if err != nil || recipe.Source != source || recipe.Target != target {
		return &Error{CarrierID: recipe.CarrierID, Rule: "independent-contracts"}
	}
	if !validSHA256(recipe.ContentSHA256) {
		return &Error{CarrierID: recipe.CarrierID, Rule: "source-digest"}
	}
	return nil
}

func validateFragmentRecipe(recipe CarrierFragmentRecipe) error {
	switch recipe.Mode {
	case "complete":
		if recipe.Guard.RequiresFragments || len(recipe.Guard.FragmentIDs) != 0 || len(recipe.Fragments) != 0 {
			return &Error{CarrierID: recipe.CarrierID, Rule: "complete-content"}
		}
	case "ordered_shallow":
		if !recipe.Guard.Measured || !recipe.Guard.RequiresFragments || len(recipe.Fragments) == 0 ||
			len(recipe.Fragments) != len(recipe.Guard.FragmentIDs) {
			return &Error{CarrierID: recipe.CarrierID, Rule: "fragment-guard"}
		}
	default:
		return &Error{CarrierID: recipe.CarrierID, Rule: "fragment-mode"}
	}
	return nil
}

func validSHA256(value string) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	// Keep the Go process boundary identical to #SHA256 in frame.cue. The CUE
	// contract admits canonical lower-case hexadecimal only; accepting an
	// upper-case spelling here would create two invocation coordinates for the
	// same digest and let a value pass Go that cannot inhabit the formal frame.
	for _, digit := range value {
		if digit >= 'A' && digit <= 'F' {
			return false
		}
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func indexOfCarrier(id string) int {
	for index, candidate := range carrierOrder {
		if candidate == id {
			return index
		}
	}
	return -1
}

func assembleContent(carrier Carrier) (json.RawMessage, error) {
	if err := validateFragmentRecipe(carrier.FragmentRecipe()); err != nil {
		return nil, err
	}
	switch carrier.FragmentMode {
	case "complete":
		// A measured report may still select complete mode, but it cannot carry
		// fragment identities: those identities are invocation evidence only
		// when the measured guard actually requires ordered shallow fragments.
		if len(carrier.Content) == 0 {
			return nil, &Error{CarrierID: carrier.ID, Rule: "complete-content"}
		}
		return canonicalObject(carrier.ID, carrier.Content)
	case "ordered_shallow":
		if len(carrier.Content) != 0 {
			return nil, &Error{CarrierID: carrier.ID, Rule: "fragment-guard"}
		}
		members := map[string]json.RawMessage{}
		fragmentIDs := map[string]struct{}{}
		for index, fragment := range carrier.Fragments {
			fragmentID := carrier.Guard.FragmentIDs[index]
			if fragmentID == "" {
				return nil, &Error{CarrierID: carrier.ID, Rule: "fragment-identity"}
			}
			if _, duplicate := fragmentIDs[fragmentID]; duplicate {
				return nil, &Error{CarrierID: carrier.ID, Rule: "fragment-identity"}
			}
			fragmentIDs[fragmentID] = struct{}{}
			object, err := decodeObject(carrier.ID, fragment)
			if err != nil {
				return nil, err
			}
			for key, value := range object {
				if _, duplicate := members[key]; duplicate {
					return nil, &Error{CarrierID: carrier.ID, Rule: "fragment-overlap"}
				}
				members[key] = value
			}
		}
		return marshalObject(members)
	default:
		return nil, &Error{CarrierID: carrier.ID, Rule: "fragment-mode"}
	}
}

func canonicalObject(id string, source []byte) (json.RawMessage, error) {
	object, err := decodeObject(id, source)
	if err != nil {
		return nil, err
	}
	return marshalObject(object)
}

func decodeObject(id string, source []byte) (map[string]json.RawMessage, error) {
	root, err := strictjson.Parse(source)
	if err != nil || root.Kind != strictjson.Object {
		return nil, &Error{CarrierID: id, Rule: "content-object"}
	}
	var object map[string]json.RawMessage
	decoder := json.NewDecoder(bytes.NewReader(source))
	decoder.UseNumber()
	if err := decoder.Decode(&object); err != nil {
		return nil, &Error{CarrierID: id, Rule: "content-json"}
	}
	return object, nil
}

func marshalObject(object map[string]json.RawMessage) (json.RawMessage, error) {
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var buffer bytes.Buffer
	buffer.WriteByte('{')
	for index, key := range keys {
		if index != 0 {
			buffer.WriteByte(',')
		}
		encodedKey, _ := json.Marshal(key)
		buffer.Write(encodedKey)
		buffer.WriteByte(':')
		buffer.Write(object[key])
	}
	buffer.WriteByte('}')
	if _, err := strictjson.Parse(buffer.Bytes()); err != nil {
		return nil, errors.New("formation-cohort:aggregate-content")
	}
	return append(json.RawMessage(nil), buffer.Bytes()...), nil
}
