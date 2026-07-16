// Package cueload resolves the permitted, offline CUE import closure over a
// repository.SourceSnapshotV1. Resolution is a pure operation over snapshotted
// bytes; it never consults a registry, proxy, module cache, or Git.
package cueload

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"hash"
	"io"
	"path"
	"sort"
	"strconv"
	"strings"
	"text/scanner"

	"cuelang.org/go/cue/parser"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

const closureDomainV1 = "cuecodec/import-closure/v1\x00"

const languageVersionV1 = "v0.17.0"

// FailureClass is mapped to the public CodecError classification by the codec
// package, avoiding an import cycle between internal loading and the API.
type FailureClass string

const (
	FailureBoundary    FailureClass = "boundary"
	FailureUnavailable FailureClass = "unavailable"
	FailureSyntax      FailureClass = "syntax"
	FailureChanged     FailureClass = "changed"
)

type Failure struct {
	Class  FailureClass
	Path   string
	Import string
	Rule   string
}

func (e *Failure) Error() string {
	if e == nil {
		return "<nil>"
	}
	return fmt.Sprintf("cue import closure: %s path=%q import=%q rule=%q", e.Class, e.Path, e.Import, e.Rule)
}

// Digest is a SHA-256 value with a canonical lowercase hexadecimal rendering.
type Digest [sha256.Size]byte

func (d Digest) String() string { return hex.EncodeToString(d[:]) }

// ImportEdgeV1 records the importing source, literal import path, and hermetic
// resolution. Builtins resolve to "builtin:" plus their literal path.
type ImportEdgeV1 struct {
	From     string
	Import   string
	Resolved string
	Builtin  bool
}

// ImportClosureV1 is a byte-sorted closure. Its digest commits to the complete
// source snapshot, so a changed unused authoritative source also invalidates it.
type ImportClosureV1 struct {
	Module          string
	LanguageVersion string
	Files           []repository.SourceFileV1
	Imports         []string
	Edges           []ImportEdgeV1
	SHA256          Digest
}

// ResolveImportClosureV1 resolves imports from every accepted authoritative
// package. Local imports may resolve only below cue/schema or cue/records; the
// other permitted imports are an explicit CUE standard-library table. Unknown
// imports fail closed rather than triggering network access.
func ResolveImportClosureV1(root string, snapshot repository.SourceSnapshotV1) (ImportClosureV1, error) {
	if err := repository.VerifySourceSnapshotV1(root, snapshot); err != nil {
		return ImportClosureV1{}, changedFailure(err)
	}
	if failure := validateSnapshotContents(snapshot); failure != nil {
		return ImportClosureV1{}, failure
	}

	moduleBytes, ok := snapshot.Content("cue.mod/module.cue")
	if !ok {
		return ImportClosureV1{}, &Failure{Class: FailureChanged, Path: "cue.mod/module.cue", Rule: "snapshot-content"}
	}
	module, languageVersion, metadataFailures := parseModuleMetadata(moduleBytes)
	for _, failure := range metadataFailures {
		failure.Path = "cue.mod/module.cue"
	}
	if selected := selectClosureFailure(metadataFailures...); selected != nil {
		return ImportClosureV1{}, selected
	}
	modulePrefix, _ := importableModulePrefix(module)

	filesByDir := make(map[string][]repository.SourceFileV1)
	var queue []string
	queued := make(map[string]bool)
	for _, file := range snapshot.Files {
		if !strings.HasSuffix(file.Path, ".cue") || file.Path == "cue.mod/module.cue" {
			continue
		}
		dir := path.Dir(file.Path)
		filesByDir[dir] = append(filesByDir[dir], file)
		// Every accepted file under both declared roots is active authority.
		// Fixtures and outputs must live outside these roots; consequently an
		// unavailable import cannot hide in an otherwise unreferenced schema.
		if (withinRoot(dir, "cue/records") || withinRoot(dir, "cue/schema")) && !queued[dir] {
			queue = append(queue, dir)
			queued[dir] = true
		}
	}
	for dir := range filesByDir {
		sort.Slice(filesByDir[dir], func(i, j int) bool {
			return filesByDir[dir][i].Path < filesByDir[dir][j].Path
		})
	}
	sort.Strings(queue)

	processedDir := make(map[string]bool)
	graph := make(map[string][]string)
	var edges []ImportEdgeV1
	var failures []*Failure
	importsSet := make(map[string]bool)

	for len(queue) > 0 {
		dir := queue[0]
		queue = queue[1:]
		if processedDir[dir] {
			continue
		}
		processedDir[dir] = true
		for _, file := range filesByDir[dir] {
			content, _ := snapshot.Content(file.Path)
			imports, scanFailures := scanImports(file.Path, content)
			failures = append(failures, scanFailures...)
			sort.Strings(imports)
			for _, imported := range imports {
				edge, targetDir, failure := resolveImport(file.Path, imported, module, modulePrefix, filesByDir)
				if failure != nil {
					failures = append(failures, failure)
					continue
				}
				edges = append(edges, edge)
				importsSet[imported] = true
				if edge.Builtin {
					continue
				}
				graph[dir] = append(graph[dir], targetDir)
				if !queued[targetDir] && !processedDir[targetDir] {
					queue = append(queue, targetDir)
					queued[targetDir] = true
					sort.Strings(queue)
				}
			}
		}
	}

	for dir := range graph {
		sort.Strings(graph[dir])
		graph[dir] = compactStrings(graph[dir])
	}
	for _, cyclePath := range importCyclePaths(graph) {
		failures = append(failures, &Failure{Class: FailureUnavailable, Path: cyclePath, Rule: "import-cycle"})
	}
	if selected := selectClosureFailure(failures...); selected != nil {
		return ImportClosureV1{}, selected
	}

	sort.Slice(edges, func(i, j int) bool {
		a, b := edges[i], edges[j]
		if a.From != b.From {
			return a.From < b.From
		}
		if a.Import != b.Import {
			return a.Import < b.Import
		}
		if a.Resolved != b.Resolved {
			return a.Resolved < b.Resolved
		}
		return !a.Builtin && b.Builtin
	})

	imports := make([]string, 0, len(importsSet))
	for imported := range importsSet {
		imports = append(imports, imported)
	}
	sort.Strings(imports)

	// ImportClosureV1 reports the complete frozen authoritative inventory, not
	// merely the semantically reached subset. This keeps an unused authoritative
	// schema content-bound and makes later activation observable in the same
	// digest without consulting the host filesystem again.
	files := append([]repository.SourceFileV1(nil), snapshot.Files...)

	closure := ImportClosureV1{Module: module, LanguageVersion: languageVersion, Files: files, Imports: imports, Edges: edges}
	closure.SHA256 = hashClosure(snapshot.SHA256, closure)
	if err := repository.VerifySourceSnapshotV1(root, snapshot); err != nil {
		return ImportClosureV1{}, changedFailure(err)
	}
	return closure, nil
}

// selectClosureFailure mirrors the public error-precedence-v1 tuple at the
// internal boundary: public code ordinal, source path bytes, then RuleID
// ordinal. Import literals are diagnostic data only and cannot select a public
// result because CodecError does not expose them.
func selectClosureFailure(candidates ...*Failure) *Failure {
	var selected *Failure
	for _, candidate := range candidates {
		if candidate == nil {
			continue
		}
		if selected == nil || compareClosureFailures(candidate, selected) < 0 {
			selected = candidate
		}
	}
	return selected
}

func compareClosureFailures(a, b *Failure) int {
	if ar, br := closureCodeRank(a.Class), closureCodeRank(b.Class); ar != br {
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
	if ar, br := closureRuleRank(a.Rule), closureRuleRank(b.Rule); ar != br {
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

func closureCodeRank(class FailureClass) uint8 {
	switch class {
	case FailureBoundary:
		return 1 // CodeRepositoryBoundary
	case FailureUnavailable, FailureChanged:
		return 2 // CodeRead
	case FailureSyntax:
		return 3 // CodeSyntax is the next public stage
	default:
		return ^uint8(0)
	}
}

func closureRuleRank(rule string) uint8 {
	order := [...]string{
		"module-field", "module-path", "language-field", "language-version",
		"package-layout", "import-closure", "offline-import", "import-layout",
		"import-unavailable", "import-escape", "import-cycle",
		"import-declaration", "import-path", "snapshot-content", "source-changed",
		"cue-load", "cue-build", "package-instance", "nil-cue-context",
	}
	for i, candidate := range order {
		if rule == candidate {
			return uint8(i + 1)
		}
	}
	return ^uint8(0)
}

// ComputeImportClosureV1 is an internal compatibility spelling.
func ComputeImportClosureV1(root string, snapshot repository.SourceSnapshotV1) (ImportClosureV1, error) {
	return ResolveImportClosureV1(root, snapshot)
}

// DeclaredModuleIdentityV1 returns the single syntactically readable module
// literal from frozen metadata. Other metadata faults (for example, a malformed
// language field) do not hide that identity. An absent, malformed, or duplicate
// module declaration is not treated as a usable identity.
func DeclaredModuleIdentityV1(snapshot repository.SourceSnapshotV1) (string, bool) {
	content, ok := snapshot.Content("cue.mod/module.cue")
	if !ok {
		return "", false
	}
	return scanDeclaredModuleLiteral(content)
}

func scanDeclaredModuleLiteral(content []byte) (string, bool) {
	var s scanner.Scanner
	s.Init(bytes.NewReader(content))
	s.Mode = scanner.ScanIdents | scanner.ScanStrings | scanner.ScanRawStrings | scanner.SkipComments
	s.Error = func(*scanner.Scanner, string) {}
	depth := 0
	count := 0
	module := ""
	valid := false
	for tok := s.Scan(); tok != scanner.EOF; tok = s.Scan() {
		switch tok {
		case '{', '[', '(':
			depth++
		case '}', ']', ')':
			if depth > 0 {
				depth--
			}
		case scanner.Ident:
			if depth != 0 || s.TokenText() != "module" {
				continue
			}
			count++
			value, ok := scanStringField(&s)
			if count == 1 && ok && value != "" {
				module, valid = value, true
			} else {
				valid = false
			}
		}
	}
	if count != 1 || !valid {
		return "", false
	}
	return module, true
}

func validateSnapshotContents(snapshot repository.SourceSnapshotV1) *Failure {
	for _, file := range snapshot.Files {
		content, ok := snapshot.Content(file.Path)
		if !ok || int64(len(content)) != file.Size {
			return &Failure{Class: FailureChanged, Path: file.Path, Rule: "snapshot-content"}
		}
		digest := sha256.Sum256(content)
		if repository.Digest(digest) != file.SHA256 {
			return &Failure{Class: FailureChanged, Path: file.Path, Rule: "snapshot-content"}
		}
	}
	return nil
}

func changedFailure(err error) *Failure {
	if failure, ok := err.(*repository.Failure); ok {
		return &Failure{Class: FailureChanged, Path: failure.Path, Rule: "source-changed"}
	}
	return &Failure{Class: FailureChanged, Path: ".", Rule: "source-changed"}
}

func parseModuleMetadata(content []byte) (string, string, []*Failure) {
	var s scanner.Scanner
	s.Init(bytes.NewReader(content))
	s.Mode = scanner.ScanIdents | scanner.ScanStrings | scanner.ScanRawStrings | scanner.SkipComments
	s.Error = func(*scanner.Scanner, string) {}
	depth := 0
	var module, languageVersion string
	var failures []*Failure
	var sawModule, sawLanguage bool
	for tok := s.Scan(); tok != scanner.EOF; tok = s.Scan() {
		switch tok {
		case '{', '[', '(':
			depth++
		case '}', ']', ')':
			if depth > 0 {
				depth--
			}
		case scanner.Ident:
			if depth != 0 {
				continue
			}
			switch s.TokenText() {
			case "module":
				if sawModule {
					failures = appendMetadataFailure(failures, "module-field")
					continue
				}
				sawModule = true
				value, ok := scanStringField(&s)
				if !ok || value == "" {
					failures = appendMetadataFailure(failures, "module-field")
					continue
				}
				module = value
			case "language":
				if sawLanguage {
					failures = appendMetadataFailure(failures, "language-field")
					continue
				}
				sawLanguage = true
				if s.Scan() != ':' {
					failures = appendMetadataFailure(failures, "language-field")
					continue
				}
				next := s.Scan()
				var value string
				var ok bool
				switch {
				case next == '{':
					value, ok = scanLanguageBlock(&s)
				case next == scanner.Ident && s.TokenText() == "version":
					value, ok = scanStringField(&s)
				}
				if !ok || value == "" {
					failures = appendMetadataFailure(failures, "language-field")
					continue
				}
				languageVersion = value
			}
		}
	}
	if s.ErrorCount != 0 || depth != 0 || !sawModule || module == "" {
		failures = appendMetadataFailure(failures, "module-field")
	} else if _, ok := importableModulePrefix(module); !ok {
		failures = appendMetadataFailure(failures, "module-path")
	}
	if !sawLanguage || languageVersion == "" {
		failures = appendMetadataFailure(failures, "language-field")
	} else if languageVersion != languageVersionV1 {
		failures = appendMetadataFailure(failures, "language-version")
	}
	return module, languageVersion, failures
}

func appendMetadataFailure(failures []*Failure, rule string) []*Failure {
	for _, failure := range failures {
		if failure.Rule == rule {
			return failures
		}
	}
	return append(failures, &Failure{Class: FailureSyntax, Rule: rule})
}

func scanStringField(s *scanner.Scanner) (string, bool) {
	if s.Scan() != ':' || s.Scan() != scanner.String {
		return "", false
	}
	value, err := strconv.Unquote(s.TokenText())
	return value, err == nil
}

func scanLanguageBlock(s *scanner.Scanner) (string, bool) {
	depth := 1
	var version string
	for tok := s.Scan(); tok != scanner.EOF; tok = s.Scan() {
		switch tok {
		case '{', '[', '(':
			depth++
		case '}', ']', ')':
			depth--
			if depth == 0 {
				return version, version != ""
			}
		case scanner.Ident:
			if depth == 1 && s.TokenText() == "version" {
				value, ok := scanStringField(s)
				if !ok || value == "" || version != "" {
					return "", false
				}
				version = value
			}
		}
		if depth < 1 {
			return "", false
		}
	}
	return "", false
}

func scanImports(filename string, content []byte) ([]string, []*Failure) {
	// Parse the complete accepted file, not only its import section. Every byte
	// below an authoritative root is active authority even when its package is
	// not currently imported by the first record. When the body is malformed,
	// parse the import section independently as well: a valid import literal is
	// still an independently detectable boundary or availability fault, and the
	// closure tuple decides which candidate is public.
	file, err := parser.ParseFile(filename, content, parser.Version("v0.17.0"))
	var failures []*Failure
	if err != nil {
		failures = append(failures, &Failure{Class: FailureSyntax, Path: filename, Rule: "import-declaration"})
		importsFile, _ := parser.ParseFile(filename, content, parser.ImportsOnly, parser.Version("v0.17.0"))
		if importsFile != nil {
			file = importsFile
		}
	}
	if file == nil {
		return nil, failures
	}
	var imports []string
	for spec := range file.ImportSpecs() {
		if spec == nil || spec.Path == nil {
			failures = append(failures, &Failure{Class: FailureSyntax, Path: filename, Rule: "import-declaration"})
			continue
		}
		value, err := strconv.Unquote(spec.Path.Value)
		if err != nil || value == "" {
			failures = append(failures, &Failure{Class: FailureSyntax, Path: filename, Rule: "import-path"})
			continue
		}
		imports = append(imports, value)
	}
	return imports, failures
}

func resolveImport(from, imported, module, modulePrefix string, filesByDir map[string][]repository.SourceFileV1) (ImportEdgeV1, string, *Failure) {
	bare := stripPackageQualifier(imported)
	if bare == "" || strings.Contains(bare, "\\") || strings.HasPrefix(bare, "/") ||
		strings.HasPrefix(bare, ".") || path.Clean(bare) != bare {
		return ImportEdgeV1{}, "", &Failure{Class: FailureBoundary, Path: from, Import: imported, Rule: "import-escape"}
	}
	for _, part := range strings.Split(bare, "/") {
		if part == "" || part == "." || part == ".." {
			return ImportEdgeV1{}, "", &Failure{Class: FailureBoundary, Path: from, Import: imported, Rule: "import-escape"}
		}
	}
	if isCUEBuiltin(bare) {
		return ImportEdgeV1{From: from, Import: imported, Resolved: "builtin:" + bare, Builtin: true}, "", nil
	}

	var suffix string
	for _, prefix := range []string{module, modulePrefix} {
		if bare == prefix {
			suffix = "."
			break
		}
		if strings.HasPrefix(bare, prefix+"/") {
			suffix = strings.TrimPrefix(bare, prefix+"/")
			break
		}
	}
	if suffix == "" {
		return ImportEdgeV1{}, "", &Failure{Class: FailureUnavailable, Path: from, Import: imported, Rule: "offline-import"}
	}
	if suffix == "." || (!withinRoot(suffix, "cue/schema") && !withinRoot(suffix, "cue/records")) {
		return ImportEdgeV1{}, "", &Failure{Class: FailureBoundary, Path: from, Import: imported, Rule: "import-layout"}
	}
	if len(filesByDir[suffix]) == 0 {
		return ImportEdgeV1{}, "", &Failure{Class: FailureUnavailable, Path: from, Import: imported, Rule: "import-unavailable"}
	}
	return ImportEdgeV1{From: from, Import: imported, Resolved: suffix}, suffix, nil
}

func importableModulePrefix(module string) (string, bool) {
	if module == "" || strings.Contains(module, "\\") || strings.HasPrefix(module, "/") || path.Clean(module) != module {
		return "", false
	}
	at := strings.LastIndexByte(module, '@')
	if at < 1 {
		return module, true
	}
	version := module[at+1:]
	if len(version) < 2 || version[0] != 'v' {
		return "", false
	}
	for _, r := range version[1:] {
		if r < '0' || r > '9' {
			return "", false
		}
	}
	return module[:at], true
}

func stripPackageQualifier(imported string) string {
	colon := strings.LastIndexByte(imported, ':')
	if colon > strings.LastIndexByte(imported, '/') {
		return imported[:colon]
	}
	return imported
}

func withinRoot(name, root string) bool {
	return name == root || strings.HasPrefix(name, root+"/")
}

func importCyclePaths(graph map[string][]string) []string {
	// A DFS's first back edge is traversal topology, not error precedence. Find
	// every cyclic strongly connected component and expose its byte-smallest
	// member as an independent candidate for the closure tuple.
	nodeSet := make(map[string]bool)
	for node, targets := range graph {
		nodeSet[node] = true
		for _, target := range targets {
			nodeSet[target] = true
		}
	}
	nodes := make([]string, 0, len(nodeSet))
	for node := range nodeSet {
		nodes = append(nodes, node)
	}
	sort.Strings(nodes)

	index := 0
	indices := make(map[string]int)
	lowlinks := make(map[string]int)
	onStack := make(map[string]bool)
	var stack []string
	var cycles []string
	var visit func(string)
	visit = func(node string) {
		index++
		indices[node] = index
		lowlinks[node] = index
		stack = append(stack, node)
		onStack[node] = true

		for _, target := range graph[node] {
			if indices[target] == 0 {
				visit(target)
				if lowlinks[target] < lowlinks[node] {
					lowlinks[node] = lowlinks[target]
				}
			} else if onStack[target] && indices[target] < lowlinks[node] {
				lowlinks[node] = indices[target]
			}
		}
		if lowlinks[node] != indices[node] {
			return
		}

		var component []string
		for {
			last := len(stack) - 1
			member := stack[last]
			stack = stack[:last]
			onStack[member] = false
			component = append(component, member)
			if member == node {
				break
			}
		}
		sort.Strings(component)
		cyclic := len(component) > 1
		if !cyclic {
			for _, target := range graph[component[0]] {
				if target == component[0] {
					cyclic = true
					break
				}
			}
		}
		if cyclic {
			cycles = append(cycles, component[0])
		}
	}
	for _, node := range nodes {
		if indices[node] == 0 {
			visit(node)
		}
	}
	sort.Strings(cycles)
	return cycles
}

func compactStrings(values []string) []string {
	if len(values) < 2 {
		return values
	}
	out := values[:1]
	for _, value := range values[1:] {
		if value != out[len(out)-1] {
			out = append(out, value)
		}
	}
	return out
}

func hashClosure(snapshot repository.Digest, closure ImportClosureV1) Digest {
	h := sha256.New()
	_, _ = io.WriteString(h, closureDomainV1)
	_, _ = h.Write(snapshot[:])
	writeLengthPrefixed(h, []byte(closure.Module))
	writeLengthPrefixed(h, []byte(closure.LanguageVersion))
	writeUint64(h, uint64(len(closure.Files)))
	for _, file := range closure.Files {
		writeLengthPrefixed(h, []byte(file.Path))
	}
	writeUint64(h, uint64(len(closure.Edges)))
	for _, edge := range closure.Edges {
		writeLengthPrefixed(h, []byte(edge.From))
		writeLengthPrefixed(h, []byte(edge.Import))
		writeLengthPrefixed(h, []byte(edge.Resolved))
		if edge.Builtin {
			_, _ = h.Write([]byte{1})
		} else {
			_, _ = h.Write([]byte{0})
		}
	}
	var digest Digest
	copy(digest[:], h.Sum(nil))
	return digest
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

var cueBuiltins = map[string]struct{}{
	"crypto/ed25519": {}, "crypto/hmac": {}, "crypto/md5": {},
	"crypto/sha1": {}, "crypto/sha256": {}, "crypto/sha512": {},
	"encoding/base64": {}, "encoding/csv": {}, "encoding/hex": {},
	"encoding/json": {}, "encoding/openapi": {}, "encoding/toml": {},
	"encoding/yaml": {}, "html": {}, "list": {}, "math": {},
	"math/bits": {}, "net": {}, "path": {}, "regexp": {},
	"strconv": {}, "strings": {}, "struct": {}, "text/tabwriter": {},
	"text/template": {}, "time": {},
}

func isCUEBuiltin(importPath string) bool {
	_, ok := cueBuiltins[importPath]
	return ok
}
