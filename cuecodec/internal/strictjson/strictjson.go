// Package strictjson parses JSON without converting numbers to float64 and
// without accepting duplicate object member names. It is deliberately small:
// the schema guard and the paired offline validator use the same representation
// so that their views of an input cannot drift.
package strictjson

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Kind identifies the JSON production represented by a Node.
type Kind uint8

const (
	Null Kind = iota
	Bool
	Number
	String
	Array
	Object
)

// Member is one member of a JSON object. Members retain source order for
// diagnostics; callers that select failures must use SortedMembers.
type Member struct {
	Name  string
	Value *Node
}

// Node is a lossless-enough JSON value for validation. NumberText contains the
// original JSON number spelling and is never routed through float64.
type Node struct {
	Kind       Kind
	BoolValue  bool
	NumberText string
	Text       string
	Elements   []*Node
	Members    []Member
}

type decimalNumber struct {
	negative    bool
	coefficient string
	exponent    *big.Int
	zero        bool
}

// ErrorCode classifies strict JSON parsing failures.
type ErrorCode string

const (
	InvalidUTF8  ErrorCode = "invalid_utf8"
	InvalidJSON  ErrorCode = "invalid_json"
	DuplicateKey ErrorCode = "duplicate_key"
	TrailingData ErrorCode = "trailing_data"
	DepthLimit   ErrorCode = "depth_limit"
)

// Error is a deterministic strict-JSON diagnostic.
type Error struct {
	Code   ErrorCode
	Path   string
	Offset int64
	Err    error
}

func (e *Error) Error() string {
	if e == nil {
		return "<nil>"
	}
	if e.Path != "" {
		return fmt.Sprintf("strictjson: %s at %s", e.Code, e.Path)
	}
	return fmt.Sprintf("strictjson: %s at byte %d", e.Code, e.Offset)
}

func (e *Error) Unwrap() error { return e.Err }

// Parse reads exactly one JSON value. Unlike encoding/json.Unmarshal it
// rejects duplicate names and retains arbitrary-precision number text.
func Parse(src []byte) (*Node, error) {
	return parse(src, 0)
}

// ParseMaxDepth reads one JSON value while enforcing the depth budget before
// allocating a node at that depth. A root value has depth one. Parse remains
// available to callers whose boundary owns a different structural policy.
func ParseMaxDepth(src []byte, maxDepth int) (*Node, error) {
	if maxDepth < 1 {
		return nil, &Error{Code: DepthLimit}
	}
	return parse(src, maxDepth)
}

func parse(src []byte, maxDepth int) (*Node, error) {
	if !utf8.Valid(src) {
		return nil, &Error{Code: InvalidUTF8}
	}
	if maxDepth > 0 {
		if err := preflightMaxDepth(src, maxDepth); err != nil {
			return nil, err
		}
	}
	dec := json.NewDecoder(bytes.NewReader(src))
	dec.UseNumber()
	var duplicates []*Error
	n, err := parseValue(dec, "", 1, maxDepth, &duplicates)
	if err != nil {
		return nil, selectParseError(err, duplicates)
	}
	if _, err := dec.Token(); err != io.EOF {
		var trailing *Error
		if err == nil {
			trailing = &Error{Code: TrailingData, Offset: dec.InputOffset()}
		} else {
			trailing = &Error{Code: InvalidJSON, Offset: dec.InputOffset(), Err: err}
		}
		return nil, selectParseError(trailing, duplicates)
	}
	if duplicate := selectDuplicateError(duplicates); duplicate != nil {
		return nil, duplicate
	}
	return n, nil
}

type scanFrame struct {
	delim      json.Delim
	path       *scanPath
	memberPath *scanPath
	nextIndex  int
	seen       map[string]*scanPath
}

type scanPath struct {
	parent *scanPath
	token  string
	trie   *pointerTrieNode
}

type pointerTrieNode struct {
	edges           []pointerTrieEdge
	duplicate       bool
	duplicateOffset int64
}

type pointerTrieEdge struct {
	byteValue byte
	node      *pointerTrieNode
}

type duplicatePaths struct {
	root pointerTrieNode
}

// preflightMaxDepth consumes the complete token stream iteratively. It retains
// only active-container bookkeeping, never semantic Nodes, so a depth failure
// cannot hide an established duplicate-key or syntax failure that occurs later
// in the document. The caller still runs the guarded node builder after this
// scan succeeds, keeping the depth check at the recursive-allocation boundary.
func preflightMaxDepth(src []byte, maxDepth int) *Error {
	dec := json.NewDecoder(bytes.NewReader(src))
	dec.UseNumber()
	var (
		stack        []scanFrame
		duplicates   duplicatePaths
		depthError   *Error
		rootComplete bool
	)
	for !rootComplete {
		offset := dec.InputOffset()
		tok, err := dec.Token()
		if err != nil {
			return selectPreflightError(&Error{
				Code: InvalidJSON, Path: materializeScanPath(scanExpectedPath(stack)), Offset: dec.InputOffset(), Err: err,
			}, &duplicates)
		}
		if delim, ok := tok.(json.Delim); ok && (delim == '}' || delim == ']') {
			if len(stack) == 0 || !matchingDelimiters(stack[len(stack)-1].delim, delim) {
				return selectPreflightError(&Error{
					Code: InvalidJSON, Path: materializeScanPath(scanExpectedPath(stack)), Offset: dec.InputOffset(),
				}, &duplicates)
			}
			stack = stack[:len(stack)-1]
			rootComplete = len(stack) == 0
			continue
		}

		if len(stack) > 0 {
			frame := &stack[len(stack)-1]
			if frame.delim == '{' && frame.memberPath == nil {
				name, ok := tok.(string)
				if !ok {
					return selectPreflightError(&Error{
						Code: InvalidJSON, Path: materializeScanPath(frame.path), Offset: dec.InputOffset(),
					}, &duplicates)
				}
				memberPath, exists := frame.seen[name]
				if exists {
					duplicates.add(memberPath, dec.InputOffset())
				} else {
					memberPath = &scanPath{parent: frame.path, token: name}
					frame.seen[name] = memberPath
				}
				frame.memberPath = memberPath
				continue
			}
		}

		depth := len(stack) + 1
		var path *scanPath
		valueDelim, isDelim := tok.(json.Delim)
		if (depth > maxDepth && depthError == nil) || isDelim {
			path = scanValuePath(stack)
		}
		if depth > maxDepth && depthError == nil {
			depthError = &Error{Code: DepthLimit, Path: materializeScanPath(path), Offset: offset}
		}
		if len(stack) == 0 {
			rootComplete = true
		} else {
			frame := &stack[len(stack)-1]
			if frame.delim == '{' {
				frame.memberPath = nil
			} else {
				frame.nextIndex++
			}
		}
		if isDelim {
			if valueDelim != '{' && valueDelim != '[' {
				return selectPreflightError(&Error{
					Code: InvalidJSON, Path: materializeScanPath(path), Offset: dec.InputOffset(),
				}, &duplicates)
			}
			frame := scanFrame{delim: valueDelim, path: path}
			if valueDelim == '{' {
				frame.seen = make(map[string]*scanPath)
			}
			stack = append(stack, frame)
			rootComplete = false
		}
	}

	if _, err := dec.Token(); err != io.EOF {
		var trailing *Error
		if err == nil {
			trailing = &Error{Code: TrailingData, Offset: dec.InputOffset()}
		} else {
			trailing = &Error{Code: InvalidJSON, Offset: dec.InputOffset(), Err: err}
		}
		return selectPreflightError(trailing, &duplicates)
	}
	if duplicate := duplicates.selected(); duplicate != nil {
		return duplicate
	}
	return depthError
}

func scanValuePath(stack []scanFrame) *scanPath {
	if len(stack) == 0 {
		return nil
	}
	frame := stack[len(stack)-1]
	if frame.delim == '{' {
		return frame.memberPath
	}
	return &scanPath{parent: frame.path, token: strconv.Itoa(frame.nextIndex)}
}

func scanExpectedPath(stack []scanFrame) *scanPath {
	if len(stack) == 0 {
		return nil
	}
	frame := stack[len(stack)-1]
	if frame.delim == '{' {
		if frame.memberPath != nil {
			return frame.memberPath
		}
		return frame.path
	}
	return &scanPath{parent: frame.path, token: strconv.Itoa(frame.nextIndex)}
}

func materializeScanPath(path *scanPath) string {
	var reversed []*scanPath
	total := 0
	for current := path; current != nil; current = current.parent {
		reversed = append(reversed, current)
		total += 1 + escapedPointerTokenLength(current.token)
	}
	var result strings.Builder
	result.Grow(total)
	for index := len(reversed) - 1; index >= 0; index-- {
		result.WriteByte('/')
		writeEscapedPointerToken(&result, reversed[index].token)
	}
	return result.String()
}

func escapedPointerTokenLength(token string) int {
	length := len(token)
	for index := 0; index < len(token); index++ {
		if token[index] == '~' || token[index] == '/' {
			length++
		}
	}
	return length
}

func writeEscapedPointerToken(result *strings.Builder, token string) {
	for index := 0; index < len(token); index++ {
		switch token[index] {
		case '~':
			result.WriteString("~0")
		case '/':
			result.WriteString("~1")
		default:
			result.WriteByte(token[index])
		}
	}
}

func selectPreflightError(primary *Error, duplicates *duplicatePaths) *Error {
	duplicate := duplicates.selected()
	if duplicate == nil {
		return primary
	}
	return selectParseError(primary, []*Error{duplicate})
}

func (paths *duplicatePaths) add(path *scanPath, offset int64) {
	terminal := paths.ensure(path)
	if !terminal.duplicate || offset < terminal.duplicateOffset {
		terminal.duplicate = true
		terminal.duplicateOffset = offset
	}
}

func (paths *duplicatePaths) ensure(path *scanPath) *pointerTrieNode {
	var unresolved []*scanPath
	current := path
	for current != nil && current.trie == nil {
		unresolved = append(unresolved, current)
		current = current.parent
	}
	terminal := &paths.root
	if current != nil {
		terminal = current.trie
	}
	for index := len(unresolved) - 1; index >= 0; index-- {
		terminal = pointerTrieChild(terminal, '/')
		token := unresolved[index].token
		for tokenIndex := 0; tokenIndex < len(token); tokenIndex++ {
			switch token[tokenIndex] {
			case '~':
				terminal = pointerTrieChild(pointerTrieChild(terminal, '~'), '0')
			case '/':
				terminal = pointerTrieChild(pointerTrieChild(terminal, '~'), '1')
			default:
				terminal = pointerTrieChild(terminal, token[tokenIndex])
			}
		}
		unresolved[index].trie = terminal
	}
	return terminal
}

func pointerTrieChild(node *pointerTrieNode, byteValue byte) *pointerTrieNode {
	index := 0
	for index < len(node.edges) && node.edges[index].byteValue < byteValue {
		index++
	}
	if index < len(node.edges) && node.edges[index].byteValue == byteValue {
		return node.edges[index].node
	}
	child := &pointerTrieNode{}
	node.edges = append(node.edges, pointerTrieEdge{})
	copy(node.edges[index+1:], node.edges[index:])
	node.edges[index] = pointerTrieEdge{byteValue: byteValue, node: child}
	return child
}

func (paths *duplicatePaths) selected() *Error {
	node := &paths.root
	pointer := make([]byte, 0, 64)
	for {
		if node.duplicate {
			return &Error{Code: DuplicateKey, Path: string(pointer), Offset: node.duplicateOffset}
		}
		if len(node.edges) == 0 {
			return nil
		}
		edge := node.edges[0]
		pointer = append(pointer, edge.byteValue)
		node = edge.node
	}
}

func matchingDelimiters(open, close json.Delim) bool {
	return open == '{' && close == '}' || open == '[' && close == ']'
}

func parseValue(dec *json.Decoder, path string, depth, maxDepth int, duplicates *[]*Error) (*Node, error) {
	if maxDepth > 0 && depth > maxDepth {
		return nil, &Error{Code: DepthLimit, Path: path, Offset: dec.InputOffset()}
	}
	tok, err := dec.Token()
	if err != nil {
		return nil, &Error{Code: InvalidJSON, Path: path, Offset: dec.InputOffset(), Err: err}
	}
	switch tok := tok.(type) {
	case nil:
		return &Node{Kind: Null}, nil
	case bool:
		return &Node{Kind: Bool, BoolValue: tok}, nil
	case json.Number:
		return &Node{Kind: Number, NumberText: tok.String()}, nil
	case string:
		return &Node{Kind: String, Text: tok}, nil
	case json.Delim:
		switch tok {
		case '{':
			n := &Node{Kind: Object}
			seen := make(map[string]struct{})
			for dec.More() {
				nameToken, err := dec.Token()
				if err != nil {
					return nil, &Error{Code: InvalidJSON, Path: path, Offset: dec.InputOffset(), Err: err}
				}
				name, ok := nameToken.(string)
				if !ok {
					return nil, &Error{Code: InvalidJSON, Path: path, Offset: dec.InputOffset()}
				}
				memberPath := JoinPointer(path, name)
				if _, ok := seen[name]; ok {
					*duplicates = append(*duplicates, &Error{Code: DuplicateKey, Path: memberPath, Offset: dec.InputOffset()})
				}
				seen[name] = struct{}{}
				value, err := parseValue(dec, memberPath, depth+1, maxDepth, duplicates)
				if err != nil {
					return nil, err
				}
				n.Members = append(n.Members, Member{Name: name, Value: value})
			}
			end, err := dec.Token()
			if err != nil || end != json.Delim('}') {
				return nil, &Error{Code: InvalidJSON, Path: path, Offset: dec.InputOffset(), Err: err}
			}
			return n, nil
		case '[':
			n := &Node{Kind: Array}
			for i := 0; dec.More(); i++ {
				value, err := parseValue(dec, JoinPointer(path, fmt.Sprintf("%d", i)), depth+1, maxDepth, duplicates)
				if err != nil {
					return nil, err
				}
				n.Elements = append(n.Elements, value)
			}
			end, err := dec.Token()
			if err != nil || end != json.Delim(']') {
				return nil, &Error{Code: InvalidJSON, Path: path, Offset: dec.InputOffset(), Err: err}
			}
			return n, nil
		}
	}
	return nil, &Error{Code: InvalidJSON, Path: path, Offset: dec.InputOffset()}
}

func selectDuplicateError(candidates []*Error) *Error {
	var selected *Error
	for _, candidate := range candidates {
		if candidate == nil {
			continue
		}
		if selected == nil || candidate.Path < selected.Path || candidate.Path == selected.Path && candidate.Offset < selected.Offset {
			selected = candidate
		}
	}
	return selected
}

func selectParseError(primary error, duplicates []*Error) *Error {
	selected, ok := primary.(*Error)
	if !ok || selected == nil {
		selected = &Error{Code: InvalidJSON, Err: primary}
	}
	// ParseMaxDepth stops as soon as the next value would exceed the budget.
	// Preserve Parse's established duplicate-key precedence for duplicates that
	// were already observed before that stop; doing so needs no allocation of
	// the over-limit node. Other syntax failures retain path-based selection.
	if selected.Code == DepthLimit {
		if duplicate := selectDuplicateError(duplicates); duplicate != nil {
			return duplicate
		}
	}
	for _, candidate := range duplicates {
		if candidate == nil {
			continue
		}
		if candidate.Path < selected.Path || candidate.Path == selected.Path && parseErrorRank(candidate.Code) < parseErrorRank(selected.Code) ||
			candidate.Path == selected.Path && parseErrorRank(candidate.Code) == parseErrorRank(selected.Code) && candidate.Offset < selected.Offset {
			selected = candidate
		}
	}
	return selected
}

func parseErrorRank(code ErrorCode) int {
	if code == DuplicateKey {
		return 2
	}
	return 1
}

// Lookup returns an object member by name.
func (n *Node) Lookup(name string) (*Node, bool) {
	if n == nil || n.Kind != Object {
		return nil, false
	}
	for _, m := range n.Members {
		if m.Name == name {
			return m.Value, true
		}
	}
	return nil, false
}

// SortedMembers returns a copy ordered by member name's UTF-8 bytes.
func (n *Node) SortedMembers() []Member {
	if n == nil || n.Kind != Object {
		return nil
	}
	result := append([]Member(nil), n.Members...)
	sort.Slice(result, func(i, j int) bool { return result[i].Name < result[j].Name })
	return result
}

// JoinPointer appends one token to an RFC 6901 JSON pointer.
func JoinPointer(base, token string) string {
	token = strings.ReplaceAll(token, "~", "~0")
	token = strings.ReplaceAll(token, "/", "~1")
	return base + "/" + token
}

// Equal applies JSON equality with mathematical, arbitrary-precision number
// comparison. Object source order is immaterial.
func Equal(a, b *Node) bool {
	if a == nil || b == nil || a.Kind != b.Kind {
		return false
	}
	switch a.Kind {
	case Null:
		return true
	case Bool:
		return a.BoolValue == b.BoolValue
	case Number:
		x, okX := parseDecimalNumber(a.NumberText)
		y, okY := parseDecimalNumber(b.NumberText)
		if !okX || !okY {
			return false
		}
		if x.zero || y.zero {
			return x.zero && y.zero
		}
		return x.negative == y.negative && x.coefficient == y.coefficient && x.exponent.Cmp(y.exponent) == 0
	case String:
		return a.Text == b.Text
	case Array:
		if len(a.Elements) != len(b.Elements) {
			return false
		}
		for i := range a.Elements {
			if !Equal(a.Elements[i], b.Elements[i]) {
				return false
			}
		}
		return true
	case Object:
		if len(a.Members) != len(b.Members) {
			return false
		}
		am, bm := a.SortedMembers(), b.SortedMembers()
		for i := range am {
			if am[i].Name != bm[i].Name || !Equal(am[i].Value, bm[i].Value) {
				return false
			}
		}
		return true
	default:
		return false
	}
}

// NumberIsInteger reports whether a valid JSON number denotes a mathematical
// integer. It never expands the exponent and therefore remains bounded even
// for inputs such as 1e999999999999999999999.
func NumberIsInteger(number string) bool {
	normalized, ok := parseDecimalNumber(number)
	return ok && (normalized.zero || normalized.exponent.Sign() >= 0)
}

// parseDecimalNumber converts a finite JSON decimal to coefficient*10^exponent.
// Leading and trailing coefficient zeroes are removed; the exponent is a
// big.Int so its magnitude is bounded by source length rather than machine int.
func parseDecimalNumber(number string) (decimalNumber, bool) {
	result := decimalNumber{exponent: new(big.Int)}
	if number == "" {
		return decimalNumber{}, false
	}
	if number[0] == '-' {
		result.negative = true
		number = number[1:]
		if number == "" {
			return decimalNumber{}, false
		}
	}

	mantissa := number
	if index := strings.IndexAny(number, "eE"); index >= 0 {
		if strings.IndexAny(number[index+1:], "eE") >= 0 {
			return decimalNumber{}, false
		}
		mantissa = number[:index]
		exponentText := number[index+1:]
		if exponentText == "" {
			return decimalNumber{}, false
		}
		if _, ok := result.exponent.SetString(exponentText, 10); !ok {
			return decimalNumber{}, false
		}
	}

	integerPart, fractionPart := mantissa, ""
	if index := strings.IndexByte(mantissa, '.'); index >= 0 {
		if strings.IndexByte(mantissa[index+1:], '.') >= 0 {
			return decimalNumber{}, false
		}
		integerPart, fractionPart = mantissa[:index], mantissa[index+1:]
	}
	if integerPart == "" || strings.ContainsAny(integerPart+fractionPart, "+-") {
		return decimalNumber{}, false
	}
	digits := integerPart + fractionPart
	for _, digit := range digits {
		if digit < '0' || digit > '9' {
			return decimalNumber{}, false
		}
	}
	digits = strings.TrimLeft(digits, "0")
	if digits == "" {
		return decimalNumber{exponent: new(big.Int), zero: true}, true
	}
	trailing := len(digits) - len(strings.TrimRight(digits, "0"))
	digits = strings.TrimRight(digits, "0")
	result.coefficient = digits
	result.exponent.Sub(result.exponent, big.NewInt(int64(len(fractionPart))))
	result.exponent.Add(result.exponent, big.NewInt(int64(trailing)))
	return result, true
}
