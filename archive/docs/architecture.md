# Architecture

AI Org Bootstrap Codex has four layers:

1. Role contracts in `roles/`.
2. Codex adapters in `.codex/agents/`.
3. Machine contracts in `schemas/` and `registry/`.
4. Deterministic runtime commands in `packages/codex-org-bootstrap` and `scripts/`.

The registry binds the first three layers together. The package validates that
binding and exposes commands for controllers.

## Verifiers

Two read-only verifier roles sit between implementer and PR:

- `linon` — adversarial CODE review (NN1–NN4, RED-test calibration), emitting `linon-review`.
- `stefan` — aesthetic review of human-facing surfaces on the rendered pixels (the four layers:
  stimulus → features → human comparison → statistical model → validation), emitting
  `aesthetic-review`. A computed metric is a correlate of beauty, not taste; the owner taste-gate
  is final, and genre bands are calibrated per cartridge (v2). Backed by the measured instrument
  `scripts/stefan-aesthetic-review.py` (MIT libraries — see THIRD_PARTY_NOTICES).

## Controller: semantic core vs deterministic harness

The controller splits along the same mechanical/semantic line the verifiers use. The semantic
core (contract authoring, aufheben synthesis, deliverable judgement) is LLM work. The mechanical
harness is code: `scripts/carrier_harness.py` launches carriers with stdin closed, pinned flags,
`bootstrap/carrier-discipline.md` prepended, a bounded timeout with retry, and post-run
scope-deviation enforcement — so invocation rules are enforced, not merely documented. Gate
instruments (`merge-gate.py`, `verify-linon-packet.py`, `stefan-aesthetic-review.py`,
`measure-result-screen.py`, `profile-evidence-check.py`) are the deterministic backbone the
semantic core consumes.
