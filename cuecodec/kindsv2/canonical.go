package kindsv2

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strconv"
	"unicode/utf8"

	"cuelang.org/go/cue"
	"cuelang.org/go/cue/format"
	"cuelang.org/go/cue/parser"
	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

func encodeBase64(src []byte) string { return base64.StdEncoding.EncodeToString(src) }

// encodeStrictJSON byte-sorts object names, retains list order, and emits the
// original validated numeric lexeme without a float conversion.
func encodeStrictJSON(node *strictjson.Node) ([]byte, error) {
	var out bytes.Buffer
	if err := writeStrictJSON(&out, node); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func writeStrictJSON(out *bytes.Buffer, node *strictjson.Node) error {
	if node == nil {
		return fmt.Errorf("nil JSON node")
	}
	switch node.Kind {
	case strictjson.Null:
		out.WriteString("null")
	case strictjson.Bool:
		out.WriteString(strconv.FormatBool(node.BoolValue))
	case strictjson.Number:
		out.WriteString(node.NumberText)
	case strictjson.String:
		encoded, _ := json.Marshal(node.Text)
		out.Write(encoded)
	case strictjson.Array:
		out.WriteByte('[')
		for i, child := range node.Elements {
			if i > 0 {
				out.WriteByte(',')
			}
			if err := writeStrictJSON(out, child); err != nil {
				return err
			}
		}
		out.WriteByte(']')
	case strictjson.Object:
		out.WriteByte('{')
		for i, member := range node.SortedMembers() {
			if i > 0 {
				out.WriteByte(',')
			}
			name, _ := json.Marshal(member.Name)
			out.Write(name)
			out.WriteByte(':')
			if err := writeStrictJSON(out, member.Value); err != nil {
				return err
			}
		}
		out.WriteByte('}')
	default:
		return fmt.Errorf("unknown JSON kind")
	}
	return nil
}

func finalCUEText(src []byte) ([]byte, error) {
	expr, err := parser.ParseExpr("canonical.cue", src)
	if err != nil {
		return nil, err
	}
	formatted, err := format.Node(expr)
	if err != nil {
		return nil, err
	}
	formatted = bytes.ReplaceAll(formatted, []byte("\r\n"), []byte("\n"))
	formatted = bytes.ReplaceAll(formatted, []byte("\r"), []byte("\n"))
	formatted = bytes.TrimRight(formatted, "\n")
	formatted = append(formatted, '\n')
	if !utf8.Valid(formatted) {
		return nil, fmt.Errorf("invalid UTF-8")
	}
	return formatted, nil
}

func cueValueExceedsDepth(value cue.Value, depth, maximum int) bool {
	if depth > maximum {
		return true
	}
	if fields, err := value.Fields(cue.Definitions(false), cue.Hidden(false), cue.Optional(false)); err == nil {
		for fields.Next() {
			if cueValueExceedsDepth(fields.Value(), depth+1, maximum) {
				return true
			}
		}
	}
	if list, err := value.List(); err == nil {
		for list.Next() {
			if cueValueExceedsDepth(list.Value(), depth+1, maximum) {
				return true
			}
		}
	}
	return false
}
