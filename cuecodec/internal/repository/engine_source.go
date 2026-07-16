package repository

import (
	"bytes"
	"crypto/sha256"
	"io"
	"io/fs"
	"path"
	"sort"
	"strings"
	"unicode/utf8"
)

const engineSnapshotDomainV2 = "cuecodec/engine-authority-snapshot/v2\x00"

// EngineSourceSnapshotV2 is the immutable sibling-engine authority capture.
// Unlike SourceSnapshotV1 it accepts an explicit closed inventory because the
// bundle is an embed.FS, not a caller-selected repository root.
type EngineSourceSnapshotV2 struct {
	Files  []SourceFileV1
	SHA256 Digest

	contents map[string][]byte
}

// CaptureEngineSourceV2 reads every declared path exactly once, rejects
// aliases and special files, and hashes path plus complete bytes in byte order.
func CaptureEngineSourceV2(authority fs.FS, names []string) (EngineSourceSnapshotV2, error) {
	if authority == nil || len(names) == 0 {
		return EngineSourceSnapshotV2{}, &Failure{Class: FailureBoundary, Path: ".", Rule: "engine-authority-empty"}
	}
	paths := append([]string(nil), names...)
	sort.Strings(paths)
	contents := make(map[string][]byte, len(paths))
	files := make([]SourceFileV1, 0, len(paths))
	h := sha256.New()
	_, _ = io.WriteString(h, engineSnapshotDomainV2)
	for index, name := range paths {
		if name == "" || path.Clean(name) != name || strings.HasPrefix(name, "/") || strings.HasPrefix(name, "../") || (index > 0 && paths[index-1] == name) {
			return EngineSourceSnapshotV2{}, &Failure{Class: FailureBoundary, Path: name, Rule: "engine-authority-path"}
		}
		info, err := fs.Stat(authority, name)
		if err != nil {
			return EngineSourceSnapshotV2{}, &Failure{Class: FailureRead, Path: name, Rule: "engine-authority-unavailable"}
		}
		if !info.Mode().IsRegular() {
			return EngineSourceSnapshotV2{}, &Failure{Class: FailureBoundary, Path: name, Rule: "engine-authority-file"}
		}
		content, err := fs.ReadFile(authority, name)
		if err != nil {
			return EngineSourceSnapshotV2{}, &Failure{Class: FailureRead, Path: name, Rule: "engine-authority-unavailable"}
		}
		if !utf8.Valid(content) {
			return EngineSourceSnapshotV2{}, &Failure{Class: FailureBoundary, Path: name, Rule: "engine-authority-utf8"}
		}
		content = bytes.Clone(content)
		contents[name] = content
		fileDigest := sha256.Sum256(content)
		files = append(files, SourceFileV1{Path: name, SHA256: Digest(fileDigest), Size: int64(len(content))})
		writeLengthPrefixed(h, []byte(name))
		writeLengthPrefixed(h, content)
	}
	var digest Digest
	copy(digest[:], h.Sum(nil))
	return EngineSourceSnapshotV2{Files: files, SHA256: digest, contents: contents}, nil
}

// Content returns a defensive copy of frozen source bytes.
func (s EngineSourceSnapshotV2) Content(name string) ([]byte, bool) {
	content, ok := s.contents[name]
	return bytes.Clone(content), ok
}

// VerifyEngineSourceV2 rejects any path, byte, or digest drift since capture.
func VerifyEngineSourceV2(authority fs.FS, names []string, expected EngineSourceSnapshotV2) error {
	actual, err := CaptureEngineSourceV2(authority, names)
	if err != nil || actual.SHA256 != expected.SHA256 || !equalFileInventory(expected.Files, actual.Files) {
		return &Failure{Class: FailureChanged, Path: ".", Rule: "engine-authority-changed"}
	}
	return nil
}
