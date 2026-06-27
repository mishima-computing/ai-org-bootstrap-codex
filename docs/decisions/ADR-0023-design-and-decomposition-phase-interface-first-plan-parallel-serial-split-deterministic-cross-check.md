# ADR-0023: The design-and-decomposition phase — aufheben plans interfaces, the decomposer splits parallel/serial, a deterministic-first cross-check gates the split

## Status

Proposed. This is the design-time front half of the distributed-branch executor (the runtime half is
`docs/design-distributed-branch-executor.md`): that document says *how isolated task branches run and integrate*;
this ADR says *how the task graph is designed, split into parallel/serial work, and proven correct before any code
is written*. It applies ADR-0009's pre-implementation contract review (#1) to the **decomposition itself**.

## Context

The distributed-branch executor makes Git the parallelism boundary: one worktree/branch per task, isolated commits
as outputs, controller-owned integration via cherry-pick, and `depends_on` as the **sole representation** of the
parallel-vs-serial axis (no dep → independent/parallel off a shared base; dep → serial chain whose child branches
off the predecessor's output = a *stacked PR*). The `depends_on` machinery is already fail-closed: malformed,
unknown, non-sibling, duplicate, and cyclic edges are rejected, and multi-dependency bases are pre-integrated in
topological order.

What was unspecified: **who produces the task graph, how the parallel/serial split is decided, and how it is
verified — before work begins.** Today an LLM decomposer emits the split and bad splits surface only at
integration (a semantic merge conflict, an unsafe "parallel" pair that actually shares mutable state). Catching
them there is the most expensive place possible: the work is already done. The load-bearing observation:

- **Design's real job is to define the interfaces at the split boundaries.** Independent work is possible exactly
  where the interface between pieces is defined (Parnas information hiding; Conway / the inverse-Conway maneuver).
  A clean interface is what makes a parallel split *safe*.
- **Independence is largely a *deterministic* property, not an AI judgement.** Whether two "parallel" tasks
  actually collide is mostly decidable from their declared file/symbol scope sets and the produced-before-consumed
  ordering of interfaces. The AI should be asked only for the semantic residue the deterministic gates cannot
  decide.

## Decision

A **design-and-decomposition phase** runs before implementation, recursively at each composite level:

```
aufheben (案: interface-first design)
   → decomposer (split into PARALLEL + SERIAL tasks via depends_on)
      → cross-check (independent gate, BEFORE implementation)
```

Roles are **redefinable for this model** — they need not be the inherited conservative/aggressive/genius dialectic;
the design role here is *interface-first*, and the cross-checker is a dedicated *decomposition* reviewer.

### Two gates (not one)

1. **案-review (plan vs goal):** is the interface-first design itself the right way to meet the goal? Are the
   chosen interfaces the right abstractions, complete, and minimal (no over-broad contracts)?
2. **split-review (split vs plan):** does the split cover the whole 案 with no gaps or overlap (the WBS *100% rule*),
   encode the *real* dependencies, and assign parallel/serial correctly?

### Deterministic-first (the load-bearing rule)

Deterministic gates carry as much of the cross-check as possible; the AI judges only the **semantic residue**.

| Deterministic gate | What it proves |
|---|---|
| schema / topo validity, duplicate-id, unknown/non-sibling dep, cycle (already built) | the graph is well-formed; a malformed edge cannot silently turn serial into unsafe parallel |
| **coverage matrix** | every element of the 案 maps to ≥1 task (WBS 100% rule); no gaps |
| **scope-overlap matrix** | declared file/symbol scope sets of tasks marked *parallel* do **not** share mutable scope without a defined interface |
| **interface produced-before-consumed** | a consumer task depends on the task that produces the interface it needs |
| depth / task-count / PR-size caps | bounds over-decomposition |

The **AI semantic residue** is only: is this interface the right abstraction; and adversarial counterexample
search — "how could these two supposedly-parallel branches conflict?", "what implementation order would break this
plan?".

### Cross-check strength scaled by risk

- **Low-risk leaf split:** one critic + the deterministic gates.
- **High-risk** (composite boundary, shared API/contract change, data migration, build/test infrastructure, global
  registry): **two independent critics**, with **independent prompts** to avoid correlated blind spots. The
  acceptance rule is **"no unresolved objection," not majority vote**; a disagreement routes to repair, it does not
  get out-voted.

### Conservative fallback + bounded repair

- **Unverifiable independence → serial fallback.** If the gates cannot prove two tasks are independent, they are
  serialized (or the shared-interface change is isolated into its own predecessor task), never run in parallel on
  faith.
- Cross-check failure routes by cause: bad 案 → **aufheben**; bad split → **decomposer**; unprovable independence →
  **serialize**. The loop is **bounded**; exceeding the cap **fails closed** (it must not silently fall back to
  running the node as an unreviewed leaf).

### Evidence roll-up

Decomposition evidence (the gate results, the cross-check certificate) is stored in the composite's
`VerifiedCommit.evidence`. A parent composite **consumes its children's certificates** rather than re-deriving all
lower-level detail. Single-child and no-op integrations inherit child evidence; only a real integration of
independent work earns a fresh review of the net diff vs its base. (Same anti-redundancy discipline used for Linon
placement.)

### "PR-sized" is measurable, not prose

A terminal task (leaf) must satisfy explicit caps: max declared files, max public surfaces changed, max acceptance
profiles, a blast-radius score; and **a shared-contract change must be isolated into its own predecessor task**
(not smuggled into a "parallel" leaf).

## Pitfalls this design must guard against

- **Hidden shared mutable state** — config, generated files, DB migrations, global registries, shared tests,
  package exports, CI scripts. These are the "looks parallel but isn't" traps; the scope-overlap matrix must model
  them, not just source files.
- **Over-decomposition** — too many tiny branches multiply coordination and cherry-pick overhead; WBS guidance:
  terminal work packages must be estimable, measurable, and worth managing.
- **Contract drift** — over-broad/vague interfaces create hidden coupling; prefer narrow, consumer-driven contracts
  ("the whole provider schema" is usually too much contract).
- **LLM verifier overconfidence** — a single cross-checker can share the decomposer's blind spots; mitigate with
  independent prompts, explicit rubrics, and (above all) by making the deterministic gates load-bearing.
- **Semantic merge conflicts** — `git cherry-pick` detects only *syntactic* conflict. Two independently-correct
  branches can still break when merged; that residue is owned by the integration layer (integration tests + Linon
  review at the merge boundary), not by this design-time phase.

## Relationship to other decisions

- **ADR-0009** (the verification boundary; #1 pre-implementation contract review; the executable contract): this
  ADR *is* #1 applied to the decomposition. The interfaces the aufheben defines become the leaf-level executable
  contracts.
- **`docs/design-distributed-branch-executor.md`**: `depends_on` as the sole, fail-closed parallel/serial
  representation; serial chains as stacked PRs; integration owned by the controller.
- **ADR-0017** (rejected making the reviewer cheaper — the only safe lever is to run it *less*): the cross-check is
  *risk-scaled and deterministic-first*, never narrowed or prewarmed.
- **Linon at merge boundaries**: that is the *runtime* review of the integrated diff; this phase is the *design-time*
  review of the plan. They are complementary — a bad split is caught here (cheap, pre-work); a semantic conflict that
  only appears once code exists is caught there.

## Consequences

- Bad parallel/serial splits are caught **before** any implementation, where they are cheapest — not at integration.
- Most independence checking is **deterministic and auditable**, not an AI hunch; the AI is reserved for the
  genuine semantic residue.
- The system is **conservative by default**: when independence cannot be proven, it serializes.
- Cost is an added design-time gate, but it is **bounded, risk-scaled, and deterministic-first**, and it removes
  expensive post-work integration failures.

## Evidence (grounding, not illustration)

- **WBS 100% rule** — the decomposition must cover the whole scope, no more, no less (coverage matrix).
- **Conway's law / inverse-Conway; Parnas information hiding** — interfaces at boundaries are what enable safe
  parallel work; the split should follow interface boundaries.
- **Consumer-driven contracts** — narrow interfaces avoid hidden coupling / contract drift.
- **Planner–verifier / plan-critique** — separating planning from execution and reviewing the plan before
  executing is an established pattern; a single verifier can share blind spots, hence independent critics for
  high-risk boundaries.
- **Data-dependence analysis** (parallelizing compilers / build systems) — parallel-vs-serial is decided by
  scope/data dependence and validated on the dependency graph (cycles, completeness), exactly the deterministic
  gates above.
