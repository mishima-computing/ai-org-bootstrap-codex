package cueload

import (
	"path"
	"strings"
	"testing/fstest"

	"cuelang.org/go/cue"
	"cuelang.org/go/cue/load"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

// BuildPackageV1 loads a package entirely from frozen snapshot bytes. Config.FS
// is intentionally used instead of an overlay: an overlay can still discover
// host files, whereas this virtual filesystem makes the accepted authority the
// loader's complete world. ImportClosureV1 must be resolved before calling it.
func BuildPackageV1(ctx *cue.Context, snapshot repository.SourceSnapshotV1, packageDir, packageName string) (cue.Value, error) {
	return buildPackageV1(ctx, snapshot, packageDir, packageName, "")
}

// BuildPackageFileV1 loads packageDir after excluding every peer CUE file in
// that directory except sourcePath. Files in imported packages remain present.
// The first-kind codec uses this alongside a full-package build so source
// ownership can be established without allowing a peer file to supply the
// declared record label.
func BuildPackageFileV1(ctx *cue.Context, snapshot repository.SourceSnapshotV1, packageDir, packageName, sourcePath string) (cue.Value, error) {
	if path.Clean(sourcePath) != sourcePath || path.Dir(sourcePath) != packageDir || !strings.HasSuffix(sourcePath, ".cue") {
		return cue.Value{}, &Failure{Class: FailureBoundary, Path: sourcePath, Rule: "package-layout"}
	}
	if _, ok := snapshot.Content(sourcePath); !ok {
		return cue.Value{}, &Failure{Class: FailureUnavailable, Path: sourcePath, Rule: "file-unavailable"}
	}
	return buildPackageV1(ctx, snapshot, packageDir, packageName, sourcePath)
}

func buildPackageV1(ctx *cue.Context, snapshot repository.SourceSnapshotV1, packageDir, packageName, onlySource string) (cue.Value, error) {
	if ctx == nil {
		return cue.Value{}, &Failure{Class: FailureSyntax, Path: packageDir, Rule: "nil-cue-context"}
	}
	if packageName == "" || path.Clean(packageDir) != packageDir ||
		(!withinRoot(packageDir, "cue/schema") && !withinRoot(packageDir, "cue/records")) {
		return cue.Value{}, &Failure{Class: FailureBoundary, Path: packageDir, Rule: "package-layout"}
	}
	if failure := validateSnapshotContents(snapshot); failure != nil {
		return cue.Value{}, failure
	}

	virtual := make(fstest.MapFS, len(snapshot.Files))
	for _, file := range snapshot.Files {
		if onlySource != "" && path.Dir(file.Path) == packageDir && file.Path != onlySource {
			continue
		}
		content, ok := snapshot.Content(file.Path)
		if !ok {
			return cue.Value{}, &Failure{Class: FailureChanged, Path: file.Path, Rule: "snapshot-content"}
		}
		virtual[file.Path] = &fstest.MapFile{Data: content, Mode: 0o444}
	}
	config := &load.Config{
		Dir:        "/",
		ModuleRoot: "/",
		Package:    packageName,
		FS:         virtual,
		// Never inherit caller registry settings. All non-builtin imports were
		// already proven local by ResolveImportClosureV1.
		Env: []string{"CUE_REGISTRY=none"},
		FromFSPath: func(name string) string {
			return strings.TrimPrefix(name, "/")
		},
	}
	instances := load.Instances([]string{"./" + packageDir}, config)
	if len(instances) != 1 {
		return cue.Value{}, &Failure{Class: FailureSyntax, Path: packageDir, Rule: "package-instance"}
	}
	if instances[0].Err != nil {
		return cue.Value{}, &Failure{Class: FailureSyntax, Path: packageDir, Rule: "cue-load"}
	}
	value := ctx.BuildInstance(instances[0])
	if value.Err() != nil {
		return cue.Value{}, &Failure{Class: FailureSyntax, Path: packageDir, Rule: "cue-build"}
	}
	return value, nil
}
