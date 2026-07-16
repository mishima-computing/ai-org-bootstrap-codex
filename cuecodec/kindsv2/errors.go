package kindsv2

import (
	"fmt"
	"strings"
)

type ErrorCode string

const (
	CodeInvalidArgument  ErrorCode = "INVALID_ARGUMENT"
	CodeNotAdmitted      ErrorCode = "NOT_ADMITTED"
	CodeAuthority        ErrorCode = "AUTHORITY"
	CodeInputLimit       ErrorCode = "INPUT_LIMIT"
	CodeEncoding         ErrorCode = "ENCODING"
	CodeSyntax           ErrorCode = "SYNTAX"
	CodeDuplicateKey     ErrorCode = "DUPLICATE_KEY"
	CodeIdentity         ErrorCode = "IDENTITY"
	CodeSchemaValidation ErrorCode = "SCHEMA_VALIDATION"
	CodeIncomplete       ErrorCode = "INCOMPLETE"
	CodeStructuralLimit  ErrorCode = "STRUCTURAL_LIMIT"
	CodeNonCanonical     ErrorCode = "NON_CANONICAL"
	CodeProjection       ErrorCode = "PROJECTION"
	CodeSchemaGeneration ErrorCode = "SCHEMA_GENERATION"
	CodeSchemaGuard      ErrorCode = "SCHEMA_GUARD"
	CodeFraming          ErrorCode = "FRAMING"
)

// Failure is the data-only stable failure envelope.  It never carries an OS,
// Git, CUE, or JSON dependency error string.
type Failure struct {
	Code        ErrorCode `json:"code"`
	Operation   Operation `json:"operation"`
	ContextID   string    `json:"context_id,omitempty"`
	CUEPath     string    `json:"cue_path,omitempty"`
	JSONPointer string    `json:"json_pointer,omitempty"`
	RuleID      string    `json:"rule_id"`
}

// CodecFailure is the public machine-diagnostic spelling used by the engine
// registry contract. It aliases Failure so the process and in-process APIs
// cannot drift into distinct failure shapes.
type CodecFailure = Failure

func (e *Failure) Error() string {
	if e == nil {
		return "<nil>"
	}
	var b strings.Builder
	fmt.Fprintf(&b, "kindsv2: %s operation=%q", e.Code, e.Operation)
	if e.ContextID != "" {
		fmt.Fprintf(&b, " context_id=%q", e.ContextID)
	}
	if e.CUEPath != "" {
		fmt.Fprintf(&b, " cue_path=%q", e.CUEPath)
	}
	if e.JSONPointer != "" {
		fmt.Fprintf(&b, " json_pointer=%q", e.JSONPointer)
	}
	fmt.Fprintf(&b, " rule_id=%q", e.RuleID)
	return b.String()
}

func failure(code ErrorCode, operation Operation, contextID, cuePath, pointer, rule string) *Failure {
	return &Failure{Code: code, Operation: operation, ContextID: contextID, CUEPath: cuePath, JSONPointer: pointer, RuleID: rule}
}
