package cueload

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"

	"cuelang.org/go/cue"
	"cuelang.org/go/cue/cuecontext"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

func TestCheckedInAuthorityHasExpectedOfflineClosure(t *testing.T) {
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	root := filepath.Clean(filepath.Join(filepath.Dir(thisFile), "..", ".."))
	snapshot, err := repository.CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	closure, err := ResolveImportClosureV1(root, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if closure.Module != "github.com/mishima-computing/cuecodec@v0" {
		t.Fatalf("module = %q", closure.Module)
	}
	if closure.LanguageVersion != languageVersionV1 {
		t.Fatalf("language version = %q, want %q", closure.LanguageVersion, languageVersionV1)
	}
	wantFiles := []string{
		"cue.mod/module.cue",
		"cue/records/proof_document.cue",
		"cue/records/proof_document_release.cue",
		"cue/schema/proof_document.cue",
	}
	if got := closureFilePaths(closure); !reflect.DeepEqual(got, wantFiles) {
		t.Fatalf("closure files = %#v, want %#v", got, wantFiles)
	}
	if len(closure.Edges) != 2 {
		t.Fatalf("closure edges = %#v", closure.Edges)
	}
	for i, wantFrom := range []string{
		"cue/records/proof_document.cue",
		"cue/records/proof_document_release.cue",
	} {
		edge := closure.Edges[i]
		if edge.From != wantFrom ||
			edge.Import != "github.com/mishima-computing/cuecodec/cue/schema" ||
			edge.Resolved != "cue/schema" || edge.Builtin {
			t.Fatalf("closure edge %d = %#v", i, edge)
		}
	}
	if closure.SHA256 == (Digest{}) || snapshot.SHA256 == (repository.Digest{}) {
		t.Fatal("authority hashes must be nonzero")
	}
}

func TestImportClosureV1RequiresPinnedLanguageVersion(t *testing.T) {
	root := newClosureFixture(t)
	writeClosureFixture(t, root, "cue.mod/module.cue", "module: \"example.test/corpus@v0\"\nlanguage: {version: \"v0.16.0\"}\n")
	writeClosureFixture(t, root, "cue/records/record.cue", "package records\nrecord: {}\n")
	snapshot, err := repository.CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	closure, err := ResolveImportClosureV1(root, snapshot)
	if closure.Module != "" || closure.LanguageVersion != "" || len(closure.Files) != 0 || len(closure.Imports) != 0 || len(closure.Edges) != 0 || closure.SHA256 != (Digest{}) {
		t.Fatalf("partial closure returned: %#v", closure)
	}
	var failure *Failure
	if !errors.As(err, &failure) || failure.Class != FailureSyntax || failure.Path != "cue.mod/module.cue" || failure.Rule != "language-version" {
		t.Fatalf("failure = %#v, error = %v", failure, err)
	}
}

func TestImportClosureV1MetadataSelectionIgnoresDeclarationOrder(t *testing.T) {
	for _, metadata := range []string{
		"module: \"/escape@v0\"\nlanguage: {version: \"v0.16.0\"}\n",
		"language: {version: \"v0.16.0\"}\nmodule: \"/escape@v0\"\n",
		"module: \"/escape@v0\"\nlanguage: 7\n",
		"language: 7\nmodule: \"/escape@v0\"\n",
	} {
		root := newClosureFixture(t)
		writeClosureFixture(t, root, "cue.mod/module.cue", metadata)
		writeClosureFixture(t, root, "cue/records/record.cue", "package records\nrecord: {}\n")
		snapshot, err := repository.CaptureSourceSnapshotV1(root)
		if err != nil {
			t.Fatal(err)
		}
		for run := 0; run < 2; run++ {
			closure, err := ResolveImportClosureV1(root, snapshot)
			if closure.Module != "" || len(closure.Files) != 0 || closure.SHA256 != (Digest{}) {
				t.Fatalf("run %d returned a partial closure: %#v", run, closure)
			}
			var failure *Failure
			want := Failure{Class: FailureSyntax, Path: "cue.mod/module.cue", Rule: "module-path"}
			if !errors.As(err, &failure) || failure == nil || *failure != want {
				t.Fatalf("run %d failure = %#v (%v), want %#v", run, failure, err, want)
			}
		}
	}
}

func TestDeclaredModuleIdentityV1IsIndependentOfOtherMetadataFaults(t *testing.T) {
	tests := []struct {
		name    string
		content string
		want    string
		ok      bool
	}{
		{name: "valid", content: "module: \"example.test/wrong@v0\"\nlanguage: 7\n", want: "example.test/wrong@v0", ok: true},
		{name: "trailing syntax", content: "module: \"example.test/wrong@v0\"\nlanguage: {version: \"v0.17.0\"}\n[\n", want: "example.test/wrong@v0", ok: true},
		{name: "duplicate", content: "module: \"example.test/one@v0\"\nmodule: \"example.test/two@v0\"\n", ok: false},
		{name: "malformed", content: "module: 7\nlanguage: {version: \"v0.17.0\"}\n", ok: false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := newClosureFixture(t)
			writeClosureFixture(t, root, "cue.mod/module.cue", tc.content)
			snapshot, err := repository.CaptureSourceSnapshotV1(root)
			if err != nil {
				t.Fatal(err)
			}
			got, ok := DeclaredModuleIdentityV1(snapshot)
			if got != tc.want || ok != tc.ok {
				t.Fatalf("DeclaredModuleIdentityV1 = (%q, %v), want (%q, %v)", got, ok, tc.want, tc.ok)
			}
		})
	}
}

func TestImportClosureV1ImportFaultsBeatBodySyntax(t *testing.T) {
	tests := []struct {
		name    string
		imports string
		want    Failure
	}{
		{
			name: "boundary before unavailable",
			imports: `import (
	"../outside"
	"outside.test/module/pkg"
)`,
			want: Failure{Class: FailureBoundary, Path: "cue/records/record.cue", Import: "../outside", Rule: "import-escape"},
		},
		{
			name: "unavailable before boundary",
			imports: `import (
	"outside.test/module/pkg"
	"../outside"
)`,
			want: Failure{Class: FailureBoundary, Path: "cue/records/record.cue", Import: "../outside", Rule: "import-escape"},
		},
		{
			name:    "unavailable",
			imports: `import "outside.test/module/pkg"`,
			want:    Failure{Class: FailureUnavailable, Path: "cue/records/record.cue", Import: "outside.test/module/pkg", Rule: "offline-import"},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := newClosureFixture(t)
			writeClosureFixture(t, root, "cue/records/record.cue", "package records\n"+test.imports+"\nbroken: [\n")
			snapshot, err := repository.CaptureSourceSnapshotV1(root)
			if err != nil {
				t.Fatal(err)
			}
			for run := 0; run < 2; run++ {
				closure, err := ResolveImportClosureV1(root, snapshot)
				if closure.Module != "" || closure.LanguageVersion != "" || len(closure.Files) != 0 ||
					len(closure.Imports) != 0 || len(closure.Edges) != 0 || closure.SHA256 != (Digest{}) {
					t.Fatalf("run %d returned a partial closure: %#v", run, closure)
				}
				var failure *Failure
				if !errors.As(err, &failure) || failure == nil || *failure != test.want {
					t.Fatalf("run %d failure = %#v (%v), want %#v", run, failure, err, test.want)
				}
			}
		})
	}
}

func TestImportClosureV1ResolvesOnlySnapshotAndBuiltins(t *testing.T) {
	root := newClosureFixture(t)
	writeClosureFixture(t, root, "cue/records/record.cue", `package records

import (
	"example.test/corpus/cue/schema"
	"strings"
)

record: schema.#Record & {value: strings.ToUpper("ok")}
`)
	writeClosureFixture(t, root, "cue/schema/schema.cue", "package schema\n#Record: close({value: string})\n")
	// Every accepted schema is active authority, even when no record currently
	// imports its package.
	writeClosureFixture(t, root, "cue/schema/unused/unused.cue", "package unused\n#Unused: int\n")

	snapshot, err := repository.CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	first, err := ResolveImportClosureV1(root, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	second, err := ResolveImportClosureV1(root, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if first.SHA256 != second.SHA256 {
		t.Fatal("closure hash changed across repeated resolution")
	}
	wantFiles := []string{"cue.mod/module.cue", "cue/records/record.cue", "cue/schema/schema.cue", "cue/schema/unused/unused.cue"}
	if got := closureFilePaths(first); !reflect.DeepEqual(got, wantFiles) {
		t.Fatalf("closure files = %#v, want %#v", got, wantFiles)
	}
	wantImports := []string{"example.test/corpus/cue/schema", "strings"}
	if !reflect.DeepEqual(first.Imports, wantImports) {
		t.Fatalf("imports = %#v, want %#v", first.Imports, wantImports)
	}
	if len(first.Edges) != 2 || first.Edges[0].Resolved != "cue/schema" || first.Edges[0].Builtin ||
		first.Edges[1].Resolved != "builtin:strings" || !first.Edges[1].Builtin {
		t.Fatalf("edges = %#v", first.Edges)
	}
}

func TestBuildPackageV1UsesOnlySnapshotFilesystem(t *testing.T) {
	root := newClosureFixture(t)
	writeClosureFixture(t, root, "cue/records/record.cue", `package records

import "example.test/corpus/cue/schema"

record: schema.#Record & {value: "ok"}
`)
	writeClosureFixture(t, root, "cue/schema/schema.cue", "package schema\n#Record: close({value: string})\n")
	snapshot, err := repository.CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ResolveImportClosureV1(root, snapshot); err != nil {
		t.Fatal(err)
	}
	// Mutate the host file after snapshotting. BuildPackageV1 must still see
	// the frozen bytes; the caller's final VerifySourceSnapshotV1 then prevents
	// publication from that stale snapshot.
	writeClosureFixture(t, root, "cue/records/record.cue", "this is not valid CUE\n")
	ctx := cuecontext.New()
	schemaPackage, err := BuildPackageV1(ctx, snapshot, "cue/schema", "schema")
	if err != nil {
		t.Fatal(err)
	}
	recordPackage, err := BuildPackageV1(ctx, snapshot, "cue/records", "records")
	if err != nil {
		t.Fatal(err)
	}
	schema := schemaPackage.LookupPath(cue.MakePath(cue.Def("Record")))
	record := recordPackage.LookupPath(cue.ParsePath("record"))
	if err := schema.Unify(record).Validate(cue.Final(), cue.Concrete(true)); err != nil {
		t.Fatalf("snapshot-built record failed schema: %v", err)
	}
	if err := repository.VerifySourceSnapshotV1(root, snapshot); err == nil {
		t.Fatal("changed host source was not rejected before publication")
	}
}

func TestImportClosureV1RejectsUnavailableAndEscape(t *testing.T) {
	tests := []struct {
		name       string
		importPath string
		class      FailureClass
		rule       string
	}{
		{name: "unavailable", importPath: "outside.test/module/pkg", class: FailureUnavailable, rule: "offline-import"},
		{name: "relative escape", importPath: "../outside", class: FailureBoundary, rule: "import-escape"},
		{name: "layout escape", importPath: "example.test/corpus/not-authoritative", class: FailureBoundary, rule: "import-layout"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := newClosureFixture(t)
			writeClosureFixture(t, root, "cue/records/record.cue", "package records\nimport \""+test.importPath+"\"\nrecord: {}\n")
			snapshot, err := repository.CaptureSourceSnapshotV1(root)
			if err != nil {
				t.Fatal(err)
			}
			_, err = ResolveImportClosureV1(root, snapshot)
			var failure *Failure
			if !errors.As(err, &failure) {
				t.Fatalf("ResolveImportClosureV1 error = %T %v", err, err)
			}
			if failure.Class != test.class || failure.Rule != test.rule || failure.Import != test.importPath {
				t.Fatalf("failure = %#v", failure)
			}
		})
	}
}

func TestImportClosureV1SelectsByPublicTupleNotDeclarationOrder(t *testing.T) {
	for _, imports := range []string{
		`import (
	"../outside"
	"example.test/corpus/not-authoritative"
)`,
		`import (
	"example.test/corpus/not-authoritative"
	"../outside"
)`,
	} {
		root := newClosureFixture(t)
		writeClosureFixture(t, root, "cue/records/record.cue", "package records\n"+imports+"\nrecord: {}\n")
		snapshot, err := repository.CaptureSourceSnapshotV1(root)
		if err != nil {
			t.Fatal(err)
		}
		for run := 0; run < 2; run++ {
			closure, err := ResolveImportClosureV1(root, snapshot)
			if closure.Module != "" || closure.LanguageVersion != "" || len(closure.Files) != 0 ||
				len(closure.Imports) != 0 || len(closure.Edges) != 0 || closure.SHA256 != (Digest{}) {
				t.Fatalf("run %d returned a partial closure: %#v", run, closure)
			}
			var failure *Failure
			if !errors.As(err, &failure) || failure == nil {
				t.Fatalf("run %d error = %T %v", run, err, err)
			}
			want := Failure{
				Class: FailureBoundary, Path: "cue/records/record.cue",
				Import: "example.test/corpus/not-authoritative", Rule: "import-layout",
			}
			if *failure != want {
				t.Fatalf("run %d failure = %#v, want %#v", run, *failure, want)
			}
		}
	}
}

func TestImportClosureV1DetectsSnapshotChangeBeforeSuccess(t *testing.T) {
	root := newClosureFixture(t)
	writeClosureFixture(t, root, "cue/records/record.cue", "package records\nrecord: {value: 1}\n")
	snapshot, err := repository.CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	writeClosureFixture(t, root, "cue/records/record.cue", "package records\nrecord: {value: 2}\n")
	closure, err := ResolveImportClosureV1(root, snapshot)
	if closure.Module != "" || len(closure.Files) != 0 || len(closure.Imports) != 0 ||
		len(closure.Edges) != 0 || closure.SHA256 != (Digest{}) {
		t.Fatalf("partial closure returned on failure: %#v", closure)
	}
	var failure *Failure
	if !errors.As(err, &failure) || failure.Class != FailureChanged || failure.Rule != "source-changed" {
		t.Fatalf("failure = %#v, error = %v", failure, err)
	}
}

func TestImportClosureV1ParsesEveryAcceptedAuthorityFile(t *testing.T) {
	root := newClosureFixture(t)
	writeClosureFixture(t, root, "cue/records/record.cue", "package records\nrecord: {}\n")
	writeClosureFixture(t, root, "cue/schema/unused/broken.cue", "package unused\nbroken: [\n")
	snapshot, err := repository.CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	closure, err := ResolveImportClosureV1(root, snapshot)
	if closure.Module != "" || len(closure.Files) != 0 || len(closure.Edges) != 0 || closure.SHA256 != (Digest{}) {
		t.Fatalf("partial closure returned: %#v", closure)
	}
	var failure *Failure
	if !errors.As(err, &failure) || failure == nil {
		t.Fatalf("error = %T %v", err, err)
	}
	want := Failure{Class: FailureSyntax, Path: "cue/schema/unused/broken.cue", Rule: "import-declaration"}
	if *failure != want {
		t.Fatalf("failure = %#v, want %#v", *failure, want)
	}
}

func TestImportClosureV1RejectsCyclesDeterministically(t *testing.T) {
	root := newClosureFixture(t)
	writeClosureFixture(t, root, "cue/records/record.cue", "package records\nimport \"example.test/corpus/cue/schema/a\"\nrecord: a.#A\n")
	writeClosureFixture(t, root, "cue/schema/a/a.cue", "package a\nimport \"example.test/corpus/cue/schema/b\"\n#A: b.#B\n")
	writeClosureFixture(t, root, "cue/schema/b/b.cue", "package b\nimport \"example.test/corpus/cue/schema/a\"\n#B: a.#A\n")
	snapshot, err := repository.CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		_, err := ResolveImportClosureV1(root, snapshot)
		var failure *Failure
		if !errors.As(err, &failure) || failure.Class != FailureUnavailable ||
			failure.Path != "cue/schema/a" || failure.Rule != "import-cycle" {
			t.Fatalf("run %d failure = %#v, error = %v", i, failure, err)
		}
	}
}

func TestImportClosureV1SelectsSmallestIndependentCycle(t *testing.T) {
	for _, imports := range []string{
		"import (\n\t\"example.test/corpus/cue/schema/a\"\n\t\"example.test/corpus/cue/schema/b\"\n)\n",
		"import (\n\t\"example.test/corpus/cue/schema/b\"\n\t\"example.test/corpus/cue/schema/a\"\n)\n",
	} {
		root := newClosureFixture(t)
		writeClosureFixture(t, root, "cue/records/record.cue", "package records\n"+imports+"record: {}\n")
		writeClosureFixture(t, root, "cue/schema/a/a.cue", "package a\nimport \"example.test/corpus/cue/schema/z\"\n#A: z.#Z\n")
		writeClosureFixture(t, root, "cue/schema/b/b.cue", "package b\nimport \"example.test/corpus/cue/schema/b\"\n#B: b.#B\n")
		writeClosureFixture(t, root, "cue/schema/z/z.cue", "package z\nimport \"example.test/corpus/cue/schema/z\"\n#Z: z.#Z\n")
		snapshot, err := repository.CaptureSourceSnapshotV1(root)
		if err != nil {
			t.Fatal(err)
		}
		for run := 0; run < 2; run++ {
			closure, err := ResolveImportClosureV1(root, snapshot)
			if closure.Module != "" || len(closure.Files) != 0 || closure.SHA256 != (Digest{}) {
				t.Fatalf("run %d returned a partial closure: %#v", run, closure)
			}
			var failure *Failure
			want := Failure{Class: FailureUnavailable, Path: "cue/schema/b", Rule: "import-cycle"}
			if !errors.As(err, &failure) || failure == nil || *failure != want {
				t.Fatalf("run %d failure = %#v (%v), want %#v", run, failure, err, want)
			}
		}
	}
}

func newClosureFixture(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	writeClosureFixture(t, root, "cue.mod/module.cue", "module: \"example.test/corpus@v0\"\nlanguage: {version: \"v0.17.0\"}\n")
	if err := os.MkdirAll(filepath.Join(root, "cue", "schema"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "cue", "records"), 0o700); err != nil {
		t.Fatal(err)
	}
	return root
}

func writeClosureFixture(t *testing.T, root, name, content string) {
	t.Helper()
	full := filepath.Join(root, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(full), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(full, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func closureFilePaths(closure ImportClosureV1) []string {
	paths := make([]string, len(closure.Files))
	for i, file := range closure.Files {
		paths[i] = file.Path
	}
	return paths
}
