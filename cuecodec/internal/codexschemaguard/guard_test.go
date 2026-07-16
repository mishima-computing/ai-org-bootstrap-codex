package codexschemaguard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestGuardPreservesBytesAndRejectsCodexAnyOf(t *testing.T) {
	accepted := []byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","additionalProperties":false,"properties":{"x":{"type":"string"}},"required":["x"]}`)
	admission, err := Guard(accepted)
	if err != nil {
		t.Fatalf("Guard accepted schema: %v", err)
	}
	if got := string(admission.Bytes()); got != string(accepted) {
		t.Fatalf("guard mutated bytes: %s", got)
	}

	rejected := []byte(`{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","additionalProperties":false,"properties":{"x":{"anyOf":[{"type":"string"},{"type":"null"}]}},"required":["x"]}`)
	_, err = Guard(rejected)
	guardErr, ok := err.(*Error)
	if !ok || guardErr.Code != CodeUnsupportedKeyword || guardErr.JSONPointer != "/properties/x/anyOf" {
		t.Fatalf("rejection = %#v (%v)", guardErr, err)
	}
}

func TestCanonicalRootPreviewPurposeBuiltSchemaPassesSafeSubset(t *testing.T) {
	snapshotPath := filepath.Join(
		"..", "..", "..", "tests", "fixtures", "codex_output_schema_snapshot.json",
	)
	snapshotBytes, err := os.ReadFile(snapshotPath)
	if err != nil {
		t.Fatal(err)
	}
	var snapshot map[string]json.RawMessage
	if err := json.Unmarshal(snapshotBytes, &snapshot); err != nil {
		t.Fatal(err)
	}
	schema, ok := snapshot["ai_org.patchwork_queue.receive.CANONICAL_ROOT_PREVIEW_SCHEMA"]
	if !ok {
		t.Fatal("canonical root preview schema is absent from the Python snapshot")
	}
	if _, err := Guard(schema); err != nil {
		t.Fatalf("canonical root preview schema failed the Codex safe-subset guard: %v", err)
	}
}
