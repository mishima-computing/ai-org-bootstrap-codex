package cuecodec

import (
	"encoding/json"
	"os"
	"runtime"
	"strings"
	"testing"
)

func TestModuleAndDependencyIntegrityManifestV1(t *testing.T) {
	goMod, err := os.ReadFile("go.mod")
	if err != nil {
		t.Fatal(err)
	}
	const wantGoMod = `module github.com/mishima-computing/cuecodec

go 1.26.5

require cuelang.org/go v0.17.0

require (
	cuelabs.dev/go/oci/ociregistry v0.0.0-20260601085548-328ff8e2c943 // indirect
	github.com/cockroachdb/apd/v3 v3.2.3 // indirect
	github.com/emicklei/proto v1.14.3 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/mitchellh/go-wordwrap v1.0.1 // indirect
	github.com/opencontainers/go-digest v1.0.0 // indirect
	github.com/opencontainers/image-spec v1.1.1 // indirect
	github.com/pelletier/go-toml/v2 v2.3.1 // indirect
	github.com/protocolbuffers/txtpbfmt v0.0.0-20260420112717-c39628bde8b5 // indirect
	github.com/rogpeppe/go-internal v1.15.0 // indirect
	go.yaml.in/yaml/v3 v3.0.4 // indirect
	golang.org/x/net v0.56.0 // indirect
	golang.org/x/oauth2 v0.36.0 // indirect
	golang.org/x/sync v0.21.0 // indirect
	golang.org/x/text v0.38.0 // indirect
	google.golang.org/protobuf v1.33.0 // indirect
)
`
	if string(goMod) != wantGoMod {
		t.Fatalf("go.mod differs from the literal pinned contract:\n%s", goMod)
	}
	goSum, err := os.ReadFile("go.sum")
	if err != nil {
		t.Fatal(err)
	}
	const wantModuleSum = "cuelang.org/go v0.17.0 h1:PrijS5ofUD01yiG11w74I04laXKLaBiMhEYvdt8Gb/A="
	const wantGoModSum = "cuelang.org/go v0.17.0/go.mod h1:xlly/o1wSLvxOsi5vkQGieU0rLOt7TvUIizOFtnxHRU="
	if !strings.Contains(string(goSum), wantModuleSum+"\n") || !strings.Contains(string(goSum), wantGoModSum+"\n") {
		t.Fatalf("go.sum lacks the pinned module checksums:\n%s", goSum)
	}

	manifestBytes, err := os.ReadFile("public-contract-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var manifest struct {
		Contract     string `json:"contract"`
		ModulePath   string `json:"module_path"`
		GoRelease    string `json:"go_release"`
		AdmittedKind struct {
			APIVersion string `json:"api_version"`
			Kind       string `json:"kind"`
			RecordPath string `json:"record_path"`
			StableID   string `json:"stable_id"`
			Records    []struct {
				RecordPath string `json:"record_path"`
				StableID   string `json:"stable_id"`
			} `json:"records"`
		} `json:"admitted_kind"`
		Dependencies []struct {
			Module   string `json:"module"`
			Version  string `json:"version"`
			Sum      string `json:"sum"`
			GoModSum string `json:"go_mod_sum"`
		} `json:"dependencies"`
		ReviewFormats []struct {
			Name              string `json:"name"`
			Encoding          string `json:"encoding"`
			SectionFraming    string `json:"section_framing"`
			SectionJSON       string `json:"section_json"`
			SectionOrder      string `json:"section_order"`
			RowOrder          string `json:"row_order"`
			RowValues         string `json:"row_values"`
			AuthorityBoundary string `json:"authority_boundary"`
		} `json:"review_formats"`
	}
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.Contract != "public-contract-v1" || manifest.ModulePath != "github.com/mishima-computing/cuecodec" || manifest.GoRelease != "1.26.5" || len(manifest.Dependencies) != 1 {
		t.Fatalf("public contract identity = %#v", manifest)
	}
	dependency := manifest.Dependencies[0]
	if dependency.Module != "cuelang.org/go" || dependency.Version != "v0.17.0" ||
		dependency.Sum != strings.TrimPrefix(wantModuleSum, "cuelang.org/go v0.17.0 ") ||
		dependency.GoModSum != strings.TrimPrefix(wantGoModSum, "cuelang.org/go v0.17.0/go.mod ") {
		t.Fatalf("public contract dependency = %#v", dependency)
	}
	admitted := manifest.AdmittedKind
	if admitted.APIVersion != "cuecodec.mishima-computing.github.io/v1" || admitted.Kind != "ProofDocument" ||
		admitted.RecordPath != "cue/records/proof_document.cue" || admitted.StableID != "proof-document:precision-proof" ||
		len(admitted.Records) != 2 ||
		admitted.Records[0].RecordPath != "cue/records/proof_document.cue" || admitted.Records[0].StableID != "proof-document:precision-proof" ||
		admitted.Records[1].RecordPath != "cue/records/proof_document_release.cue" || admitted.Records[1].StableID != "proof-document:release-readiness" {
		t.Fatalf("public contract admitted corpus = %#v", admitted)
	}
	if len(manifest.ReviewFormats) != 1 {
		t.Fatalf("public contract review formats = %#v", manifest.ReviewFormats)
	}
	review := manifest.ReviewFormats[0]
	if review.Name != "FlatReviewV1" || review.Encoding != "RFC 7464 JSON text sequence" ||
		review.SectionFraming != "RS + compact JSON + LF" || review.SectionOrder != "stable_id byte order" ||
		review.SectionJSON != "UTF-8; fields rows then stable_id; row fields pointer then value; Go 1.26.5 encoding/json string escaping" ||
		review.RowOrder != "escaped RFC 6901 JSON Pointer byte order" ||
		review.RowValues != "canonical precision-preserving carrier scalar tokens and empty containers" ||
		review.AuthorityBoundary != "output-only" {
		t.Fatalf("public contract FlatReviewV1 = %#v", review)
	}
}

func TestPinnedToolchainPrerequisites(t *testing.T) {
	const wantGo = "go1.26.5"
	if got := runtime.Version(); got != wantGo {
		t.Fatalf("PREREQUISITE_FAILED: Go runtime = %q, want %q", got, wantGo)
	}
	pin, err := os.ReadFile(".go-version")
	if err != nil {
		t.Fatalf("PREREQUISITE_FAILED: read .go-version: %v", err)
	}
	if got := strings.TrimSpace(string(pin)); got != strings.TrimPrefix(wantGo, "go") {
		t.Fatalf("PREREQUISITE_FAILED: .go-version = %q, want %q", got, strings.TrimPrefix(wantGo, "go"))
	}
}
