package repository

import (
	"testing"
	"testing/fstest"
)

func TestEngineSourceSnapshotV2IsOrderedFrozenAndVerified(t *testing.T) {
	authority := fstest.MapFS{
		"schema/value.cue":      &fstest.MapFile{Data: []byte("#Value: string\n"), Mode: 0o444},
		"registry/catalog.json": &fstest.MapFile{Data: []byte("{}\n"), Mode: 0o444},
	}
	names := []string{"schema/value.cue", "registry/catalog.json"}
	snapshot, err := CaptureEngineSourceV2(authority, names)
	if err != nil {
		t.Fatalf("CaptureEngineSourceV2: %v", err)
	}
	if snapshot.Files[0].Path != "registry/catalog.json" || snapshot.Files[1].Path != "schema/value.cue" {
		t.Fatalf("file order = %#v", snapshot.Files)
	}
	content, ok := snapshot.Content("schema/value.cue")
	if !ok {
		t.Fatal("frozen content missing")
	}
	content[0] = '!'
	again, _ := snapshot.Content("schema/value.cue")
	if again[0] == '!' {
		t.Fatal("Content did not return a defensive copy")
	}
	if err := VerifyEngineSourceV2(authority, names, snapshot); err != nil {
		t.Fatalf("VerifyEngineSourceV2 unchanged: %v", err)
	}
	authority["schema/value.cue"].Data = []byte("#Value: int\n")
	if err := VerifyEngineSourceV2(authority, names, snapshot); err == nil {
		t.Fatal("VerifyEngineSourceV2 accepted drift")
	}
}
