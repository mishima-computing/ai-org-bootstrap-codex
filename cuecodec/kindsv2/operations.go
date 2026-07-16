package kindsv2

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"strings"
	"unicode/utf8"

	"cuelang.org/go/cue"
	cueerrors "cuelang.org/go/cue/errors"
	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

type parsedSemantic struct {
	body       []byte
	canonical  []byte
	historical bool
}

const (
	patchworkCheckContextV1 = "patchwork-check-event-stream-v1"
	maxPatchworkStreamBytes = 4 << 20
)

// Emit admits the exact consumer JSON projection and emits canonical CUE.
func (r *Registry) Emit(ctx context.Context, contextID string, projection []byte) (Artifact, error) {
	const operation = OperationEmit
	if err := r.resolve(ctx, operation, contextID); err != nil {
		return Artifact{}, err
	}
	body, _, err := r.admitSemanticBody(operation, contextID, projection)
	if err != nil {
		return Artifact{}, err
	}
	canonical, err := r.canonicalEnvelope(operation, contextID, body)
	if err != nil {
		return Artifact{}, err
	}
	if err := r.verifyAuthority(operation, contextID); err != nil {
		return Artifact{}, err
	}
	return newArtifact("application/cue; profile="+CanonicalFormatV1, canonical), nil
}

// Parse accepts canonical CUE or the registered historical body-only JSON
// writer.  Canonical recognition is definitional: validate and byte-identical
// re-emit first; only a body-only object may enter the legacy importer.
func (r *Registry) Parse(ctx context.Context, contextID string, src []byte) (Artifact, error) {
	parsed, err := r.parse(ctx, OperationParse, contextID, src)
	if err != nil {
		return Artifact{}, err
	}
	return newArtifact("application/json; profile="+ConsumerJSONProfileV1, parsed.body), nil
}

// Project returns the registered disposable body projection.
func (r *Registry) Project(ctx context.Context, contextID, profile string, src []byte) (Artifact, error) {
	if profile == "" {
		profile = ConsumerJSONProfileV1
	}
	if profile != ConsumerJSONProfileV1 {
		return Artifact{}, failure(CodeInvalidArgument, OperationProject, contextID, "", "", "projection-profile")
	}
	parsed, err := r.parse(ctx, OperationProject, contextID, src)
	if err != nil {
		return Artifact{}, err
	}
	// The first cross-contract carrier targets the root cover schema. Validate
	// those projection bytes against the exact guarded schema bytes generated
	// for this invocation. Other contexts keep their registered consumer JSON
	// projection until their own structured-output cohorts are admitted.
	if contextID == "patch-series-cover-letter-v1" {
		if err := r.ValidateProjection(ctx, contextID, parsed.body); err != nil {
			return Artifact{}, err
		}
	}
	return newArtifact("application/json; profile="+profile, parsed.body), nil
}

// Vet validates without releasing a body projection.
func (r *Registry) Vet(ctx context.Context, contextID string, src []byte) error {
	_, err := r.parse(ctx, OperationVet, contextID, src)
	return err
}

func (r *Registry) parse(ctx context.Context, operation Operation, contextID string, src []byte) (parsedSemantic, error) {
	if err := r.resolve(ctx, operation, contextID); err != nil {
		return parsedSemantic{}, err
	}
	if len(src) > r.maxBytes {
		return parsedSemantic{}, failure(CodeInputLimit, operation, contextID, "", "", "max-bytes")
	}
	if !utf8.Valid(src) {
		return parsedSemantic{}, failure(CodeEncoding, operation, contextID, "", "", "utf8")
	}

	// Canonical CUE is intentionally recognized before the historical JSON
	// writer.  The canonical formatter is allowed to use unquoted CUE field
	// labels, so routing all input through a JSON parser would make the codec
	// unable to consume its own output.
	if parsed, recognized, err := r.parseCanonicalEnvelope(operation, contextID, src); recognized {
		return parsed, err
	}

	// The only compatibility input is the registered body-only JSON writer.
	// Keeping this as a strict-JSON path retains duplicate-key and exact-number
	// diagnostics instead of allowing CUE's constraint-merging semantics to
	// reinterpret historical data.
	root, err := parseJSON(operation, contextID, src, r.maxDepth)
	if err != nil {
		return parsedSemantic{}, err
	}
	if looksLikeEnvelope(root) {
		// A strict-JSON envelope that was not recognized above could only have
		// failed CUE compilation.  It is not a historical body and must never
		// fall through to that importer.
		return parsedSemantic{}, failure(CodeSyntax, operation, contextID, "", "", "cue-syntax")
	}

	bodySource, err := encodeStrictJSON(root)
	if err != nil {
		return parsedSemantic{}, failure(CodeProjection, operation, contextID, "", "", "legacy-json")
	}
	body, _, err := r.admitSemanticBody(operation, contextID, bodySource)
	if err != nil {
		return parsedSemantic{}, err
	}
	if err := r.verifyAuthority(operation, contextID); err != nil {
		return parsedSemantic{}, err
	}
	return parsedSemantic{body: body, historical: true}, nil
}

// parseCanonicalEnvelope returns recognized=false only when the input is not
// an engine envelope.  Once any envelope coordinate is present, all failures
// remain on the canonical path and cannot be reinterpreted as legacy JSON.
func (r *Registry) parseCanonicalEnvelope(operation Operation, contextID string, src []byte) (parsedSemantic, bool, error) {
	value := r.ctx.CompileBytes(src, cue.Filename("semantic-status-envelope.cue"))
	if value.Err() != nil {
		return parsedSemantic{}, false, nil
	}

	iter, err := value.Fields(cue.Definitions(false), cue.Hidden(false), cue.Optional(false))
	if err != nil {
		return parsedSemantic{}, false, nil
	}
	want := map[string]bool{"apiVersion": true, "body": true, "kind": true, "variant": true}
	recognized := false
	var extra string
	for iter.Next() {
		name := iter.Selector().Unquoted()
		if want[name] {
			recognized = true
		} else if extra == "" {
			extra = name
		}
	}
	if !recognized {
		return parsedSemantic{}, false, nil
	}
	if extra != "" {
		return parsedSemantic{}, true, failure(CodeIdentity, operation, contextID, extra, strictjson.JoinPointer("", extra), "envelope-field")
	}
	if cueValueExceedsDepth(value, 1, r.maxDepth) {
		return parsedSemantic{}, true, failure(CodeStructuralLimit, operation, contextID, "", "", "max-depth")
	}

	contract, ok := r.contractsByContext[contextID]
	if !ok {
		return parsedSemantic{}, true, failure(CodeNotAdmitted, operation, contextID, "", "", "context-not-admitted")
	}
	for _, identity := range []struct{ name, want string }{
		{"apiVersion", contract.APIVersion},
		{"kind", contract.Kind},
		{"variant", contract.Variant},
	} {
		field := value.LookupPath(cue.MakePath(cue.Str(identity.name)))
		if !field.Exists() {
			return parsedSemantic{}, true, failure(CodeIdentity, operation, contextID, identity.name, strictjson.JoinPointer("", identity.name), "identity-missing")
		}
		got, err := field.String()
		if err != nil || got != identity.want {
			return parsedSemantic{}, true, failure(CodeIdentity, operation, contextID, identity.name, strictjson.JoinPointer("", identity.name), "kind-mismatch")
		}
	}

	bodyValue := value.LookupPath(cue.MakePath(cue.Str("body")))
	if !bodyValue.Exists() {
		return parsedSemantic{}, true, failure(CodeIdentity, operation, contextID, "body", "/body", "identity-missing")
	}
	unified := r.envelopes[contextID].Unify(value)
	if err := unified.Validate(cue.Final()); err != nil {
		return parsedSemantic{}, true, cueFailure(CodeSchemaValidation, operation, contextID, "schema-unification", err)
	}
	if err := unified.Validate(cue.Final(), cue.Concrete(true)); err != nil {
		return parsedSemantic{}, true, cueFailure(CodeIncomplete, operation, contextID, "recursive-concreteness", err)
	}

	bodyJSON, err := bodyValue.MarshalJSON()
	if err != nil {
		return parsedSemantic{}, true, failure(CodeProjection, operation, contextID, "body", "/body", "body-json")
	}
	body, _, err := r.admitSemanticBody(operation, contextID, bodyJSON)
	if err != nil {
		return parsedSemantic{}, true, err
	}
	canonical, err := r.canonicalEnvelope(operation, contextID, body)
	if err != nil {
		return parsedSemantic{}, true, err
	}
	if !bytes.Equal(src, canonical) {
		return parsedSemantic{}, true, failure(CodeNonCanonical, operation, contextID, "", "", CanonicalFormatV1)
	}
	if err := r.verifyAuthority(operation, contextID); err != nil {
		return parsedSemantic{}, true, err
	}
	return parsedSemantic{body: body, canonical: canonical}, true, nil
}

func parseJSON(operation Operation, contextID string, src []byte, maxDepth int) (*strictjson.Node, error) {
	if !utf8.Valid(src) {
		return nil, failure(CodeEncoding, operation, contextID, "", "", "utf8")
	}
	node, err := strictjson.ParseMaxDepth(src, maxDepth)
	if err == nil {
		return node, nil
	}
	if parseErr, ok := err.(*strictjson.Error); ok {
		if parseErr.Code == strictjson.DuplicateKey {
			return nil, failure(CodeDuplicateKey, operation, contextID, "", parseErr.Path, "duplicate-key")
		}
		if parseErr.Code == strictjson.DepthLimit {
			// Keep the established public v2 failure payload unchanged; the
			// internal parser path is used only to enforce the budget before
			// recursive Node allocation.
			return nil, failure(CodeStructuralLimit, operation, contextID, "", "", "max-depth")
		}
		return nil, failure(CodeSyntax, operation, contextID, "", parseErr.Path, "json-syntax")
	}
	return nil, failure(CodeSyntax, operation, contextID, "", "", "json-syntax")
}

func (r *Registry) admitSemanticBody(operation Operation, contextID string, src []byte) ([]byte, cue.Value, error) {
	if len(src) > r.maxBytes {
		return nil, cue.Value{}, failure(CodeInputLimit, operation, contextID, "", "", "max-bytes")
	}
	root, err := parseJSON(operation, contextID, src, r.maxDepth)
	if err != nil {
		return nil, cue.Value{}, err
	}
	if contextID == SemanticContextV1 {
		if err := validateSemanticShape(operation, contextID, root); err != nil {
			return nil, cue.Value{}, err
		}
	} else if contextID == patchworkCheckContextV1 {
		if err := validatePatchworkCheckStream(operation, contextID, root); err != nil {
			return nil, cue.Value{}, err
		}
	} else if root == nil || root.Kind != strictjson.Object {
		return nil, cue.Value{}, failure(CodeSchemaValidation, operation, contextID, "", "", "body-object")
	}
	canonical, err := encodeStrictJSON(root)
	if err != nil {
		return nil, cue.Value{}, failure(CodeProjection, operation, contextID, "", "", "body-json")
	}
	value := r.ctx.CompileBytes(canonical, cue.Filename("semantic-status.json"))
	if value.Err() != nil {
		return nil, cue.Value{}, failure(CodeSyntax, operation, contextID, "", "", "cue-syntax")
	}
	schema, ok := r.bodySchemas[contextID]
	if !ok {
		return nil, cue.Value{}, failure(CodeNotAdmitted, operation, contextID, "", "", "context-not-admitted")
	}
	unified := schema.Unify(value)
	if err := unified.Validate(cue.Final()); err != nil {
		return nil, cue.Value{}, cueFailure(CodeSchemaValidation, operation, contextID, "schema-unification", err)
	}
	if err := unified.Validate(cue.Final(), cue.Concrete(true)); err != nil {
		return nil, cue.Value{}, cueFailure(CodeIncomplete, operation, contextID, "recursive-concreteness", err)
	}
	return canonical, unified, nil
}

func validatePatchworkCheckStream(operation Operation, contextID string, root *strictjson.Node) error {
	if root == nil || root.Kind != strictjson.Object {
		return failure(CodeSchemaValidation, operation, contextID, "", "", "body-object")
	}
	want := map[string]bool{
		"raw_stream_base64": true,
		"source_oid":        true,
		"source_path":       true,
	}
	for _, member := range root.SortedMembers() {
		if !want[member.Name] {
			return failure(
				CodeSchemaValidation, operation, contextID, member.Name,
				strictjson.JoinPointer("", member.Name), "additional-field",
			)
		}
	}
	fields := make(map[string]string, len(want))
	for _, name := range []string{"raw_stream_base64", "source_oid", "source_path"} {
		value, ok := root.Lookup(name)
		if !ok {
			return failure(CodeSchemaValidation, operation, contextID, name, strictjson.JoinPointer("", name), "required-field")
		}
		if value.Kind != strictjson.String {
			return failure(CodeSchemaValidation, operation, contextID, name, strictjson.JoinPointer("", name), "string")
		}
		fields[name] = value.Text
	}
	if !isGitObjectID(fields["source_oid"]) {
		return failure(CodeIdentity, operation, contextID, "source_oid", "/source_oid", "source-oid")
	}
	if !isPatchworkSourcePath(fields["source_path"]) {
		return failure(CodeIdentity, operation, contextID, "source_path", "/source_path", "source-path")
	}
	encoded := fields["raw_stream_base64"]
	decodedSize, ok := base64DecodedSize(encoded)
	if !ok {
		return failure(CodeEncoding, operation, contextID, "raw_stream_base64", "/raw_stream_base64", "rfc4648-base64")
	}
	if decodedSize > maxPatchworkStreamBytes {
		return failure(CodeInputLimit, operation, contextID, "raw_stream_base64", "/raw_stream_base64", "decoded-stream-4-mib")
	}
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return failure(CodeEncoding, operation, contextID, "raw_stream_base64", "/raw_stream_base64", "rfc4648-base64")
	}
	if base64.StdEncoding.EncodeToString(decoded) != encoded {
		return failure(CodeNonCanonical, operation, contextID, "raw_stream_base64", "/raw_stream_base64", "rfc4648-base64")
	}
	return nil
}

func isGitObjectID(value string) bool {
	if len(value) != 40 && len(value) != 64 {
		return false
	}
	for _, character := range []byte(value) {
		if !(character >= '0' && character <= '9') && !(character >= 'a' && character <= 'f') {
			return false
		}
	}
	return true
}

func isPatchworkSourcePath(value string) bool {
	if value == "patchwork-check-event-stream.cue" || value == "patchwork-check-events.jsonl" {
		return true
	}
	const prefix = "sub/"
	if !strings.HasPrefix(value, prefix) {
		return false
	}
	remainder := strings.TrimPrefix(value, prefix)
	key, filename, found := strings.Cut(remainder, "/")
	if !found || (filename != "patchwork-check-event-stream.cue" && filename != "patchwork-check-events.jsonl") {
		return false
	}
	if key == "" {
		return false
	}
	for _, character := range []byte(key) {
		if !(character >= 'a' && character <= 'z') && !(character >= '0' && character <= '9') && character != '_' {
			return false
		}
	}
	return true
}

func base64DecodedSize(encoded string) (int, bool) {
	if len(encoded)%4 != 0 {
		return 0, false
	}
	padding := 0
	for index := len(encoded); index > 0 && encoded[index-1] == '='; index-- {
		padding++
	}
	if padding > 2 {
		return 0, false
	}
	dataEnd := len(encoded) - padding
	for index, character := range []byte(encoded) {
		if index >= dataEnd {
			if character != '=' {
				return 0, false
			}
			continue
		}
		if !(character >= 'A' && character <= 'Z') &&
			!(character >= 'a' && character <= 'z') &&
			!(character >= '0' && character <= '9') &&
			character != '+' && character != '/' {
			return 0, false
		}
	}
	return len(encoded)/4*3 - padding, true
}

func validateSemanticShape(operation Operation, contextID string, root *strictjson.Node) error {
	if root == nil || root.Kind != strictjson.Object {
		return failure(CodeSchemaValidation, operation, contextID, "", "", "body-object")
	}
	want := map[string]bool{"change_kind": true, "subsystem": true, "owner": true, "working_state": true}
	for _, member := range root.SortedMembers() {
		if !want[member.Name] {
			return failure(CodeSchemaValidation, operation, contextID, member.Name, strictjson.JoinPointer("", member.Name), "additional-field")
		}
	}
	for _, name := range []string{"change_kind", "owner", "subsystem", "working_state"} {
		value, ok := root.Lookup(name)
		if !ok {
			return failure(CodeSchemaValidation, operation, contextID, name, strictjson.JoinPointer("", name), "required-field")
		}
		if value.Kind != strictjson.String {
			return failure(CodeSchemaValidation, operation, contextID, name, strictjson.JoinPointer("", name), "string")
		}
	}
	return nil
}

func (r *Registry) canonicalEnvelope(operation Operation, contextID string, body []byte) ([]byte, error) {
	contract, ok := r.contractsByContext[contextID]
	if !ok {
		return nil, failure(CodeNotAdmitted, operation, contextID, "", "", "context-not-admitted")
	}
	src := []byte(`{"apiVersion":"` + contract.APIVersion + `","body":` + string(body) + `,"kind":"` + contract.Kind + `","variant":"` + contract.Variant + `"}`)
	value := r.ctx.CompileBytes(src, cue.Filename("semantic-status-envelope.cue"))
	if value.Err() != nil {
		return nil, failure(CodeSyntax, operation, contextID, "", "", "cue-syntax")
	}
	unified := r.envelopes[contextID].Unify(value)
	if err := unified.Validate(cue.Final()); err != nil {
		return nil, cueFailure(CodeSchemaValidation, operation, contextID, "schema-unification", err)
	}
	if err := unified.Validate(cue.Final(), cue.Concrete(true)); err != nil {
		return nil, cueFailure(CodeIncomplete, operation, contextID, "recursive-concreteness", err)
	}
	carrier, err := unified.MarshalJSON()
	if err != nil {
		return nil, failure(CodeProjection, operation, contextID, "", "", "carrier-json")
	}
	canonical, err := finalCUEText(carrier)
	if err != nil {
		return nil, failure(CodeProjection, operation, contextID, "", "", "cue-format")
	}
	if len(canonical) > r.maxBytes {
		return nil, failure(CodeStructuralLimit, operation, contextID, "", "", "max-bytes")
	}
	return canonical, nil
}

func looksLikeEnvelope(root *strictjson.Node) bool {
	if root == nil || root.Kind != strictjson.Object {
		return false
	}
	for _, name := range []string{"apiVersion", "body", "kind", "variant"} {
		if _, ok := root.Lookup(name); ok {
			return true
		}
	}
	return false
}

func validateEnvelopeIdentity(operation Operation, contextID string, root *strictjson.Node) (*strictjson.Node, error) {
	want := map[string]bool{"apiVersion": true, "body": true, "kind": true, "variant": true}
	for _, member := range root.SortedMembers() {
		if !want[member.Name] {
			return nil, failure(CodeIdentity, operation, contextID, member.Name, strictjson.JoinPointer("", member.Name), "envelope-field")
		}
	}
	identities := []struct{ name, want string }{
		{"apiVersion", SemanticAPIVersionV1}, {"kind", SemanticKindV1}, {"variant", SemanticVariantV1},
	}
	for _, identity := range identities {
		value, ok := root.Lookup(identity.name)
		if !ok {
			return nil, failure(CodeIdentity, operation, contextID, identity.name, strictjson.JoinPointer("", identity.name), "identity-missing")
		}
		if value.Kind != strictjson.String || value.Text != identity.want {
			return nil, failure(CodeIdentity, operation, contextID, identity.name, strictjson.JoinPointer("", identity.name), "kind-mismatch")
		}
	}
	body, ok := root.Lookup("body")
	if !ok {
		return nil, failure(CodeIdentity, operation, contextID, "body", "/body", "identity-missing")
	}
	return body, nil
}

func cueFailure(code ErrorCode, operation Operation, contextID, rule string, err error) *Failure {
	path := ""
	diagnostics := cueerrors.Errors(err)
	if len(diagnostics) > 0 {
		parts := cueerrors.Path(diagnostics[0])
		for i, part := range parts {
			if i > 0 {
				path += "."
			}
			path += part
		}
	}
	return failure(code, operation, contextID, path, "", rule)
}

func (p parsedSemantic) String() string {
	return fmt.Sprintf("historical=%v bytes=%d", p.historical, len(p.body))
}
