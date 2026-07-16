// Package repository implements the fixed, hermetic repository authority used
// by cuecodec. It deliberately has no Git integration: tracked, untracked, and
// ignored authoritative files are indistinguishable inputs.
package repository

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"hash"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"
)

const snapshotDomainV1 = "cuecodec/source-snapshot/v1\x00"

var authoritativeRootsV1 = [...]string{
	"cue.mod/module.cue",
	"cue/records",
	"cue/schema",
}

// FailureClass is consumed by the package-level codec error adapter.
type FailureClass string

const (
	FailureBoundary FailureClass = "boundary"
	FailureRead     FailureClass = "read"
	FailureChanged  FailureClass = "changed"
)

// Failure contains stable repository context. Cause text is intentionally not
// exposed: filesystem messages vary across operating systems.
type Failure struct {
	Class FailureClass
	Path  string
	Rule  string
}

func (e *Failure) Error() string {
	if e == nil {
		return "<nil>"
	}
	return fmt.Sprintf("repository: %s path=%q rule=%q", e.Class, e.Path, e.Rule)
}

// Digest is a SHA-256 value. Its String method always uses lowercase hex.
type Digest [sha256.Size]byte

func (d Digest) String() string { return hex.EncodeToString(d[:]) }

// SourceFileV1 is one accepted authoritative input.
type SourceFileV1 struct {
	Path   string
	SHA256 Digest
	Size   int64
}

// SourceSnapshotV1 commits to the path and complete bytes of every accepted
// authoritative input. Files is always byte-sorted by slash-form relative path.
type SourceSnapshotV1 struct {
	Files  []SourceFileV1
	SHA256 Digest

	contents   map[string][]byte
	identities map[string]fs.FileInfo
}

// CaptureSourceSnapshotV1 walks only the declared authority layout. It never
// invokes Git, follows no symlink, and rejects special files in accepted slots.
func CaptureSourceSnapshotV1(root string) (SourceSnapshotV1, error) {
	rootAbs, failure := authorityRoot(root)
	if failure != nil {
		return SourceSnapshotV1{}, failure
	}
	rootHandle, err := os.OpenRoot(rootAbs)
	if err != nil {
		return SourceSnapshotV1{}, &Failure{Class: FailureRead, Path: ".", Rule: "root-unavailable"}
	}
	defer rootHandle.Close()

	contents := make(map[string][]byte)
	identities := make(map[string]fs.FileInfo)
	var failures []*Failure
	if failure := captureOne(rootHandle, authoritativeRootsV1[0], contents, identities); failure != nil {
		failures = append(failures, failure)
	}
	for _, relRoot := range authoritativeRootsV1[1:] {
		if failure := captureTree(rootHandle, relRoot, contents, identities); failure != nil {
			failures = append(failures, failure)
		}
	}
	if selected := selectRepositoryFailure(failures...); selected != nil {
		return SourceSnapshotV1{}, selected
	}

	paths := make([]string, 0, len(contents))
	for name := range contents {
		paths = append(paths, name)
	}
	sort.Strings(paths) // Go string comparison is bytewise.

	files := make([]SourceFileV1, 0, len(paths))
	h := sha256.New()
	_, _ = io.WriteString(h, snapshotDomainV1)
	writeUint64(h, uint64(len(paths)))
	for _, name := range paths {
		content := contents[name]
		fileHash := sha256.Sum256(content)
		files = append(files, SourceFileV1{
			Path: name, SHA256: Digest(fileHash), Size: int64(len(content)),
		})
		writeLengthPrefixed(h, []byte(name))
		writeLengthPrefixed(h, content)
	}

	var digest Digest
	copy(digest[:], h.Sum(nil))
	return SourceSnapshotV1{Files: files, SHA256: digest, contents: contents, identities: identities}, nil
}

// SnapshotSourcesV1 is an internal compatibility spelling used by tests and
// evidence code; it has exactly the same semantics as CaptureSourceSnapshotV1.
func SnapshotSourcesV1(root string) (SourceSnapshotV1, error) {
	return CaptureSourceSnapshotV1(root)
}

// VerifySourceSnapshotV1 re-reads the authority and rejects any inventory or
// byte change. The returned error identifies the first differing byte-sorted
// path and uses the source-changed rule even when a whole root disappeared.
func VerifySourceSnapshotV1(root string, expected SourceSnapshotV1) error {
	actual, err := CaptureSourceSnapshotV1(root)
	if err != nil {
		if failure, ok := err.(*Failure); ok {
			return &Failure{Class: FailureChanged, Path: failure.Path, Rule: "source-changed"}
		}
		return &Failure{Class: FailureChanged, Path: ".", Rule: "source-changed"}
	}
	if expected.SHA256 == actual.SHA256 && equalFileInventory(expected.Files, actual.Files) && equalFileIdentities(expected, actual) {
		return nil
	}
	return &Failure{
		Class: FailureChanged,
		Path:  firstSnapshotDifference(expected, actual),
		Rule:  "source-changed",
	}
}

// Content returns a copy of the snapshotted bytes for name. Evaluation should
// use these bytes, then call VerifySourceSnapshotV1 before publishing a result.
func (s SourceSnapshotV1) Content(name string) ([]byte, bool) {
	content, ok := s.contents[name]
	if !ok {
		return nil, false
	}
	return bytes.Clone(content), true
}

// Clone makes defensive copies of every slice and source byte buffer.
func (s SourceSnapshotV1) Clone() SourceSnapshotV1 {
	clone := SourceSnapshotV1{
		Files: append([]SourceFileV1(nil), s.Files...), SHA256: s.SHA256,
		contents:   make(map[string][]byte, len(s.contents)),
		identities: make(map[string]fs.FileInfo, len(s.identities)),
	}
	for name, content := range s.contents {
		clone.contents[name] = bytes.Clone(content)
	}
	for name, identity := range s.identities {
		clone.identities[name] = identity
	}
	return clone
}

func authorityRoot(root string) (string, *Failure) {
	if root == "" {
		return "", &Failure{Class: FailureBoundary, Path: ".", Rule: "empty-root"}
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return "", &Failure{Class: FailureBoundary, Path: ".", Rule: "invalid-root"}
	}
	info, err := os.Lstat(abs)
	if err != nil {
		return "", &Failure{Class: FailureRead, Path: ".", Rule: "root-unavailable"}
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return "", &Failure{Class: FailureBoundary, Path: ".", Rule: "root-symlink"}
	}
	if !info.IsDir() {
		return "", &Failure{Class: FailureBoundary, Path: ".", Rule: "root-not-directory"}
	}
	resolved, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return "", &Failure{Class: FailureBoundary, Path: ".", Rule: "invalid-root"}
	}
	return filepath.Clean(resolved), nil
}

func captureOne(root *os.Root, name string, contents map[string][]byte, identities map[string]fs.FileInfo) *Failure {
	if failure := authorityPath(root, name); failure != nil {
		return failure
	}
	content, identity, failure := readStableRegular(root, name)
	if failure != nil {
		return failure
	}
	contents[name] = content
	identities[name] = identity
	return nil
}

func captureTree(root *os.Root, relRoot string, contents map[string][]byte, identities map[string]fs.FileInfo) *Failure {
	if failure := authorityPath(root, relRoot); failure != nil {
		return failure
	}
	info, err := root.Lstat(relRoot)
	if err != nil {
		return &Failure{Class: FailureRead, Path: relRoot, Rule: "root-unavailable"}
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return &Failure{Class: FailureBoundary, Path: relRoot, Rule: "symlink"}
	}
	if !info.IsDir() {
		return &Failure{Class: FailureBoundary, Path: relRoot, Rule: "root-not-directory"}
	}

	var failures []*Failure
	err = fs.WalkDir(root.FS(), relRoot, func(name string, entry fs.DirEntry, walkErr error) error {
		if failure := validateRelativePath(name); failure != nil {
			failures = append(failures, failure)
			if entry != nil && entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if walkErr != nil {
			failures = append(failures, &Failure{Class: FailureRead, Path: name, Rule: "walk-unavailable"})
			return nil
		}
		// Re-check every component through the opened repository root. Root.Open
		// and Root.FS cannot escape this handle even if a component is replaced
		// concurrently; the Lstat walk additionally makes every symlink an
		// explicit contract failure rather than an alternate resolution path.
		if failure := authorityPath(root, name); failure != nil {
			failures = append(failures, failure)
			if entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 {
			// Do not inspect the target to decide whether it is authoritative:
			// doing so would itself follow the link. Reject every link below an
			// authority root, including a directory link that could hide .cue files.
			failures = append(failures, &Failure{Class: FailureBoundary, Path: name, Rule: "symlink"})
			return nil
		}
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".cue") {
			return nil
		}
		content, identity, failure := readStableRegular(root, name)
		if failure != nil {
			failures = append(failures, failure)
			return nil
		}
		contents[name] = content
		identities[name] = identity
		return nil
	})
	if err != nil {
		failures = append(failures, &Failure{Class: FailureRead, Path: relRoot, Rule: "walk-unavailable"})
	}
	return selectRepositoryFailure(failures...)
}

func selectRepositoryFailure(candidates ...*Failure) *Failure {
	var selected *Failure
	for _, candidate := range candidates {
		if candidate == nil {
			continue
		}
		if selected == nil || compareRepositoryFailures(candidate, selected) < 0 {
			selected = candidate
		}
	}
	return selected
}

func compareRepositoryFailures(a, b *Failure) int {
	if ar, br := repositoryCodeRank(a.Class), repositoryCodeRank(b.Class); ar != br {
		if ar < br {
			return -1
		}
		return 1
	}
	if a.Path < b.Path {
		return -1
	}
	if a.Path > b.Path {
		return 1
	}
	if ar, br := repositoryRuleRank(a.Rule), repositoryRuleRank(b.Rule); ar != br {
		if ar < br {
			return -1
		}
		return 1
	}
	if a.Rule < b.Rule {
		return -1
	}
	if a.Rule > b.Rule {
		return 1
	}
	return 0
}

func repositoryCodeRank(class FailureClass) uint8 {
	if class == FailureBoundary {
		return 1 // CodeRepositoryBoundary
	}
	if class == FailureRead || class == FailureChanged {
		return 2 // CodeRead
	}
	return ^uint8(0)
}

func repositoryRuleRank(rule string) uint8 {
	order := [...]string{
		"repository-root", "repository", "empty-root", "invalid-root",
		"root-unavailable", "root-symlink", "root-not-directory", "invalid-path",
		"path-escape", "path-unavailable", "symlink", "special-file",
		"file-unavailable", "walk-unavailable", "changed-during-read",
		"snapshot-content", "source-changed",
	}
	for i, candidate := range order {
		if rule == candidate {
			return uint8(i + 1)
		}
	}
	return ^uint8(0)
}

func authorityPath(root *os.Root, name string) *Failure {
	if failure := validateRelativePath(name); failure != nil {
		return failure
	}
	current := ""
	for _, component := range strings.Split(name, "/") {
		current = path.Join(current, component)
		info, statErr := root.Lstat(current)
		if statErr != nil {
			return &Failure{Class: FailureRead, Path: current, Rule: "path-unavailable"}
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return &Failure{Class: FailureBoundary, Path: current, Rule: "symlink"}
		}
	}
	return nil
}

func validateRelativePath(name string) *Failure {
	if name == "" || name == "." || !utf8.ValidString(name) {
		return &Failure{Class: FailureBoundary, Path: name, Rule: "invalid-path"}
	}
	if strings.Contains(name, "\\") || strings.HasPrefix(name, "/") || path.Clean(name) != name {
		return &Failure{Class: FailureBoundary, Path: name, Rule: "path-escape"}
	}
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." {
			return &Failure{Class: FailureBoundary, Path: name, Rule: "path-escape"}
		}
	}
	return nil
}

func readStableRegular(root *os.Root, name string) ([]byte, fs.FileInfo, *Failure) {
	before, err := root.Lstat(name)
	if err != nil {
		return nil, nil, &Failure{Class: FailureRead, Path: name, Rule: "file-unavailable"}
	}
	if before.Mode()&os.ModeSymlink != 0 {
		return nil, nil, &Failure{Class: FailureBoundary, Path: name, Rule: "symlink"}
	}
	if !before.Mode().IsRegular() {
		return nil, nil, &Failure{Class: FailureBoundary, Path: name, Rule: "special-file"}
	}

	f, err := root.Open(name)
	if err != nil {
		return nil, nil, &Failure{Class: FailureRead, Path: name, Rule: "file-unavailable"}
	}
	opened, statErr := f.Stat()
	if statErr != nil || !os.SameFile(before, opened) || !opened.Mode().IsRegular() {
		_ = f.Close()
		return nil, nil, &Failure{Class: FailureChanged, Path: name, Rule: "changed-during-read"}
	}
	content, readErr := io.ReadAll(f)
	closeErr := f.Close()
	after, afterErr := root.Lstat(name)
	if readErr != nil || closeErr != nil {
		return nil, nil, &Failure{Class: FailureRead, Path: name, Rule: "file-unavailable"}
	}
	if afterErr != nil || after.Mode()&os.ModeSymlink != 0 || !os.SameFile(opened, after) ||
		after.Size() != int64(len(content)) || opened.Size() != int64(len(content)) ||
		!opened.ModTime().Equal(after.ModTime()) {
		return nil, nil, &Failure{Class: FailureChanged, Path: name, Rule: "changed-during-read"}
	}
	return content, after, nil
}

func equalFileIdentities(a, b SourceSnapshotV1) bool {
	if len(a.identities) != len(b.identities) {
		return false
	}
	for name, identity := range a.identities {
		other, ok := b.identities[name]
		if !ok || identity == nil || other == nil || !os.SameFile(identity, other) {
			return false
		}
	}
	return true
}

func equalFileInventory(a, b []SourceFileV1) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func firstInventoryDifference(a, b []SourceFileV1) string {
	i, j := 0, 0
	for i < len(a) && j < len(b) {
		if a[i].Path < b[j].Path {
			return a[i].Path
		}
		if b[j].Path < a[i].Path {
			return b[j].Path
		}
		if a[i] != b[j] {
			return a[i].Path
		}
		i++
		j++
	}
	if i < len(a) {
		return a[i].Path
	}
	if j < len(b) {
		return b[j].Path
	}
	return "."
}

func firstSnapshotDifference(a, b SourceSnapshotV1) string {
	if name := firstInventoryDifference(a.Files, b.Files); name != "." {
		return name
	}
	paths := make([]string, 0, len(a.identities)+len(b.identities))
	seen := make(map[string]bool, len(a.identities)+len(b.identities))
	for name := range a.identities {
		seen[name] = true
		paths = append(paths, name)
	}
	for name := range b.identities {
		if !seen[name] {
			paths = append(paths, name)
		}
	}
	sort.Strings(paths)
	for _, name := range paths {
		left, leftOK := a.identities[name]
		right, rightOK := b.identities[name]
		if !leftOK || !rightOK || left == nil || right == nil || !os.SameFile(left, right) {
			return name
		}
	}
	return "."
}

func writeUint64(w hash.Hash, n uint64) {
	var buf [8]byte
	binary.BigEndian.PutUint64(buf[:], n)
	_, _ = w.Write(buf[:])
}

func writeLengthPrefixed(w hash.Hash, value []byte) {
	writeUint64(w, uint64(len(value)))
	_, _ = w.Write(value)
}
