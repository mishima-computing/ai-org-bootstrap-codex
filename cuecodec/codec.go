package cuecodec

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"

	"cuelang.org/go/cue"
	"cuelang.org/go/cue/cuecontext"
	cueerrors "cuelang.org/go/cue/errors"
	"github.com/mishima-computing/cuecodec/internal/cueload"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

const (
	defaultMaxDepth = 64
	defaultMaxBytes = 8 << 20

	modulePathV1    = "github.com/mishima-computing/cuecodec@v0"
	schemaPathV1    = "cue/schema/proof_document.cue"
	recordPathV1    = "cue/records/proof_document.cue"
	recordLabelV1   = "proof_document_precision"
	recordStableV1  = "proof-document:precision-proof"
	releasePathV1   = "cue/records/proof_document_release.cue"
	releaseLabelV1  = "proof_document_release"
	releaseStableV1 = "proof-document:release-readiness"
	schemaDefV1     = "ProofDocument"
	apiVersionV1    = "cuecodec.mishima-computing.github.io/v1"
	kindNameV1      = "ProofDocument"
	guardRulesetV1  = "structured-output-v1"

	canonicalCorpusDomainV1 = "cuecodec/kind-admission/canonical-corpus/v1"
	carrierCorpusDomainV1   = "cuecodec/kind-admission/carrier-corpus/v1"
)

var firstKindV1 = Kind{APIVersion: apiVersionV1, Kind: kindNameV1}

type recordDeclarationV1 struct {
	path     string
	label    string
	stableID string
}

// firstRecordCorpusV1 is the complete, byte-path-sorted record authority for
// the first kind. A record is not discoverable through the codec unless every
// declaration in this corpus completes the same admission pipeline.
var firstRecordCorpusV1 = [...]recordDeclarationV1{
	{path: recordPathV1, label: recordLabelV1, stableID: recordStableV1},
	{path: releasePathV1, label: releaseLabelV1, stableID: releaseStableV1},
}

type kindKey struct {
	apiVersion string
	kind       string
}

type kindEntry struct {
	kind        Kind
	schema      cue.Value
	records     map[string]Record
	schemaBytes []byte
	proof       kindAdmissionProofV1
}

// kindAdmissionProofV1 is recomputed from one immutable source snapshot. It is
// intentionally internal evidence, not a second public API surface.
type kindAdmissionProofV1 struct {
	sourceSHA256      [sha256.Size]byte
	closureSHA256     [sha256.Size]byte
	canonicalSHA256   [sha256.Size]byte
	carrierSHA256     [sha256.Size]byte
	schemaSHA256      [sha256.Size]byte
	guardInputSHA256  [sha256.Size]byte
	validatorSHA256   [sha256.Size]byte
	publishableSHA256 [sha256.Size]byte
	admitted          bool
}

type recordAdmissionV1 struct {
	declaration recordDeclarationV1
	record      Record
	canonical   []byte
	carrier     []byte
}

type corpusArtifactV1 struct {
	recordPath string
	bytes      []byte
}

// New freezes repository authority, proves the complete first-kind admission
// pipeline, and only then makes that kind visible through SupportedKinds.
func New(opts Options) (*Codec, error) {
	root, limits, err := normalizeOptions(opts)
	if err != nil {
		return nil, err
	}
	snapshot, captureErr := repository.CaptureSourceSnapshotV1(root)
	if captureErr != nil {
		return nil, adaptRepositoryError("new", Kind{}, captureErr)
	}
	earlyCandidates := firstRecordCorpusCandidates(snapshot)
	if declaredModule, ok := cueload.DeclaredModuleIdentityV1(snapshot); ok && declaredModule != modulePathV1 {
		earlyCandidates = append(earlyCandidates, codecError(CodeRepositoryBoundary, "new", Kind{}, "cue.mod/module.cue", "module", "", "module-identity"))
	}
	closure, closureErr := cueload.ResolveImportClosureV1(root, snapshot)
	if closureErr != nil {
		candidates := append([]*CodecError(nil), earlyCandidates...)
		candidates = append(candidates, adaptClosureError("new", Kind{}, closureErr))
		return nil, selectCodecError(candidates...)
	}
	if closure.Module != modulePathV1 {
		candidates := append([]*CodecError(nil), earlyCandidates...)
		candidates = append(candidates, codecError(CodeRepositoryBoundary, "new", Kind{}, "cue.mod/module.cue", "module", "", "module-identity"))
		return nil, selectCodecError(candidates...)
	}
	if selected := selectCodecError(earlyCandidates...); selected != nil && selected.Code != CodeIdentity {
		return nil, selected
	}

	c := &Codec{
		root: root, limits: limits, ctx: cuecontext.New(), snapshot: snapshot,
		closure: closure, entries: make(map[kindKey]*kindEntry),
	}
	entry, admissionErr := c.admitFirstKind()
	if admissionErr != nil {
		return nil, admissionErr
	}
	if verifyErr := c.verifySnapshot("new", entry.kind, ""); verifyErr != nil {
		return nil, verifyErr
	}
	c.entries[keyFor(entry.kind)] = entry
	c.kinds = []Kind{entry.kind}
	return c, nil
}

func normalizeOptions(opts Options) (string, Limits, error) {
	if opts.Limits.MaxDepth < 0 || opts.Limits.MaxBytes < 0 {
		return "", Limits{}, codecError(CodeInvalidArgument, "new", Kind{}, "", "", "", "limits-negative")
	}
	limits := opts.Limits
	if limits.MaxDepth == 0 {
		limits.MaxDepth = defaultMaxDepth
	}
	if limits.MaxBytes == 0 {
		limits.MaxBytes = defaultMaxBytes
	}
	root := opts.RepositoryRoot
	if root == "" {
		var err error
		root, err = os.Getwd()
		if err != nil {
			return "", Limits{}, codecError(CodeRepositoryBoundary, "new", Kind{}, "", "", "", "working-directory")
		}
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return "", Limits{}, codecError(CodeRepositoryBoundary, "new", Kind{}, "", "", "", "repository-root")
	}
	return filepath.Clean(abs), limits, nil
}

func (c *Codec) admitFirstKind() (*kindEntry, error) {
	corpusCandidates := firstRecordCorpusCandidates(c.snapshot)
	schemaFile, recordFiles, loadErr := c.loadSnapshotPackages()
	if loadErr != nil {
		candidates := append([]*CodecError(nil), corpusCandidates...)
		candidates = append(candidates, loadErr)
		return nil, selectCodecError(candidates...)
	}
	recordCandidates := append([]*CodecError(nil), corpusCandidates...)
	values := make(map[string]cue.Value, len(firstRecordCorpusV1))
	for _, declaration := range firstRecordCorpusV1 {
		recordFile, ok := recordFiles[declaration.path]
		if !ok {
			continue
		}
		iter, fieldsErr := recordFile.Fields(cue.Definitions(false), cue.Hidden(false), cue.Optional(false))
		visibleFields := 0
		if fieldsErr == nil {
			for iter.Next() {
				visibleFields++
			}
		}
		value := recordFile.LookupPath(cue.MakePath(cue.Str(declaration.label)))
		if fieldsErr != nil || !value.Exists() || value.Err() != nil {
			recordCandidates = append(recordCandidates, codecError(CodeSyntax, "new", firstKindV1, declaration.path, "", "", "record-shape"))
			continue
		}
		values[declaration.path] = value
		if visibleFields > 1 {
			recordCandidates = append(recordCandidates, codecError(CodeIdentity, "new", firstKindV1, declaration.path, "", "", "one-record-per-file"))
		}
		// Identity is a stage earlier than schema admission and does not require
		// the schema value. Checking the declared stable ID here both prevents a
		// record substitution and proves corpus-wide stable-ID distinctness.
		_, stableID, identityErr := validateEmbeddedIdentity("new", firstKindV1, firstKindV1, declaration.path, value)
		if identityErr != nil {
			recordCandidates = append(recordCandidates, identityErr)
		} else if stableID != declaration.stableID {
			recordCandidates = append(recordCandidates, codecError(CodeIdentity, "new", firstKindV1, declaration.path, "stable_id", "", "stable-id-mismatch"))
		}
	}
	if selected := selectCodecError(recordCandidates...); selected != nil {
		return nil, selected
	}

	schema := schemaFile.LookupPath(cue.MakePath(cue.Def(schemaDefV1)))
	if !schema.Exists() || schema.Err() != nil {
		return nil, codecError(CodeSchemaValidation, "new", firstKindV1, schemaPathV1, "#"+schemaDefV1, "", "schema-definition")
	}

	entry := &kindEntry{kind: firstKindV1, schema: schema, records: make(map[string]Record)}
	admissions := make([]recordAdmissionV1, 0, len(firstRecordCorpusV1))
	var validationCandidates []*CodecError
	for _, declaration := range firstRecordCorpusV1 {
		record, validationErr := c.validateRecord("new", entry, firstKindV1, declaration.path, values[declaration.path])
		if validationErr != nil {
			validationCandidates = append(validationCandidates, validationErr.(*CodecError))
			continue
		}
		admissions = append(admissions, recordAdmissionV1{declaration: declaration, record: record})
	}
	if selected := selectCodecError(validationCandidates...); selected != nil {
		return nil, selected
	}

	var artifactCandidates []*CodecError
	for i := range admissions {
		admission := &admissions[i]
		canonical, canonicalErr := c.encodeCanonical("new", admission.record)
		if canonicalErr != nil {
			artifactCandidates = append(artifactCandidates, canonicalErr.(*CodecError))
			continue
		}
		parsed := c.ctx.CompileBytes(canonical, cue.Filename("canonical.cue"))
		if parsed.Err() != nil {
			artifactCandidates = append(artifactCandidates, codecError(CodeNonCanonical, "new", firstKindV1, admission.declaration.path, "", "", "canonical-parse"))
			continue
		}
		roundTrip, roundTripErr := c.validateRecord("new", entry, firstKindV1, admission.declaration.path, parsed)
		if roundTripErr != nil {
			artifactCandidates = append(artifactCandidates, roundTripErr.(*CodecError))
			continue
		}
		reencoded, reencodeErr := c.encodeCanonical("new", roundTrip)
		if reencodeErr != nil {
			artifactCandidates = append(artifactCandidates, reencodeErr.(*CodecError))
			continue
		}
		if !bytesEqual(canonical, reencoded) || !admission.record.value.Equals(roundTrip.value) {
			artifactCandidates = append(artifactCandidates, codecError(CodeNonCanonical, "new", firstKindV1, admission.declaration.path, "", "", "canonical-round-trip"))
			continue
		}

		carrier, carrierErr := canonicalCarrierJSON(admission.record.value)
		if carrierErr != nil {
			artifactCandidates = append(artifactCandidates, codecError(CodeProjection, "new", firstKindV1, admission.declaration.path, "", "", "carrier-json"))
			continue
		}
		canonicalCarrier, canonicalCarrierErr := canonicalCarrierJSON(roundTrip.value)
		if canonicalCarrierErr != nil || !jsonEqual(carrier, canonicalCarrier) {
			artifactCandidates = append(artifactCandidates, codecError(CodeProjection, "new", firstKindV1, admission.declaration.path, "", "", "carrier-equality"))
			continue
		}
		admission.canonical = canonical
		admission.carrier = carrier
	}
	if selected := selectCodecError(artifactCandidates...); selected != nil {
		return nil, selected
	}

	schemaBytes, guardedBytes, schemaHash, schemaErr := c.generateAndGuardSchema(entry)
	if schemaErr != nil {
		return nil, schemaErr
	}
	var pairedCandidates []*CodecError
	for run := 0; run < 2; run++ {
		for _, admission := range admissions {
			if err := validatePairedCarrier(guardedBytes, admission.carrier); err != nil {
				pairedCandidates = append(pairedCandidates, codecError(CodePairedInstance, "new", firstKindV1, admission.declaration.path, "", "", "offline-json-schema"))
			}
			carrierValue := c.ctx.CompileBytes(admission.carrier, cue.Filename("carrier.json"))
			if carrierValue.Err() != nil || entry.schema.Unify(carrierValue).Validate(cue.Final(), cue.Concrete(true)) != nil {
				pairedCandidates = append(pairedCandidates, codecError(CodePairedInstance, "new", firstKindV1, admission.declaration.path, "", "", "cue-paired-instance"))
			}
		}
	}
	if selected := selectCodecError(pairedCandidates...); selected != nil {
		return nil, selected
	}

	canonicalArtifacts := make([]corpusArtifactV1, 0, len(admissions))
	carrierArtifacts := make([]corpusArtifactV1, 0, len(admissions))
	for _, admission := range admissions {
		canonicalArtifacts = append(canonicalArtifacts, corpusArtifactV1{recordPath: admission.declaration.path, bytes: admission.canonical})
		carrierArtifacts = append(carrierArtifacts, corpusArtifactV1{recordPath: admission.declaration.path, bytes: admission.carrier})
	}

	proof := kindAdmissionProofV1{
		sourceSHA256:      [sha256.Size]byte(c.snapshot.SHA256),
		closureSHA256:     [sha256.Size]byte(c.closure.SHA256),
		canonicalSHA256:   hashCorpusArtifactsV1(canonicalCorpusDomainV1, canonicalArtifacts),
		carrierSHA256:     hashCorpusArtifactsV1(carrierCorpusDomainV1, carrierArtifacts),
		schemaSHA256:      schemaHash,
		guardInputSHA256:  sha256.Sum256(schemaBytes),
		validatorSHA256:   sha256.Sum256(guardedBytes),
		publishableSHA256: sha256.Sum256(guardedBytes),
		admitted:          true,
	}
	if proof.schemaSHA256 != proof.guardInputSHA256 || proof.schemaSHA256 != proof.validatorSHA256 || proof.schemaSHA256 != proof.publishableSHA256 {
		return nil, codecError(CodeSchemaGuard, "new", firstKindV1, recordPathV1, "", "", "unchanged-schema-bytes")
	}
	entry.schemaBytes = append([]byte(nil), guardedBytes...)
	entry.proof = proof
	for _, admission := range admissions {
		entry.records[admission.declaration.path] = admission.record
	}
	return entry, nil
}

func firstRecordCorpusCandidates(snapshot repository.SourceSnapshotV1) []*CodecError {
	var recordFiles []string
	for _, file := range snapshot.Files {
		if strings.HasPrefix(file.Path, "cue/records/") && strings.HasSuffix(file.Path, ".cue") {
			recordFiles = append(recordFiles, file.Path)
		}
	}
	sort.Strings(recordFiles)
	declared := make(map[string]bool, len(firstRecordCorpusV1))
	for _, declaration := range firstRecordCorpusV1 {
		declared[declaration.path] = true
	}
	var candidates []*CodecError
	for _, declaration := range firstRecordCorpusV1 {
		if !containsString(recordFiles, declaration.path) {
			candidates = append(candidates, codecError(CodeRead, "new", firstKindV1, declaration.path, "", "", "record-unavailable"))
		}
	}
	for _, name := range recordFiles {
		if !declared[name] {
			candidates = append(candidates, codecError(CodeIdentity, "new", firstKindV1, name, "", "", "one-record-per-file"))
		}
	}
	return candidates
}

func containsString(values []string, want string) bool {
	i := sort.SearchStrings(values, want)
	return i < len(values) && values[i] == want
}

// loadSnapshotPackages gives the CUE loader an fs.FS containing exactly the
// frozen authoritative inventory. Host files, module caches, registries, and
// the network are therefore outside the loader's namespace. ImportClosureV1
// has already rejected every non-standard import outside this map.
func (c *Codec) loadSnapshotPackages() (cue.Value, map[string]cue.Value, *CodecError) {
	schema, schemaErr := cueload.BuildPackageV1(c.ctx, c.snapshot, "cue/schema", "schema")
	_, recordsErr := cueload.BuildPackageV1(c.ctx, c.snapshot, "cue/records", "records")
	records := make(map[string]cue.Value, len(firstRecordCorpusV1))
	candidates := []*CodecError{
		adaptOptionalClosureError("new", firstKindV1, schemaErr),
		adaptOptionalClosureError("new", firstKindV1, recordsErr),
	}
	for _, declaration := range firstRecordCorpusV1 {
		if _, present := c.snapshot.Content(declaration.path); !present {
			continue
		}
		record, recordErr := cueload.BuildPackageFileV1(c.ctx, c.snapshot, "cue/records", "records", declaration.path)
		if recordErr == nil {
			records[declaration.path] = record
		}
		candidates = append(candidates, adaptOptionalClosureError("new", firstKindV1, recordErr))
	}
	if selected := selectCodecError(candidates...); selected != nil {
		return cue.Value{}, nil, selected
	}
	return schema, records, nil
}

// hashCorpusArtifactsV1 commits to the complete record path and output bytes
// with an unambiguous, domain-separated, byte-path-sorted encoding.
func hashCorpusArtifactsV1(domain string, artifacts []corpusArtifactV1) [sha256.Size]byte {
	ordered := append([]corpusArtifactV1(nil), artifacts...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].recordPath < ordered[j].recordPath })
	h := sha256.New()
	var size [8]byte
	writeLengthPrefixed := func(value []byte) {
		binary.BigEndian.PutUint64(size[:], uint64(len(value)))
		_, _ = h.Write(size[:])
		_, _ = h.Write(value)
	}
	writeLengthPrefixed([]byte(domain))
	binary.BigEndian.PutUint64(size[:], uint64(len(ordered)))
	_, _ = h.Write(size[:])
	for _, artifact := range ordered {
		writeLengthPrefixed([]byte(artifact.recordPath))
		writeLengthPrefixed(artifact.bytes)
	}
	var digest [sha256.Size]byte
	copy(digest[:], h.Sum(nil))
	return digest
}

func (c *Codec) validateRecord(operation string, entry *kindEntry, requested Kind, sourcePath string, value cue.Value) (Record, error) {
	actual, stableID, identityErr := validateEmbeddedIdentity(operation, requested, entry.kind, sourcePath, value)
	if identityErr != nil {
		return Record{}, identityErr
	}
	unified := entry.schema.Unify(value)
	if err := unified.Validate(cue.Final()); err != nil {
		return Record{}, codecErrorFromCUE(CodeSchemaValidation, operation, requested, sourcePath, entry.schema.Path().String(), "schema-unification", err)
	}
	if err := unified.Validate(cue.Final(), cue.Concrete(true)); err != nil {
		return Record{}, codecErrorFromCUE(CodeIncomplete, operation, requested, sourcePath, entry.schema.Path().String(), "recursive-concreteness", err)
	}
	depth, err := cueValueDepth(unified)
	if err != nil {
		return Record{}, codecError(CodeIncomplete, operation, requested, sourcePath, "", "", "unsupported-shape")
	}
	if depth > c.limits.MaxDepth {
		return Record{}, codecError(CodeStructuralLimit, operation, requested, sourcePath, "", "", "max-depth")
	}
	return Record{owner: c, kind: actual, stableID: stableID, sourcePath: sourcePath, value: unified}, nil
}

func validateEmbeddedIdentity(operation string, requested, admitted Kind, sourcePath string, value cue.Value) (Kind, string, *CodecError) {
	actual, stableID, identityFailures := embeddedIdentity(value)
	identityCandidates := make([]*CodecError, 0, len(identityFailures)+2)
	for _, failure := range identityFailures {
		identityCandidates = append(identityCandidates, codecError(CodeIdentity, operation, requested, sourcePath, failure.cuePath, "", failure.rule))
	}
	if actual.APIVersion != "" && (actual.APIVersion != requested.APIVersion || actual.APIVersion != admitted.APIVersion) {
		identityCandidates = append(identityCandidates, codecError(CodeIdentity, operation, requested, sourcePath, "apiVersion", "", "kind-mismatch"))
	}
	if actual.Kind != "" && (actual.Kind != requested.Kind || actual.Kind != admitted.Kind) {
		identityCandidates = append(identityCandidates, codecError(CodeIdentity, operation, requested, sourcePath, "kind", "", "kind-mismatch"))
	}
	if selected := selectCodecError(identityCandidates...); selected != nil {
		return Kind{}, "", selected
	}
	return actual, stableID, nil
}

type identityFailure struct{ cuePath, rule string }

func embeddedIdentity(value cue.Value) (Kind, string, []*identityFailure) {
	get := func(name string) (string, *identityFailure) {
		field := value.LookupPath(cue.MakePath(cue.Str(name)))
		if !field.Exists() || field.Err() != nil {
			return "", &identityFailure{cuePath: name, rule: "identity-missing"}
		}
		s, err := field.String()
		if err != nil || s == "" {
			return "", &identityFailure{cuePath: name, rule: "identity-string"}
		}
		return s, nil
	}
	apiVersion, apiFailure := get("apiVersion")
	kind, kindFailure := get("kind")
	stableID, stableFailure := get("stable_id")
	failures := make([]*identityFailure, 0, 3)
	for _, failure := range []*identityFailure{apiFailure, kindFailure, stableFailure} {
		if failure != nil {
			failures = append(failures, failure)
		}
	}
	if stableFailure == nil && !validStableID(stableID) {
		failures = append(failures, &identityFailure{cuePath: "stable_id", rule: "stable-id-syntax"})
	}
	return Kind{APIVersion: apiVersion, Kind: kind}, stableID, failures
}

func validStableID(value string) bool {
	parts := strings.Split(value, ":")
	if len(parts) != 2 {
		return false
	}
	for _, part := range parts {
		if part == "" || part[0] < 'a' || part[0] > 'z' {
			return false
		}
		for _, r := range part[1:] {
			if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '-') {
				return false
			}
		}
		if part[len(part)-1] == '-' {
			return false
		}
	}
	return true
}

func cueValueDepth(value cue.Value) (int, error) {
	switch kind := value.IncompleteKind(); {
	case kind&cue.StructKind != 0:
		iter, err := value.Fields(cue.Definitions(false), cue.Hidden(false), cue.Optional(false))
		if err != nil {
			return 0, err
		}
		max := 1
		for iter.Next() {
			depth, err := cueValueDepth(iter.Value())
			if err != nil {
				return 0, err
			}
			if depth+1 > max {
				max = depth + 1
			}
		}
		return max, nil
	case kind&cue.ListKind != 0:
		iter, err := value.List()
		if err != nil {
			return 0, err
		}
		max := 1
		for iter.Next() {
			depth, err := cueValueDepth(iter.Value())
			if err != nil {
				return 0, err
			}
			if depth+1 > max {
				max = depth + 1
			}
		}
		return max, nil
	case kind&(cue.NullKind|cue.BoolKind|cue.IntKind|cue.FloatKind|cue.StringKind) != 0:
		return 1, nil
	default:
		return 0, fmt.Errorf("unsupported CUE kind %v", kind)
	}
}

func validContext(ctx context.Context, operation string, kind Kind, recordPath string) error {
	if ctx == nil || ctx.Err() != nil {
		return codecError(CodeInvalidArgument, operation, kind, recordPath, "", "", "context")
	}
	return nil
}

func (c *Codec) lookupKind(operation string, kind Kind) (*kindEntry, error) {
	if c == nil {
		return nil, codecError(CodeInvalidArgument, operation, kind, "", "", "", "nil-codec")
	}
	if !validKindIdentity(kind) {
		return nil, codecError(CodeInvalidArgument, operation, kind, "", "", "", "kind-identity")
	}
	c.mu.RLock()
	defer c.mu.RUnlock()
	entry := c.entries[keyFor(kind)]
	if entry == nil || !entry.proof.admitted {
		return nil, codecError(CodeUnsupportedKind, operation, kind, "", "", "", "kind-not-admitted")
	}
	return entry, nil
}

func validKindIdentity(kind Kind) bool {
	if kind.APIVersion == "" || kind.Kind == "" || !utf8.ValidString(kind.APIVersion) || !utf8.ValidString(kind.Kind) ||
		strings.ContainsAny(kind.APIVersion, "\\\x00\r\n\t ") || strings.HasPrefix(kind.APIVersion, "/") || strings.HasSuffix(kind.APIVersion, "/") {
		return false
	}
	for _, component := range strings.Split(kind.APIVersion, "/") {
		if component == "" || component == "." || component == ".." {
			return false
		}
	}
	for i, r := range kind.Kind {
		if !(r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || i > 0 && r >= '0' && r <= '9') {
			return false
		}
	}
	return true
}

func (c *Codec) validateRecordOwner(operation string, record Record) error {
	if c == nil || record.owner == nil || record.owner != c || !record.value.Exists() {
		return codecError(CodeInvalidArgument, operation, record.kind, record.sourcePath, "", "", "record-owner")
	}
	entry, err := c.lookupKind(operation, record.kind)
	if err != nil {
		return err
	}
	validated, err := c.validateRecord(operation, entry, record.kind, record.sourcePath, record.value)
	if err != nil {
		return err
	}
	if validated.stableID != record.stableID {
		return codecError(CodeIdentity, operation, record.kind, record.sourcePath, "stable_id", "", "stable-id-mismatch")
	}
	return nil
}

func validateRecordPath(recordPath, operation string, kind Kind) error {
	if recordPath == "" || !utf8.ValidString(recordPath) || strings.ContainsRune(recordPath, 0) || strings.Contains(recordPath, "\\") || strings.HasPrefix(recordPath, "/") || path.Clean(recordPath) != recordPath || !strings.HasPrefix(recordPath, "cue/records/") || !strings.HasSuffix(recordPath, ".cue") {
		return codecError(CodeRepositoryBoundary, operation, kind, recordPath, "", "", "record-path")
	}
	return nil
}

func (c *Codec) verifySnapshot(operation string, kind Kind, recordPath string) error {
	if c == nil {
		return codecError(CodeInvalidArgument, operation, kind, recordPath, "", "", "nil-codec")
	}
	if err := repository.VerifySourceSnapshotV1(c.root, c.snapshot); err != nil {
		adapted := adaptRepositoryError(operation, kind, err)
		adapted.RecordPath = recordPath
		if failure, ok := err.(*repository.Failure); ok && failure.Path != "" {
			adapted.RecordPath = failure.Path
		}
		adapted.RuleID = "snapshot-changed"
		return adapted
	}
	return nil
}

func keyFor(kind Kind) kindKey { return kindKey{apiVersion: kind.APIVersion, kind: kind.Kind} }

func codecError(code ErrorCode, operation string, kind Kind, recordPath, cuePath, jsonPointer, rule string) *CodecError {
	return &CodecError{Code: code, Operation: operation, Kind: kind, RecordPath: recordPath, CUEPath: cuePath, JSONPointer: jsonPointer, RuleID: rule}
}

func codecErrorFromCUE(code ErrorCode, operation string, kind Kind, recordPath, rootPath, rule string, err error) *CodecError {
	diagnostics := cueerrors.Errors(err)
	candidates := make([]*CodecError, 0, len(diagnostics))
	for _, diagnostic := range diagnostics {
		cuePath := strings.Join(cueerrors.Path(diagnostic), ".")
		if rootPath != "" {
			if cuePath == rootPath {
				cuePath = ""
			} else {
				cuePath = strings.TrimPrefix(cuePath, rootPath+".")
			}
		}
		candidates = append(candidates, codecError(code, operation, kind, recordPath, cuePath, "", rule))
	}
	if selected := selectCodecError(candidates...); selected != nil {
		return selected
	}
	return codecError(code, operation, kind, recordPath, "", "", rule)
}

func adaptRepositoryError(operation string, kind Kind, err error) *CodecError {
	failure, ok := err.(*repository.Failure)
	if !ok {
		return codecError(CodeRead, operation, kind, "", "", "", "repository")
	}
	code := CodeRead
	if failure.Class == repository.FailureBoundary {
		code = CodeRepositoryBoundary
	}
	return codecError(code, operation, kind, failure.Path, "", "", failure.Rule)
}

func adaptClosureError(operation string, kind Kind, err error) *CodecError {
	failure, ok := err.(*cueload.Failure)
	if !ok {
		return codecError(CodeRead, operation, kind, "", "", "", "import-closure")
	}
	code := CodeRead
	if failure.Class == cueload.FailureBoundary {
		code = CodeRepositoryBoundary
	} else if failure.Class == cueload.FailureSyntax {
		code = CodeSyntax
	}
	// RuleID is a stable classifier, never a data-bearing message. The import
	// literal remains available on the internal failure for diagnostics, but it
	// must not manufacture a new public rule identifier per input.
	return codecError(code, operation, kind, failure.Path, "", "", failure.Rule)
}

func adaptOptionalClosureError(operation string, kind Kind, err error) *CodecError {
	if err == nil {
		return nil
	}
	return adaptClosureError(operation, kind, err)
}

func sortKinds(kinds []Kind) {
	sort.Slice(kinds, func(i, j int) bool {
		if kinds[i].APIVersion != kinds[j].APIVersion {
			return kinds[i].APIVersion < kinds[j].APIVersion
		}
		return kinds[i].Kind < kinds[j].Kind
	})
}
