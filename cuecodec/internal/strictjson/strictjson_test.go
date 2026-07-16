package strictjson

import (
	"errors"
	"strings"
	"testing"
)

func TestParseMaxDepthRejectsBeforeAllocatingOverLimitNode(t *testing.T) {
	for _, test := range []struct {
		name    string
		depth   int
		wantErr bool
	}{
		{name: "below", depth: 63},
		{name: "exact", depth: 64},
		{name: "above", depth: 65, wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			source := strings.Repeat("[", test.depth-1) + "0" + strings.Repeat("]", test.depth-1)
			_, err := ParseMaxDepth([]byte(source), 64)
			if !test.wantErr {
				if err != nil {
					t.Fatalf("ParseMaxDepth: %v", err)
				}
				return
			}
			var parseErr *Error
			if !errors.As(err, &parseErr) || parseErr.Code != DepthLimit {
				t.Fatalf("ParseMaxDepth error = %#v, want depth limit", err)
			}
			if parseErr.Path != strings.Repeat("/0", 64) {
				t.Fatalf("depth path = %q", parseErr.Path)
			}
		})
	}
}

func TestParseMaxDepthPreservesEarlierDuplicateOverLaterDepthLimit(t *testing.T) {
	source := `{"z":1,"z":2,"a":[[[0]]]}`
	_, err := ParseMaxDepth([]byte(source), 4)
	var parseErr *Error
	if !errors.As(err, &parseErr) || parseErr.Code != DuplicateKey || parseErr.Path != "/z" {
		t.Fatalf("ParseMaxDepth error = %#v, want duplicate at /z", parseErr)
	}
}

func TestParseMaxDepthPreservesLaterParseFailuresOverEarlierDepthLimit(t *testing.T) {
	tests := []struct {
		name   string
		source string
		code   ErrorCode
		path   string
	}{
		{name: "duplicate", source: `{"a":[[[0]]],"z":1,"z":2}`, code: DuplicateKey, path: "/z"},
		{name: "syntax", source: `{"a":[[[0]]],"z":[}`, code: InvalidJSON, path: "/z/0"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := ParseMaxDepth([]byte(test.source), 4)
			var parseErr *Error
			if !errors.As(err, &parseErr) || parseErr.Code != test.code || parseErr.Path != test.path {
				t.Fatalf("ParseMaxDepth error = %#v, want %s at %s", parseErr, test.code, test.path)
			}
		})
	}
}

func TestParseMaxDepthFarBeyondLimitKeepsCompactPathState(t *testing.T) {
	const depth = 8000
	source := strings.Repeat(`{"abcdefgh":`, depth-1) + "0" + strings.Repeat("}", depth-1)
	_, err := ParseMaxDepth([]byte(source), 64)
	var parseErr *Error
	if !errors.As(err, &parseErr) || parseErr.Code != DepthLimit {
		t.Fatalf("ParseMaxDepth error = %#v, want depth limit", err)
	}
	if want := strings.Repeat("/abcdefgh", 64); parseErr.Path != want {
		t.Fatalf("depth pointer length = %d, want %d", len(parseErr.Path), len(want))
	}
}

func TestParseMaxDepthDuplicateIndexPreservesEscapedPointerOrder(t *testing.T) {
	source := `{"z/z":1,"z/z":2,"a~a":1,"a~a":2}`
	_, err := ParseMaxDepth([]byte(source), 64)
	var parseErr *Error
	if !errors.As(err, &parseErr) || parseErr.Code != DuplicateKey || parseErr.Path != "/a~0a" {
		t.Fatalf("ParseMaxDepth error = %#v, want duplicate at /a~0a", parseErr)
	}
}

func BenchmarkParseMaxDepthFarBeyondLimit(b *testing.B) {
	const depth = 8000
	source := []byte(strings.Repeat(`{"abcdefgh":`, depth-1) + "0" + strings.Repeat("}", depth-1))
	b.ReportAllocs()
	b.ResetTimer()
	for index := 0; index < b.N; index++ {
		if _, err := ParseMaxDepth(source, 64); err == nil {
			b.Fatal("ParseMaxDepth unexpectedly accepted over-depth input")
		}
	}
}

func TestParseRejectsDuplicateAtStablePointer(t *testing.T) {
	_, err := Parse([]byte(`{"outer":{"value":1,"value":2}}`))
	var parseErr *Error
	if !errors.As(err, &parseErr) {
		t.Fatalf("Parse error = %v, want *strictjson.Error", err)
	}
	if parseErr.Code != DuplicateKey || parseErr.Path != "/outer/value" {
		t.Fatalf("Parse error = %#v, want duplicate at /outer/value", parseErr)
	}
}

func TestParseSelectsLexicographicallySmallestDuplicatePointer(t *testing.T) {
	variants := []string{
		`{"z":1,"z":2,"a":{"b":1,"b":2}}`,
		`{"a":{"b":1,"b":2},"z":1,"z":2}`,
	}
	for _, source := range variants {
		for run := 0; run < 2; run++ {
			_, err := Parse([]byte(source))
			var parseErr *Error
			if !errors.As(err, &parseErr) {
				t.Fatalf("run %d Parse(%s) error = %v, want *Error", run, source, err)
			}
			if parseErr.Code != DuplicateKey || parseErr.Path != "/a/b" {
				t.Fatalf("run %d Parse(%s) error = %#v, want duplicate at /a/b", run, source, parseErr)
			}
		}
	}
}

func TestParseSelectsBetweenDuplicateAndTerminalSyntax(t *testing.T) {
	tests := []struct {
		source string
		code   ErrorCode
		path   string
	}{
		{source: `{"a":1,"a":2,"z":[}`, code: DuplicateKey, path: "/a"},
		{source: `{"z":1,"z":2,"a":[}`, code: InvalidJSON, path: "/a"},
		{source: `{"a":1,"a":2} {}`, code: TrailingData, path: ""},
	}
	for _, tc := range tests {
		for run := 0; run < 2; run++ {
			_, err := Parse([]byte(tc.source))
			var parseErr *Error
			if !errors.As(err, &parseErr) || parseErr.Code != tc.code || parseErr.Path != tc.path {
				t.Fatalf("run %d Parse(%s) error = %#v, want %s at %q", run, tc.source, parseErr, tc.code, tc.path)
			}
		}
	}
}

func TestEqualDoesNotUseFloat64(t *testing.T) {
	a, err := Parse([]byte(`900719925474099312345678901234567890.0`))
	if err != nil {
		t.Fatal(err)
	}
	b, err := Parse([]byte(`9.00719925474099312345678901234567890e35`))
	if err != nil {
		t.Fatal(err)
	}
	if !Equal(a, b) {
		t.Fatal("mathematically equal arbitrary-precision numbers differ")
	}
	c, err := Parse([]byte(`900719925474099312345678901234567891`))
	if err != nil {
		t.Fatal(err)
	}
	if Equal(a, c) {
		t.Fatal("distinct arbitrary-precision numbers compare equal")
	}
}

func TestEqualAndIntegerSupportHugeDecimalExponents(t *testing.T) {
	const exponent = "999999999999999999999"
	a := mustParseJSON(t, `1e`+exponent)
	b := mustParseJSON(t, `10e999999999999999999998`)
	if !Equal(a, b) {
		t.Fatal("mathematically equal huge-exponent numbers differ")
	}
	if !NumberIsInteger(`1e`+exponent) || !NumberIsInteger(`0e-`+exponent) {
		t.Fatal("huge-exponent integer classification failed")
	}
	if NumberIsInteger(`1e-` + exponent) {
		t.Fatal("nonzero number with huge negative exponent classified as integer")
	}
}

func mustParseJSON(t *testing.T, source string) *Node {
	t.Helper()
	node, err := Parse([]byte(source))
	if err != nil {
		t.Fatal(err)
	}
	return node
}

func TestEqualPreservesListOrderAndIgnoresObjectOrder(t *testing.T) {
	a, err := Parse([]byte(`{"object":{"a":1,"b":2},"list":[3,1,2]}`))
	if err != nil {
		t.Fatal(err)
	}
	b, err := Parse([]byte(`{"list":[3,1,2],"object":{"b":2,"a":1}}`))
	if err != nil {
		t.Fatal(err)
	}
	if !Equal(a, b) {
		t.Fatal("source-order-equivalent objects differ")
	}
	c, err := Parse([]byte(`{"object":{"a":1,"b":2},"list":[1,2,3]}`))
	if err != nil {
		t.Fatal(err)
	}
	if Equal(a, c) {
		t.Fatal("different list orders compare equal")
	}
}
