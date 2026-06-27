# ADR-0006: Don't narrow the unknown-handler — Linon scope and the epistemics of review speedup

## Status

Accepted. (Owner-initiated design principle. The specific scope-narrowing levers below are
gated on measurement and are NOT yet adopted.)

## Context

We measured where an org run's LLM time actually goes, at **role** granularity rather than
stage granularity. Per a full six-stage arcade build (designer → implementer → linon per
stage):

| role | share | avg/turn |
| --- | --- | --- |
| implementer (build) | 45% | 308s |
| linon (adversarial review) | 30% | 206s |
| conservative-designer | 24% | 165s |

The headline correction: **the implementer is under half** of role time. Earlier
implementer-only measurements (direct carrier prompts, no designer/linon in the loop)
missed more than half the org's real cost. Review and design together are the majority.

That puts Linon's 30% in scope for speedup. The reflex is to make Linon **read less**:
review only the diff, or only the changed files plus their static-import dependency cone,
or cache-skip a region whose cone is unchanged. Each of these narrows what Linon looks at.

But Linon is the **adversarial verifier** — the role whose entire purpose is to catch what
was **not anticipated** (NN1–NN4 plus red-test calibration). Narrowing its field collides
with that purpose:

- **Scoping to a coupling model assumes the model is complete.** A static import graph does
  not capture runtime coupling, config, implicit contracts, global/shared state, or data
  dependencies. Linon exists precisely because our models of "what affects what" are
  incomplete. Cone-scoping bets on the very completeness Linon is there to challenge.
- **Diff-anchoring is worse than file-scoping.** A line-level diff is a context-free unit;
  anchoring review to changed lines narrows attention away from how the change interacts
  with **unchanged** code in the same module and neighbourhood — often exactly where the
  defect is. If a graph scope is ever used, the unit is **whole modules** (changed nodes ∪
  their dependency cone), never a diff. The diff adds nothing the cone does not already
  contain.
- **We do not actually know the broad read is dispensable.** Whether the wide scan is
  load-bearing differs by repository (tightly- vs loosely-coupled) and by change. Asserting
  "the diff is meaningless" or "the cone suffices" is an **unmeasured claim**. The accurate
  state is uncertainty.

## Decision

1. **Linon defaults to a wide scope.** It is the unknown-handler; treat narrowing as a
   capability reduction, not a free optimisation.

2. **Classify every review speedup by whether it narrows Linon's epistemic field.**

   - **Field-preserving (safe — no coverage loss; spend here first):**
     - *Pipeline overlap* — run Linon(N) concurrently with build(N+1), read-only, on a git
       worktree pinned to stage N's commit. Wall-clock only; Linon still sees everything.
     - *Internal lens-parallelism* — run NN1–NN4 (and red-test calibration) as concurrent
       sub-reviews and merge.
     - *Faster inference* — same field, subject to a separate quality check on the model.
   - **Field-narrowing (a bet on the coupling model):**
     - *Dependency-cone scoping* — review changed nodes ∪ their import/importer cone as
       whole modules. **REJECTED by measurement** (see Consequences): unenforceable under
       the current carrier sandbox, against Linon's grain, and evidence-lossy.
     - *Content-addressed cache-skip* — skip Linon on a region whose cone is unchanged.
       Still gated; weakened by the same "the broad scan is load-bearing" evidence.

     A field-narrowing lever is adopted **only** with measured evidence that it does not drop
     catches — including bugs deliberately seeded through **non-import** coupling (config,
     runtime, implicit contract, shared state) — and is revisited humbly, because a passing
     bug-set may not represent the real unknowns.

3. **Epistemic discipline.** Do not assert that a region is irrelevant to a review without
   measuring it. Uncertainty is the default; measure before narrowing the safety net.

## Consequences

- **Build∥review pipelining is implemented** (the field-preserving lever) and measured
  against the serial baseline. Six-stage A/B: **serial 4074s → pipelined 3124s = 23.3%
  reclaim (1.30×)**. Of Linon's 30% share, **77% was hidden under the next build**; the
  unreclaimed remainder is the final stage's review (an untouchable tail, nothing to hide
  under) plus contention between two concurrent carriers on the same API rate limit (the
  coordination tax — real parallelism < theoretical). The reclaim is real but below Linon's
  full share, which is the expected shape, not a disappointment. Evidence (raw data, n=1,
  harness external): `docs/evidence/role-timing-and-pipelining.md`.
- **Cone scoping was measured and REJECTED.** A seeded-bug experiment on a built 60-module
  repo ran Linon read-only over two bugs × two scopes (full repo vs import-cone, the cone
  pruned to whole modules only):
  - *G — in-graph control* (rename `ScoreEntry.points`→`value`, breaking a direct importer):
    caught at full **and** cone — the cone preserves recall when the coupling is in-graph.
  - *N — non-graph* (add an extra `meta` key to the signed-score payload; the only thing it
    violates is `score.schema.json`, `additionalProperties:false`, which is referenced by
    path and never imported, so it is outside every cone).
  Three findings, all against the cone:
  1. **Unenforceable.** The carrier's read-only sandbox does not confine reads to the `-C`
     repo — it reads the whole filesystem. Cone-Linon reconstructed the schema by reading a
     **sibling full checkout** (its own evidence_ref cited
     `/tmp/.../N-nongraph-full/schemas/score.schema.json`). Pruning a copy does not scope
     reads; only filesystem isolation (a box/microVM with just the cone mounted) would — a
     real cost just to *test*, let alone ship, the lever.
  2. **Against Linon's grain.** Linon scans the whole *pre-change* codebase by default: for
     a **one-line** diff it cited **~48 unchanged files** (`schemas/score.schema.json`,
     `docs/architecture.md`, `docs/README.md`, `tests/test_security.py`, sibling modules…).
     The broad scan is how it catches cross-cutting defects and how it grounds findings in
     real files — it is the unknown-handling, not overhead to trim.
  3. **Evidence-lossy.** Full-Linon *cited* the schema (`score.schema.json:41-71`) — a
     controller-confirmable fact. A truly-isolated cone-Linon could only *reconstruct/assert*
     the contract from prior knowledge — a weaker self-report. Narrowing the field trades
     verifiable evidence for guesswork.
  Net: the broad scan is a load-bearing part of *why* Linon is sharp. "Speed up Linon by
  reading less" would dumb the verifier — the ADR-0005 mistake, one role over. (web_search
  fired **0×** in all runs — these are repo-grounded bugs; the "pruning reduces web cost"
  hypothesis needs an external-fact bug to test and remains unmeasured.) Evidence (raw data, n=1 per cell, documented confound): `docs/evidence/cone-recall-experiment.md`.
- **Statelessness is kept, deliberately.** Linon carries no memory between turns and may
  re-raise a finding every review — for a safety net, re-raising a still-true finding is a
  feature, and suppression is how false negatives are manufactured. Any "don't re-litigate"
  dedup lives in the deterministic controller (which already independently confirms findings
  under NN1), never inside Linon. Statelessness is also what makes the field-narrowing
  levers cacheable **if** they are ever proven safe.
- **The A/B-aufheben cassette mechanism is additive under this ADR (it only ever *adds* field).**
  Cassettes are a JSON-defined priming library (`cassettes/cassettes.json`); each entry carries a
  `when` selector, a one-line `description`, a prime `body`, and a `paired_gate`. The mechanism runs two
  lanes, neither of which narrows anyone's field:
  - **LIVE (what the implementer builds on).** `implement_host.select_cassettes` is a *pure deterministic
    lookup* over the `when` fields — no model call, non-blocking. Selected `body` text is **appended** into
    the build-map prompt (`format_build_section`); an empty pick (`[]` = NONE) primes nothing. Like the rest
    of the build-map grounding, this is additive: it adds priming, it never narrows
    `files_allowed_to_change` and never gates the launch.
  - **SHADOW (the experiment, observed-only).** On implementer launch the host *fire-and-forgets* an
    aufheben-style query in parallel — aufheben is shown only the **track list** (names + descriptions) plus
    an explicit **NONE** option (progressive disclosure: bodies are never disclosed to the picker), and asked
    for 0–2 names. The result streams as a `cassette_shadow` event carrying both the `deterministic_pick` and
    the `aufheben_pick`. The launch **must not block on or fail from** the shadow: it runs on a daemon thread
    and every error is swallowed, so the experiment can be measured against the deterministic baseline without
    ever endangering the LIVE path. The real carrier query is opt-in (`AOB_CASSETTE_SHADOW=1`); when off, the
    event still streams the deterministic pick at zero cost. Same discipline as the rest of this ADR: buy the
    comparison structurally, off the critical path, and let measurement decide whether the picker beats the
    lookup — never narrow or gate the working path on the unproven lane.
- **This extends ADR-0005.** There we refused to dumb the *implementer* to buy speed; here
  we refuse to narrow the *verifier*'s field on assumption. Same rule from both ends: do not
  reduce an agent's capacity or field of view to save time — buy speed structurally, where
  it costs no quality, and let measurement (not a confident claim) gate anything that might.
