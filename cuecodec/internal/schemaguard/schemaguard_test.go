package schemaguard

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
)

func TestGuardIsNonMutating(t *testing.T) {
	schema := mustJSON(t, rootObject(map[string]any{"value": map[string]any{"type": "integer"}}, nil))
	original := append([]byte(nil), schema...)
	wantHash := sha256.Sum256(schema)
	admitted, err := Guard(schema)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(schema, original) || !bytes.Equal(admitted.Bytes(), original) {
		t.Fatal("guard changed its input bytes")
	}
	if admitted.SHA256() != wantHash {
		t.Fatal("guard digest does not describe the original bytes")
	}
	copy1 := admitted.Bytes()
	copy1[0] ^= 1
	if !bytes.Equal(admitted.Bytes(), original) {
		t.Fatal("Admission.Bytes exposed mutable guard state")
	}
}

// The former 15,000-byte whole-schema cap (max_schema_bytes) is VOID by
// requester ruling: the guard enforces the public Structured Outputs
// safe-subset baseline only. This negative control proves an otherwise valid
// schema well above the voided cap is admitted byte-identically.
func TestGuardHasNoWholeSchemaByteCap(t *testing.T) {
	const voidedCap = 15000
	base := rootObject(map[string]any{"value": map[string]any{"type": "string"}}, nil)
	base["description"] = strings.Repeat("x", 2*voidedCap)
	schema := mustJSON(t, base)
	if len(schema) <= voidedCap {
		t.Fatalf("fixture is %d bytes; want more than the voided %d-byte cap", len(schema), voidedCap)
	}
	assertGuardedTwice(t, schema)
}

func TestGuardOccurrenceExpandedPropertyBoundary(t *testing.T) {
	for _, tc := range []struct {
		name     string
		defProps int
		wantErr  ErrorCode
	}{
		{name: "below", defProps: 48}, // root 2 + two occurrences of 48 = 98
		{name: "at", defProps: 49},    // root 2 + two occurrences of 49 = 100
		{name: "above", defProps: 50, wantErr: CodePropertyLimit},
	} {
		t.Run(tc.name, func(t *testing.T) {
			definition := objectSchema(scalarProperties(tc.defProps))
			root := rootObject(map[string]any{
				"left":  map[string]any{"$ref": "#/$defs/Shared"},
				"right": map[string]any{"$ref": "#/$defs/Shared"},
			}, map[string]any{"Shared": definition})
			schema := mustJSON(t, root)
			if tc.wantErr == "" {
				assertGuardedTwice(t, schema)
				return
			}
			assertGuardCode(t, schema, tc.wantErr)
		})
	}
}

func TestGuardOccurrenceExpandedDepthBoundary(t *testing.T) {
	for _, tc := range []struct {
		depth   int
		wantErr ErrorCode
	}{{depth: MaxDepth - 1}, {depth: MaxDepth}, {depth: MaxDepth + 1, wantErr: CodeDepthLimit}} {
		t.Run(fmt.Sprintf("depth_%d", tc.depth), func(t *testing.T) {
			schema := nestedRoot(tc.depth)
			encoded := mustJSON(t, schema)
			if tc.wantErr == "" {
				assertGuardedTwice(t, encoded)
				return
			}
			assertGuardCode(t, encoded, tc.wantErr)
		})
	}
}

func TestGuardReferenceAndNullTopology(t *testing.T) {
	directNull := rootObject(map[string]any{
		"value": map[string]any{"anyOf": []any{
			map[string]any{"type": "string"},
			map[string]any{"$ref": "#/$defs/Nil"},
		}},
	}, map[string]any{"Nil": map[string]any{"type": "null"}})
	assertGuardedTwice(t, mustJSON(t, directNull))

	chainedNull := rootObject(map[string]any{
		"value": map[string]any{"anyOf": []any{
			map[string]any{"type": "string"},
			map[string]any{"$ref": "#/$defs/Indirect"},
		}},
	}, map[string]any{
		"Indirect": map[string]any{"$ref": "#/$defs/Nil"},
		"Nil":      map[string]any{"type": "null"},
	})
	assertGuardedTwice(t, mustJSON(t, chainedNull))

	outsideUnion := rootObject(map[string]any{
		"value": map[string]any{"$ref": "#/$defs/Indirect"},
	}, map[string]any{
		"Indirect": map[string]any{"$ref": "#/$defs/Nil"},
		"Nil":      map[string]any{"type": "null"},
	})
	assertGuardCode(t, mustJSON(t, outsideUnion), CodeNullPosition)

	twoNulls := rootObject(map[string]any{
		"value": map[string]any{"anyOf": []any{
			map[string]any{"type": "null"},
			map[string]any{"$ref": "#/$defs/Nil"},
		}},
	}, map[string]any{"Nil": map[string]any{"type": "null"}})
	assertGuardCode(t, mustJSON(t, twoNulls), CodeAnyOf)

	twoNullChains := rootObject(map[string]any{
		"value": map[string]any{"anyOf": []any{
			map[string]any{"type": "null"},
			map[string]any{"$ref": "#/$defs/Indirect"},
		}},
	}, map[string]any{
		"Indirect": map[string]any{"$ref": "#/$defs/Nil"},
		"Nil":      map[string]any{"type": "null"},
	})
	assertGuardError(t, mustJSON(t, twoNullChains), Error{
		Code: CodeAnyOf, JSONPointer: "/properties/value/anyOf/1",
		Detail: "anyOf may contain at most one null-resolving branch",
	})

	nestedUnion := rootObject(map[string]any{
		"value": map[string]any{"anyOf": []any{
			map[string]any{"type": "string"},
			map[string]any{"anyOf": []any{
				map[string]any{"type": "boolean"},
				map[string]any{"type": "null"},
			}},
		}},
	}, nil)
	assertGuardCode(t, mustJSON(t, nestedUnion), CodeAnyOf)

	referencedNestedUnion := rootObject(map[string]any{
		"value": map[string]any{"anyOf": []any{
			map[string]any{"type": "string"},
			map[string]any{"$ref": "#/$defs/Nested"},
		}},
	}, map[string]any{
		"Nested": map[string]any{"anyOf": []any{
			map[string]any{"type": "boolean"},
			map[string]any{"type": "null"},
		}},
	})
	assertGuardError(t, mustJSON(t, referencedNestedUnion), Error{
		Code: CodeAnyOf, JSONPointer: "/properties/value/anyOf/1",
		Detail: "anyOf branch cannot itself resolve to a union",
	})

	cycle := rootObject(map[string]any{
		"value": map[string]any{"$ref": "#/$defs/A"},
	}, map[string]any{"A": map[string]any{"$ref": "#/$defs/A"}})
	assertGuardCode(t, mustJSON(t, cycle), CodeReferenceCycle)

	cycleProperties := scalarProperties(MaxTotalProperties)
	cycleProperties["cycle"] = map[string]any{"$ref": "#/$defs/A"}
	cycleWithTooManyProperties := rootObject(cycleProperties, map[string]any{
		"A": map[string]any{"$ref": "#/$defs/A"},
	})
	assertGuardCode(t, mustJSON(t, cycleWithTooManyProperties), CodeReferenceCycle)

	unreachable := rootObject(map[string]any{}, map[string]any{"Unused": map[string]any{"type": "string"}})
	assertGuardCode(t, mustJSON(t, unreachable), CodeUnreachableDef)
}

func TestGuardReferenceExpandedDepthBoundary(t *testing.T) {
	for _, tc := range []struct {
		name        string
		targetDepth int
		wantErr     ErrorCode
	}{
		{name: "below", targetDepth: MaxDepth - 2},
		{name: "at", targetDepth: MaxDepth - 1},
		{name: "above", targetDepth: MaxDepth, wantErr: CodeDepthLimit},
	} {
		t.Run(tc.name, func(t *testing.T) {
			definition := nestedSchema(tc.targetDepth)
			schema := rootObject(map[string]any{
				"value": map[string]any{"$ref": "#/$defs/Deep"},
			}, map[string]any{"Deep": definition})
			encoded := mustJSON(t, schema)
			if tc.wantErr == "" {
				assertGuardedTwice(t, encoded)
				return
			}
			assertGuardCode(t, encoded, tc.wantErr)
		})
	}
}

func TestGuardAcceptsEveryProductionAndReferenceTopology(t *testing.T) {
	defs := map[string]any{
		"Text":  map[string]any{"type": "string", "enum": []any{"alpha", "beta"}, "description": "text enum"},
		"Alias": map[string]any{"$ref": "#/$defs/Text"},
		"Pair": objectSchema(map[string]any{
			"count": map[string]any{"type": "integer", "enum": []any{json.Number("1"), json.Number("2e0")}},
			"flag":  map[string]any{"type": "boolean", "enum": []any{true, false}},
		}),
	}
	root := rootObject(map[string]any{
		"alias":  map[string]any{"$ref": "#/$defs/Alias"},
		"array":  map[string]any{"type": "array", "items": map[string]any{"$ref": "#/$defs/Pair"}, "description": "array"},
		"number": map[string]any{"type": "number", "enum": []any{json.Number("0.1"), json.Number("1e3")}},
		"object": map[string]any{"$ref": "#/$defs/Pair"},
		"optional_value": map[string]any{"anyOf": []any{
			map[string]any{"type": "string"},
			map[string]any{"type": "null"},
		}, "description": "nullable union"},
	}, defs)
	root["description"] = "complete structured-output-v1 witness"
	assertGuardedTwice(t, mustJSON(t, root))
}

func TestGuardEnumUsesArbitrarySizeDecimalExponent(t *testing.T) {
	const huge = "1e999999999999999999999"
	duplicate := []byte(`{"$schema":"` + Draft202012URI + `","type":"object","properties":{"value":{"type":"number","enum":[` + huge + `,` + huge + `]}},"required":["value"],"additionalProperties":false}`)
	assertGuardCode(t, duplicate, CodeEnum)
	integer := []byte(`{"$schema":"` + Draft202012URI + `","type":"object","properties":{"value":{"type":"integer","enum":[` + huge + `]}},"required":["value"],"additionalProperties":false}`)
	assertGuardedTwice(t, integer)
}

func TestGuardRejectsReferenceTopologyClasses(t *testing.T) {
	tests := []struct {
		name   string
		schema map[string]any
		code   ErrorCode
	}{
		{
			name: "mutual cycle",
			schema: rootObject(map[string]any{"value": map[string]any{"$ref": "#/$defs/A"}}, map[string]any{
				"A": map[string]any{"$ref": "#/$defs/B"},
				"B": map[string]any{"$ref": "#/$defs/A"},
			}),
			code: CodeReferenceCycle,
		},
		{
			name:   "anchored reference",
			schema: rootObject(map[string]any{"value": map[string]any{"$ref": "#Value"}}, nil),
			code:   CodeReference,
		},
		{
			name:   "reference has trailing pointer segment",
			schema: rootObject(map[string]any{"value": map[string]any{"$ref": "#/$defs/Value/type"}}, nil),
			code:   CodeReference,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			assertGuardCode(t, mustJSON(t, tc.schema), tc.code)
		})
	}
}

func TestGuardRejectsUnapprovedProductionsAndDuplicates(t *testing.T) {
	tests := []struct {
		name   string
		schema []byte
		code   ErrorCode
	}{
		{
			name:   "invalid JSON",
			schema: []byte(`{"$schema":`),
			code:   CodeInvalidJSON,
		},
		{
			name:   "UTF-8 BOM",
			schema: append([]byte{0xef, 0xbb, 0xbf}, mustJSON(t, rootObject(map[string]any{}, nil))...),
			code:   CodeInvalidJSON,
		},
		{
			name:   "invalid UTF-8",
			schema: []byte{'{', '"', 0xff, '"', ':', '1', '}'},
			code:   CodeInvalidJSON,
		},
		{
			name:   "duplicate key",
			schema: []byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","type":"object","properties":{},"required":[],"additionalProperties":false}`),
			code:   CodeDuplicateKey,
		},
		{
			name: "unsupported type",
			schema: mustJSON(t, rootObject(map[string]any{
				"value": map[string]any{"type": "bytes"},
			}, nil)),
			code: CodeType,
		},
		{
			name: "title",
			schema: mustJSON(t, func() map[string]any {
				r := rootObject(map[string]any{}, nil)
				r["title"] = "not admitted"
				return r
			}()),
			code: CodeUnsupportedKeyword,
		},
		{
			name: "const",
			schema: mustJSON(t, rootObject(map[string]any{
				"value": map[string]any{"type": "string", "const": "x"},
			}, nil)),
			code: CodeUnsupportedKeyword,
		},
		{
			name: "numeric enum duplicate",
			schema: mustJSON(t, rootObject(map[string]any{
				"value": map[string]any{"type": "number", "enum": []any{json.Number("1"), json.Number("1.0")}},
			}, nil)),
			code: CodeEnum,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) { assertGuardCode(t, tc.schema, tc.code) })
	}
}

func TestGuardSelectsLexicographicallySmallestDuplicatePointer(t *testing.T) {
	variants := [][]byte{
		[]byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"z":{"type":"string","type":"string"},"a":{"type":"string","type":"string"}},"required":["a","z"],"additionalProperties":false}`),
		[]byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"a":{"type":"string","type":"string"},"z":{"type":"string","type":"string"}},"required":["a","z"],"additionalProperties":false}`),
	}
	for _, schema := range variants {
		assertGuardError(t, schema, Error{
			Code: CodeDuplicateKey, JSONPointer: "/properties/a/type", Detail: "duplicate_key",
		})
	}
}

func TestGuardSelectsBetweenDuplicateAndTerminalSyntax(t *testing.T) {
	assertGuardError(t, []byte(`{"a":1,"a":2,"z":[}`), Error{
		Code: CodeDuplicateKey, JSONPointer: "/a", Detail: "duplicate_key",
	})
	assertGuardError(t, []byte(`{"z":1,"z":2,"a":[}`), Error{
		Code: CodeInvalidJSON, JSONPointer: "/a", Detail: "invalid_json",
	})
}

func TestGuardCompleteRuleTableRejections(t *testing.T) {
	wrongDialect := rootObject(map[string]any{}, nil)
	wrongDialect["$schema"] = "http://json-schema.org/draft-07/schema#"
	rootArray := map[string]any{
		"$schema": Draft202012URI,
		"type":    "array",
		"items":   map[string]any{"type": "string"},
	}
	nestedDialect := rootObject(map[string]any{
		"value": map[string]any{"type": "string", "$schema": Draft202012URI},
	}, nil)
	badRequired := rootObject(map[string]any{"value": map[string]any{"type": "string"}}, nil)
	badRequired["required"] = []string{}
	openObject := rootObject(map[string]any{}, nil)
	openObject["additionalProperties"] = true

	tests := []struct {
		name   string
		schema any
		code   ErrorCode
	}{
		{name: "dialect", schema: wrongDialect, code: CodeDialect},
		{name: "object-form root", schema: rootArray, code: CodeRootObject},
		{name: "nested root keyword", schema: nestedDialect, code: CodeRootOnlyKeyword},
		{name: "definition name", schema: rootObject(map[string]any{}, map[string]any{"bad/name": map[string]any{"type": "string"}}), code: CodeDefinitionName},
		{name: "definition node object", schema: rootObject(map[string]any{}, map[string]any{"Bad": "string"}), code: CodeDefinitionShape},
		{name: "external ref", schema: rootObject(map[string]any{"value": map[string]any{"$ref": "https://example.test/schema"}}, nil), code: CodeReference},
		{name: "missing ref", schema: rootObject(map[string]any{"value": map[string]any{"$ref": "#/$defs/Missing"}}, nil), code: CodeReferenceTarget},
		{name: "ref sibling", schema: rootObject(map[string]any{"value": map[string]any{"$ref": "#/$defs/Value", "description": "no siblings"}}, map[string]any{"Value": map[string]any{"type": "string"}}), code: CodeSchemaForm},
		{name: "anyOf cardinality", schema: rootObject(map[string]any{"value": map[string]any{"anyOf": []any{map[string]any{"type": "string"}}}}, nil), code: CodeAnyOf},
		{name: "anyOf branch object", schema: rootObject(map[string]any{"value": map[string]any{"anyOf": []any{"string", map[string]any{"type": "null"}}}}, nil), code: CodeAnyOf},
		{name: "property schema object", schema: rootObject(map[string]any{"value": "string"}, nil), code: CodeProperties},
		{name: "required exact", schema: badRequired, code: CodeRequired},
		{name: "closed object", schema: openObject, code: CodeAdditionalProperties},
		{name: "array items", schema: rootObject(map[string]any{"value": map[string]any{"type": "array"}}, nil), code: CodeItems},
		{name: "enum nonempty", schema: rootObject(map[string]any{"value": map[string]any{"type": "string", "enum": []any{}}}, nil), code: CodeEnum},
		{name: "enum same type", schema: rootObject(map[string]any{"value": map[string]any{"type": "string", "enum": []any{"x", 1}}}, nil), code: CodeEnum},
		{name: "null position", schema: rootObject(map[string]any{"value": map[string]any{"type": "null"}}, nil), code: CodeNullPosition},
		{name: "description string", schema: func() map[string]any { r := rootObject(map[string]any{}, nil); r["description"] = 1; return r }(), code: CodeDescription},
		{name: "unknown keyword", schema: func() map[string]any { r := rootObject(map[string]any{}, nil); r["oneOf"] = []any{}; return r }(), code: CodeUnsupportedKeyword},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			assertGuardCode(t, mustJSON(t, tc.schema), tc.code)
		})
	}
}

func TestGuardSameStageSelectionUsesJSONPointerThenRuleOrdinal(t *testing.T) {
	schema := rootObject(map[string]any{
		"zeta":  map[string]any{"type": "bytes"},
		"alpha": map[string]any{"type": "string"},
	}, nil)
	schema["description"] = 1
	schema["zzz"] = true
	encoded := mustJSON(t, schema)

	var first Error
	for run := 0; run < 2; run++ {
		_, err := Guard(encoded)
		var guardErr *Error
		if !errors.As(err, &guardErr) {
			t.Fatalf("run %d error = %T %v", run, err, err)
		}
		want := Error{Code: CodeDescription, JSONPointer: "/description", Detail: "description must be a string"}
		if *guardErr != want {
			t.Fatalf("run %d error = %#v, want %#v", run, *guardErr, want)
		}
		if run == 0 {
			first = *guardErr
		} else if *guardErr != first {
			t.Fatalf("selection changed: first %#v, second %#v", first, *guardErr)
		}
	}
}

func TestGuardCompoundSelectionUsesCompletePublicTuple(t *testing.T) {
	wrongDialectRootArray := map[string]any{
		"$schema": "http://json-schema.org/draft-07/schema#",
		"type":    "array",
		"items":   map[string]any{"type": "string"},
	}
	assertGuardError(t, mustJSON(t, wrongDialectRootArray), Error{
		Code: CodeRootObject, Detail: "root must use the typed object form",
	})

	malformedObject := map[string]any{
		"$schema":              Draft202012URI,
		"type":                 "object",
		"properties":           []any{},
		"required":             map[string]any{},
		"additionalProperties": true,
	}
	assertGuardError(t, mustJSON(t, malformedObject), Error{
		Code: CodeAdditionalProperties, JSONPointer: "/additionalProperties",
		Detail: "additionalProperties must be literal false",
	})

	badDefinition := rootObject(map[string]any{}, map[string]any{
		"bad/name": map[string]any{"type": "string"},
	})
	badDefinition["$schema"] = "http://json-schema.org/draft-07/schema#"
	assertGuardError(t, mustJSON(t, badDefinition), Error{
		Code: CodeDefinitionName, JSONPointer: "/$defs/bad~1name",
		Detail: "definition name does not match [A-Za-z_][A-Za-z0-9_.-]*",
	})

	invalidUnreachable := rootObject(map[string]any{}, map[string]any{
		"Unused": map[string]any{},
	})
	assertGuardError(t, mustJSON(t, invalidUnreachable), Error{
		Code: CodeSchemaForm, JSONPointer: "/$defs/Unused",
		Detail: "schema must use exactly one of type, anyOf, or $ref",
	})

	deepWithUnused := nestedRoot(MaxDepth + 1)
	deepWithUnused["$defs"] = map[string]any{"Unused": map[string]any{"type": "string"}}
	assertGuardError(t, mustJSON(t, deepWithUnused), Error{
		Code: CodeUnreachableDef, JSONPointer: "/$defs/Unused",
		Detail: "definition is not reachable from the root schema",
	})

	tooManyWithInvalidLast := scalarProperties(MaxTotalProperties + 1)
	tooManyWithInvalidLast["p100"] = "not-a-schema"
	assertGuardError(t, mustJSON(t, rootObject(tooManyWithInvalidLast, nil)), Error{
		Code:   CodePropertyLimit,
		Detail: fmt.Sprintf("expanded property count %d exceeds %d", MaxTotalProperties+1, MaxTotalProperties),
	})

	invalidBeforeContextualNull := rootObject(map[string]any{
		"a": map[string]any{},
		"z": map[string]any{"$ref": "#/$defs/Nil"},
	}, map[string]any{
		"Nil": map[string]any{"type": "null"},
	})
	assertGuardError(t, mustJSON(t, invalidBeforeContextualNull), Error{
		Code: CodeNullPosition, JSONPointer: "/$defs/Nil",
		Detail: "null is allowed only in an anyOf branch or its $ref chain",
	})
}

func TestGuardDepthTieUsesLexicographicallySmallestDeepestPointer(t *testing.T) {
	deep := nestedSchema(MaxDepth)
	schema := rootObject(map[string]any{
		"a": map[string]any{"$ref": "#/$defs/Z"},
		"b": map[string]any{"$ref": "#/$defs/A"},
	}, map[string]any{
		"A": deep,
		"Z": deep,
	})
	wantPointer := "/$defs/A" + strings.Repeat("/properties/child", MaxDepth-1)
	assertGuardError(t, mustJSON(t, schema), Error{
		Code: CodeDepthLimit, JSONPointer: wantPointer,
		Detail: fmt.Sprintf("expanded depth %d exceeds %d", MaxDepth+1, MaxDepth),
	})
}

func TestGuardAnyOfSelectionUsesLexicalBranchPointer(t *testing.T) {
	nestedBranches := make([]any, 11)
	for i := range nestedBranches {
		nestedBranches[i] = map[string]any{"type": "string"}
	}
	nestedBranches[2] = map[string]any{"$ref": "#/$defs/NestedTwo"}
	nestedBranches[10] = map[string]any{"$ref": "#/$defs/NestedTen"}
	nestedDefinition := func() map[string]any {
		return map[string]any{"anyOf": []any{
			map[string]any{"type": "boolean"},
			map[string]any{"type": "null"},
		}}
	}
	nestedSchema := rootObject(map[string]any{
		"value": map[string]any{"anyOf": nestedBranches},
	}, map[string]any{
		"NestedTwo": nestedDefinition(),
		"NestedTen": nestedDefinition(),
	})
	assertGuardError(t, mustJSON(t, nestedSchema), Error{
		Code: CodeAnyOf, JSONPointer: "/properties/value/anyOf/10",
		Detail: "anyOf branch cannot itself resolve to a union",
	})

	nullBranches := make([]any, 11)
	for i := range nullBranches {
		nullBranches[i] = map[string]any{"type": "string"}
	}
	nullBranches[0] = map[string]any{"type": "null"}
	nullBranches[2] = map[string]any{"type": "null"}
	nullBranches[10] = map[string]any{"type": "null"}
	nullSchema := rootObject(map[string]any{
		"value": map[string]any{"anyOf": nullBranches},
	}, nil)
	assertGuardError(t, mustJSON(t, nullSchema), Error{
		Code: CodeAnyOf, JSONPointer: "/properties/value/anyOf/10",
		Detail: "anyOf may contain at most one null-resolving branch",
	})
}

func rootObject(properties map[string]any, defs map[string]any) map[string]any {
	r := objectSchema(properties)
	r["$schema"] = Draft202012URI
	if defs != nil {
		r["$defs"] = defs
	}
	return r
}

func objectSchema(properties map[string]any) map[string]any {
	required := make([]string, 0, len(properties))
	for name := range properties {
		required = append(required, name)
	}
	sortStrings(required)
	return map[string]any{
		"type":                 "object",
		"properties":           properties,
		"required":             required,
		"additionalProperties": false,
	}
}

func scalarProperties(count int) map[string]any {
	properties := make(map[string]any, count)
	for i := 0; i < count; i++ {
		properties[fmt.Sprintf("p%03d", i)] = map[string]any{"type": "string"}
	}
	return properties
}

func nestedRoot(depth int) map[string]any {
	schema := nestedSchema(depth)
	schema["$schema"] = Draft202012URI
	return schema
}

func nestedSchema(depth int) map[string]any {
	var schema map[string]any = map[string]any{"type": "string"}
	for i := 1; i < depth; i++ {
		schema = objectSchema(map[string]any{"child": schema})
	}
	return schema
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	result, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func assertGuardCode(t *testing.T, schema []byte, want ErrorCode) {
	t.Helper()
	original := append([]byte(nil), schema...)
	var first Error
	for run := 0; run < 2; run++ {
		_, err := Guard(schema)
		if err == nil {
			t.Fatalf("Guard unexpectedly admitted schema; want %s", want)
		}
		var guardErr *Error
		if !errors.As(err, &guardErr) {
			t.Fatalf("Guard error is %T, want *schemaguard.Error", err)
		}
		if guardErr.Code != want {
			t.Fatalf("Guard code = %s, want %s (%v)", guardErr.Code, want, err)
		}
		if run == 0 {
			first = *guardErr
		} else if *guardErr != first {
			t.Fatalf("Guard selection changed: first %#v, second %#v", first, *guardErr)
		}
		if !bytes.Equal(schema, original) {
			t.Fatal("Guard mutated rejected schema")
		}
	}
}

func assertGuardError(t *testing.T, schema []byte, want Error) {
	t.Helper()
	original := append([]byte(nil), schema...)
	for run := 0; run < 2; run++ {
		_, err := Guard(schema)
		var guardErr *Error
		if !errors.As(err, &guardErr) || guardErr == nil {
			t.Fatalf("run %d error = %T %v, want %#v", run, err, err, want)
		}
		if *guardErr != want {
			t.Fatalf("run %d error = %#v, want %#v", run, *guardErr, want)
		}
		if !bytes.Equal(schema, original) {
			t.Fatalf("run %d Guard mutated rejected schema", run)
		}
	}
}

func assertGuardedTwice(t *testing.T, schema []byte) {
	t.Helper()
	original := append([]byte(nil), schema...)
	wantHash := sha256.Sum256(original)
	for run := 0; run < 2; run++ {
		admitted, err := Guard(schema)
		if err != nil {
			t.Fatal(err)
		}
		if admitted.SHA256() != wantHash || !bytes.Equal(admitted.Bytes(), original) || !bytes.Equal(schema, original) {
			t.Fatal("Guard changed admitted schema or its hash")
		}
	}
}

func sortStrings(values []string) {
	for i := 1; i < len(values); i++ {
		for j := i; j > 0 && values[j] < values[j-1]; j-- {
			values[j], values[j-1] = values[j-1], values[j]
		}
	}
}
