package cuecodec

import (
	"errors"
	"reflect"
	"sort"
	"testing"
)

func TestCodecErrorIsStableAndSupportsErrorsAs(t *testing.T) {
	want := &CodecError{
		Code:        CodeSchemaGuard,
		Operation:   "guard_schema",
		Kind:        Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"},
		RecordPath:  "cue/records/proof_document.cue",
		CUEPath:     "spec.large_integer",
		JSONPointer: "/properties/spec",
		RuleID:      "structured-output-v1",
	}
	const wantText = `cuecodec: SCHEMA_GUARD operation="guard_schema" kind="cuecodec.mishima-computing.github.io/v1/ProofDocument" record_path="cue/records/proof_document.cue" cue_path="spec.large_integer" json_pointer="/properties/spec" rule_id="structured-output-v1"`
	for i := 0; i < 2; i++ {
		if got := want.Error(); got != wantText {
			t.Fatalf("run %d Error() = %q, want %q", i, got, wantText)
		}
	}
	var err error = want
	var got *CodecError
	if !errors.As(err, &got) || got != want {
		t.Fatalf("errors.As result = %#v", got)
	}
}

func TestErrorPrecedenceV1GuardPrimaryIsIndependentOfEvidenceOrder(t *testing.T) {
	kind := Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"}
	pointerFirst := &CodecError{
		Code: CodeSchemaGuard, Operation: "new", Kind: kind,
		RecordPath: "cue/records/z.cue", JSONPointer: "/a", RuleID: "unsupported_keyword",
	}
	recordFirst := &CodecError{
		Code: CodeSchemaGuard, Operation: "new", Kind: kind,
		RecordPath: "cue/records/a.cue", JSONPointer: "/z", RuleID: "depth_limit",
	}
	if got := selectCodecError(pointerFirst, recordFirst); got != recordFirst {
		t.Fatalf("public guard primary = %#v, want record-path winner %#v", got, recordFirst)
	}
	evidence := []*CodecError{recordFirst, pointerFirst}
	sort.Slice(evidence, func(i, j int) bool {
		if evidence[i].JSONPointer != evidence[j].JSONPointer {
			return evidence[i].JSONPointer < evidence[j].JSONPointer
		}
		return ruleRank(evidence[i].RuleID) < ruleRank(evidence[j].RuleID)
	})
	if evidence[0] != pointerFirst {
		t.Fatalf("evidence order = %#v, want JSON-pointer winner first", evidence)
	}
}

func TestErrorPrecedenceV1AdjacentAndSameStage(t *testing.T) {
	kind := Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"}
	load := &CodecError{Code: CodeSyntax, Operation: "new", Kind: kind, RecordPath: "cue/records/z.cue", RuleID: "cue-syntax"}
	identity := &CodecError{Code: CodeIdentity, Operation: "new", Kind: kind, RecordPath: "cue/records/a.cue", RuleID: "kind"}
	if got := selectCodecError(identity, load); got != load {
		t.Fatalf("adjacent-stage selection = %#v, want syntax/load error", got)
	}

	a := &CodecError{Code: CodeSchemaValidation, Operation: "new", Kind: kind, RecordPath: "cue/records/a.cue", CUEPath: "spec.a", RuleID: "schema"}
	b := &CodecError{Code: CodeSchemaValidation, Operation: "new", Kind: kind, RecordPath: "cue/records/b.cue", CUEPath: "spec.b", RuleID: "schema"}
	aBefore, bBefore := *a, *b
	for i := 0; i < 2; i++ {
		if got := selectCodecError(b, a); got != a {
			t.Fatalf("run %d same-stage selection = %#v, want a", i, got)
		}
	}
	if !reflect.DeepEqual(*a, aBefore) || !reflect.DeepEqual(*b, bBefore) {
		t.Fatal("error selection mutated a candidate")
	}
}

func TestErrorPrecedenceV1ReturnsNilForNoCandidate(t *testing.T) {
	if got := selectCodecError(nil, nil); got != nil {
		t.Fatalf("selection = %#v, want nil", got)
	}
}

func TestErrorPrecedenceV1DriftIsFinalAndRuleOrdinalsAreDeclared(t *testing.T) {
	kind := Kind{APIVersion: "cuecodec.mishima-computing.github.io/v1", Kind: "ProofDocument"}
	drift := &CodecError{Code: CodeRead, Operation: "load_record", Kind: kind, RuleID: "snapshot-changed"}
	syntax := &CodecError{Code: CodeSyntax, Operation: "load_record", Kind: kind, RuleID: "cue-syntax"}
	if got := selectCodecError(drift, syntax); got != syntax {
		t.Fatalf("drift/earlier-stage selection = %#v, want syntax", got)
	}

	laterRuleLexically := &CodecError{Code: CodeIdentity, Operation: "new", Kind: kind, RuleID: "identity-missing"}
	earlierDeclaredRule := &CodecError{Code: CodeIdentity, Operation: "new", Kind: kind, RuleID: "record-shape"}
	// CUE/location fields tie, so the declared ordinal—not accidental arrival
	// order—selects the primary result.
	if got := selectCodecError(laterRuleLexically, earlierDeclaredRule); got != earlierDeclaredRule {
		t.Fatalf("rule ordinal selection = %#v, want %#v", got, earlierDeclaredRule)
	}
}
