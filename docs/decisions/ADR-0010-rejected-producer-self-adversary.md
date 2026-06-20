# ADR-0010: Rejected — the producer self-adversary (implementer self-review before hand-off)

## Status

**Rejected (2026-06-21).** Proposed, built as a shadow-mode measurement, measured on a live run, and rejected
on the evidence. The measurement instrument is removed together with this decision; this record preserves the
reasoning so the idea is not silently re-proposed.

## Context

~55% of work units fail first review (ADR-0009). A Linon finding triggers a **full-wave** repair:
`_repair_roles_for` re-runs the designers + aufheben, not the implementer alone, because a Linon finding is
semantic and "may need a re-design." Many such findings are nonetheless impl-level — a defect the implementer
could fix itself, in a file it was allowed to change.

The proposal (call it **A**): before the implementer hands off, run the **same adversary the reviewer uses**
(`codex review`) on the implementer's own diff and let it self-fix the in-scope findings. The intended effect
was to convert impl-level Linon rejections — which currently cost a full wave — into a cheap implementer-only
self-fix, raising the first-pass rate **without anchoring Linon** (Linon stays the unanchored independent
adversary, ADR/README; the producer simply clears the bar more often). This is the "improve the producer so it
satisfies the independent reviewer" axis, the implementer-side counterpart to the designer-side deliverable-kind
declaration requirement (ADR-0009 pre-flight).

A was built **shadow-first**: a measurement (`PRODUCER_SELF_REVIEW=shadow`) that runs `codex review` on the
implementer's diff and classifies findings — `preemptable_in_scope` (P1/P2 in an allowed file = self-fixable)
vs `out_of_scope` (a design problem, deliberately left for Linon) — and streams `would_preempt_full_wave`,
**without** self-fixing. Promotion to actual self-fix (`block`) was gated on the measurement.

## Decision: rejected

The first live measurement (a CLI build goal, `PRODUCER_SELF_REVIEW=shadow`) falsified the core hypothesis:

- the self-review flagged `preemptable_in_scope = 1`, `would_preempt_full_wave = True`;
- but the run **converged on the first try** — **0** repair iterations, Linon passed the initial build.

So on this goal the "pre-emptable full wave" **did not exist** — Linon did not reject. Had A been in `block`
mode, the implementer would have self-fixed a finding the actual reviewer did not require: **wasted work, not a
saved wave** (gross opportunity 1, net pre-emption 0).

The root cause is structural, not a tuning gap:

1. `codex review` as a self-adversary is **stricter than Linon's actual rejection bar** — it surfaces P2
   nitpicks Linon does not act on.
2. In the default configuration Linon is a free-reading carrier — a **different reviewer** — so a self-review
   finding does not reliably correspond to a Linon rejection.

Therefore `preemptable_in_scope` **over-counts** A's value: "a finding the self-review rates in-scope and
high-priority" is not a reliable predictor of "a finding Linon would have rejected the build over." A's
realised benefit (pre-empted full waves) is bounded by the *intersection* of self-review findings and actual
Linon rejections — which the first data point put at zero — while its cost (an extra `codex review` per leaf,
plus self-fix churn on findings the reviewer would have waved through) is paid on **every** leaf.

This is precisely the outcome shadow-first exists to produce: the probe falsified the idea cheaply, before the
expensive self-fix path was built (ADR-0005, settledness — spend only where it is shown to pay).

## Consequence

- The producer self-adversary is **not pursued**; the shadow-measurement instrument is **removed** (an
  off-by-default dead path is not retained — it reads as a feature and accretes).
- The first-pass-rate investment goes to the **designer-side structural obligations** instead: make the
  contract harder to under-specify (the deliverable-kind declaration requirement and the rest of the ADR-0009
  pre-flight), which improves *what the producer hands off* without a second reviewer pass and without a
  false-positive churn cost.
- If revisited, A must be justified on **net** pre-emption — self-review findings that *intersect actual Linon
  rejections* across many runs — not on `gross` in-scope findings. A single positive `would_preempt_full_wave`
  is not evidence; the first live data point was a false positive.

## Grounding

- Live measurement, one CLI build goal, `PRODUCER_SELF_REVIEW=shadow`: `preemptable_in_scope = 1`,
  `would_preempt_full_wave = True`, yet **0** repair iterations (Linon clean on the first pass) — gross 1 /
  net 0.
- `_repair_roles_for` (`scripts/controller_pipeline.py`): a Linon finding keeps the full role set; only the
  deterministic gates (conformance / fuzz / secret) route implementer-only — the full-wave cost A aimed to
  pre-empt.
- The reviewer-independence principle this protected is recorded in the README ("the review half is an
  unanchored adversary"): A was attractive precisely because it raised first-pass rate without touching Linon;
  it is rejected on cost/benefit, not on principle.
