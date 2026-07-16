package flatreview

import (
	"bytes"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestEncodeStableIDAndPointerOrder(t *testing.T) {
	records := []Record{
		{StableID: "z:id", Carrier: []byte(`{"value":2}`)},
		{StableID: "a:id", Carrier: []byte(`{"value":1}`)},
	}
	original := []Record{
		{StableID: records[0].StableID, Carrier: bytes.Clone(records[0].Carrier)},
		{StableID: records[1].StableID, Carrier: bytes.Clone(records[1].Carrier)},
	}
	want := []byte("\x1e" + `{"rows":[{"pointer":"/value","value":1}],"stable_id":"a:id"}` + "\n" +
		"\x1e" + `{"rows":[{"pointer":"/value","value":2}],"stable_id":"z:id"}` + "\n")

	for run := 0; run < 2; run++ {
		got, err := Encode(records)
		if err != nil {
			t.Fatalf("run %d: Encode: %v", run, err)
		}
		if !bytes.Equal(got, want) {
			t.Fatalf("run %d: candidate = %q, want %q", run, got, want)
		}
	}
	if !reflect.DeepEqual(records, original) {
		t.Fatalf("Encode mutated input\ngot:  %#v\nwant: %#v", records, original)
	}
}

func TestEncodeEscapesPointersAndPreservesExactValues(t *testing.T) {
	carrier, err := os.ReadFile(filepath.Join("..", "..", "testdata", "locality", "pointers.json"))
	if err != nil {
		t.Fatal(err)
	}
	got, err := Encode([]Record{{StableID: "escape:test", Carrier: carrier}})
	if err != nil {
		t.Fatal(err)
	}
	want := []byte("\x1e" + `{"rows":[{"pointer":"/array/0","value":0},{"pointer":"/array/1","value":1},{"pointer":"/array/10","value":10},{"pointer":"/array/2","value":2},{"pointer":"/array/3","value":3},{"pointer":"/array/4","value":4},{"pointer":"/array/5","value":5},{"pointer":"/array/6","value":6},{"pointer":"/array/7","value":7},{"pointer":"/array/8","value":8},{"pointer":"/array/9","value":9},{"pointer":"/a~1b/m~0n","value":1},{"pointer":"/empty~0object","value":{}},{"pointer":"/empty~1array","value":[]},{"pointer":"/exact","value":0.1000000000000000000000000001},{"pointer":"/large","value":900719925474099312345678901234567890},{"pointer":"/z","value":0}],"stable_id":"escape:test"}` + "\n")
	if !bytes.Equal(got, want) {
		t.Fatalf("escaped candidate differs\ngot:  %q\nwant: %q", got, want)
	}
}

func TestEncodeRejectsInvalidCorpusWithoutPartialBytes(t *testing.T) {
	tests := []struct {
		name    string
		records []Record
	}{
		{name: "empty stable ID", records: []Record{{Carrier: []byte(`null`)}}},
		{name: "invalid stable ID UTF-8", records: []Record{{StableID: string([]byte{0xff}), Carrier: []byte(`null`)}}},
		{name: "duplicate stable ID", records: []Record{{StableID: "same:id", Carrier: []byte(`1`)}, {StableID: "same:id", Carrier: []byte(`2`)}}},
		{name: "duplicate carrier key", records: []Record{{StableID: "bad:id", Carrier: []byte(`{"a":1,"a":2}`)}}},
		{name: "invalid carrier", records: []Record{{StableID: "bad:id", Carrier: []byte(`{`)}}},
		{name: "isolated high surrogate", records: []Record{{StableID: "bad:id", Carrier: []byte(`{"value":"\ud800"}`)}}},
		{name: "isolated low surrogate", records: []Record{{StableID: "bad:id", Carrier: []byte(`{"value":"\udc00"}`)}}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Encode(tc.records)
			if err == nil || got != nil {
				t.Fatalf("Encode = (%q, %v), want nil/error", got, err)
			}
		})
	}
}

func TestEncodeSelectsDuplicateBeforeCarrierFault(t *testing.T) {
	for _, records := range [][]Record{
		{{StableID: "same:id", Carrier: []byte(`{`)}, {StableID: "same:id", Carrier: []byte(`null`)}},
		{{StableID: "same:id", Carrier: []byte(`null`)}, {StableID: "same:id", Carrier: []byte(`{`)}},
	} {
		got, err := Encode(records)
		if got != nil || err == nil || !strings.Contains(err.Error(), "duplicate stable ID") {
			t.Fatalf("duplicate/carrier compound = (%q, %v), want nil/duplicate stable ID", got, err)
		}
	}
}

func TestEncodeAcceptsPairedUnicodeSurrogates(t *testing.T) {
	got, err := Encode([]Record{{StableID: "unicode:test", Carrier: []byte(`{"value":"\ud83d\ude00"}`)}})
	if err != nil {
		t.Fatal(err)
	}
	want := []byte("\x1e" + `{"rows":[{"pointer":"/value","value":"😀"}],"stable_id":"unicode:test"}` + "\n")
	if !bytes.Equal(got, want) {
		t.Fatalf("paired surrogate candidate = %q, want %q", got, want)
	}
}

func TestEncodeRootAndFramingEdges(t *testing.T) {
	tests := []struct {
		name    string
		carrier string
		wantRow string
	}{
		{name: "root null", carrier: `null`, wantRow: `{"pointer":"","value":null}`},
		{name: "root object", carrier: `{}`, wantRow: `{"pointer":"","value":{}}`},
		{name: "root array", carrier: `[]`, wantRow: `{"pointer":"","value":[]}`},
		{name: "empty member", carrier: `{"":1}`, wantRow: `{"pointer":"/","value":1}`},
		{name: "escaped surrogate text", carrier: `{"value":"\\ud800"}`, wantRow: `{"pointer":"/value","value":"\\ud800"}`},
		{name: "embedded separators", carrier: `{"value":"\u001e\n"}`, wantRow: `{"pointer":"/value","value":"\u001e\n"}`},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Encode([]Record{{StableID: "edge:test", Carrier: []byte(tc.carrier)}})
			if err != nil {
				t.Fatal(err)
			}
			want := []byte("\x1e" + `{"rows":[` + tc.wantRow + `],"stable_id":"edge:test"}` + "\n")
			if !bytes.Equal(got, want) {
				t.Fatalf("edge candidate = %q, want %q", got, want)
			}
			if bytes.Count(got, []byte{0x1e}) != 1 || bytes.Count(got, []byte{'\n'}) != 1 {
				t.Fatalf("data escaped framing delimiters: %q", got)
			}
		})
	}
}

func TestOutputOnlyImportBoundary(t *testing.T) {
	repositoryRoot := filepath.Clean(filepath.Join("..", ".."))
	imports := productionImports(t, filepath.Join(repositoryRoot, "internal", "flatreview"))
	for _, imported := range imports {
		if strings.HasPrefix(imported, "cuelang.org/go") ||
			imported == "github.com/mishima-computing/cuecodec/internal/cueload" ||
			imported == "github.com/mishima-computing/cuecodec/internal/repository" ||
			imported == "io/fs" || imported == "os" {
			t.Errorf("internal/flatreview imports authority/filesystem package %q", imported)
		}
	}

	for _, authorityPackage := range []string{"cueload", "repository"} {
		imports := productionImports(t, filepath.Join(repositoryRoot, "internal", authorityPackage))
		for _, imported := range imports {
			if imported == "github.com/mishima-computing/cuecodec/internal/flatreview" {
				t.Errorf("internal/%s imports output-only flatreview package", authorityPackage)
			}
		}
	}
}

func productionImports(t *testing.T, dir string) []string {
	t.Helper()
	packages, err := parser.ParseDir(token.NewFileSet(), dir, func(info os.FileInfo) bool {
		return strings.HasSuffix(info.Name(), ".go") && !strings.HasSuffix(info.Name(), "_test.go")
	}, parser.ImportsOnly)
	if err != nil {
		t.Fatal(err)
	}
	var imports []string
	for _, pkg := range packages {
		for _, file := range pkg.Files {
			for _, spec := range file.Imports {
				path, err := strconv.Unquote(spec.Path.Value)
				if err != nil {
					t.Fatal(err)
				}
				imports = append(imports, path)
			}
		}
	}
	return imports
}
