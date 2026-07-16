package cuecodec

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/mishima-computing/cuecodec/internal/jsonschemavalidate"
	"github.com/mishima-computing/cuecodec/internal/schemaguard"
	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

func TestFirstKindAdmissionProofV1(t *testing.T) {
	c := newTestCodec(t, ".", Limits{})
	kinds := c.SupportedKinds()
	if len(kinds) != 1 || kinds[0] != firstKindV1 {
		t.Fatalf("SupportedKinds = %#v, want %#v", kinds, []Kind{firstKindV1})
	}
	kinds[0] = Kind{}
	if c.SupportedKinds()[0] != firstKindV1 {
		t.Fatal("SupportedKinds exposed mutable registry storage")
	}

	record, err := c.LoadRecord(context.Background(), firstKindV1, recordPathV1)
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := c.EncodeCanonical(context.Background(), record)
	if err != nil {
		t.Fatal(err)
	}
	wantCanonical, err := os.ReadFile("testdata/canonical/proof_document.cue")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(canonical, wantCanonical) {
		t.Fatalf("canonical bytes differ\ngot:  %q\nwant: %q", canonical, wantCanonical)
	}
	if got, want := sha256.Sum256(canonical), [32]byte{0x69, 0x5c, 0x53, 0x4b, 0xae, 0x82, 0xdd, 0x6e, 0x46, 0x14, 0x01, 0xfb, 0xf8, 0x5c, 0x9d, 0x1b, 0xd2, 0xc0, 0xd4, 0xe5, 0x17, 0x3e, 0x5f, 0xaa, 0x08, 0xb4, 0x91, 0x6d, 0x08, 0xc7, 0x98, 0xf5}; got != want {
		t.Fatalf("canonical SHA-256 = %x, want %x", got, want)
	}
	digestGolden, err := os.ReadFile("testdata/canonical/proof_document.sha256")
	if err != nil {
		t.Fatal(err)
	}
	fields := strings.Fields(string(digestGolden))
	if len(fields) != 2 || fields[0] != "695c534bae82dd6e461401fbf85c9d1bd2c0d4e5173e5faa08b4916d08c798f5" || fields[1] != "proof_document.cue" {
		t.Fatalf("canonical digest golden = %q", digestGolden)
	}
	parsed, err := c.ParseCanonical(context.Background(), firstKindV1, canonical)
	if err != nil {
		t.Fatal(err)
	}
	reencoded, err := c.EncodeCanonical(context.Background(), parsed)
	if err != nil || !bytes.Equal(canonical, reencoded) || !record.Value().Equals(parsed.Value()) {
		t.Fatalf("canonical round trip failed: %v", err)
	}

	carrier, err := c.ExportJSON(context.Background(), record)
	if err != nil {
		t.Fatal(err)
	}
	carrierNode, err := strictjson.Parse(carrier)
	if err != nil {
		t.Fatal(err)
	}
	spec, _ := carrierNode.Lookup("spec")
	large, _ := spec.Lookup("large_integer")
	if got, want := large.NumberText, "900719925474099312345678901234567890"; got != want {
		t.Fatalf("large integer = %q, want %q", got, want)
	}
	steps, _ := spec.Lookup("ordered_steps")
	for i, want := range []string{"third", "first", "second"} {
		name, _ := steps.Elements[i].Lookup("name")
		if name.Text != want {
			t.Fatalf("ordered_steps[%d] = %q, want %q", i, name.Text, want)
		}
	}

	releaseRecord, err := c.LoadRecord(context.Background(), firstKindV1, releasePathV1)
	if err != nil {
		t.Fatal(err)
	}
	if releaseRecord.StableID() != releaseStableV1 || releaseRecord.SourcePath() != releasePathV1 {
		t.Fatalf("release record identity = %q/%q, want %q/%q", releaseRecord.StableID(), releaseRecord.SourcePath(), releaseStableV1, releasePathV1)
	}
	releaseCanonical, err := c.EncodeCanonical(context.Background(), releaseRecord)
	if err != nil {
		t.Fatal(err)
	}
	wantReleaseCanonical, err := os.ReadFile("testdata/canonical/proof_document_release.cue")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(releaseCanonical, wantReleaseCanonical) {
		t.Fatalf("release canonical bytes differ\ngot:  %q\nwant: %q", releaseCanonical, wantReleaseCanonical)
	}
	releaseDigest := sha256.Sum256(releaseCanonical)
	if got, want := hex.EncodeToString(releaseDigest[:]), "325bca458185156392cb251cd40393e050bbf91e734871836ad8ba85563598a2"; got != want {
		t.Fatalf("release canonical SHA-256 = %s, want %s", got, want)
	}
	releaseDigestGolden, err := os.ReadFile("testdata/canonical/proof_document_release.sha256")
	if err != nil {
		t.Fatal(err)
	}
	releaseFields := strings.Fields(string(releaseDigestGolden))
	if len(releaseFields) != 2 || releaseFields[0] != hex.EncodeToString(releaseDigest[:]) || releaseFields[1] != "proof_document_release.cue" {
		t.Fatalf("release canonical digest golden = %q", releaseDigestGolden)
	}
	releaseParsed, err := c.ParseCanonical(context.Background(), firstKindV1, releaseCanonical)
	if err != nil {
		t.Fatal(err)
	}
	releaseReencoded, err := c.EncodeCanonical(context.Background(), releaseParsed)
	if err != nil || !bytes.Equal(releaseCanonical, releaseReencoded) || !releaseRecord.Value().Equals(releaseParsed.Value()) {
		t.Fatalf("release canonical round trip failed: %v", err)
	}
	releaseCarrier, err := c.ExportJSON(context.Background(), releaseRecord)
	if err != nil {
		t.Fatal(err)
	}
	releaseCarrierNode, err := strictjson.Parse(releaseCarrier)
	if err != nil {
		t.Fatal(err)
	}
	releaseSpec, _ := releaseCarrierNode.Lookup("spec")
	releaseLarge, _ := releaseSpec.Lookup("large_integer")
	if got, want := releaseLarge.NumberText, "-900719925474099312345678901234567891"; got != want {
		t.Fatalf("release large integer = %q, want %q", got, want)
	}
	releaseSteps, _ := releaseSpec.Lookup("ordered_steps")
	for i, want := range []string{"verify", "publish", "observe"} {
		name, _ := releaseSteps.Elements[i].Lookup("name")
		if name.Text != want {
			t.Fatalf("release ordered_steps[%d] = %q, want %q", i, name.Text, want)
		}
	}

	entry := c.entries[keyFor(firstKindV1)]
	if entry == nil || !entry.proof.admitted {
		t.Fatal("kind was advertised without an admitted proof")
	}
	if len(entry.records) != len(firstRecordCorpusV1) {
		t.Fatalf("admitted record count = %d, want %d", len(entry.records), len(firstRecordCorpusV1))
	}
	admission, err := schemaguard.Guard(entry.schemaBytes)
	if err != nil {
		t.Fatal(err)
	}
	if err := jsonschemavalidate.ValidateAdmitted(admission, carrier); err != nil {
		t.Fatal(err)
	}
	if err := jsonschemavalidate.ValidateAdmitted(admission, releaseCarrier); err != nil {
		t.Fatal(err)
	}
	proof := entry.proof
	if proof.schemaSHA256 != admission.SHA256() || proof.guardInputSHA256 != proof.validatorSHA256 || proof.validatorSHA256 != proof.publishableSHA256 {
		t.Fatal("guard, validator, and publishable schema hashes differ")
	}
	if proof.sourceSHA256 == ([32]byte{}) || proof.closureSHA256 == ([32]byte{}) || proof.canonicalSHA256 == ([32]byte{}) || proof.carrierSHA256 == ([32]byte{}) {
		t.Fatal("admission proof contains an unbound digest")
	}
	wantCanonicalProof := hashCorpusArtifactsV1(canonicalCorpusDomainV1, []corpusArtifactV1{
		{recordPath: recordPathV1, bytes: canonical},
		{recordPath: releasePathV1, bytes: releaseCanonical},
	})
	wantCarrierProof := hashCorpusArtifactsV1(carrierCorpusDomainV1, []corpusArtifactV1{
		{recordPath: recordPathV1, bytes: carrier},
		{recordPath: releasePathV1, bytes: releaseCarrier},
	})
	if proof.canonicalSHA256 != wantCanonicalProof || proof.carrierSHA256 != wantCarrierProof {
		t.Fatal("admission proof does not bind the complete canonical/carrier corpus")
	}
	if proof.canonicalSHA256 == hashCorpusArtifactsV1(canonicalCorpusDomainV1, []corpusArtifactV1{{recordPath: recordPathV1, bytes: canonical}}) ||
		proof.carrierSHA256 == hashCorpusArtifactsV1(carrierCorpusDomainV1, []corpusArtifactV1{{recordPath: recordPathV1, bytes: carrier}}) {
		t.Fatal("admission proof is insensitive to omission of the second record")
	}
	changedReleaseCanonical := bytes.Clone(releaseCanonical)
	changedReleaseCanonical[len(changedReleaseCanonical)-2] ^= 1
	if proof.canonicalSHA256 == hashCorpusArtifactsV1(canonicalCorpusDomainV1, []corpusArtifactV1{
		{recordPath: recordPathV1, bytes: canonical},
		{recordPath: releasePathV1, bytes: changedReleaseCanonical},
	}) {
		t.Fatal("admission proof is insensitive to changed second-record canonical bytes")
	}

	secondCodec := newTestCodec(t, ".", Limits{})
	secondEntry := secondCodec.entries[keyFor(firstKindV1)]
	if secondEntry == nil || secondEntry.proof != proof || !bytes.Equal(secondEntry.schemaBytes, entry.schemaBytes) {
		t.Fatal("independent admission recomputation changed proof or schema bytes")
	}
}

func TestPublicContractV1HasNoAccidentalExports(t *testing.T) {
	packages, err := parser.ParseDir(token.NewFileSet(), ".", func(info os.FileInfo) bool {
		return filepath.Ext(info.Name()) == ".go" && !stringsHasSuffix(info.Name(), "_test.go")
	}, 0)
	if err != nil {
		t.Fatal(err)
	}
	pkg := packages["cuecodec"]
	if pkg == nil {
		t.Fatal("root cuecodec package not found")
	}
	var got []string
	for _, file := range pkg.Files {
		for _, decl := range file.Decls {
			switch decl := decl.(type) {
			case *ast.FuncDecl:
				if decl.Recv == nil && ast.IsExported(decl.Name.Name) {
					got = append(got, "func "+decl.Name.Name)
				}
			case *ast.GenDecl:
				for _, spec := range decl.Specs {
					switch spec := spec.(type) {
					case *ast.TypeSpec:
						if ast.IsExported(spec.Name.Name) {
							got = append(got, "type "+spec.Name.Name)
						}
					case *ast.ValueSpec:
						for _, name := range spec.Names {
							if ast.IsExported(name.Name) {
								got = append(got, "const "+name.Name)
							}
						}
					}
				}
			}
		}
	}
	sort.Strings(got)
	want := []string{
		"const CodeIdentity", "const CodeIncomplete", "const CodeInputLimit",
		"const CodeInvalidArgument", "const CodeNonCanonical", "const CodePairedInstance",
		"const CodeProjection", "const CodeRead", "const CodeRepositoryBoundary",
		"const CodeSchemaGeneration", "const CodeSchemaGuard", "const CodeSchemaValidation",
		"const CodeStructuralLimit", "const CodeSyntax", "const CodeUnsupportedKind",
		"func New", "type Codec", "type CodecError", "type ErrorCode", "type Kind",
		"type Limits", "type Options", "type Record",
	}
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("exported declarations = %#v, want %#v", got, want)
	}
	assertMethodNames(t, reflect.TypeOf((*Codec)(nil)), []string{"EncodeCanonical", "EncodeFlatReviewV1", "ExportJSON", "LoadRecord", "ParseCanonical", "SupportedKinds"})
	assertMethodNames(t, reflect.TypeOf(Record{}), []string{"Kind", "SourcePath", "StableID", "Value"})
	assertMethodNames(t, reflect.TypeOf((*CodecError)(nil)), []string{"Error"})
}

func assertMethodNames(t *testing.T, typ reflect.Type, want []string) {
	t.Helper()
	got := make([]string, typ.NumMethod())
	for i := range got {
		got[i] = typ.Method(i).Name
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%v exported methods = %#v, want %#v", typ, got, want)
	}
}

func stringsHasSuffix(value, suffix string) bool {
	return len(value) >= len(suffix) && value[len(value)-len(suffix):] == suffix
}

func TestCanonicalSourceOrderEquivalenceAndListOrder(t *testing.T) {
	c := newTestCodec(t, ".", Limits{})
	entry := c.entries[keyFor(firstKindV1)]
	reordered := c.ctx.CompileString(`{
spec: {
  ordered_steps: [{weight: 3, name: "third"}, {weight: 1, name: "first"}, {weight: 2, name: "second"}]
  nested: {rank_sequence: [3, 1, 2], owner: {active: true, name: "codec-admission"}}
  exponent: 1.234567890123456789e+42
  exact_decimal: 0.1000000000000000000000000001
  large_integer: 900719925474099312345678901234567890
  enabled: true
}
metadata: {title: "Precision-preserving admission proof", labels: ["canonical", "offline", "v1"]}
stable_id: "proof-document:precision-proof"
kind: "ProofDocument"
apiVersion: "cuecodec.mishima-computing.github.io/v1"
}`)
	if reordered.Err() != nil {
		t.Fatal(reordered.Err())
	}
	record, err := c.validateRecord("encode_canonical", entry, firstKindV1, "", reordered)
	if err != nil {
		t.Fatal(err)
	}
	got, err := c.EncodeCanonical(context.Background(), record)
	if err != nil {
		t.Fatal(err)
	}
	want, _ := os.ReadFile("testdata/canonical/proof_document.cue")
	if !bytes.Equal(got, want) {
		t.Fatalf("source-order-equivalent value changed canonical bytes\ngot: %q\nwant: %q", got, want)
	}
}

func TestPublicFailurePrecedenceAndNoPartialResult(t *testing.T) {
	if c, err := New(Options{RepositoryRoot: ".", Limits: Limits{MaxDepth: -1}}); c != nil || codecErrorCode(err) != CodeInvalidArgument {
		t.Fatalf("invalid options = (%#v, %v), want nil/INVALID_ARGUMENT", c, err)
	}
	if c, err := New(Options{RepositoryRoot: ".", Limits: Limits{MaxDepth: 1}}); c != nil || codecErrorCode(err) != CodeStructuralLimit {
		t.Fatalf("structural limit = (%#v, %v), want nil/STRUCTURAL_LIMIT", c, err)
	}
	c := newTestCodec(t, ".", Limits{})

	unknown := Kind{APIVersion: firstKindV1.APIVersion, Kind: "Unknown"}
	if record, err := c.LoadRecord(context.Background(), unknown, "../escape.cue"); !isZeroRecord(record) || codecErrorCode(err) != CodeUnsupportedKind {
		t.Fatalf("kind/path compound = (%#v, %v), want zero/UNSUPPORTED_KIND", record, err)
	}
	if record, err := c.LoadRecord(context.Background(), firstKindV1, "../escape.cue"); !isZeroRecord(record) || codecErrorCode(err) != CodeRepositoryBoundary {
		t.Fatalf("escaping path = (%#v, %v), want zero/REPOSITORY_BOUNDARY", record, err)
	}
	if record, err := c.LoadRecord(context.Background(), firstKindV1, "cue/records/missing.cue"); !isZeroRecord(record) || codecErrorCode(err) != CodeRead {
		t.Fatalf("missing record = (%#v, %v), want zero/READ", record, err)
	}

	originalLimits := c.limits
	c.limits.MaxBytes = 32
	if record, err := c.ParseCanonical(context.Background(), firstKindV1, bytes.Repeat([]byte{'{'}, 33)); !isZeroRecord(record) || codecErrorCode(err) != CodeInputLimit {
		t.Fatalf("limit/syntax compound = (%#v, %v), want zero/INPUT_LIMIT", record, err)
	}
	c.limits = originalLimits
	if record, err := c.ParseCanonical(context.Background(), firstKindV1, []byte(`{`)); !isZeroRecord(record) || codecErrorCode(err) != CodeSyntax {
		t.Fatalf("syntax = (%#v, %v), want zero/SYNTAX", record, err)
	}

	canonical, _ := os.ReadFile("testdata/canonical/proof_document.cue")
	noncanonical := append([]byte(" \n"), canonical...)
	if record, err := c.ParseCanonical(context.Background(), firstKindV1, noncanonical); !isZeroRecord(record) || codecErrorCode(err) != CodeNonCanonical {
		t.Fatalf("noncanonical = (%#v, %v), want zero/NON_CANONICAL", record, err)
	}
	if result, err := c.EncodeCanonical(context.Background(), Record{}); result != nil || codecErrorCode(err) != CodeInvalidArgument {
		t.Fatalf("zero record encode = (%q, %v), want nil/INVALID_ARGUMENT", result, err)
	}
	if result, err := c.ExportJSON(context.Background(), Record{}); result != nil || codecErrorCode(err) != CodeInvalidArgument {
		t.Fatalf("zero record export = (%q, %v), want nil/INVALID_ARGUMENT", result, err)
	}
}

func TestNewClassifiesReachablePipelineFaults(t *testing.T) {
	tests := []struct {
		name        string
		file        string
		old         string
		replacement string
		code        ErrorCode
		cuePath     string
		jsonPointer string
		ruleID      string
	}{
		{
			name: "api version identity", file: recordPathV1,
			old: `apiVersion: "cuecodec.mishima-computing.github.io/v1"`, replacement: `apiVersion: "cuecodec.mishima-computing.github.io/v2"`,
			code: CodeIdentity, cuePath: "apiVersion", ruleID: "kind-mismatch",
		},
		{
			name: "kind identity", file: recordPathV1,
			old: `kind:       "ProofDocument"`, replacement: `kind:       "OtherDocument"`,
			code: CodeIdentity, cuePath: "kind", ruleID: "kind-mismatch",
		},
		{
			name: "missing identity", file: recordPathV1,
			old: `stable_id:  "proof-document:precision-proof"`, replacement: `other_id:   "proof-document:precision-proof"`,
			code: CodeIdentity, cuePath: "stable_id", ruleID: "identity-missing",
		},
		{
			name: "schema validation", file: recordPathV1,
			old: `stable_id:  "proof-document:precision-proof"`, replacement: "stable_id:  \"proof-document:precision-proof\"\n\textra:      true",
			code: CodeSchemaValidation, cuePath: "extra", ruleID: "schema-unification",
		},
		{
			name: "recursive incompleteness", file: recordPathV1,
			old: `title:  "Precision-preserving admission proof"`, replacement: `title:  string`,
			code: CodeIncomplete, cuePath: "metadata.title", ruleID: "recursive-concreteness",
		},
		{
			name: "schema guard", file: schemaPathV1,
			old: `weight!: int`, replacement: `weight!: int & >=0`,
			code: CodeSchemaGuard, cuePath: "#ProofDocument",
			jsonPointer: "/properties/spec/properties/ordered_steps/items/properties/weight/allOf",
			ruleID:      "unsupported_keyword",
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := copyAuthority(t)
			mutateAuthority(t, root, tc.file, tc.old, tc.replacement)
			for run := 0; run < 2; run++ {
				codec, err := New(Options{RepositoryRoot: root})
				if codec != nil || codecErrorCode(err) != tc.code {
					t.Fatalf("run %d New = (%#v, %v), want nil/%s", run, codec, err, tc.code)
				}
				var codecErr *CodecError
				if !errors.As(err, &codecErr) {
					t.Fatalf("run %d error = %T %v", run, err, err)
				}
				wantPath := recordPathV1
				if tc.code == CodeSchemaGuard {
					wantPath = schemaPathV1
				}
				want := CodecError{
					Code: tc.code, Operation: "new", Kind: firstKindV1,
					RecordPath: wantPath, CUEPath: tc.cuePath,
					JSONPointer: tc.jsonPointer, RuleID: tc.ruleID,
				}
				if *codecErr != want {
					t.Fatalf("run %d CodecError = %#v, want %#v", run, *codecErr, want)
				}
			}
		})
	}

	root := copyAuthority(t)
	mutateAuthority(t, root, recordPathV1, "package records", "package")
	if codec, err := New(Options{RepositoryRoot: root}); codec != nil || codecErrorCode(err) != CodeSyntax {
		t.Fatalf("syntax New = (%#v, %v), want nil/SYNTAX", codec, err)
	}
}

func TestNewIdentityPrecedesSchemaValidation(t *testing.T) {
	root := copyAuthority(t)
	mutateAuthority(t, root, recordPathV1,
		`apiVersion: "cuecodec.mishima-computing.github.io/v1"`,
		`apiVersion: "cuecodec.mishima-computing.github.io/v2"`)
	mutateAuthority(t, root, recordPathV1,
		`stable_id:  "proof-document:precision-proof"`,
		"stable_id:  \"proof-document:precision-proof\"\n\textra:      true")
	want := CodecError{
		Code: CodeIdentity, Operation: "new", Kind: firstKindV1,
		RecordPath: recordPathV1, CUEPath: "apiVersion", RuleID: "kind-mismatch",
	}
	for run := 0; run < 2; run++ {
		codec, err := New(Options{RepositoryRoot: root})
		if codec != nil {
			t.Fatalf("run %d returned a partial codec", run)
		}
		var codecErr *CodecError
		if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
			t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, want)
		}
	}
}

func TestFirstRecordCorpusIsBoundToDeclaredSourcePath(t *testing.T) {
	root := copyAuthority(t)
	content, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(recordPathV1)))
	if err != nil {
		t.Fatal(err)
	}
	moved := "cue/records/moved.cue"
	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(moved)), content, 0o644); err != nil {
		t.Fatal(err)
	}
	want := CodecError{
		Code: CodeIdentity, Operation: "new", Kind: firstKindV1,
		RecordPath: moved, RuleID: "one-record-per-file",
	}
	for run := 0; run < 2; run++ {
		codec, err := New(Options{RepositoryRoot: root})
		if codec != nil {
			t.Fatalf("run %d returned a partial codec", run)
		}
		var codecErr *CodecError
		if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
			t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, want)
		}
	}
}

func TestFirstRecordCorpusCompoundPrecedence(t *testing.T) {
	tests := []struct {
		name         string
		extraPath    string
		extraContent string
		mutation     func(*testing.T, string)
		want         CodecError
	}{
		{
			name:         "record shape precedes extra corpus identity",
			extraPath:    "cue/records/zzz.cue",
			extraContent: "package records\n\nz: {apiVersion: \"cuecodec.mishima-computing.github.io/v1\", kind: \"ProofDocument\", stable_id: \"proof-document:extra\"}\n",
			mutation: func(t *testing.T, root string) {
				mutateAuthority(t, root, recordPathV1, "proof_document_precision: {", "#proof_document_precision: {")
			},
			want: CodecError{
				Code: CodeSyntax, Operation: "new", Kind: firstKindV1,
				RecordPath: recordPathV1, RuleID: "record-shape",
			},
		},
		{
			name:      "declared identity wins before later extra path",
			extraPath: "cue/records/zzz.cue",
			mutation: func(t *testing.T, root string) {
				mutateAuthority(t, root, recordPathV1, `apiVersion: "cuecodec.mishima-computing.github.io/v1"`, `apiVersion: "cuecodec.mishima-computing.github.io/v2"`)
			},
			want: CodecError{
				Code: CodeIdentity, Operation: "new", Kind: firstKindV1,
				RecordPath: recordPathV1, CUEPath: "apiVersion", RuleID: "kind-mismatch",
			},
		},
		{
			name:      "earlier extra path wins before declared identity",
			extraPath: "cue/records/aaa.cue",
			mutation: func(t *testing.T, root string) {
				mutateAuthority(t, root, recordPathV1, `apiVersion: "cuecodec.mishima-computing.github.io/v1"`, `apiVersion: "cuecodec.mishima-computing.github.io/v2"`)
			},
			want: CodecError{
				Code: CodeIdentity, Operation: "new", Kind: firstKindV1,
				RecordPath: "cue/records/aaa.cue", RuleID: "one-record-per-file",
			},
		},
		{
			name:      "declared file cardinality wins before later extra path",
			extraPath: "cue/records/zzz.cue",
			mutation: func(t *testing.T, root string) {
				mutateAuthority(t, root, recordPathV1,
					"#proof_document_schema: schema.#ProofDocument",
					"#proof_document_schema: schema.#ProofDocument\n\nsecond_record: {apiVersion: \"cuecodec.mishima-computing.github.io/v1\", kind: \"ProofDocument\", stable_id: \"proof-document:second\"}")
			},
			want: CodecError{
				Code: CodeIdentity, Operation: "new", Kind: firstKindV1,
				RecordPath: recordPathV1, RuleID: "one-record-per-file",
			},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := copyAuthority(t)
			tc.mutation(t, root)
			content := tc.extraContent
			if content == "" {
				content = "package records\n\n#extra: true\n"
			}
			writeAuthorityFile(t, root, tc.extraPath, content)
			for run := 0; run < 2; run++ {
				codec, err := New(Options{RepositoryRoot: root})
				if codec != nil {
					t.Fatalf("run %d returned a partial codec", run)
				}
				var codecErr *CodecError
				if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != tc.want {
					t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, tc.want)
				}
			}
		})
	}
}

func TestMissingDeclaredRecordPrecedesPackageLoad(t *testing.T) {
	for _, recordPath := range []string{recordPathV1, releasePathV1} {
		t.Run(filepath.Base(recordPath), func(t *testing.T) {
			root := copyAuthority(t)
			if err := os.Remove(filepath.Join(root, filepath.FromSlash(recordPath))); err != nil {
				t.Fatal(err)
			}
			want := CodecError{
				Code: CodeRead, Operation: "new", Kind: firstKindV1,
				RecordPath: recordPath, RuleID: "record-unavailable",
			}
			for run := 0; run < 2; run++ {
				codec, err := New(Options{RepositoryRoot: root})
				if codec != nil {
					t.Fatalf("run %d returned a partial codec", run)
				}
				var codecErr *CodecError
				if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
					t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, want)
				}
			}
		})
	}
}

func TestMissingDeclaredRecordPrecedesMalformedExtra(t *testing.T) {
	root := copyAuthority(t)
	if err := os.Remove(filepath.Join(root, filepath.FromSlash(recordPathV1))); err != nil {
		t.Fatal(err)
	}
	writeAuthorityFile(t, root, "cue/records/zzz.cue", "package records\n\nbroken: [\n")
	want := CodecError{
		Code: CodeRead, Operation: "new", Kind: firstKindV1,
		RecordPath: recordPathV1, RuleID: "record-unavailable",
	}
	for run := 0; run < 2; run++ {
		codec, err := New(Options{RepositoryRoot: root})
		if codec != nil {
			t.Fatalf("run %d returned a partial codec", run)
		}
		var codecErr *CodecError
		if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
			t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, want)
		}
	}
}

func TestCompleteRecordCorpusRequiresDeclaredDistinctStableIDs(t *testing.T) {
	root := copyAuthority(t)
	mutateAuthority(t, root, releasePathV1,
		`stable_id:  "proof-document:release-readiness"`,
		`stable_id:  "proof-document:precision-proof"`)
	want := CodecError{
		Code: CodeIdentity, Operation: "new", Kind: firstKindV1,
		RecordPath: releasePathV1, CUEPath: "stable_id", RuleID: "stable-id-mismatch",
	}
	for run := 0; run < 2; run++ {
		codec, err := New(Options{RepositoryRoot: root})
		if codec != nil {
			t.Fatalf("run %d returned a partial codec", run)
		}
		var codecErr *CodecError
		if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
			t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, want)
		}
	}
}

func TestCompleteRecordCorpusCrossRecordPrecedence(t *testing.T) {
	root := copyAuthority(t)
	mutateAuthority(t, root, recordPathV1,
		`title:  "Precision-preserving admission proof"`,
		`title:  string`)
	mutateAuthority(t, root, releasePathV1,
		`stable_id:  "proof-document:release-readiness"`,
		"stable_id:  \"proof-document:release-readiness\"\n\textra:      true")
	want := CodecError{
		Code: CodeSchemaValidation, Operation: "new", Kind: firstKindV1,
		RecordPath: releasePathV1, CUEPath: "extra", RuleID: "schema-unification",
	}
	for run := 0; run < 2; run++ {
		codec, err := New(Options{RepositoryRoot: root})
		if codec != nil {
			t.Fatalf("run %d returned a partial codec", run)
		}
		var codecErr *CodecError
		if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
			t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, want)
		}
	}
}

func TestWrongModuleIdentityPrecedesClosureFaults(t *testing.T) {
	for _, moduleFile := range []string{
		"module: \"example.test/wrong@v0\"\nlanguage: {version: \"v0.17.0\"}\n",
		"module: \"example.test/wrong@v0\"\nlanguage: 7\n",
		"language: 7\nmodule: \"example.test/wrong@v0\"\n",
		"module: \"example.test/wrong@v0\"\nlanguage: {version: \"v0.17.0\"}\n[\n",
	} {
		root := copyAuthority(t)
		writeAuthorityFile(t, root, "cue.mod/module.cue", moduleFile)
		want := CodecError{
			Code: CodeRepositoryBoundary, Operation: "new",
			RecordPath: "cue.mod/module.cue", CUEPath: "module", RuleID: "module-identity",
		}
		for run := 0; run < 2; run++ {
			codec, err := New(Options{RepositoryRoot: root})
			if codec != nil {
				t.Fatalf("metadata %q run %d returned a partial codec", moduleFile, run)
			}
			var codecErr *CodecError
			if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
				t.Fatalf("metadata %q run %d CodecError = %#v (%v), want %#v", moduleFile, run, codecErr, err, want)
			}
		}
	}
}

func TestPackageLoadErrorsUsePublicTuple(t *testing.T) {
	root := copyAuthority(t)
	mutateAuthority(t, root, schemaPathV1, "package schema", "package schema\n\n#schema_fault: 1 & 2")
	mutateAuthority(t, root, recordPathV1,
		"#proof_document_schema: schema.#ProofDocument",
		"#proof_document_schema: schema.#ProofDocument\n\n#record_fault: 1 & 2")
	want := CodecError{
		Code: CodeSyntax, Operation: "new", Kind: firstKindV1,
		RecordPath: "cue/records", RuleID: "cue-build",
	}
	for run := 0; run < 2; run++ {
		codec, err := New(Options{RepositoryRoot: root})
		if codec != nil {
			t.Fatalf("run %d returned a partial codec", run)
		}
		var codecErr *CodecError
		if !errors.As(err, &codecErr) || codecErr == nil || *codecErr != want {
			t.Fatalf("run %d CodecError = %#v (%v), want %#v", run, codecErr, err, want)
		}
	}
}

func TestChangedSourceIsRejectedBeforeSuccess(t *testing.T) {
	root := copyAuthority(t)
	c := newTestCodec(t, root, Limits{})
	record, err := c.LoadRecord(context.Background(), firstKindV1, recordPathV1)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(root, filepath.FromSlash(recordPathV1))
	source, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, append(source, []byte("\n// changed\n")...), 0o644); err != nil {
		t.Fatal(err)
	}
	if kinds := c.SupportedKinds(); kinds != nil {
		t.Fatalf("drifted codec still advertises kinds: %#v", kinds)
	}
	// Drift is the final public stage. Earlier faults must retain their exact
	// classification even though the same call also cannot publish a result
	// from this stale codec.
	if result, err := c.LoadRecord(context.Background(), firstKindV1, "cue/records/missing.cue"); !isZeroRecord(result) || codecErrorCode(err) != CodeRead {
		t.Fatalf("drift/read compound = (%#v, %v), want zero/READ", result, err)
	}
	if result, err := c.ParseCanonical(context.Background(), firstKindV1, []byte(`{`)); !isZeroRecord(result) || codecErrorCode(err) != CodeSyntax {
		t.Fatalf("drift/syntax compound = (%#v, %v), want zero/SYNTAX", result, err)
	}
	if result, err := c.EncodeCanonical(context.Background(), record); result != nil || codecErrorCode(err) != CodeRead {
		t.Fatalf("changed source encode = (%q, %v), want nil/READ", result, err)
	}
	originalLimit := c.limits.MaxBytes
	c.limits.MaxBytes = 1
	if result, err := c.ExportJSON(context.Background(), record); result != nil || codecErrorCode(err) != CodeStructuralLimit {
		t.Fatalf("drift/output-limit compound = (%q, %v), want nil/STRUCTURAL_LIMIT", result, err)
	}
	c.limits.MaxBytes = originalLimit
}

func newTestCodec(t *testing.T, root string, limits Limits) *Codec {
	t.Helper()
	c, err := New(Options{RepositoryRoot: root, Limits: limits})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return c
}

func codecErrorCode(err error) ErrorCode {
	var codecErr *CodecError
	if !errors.As(err, &codecErr) {
		return ""
	}
	return codecErr.Code
}

func isZeroRecord(record Record) bool {
	return record.owner == nil && record.kind == (Kind{}) && record.stableID == "" && record.sourcePath == ""
}

func copyAuthority(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for _, name := range []string{"cue.mod/module.cue", schemaPathV1, recordPathV1, releasePathV1} {
		data, err := os.ReadFile(filepath.FromSlash(name))
		if err != nil {
			t.Fatal(err)
		}
		target := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func mutateAuthority(t *testing.T, root, name, old, replacement string) {
	t.Helper()
	file := filepath.Join(root, filepath.FromSlash(name))
	content, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(content), old) != 1 {
		t.Fatalf("fixture %s contains %d copies of mutation target %q", name, strings.Count(string(content), old), old)
	}
	content = []byte(strings.Replace(string(content), old, replacement, 1))
	if err := os.WriteFile(file, content, 0o644); err != nil {
		t.Fatal(err)
	}
}

func writeAuthorityFile(t *testing.T, root, name, content string) {
	t.Helper()
	file := filepath.Join(root, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(file), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
