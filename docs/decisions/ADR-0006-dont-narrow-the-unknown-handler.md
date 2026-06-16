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
   - **Field-narrowing (a bet on the coupling model — adopt only on proof):**
     - *Dependency-cone scoping* — review changed nodes ∪ their import/importer cone as
       whole modules.
     - *Content-addressed cache-skip* — skip Linon on a region whose cone is unchanged.

     These are adopted **only** with measured evidence that they do not drop catches —
     including bugs deliberately seeded through **non-import** coupling (config, runtime,
     implicit contract, shared state) — and are revisited humbly, because a passing
     bug-set may not represent the real unknowns.

3. **Epistemic discipline.** Do not assert that a region is irrelevant to a review without
   measuring it. Uncertainty is the default; measure before narrowing the safety net.

## Consequences

- **Build∥review pipelining is implemented** (the field-preserving lever) and measured
  against the serial baseline (serial role build ≈ 4075s). The reclaim is **less than
  Linon's 30% share**, because the final stage's review has nothing to hide under (an
  untouchable tail) and because two concurrent carriers contend on the same API rate limit
  (the coordination tax — real parallelism < theoretical). The exact A/B number is recorded
  with the run.
- **Cone scoping and cache-skip are deferred** behind a seeded-bug recall measurement that
  must include non-graph coupling. They are documented here as candidates, not decisions.
- **Statelessness is kept, deliberately.** Linon carries no memory between turns and may
  re-raise a finding every review — for a safety net, re-raising a still-true finding is a
  feature, and suppression is how false negatives are manufactured. Any "don't re-litigate"
  dedup lives in the deterministic controller (which already independently confirms findings
  under NN1), never inside Linon. Statelessness is also what makes the field-narrowing
  levers cacheable **if** they are ever proven safe.
- **This extends ADR-0005.** There we refused to dumb the *implementer* to buy speed; here
  we refuse to narrow the *verifier*'s field on assumption. Same rule from both ends: do not
  reduce an agent's capacity or field of view to save time — buy speed structurally, where
  it costs no quality, and let measurement (not a confident claim) gate anything that might.
