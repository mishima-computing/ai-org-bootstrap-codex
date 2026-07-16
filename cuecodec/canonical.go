package cuecodec

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"unicode/utf8"

	"cuelang.org/go/cue/format"
	"cuelang.org/go/cue/parser"
	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

// encodeCanonical implements final-cue-text-v1. The evaluated public CUE
// value is first projected to precision-preserving JSON, whose object keys are
// recursively byte-sorted by encoding/json. JSON is a CUE expression, so the
// sole sorted expression is then rendered by the pinned CUE formatter without
// the Simplify option. Arrays are never reordered.
func (c *Codec) encodeCanonical(operation string, record Record) ([]byte, error) {
	carrier, err := canonicalCarrierJSON(record.value)
	if err != nil {
		return nil, codecError(CodeProjection, operation, record.kind, record.sourcePath, "", "", "canonical-public-value")
	}
	expr, err := parser.ParseExpr("canonical.cue", carrier)
	if err != nil {
		return nil, codecError(CodeProjection, operation, record.kind, record.sourcePath, "", "", "canonical-ast")
	}
	formatted, err := format.Node(expr)
	if err != nil {
		return nil, codecError(CodeProjection, operation, record.kind, record.sourcePath, "", "", "cue-format")
	}
	formatted = normalizeCanonicalText(formatted)
	if !utf8.Valid(formatted) {
		return nil, codecError(CodeProjection, operation, record.kind, record.sourcePath, "", "", "utf8")
	}
	if len(formatted) > c.limits.MaxBytes {
		return nil, codecError(CodeStructuralLimit, operation, record.kind, record.sourcePath, "", "", "max-bytes")
	}
	return formatted, nil
}

func normalizeCanonicalText(src []byte) []byte {
	src = bytes.ReplaceAll(src, []byte("\r\n"), []byte("\n"))
	src = bytes.ReplaceAll(src, []byte("\r"), []byte("\n"))
	src = bytes.TrimSuffix(src, []byte("\n"))
	return append(src, '\n')
}

// canonicalCarrierJSON never decodes a CUE number through float64. Go's JSON
// encoder byte-sorts string map keys, while json.Number retains exact number
// spelling and arrays retain source order.
func canonicalCarrierJSON(value interface{ MarshalJSON() ([]byte, error) }) ([]byte, error) {
	raw, err := value.MarshalJSON()
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var public any
	if err := decoder.Decode(&public); err != nil {
		return nil, err
	}
	if err := requireJSONEOF(decoder); err != nil {
		return nil, err
	}
	return json.Marshal(public)
}

func requireJSONEOF(decoder *json.Decoder) error {
	var trailing any
	if err := decoder.Decode(&trailing); err == nil {
		return errors.New("more than one JSON value")
	} else if err != io.EOF {
		return err
	}
	return nil
}

func jsonEqual(a, b []byte) bool {
	left, err := strictjson.Parse(a)
	if err != nil {
		return false
	}
	right, err := strictjson.Parse(b)
	return err == nil && strictjson.Equal(left, right)
}

func bytesEqual(a, b []byte) bool { return bytes.Equal(a, b) }
