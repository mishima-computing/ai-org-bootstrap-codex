package jsonschemavalidate

import (
	"errors"
	"testing"

	"github.com/mishima-computing/cuecodec/internal/schemaguard"
)

func TestValidateArbitraryPrecisionAndListOrder(t *testing.T) {
	schema := []byte(`{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "type":"object",
  "properties":{
    "large":{"type":"integer","enum":[900719925474099312345678901234567890]},
    "exact":{"type":"number"},
    "ordered":{"type":"array","items":{"type":"integer"}}
  },
  "required":["large","exact","ordered"],
  "additionalProperties":false
}`)
	instance := []byte(`{"large":900719925474099312345678901234567890,"exact":0.1000000000000000000000000001,"ordered":[3,1,2]}`)
	if err := Validate(schema, instance); err != nil {
		t.Fatal(err)
	}
	wrongInteger := []byte(`{"large":900719925474099312345678901234567891,"exact":0.1000000000000000000000000001,"ordered":[3,1,2]}`)
	assertValidationCode(t, schema, wrongInteger, CodeEnum)
	wrongOrderType := []byte(`{"large":900719925474099312345678901234567890,"exact":0.1000000000000000000000000001,"ordered":[3,"1",2]}`)
	assertValidationCode(t, schema, wrongOrderType, CodeType)
}

func TestValidateClosedRequiredObject(t *testing.T) {
	schema := []byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"enabled":{"type":"boolean"}},"required":["enabled"],"additionalProperties":false}`)
	if err := Validate(schema, []byte(`{"enabled":true}`)); err != nil {
		t.Fatal(err)
	}
	assertValidationCode(t, schema, []byte(`{}`), CodeRequired)
	assertValidationCode(t, schema, []byte(`{"enabled":true,"extra":false}`), CodeAdditional)
	assertValidationCode(t, schema, []byte(`{"enabled":"true"}`), CodeType)
	assertValidationCode(t, schema, []byte(`{"enabled":true,"enabled":false}`), CodeDuplicateKey)
}

func TestValidateLocalReferenceAndNullableAnyOf(t *testing.T) {
	schema := []byte(`{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$defs":{"Name":{"type":"string","enum":["first","second"]},"Nil":{"type":"null"}},
  "type":"object",
  "properties":{
    "name":{"$ref":"#/$defs/Name"},
    "note":{"anyOf":[{"type":"string"},{"$ref":"#/$defs/Nil"}]}
  },
  "required":["name","note"],
  "additionalProperties":false
}`)
	admitted, err := schemaguard.Guard(schema)
	if err != nil {
		t.Fatal(err)
	}
	for _, instance := range [][]byte{
		[]byte(`{"name":"first","note":null}`),
		[]byte(`{"name":"second","note":"present"}`),
	} {
		if err := ValidateAdmitted(admitted, instance); err != nil {
			t.Fatal(err)
		}
	}
	assertValidationCode(t, schema, []byte(`{"name":"third","note":null}`), CodeEnum)
	assertValidationCode(t, schema, []byte(`{"name":"first","note":7}`), CodeAnyOf)
}

func TestValidateAnyOfAcceptsOneOrMoreMatchingBranches(t *testing.T) {
	schema := []byte(`{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "type":"object",
  "properties":{"value":{"anyOf":[{"type":"number"},{"type":"integer"}]}},
  "required":["value"],
  "additionalProperties":false
}`)
	if err := Validate(schema, []byte(`{"value":1.5}`)); err != nil {
		t.Fatalf("one matching branch: %v", err)
	}
	if err := Validate(schema, []byte(`{"value":1}`)); err != nil {
		t.Fatalf("overlapping branches: %v", err)
	}
	assertValidationCode(t, schema, []byte(`{"value":"1"}`), CodeAnyOf)
}

func TestValidateMathematicalInteger(t *testing.T) {
	schema := []byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"value":{"type":"integer"}},"required":["value"],"additionalProperties":false}`)
	for _, instance := range [][]byte{
		[]byte(`{"value":1.0}`),
		[]byte(`{"value":1e42}`),
	} {
		if err := Validate(schema, instance); err != nil {
			t.Fatalf("exact integer %s: %v", instance, err)
		}
	}
	assertValidationCode(t, schema, []byte(`{"value":1.5}`), CodeType)
}

func TestValidateHugeDecimalExponentWithoutExpansion(t *testing.T) {
	const exponent = "999999999999999999999"
	const huge = "1e" + exponent
	schema := []byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"value":{"type":"integer","enum":[` + huge + `]},"zero":{"type":"integer"}},"required":["value","zero"],"additionalProperties":false}`)
	instance := []byte(`{"value":` + huge + `,"zero":0e-` + exponent + `}`)
	for run := 0; run < 2; run++ {
		if err := Validate(schema, instance); err != nil {
			t.Fatalf("run %d: %v", run, err)
		}
	}
}

func TestValidateRejectsUnguardedSchema(t *testing.T) {
	schema := []byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{},"required":[],"additionalProperties":true}`)
	err := Validate(schema, []byte(`{}`))
	var validationErr *Error
	if !errors.As(err, &validationErr) || validationErr.Code != CodeSchema {
		t.Fatalf("Validate error = %v, want schema error", err)
	}
}

func assertValidationCode(t *testing.T, schema, instance []byte, want ErrorCode) {
	t.Helper()
	err := Validate(schema, instance)
	if err == nil {
		t.Fatalf("Validate unexpectedly accepted %s; want %s", instance, want)
	}
	var validationErr *Error
	if !errors.As(err, &validationErr) {
		t.Fatalf("Validate error is %T, want *jsonschemavalidate.Error", err)
	}
	if validationErr.Code != want {
		t.Fatalf("Validate code = %s, want %s (%v)", validationErr.Code, want, err)
	}
}
