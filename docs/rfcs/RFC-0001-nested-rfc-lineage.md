# RFC-0001: Nested RFC lineage — form is invariant, process is plural

Status: RATIFIED (direction-ok requester 2026-07-03, via round-3 blind-judging verdict) — IMPLEMENTED
Implementation driver: the org's own promoted technical-approach (branch ai-org/rfc/nested-rfc-lineage-redesign, repo_native_manifest_nodes)
Requester: requester
Date: 2026-07-03

This document uses the org's own RFC field registry as its structure (dogfooding the
form). Sections map to `ai_org/rfc/field_registry.py` fields; org-internal sections
(technical approach, patch plan) follow the promote layout.

## raw_request

> RFCが子RFCを持つ構造を「ネスト」として再設計して
> （"Redesign RFC-holds-child-RFC as nesting."）

## working_title

Nested RFC lineage: every node is RFC-shaped; nesting is the truth-form; the
processes that write nodes are plural.

## problem_or_motivation

The ratified 2026-07-02 node model represented decomposition as child BRANCHES
linked by typed edges. Live experience and design review exposed three defects:

1. Sibling doc-node merges conflicted structurally (lived bug in the lineage smoke:
   fixed by "group tree merges implementations not doc nodes" — a symptom of doc
   nodes living on separate branches at all).
2. Children carried no principled inheritance: what a child may assume from its
   parent (stack, spine, budgets) was implicit, so child grounding could re-derive
   and diverge from parent decisions.
3. Decomposition quality had no falsifiable gate: "task lists" cannot be judged,
   and nothing forced a child to be independently intelligible.

The driving observation (requester): 「ドラクエをつくって」 and 「ドラクエみたいなバトル
システムをつくって」 are BOTH valid RFCs. A child has the FORM of a front-door
request while its EXISTENCE is inside the parent. Therefore the correct shape is
RFCs nested inside RFCs — and, critically, 「再帰はネストの形をとりますが、ネストは
再帰ではない」: fix the FORM as an invariant, free the PROCESS.

## intended_users_or_jobs

- The lineage machinery (author side of an RFC group) decomposing large requests.
- Child workers deepening assigned nodes.
- Review (objection anchoring), completeness checkpoints (#18), resolved() roll-up,
  delivery (root-only trigger) — all consumers that read the nest by its form.

## desired_outcomes_success

1. Every node satisfies RFC form, machine-checkable per node regardless of which
   process wrote it (formation / template-stamping / parent-retained).
2. A parent authors ONLY each child's outer form, and that outer form IS a
   front-door request (same shape as `ai_org/rfc/submit.py` inbox requests). The
   standalone-request test becomes the decomposition quality gate.
3. Child deepening happens on private branches scoped to the node's subtree,
   merged into the single RFC branch; sibling merges are conflict-free by path
   disjointness; an out-of-subtree diff is rejected deterministically.
4. Spine changes propagate by git alone: parent commit → child rebase/merge;
   acknowledgement = ancestry; staleness = merge-base. Zero new ledgers.
5. run15-class workloads (Dragon-Quest-scale) can stamp repetitive content nodes
   from template families × scale tables without grounding/11-step cost per node.

## affected_area_platform

`ai_org/rfc/lineage.py` (primary), `ai_org/rfc/__init__.py` pull routing,
minimal `receive.py` touchpoints, lineage tests. No product-repo impact.

## background_facts

Evidence trail (all reports under `~/aiorg_assets/`):

- `nest_crosscheck/REPORT.md` — adversarial cross-check: 1 FATAL / 7 WOUNDED /
  2 SURVIVES, demanding three amendments.
- `nest_research/REPORT.md` — neutral evidence on identity-vs-location,
  commissioning contracts, monorepo shared artifacts.
- `linux_mirror_research/REPORT.md` — the decisive check: all seven debated
  phenomena OCCUR in the Linux kernel community and are MANAGED, none SOLVED;
  the managed forms match this design (author-side decomposition via patch
  series; Change-Id explicitly rejected in favor of weak composable identity;
  rebase-and-resend as the staleness norm; MAINTAINERS path ownership;
  immutable topic branches for shared prerequisites; form filters as intake,
  signer responsibility for substance). Carved as Memento P1–P7 into
  `ai_org/rfc/lineage.py` (commit b7ad171); that Memento overrides this RFC
  where they conflict.
- Reference facets seeded from all three reports (canonical store, 514 terms).

Adjudication of the cross-check's three demanded amendments:
1. First-class node identity — REJECTED as machinery; ADOPTED as two-stage
   identity (nest position while forming; serial at direction-ok), per Linux P1.
2. Versioned contract amendments + acknowledgement — REJECTED as artifacts;
   git IS the amendment record (commit/ancestry/merge-base), per Linux P5 and
   the substrate-check rule (paper-world mechanisms compensate for missing merge
   machinery; importing them into git is the SAP failure mode).
3. Typed ownership beyond paths — ADOPTED in the whole-view form: children never
   edit shared paths because whole-affecting edits are structurally the parent's
   work; aggregates are generated from per-node manifests; spine changes are
   parent-authored immutable commits children merge, per Linux P3.

## tech_stack

Unchanged (Python + git; codex --output-schema safe subset for any schema).

## constraints_assumptions

- Form invariant: field registry validates every node; generators are unconstrained.
- Parent = the child's requester. The parent-authored artifact is a request
  (existing inbox shape). No new artifact kinds (no contract.json).
- Write scope = subtree = directory, enforced at group merge by diff-path
  inspection; fail-closed with offending paths named.
- Mutability boundary (Linux P5): private child branches rebase freely; RFC-branch
  history once merged is public and never rewritten.
- Identity two-stage (Linux P1): path while forming; serial sub-numbers
  (0042-1, 0042-1-1) at direction-ok; edges and anchors reference serials.
- Coverage ledger (100% rule) survives as a per-node, parent-owned artifact.
- Rolling wave survives: requested children exist as request.json-only until
  elaboration.
- Escalation (synthetic review round) and rebaseline survive with peer node addressing.
- Template-stamped nodes carry provenance (template id, scale-table row, stamping
  author signature); no semantic validator for stamped content (Linux P7).
- Dependency DAG stays metadata; containment does not imply ordering.

## non_goals_out_of_scope

Per lineage.py Memento P1–P7, lineage does NOT attempt to solve what Linux
manages by convention:

- No universal change-id registry (P1).
- No central planning system beyond author-side decomposition (P2).
- No mechanization of review bandwidth; that is the org-level differentiation
  target, not a lineage feature (P4).
- No amendment ledgers / acknowledgement protocols (P5).
- No mechanical arbiter for authority disputes; escalation stays social-shaped:
  child → parent → root (P6).
- No CI-grade semantic validator for stamped units (P7).
- Delivery/return path, review content deepening, and #18's fill machinery are
  separate work; this RFC only guarantees the nest gives them per-node hooks.

## references

- `ai_org/rfc/lineage.py` module Memento (P1–P7, commit b7ad171)
- `~/aiorg_assets/linux_mirror_research/REPORT.md` and its Sources table
  (Documentation/process/*, MAINTAINERS, b4/patchwork docs, upstream commits)
- `~/aiorg_assets/nest_crosscheck/REPORT.md`, `~/aiorg_assets/nest_research/REPORT.md`
- Reference facets: "weak composable change identity", "author-side decomposition
  patch series", "immutable topic branch for shared prerequisites", "form filter
  plus signer responsibility for stamped units", "bounded disposition loop for
  design review"
- Memory: substrate-check-imported-mechanisms (the SAP razor)

## Technical approach (org-internal)

### Node storage layout (on the single RFC branch)

```
rfc.json                     # root node, existing promote layout
technical-approach.json
domain-spec/
spine/                       # parent-owned shared bindings (root)
lineage-ledger.json          # per-node coverage ledger (parent-owned)
sub/<child_key>/
  request.json               # parent-authored outer form = front-door request shape
  rfc.json                   # child-authored: grounded against PARENT scope
  technical-approach.json    # child-authored
  domain-spec/               # child-authored
  lineage-ledger.json        # child's own ledger once it splits
```

The storage layout is not the dependency truth. Dependency truth is the declared
`edges` block in each node manifest; `part_of` records provenance, and nesting
views are projections.

### Processes (plural, per node)

- Formation: novel nodes; deliberating pipeline scoped to the node; grounding
  binds to the enclosing scope (root binds to the world — same mechanism, two
  instantiations).
- Stamping: `stamp_children(template_spec, scale_table)` writes sibling
  request.json outer forms with provenance; no per-node grounding.
- Retention: parent-retained integration nodes recorded in the ledger.

### Gates (deterministic, per node)

- Form gate: field registry validation of whichever artifacts the node has at its
  lifecycle stage (request-only for requested nodes).
- Write-scope gate at group merge: child diff ⊆ its subtree, else fail-closed.
- Spine-ancestry gate at group merge: required spine commits are ancestors of the
  child branch.
- Completeness checkpoints (#18) get per-node hooks (outer form well-formed;
  interior completeness by the request content and acceptance rows).

### What dies

- Child RFC branches for doc nodes (implementation branches remain).
- Any vestigial branch-per-child fields in schemas (removed cleanly, not kept).

## open_questions (non-blocking, recorded per bounded-disposition)

1. Does a stamped node ever need promotion to formation-grade (novelty discovered
   mid-deepening)? Presumed: child escalates; parent re-authors the request as a
   formation node. To be observed in run15+.
2. Serial assignment timing for children whose direction-ok is implied by the
   parent's (ratified: children skip direction review) — assign at first
   group-merge acceptance? To be settled at implementation with a Memento.
3. Whether `spine/` starts as convention (directory) or needs registry fields.
   Presumed: convention first; registry only if consumers need typed access.
