package cuecodec_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"cuelang.org/go/cue"
	"github.com/mishima-computing/cuecodec"
)

var (
	_ func(cuecodec.Options) (*cuecodec.Codec, error) = cuecodec.New

	_ func(*cuecodec.Codec) []cuecodec.Kind                                                  = (*cuecodec.Codec).SupportedKinds
	_ func(*cuecodec.Codec, context.Context, cuecodec.Kind, string) (cuecodec.Record, error) = (*cuecodec.Codec).LoadRecord
	_ func(*cuecodec.Codec, context.Context, cuecodec.Kind, []byte) (cuecodec.Record, error) = (*cuecodec.Codec).ParseCanonical
	_ func(*cuecodec.Codec, context.Context, cuecodec.Record) ([]byte, error)                = (*cuecodec.Codec).EncodeCanonical
	_ func(*cuecodec.Codec, context.Context, cuecodec.Kind) ([]byte, error)                  = (*cuecodec.Codec).EncodeFlatReviewV1
	_ func(*cuecodec.Codec, context.Context, cuecodec.Record) ([]byte, error)                = (*cuecodec.Codec).ExportJSON

	_ func(cuecodec.Record) cuecodec.Kind = cuecodec.Record.Kind
	_ func(cuecodec.Record) string        = cuecodec.Record.StableID
	_ func(cuecodec.Record) string        = cuecodec.Record.SourcePath
	_ func(cuecodec.Record) cue.Value     = cuecodec.Record.Value

	_ error = (*cuecodec.CodecError)(nil)
)

func TestPublicContractV1(t *testing.T) {
	repositoryRoot, err := filepath.Abs(".")
	if err != nil {
		t.Fatal(err)
	}

	codec, err := cuecodec.New(cuecodec.Options{
		RepositoryRoot: repositoryRoot,
		Limits: cuecodec.Limits{
			MaxDepth: 64,
			MaxBytes: 1 << 20,
		},
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	wantKind := cuecodec.Kind{
		APIVersion: "cuecodec.mishima-computing.github.io/v1",
		Kind:       "ProofDocument",
	}
	kinds := codec.SupportedKinds()
	if len(kinds) != 1 || kinds[0] != wantKind {
		t.Fatalf("SupportedKinds() = %#v, want [%#v]", kinds, wantKind)
	}

	records := []struct {
		path     string
		stableID string
	}{
		{path: "cue/records/proof_document.cue", stableID: "proof-document:precision-proof"},
		{path: "cue/records/proof_document_release.cue", stableID: "proof-document:release-readiness"},
	}
	for _, want := range records {
		t.Run(want.stableID, func(t *testing.T) {
			record, err := codec.LoadRecord(context.Background(), wantKind, want.path)
			if err != nil {
				t.Fatalf("LoadRecord: %v", err)
			}
			if record.Kind() != wantKind {
				t.Fatalf("record Kind() = %#v, want %#v", record.Kind(), wantKind)
			}
			if got := record.StableID(); got != want.stableID {
				t.Fatalf("record StableID() = %q, want %q", got, want.stableID)
			}
			if got := filepath.ToSlash(record.SourcePath()); got != want.path {
				t.Fatalf("record SourcePath() = %q, want %q", got, want.path)
			}
			if !record.Value().Exists() {
				t.Fatal("record Value() does not exist")
			}

			canonical, err := codec.EncodeCanonical(context.Background(), record)
			if err != nil {
				t.Fatalf("EncodeCanonical: %v", err)
			}
			canonicalAgain, err := codec.EncodeCanonical(context.Background(), record)
			if err != nil {
				t.Fatalf("EncodeCanonical repeat: %v", err)
			}
			if !bytes.Equal(canonical, canonicalAgain) {
				t.Fatal("EncodeCanonical changed bytes across repeated calls")
			}

			parsed, err := codec.ParseCanonical(context.Background(), wantKind, canonical)
			if err != nil {
				t.Fatalf("ParseCanonical: %v", err)
			}
			if parsed.Kind() != wantKind || parsed.StableID() != record.StableID() {
				t.Fatalf("parsed identity = %#v/%q, want %#v/%q", parsed.Kind(), parsed.StableID(), wantKind, record.StableID())
			}
			if parsed.SourcePath() != "" {
				t.Fatalf("parsed SourcePath() = %q, want empty", parsed.SourcePath())
			}
			if !record.Value().Equals(parsed.Value()) {
				t.Fatal("parsed value differs from authoritative record")
			}
			reencoded, err := codec.EncodeCanonical(context.Background(), parsed)
			if err != nil {
				t.Fatalf("EncodeCanonical parsed: %v", err)
			}
			if !bytes.Equal(canonical, reencoded) {
				t.Fatal("parsed record did not reproduce canonical bytes")
			}

			carrier, err := codec.ExportJSON(context.Background(), record)
			if err != nil {
				t.Fatalf("ExportJSON: %v", err)
			}
			parsedCarrier, err := codec.ExportJSON(context.Background(), parsed)
			if err != nil {
				t.Fatalf("ExportJSON parsed: %v", err)
			}
			if !json.Valid(carrier) || !bytes.Equal(carrier, parsedCarrier) {
				t.Fatalf("carrier round trip differs or is invalid\noriginal: %q\nparsed:   %q", carrier, parsedCarrier)
			}
		})
	}

	review, err := codec.EncodeFlatReviewV1(context.Background(), wantKind)
	if err != nil {
		t.Fatalf("EncodeFlatReviewV1: %v", err)
	}
	reviewAgain, err := codec.EncodeFlatReviewV1(context.Background(), wantKind)
	if err != nil {
		t.Fatalf("EncodeFlatReviewV1 repeat: %v", err)
	}
	if !bytes.Equal(review, reviewAgain) || len(review) == 0 || review[0] != 0x1e || bytes.Count(review, []byte{0x1e}) != len(records) {
		t.Fatalf("FlatReviewV1 is unstable or lacks one framed section per record: %q", review)
	}
}

func TestCodecErrorPublicFieldsAndCodes(t *testing.T) {
	kind := cuecodec.Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"}
	want := &cuecodec.CodecError{
		Code:        cuecodec.CodeSchemaGuard,
		Operation:   "guard_schema",
		Kind:        kind,
		RecordPath:  "cue/records/proof_document.cue",
		CUEPath:     "spec.large_integer",
		JSONPointer: "/properties/spec",
		RuleID:      "structured-output-v1",
	}
	var err error = want
	var got *cuecodec.CodecError
	if !errors.As(err, &got) || got != want {
		t.Fatalf("errors.As did not recover CodecError: %#v", got)
	}

	codes := map[cuecodec.ErrorCode]string{
		cuecodec.CodeInvalidArgument:    "INVALID_ARGUMENT",
		cuecodec.CodeUnsupportedKind:    "UNSUPPORTED_KIND",
		cuecodec.CodeRepositoryBoundary: "REPOSITORY_BOUNDARY",
		cuecodec.CodeInputLimit:         "INPUT_LIMIT",
		cuecodec.CodeRead:               "READ",
		cuecodec.CodeSyntax:             "SYNTAX",
		cuecodec.CodeIdentity:           "IDENTITY",
		cuecodec.CodeSchemaValidation:   "SCHEMA_VALIDATION",
		cuecodec.CodeIncomplete:         "INCOMPLETE",
		cuecodec.CodeStructuralLimit:    "STRUCTURAL_LIMIT",
		cuecodec.CodeNonCanonical:       "NON_CANONICAL",
		cuecodec.CodeProjection:         "PROJECTION",
		cuecodec.CodeSchemaGeneration:   "SCHEMA_GENERATION",
		cuecodec.CodeSchemaGuard:        "SCHEMA_GUARD",
		cuecodec.CodePairedInstance:     "PAIRED_INSTANCE",
	}
	for code, wantString := range codes {
		if gotString := string(code); gotString != wantString {
			t.Errorf("error code = %q, want %q", gotString, wantString)
		}
	}
}

func TestPublicErrorPrecedenceV1ExactResults(t *testing.T) {
	repositoryRoot, err := filepath.Abs(".")
	if err != nil {
		t.Fatal(err)
	}
	wantKind := cuecodec.Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"}
	codec, err := cuecodec.New(cuecodec.Options{
		RepositoryRoot: repositoryRoot,
		Limits:         cuecodec.Limits{MaxDepth: 64, MaxBytes: 1 << 20},
	})
	if err != nil {
		t.Fatal(err)
	}

	for run := 0; run < 2; run++ {
		created, err := cuecodec.New(cuecodec.Options{RepositoryRoot: repositoryRoot, Limits: cuecodec.Limits{MaxDepth: -1}})
		if created != nil {
			t.Fatalf("run %d invalid options returned a codec", run)
		}
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeInvalidArgument, Operation: "new", RuleID: "limits-negative",
		})

		unknown := cuecodec.Kind{APIVersion: wantKind.APIVersion, Kind: "Unknown"}
		record, err := codec.LoadRecord(context.Background(), unknown, "../escape.cue")
		assertZeroRecord(t, record)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeUnsupportedKind, Operation: "load_record", Kind: unknown, RuleID: "kind-not-admitted",
		})

		record, err = codec.LoadRecord(context.Background(), wantKind, "../escape.cue")
		assertZeroRecord(t, record)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeRepositoryBoundary, Operation: "load_record", Kind: wantKind,
			RecordPath: "../escape.cue", RuleID: "record-path",
		})

		record, err = codec.LoadRecord(context.Background(), wantKind, "cue/records/missing.cue")
		assertZeroRecord(t, record)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeRead, Operation: "load_record", Kind: wantKind,
			RecordPath: "cue/records/missing.cue", RuleID: "record-unavailable",
		})

		record, err = codec.ParseCanonical(context.Background(), wantKind, bytes.Repeat([]byte{'{'}, (1<<20)+1))
		assertZeroRecord(t, record)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeInputLimit, Operation: "parse_canonical", Kind: wantKind, RuleID: "max-bytes",
		})

		record, err = codec.ParseCanonical(context.Background(), wantKind, []byte(`{`))
		assertZeroRecord(t, record)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeSyntax, Operation: "parse_canonical", Kind: wantKind, RuleID: "cue-syntax",
		})

		// All three identity fields are absent. The declared CUE-path ordering
		// must select apiVersion independently of evaluation traversal order.
		record, err = codec.ParseCanonical(context.Background(), wantKind, []byte(`{metadata: {}, spec: {}}`))
		assertZeroRecord(t, record)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeIdentity, Operation: "parse_canonical", Kind: wantKind,
			CUEPath: "apiVersion", RuleID: "identity-missing",
		})

		// A later missing identity must not hide the lexicographically earlier
		// apiVersion mismatch from the same identity stage.
		record, err = codec.ParseCanonical(context.Background(), wantKind, []byte(`{
apiVersion: "cuecodec.mishima-computing.github.io/v2"
kind: "ProofDocument"
metadata: {}
spec: {}
}`))
		assertZeroRecord(t, record)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeIdentity, Operation: "parse_canonical", Kind: wantKind,
			CUEPath: "apiVersion", RuleID: "kind-mismatch",
		})

		encoded, err := codec.EncodeCanonical(context.Background(), cuecodec.Record{})
		if encoded != nil {
			t.Fatalf("run %d zero-record encode returned %q", run, encoded)
		}
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeInvalidArgument, Operation: "encode_canonical", RuleID: "record-owner",
		})
	}
}

func TestPublicLateStageErrorsAreExactAndReturnNoCodec(t *testing.T) {
	const schemaPath = "cue/schema/proof_document.cue"
	const recordPath = "cue/records/proof_document.cue"
	kind := cuecodec.Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"}
	tests := []struct {
		name      string
		mutations []externalMutation
		limits    cuecodec.Limits
		want      cuecodec.CodecError
	}{
		{
			name: "schema validation",
			mutations: []externalMutation{{recordPath,
				`stable_id:  "proof-document:precision-proof"`,
				"stable_id:  \"proof-document:precision-proof\"\n\textra:      true"}},
			want: cuecodec.CodecError{
				Code: cuecodec.CodeSchemaValidation, Operation: "new", Kind: kind,
				RecordPath: recordPath, CUEPath: "extra", RuleID: "schema-unification",
			},
		},
		{
			name: "recursive incompleteness",
			mutations: []externalMutation{{recordPath,
				`title:  "Precision-preserving admission proof"`, `title:  string`}},
			want: cuecodec.CodecError{
				Code: cuecodec.CodeIncomplete, Operation: "new", Kind: kind,
				RecordPath: recordPath, CUEPath: "metadata.title", RuleID: "recursive-concreteness",
			},
		},
		{
			name:   "structural limit",
			limits: cuecodec.Limits{MaxDepth: 1},
			want: cuecodec.CodecError{
				Code: cuecodec.CodeStructuralLimit, Operation: "new", Kind: kind,
				RecordPath: recordPath, RuleID: "max-depth",
			},
		},
		{
			name:      "schema guard",
			mutations: []externalMutation{{schemaPath, `weight!: int`, `weight!: int & >=0`}},
			want: cuecodec.CodecError{
				Code: cuecodec.CodeSchemaGuard, Operation: "new", Kind: kind,
				RecordPath: schemaPath, CUEPath: "#ProofDocument",
				JSONPointer: "/properties/spec/properties/ordered_steps/items/properties/weight/allOf",
				RuleID:      "unsupported_keyword",
			},
		},
		{
			name: "identity precedes schema validation",
			mutations: []externalMutation{
				{recordPath, `apiVersion: "cuecodec.mishima-computing.github.io/v1"`, `apiVersion: "cuecodec.mishima-computing.github.io/v2"`},
				{recordPath, `stable_id:  "proof-document:precision-proof"`, "stable_id:  \"proof-document:precision-proof\"\n\textra:      true"},
			},
			want: cuecodec.CodecError{
				Code: cuecodec.CodeIdentity, Operation: "new", Kind: kind,
				RecordPath: recordPath, CUEPath: "apiVersion", RuleID: "kind-mismatch",
			},
		},
		{
			name: "schema validation precedes incompleteness",
			mutations: []externalMutation{
				{recordPath, `stable_id:  "proof-document:precision-proof"`, "stable_id:  \"proof-document:precision-proof\"\n\textra:      true"},
				{recordPath, `title:  "Precision-preserving admission proof"`, `title:  string`},
			},
			want: cuecodec.CodecError{
				Code: cuecodec.CodeSchemaValidation, Operation: "new", Kind: kind,
				RecordPath: recordPath, CUEPath: "extra", RuleID: "schema-unification",
			},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := copyExternalAuthority(t, schemaPath, recordPath)
			for _, mutation := range tc.mutations {
				mutateExternalAuthority(t, root, mutation)
			}
			for run := 0; run < 2; run++ {
				codec, err := cuecodec.New(cuecodec.Options{RepositoryRoot: root, Limits: tc.limits})
				if codec != nil {
					t.Fatalf("run %d returned a partial codec", run)
				}
				assertExactCodecError(t, err, tc.want)
			}
		})
	}
}

func TestPublicNonCanonicalErrorIsExactAndReturnsZeroRecord(t *testing.T) {
	root, err := filepath.Abs(".")
	if err != nil {
		t.Fatal(err)
	}
	codec, err := cuecodec.New(cuecodec.Options{RepositoryRoot: root})
	if err != nil {
		t.Fatal(err)
	}
	kind := cuecodec.Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"}
	record, err := codec.LoadRecord(context.Background(), kind, "cue/records/proof_document.cue")
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := codec.EncodeCanonical(context.Background(), record)
	if err != nil {
		t.Fatal(err)
	}
	for run := 0; run < 2; run++ {
		parsed, err := codec.ParseCanonical(context.Background(), kind, append([]byte(" \n"), canonical...))
		assertZeroRecord(t, parsed)
		assertExactCodecError(t, err, cuecodec.CodecError{
			Code: cuecodec.CodeNonCanonical, Operation: "parse_canonical", Kind: kind,
			RuleID: "final-cue-text-v1",
		})
	}
}

// Projection, schema-generation, and paired-instance failures are defensive
// admission codes with no caller-controlled public fixture after the earlier
// CUE/schema/guard gates succeed. Their literal values remain compiled by the
// public code table above; backend fault injection is intentionally not public.
func TestPublicContractV1RecordsAdmissionInternalCodesAsInapplicable(t *testing.T) {
	got := []cuecodec.ErrorCode{
		cuecodec.CodeProjection,
		cuecodec.CodeSchemaGeneration,
		cuecodec.CodePairedInstance,
	}
	want := []cuecodec.ErrorCode{"PROJECTION", "SCHEMA_GENERATION", "PAIRED_INSTANCE"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("internal admission code %d = %q, want %q", i, got[i], want[i])
		}
	}
}

type externalMutation struct {
	path        string
	old         string
	replacement string
}

func copyExternalAuthority(t *testing.T, schemaPath, recordPath string) string {
	t.Helper()
	root := t.TempDir()
	for _, name := range []string{"cue.mod/module.cue", schemaPath, recordPath, "cue/records/proof_document_release.cue"} {
		content, err := os.ReadFile(filepath.FromSlash(name))
		if err != nil {
			t.Fatal(err)
		}
		target := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, content, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func mutateExternalAuthority(t *testing.T, root string, mutation externalMutation) {
	t.Helper()
	target := filepath.Join(root, filepath.FromSlash(mutation.path))
	content, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if count := strings.Count(string(content), mutation.old); count != 1 {
		t.Fatalf("%s contains %d copies of mutation target %q", mutation.path, count, mutation.old)
	}
	updated := strings.Replace(string(content), mutation.old, mutation.replacement, 1)
	if err := os.WriteFile(target, []byte(updated), 0o644); err != nil {
		t.Fatal(err)
	}
}

func assertExactCodecError(t *testing.T, err error, want cuecodec.CodecError) {
	t.Helper()
	var got *cuecodec.CodecError
	if !errors.As(err, &got) || got == nil {
		t.Fatalf("error = %T %v, want *cuecodec.CodecError %#v", err, err, want)
	}
	if *got != want {
		t.Fatalf("CodecError = %#v, want %#v", *got, want)
	}
}

func assertZeroRecord(t *testing.T, record cuecodec.Record) {
	t.Helper()
	if record.Kind() != (cuecodec.Kind{}) || record.StableID() != "" || record.SourcePath() != "" || record.Value().Exists() {
		t.Fatalf("partial record returned: kind=%#v stable_id=%q source=%q value_exists=%v", record.Kind(), record.StableID(), record.SourcePath(), record.Value().Exists())
	}
}
