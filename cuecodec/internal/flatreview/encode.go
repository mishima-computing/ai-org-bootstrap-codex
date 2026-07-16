// Package flatreview encodes output-only, deterministic review candidates.
//
// The package deliberately accepts only stable IDs and already-projected JSON
// carrier bytes. It has no repository, CUE, admission, or filesystem surface,
// so review output cannot become an authority-loading input through this
// dependency boundary.
package flatreview

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"unicode/utf8"

	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

const recordSeparator = byte(0x1e)

// Record is one already-admitted carrier selected for review. Carrier must be
// exactly one canonical, precision-preserving strict JSON value. Encode never
// mutates either field.
type Record struct {
	StableID string
	Carrier  []byte
}

type rowV1 struct {
	Pointer string          `json:"pointer"`
	Value   json.RawMessage `json:"value"`
}

// Field order is compatibility-bearing. It is fixed here rather than relying
// on map iteration: rows precede stable_id, and row fields are pointer/value.
type sectionV1 struct {
	Rows     []rowV1 `json:"rows"`
	StableID string  `json:"stable_id"`
}

// Encode emits FlatReviewV1 as an RFC 7464 JSON text sequence. Every section
// is independently encoded as RS + compact JSON + LF, sections are byte-sorted
// by stable ID, and no corpus-wide count, ordinal, offset, or digest is added.
// Consequently, changing one record cannot rewrite another record's section.
//
// Rows contain every scalar leaf and every empty container. Their pointers are
// RFC 6901 escaped and then byte-sorted. Number text is retained verbatim from
// the precision-preserving carrier and is never converted through float64.
func Encode(records []Record) ([]byte, error) {
	ordered := append([]Record(nil), records...)
	sort.Slice(ordered, func(i, j int) bool {
		return ordered[i].StableID < ordered[j].StableID
	})

	for i, record := range ordered {
		if record.StableID == "" || !utf8.ValidString(record.StableID) {
			return nil, fmt.Errorf("flat review: invalid stable ID")
		}
		if i > 0 && ordered[i-1].StableID == record.StableID {
			return nil, fmt.Errorf("flat review: duplicate stable ID")
		}
	}

	var candidate bytes.Buffer
	for _, record := range ordered {
		section, err := encodeSection(record)
		if err != nil {
			return nil, err
		}
		_, _ = candidate.Write(section)
	}
	return candidate.Bytes(), nil
}

func encodeSection(record Record) ([]byte, error) {
	root, err := strictjson.Parse(record.Carrier)
	if err != nil {
		return nil, fmt.Errorf("flat review: stable ID %q: carrier: %w", record.StableID, err)
	}
	// encoding/json accepts isolated UTF-16 surrogate escapes and substitutes
	// U+FFFD. That would make two distinct inputs collapse to the same review
	// value, so FlatReviewV1 rejects them instead.
	if !validUnicodeEscapes(record.Carrier) {
		return nil, fmt.Errorf("flat review: stable ID %q: carrier: unpaired Unicode surrogate", record.StableID)
	}
	rows, err := flattenRows(root, "", nil)
	if err != nil {
		return nil, fmt.Errorf("flat review: stable ID %q: %w", record.StableID, err)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Pointer < rows[j].Pointer })

	text, err := json.Marshal(sectionV1{Rows: rows, StableID: record.StableID})
	if err != nil {
		return nil, fmt.Errorf("flat review: stable ID %q: section: %w", record.StableID, err)
	}
	section := make([]byte, 0, len(text)+2)
	section = append(section, recordSeparator)
	section = append(section, text...)
	section = append(section, '\n')
	return section, nil
}

func validUnicodeEscapes(src []byte) bool {
	inString := false
	for i := 0; i < len(src); i++ {
		if !inString {
			if src[i] == '"' {
				inString = true
			}
			continue
		}
		if src[i] == '"' {
			inString = false
			continue
		}
		if src[i] != '\\' || i+1 >= len(src) {
			continue
		}
		if src[i+1] != 'u' {
			i++
			continue
		}
		code, ok := hexCodeUnit(src[i+2:])
		if !ok {
			// Strict JSON parsing, which runs first, owns malformed escapes.
			continue
		}
		switch {
		case code >= 0xd800 && code <= 0xdbff:
			if i+12 > len(src) || src[i+6] != '\\' || src[i+7] != 'u' {
				return false
			}
			low, ok := hexCodeUnit(src[i+8:])
			if !ok || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 11
		case code >= 0xdc00 && code <= 0xdfff:
			return false
		default:
			i += 5
		}
	}
	return true
}

func hexCodeUnit(src []byte) (uint16, bool) {
	if len(src) < 4 {
		return 0, false
	}
	var result uint16
	for _, digit := range src[:4] {
		result <<= 4
		switch {
		case digit >= '0' && digit <= '9':
			result |= uint16(digit - '0')
		case digit >= 'a' && digit <= 'f':
			result |= uint16(digit-'a') + 10
		case digit >= 'A' && digit <= 'F':
			result |= uint16(digit-'A') + 10
		default:
			return 0, false
		}
	}
	return result, true
}

func flattenRows(node *strictjson.Node, pointer string, rows []rowV1) ([]rowV1, error) {
	if node == nil {
		return nil, fmt.Errorf("nil value at %q", pointer)
	}
	switch node.Kind {
	case strictjson.Object:
		members := node.SortedMembers()
		if len(members) == 0 {
			return append(rows, rowV1{Pointer: pointer, Value: json.RawMessage(`{}`)}), nil
		}
		var err error
		for _, member := range members {
			rows, err = flattenRows(member.Value, strictjson.JoinPointer(pointer, member.Name), rows)
			if err != nil {
				return nil, err
			}
		}
		return rows, nil
	case strictjson.Array:
		if len(node.Elements) == 0 {
			return append(rows, rowV1{Pointer: pointer, Value: json.RawMessage(`[]`)}), nil
		}
		var err error
		for i, element := range node.Elements {
			rows, err = flattenRows(element, strictjson.JoinPointer(pointer, strconv.Itoa(i)), rows)
			if err != nil {
				return nil, err
			}
		}
		return rows, nil
	default:
		value, err := marshalLeaf(node)
		if err != nil {
			return nil, fmt.Errorf("value at %q: %w", pointer, err)
		}
		return append(rows, rowV1{Pointer: pointer, Value: value}), nil
	}
}

func marshalLeaf(node *strictjson.Node) (json.RawMessage, error) {
	switch node.Kind {
	case strictjson.Null:
		return json.RawMessage(`null`), nil
	case strictjson.Bool:
		if node.BoolValue {
			return json.RawMessage(`true`), nil
		}
		return json.RawMessage(`false`), nil
	case strictjson.Number:
		return json.RawMessage(node.NumberText), nil
	case strictjson.String:
		value, err := json.Marshal(node.Text)
		return json.RawMessage(value), err
	default:
		return nil, fmt.Errorf("non-leaf JSON kind %d", node.Kind)
	}
}
