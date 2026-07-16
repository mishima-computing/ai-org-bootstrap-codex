package cueload

import (
	"path"
	"strings"

	"cuelang.org/go/cue"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

// BuildEngineFileV2 compiles one file exclusively from a frozen sibling-engine
// snapshot. Engine definitions are intentionally import-free, making a single
// captured file the complete loader boundary rather than ambient disk state.
func BuildEngineFileV2(ctx *cue.Context, snapshot repository.EngineSourceSnapshotV2, sourcePath string) (cue.Value, error) {
	if ctx == nil {
		return cue.Value{}, &Failure{Class: FailureSyntax, Path: sourcePath, Rule: "nil-cue-context"}
	}
	if sourcePath == "" || path.Clean(sourcePath) != sourcePath || strings.HasPrefix(sourcePath, "/") || !strings.HasSuffix(sourcePath, ".cue") {
		return cue.Value{}, &Failure{Class: FailureBoundary, Path: sourcePath, Rule: "engine-source-path"}
	}
	content, ok := snapshot.Content(sourcePath)
	if !ok {
		return cue.Value{}, &Failure{Class: FailureUnavailable, Path: sourcePath, Rule: "engine-source-unavailable"}
	}
	value := ctx.CompileBytes(content, cue.Filename(sourcePath))
	if value.Err() != nil {
		return cue.Value{}, &Failure{Class: FailureSyntax, Path: sourcePath, Rule: "engine-cue-build"}
	}
	return value, nil
}
