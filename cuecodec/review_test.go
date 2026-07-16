package cuecodec

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestFlatReviewV1GoldenAndStableIDLocality(t *testing.T) {
	baselineCodec := newTestCodec(t, ".", Limits{})
	baseline, err := baselineCodec.EncodeFlatReviewV1(context.Background(), firstKindV1)
	if err != nil {
		t.Fatal(err)
	}
	repeated, err := baselineCodec.EncodeFlatReviewV1(context.Background(), firstKindV1)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(baseline, repeated) {
		t.Fatal("FlatReviewV1 changed across repeated encodes")
	}

	want := flatReviewGoldenCandidate(t)
	if !bytes.Equal(baseline, want) {
		t.Fatalf("FlatReviewV1 golden differs\ngot:  %q\nwant: %q", baseline, want)
	}
	if got, wantDigest := sha256.Sum256(baseline), "5338ac79f80b71f4293309b93531f1924f24c91df8424d1d6de3e497df843409"; hex.EncodeToString(got[:]) != wantDigest {
		t.Fatalf("FlatReviewV1 SHA-256 = %x, want %s", got, wantDigest)
	}

	baselineOrder, baselineSections := splitFlatReviewCandidate(t, baseline)
	wantOrder := []string{recordStableV1, releaseStableV1}
	if !reflect.DeepEqual(baselineOrder, wantOrder) {
		t.Fatalf("stable-ID section order = %#v, want %#v", baselineOrder, wantOrder)
	}

	mutations := []struct {
		name        string
		recordPath  string
		old         string
		replacement string
		stableID    string
	}{
		{
			name: "precision record", recordPath: recordPathV1,
			old: `title:  "Precision-preserving admission proof"`, replacement: `title:  "Precision-preserving locality mutation"`,
			stableID: recordStableV1,
		},
		{
			name: "release record", recordPath: releasePathV1,
			old: `title:  "Release readiness admission proof"`, replacement: `title:  "Release readiness locality mutation"`,
			stableID: releaseStableV1,
		},
	}
	for _, tc := range mutations {
		t.Run(tc.name, func(t *testing.T) {
			root := copyAuthority(t)
			mutateAuthority(t, root, tc.recordPath, tc.old, tc.replacement)
			changedCodec := newTestCodec(t, root, Limits{})
			changed, err := changedCodec.EncodeFlatReviewV1(context.Background(), firstKindV1)
			if err != nil {
				t.Fatal(err)
			}
			changedOrder, changedSections := splitFlatReviewCandidate(t, changed)
			if !reflect.DeepEqual(changedOrder, wantOrder) {
				t.Fatalf("changed stable-ID order = %#v, want %#v", changedOrder, wantOrder)
			}
			for stableID, before := range baselineSections {
				after, ok := changedSections[stableID]
				if !ok {
					t.Fatalf("changed candidate lacks section %q", stableID)
				}
				if stableID == tc.stableID {
					if bytes.Equal(before, after) {
						t.Fatalf("changed record %q retained identical section bytes", stableID)
					}
					continue
				}
				if !bytes.Equal(before, after) {
					t.Fatalf("changing %q rewrote unrelated section %q\nbefore: %q\nafter:  %q", tc.stableID, stableID, before, after)
				}
			}
		})
	}
}

func TestFlatReviewV1IsOutsideAuthorityLoading(t *testing.T) {
	root := copyAuthority(t)
	before := newTestCodec(t, root, Limits{})
	candidate, err := before.EncodeFlatReviewV1(context.Background(), firstKindV1)
	if err != nil {
		t.Fatal(err)
	}
	beforeEntry := before.entries[keyFor(firstKindV1)]

	// Use a .cue suffix deliberately: exclusion must follow the declared root
	// layout, not an extension heuristic or the bytes' syntactic validity.
	reviewPath := filepath.Join(root, "testdata", "locality", "flat-review.cue")
	if err := os.MkdirAll(filepath.Dir(reviewPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(reviewPath, candidate, 0o644); err != nil {
		t.Fatal(err)
	}

	// The existing codec's snapshot gate and a completely fresh authority load
	// must both ignore the output-only review candidate.
	again, err := before.EncodeFlatReviewV1(context.Background(), firstKindV1)
	if err != nil || !bytes.Equal(again, candidate) {
		t.Fatalf("review output changed existing codec result: (%q, %v)", again, err)
	}
	after := newTestCodec(t, root, Limits{})
	afterEntry := after.entries[keyFor(firstKindV1)]
	if before.snapshot.SHA256 != after.snapshot.SHA256 ||
		before.closure.SHA256 != after.closure.SHA256 ||
		beforeEntry.proof != afterEntry.proof ||
		!bytes.Equal(beforeEntry.schemaBytes, afterEntry.schemaBytes) {
		t.Fatal("review output entered source, closure, admission, or schema authority")
	}
	if _, present := after.snapshot.Content("testdata/locality/flat-review.cue"); present {
		t.Fatal("review output appeared in SourceSnapshotV1")
	}
}

func TestEncodeFlatReviewV1ErrorsHaveNoPartialCandidate(t *testing.T) {
	root := copyAuthority(t)
	c := newTestCodec(t, root, Limits{})

	if result, err := c.EncodeFlatReviewV1(nil, firstKindV1); result != nil {
		t.Fatalf("nil context returned partial candidate %q", result)
	} else {
		assertReviewCodecError(t, err, CodecError{Code: CodeInvalidArgument, Operation: "encode_flat_review_v1", Kind: firstKindV1, RuleID: "context"})
	}
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	if result, err := c.EncodeFlatReviewV1(canceled, firstKindV1); result != nil {
		t.Fatalf("canceled context returned partial candidate %q", result)
	} else {
		assertReviewCodecError(t, err, CodecError{Code: CodeInvalidArgument, Operation: "encode_flat_review_v1", Kind: firstKindV1, RuleID: "context"})
	}
	if result, err := c.EncodeFlatReviewV1(context.Background(), Kind{}); result != nil {
		t.Fatalf("invalid kind returned partial candidate %q", result)
	} else {
		assertReviewCodecError(t, err, CodecError{Code: CodeInvalidArgument, Operation: "encode_flat_review_v1", RuleID: "kind-identity"})
	}
	unknown := Kind{APIVersion: firstKindV1.APIVersion, Kind: "Unknown"}
	if result, err := c.EncodeFlatReviewV1(context.Background(), unknown); result != nil {
		t.Fatalf("unknown kind returned partial candidate %q", result)
	} else {
		assertReviewCodecError(t, err, CodecError{Code: CodeUnsupportedKind, Operation: "encode_flat_review_v1", Kind: unknown, RuleID: "kind-not-admitted"})
	}
	if result, err := (*Codec)(nil).EncodeFlatReviewV1(context.Background(), firstKindV1); result != nil {
		t.Fatalf("nil codec returned partial candidate %q", result)
	} else {
		assertReviewCodecError(t, err, CodecError{Code: CodeInvalidArgument, Operation: "encode_flat_review_v1", Kind: firstKindV1, RuleID: "nil-codec"})
	}

	candidate, err := c.EncodeFlatReviewV1(context.Background(), firstKindV1)
	if err != nil {
		t.Fatal(err)
	}
	originalLimit := c.limits.MaxBytes
	c.limits.MaxBytes = len(candidate)
	if result, err := c.EncodeFlatReviewV1(context.Background(), firstKindV1); err != nil || !bytes.Equal(result, candidate) {
		t.Fatalf("exact candidate limit = (%q, %v), want complete candidate", result, err)
	}
	c.limits.MaxBytes = len(candidate) - 1
	if result, err := c.EncodeFlatReviewV1(context.Background(), firstKindV1); result != nil {
		t.Fatalf("candidate limit returned partial candidate %q", result)
	} else {
		assertReviewCodecError(t, err, CodecError{Code: CodeStructuralLimit, Operation: "encode_flat_review_v1", Kind: firstKindV1, RuleID: "max-bytes"})
	}
	c.limits.MaxBytes = originalLimit

	mutateAuthority(t, root, recordPathV1,
		`title:  "Precision-preserving admission proof"`,
		`title:  "Precision-preserving drift"`)
	if result, err := c.EncodeFlatReviewV1(context.Background(), firstKindV1); result != nil {
		t.Fatalf("snapshot drift returned partial candidate %q", result)
	} else {
		assertReviewCodecError(t, err, CodecError{
			Code: CodeRead, Operation: "encode_flat_review_v1", Kind: firstKindV1,
			RecordPath: recordPathV1, RuleID: "snapshot-changed",
		})
	}
}

func assertReviewCodecError(t *testing.T, err error, want CodecError) {
	t.Helper()
	got, ok := err.(*CodecError)
	if !ok || got == nil || *got != want {
		t.Fatalf("CodecError = %#v (%v), want %#v", got, err, want)
	}
}

func flatReviewGoldenCandidate(t *testing.T) []byte {
	t.Helper()
	var candidate []byte
	for _, name := range []string{
		"proof_document_precision.flat-review-v1.json",
		"proof_document_release.flat-review-v1.json",
	} {
		section, err := os.ReadFile(filepath.Join("testdata", "locality", name))
		if err != nil {
			t.Fatal(err)
		}
		candidate = append(candidate, 0x1e)
		candidate = append(candidate, section...)
	}
	return candidate
}

func splitFlatReviewCandidate(t *testing.T, candidate []byte) ([]string, map[string][]byte) {
	t.Helper()
	remaining := candidate
	sections := make(map[string][]byte)
	var order []string
	for len(remaining) > 0 {
		if remaining[0] != 0x1e {
			t.Fatalf("section does not start with RS: %q", remaining)
		}
		newline := bytes.IndexByte(remaining, '\n')
		if newline < 0 {
			t.Fatalf("section has no terminal LF: %q", remaining)
		}
		frame := bytes.Clone(remaining[:newline+1])
		var identity struct {
			StableID string `json:"stable_id"`
		}
		if err := json.Unmarshal(frame[1:len(frame)-1], &identity); err != nil {
			t.Fatalf("invalid section JSON: %v", err)
		}
		if identity.StableID == "" {
			t.Fatal("section has empty stable_id")
		}
		if _, duplicate := sections[identity.StableID]; duplicate {
			t.Fatalf("duplicate section stable_id %q", identity.StableID)
		}
		order = append(order, identity.StableID)
		sections[identity.StableID] = frame
		remaining = remaining[newline+1:]
	}
	return order, sections
}
