// Package schemaguard implements the non-mutating structured-output-v1 JSON
// Schema admission guard. The accepted language is intentionally much smaller
// than Draft 2020-12; accepting a schema here means the paired offline
// validator can implement every admitted production without network access.
package schemaguard

import (
	"crypto/sha256"
	"fmt"
	"sort"
	"strings"

	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

// The guard enforces the public Structured Outputs safe-subset baseline only.
// A former AI Org-local 15,000-byte whole-schema cap (max_schema_bytes) was
// declared VOID by requester ruling (requester, round-0003 PORT-1); no byte-count
// rule may be reintroduced here or anywhere else in the rule table.
const (
	Draft202012URI     = "https://json-schema.org/draft/2020-12/schema"
	MaxTotalProperties = 100
	MaxDepth           = 5
)

// ErrorCode is the stable rule classification returned by Guard.
type ErrorCode string

const (
	CodeInvalidJSON          ErrorCode = "invalid_json"
	CodeDuplicateKey         ErrorCode = "duplicate_key"
	CodeRootObject           ErrorCode = "root_object"
	CodeDialect              ErrorCode = "draft_2020_12"
	CodeDefinitionName       ErrorCode = "definition_name"
	CodeDefinitionShape      ErrorCode = "definition_shape"
	CodeUnsupportedKeyword   ErrorCode = "unsupported_keyword"
	CodeRootOnlyKeyword      ErrorCode = "root_only_keyword"
	CodeDescription          ErrorCode = "description"
	CodeSchemaForm           ErrorCode = "schema_form"
	CodeType                 ErrorCode = "type"
	CodeProperties           ErrorCode = "properties"
	CodeRequired             ErrorCode = "required"
	CodeAdditionalProperties ErrorCode = "additional_properties"
	CodeItems                ErrorCode = "items"
	CodeEnum                 ErrorCode = "enum"
	CodeAnyOf                ErrorCode = "any_of"
	CodeNullPosition         ErrorCode = "null_position"
	CodeReference            ErrorCode = "reference"
	CodeReferenceTarget      ErrorCode = "reference_target"
	CodeReferenceCycle       ErrorCode = "reference_cycle"
	CodeUnreachableDef       ErrorCode = "unreachable_definition"
	CodePropertyLimit        ErrorCode = "property_limit"
	CodeDepthLimit           ErrorCode = "depth_limit"
)

// Error describes one deterministic structured-output-v1 rejection.
type Error struct {
	Code        ErrorCode
	JSONPointer string
	Detail      string
}

func (e *Error) Error() string {
	if e == nil {
		return "<nil>"
	}
	if e.JSONPointer == "" {
		return fmt.Sprintf("structured-output-v1: %s: %s", e.Code, e.Detail)
	}
	return fmt.Sprintf("structured-output-v1: %s at %s: %s", e.Code, e.JSONPointer, e.Detail)
}

// Admission holds the exact bytes admitted by Guard and their digest. Its byte
// slice is private and every accessor returns a copy, so callers cannot turn a
// guard result into a schema different from the one that was checked.
type Admission struct {
	bytes  []byte
	digest [sha256.Size]byte
}

// Bytes returns a byte-identical copy of the guarded input.
func (a Admission) Bytes() []byte { return append([]byte(nil), a.bytes...) }

// SHA256 returns the digest of the original guarded bytes.
func (a Admission) SHA256() [sha256.Size]byte { return a.digest }

// Guard checks schema against structured-output-v1 without changing schema.
// On failure it returns a zero Admission, never a partial result.
func Guard(schema []byte) (Admission, error) {
	original := append([]byte(nil), schema...)
	root, err := strictjson.Parse(original)
	if err != nil {
		if parseErr, ok := err.(*strictjson.Error); ok {
			code := CodeInvalidJSON
			if parseErr.Code == strictjson.DuplicateKey {
				code = CodeDuplicateKey
			}
			return Admission{}, reject(code, parseErr.Path, string(parseErr.Code))
		}
		return Admission{}, reject(CodeInvalidJSON, "", "invalid JSON")
	}
	diagnostics := collectSurfaceDiagnostics(root)
	diagnostics = append(diagnostics, collectGraphDiagnostics(root)...)
	diagnostics = append(diagnostics, collectContextDiagnostics(root)...)
	checker := &checker{
		root:    root,
		defs:    make(map[string]*strictjson.Node),
		reached: make(map[string]bool),
		memo:    make(map[memoKey]metrics),
	}
	if err := checker.prepare(); err != nil {
		diagnostics = append(diagnostics, asGuardError(err))
	} else {
		_, validationErr := checker.validateNode(root, "", true, false, make(map[string]bool))
		if validationErr != nil {
			diagnostics = append(diagnostics, asGuardError(validationErr))
		}
	}
	if selected := selectGuardError(diagnostics...); selected != nil {
		return Admission{}, selected
	}
	return Admission{bytes: original, digest: sha256.Sum256(original)}, nil
}

// collectSurfaceDiagnostics walks every syntactically schema-valued JSON
// position and records all local grammar violations whose applicability does
// not depend on reference reachability. It deliberately does not stop after a
// malformed sibling: the sole public selector, not check arrival, chooses the
// primary JSON-pointer/rule tuple.
func collectSurfaceDiagnostics(root *strictjson.Node) []*Error {
	collector := &surfaceCollector{defs: make(map[string]*strictjson.Node)}
	if root == nil || root.Kind != strictjson.Object {
		return []*Error{reject(CodeRootObject, "", "root schema must be an object")}
	}
	if dialect, ok := root.Lookup("$schema"); !ok || dialect.Kind != strictjson.String || dialect.Text != Draft202012URI {
		collector.add(CodeDialect, "/$schema", "root must declare the exact Draft 2020-12 URI")
	}
	if defs, ok := root.Lookup("$defs"); ok {
		if defs.Kind != strictjson.Object {
			collector.add(CodeDefinitionShape, "/$defs", "$defs must be an object")
		} else {
			for _, definition := range defs.SortedMembers() {
				definitionPath := strictjson.JoinPointer("/$defs", definition.Name)
				if !validDefinitionName(definition.Name) {
					collector.add(CodeDefinitionName, definitionPath, "definition name does not match [A-Za-z_][A-Za-z0-9_.-]*")
				}
				if definition.Value.Kind != strictjson.Object {
					collector.add(CodeDefinitionShape, definitionPath, "definition must be a schema object")
					continue
				}
				if validDefinitionName(definition.Name) {
					collector.defs[definition.Name] = definition.Value
				}
			}
		}
	}
	collector.walk(root, "", true, false)
	if defs, ok := root.Lookup("$defs"); ok && defs.Kind == strictjson.Object {
		for _, definition := range defs.SortedMembers() {
			if definition.Value.Kind == strictjson.Object {
				// A definition root may be a null schema when every path reaching
				// it originates in an anyOf branch. Reachability analysis owns that
				// contextual decision; descendants reset null permission normally.
				collector.walk(definition.Value, strictjson.JoinPointer("/$defs", definition.Name), false, true)
			}
		}
	}
	return collector.diagnostics
}

type surfaceCollector struct {
	defs        map[string]*strictjson.Node
	diagnostics []*Error
}

func (c *surfaceCollector) add(code ErrorCode, pointer, detail string) {
	c.diagnostics = append(c.diagnostics, reject(code, pointer, detail))
}

func (c *surfaceCollector) walk(node *strictjson.Node, pointer string, isRoot, allowNull bool) {
	if node == nil || node.Kind != strictjson.Object {
		c.add(CodeSchemaForm, pointer, "every schema node must be an object")
		return
	}
	for _, member := range node.SortedMembers() {
		memberPointer := strictjson.JoinPointer(pointer, member.Name)
		if !allowedKeyword(member.Name) {
			c.add(CodeUnsupportedKeyword, memberPointer, "keyword is not admitted by structured-output-v1")
			continue
		}
		if !isRoot && (member.Name == "$schema" || member.Name == "$defs") {
			c.add(CodeRootOnlyKeyword, memberPointer, "keyword is allowed only at the root")
		}
		if member.Name == "description" && member.Value.Kind != strictjson.String {
			c.add(CodeDescription, memberPointer, "description must be a string")
		}
	}

	ref, hasRef := node.Lookup("$ref")
	anyOf, hasAnyOf := node.Lookup("anyOf")
	typeNode, hasType := node.Lookup("type")
	if isRoot && (hasRef || hasAnyOf || !hasType || typeNode.Kind != strictjson.String || typeNode.Text != "object") {
		c.add(CodeRootObject, pointer, "root must use the typed object form")
	}

	if hasRef {
		if len(node.Members) != 1 {
			c.add(CodeSchemaForm, pointer, "$ref admits no sibling keywords")
		}
		if ref.Kind != strictjson.String || !strings.HasPrefix(ref.Text, "#/$defs/") {
			c.add(CodeReference, strictjson.JoinPointer(pointer, "$ref"), "$ref must be exactly #/$defs/<name>")
			return
		}
		name := strings.TrimPrefix(ref.Text, "#/$defs/")
		if !validDefinitionName(name) || ref.Text != "#/$defs/"+name {
			c.add(CodeReference, strictjson.JoinPointer(pointer, "$ref"), "$ref must contain one unescaped admitted definition name")
			return
		}
		if _, ok := c.defs[name]; !ok {
			c.add(CodeReferenceTarget, strictjson.JoinPointer(pointer, "$ref"), "referenced definition does not exist")
		}
		return
	}

	if hasAnyOf {
		if hasType || hasForeignFormKeyword(node, "anyOf") {
			c.add(CodeSchemaForm, pointer, "anyOf is mutually exclusive with other schema forms")
		}
		if anyOf.Kind != strictjson.Array || len(anyOf.Elements) < 2 {
			c.add(CodeAnyOf, strictjson.JoinPointer(pointer, "anyOf"), "anyOf must contain at least two schema objects")
			return
		}
		for i, branch := range anyOf.Elements {
			branchPath := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "anyOf"), fmt.Sprintf("%d", i))
			if branch.Kind != strictjson.Object {
				c.add(CodeAnyOf, branchPath, "anyOf branch must be a schema object")
				continue
			}
			if _, nested := branch.Lookup("anyOf"); nested {
				c.add(CodeAnyOf, branchPath, "anyOf branch cannot itself be a union")
			}
			c.walk(branch, branchPath, false, true)
		}
		return
	}

	if !hasType {
		if hasUnsupportedKeyword(node) {
			return
		}
		c.add(CodeSchemaForm, pointer, "schema must use exactly one of type, anyOf, or $ref")
		return
	}
	if typeNode.Kind != strictjson.String || !admittedType(typeNode.Text) {
		c.add(CodeType, strictjson.JoinPointer(pointer, "type"), "type must be one admitted string")
		return
	}

	switch typeNode.Text {
	case "object":
		allowed := []string{"type", "properties", "required", "additionalProperties"}
		if isRoot {
			allowed = append(allowed, "$schema", "$defs")
		}
		if hasForeignFormKeyword(node, allowed...) {
			c.add(CodeSchemaForm, pointer, "object schema has keywords from another form")
		}
		c.collectObject(node, pointer)
	case "array":
		if hasForeignFormKeyword(node, "type", "items") {
			c.add(CodeSchemaForm, pointer, "array schema has keywords from another form")
		}
		items, ok := node.Lookup("items")
		if !ok || items.Kind != strictjson.Object {
			c.add(CodeItems, strictjson.JoinPointer(pointer, "items"), "items must be a schema object")
		} else {
			c.walk(items, strictjson.JoinPointer(pointer, "items"), false, false)
		}
	case "string", "number", "integer", "boolean":
		if hasForeignFormKeyword(node, "type", "enum") {
			c.add(CodeSchemaForm, pointer, "scalar schema has keywords from another form")
		}
		c.collectEnum(node, pointer, typeNode.Text)
	case "null":
		if !allowNull {
			c.add(CodeNullPosition, pointer, "null is allowed only in an anyOf branch or its $ref chain")
		}
		if hasForeignFormKeyword(node, "type") {
			c.add(CodeSchemaForm, pointer, "null schema does not admit these siblings")
		}
	}
}

func hasUnsupportedKeyword(node *strictjson.Node) bool {
	if node == nil || node.Kind != strictjson.Object {
		return false
	}
	for _, member := range node.Members {
		if !allowedKeyword(member.Name) {
			return true
		}
	}
	return false
}

func (c *surfaceCollector) collectObject(node *strictjson.Node, pointer string) {
	properties, hasProperties := node.Lookup("properties")
	required, hasRequired := node.Lookup("required")
	additional, hasAdditional := node.Lookup("additionalProperties")
	if !hasProperties || properties.Kind != strictjson.Object {
		c.add(CodeProperties, strictjson.JoinPointer(pointer, "properties"), "object properties must be an object")
	}
	if !hasRequired || required.Kind != strictjson.Array {
		c.add(CodeRequired, strictjson.JoinPointer(pointer, "required"), "required must be an array")
	}
	if !hasAdditional || additional.Kind != strictjson.Bool || additional.BoolValue {
		c.add(CodeAdditionalProperties, strictjson.JoinPointer(pointer, "additionalProperties"), "additionalProperties must be literal false")
	}

	requiredSet := make(map[string]bool)
	if hasRequired && required.Kind == strictjson.Array {
		for i, item := range required.Elements {
			itemPath := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "required"), fmt.Sprintf("%d", i))
			if item.Kind != strictjson.String {
				c.add(CodeRequired, itemPath, "required entries must be strings")
				continue
			}
			if requiredSet[item.Text] {
				c.add(CodeRequired, itemPath, "required entries must be unique")
			}
			requiredSet[item.Text] = true
		}
	}
	if hasProperties && properties.Kind == strictjson.Object {
		for _, property := range properties.SortedMembers() {
			propertyPath := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "properties"), property.Name)
			if hasRequired && required.Kind == strictjson.Array && !requiredSet[property.Name] {
				c.add(CodeRequired, strictjson.JoinPointer(pointer, "required"), fmt.Sprintf("property %q is not required", property.Name))
			}
			if property.Value.Kind != strictjson.Object {
				c.add(CodeProperties, propertyPath, "property schema must be an object")
				continue
			}
			c.walk(property.Value, propertyPath, false, false)
		}
		if hasRequired && required.Kind == strictjson.Array {
			for name := range requiredSet {
				if _, ok := properties.Lookup(name); !ok {
					c.add(CodeRequired, strictjson.JoinPointer(pointer, "required"), fmt.Sprintf("required name %q is not a property", name))
				}
			}
		}
	}
}

func (c *surfaceCollector) collectEnum(node *strictjson.Node, pointer, typeName string) {
	enum, ok := node.Lookup("enum")
	if !ok {
		return
	}
	if enum.Kind != strictjson.Array || len(enum.Elements) == 0 {
		c.add(CodeEnum, strictjson.JoinPointer(pointer, "enum"), "enum must be a nonempty array")
		return
	}
	for i, candidate := range enum.Elements {
		candidatePath := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "enum"), fmt.Sprintf("%d", i))
		if !matchesScalarType(candidate, typeName) {
			c.add(CodeEnum, candidatePath, "enum value does not have the declared scalar type")
		}
		for j := 0; j < i; j++ {
			if strictjson.Equal(candidate, enum.Elements[j]) {
				c.add(CodeEnum, candidatePath, "enum values must be unique")
				break
			}
		}
	}
}

// hasForeignFormKeyword ignores annotations, root-only metadata, and unknown
// keywords because those have their own more specific rules. It reports only
// admitted schema-form keywords that conflict with the selected form.
func hasForeignFormKeyword(node *strictjson.Node, allowed ...string) bool {
	set := make(map[string]bool, len(allowed))
	for _, name := range allowed {
		set[name] = true
	}
	for _, member := range node.Members {
		if member.Name == "description" || member.Name == "$schema" || member.Name == "$defs" || !allowedKeyword(member.Name) {
			continue
		}
		if !set[member.Name] {
			return true
		}
	}
	return false
}

// collectGraphDiagnostics computes reference topology and limits independently
// from local-form validity. This makes property/depth/unreachable candidates
// available to the same final selector even when another node is malformed.
func collectGraphDiagnostics(root *strictjson.Node) []*Error {
	if root == nil || root.Kind != strictjson.Object {
		return nil
	}
	walker := &graphWalker{
		defs:    make(map[string]*strictjson.Node),
		reached: make(map[string]bool),
		memo:    make(map[*strictjson.Node]metrics),
	}
	if defs, ok := root.Lookup("$defs"); ok && defs.Kind == strictjson.Object {
		for _, definition := range defs.SortedMembers() {
			if validDefinitionName(definition.Name) && definition.Value.Kind == strictjson.Object {
				walker.defs[definition.Name] = definition.Value
			}
		}
	}
	m, acyclic := walker.walk(root, "", make(map[string]bool))
	if acyclic {
		if m.properties > MaxTotalProperties {
			walker.diagnostics = append(walker.diagnostics, reject(CodePropertyLimit, "", fmt.Sprintf("expanded property count %d exceeds %d", m.properties, MaxTotalProperties)))
		}
		if m.height > MaxDepth {
			walker.diagnostics = append(walker.diagnostics, reject(CodeDepthLimit, m.deepestPath, fmt.Sprintf("expanded depth %d exceeds %d", m.height, MaxDepth)))
		}
	}
	names := make([]string, 0, len(walker.defs))
	for name := range walker.defs {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if !walker.reached[name] {
			walker.diagnostics = append(walker.diagnostics, reject(CodeUnreachableDef, strictjson.JoinPointer("/$defs", name), "definition is not reachable from the root schema"))
		}
	}
	return walker.diagnostics
}

type graphWalker struct {
	defs        map[string]*strictjson.Node
	reached     map[string]bool
	memo        map[*strictjson.Node]metrics
	diagnostics []*Error
}

func (w *graphWalker) walk(node *strictjson.Node, pointer string, refStack map[string]bool) (metrics, bool) {
	if node == nil || node.Kind != strictjson.Object {
		return metrics{height: 1, deepestPath: pointer}, true
	}
	if cached, ok := w.memo[node]; ok {
		return cached, true
	}
	result := metrics{height: 1, deepestPath: pointer}
	if ref, ok := node.Lookup("$ref"); ok {
		if ref.Kind != strictjson.String || !strings.HasPrefix(ref.Text, "#/$defs/") {
			return result, true
		}
		name := strings.TrimPrefix(ref.Text, "#/$defs/")
		target, exists := w.defs[name]
		if !exists || ref.Text != "#/$defs/"+name || !validDefinitionName(name) {
			return result, true
		}
		w.reached[name] = true
		if refStack[name] {
			w.diagnostics = append(w.diagnostics, reject(CodeReferenceCycle, strictjson.JoinPointer(pointer, "$ref"), "reference cycle is not admitted"))
			return result, false
		}
		refStack[name] = true
		resolved, acyclic := w.walk(target, strictjson.JoinPointer("/$defs", name), refStack)
		delete(refStack, name)
		if acyclic {
			w.memo[node] = resolved
		}
		return resolved, acyclic
	}
	if anyOf, ok := node.Lookup("anyOf"); ok && anyOf.Kind == strictjson.Array {
		acyclic := true
		for i, branch := range anyOf.Elements {
			if branch.Kind != strictjson.Object {
				continue
			}
			branchPointer := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "anyOf"), fmt.Sprintf("%d", i))
			child, childAcyclic := w.walk(branch, branchPointer, refStack)
			acyclic = acyclic && childAcyclic
			result.properties = cappedAdd(result.properties, child.properties)
			mergeMetricHeight(&result, child, 0)
		}
		if acyclic {
			w.memo[node] = result
		}
		return result, acyclic
	}
	typeNode, hasType := node.Lookup("type")
	if !hasType || typeNode.Kind != strictjson.String {
		w.memo[node] = result
		return result, true
	}
	switch typeNode.Text {
	case "object":
		properties, ok := node.Lookup("properties")
		if !ok || properties.Kind != strictjson.Object {
			w.memo[node] = result
			return result, true
		}
		result.properties = len(properties.Members)
		acyclic := true
		for _, property := range properties.SortedMembers() {
			if property.Value.Kind != strictjson.Object {
				continue
			}
			propertyPointer := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "properties"), property.Name)
			child, childAcyclic := w.walk(property.Value, propertyPointer, refStack)
			acyclic = acyclic && childAcyclic
			result.properties = cappedAdd(result.properties, child.properties)
			mergeMetricHeight(&result, child, 1)
		}
		if acyclic {
			w.memo[node] = result
		}
		return result, acyclic
	case "array":
		items, ok := node.Lookup("items")
		if !ok || items.Kind != strictjson.Object {
			w.memo[node] = result
			return result, true
		}
		child, acyclic := w.walk(items, strictjson.JoinPointer(pointer, "items"), refStack)
		result.properties = child.properties
		mergeMetricHeight(&result, child, 1)
		if acyclic {
			w.memo[node] = result
		}
		return result, acyclic
	default:
		w.memo[node] = result
		return result, true
	}
}

func mergeMetricHeight(result *metrics, child metrics, edge int) {
	height := edge + child.height
	if height > result.height || height == result.height && child.deepestPath < result.deepestPath {
		result.height = height
		result.deepestPath = child.deepestPath
	}
}

// collectContextDiagnostics walks every reachable schema position while
// carrying the one piece of grammar state that cannot be decided from a
// definition in isolation: whether a null schema is reached from an anyOf
// branch through only reference edges. Object-property and array-item edges
// reset that permission, while reference chains preserve it.
func collectContextDiagnostics(root *strictjson.Node) []*Error {
	if root == nil || root.Kind != strictjson.Object {
		return nil
	}
	walker := &contextWalker{
		defs:    make(map[string]*strictjson.Node),
		visited: make(map[contextKey]bool),
	}
	if defs, ok := root.Lookup("$defs"); ok && defs.Kind == strictjson.Object {
		for _, definition := range defs.SortedMembers() {
			if validDefinitionName(definition.Name) && definition.Value.Kind == strictjson.Object {
				walker.defs[definition.Name] = definition.Value
			}
		}
	}
	walker.walk(root, "", false)
	return walker.diagnostics
}

type contextKey struct {
	node      *strictjson.Node
	allowNull bool
}

type contextWalker struct {
	defs        map[string]*strictjson.Node
	visited     map[contextKey]bool
	diagnostics []*Error
}

func (w *contextWalker) walk(node *strictjson.Node, pointer string, allowNull bool) {
	if node == nil || node.Kind != strictjson.Object {
		return
	}
	key := contextKey{node: node, allowNull: allowNull}
	if w.visited[key] {
		return
	}
	w.visited[key] = true

	if ref, ok := node.Lookup("$ref"); ok {
		if len(node.Members) != 1 || ref.Kind != strictjson.String || !strings.HasPrefix(ref.Text, "#/$defs/") {
			return
		}
		name := strings.TrimPrefix(ref.Text, "#/$defs/")
		if ref.Text != "#/$defs/"+name || !validDefinitionName(name) {
			return
		}
		if target, ok := w.defs[name]; ok {
			w.walk(target, strictjson.JoinPointer("/$defs", name), allowNull)
		}
		return
	}
	if anyOf, ok := node.Lookup("anyOf"); ok {
		if anyOf.Kind != strictjson.Array {
			return
		}
		nullBranches := 0
		for i, branch := range anyOf.Elements {
			if branch.Kind == strictjson.Object {
				branchPointer := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "anyOf"), fmt.Sprintf("%d", i))
				if resolved, ok := w.resolveBranch(branch); ok {
					if _, nested := resolved.Lookup("anyOf"); nested {
						w.diagnostics = append(w.diagnostics, reject(CodeAnyOf, branchPointer, "anyOf branch cannot itself resolve to a union"))
					}
					if typeNode, ok := resolved.Lookup("type"); ok && typeNode.Kind == strictjson.String && typeNode.Text == "null" {
						nullBranches++
						if nullBranches > 1 {
							w.diagnostics = append(w.diagnostics, reject(CodeAnyOf, branchPointer, "anyOf may contain at most one null-resolving branch"))
						}
					}
				}
				w.walk(branch, branchPointer, true)
			}
		}
		return
	}

	typeNode, ok := node.Lookup("type")
	if !ok || typeNode.Kind != strictjson.String {
		return
	}
	switch typeNode.Text {
	case "null":
		if !allowNull {
			w.diagnostics = append(w.diagnostics, reject(CodeNullPosition, pointer, "null is allowed only in an anyOf branch or its $ref chain"))
		}
	case "object":
		properties, ok := node.Lookup("properties")
		if !ok || properties.Kind != strictjson.Object {
			return
		}
		for _, property := range properties.SortedMembers() {
			if property.Value.Kind == strictjson.Object {
				propertyPointer := strictjson.JoinPointer(strictjson.JoinPointer(pointer, "properties"), property.Name)
				w.walk(property.Value, propertyPointer, false)
			}
		}
	case "array":
		if items, ok := node.Lookup("items"); ok && items.Kind == strictjson.Object {
			w.walk(items, strictjson.JoinPointer(pointer, "items"), false)
		}
	}
}

func (w *contextWalker) resolveBranch(branch *strictjson.Node) (*strictjson.Node, bool) {
	seen := make(map[string]bool)
	current := branch
	for current != nil {
		ref, hasRef := current.Lookup("$ref")
		if !hasRef {
			return current, true
		}
		if len(current.Members) != 1 || ref.Kind != strictjson.String || !strings.HasPrefix(ref.Text, "#/$defs/") {
			return nil, false
		}
		name := strings.TrimPrefix(ref.Text, "#/$defs/")
		if ref.Text != "#/$defs/"+name || !validDefinitionName(name) || seen[name] {
			return nil, false
		}
		seen[name] = true
		target, ok := w.defs[name]
		if !ok {
			return nil, false
		}
		current = target
	}
	return nil, false
}

func admittedType(name string) bool {
	switch name {
	case "object", "array", "string", "number", "integer", "boolean", "null":
		return true
	default:
		return false
	}
}

func asGuardError(err error) *Error {
	guardErr, _ := err.(*Error)
	return guardErr
}

func selectGuardError(candidates ...*Error) *Error {
	var selected *Error
	for _, candidate := range candidates {
		if candidate == nil {
			continue
		}
		if selected == nil || candidate.JSONPointer < selected.JSONPointer ||
			(candidate.JSONPointer == selected.JSONPointer && guardRuleRank(candidate.Code) < guardRuleRank(selected.Code)) ||
			(candidate.JSONPointer == selected.JSONPointer && guardRuleRank(candidate.Code) == guardRuleRank(selected.Code) && candidate.Detail < selected.Detail) {
			selected = candidate
		}
	}
	return selected
}

func guardRuleRank(code ErrorCode) int {
	order := [...]ErrorCode{
		CodeInvalidJSON, CodeDuplicateKey, CodeRootObject,
		CodeDialect, CodeDefinitionName, CodeDefinitionShape,
		CodeUnsupportedKeyword, CodeRootOnlyKeyword, CodeDescription,
		CodeSchemaForm, CodeType, CodeProperties, CodeRequired,
		CodeAdditionalProperties, CodeItems, CodeEnum, CodeAnyOf,
		CodeNullPosition, CodeReference, CodeReferenceTarget,
		CodeReferenceCycle, CodeUnreachableDef, CodePropertyLimit, CodeDepthLimit,
	}
	for i, candidate := range order {
		if code == candidate {
			return i + 1
		}
	}
	return len(order) + 1
}

// Check is the error-only form of Guard.
func Check(schema []byte) error {
	_, err := Guard(schema)
	return err
}

type checker struct {
	root    *strictjson.Node
	defs    map[string]*strictjson.Node
	reached map[string]bool
	memo    map[memoKey]metrics
}

type memoKey struct {
	node      *strictjson.Node
	root      bool
	allowNull bool
}

// metrics are occurrence-expanded. Reference and anyOf edges add no depth;
// object-property and array-item edges add one.
type metrics struct {
	properties  int
	height      int
	deepestPath string
}

func (c *checker) prepare() error {
	if c.root == nil || c.root.Kind != strictjson.Object {
		return reject(CodeRootObject, "", "root schema must be an object")
	}
	dialect, ok := c.root.Lookup("$schema")
	if !ok || dialect.Kind != strictjson.String || dialect.Text != Draft202012URI {
		return reject(CodeDialect, "/$schema", "root must declare the exact Draft 2020-12 URI")
	}
	defs, ok := c.root.Lookup("$defs")
	if !ok {
		return nil
	}
	if defs.Kind != strictjson.Object {
		return reject(CodeDefinitionShape, "/$defs", "$defs must be an object")
	}
	for _, member := range defs.SortedMembers() {
		path := strictjson.JoinPointer("/$defs", member.Name)
		if !validDefinitionName(member.Name) {
			return reject(CodeDefinitionName, path, "definition name does not match [A-Za-z_][A-Za-z0-9_.-]*")
		}
		if member.Value.Kind != strictjson.Object {
			return reject(CodeDefinitionShape, path, "definition must be a schema object")
		}
		c.defs[member.Name] = member.Value
	}
	return nil
}

func (c *checker) validateNode(node *strictjson.Node, path string, isRoot, allowNull bool, refStack map[string]bool) (metrics, error) {
	if node == nil || node.Kind != strictjson.Object {
		return metrics{}, reject(CodeSchemaForm, path, "every schema node must be an object")
	}
	key := memoKey{node: node, root: isRoot, allowNull: allowNull}
	if result, ok := c.memo[key]; ok {
		return result, nil
	}
	for _, member := range node.SortedMembers() {
		if !allowedKeyword(member.Name) {
			return metrics{}, reject(CodeUnsupportedKeyword, strictjson.JoinPointer(path, member.Name), "keyword is not admitted by structured-output-v1")
		}
		if !isRoot && (member.Name == "$schema" || member.Name == "$defs") {
			return metrics{}, reject(CodeRootOnlyKeyword, strictjson.JoinPointer(path, member.Name), "keyword is allowed only at the root")
		}
	}
	if description, ok := node.Lookup("description"); ok && description.Kind != strictjson.String {
		return metrics{}, reject(CodeDescription, strictjson.JoinPointer(path, "description"), "description must be a string")
	}
	_, hasRef := node.Lookup("$ref")
	_, hasAnyOf := node.Lookup("anyOf")
	_, hasType := node.Lookup("type")

	if isRoot {
		rootType, _ := node.Lookup("type")
		if hasRef || hasAnyOf || !hasType || rootType.Kind != strictjson.String || rootType.Text != "object" {
			return metrics{}, reject(CodeRootObject, path, "root must use the typed object form")
		}
	}
	if hasRef {
		if len(node.Members) != 1 {
			return metrics{}, reject(CodeSchemaForm, path, "$ref admits no sibling keywords")
		}
		result, err := c.validateReference(node, path, allowNull, refStack)
		if err == nil {
			c.memo[key] = result
		}
		return result, err
	}
	if hasAnyOf {
		if hasType || !onlyKeywords(node, "anyOf", "description") {
			return metrics{}, reject(CodeSchemaForm, path, "anyOf is mutually exclusive with other schema forms")
		}
		if isRoot {
			return metrics{}, reject(CodeAnyOf, path, "anyOf is not allowed at the root")
		}
		result, err := c.validateAnyOf(node, path, refStack)
		if err == nil {
			c.memo[key] = result
		}
		return result, err
	}
	if !hasType {
		return metrics{}, reject(CodeSchemaForm, path, "schema must use exactly one of type, anyOf, or $ref")
	}
	result, err := c.validateTyped(node, path, isRoot, allowNull, refStack)
	if err == nil {
		c.memo[key] = result
	}
	return result, err
}

func (c *checker) validateReference(node *strictjson.Node, path string, allowNull bool, refStack map[string]bool) (metrics, error) {
	ref, _ := node.Lookup("$ref")
	if ref.Kind != strictjson.String || !strings.HasPrefix(ref.Text, "#/$defs/") {
		return metrics{}, reject(CodeReference, strictjson.JoinPointer(path, "$ref"), "$ref must be exactly #/$defs/<name>")
	}
	name := strings.TrimPrefix(ref.Text, "#/$defs/")
	if !validDefinitionName(name) || ref.Text != "#/$defs/"+name {
		return metrics{}, reject(CodeReference, strictjson.JoinPointer(path, "$ref"), "$ref must contain one unescaped admitted definition name")
	}
	target, ok := c.defs[name]
	if !ok {
		return metrics{}, reject(CodeReferenceTarget, strictjson.JoinPointer(path, "$ref"), "referenced definition does not exist")
	}
	if refStack[name] {
		return metrics{}, reject(CodeReferenceCycle, strictjson.JoinPointer(path, "$ref"), "reference cycle is not admitted")
	}
	c.reached[name] = true
	refStack[name] = true
	// Null permission follows an acyclic reference chain only when that chain
	// starts in an anyOf branch. A reference reached from any other position
	// carries false and therefore cannot make a null definition admissible.
	result, err := c.validateNode(target, strictjson.JoinPointer("/$defs", name), false, allowNull, refStack)
	delete(refStack, name)
	return result, err
}

func (c *checker) validateAnyOf(node *strictjson.Node, path string, refStack map[string]bool) (metrics, error) {
	branches, _ := node.Lookup("anyOf")
	if branches.Kind != strictjson.Array || len(branches.Elements) < 2 {
		return metrics{}, reject(CodeAnyOf, strictjson.JoinPointer(path, "anyOf"), "anyOf must contain at least two schema objects")
	}
	result := metrics{height: 1, deepestPath: path}
	nullBranches := 0
	for i, branch := range branches.Elements {
		branchPath := strictjson.JoinPointer(strictjson.JoinPointer(path, "anyOf"), fmt.Sprintf("%d", i))
		if branch.Kind != strictjson.Object {
			return metrics{}, reject(CodeAnyOf, branchPath, "anyOf branch must be a schema object")
		}
		if resolved, ok := c.resolveBranch(branch); ok {
			_, nested := resolved.Lookup("anyOf")
			if nested {
				return metrics{}, reject(CodeAnyOf, branchPath, "anyOf branch cannot itself resolve to a union")
			}
		}
		m, err := c.validateNode(branch, branchPath, false, true, refStack)
		if err != nil {
			return metrics{}, err
		}
		if c.isNullBranch(branch) {
			nullBranches++
			if nullBranches > 1 {
				return metrics{}, reject(CodeAnyOf, branchPath, "anyOf may contain at most one null-resolving branch")
			}
		}
		result.properties = cappedAdd(result.properties, m.properties)
		if m.height > result.height || m.height == result.height && m.deepestPath < result.deepestPath {
			result.height, result.deepestPath = m.height, m.deepestPath
		}
	}
	return result, nil
}

func (c *checker) isNullBranch(branch *strictjson.Node) bool {
	resolved, ok := c.resolveBranch(branch)
	if !ok {
		return false
	}
	if typeNode, ok := resolved.Lookup("type"); ok {
		return typeNode.Kind == strictjson.String && typeNode.Text == "null"
	}
	return false
}

// resolveBranch follows an exact local-reference chain without evaluating it.
// Invalid and cyclic references are left to validateReference, whose rule has
// precedence over topology observations made by this helper.
func (c *checker) resolveBranch(branch *strictjson.Node) (*strictjson.Node, bool) {
	seen := make(map[string]bool)
	current := branch
	for current != nil {
		ref, hasRef := current.Lookup("$ref")
		if !hasRef {
			return current, true
		}
		if len(current.Members) != 1 || ref.Kind != strictjson.String || !strings.HasPrefix(ref.Text, "#/$defs/") {
			return nil, false
		}
		name := strings.TrimPrefix(ref.Text, "#/$defs/")
		if ref.Text != "#/$defs/"+name || !validDefinitionName(name) || seen[name] {
			return nil, false
		}
		seen[name] = true
		target, ok := c.defs[name]
		if !ok {
			return nil, false
		}
		current = target
	}
	return nil, false
}

func (c *checker) validateTyped(node *strictjson.Node, path string, isRoot, allowNull bool, refStack map[string]bool) (metrics, error) {
	typeNode, _ := node.Lookup("type")
	if typeNode.Kind != strictjson.String {
		return metrics{}, reject(CodeType, strictjson.JoinPointer(path, "type"), "type must be one admitted string")
	}
	switch typeNode.Text {
	case "object":
		allowed := []string{"type", "properties", "required", "additionalProperties", "description"}
		if isRoot {
			allowed = append(allowed, "$schema", "$defs")
		}
		if !onlyKeywords(node, allowed...) {
			return metrics{}, reject(CodeSchemaForm, path, "object schema has keywords from another form")
		}
		return c.validateObject(node, path, refStack)
	case "array":
		if isRoot || !onlyKeywords(node, "type", "items", "description") {
			return metrics{}, reject(CodeSchemaForm, path, "array schema has keywords from another form")
		}
		return c.validateArray(node, path, refStack)
	case "string", "number", "integer", "boolean":
		if isRoot || !onlyKeywords(node, "type", "enum", "description") {
			return metrics{}, reject(CodeSchemaForm, path, "scalar schema has keywords from another form")
		}
		if err := validateEnum(node, path, typeNode.Text); err != nil {
			return metrics{}, err
		}
		return metrics{height: 1, deepestPath: path}, nil
	case "null":
		if isRoot || !allowNull {
			return metrics{}, reject(CodeNullPosition, path, "null is allowed only in an anyOf branch or its direct $ref")
		}
		if !onlyKeywords(node, "type", "description") {
			return metrics{}, reject(CodeSchemaForm, path, "null schema does not admit these siblings")
		}
		return metrics{height: 1, deepestPath: path}, nil
	default:
		return metrics{}, reject(CodeType, strictjson.JoinPointer(path, "type"), "type is not admitted by structured-output-v1")
	}
}

func (c *checker) validateObject(node *strictjson.Node, path string, refStack map[string]bool) (metrics, error) {
	properties, ok := node.Lookup("properties")
	if !ok || properties.Kind != strictjson.Object {
		return metrics{}, reject(CodeProperties, strictjson.JoinPointer(path, "properties"), "object properties must be an object")
	}
	required, ok := node.Lookup("required")
	if !ok || required.Kind != strictjson.Array {
		return metrics{}, reject(CodeRequired, strictjson.JoinPointer(path, "required"), "required must be an array")
	}
	additional, ok := node.Lookup("additionalProperties")
	if !ok || additional.Kind != strictjson.Bool || additional.BoolValue {
		return metrics{}, reject(CodeAdditionalProperties, strictjson.JoinPointer(path, "additionalProperties"), "additionalProperties must be literal false")
	}
	requiredSet := make(map[string]bool, len(required.Elements))
	for i, item := range required.Elements {
		itemPath := strictjson.JoinPointer(strictjson.JoinPointer(path, "required"), fmt.Sprintf("%d", i))
		if item.Kind != strictjson.String {
			return metrics{}, reject(CodeRequired, itemPath, "required entries must be strings")
		}
		if requiredSet[item.Text] {
			return metrics{}, reject(CodeRequired, itemPath, "required entries must be unique")
		}
		requiredSet[item.Text] = true
	}
	propertyMembers := properties.SortedMembers()
	for _, property := range propertyMembers {
		if !requiredSet[property.Name] {
			return metrics{}, reject(CodeRequired, strictjson.JoinPointer(path, "required"), fmt.Sprintf("property %q is not required", property.Name))
		}
	}
	extraRequired := make([]string, 0)
	for name := range requiredSet {
		if _, ok := properties.Lookup(name); !ok {
			extraRequired = append(extraRequired, name)
		}
	}
	sort.Strings(extraRequired)
	if len(extraRequired) != 0 {
		return metrics{}, reject(CodeRequired, strictjson.JoinPointer(path, "required"), fmt.Sprintf("required name %q is not a property", extraRequired[0]))
	}
	result := metrics{properties: len(propertyMembers), height: 1, deepestPath: path}
	for _, property := range propertyMembers {
		propertyPath := strictjson.JoinPointer(strictjson.JoinPointer(path, "properties"), property.Name)
		if property.Value.Kind != strictjson.Object {
			return metrics{}, reject(CodeProperties, propertyPath, "property schema must be an object")
		}
		m, err := c.validateNode(property.Value, propertyPath, false, false, refStack)
		if err != nil {
			return metrics{}, err
		}
		result.properties = cappedAdd(result.properties, m.properties)
		if 1+m.height > result.height || 1+m.height == result.height && m.deepestPath < result.deepestPath {
			result.height, result.deepestPath = 1+m.height, m.deepestPath
		}
	}
	return result, nil
}

func (c *checker) validateArray(node *strictjson.Node, path string, refStack map[string]bool) (metrics, error) {
	items, ok := node.Lookup("items")
	if !ok || items.Kind != strictjson.Object {
		return metrics{}, reject(CodeItems, strictjson.JoinPointer(path, "items"), "items must be a schema object")
	}
	m, err := c.validateNode(items, strictjson.JoinPointer(path, "items"), false, false, refStack)
	if err != nil {
		return metrics{}, err
	}
	return metrics{properties: m.properties, height: 1 + m.height, deepestPath: m.deepestPath}, nil
}

func validateEnum(node *strictjson.Node, path, typeName string) error {
	enum, ok := node.Lookup("enum")
	if !ok {
		return nil
	}
	if enum.Kind != strictjson.Array || len(enum.Elements) == 0 {
		return reject(CodeEnum, strictjson.JoinPointer(path, "enum"), "enum must be a nonempty array")
	}
	for i, candidate := range enum.Elements {
		candidatePath := strictjson.JoinPointer(strictjson.JoinPointer(path, "enum"), fmt.Sprintf("%d", i))
		if !matchesScalarType(candidate, typeName) {
			return reject(CodeEnum, candidatePath, "enum value does not have the declared scalar type")
		}
		for j := 0; j < i; j++ {
			if strictjson.Equal(candidate, enum.Elements[j]) {
				return reject(CodeEnum, candidatePath, "enum values must be unique")
			}
		}
	}
	return nil
}

func matchesScalarType(node *strictjson.Node, typeName string) bool {
	switch typeName {
	case "string":
		return node.Kind == strictjson.String
	case "boolean":
		return node.Kind == strictjson.Bool
	case "number":
		return node.Kind == strictjson.Number
	case "integer":
		return node.Kind == strictjson.Number && strictjson.NumberIsInteger(node.NumberText)
	default:
		return false
	}
}

func allowedKeyword(name string) bool {
	switch name {
	case "$schema", "$defs", "$ref", "type", "properties", "required", "additionalProperties", "items", "enum", "anyOf", "description":
		return true
	default:
		return false
	}
}

func onlyKeywords(node *strictjson.Node, allowed ...string) bool {
	set := make(map[string]bool, len(allowed))
	for _, name := range allowed {
		set[name] = true
	}
	for _, member := range node.Members {
		if !set[member.Name] {
			return false
		}
	}
	return true
}

func validDefinitionName(name string) bool {
	if name == "" || !isASCIILetter(name[0]) && name[0] != '_' {
		return false
	}
	for i := 1; i < len(name); i++ {
		b := name[i]
		if !isASCIILetter(b) && (b < '0' || b > '9') && b != '_' && b != '.' && b != '-' {
			return false
		}
	}
	return true
}

func isASCIILetter(b byte) bool { return b >= 'A' && b <= 'Z' || b >= 'a' && b <= 'z' }

func cappedAdd(a, b int) int {
	if a > MaxTotalProperties || b > MaxTotalProperties || a > MaxTotalProperties-b {
		return MaxTotalProperties + 1
	}
	return a + b
}

func reject(code ErrorCode, pointer, detail string) *Error {
	return &Error{Code: code, JSONPointer: pointer, Detail: detail}
}
