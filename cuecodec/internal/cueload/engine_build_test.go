package cueload

import (
	"testing"
	"testing/fstest"

	"cuelang.org/go/cue"
	"cuelang.org/go/cue/cuecontext"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

func TestBuildEngineFileV2UsesFrozenBytes(t *testing.T) {
	authority := fstest.MapFS{
		"schema/value.cue": &fstest.MapFile{Data: []byte("#Value: close({name: string})\n"), Mode: 0o444},
	}
	snapshot, err := repository.CaptureEngineSourceV2(authority, []string{"schema/value.cue"})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	authority["schema/value.cue"].Data = []byte("this is not CUE")
	value, err := BuildEngineFileV2(cuecontext.New(), snapshot, "schema/value.cue")
	if err != nil {
		t.Fatalf("BuildEngineFileV2: %v", err)
	}
	definition := value.LookupPath(cue.MakePath(cue.Def("Value")))
	if !definition.Exists() || definition.Err() != nil {
		t.Fatalf("definition = %v", definition.Err())
	}
}
