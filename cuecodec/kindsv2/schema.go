package kindsv2

import (
	"context"

	"cuelang.org/go/encoding/jsonschema"
	"github.com/mishima-computing/cuecodec/internal/codexschemaguard"
	"github.com/mishima-computing/cuecodec/internal/jsonschemavalidate"
	"github.com/mishima-computing/cuecodec/internal/schemaguard"
)

// Schema generates from the admitted non-concrete definition.  No instance is
// accepted or required at this boundary, and the exact returned bytes are the
// input to both structured-output guards.
func (r *Registry) Schema(ctx context.Context, contextID, profile string) (Artifact, error) {
	const operation = OperationSchema
	if profile == "" {
		profile = CodexSchemaProfileV1
	}
	if profile != CodexSchemaProfileV1 {
		return Artifact{}, failure(CodeInvalidArgument, operation, contextID, "", "", "schema-profile")
	}
	if err := r.resolve(ctx, operation, contextID); err != nil {
		return Artifact{}, err
	}
	schema, ok := r.bodySchemas[contextID]
	if !ok {
		return Artifact{}, failure(CodeNotAdmitted, operation, contextID, "", "", "context-not-admitted")
	}
	expr, err := jsonschema.Generate(schema, &jsonschema.GenerateConfig{Version: jsonschema.VersionDraft2020_12})
	if err != nil {
		return Artifact{}, failure(CodeSchemaGeneration, operation, contextID, "#SemanticStatus", "", "encoding-jsonschema-generate")
	}
	value := r.ctx.BuildExpr(expr)
	if value.Err() != nil {
		return Artifact{}, failure(CodeSchemaGeneration, operation, contextID, "#SemanticStatus", "", "schema-ast")
	}
	encoded, err := value.MarshalJSON()
	if err != nil {
		return Artifact{}, failure(CodeSchemaGeneration, operation, contextID, "#SemanticStatus", "", "schema-json")
	}
	if len(encoded) > r.maxBytes {
		return Artifact{}, failure(CodeStructuralLimit, operation, contextID, "#SemanticStatus", "", "max-bytes")
	}
	if _, err := schemaguard.Guard(encoded); err != nil {
		return Artifact{}, adaptSchemaGuard(operation, contextID, err)
	}
	if _, err := codexschemaguard.Guard(encoded); err != nil {
		return Artifact{}, adaptSchemaGuard(operation, contextID, err)
	}
	if err := r.verifyAuthority(operation, contextID); err != nil {
		return Artifact{}, err
	}
	return newArtifact("application/schema+json; profile="+profile, encoded), nil
}

// ValidateProjection validates against the exact generated-and-guarded schema
// bytes used by Schema.
func (r *Registry) ValidateProjection(ctx context.Context, contextID string, projection []byte) error {
	schemaArtifact, err := r.Schema(ctx, contextID, CodexSchemaProfileV1)
	if err != nil {
		return err
	}
	schema, err := schemaArtifact.Bytes()
	if err != nil {
		return failure(CodeSchemaGuard, OperationSchema, contextID, "", "", "artifact-base64")
	}
	if err := jsonschemavalidate.Validate(schema, projection); err != nil {
		validatorErr, ok := err.(*jsonschemavalidate.Error)
		if !ok {
			return failure(CodeSchemaValidation, OperationSchema, contextID, "", "", "offline-json-schema")
		}
		return failure(CodeSchemaValidation, OperationSchema, contextID, "", validatorErr.InstancePointer, string(validatorErr.Code))
	}
	return nil
}

func adaptSchemaGuard(operation Operation, contextID string, err error) *Failure {
	if guardErr, ok := err.(*schemaguard.Error); ok {
		return failure(CodeSchemaGuard, operation, contextID, "#SemanticStatus", guardErr.JSONPointer, string(guardErr.Code))
	}
	if guardErr, ok := err.(*codexschemaguard.Error); ok {
		return failure(CodeSchemaGuard, operation, contextID, "#SemanticStatus", guardErr.JSONPointer, string(guardErr.Code))
	}
	return failure(CodeSchemaGuard, operation, contextID, "#SemanticStatus", "", "schema-guard")
}
