// Package jsonschemavalidate validates JSON instances against the exact
// structured-output-v1 subset admitted by internal/schemaguard. It performs no
// I/O and resolves only already-guarded local $defs references.
package jsonschemavalidate

import (
	"crypto/sha256"
	"fmt"
	"sort"

	"github.com/mishima-computing/cuecodec/internal/schemaguard"
	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

// ErrorCode is the stable validator failure classification.
type ErrorCode string

const (
	CodeSchema       ErrorCode = "schema"
	CodeInvalidJSON  ErrorCode = "invalid_json"
	CodeDuplicateKey ErrorCode = "duplicate_key"
	CodeType         ErrorCode = "type"
	CodeRequired     ErrorCode = "required"
	CodeAdditional   ErrorCode = "additional_property"
	CodeEnum         ErrorCode = "enum"
	CodeAnyOf        ErrorCode = "any_of"
)

// Error identifies both the failing instance location and the guarded schema
// production that rejected it.
type Error struct {
	Code            ErrorCode
	InstancePointer string
	SchemaPointer   string
	Detail          string
}

func (e *Error) Error() string {
	if e == nil {
		return "<nil>"
	}
	return fmt.Sprintf("paired-instance-v1: %s at instance %q schema %q: %s", e.Code, e.InstancePointer, e.SchemaPointer, e.Detail)
}

// Validate guards schema and then validates instance against those exact
// bytes. It is the convenient entry point for callers that do not retain an
// Admission between the guard and validator stages.
func Validate(schema, instance []byte) error {
	admitted, err := schemaguard.Guard(schema)
	if err != nil {
		return &Error{Code: CodeSchema, Detail: err.Error()}
	}
	return ValidateAdmitted(admitted, instance)
}

// ValidateAdmitted validates instance against a prior non-mutating guard
// result. The digest is checked before parsing so even accidental corruption
// between the guard and validator boundaries fails closed.
func ValidateAdmitted(admitted schemaguard.Admission, instance []byte) error {
	schemaBytes := admitted.Bytes()
	if sha256.Sum256(schemaBytes) != admitted.SHA256() {
		return &Error{Code: CodeSchema, Detail: "guarded schema digest mismatch"}
	}
	schema, err := strictjson.Parse(schemaBytes)
	if err != nil {
		return &Error{Code: CodeSchema, Detail: "guarded schema is not strict JSON"}
	}
	value, err := strictjson.Parse(instance)
	if err != nil {
		code := CodeInvalidJSON
		pointer := ""
		if parseErr, ok := err.(*strictjson.Error); ok {
			pointer = parseErr.Path
			if parseErr.Code == strictjson.DuplicateKey {
				code = CodeDuplicateKey
			}
		}
		return &Error{Code: code, InstancePointer: pointer, Detail: "instance must be strict JSON"}
	}
	v := validator{defs: make(map[string]*strictjson.Node)}
	if defs, ok := schema.Lookup("$defs"); ok {
		for _, member := range defs.Members {
			v.defs[member.Name] = member.Value
		}
	}
	return v.validate(schema, value, "", "")
}

type validator struct {
	defs map[string]*strictjson.Node
}

func (v validator) validate(schema, instance *strictjson.Node, schemaPath, instancePath string) error {
	if ref, ok := schema.Lookup("$ref"); ok {
		name := ref.Text[len("#/$defs/"):]
		return v.validate(v.defs[name], instance, strictjson.JoinPointer("/$defs", name), instancePath)
	}
	if anyOf, ok := schema.Lookup("anyOf"); ok {
		matches := 0
		for i, branch := range anyOf.Elements {
			branchPath := strictjson.JoinPointer(strictjson.JoinPointer(schemaPath, "anyOf"), fmt.Sprintf("%d", i))
			if err := v.validate(branch, instance, branchPath, instancePath); err == nil {
				matches++
			}
		}
		if matches >= 1 {
			return nil
		}
		return validationError(CodeAnyOf, instancePath, strictjson.JoinPointer(schemaPath, "anyOf"), "value does not match any anyOf branch")
	}
	typeNode, _ := schema.Lookup("type")
	switch typeNode.Text {
	case "object":
		if instance.Kind != strictjson.Object {
			return typeError(instancePath, schemaPath, "object")
		}
		return v.validateObject(schema, instance, schemaPath, instancePath)
	case "array":
		if instance.Kind != strictjson.Array {
			return typeError(instancePath, schemaPath, "array")
		}
		items, _ := schema.Lookup("items")
		for i, item := range instance.Elements {
			if err := v.validate(items, item, strictjson.JoinPointer(schemaPath, "items"), strictjson.JoinPointer(instancePath, fmt.Sprintf("%d", i))); err != nil {
				return err
			}
		}
		return nil
	case "string":
		if instance.Kind != strictjson.String {
			return typeError(instancePath, schemaPath, "string")
		}
	case "number":
		if instance.Kind != strictjson.Number {
			return typeError(instancePath, schemaPath, "number")
		}
	case "integer":
		if instance.Kind != strictjson.Number || !isInteger(instance.NumberText) {
			return typeError(instancePath, schemaPath, "integer")
		}
	case "boolean":
		if instance.Kind != strictjson.Bool {
			return typeError(instancePath, schemaPath, "boolean")
		}
	case "null":
		if instance.Kind != strictjson.Null {
			return typeError(instancePath, schemaPath, "null")
		}
	}
	if enum, ok := schema.Lookup("enum"); ok {
		for _, candidate := range enum.Elements {
			if strictjson.Equal(candidate, instance) {
				return nil
			}
		}
		return validationError(CodeEnum, instancePath, strictjson.JoinPointer(schemaPath, "enum"), "value is not a member of enum")
	}
	return nil
}

func (v validator) validateObject(schema, instance *strictjson.Node, schemaPath, instancePath string) error {
	properties, _ := schema.Lookup("properties")
	propertyMembers := properties.SortedMembers()
	for _, property := range propertyMembers {
		if _, ok := instance.Lookup(property.Name); !ok {
			return validationError(
				CodeRequired,
				strictjson.JoinPointer(instancePath, property.Name),
				strictjson.JoinPointer(schemaPath, "required"),
				"required property is absent",
			)
		}
	}
	unknown := make([]string, 0)
	for _, member := range instance.Members {
		if _, ok := properties.Lookup(member.Name); !ok {
			unknown = append(unknown, member.Name)
		}
	}
	sort.Strings(unknown)
	if len(unknown) != 0 {
		return validationError(
			CodeAdditional,
			strictjson.JoinPointer(instancePath, unknown[0]),
			strictjson.JoinPointer(schemaPath, "additionalProperties"),
			"property is not declared by the closed object schema",
		)
	}
	for _, property := range propertyMembers {
		value, _ := instance.Lookup(property.Name)
		if err := v.validate(
			property.Value,
			value,
			strictjson.JoinPointer(strictjson.JoinPointer(schemaPath, "properties"), property.Name),
			strictjson.JoinPointer(instancePath, property.Name),
		); err != nil {
			return err
		}
	}
	return nil
}

func typeError(instancePath, schemaPath, want string) error {
	return validationError(CodeType, instancePath, strictjson.JoinPointer(schemaPath, "type"), "expected "+want)
}

func validationError(code ErrorCode, instancePath, schemaPath, detail string) *Error {
	return &Error{Code: code, InstancePointer: instancePath, SchemaPointer: schemaPath, Detail: detail}
}

func isInteger(number string) bool {
	return strictjson.NumberIsInteger(number)
}
