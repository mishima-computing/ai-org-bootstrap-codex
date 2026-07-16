package repository

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
)

func TestSourceSnapshotV1FixedLayoutAndStableOrder(t *testing.T) {
	root := newAuthorityFixture(t)
	writeFixture(t, root, ".gitignore", "cue/records/ignored.cue\n.ai-org/\n")
	writeFixture(t, root, "cue/records/z.cue", "package records\nz: 1\n")
	writeFixture(t, root, "cue/records/ignored.cue", "package records\nignored: true\n")
	writeFixture(t, root, "cue/schema/a.cue", "package schema\n#a: string\n")
	writeFixture(t, root, "generated/output.cue", "package generated\nvalue: 1\n")
	writeFixture(t, root, ".ai-org/state.cue", "package state\nvalue: 1\n")
	writeFixture(t, root, ".cuecodec-state/cache.cue", "package state\nvalue: 1\n")
	writeFixture(t, root, "testdata/inactive.cue", "package inactive\nvalue: 1\n")
	writeFixture(t, root, "evidence/proof.cue", "package evidence\nvalue: 1\n")
	writeFixture(t, root, "coverage/map.cue", "package coverage\nvalue: 1\n")

	first, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	second, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	wantPaths := []string{
		"cue.mod/module.cue",
		"cue/records/ignored.cue",
		"cue/records/z.cue",
		"cue/schema/a.cue",
	}
	if got := inventoryPaths(first.Files); !reflect.DeepEqual(got, wantPaths) {
		t.Fatalf("inventory paths = %#v, want %#v", got, wantPaths)
	}
	if first.SHA256 != second.SHA256 || !equalFileInventory(first.Files, second.Files) {
		t.Fatal("repeated snapshot was not stable")
	}

	// Non-authoritative outputs and state are excluded by layout, not ignore
	// rules, so changing them cannot affect the authority hash.
	writeFixture(t, root, "generated/output.cue", "package generated\nvalue: 2\n")
	writeFixture(t, root, ".ai-org/state.cue", "package state\nvalue: 2\n")
	writeFixture(t, root, ".cuecodec-state/cache.cue", "package state\nvalue: 2\n")
	writeFixture(t, root, "testdata/inactive.cue", "package inactive\nvalue: 2\n")
	writeFixture(t, root, "evidence/proof.cue", "package evidence\nvalue: 2\n")
	writeFixture(t, root, "coverage/map.cue", "package coverage\nvalue: 2\n")
	third, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	if first.SHA256 != third.SHA256 {
		t.Fatal("non-authoritative file changed the source snapshot")
	}
}

func TestSourceSnapshotV1TreatsTrackedUntrackedAndIgnoredFilesIdentically(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skipf("git prerequisite unavailable: %v", err)
	}
	root := newAuthorityFixture(t)
	writeFixture(t, root, ".gitignore", "cue/records/ignored.cue\n")
	writeFixture(t, root, "cue/schema/tracked.cue", "package schema\n#Tracked: string\n")
	writeFixture(t, root, "cue/records/untracked.cue", "package records\nuntracked: true\n")
	writeFixture(t, root, "cue/records/ignored.cue", "package records\nignored: true\n")
	runGitFixture(t, root, "init", "-q")
	runGitFixture(t, root, "add", "-f", "cue.mod/module.cue", "cue/schema/tracked.cue")
	if output := runGitFixture(t, root, "ls-files", "--error-unmatch", "cue/schema/tracked.cue"); output == "" {
		t.Fatal("tracked fixture is not in the index")
	}
	if output := runGitFixture(t, root, "ls-files", "--others", "--exclude-standard", "cue/records/untracked.cue"); output == "" {
		t.Fatal("untracked fixture was not reported by Git")
	}
	cmd := exec.Command("git", "-c", "core.excludesFile=/dev/null", "-C", root, "check-ignore", "-q", "cue/records/ignored.cue")
	if err := cmd.Run(); err != nil {
		t.Fatalf("ignored fixture was not ignored: %v", err)
	}

	ignored, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	wantPaths := []string{
		"cue.mod/module.cue",
		"cue/records/ignored.cue",
		"cue/records/untracked.cue",
		"cue/schema/tracked.cue",
	}
	if got := inventoryPaths(ignored.Files); !reflect.DeepEqual(got, wantPaths) {
		t.Fatalf("inventory = %#v, want %#v", got, wantPaths)
	}

	// Ignore policy is not authority. Removing only the ignore match while all
	// accepted bytes remain identical must preserve both inventory and digest.
	writeFixture(t, root, ".gitignore", "# no accepted path is ignored\n")
	visible, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	if ignored.SHA256 != visible.SHA256 || !equalFileInventory(ignored.Files, visible.Files) {
		t.Fatal("Git ignore state changed source authority")
	}
}

func TestSourceSnapshotV1DetectsChangedInputs(t *testing.T) {
	root := newAuthorityFixture(t)
	writeFixture(t, root, "cue/records/record.cue", "package records\nvalue: 1\n")

	snapshot, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	writeFixture(t, root, "cue/records/record.cue", "package records\nvalue: 2\n")
	err = VerifySourceSnapshotV1(root, snapshot)
	var failure *Failure
	if !errors.As(err, &failure) {
		t.Fatalf("VerifySourceSnapshotV1 error = %T %v", err, err)
	}
	if failure.Class != FailureChanged || failure.Path != "cue/records/record.cue" || failure.Rule != "source-changed" {
		t.Fatalf("failure = %#v", failure)
	}
}

func TestSourceSnapshotV1DetectsSameByteFileReplacement(t *testing.T) {
	root := newAuthorityFixture(t)
	const name = "cue/records/record.cue"
	const content = "package records\nvalue: 1\n"
	writeFixture(t, root, name, content)
	snapshot, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, filepath.FromSlash(name))
	replacement, err := os.CreateTemp(filepath.Dir(target), "replacement-*.cue")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := replacement.WriteString(content); err != nil {
		_ = replacement.Close()
		t.Fatal(err)
	}
	if err := replacement.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement.Name(), target); err != nil {
		t.Fatal(err)
	}
	err = VerifySourceSnapshotV1(root, snapshot)
	var failure *Failure
	if !errors.As(err, &failure) || failure.Class != FailureChanged || failure.Path != name {
		t.Fatalf("same-byte replacement failure = %#v, error = %v", failure, err)
	}
}

func TestSourceSnapshotV1RejectsSymlinkWithoutFollowing(t *testing.T) {
	root := newAuthorityFixture(t)
	outside := filepath.Join(t.TempDir(), "outside.cue")
	if err := os.WriteFile(outside, []byte("package escaped\nvalue: 1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "cue", "schema", "escape.cue")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}

	_, err := CaptureSourceSnapshotV1(root)
	var failure *Failure
	if !errors.As(err, &failure) {
		t.Fatalf("CaptureSourceSnapshotV1 error = %T %v", err, err)
	}
	if failure.Class != FailureBoundary || failure.Path != "cue/schema/escape.cue" || failure.Rule != "symlink" {
		t.Fatalf("failure = %#v", failure)
	}
}

func TestSourceSnapshotV1SelectsBoundaryBeforeReadWithinStage(t *testing.T) {
	root := newAuthorityFixture(t)
	if err := os.Remove(filepath.Join(root, "cue.mod", "module.cue")); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "outside.cue")
	if err := os.WriteFile(outside, []byte("package outside\nvalue: true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	linkName := filepath.Join(root, "cue", "schema", "escape.cue")
	if err := os.Symlink(outside, linkName); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	want := Failure{Class: FailureBoundary, Path: "cue/schema/escape.cue", Rule: "symlink"}
	for run := 0; run < 2; run++ {
		snapshot, err := CaptureSourceSnapshotV1(root)
		if len(snapshot.Files) != 0 || snapshot.SHA256 != (Digest{}) {
			t.Fatalf("run %d returned a partial snapshot: %#v", run, snapshot)
		}
		var failure *Failure
		if !errors.As(err, &failure) || failure == nil || *failure != want {
			t.Fatalf("run %d failure = %#v (%v), want %#v", run, failure, err, want)
		}
	}
}

func TestSourceSnapshotV1ContentIsDefensiveCopy(t *testing.T) {
	root := newAuthorityFixture(t)
	writeFixture(t, root, "cue/records/record.cue", "package records\nvalue: 1\n")
	snapshot, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		t.Fatal(err)
	}

	first, ok := snapshot.Content("cue/records/record.cue")
	if !ok {
		t.Fatal("missing content")
	}
	first[0] = 'X'
	second, _ := snapshot.Content("cue/records/record.cue")
	if second[0] == 'X' {
		t.Fatal("Content exposed mutable snapshot storage")
	}
}

func newAuthorityFixture(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	writeFixture(t, root, "cue.mod/module.cue", "module: \"example.test/corpus@v0\"\nlanguage: version: \"v0.17.0\"\n")
	if err := os.MkdirAll(filepath.Join(root, "cue", "schema"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "cue", "records"), 0o700); err != nil {
		t.Fatal(err)
	}
	return root
}

func writeFixture(t *testing.T, root, name, content string) {
	t.Helper()
	full := filepath.Join(root, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(full), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(full, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func inventoryPaths(files []SourceFileV1) []string {
	paths := make([]string, len(files))
	for i, file := range files {
		paths[i] = file.Path
	}
	return paths
}

func runGitFixture(t *testing.T, root string, args ...string) string {
	t.Helper()
	fullArgs := append([]string{"-c", "core.excludesFile=/dev/null", "-C", root}, args...)
	output, err := exec.Command("git", fullArgs...).CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, output)
	}
	return string(output)
}
