package cuecodec

import (
	"crypto/sha256"

	"cuelang.org/go/encoding/jsonschema"
	"github.com/mishima-computing/cuecodec/internal/jsonschemavalidate"
	"github.com/mishima-computing/cuecodec/internal/schemaguard"
)

// generateAndGuardSchema is deliberately in-process. The bytes produced by
// marshaling the generator's returned AST are the guard input, validator input,
// and publishable candidate; no normalization or lowering step exists.
func (c *Codec) generateAndGuardSchema(entry *kindEntry) (generated, guarded []byte, digest [sha256.Size]byte, err error) {
	expr, generationErr := jsonschema.Generate(entry.schema, &jsonschema.GenerateConfig{
		Version: jsonschema.VersionDraft2020_12,
	})
	if generationErr != nil {
		return nil, nil, digest, codecError(CodeSchemaGeneration, "new", entry.kind, schemaPathV1, "#"+schemaDefV1, "", "encoding-jsonschema-generate")
	}
	schemaValue := c.ctx.BuildExpr(expr)
	if schemaValue.Err() != nil {
		return nil, nil, digest, codecError(CodeSchemaGeneration, "new", entry.kind, schemaPathV1, "#"+schemaDefV1, "", "schema-ast")
	}
	generated, generationErr = schemaValue.MarshalJSON()
	if generationErr != nil {
		return nil, nil, digest, codecError(CodeSchemaGeneration, "new", entry.kind, schemaPathV1, "#"+schemaDefV1, "", "schema-json")
	}
	digest = sha256.Sum256(generated)

	// Guard twice over the same immutable bytes. This is inexpensive for the
	// bounded schema and makes repeat stability part of runtime admission.
	first, guardErr := schemaguard.Guard(generated)
	if guardErr != nil {
		return nil, nil, digest, adaptGuardError(entry.kind, guardErr)
	}
	second, guardErr := schemaguard.Guard(generated)
	if guardErr != nil || first.SHA256() != second.SHA256() || first.SHA256() != digest || !bytesEqual(first.Bytes(), generated) || !bytesEqual(second.Bytes(), generated) {
		return nil, nil, digest, codecError(CodeSchemaGuard, "new", entry.kind, schemaPathV1, "#"+schemaDefV1, "", "guard-repeat-stability")
	}
	return generated, first.Bytes(), digest, nil
}

func adaptGuardError(kind Kind, err error) *CodecError {
	guardErr, ok := err.(*schemaguard.Error)
	if !ok {
		return codecError(CodeSchemaGuard, "new", kind, schemaPathV1, "#"+schemaDefV1, "", guardRulesetV1)
	}
	return codecError(CodeSchemaGuard, "new", kind, schemaPathV1, "#"+schemaDefV1, guardErr.JSONPointer, string(guardErr.Code))
}

func validatePairedCarrier(schema, carrier []byte) error {
	return jsonschemavalidate.Validate(schema, carrier)
}
