// Package codexschemaguard enforces the additional restrictions of the
// Codex Structured Output V1 carrier.  It composes with, and never weakens or
// mutates, the frozen structured-output-v1 guard.
package codexschemaguard

import (
	"crypto/sha256"
	"fmt"

	"github.com/mishima-computing/cuecodec/internal/schemaguard"
	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

type ErrorCode string

const (
	CodeUnsupportedKeyword ErrorCode = "unsupported_keyword"
	CodeDescription        ErrorCode = "description"
)

type Error struct {
	Code        ErrorCode
	JSONPointer string
}

func (e *Error) Error() string {
	if e == nil {
		return "<nil>"
	}
	return fmt.Sprintf("codex-structured-output-v1: %s at %s", e.Code, e.JSONPointer)
}

type Admission struct {
	bytes  []byte
	digest [sha256.Size]byte
}

func (a Admission) Bytes() []byte             { return append([]byte(nil), a.bytes...) }
func (a Admission) SHA256() [sha256.Size]byte { return a.digest }

var forbidden = map[string]bool{
	"allOf": true, "anyOf": true, "oneOf": true, "not": true,
	"if": true, "then": true, "else": true, "const": true,
	"minLength": true, "maxLength": true, "pattern": true, "format": true,
}

// Guard admits exactly the original schema bytes or no bytes at all.
func Guard(src []byte) (Admission, error) {
	if _, err := schemaguard.Guard(src); err != nil {
		return Admission{}, err
	}
	root, err := strictjson.Parse(src)
	if err != nil {
		return Admission{}, err
	}
	var diagnostics []*Error
	walk(root, "", &diagnostics)
	var selected *Error
	for _, candidate := range diagnostics {
		if selected == nil || candidate.JSONPointer < selected.JSONPointer ||
			candidate.JSONPointer == selected.JSONPointer && candidate.Code < selected.Code {
			selected = candidate
		}
	}
	if selected != nil {
		return Admission{}, selected
	}
	copyBytes := append([]byte(nil), src...)
	return Admission{bytes: copyBytes, digest: sha256.Sum256(copyBytes)}, nil
}

func walk(node *strictjson.Node, pointer string, diagnostics *[]*Error) {
	if node == nil {
		return
	}
	switch node.Kind {
	case strictjson.Object:
		for _, member := range node.SortedMembers() {
			childPointer := strictjson.JoinPointer(pointer, member.Name)
			if forbidden[member.Name] {
				*diagnostics = append(*diagnostics, &Error{Code: CodeUnsupportedKeyword, JSONPointer: childPointer})
			}
			if member.Name == "description" && member.Value.Kind != strictjson.String {
				*diagnostics = append(*diagnostics, &Error{Code: CodeDescription, JSONPointer: childPointer})
			}
			walk(member.Value, childPointer, diagnostics)
		}
	case strictjson.Array:
		for i, child := range node.Elements {
			walk(child, strictjson.JoinPointer(pointer, fmt.Sprintf("%d", i)), diagnostics)
		}
	}
}
