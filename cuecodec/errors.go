package cuecodec

import (
	"fmt"
	"strings"
)

// ErrorCode is the stable machine-readable classification of a CodecError.
type ErrorCode string

const (
	CodeInvalidArgument    ErrorCode = "INVALID_ARGUMENT"
	CodeUnsupportedKind    ErrorCode = "UNSUPPORTED_KIND"
	CodeRepositoryBoundary ErrorCode = "REPOSITORY_BOUNDARY"
	CodeInputLimit         ErrorCode = "INPUT_LIMIT"
	CodeRead               ErrorCode = "READ"
	CodeSyntax             ErrorCode = "SYNTAX"
	CodeIdentity           ErrorCode = "IDENTITY"
	CodeSchemaValidation   ErrorCode = "SCHEMA_VALIDATION"
	CodeIncomplete         ErrorCode = "INCOMPLETE"
	CodeStructuralLimit    ErrorCode = "STRUCTURAL_LIMIT"
	CodeNonCanonical       ErrorCode = "NON_CANONICAL"
	CodeProjection         ErrorCode = "PROJECTION"
	CodeSchemaGeneration   ErrorCode = "SCHEMA_GENERATION"
	CodeSchemaGuard        ErrorCode = "SCHEMA_GUARD"
	CodePairedInstance     ErrorCode = "PAIRED_INSTANCE"
)

// CodecError is the complete public-contract-v1 failure value. Its fields are
// deliberately data-only: the selected classification and stable context do
// not depend on an operating-system or dependency error string.
type CodecError struct {
	Code        ErrorCode
	Operation   string
	Kind        Kind
	RecordPath  string
	CUEPath     string
	JSONPointer string
	RuleID      string
}

func (e *CodecError) Error() string {
	if e == nil {
		return "<nil>"
	}
	var b strings.Builder
	b.WriteString("cuecodec: ")
	b.WriteString(string(e.Code))
	appendCodecErrorField(&b, "operation", e.Operation)
	if e.Kind.APIVersion != "" || e.Kind.Kind != "" {
		appendCodecErrorField(&b, "kind", e.Kind.APIVersion+"/"+e.Kind.Kind)
	}
	appendCodecErrorField(&b, "record_path", e.RecordPath)
	appendCodecErrorField(&b, "cue_path", e.CUEPath)
	appendCodecErrorField(&b, "json_pointer", e.JSONPointer)
	appendCodecErrorField(&b, "rule_id", e.RuleID)
	return b.String()
}

func appendCodecErrorField(b *strings.Builder, name, value string) {
	if value == "" {
		return
	}
	b.WriteByte(' ')
	b.WriteString(name)
	b.WriteByte('=')
	fmt.Fprintf(b, "%q", value)
}

// selectCodecError implements error-precedence-v1 without widening the public
// API. The fixed stage/code table precedes bytewise context ordering. The
// selected pointer is returned unchanged, and nil candidates are ignored.
func selectCodecError(candidates ...*CodecError) *CodecError {
	var selected *CodecError
	for _, candidate := range candidates {
		if candidate == nil {
			continue
		}
		if selected == nil || compareCodecErrors(candidate, selected) < 0 {
			selected = candidate
		}
	}
	return selected
}

func compareCodecErrors(a, b *CodecError) int {
	if ar, br := errorStageRank(a), errorStageRank(b); ar != br {
		if ar < br {
			return -1
		}
		return 1
	}
	if ar, br := errorCodeRank(a.Code), errorCodeRank(b.Code); ar != br {
		if ar < br {
			return -1
		}
		return 1
	}
	for _, pair := range [][2]string{
		{a.RecordPath, b.RecordPath},
		{a.CUEPath, b.CUEPath},
		{a.JSONPointer, b.JSONPointer},
	} {
		if pair[0] < pair[1] {
			return -1
		}
		if pair[0] > pair[1] {
			return 1
		}
	}
	if ar, br := ruleRank(a.RuleID), ruleRank(b.RuleID); ar != br {
		if ar < br {
			return -1
		}
		return 1
	}
	if a.RuleID < b.RuleID {
		return -1
	}
	if a.RuleID > b.RuleID {
		return 1
	}
	return 0
}

func errorStageRank(err *CodecError) uint16 {
	if err == nil {
		return ^uint16(0)
	}
	// Snapshot verification is the publication/drift gate. It deliberately
	// retains the public READ classification while ranking after every fault
	// that can be established from the frozen inputs and caller arguments.
	if err.RuleID == "snapshot-changed" {
		return 12
	}
	switch err.Code {
	case CodeInvalidArgument, CodeUnsupportedKind:
		return 1
	case CodeRepositoryBoundary, CodeInputLimit, CodeRead:
		return 2
	case CodeSyntax:
		return 3
	case CodeIdentity:
		return 4
	case CodeSchemaValidation:
		return 5
	case CodeIncomplete:
		return 6
	case CodeStructuralLimit:
		return 7
	case CodeNonCanonical:
		return 8
	case CodeProjection, CodeSchemaGeneration:
		return 9
	case CodeSchemaGuard:
		return 10
	case CodePairedInstance:
		return 11
	default:
		return ^uint16(0)
	}
}

func errorCodeRank(code ErrorCode) uint16 {
	order := []ErrorCode{
		CodeInvalidArgument, CodeUnsupportedKind, CodeRepositoryBoundary,
		CodeInputLimit, CodeRead, CodeSyntax, CodeIdentity,
		CodeSchemaValidation, CodeIncomplete, CodeStructuralLimit,
		CodeNonCanonical, CodeProjection, CodeSchemaGeneration,
		CodeSchemaGuard, CodePairedInstance,
	}
	for i, candidate := range order {
		if code == candidate {
			return uint16(i + 1)
		}
	}
	return ^uint16(0)
}

func ruleRank(rule string) uint16 {
	order := []string{
		// Public codec and repository rules. Ordering is compatibility-bearing
		// only after code and location fields compare equal.
		"context", "limits-negative", "nil-codec", "kind-identity",
		"kind-not-admitted", "record-owner", "working-directory",
		"repository-root", "repository", "empty-root", "invalid-root", "root-unavailable",
		"root-symlink", "root-not-directory", "invalid-path", "path-escape",
		"path-unavailable", "symlink", "special-file", "file-unavailable",
		"walk-unavailable", "changed-during-read", "record-path", "max-bytes",
		"record-unavailable", "module-identity", "module-field", "module-path",
		"language-field", "language-version", "package-layout", "import-closure", "offline-import",
		"import-layout", "import-unavailable", "import-escape", "import-cycle",
		"import-declaration", "import-path", "snapshot-content", "source-changed", "cue-load",
		"cue-build", "package-instance", "nil-cue-context", "cue-syntax",
		"record-shape", "identity-missing", "identity-string", "stable-id-syntax",
		"kind-mismatch", "one-record-per-file", "stable-id-mismatch",
		"schema-definition", "schema-unification", "recursive-concreteness",
		"unsupported-shape", "max-depth", "canonical-parse",
		"canonical-round-trip", "final-cue-text-v1", "canonical-public-value",
		"canonical-ast", "cue-format", "utf8", "carrier-json", "flat-review-v1",
		"carrier-equality", "encoding-jsonschema-generate", "schema-ast",
		"schema-json", "guard-repeat-stability", "unchanged-schema-bytes", "structured-output-v1",
		"offline-json-schema", "cue-paired-instance", "snapshot-changed",
		// structured-output-v1 rule table. The former schema_bytes rule (the
		// 15,000-byte whole-schema cap) was declared VOID by requester ruling
		// and must not reappear here.
		"invalid_json", "duplicate_key", "root_object",
		"draft_2020_12", "definition_name", "definition_shape",
		"unsupported_keyword", "root_only_keyword", "description",
		"schema_form", "type", "properties", "required",
		"additional_properties", "items", "enum", "any_of",
		"null_position", "reference", "reference_target",
		"reference_cycle", "unreachable_definition", "property_limit",
		"depth_limit",
	}
	for i, candidate := range order {
		if rule == candidate {
			return uint16(i + 1)
		}
	}
	return ^uint16(0)
}
