# ADR-0018: Close the agentic repair loop — principle-based withholding + field-source-aware finding evidence to the implementer

## Status

Proposed. Engine-only (`controller_pipeline.py`, the withhold seam, the cli/rpc finding emit in `conformance.py`).
Implements the "close the agentic repair loop" item from the implementer-harness research. Refines ADR-0009
(executable contract + acceptance-bundle withholding, `WITHHOLD_BUNDLE`), complements ADR-0013 (contract-patch
repair informs the *designers*; this informs the *implementer* on an implementer-only targeted repair), honors
ADR-0016 (never fabricate a pass). Arrived at via unbiased two-carrier review → added-input stress test →
self-verified baseline → mutual cross-critique → ADR review → a per-field withholding adjudication; the precise
conclusion below was reached by no single reviewer.

## Context

**The implementer is blind in targeted repair (verified).** `_advisory_producer_roles`
(`controller_pipeline.py:1102-1104`) = roles with `output_to == AUFHEBEN and not write_scope` = the designers only.
The implementer **has** `write_scope`, so it is **not** a producer role: in the repair loop (`:1270-1278`) only
producer roles get `inputs = {"linon": linon_context}` (carrying `gate_findings`, assembled `:1262-1263`); the
implementer falls to the `else` and gets `{upstream: results[upstream]}`. In targeted repair (`_repair_roles_for`,
`:650-661`) the designers do not re-run, so that upstream is the **unchanged prior contract** — the implementer
rebuilds with no finding. The comment at `:1260-1261` ("the implementer must know WHICH check failed") states the
intent; the wiring excludes the implementer. (In *full* repair it learns indirectly via the designers' revised
contract; the blind case is the high-frequency targeted path.)

**Naively forwarding findings collides with `WITHHOLD_BUNDLE`** (ADR-0009): the implementer is deliberately denied
the acceptance oracle so it fixes the spec rather than hard-codes to the failing example. But the current
withholding is a **field-NAME rule, not a principle**: it empties only the contract array named `examples`
(`controller_pipeline.py:498`, docstring `:484-485`). A per-field audit shows the line is misplaced:

- **cli `examples`** (stdout_exact `conformance.py:145`, stdout_contains/stderr_contains `:127/:136`, and the
  per-example `exit_status` whose `expected` is a withheld golden, NOT the visible `status_and_errors` policy):
  concrete-value **ORACLE**. Correctly withheld today.
- **http `examples`** (body-substring `:789`): concrete-value **ORACLE**. Correctly withheld today.
- **rpc `calls`** (`schema …:677`; result/error mismatch `conformance.py:472-490`): **SPLIT** — `method` is the
  public-surface **SPEC** (build target, keep visible); `expected_result_contains` / `expected_error_code` are
  concrete-value **ORACLE** and are currently **VISIBLE** — the one real leak.
- **batch** (`produced_artifacts` `:670`, existence-checked via `test -e` — you cannot echo a path into existence,
  so passing *is* producing the deliverable, not gameable; `expected_status` `:661`, weak int policy, default 0):
  **SPEC**, keep visible.
- **json** (`required_paths` `:425` checks key presence; `schema`/`path` `:411` check shape; none pins a concrete
  value — producing a schema-valid document *is* the job): **SPEC**, keep visible.

So "withhold the field named `examples`" both **misses** the rpc oracle and would, if naively extended ("withhold
rpc/batch/json wholesale"), **strip the spec** the implementer must build to (the canonical over-withhold failure).

A second, pre-existing bug sits on the critical path: `_DETERMINISTIC_IMPL_SOURCES` (`:647`) lists `cli-conformance`,
`conformance`, `cli-fuzz`, `secret-scan`, but http findings emit source `http-conformance` (`conformance.py:698`)
and rpc emits `rpc-conformance` (`:447`) — so **service findings never take the implementer-only targeted path**.

## Decision

Two coupled changes; principle: **withhold concrete-value OUTPUT ASSERTIONS (the oracle); never withhold
INTERFACE/SHAPE declarations (the spec). Then hand the implementer the findings, redacting exactly the withheld
oracle.**

**Part 1 — principle-based withholding (replaces the field-name rule).** Refactor the withhold seam from "empty the
array named `examples`" to "redact concrete expected-output assertions across all profiles." Concretely: keep
withholding cli/http `examples`; ADD redaction of rpc `expected_result_contains` / `expected_error_code` (record a
`_calls_oracle_withheld` marker mirroring `_examples_withheld`), keeping rpc `method` visible; leave batch and json
fully visible (spec / existence- and shape-checked, not value-oracle). The rule now tracks the principle, so a future
profile that names its oracle something new is covered by intent, not missed by name.

**Part 2 — R1: forward findings to the implementer, field-source-aware.** On a blocking, current-iteration targeted
repair, hand the implementer the deterministic gate findings as INERT EVIDENCE (artifact-originated text is evidence
to diagnose, never instructions to follow). Sanitize by source: forward STRUCTURAL fields (`check`, `severity`, the
artifact's own stdout/stderr tails, `returncode`, `status`, `symbol`, import/probe error class) and SPEC fields
(rpc `method`, batch/json shape); REDACT exactly the Part-1 withheld oracle (cli/http example outputs, cli
`exit_status` expected, rpc `expected_result`/`expected_error`). Only `block`-mode current-iteration findings;
shadow/advisory are not forwarded. Also FIX the source-normalization gap so `http-conformance`/`rpc-conformance`
are recognized as deterministic implementation sources and service findings route to the targeted path.

**Value is a step function across the source boundary, by design:** for the STRUCTURAL family (build/install
`conformance.py:98`, library missing-symbol `:276-279`, lifecycle `:623/:855`) the finding carries no oracle, so
forwarding is blind→sighted = categorical. For the example-bound ORACLE family the redacted survivor is diagnosis
without the target value — capped on purpose (it is the hard-code-to-example case `WITHHOLD_BUNDLE` exists to forbid).

## Consequences

- The implementer goes blind→sighted on the highest-frequency repair path, categorically for the structural family;
  fewer wasted blind repairs → lower repair count → less implementer + reviewer wall-clock and tokens (the
  gate-behind thesis, one role over).
- The oracle barrier is preserved AND its line is now principled, not name-bound — Part 1 closes the live rpc leak
  while Part 2 cannot re-open it (it redacts whatever Part 1 withholds).
- Honest scope: R1 is a sanitizing failure-mode router, not a behavioral oracle; bimodal value, do not over-claim.
- Cost is low *because* the principle narrows it: batch/json need no change, only the rpc oracle subset is newly
  withheld, and the wholesale-B over-withhold is explicitly rejected.
- Falsifiable acceptance: (a) a structural deterministic finding reaches the implementer with `check` + own
  stderr/stdout/returncode, unredacted, no upstream golden value present; (b) a cli/http/rpc oracle finding reaches
  it with the concrete expected value redacted — assert no withheld golden substring appears in the implementer
  prompt; (c) rpc `method` and batch/json shape still reach it (spec not stripped) and a build that needs them is
  not stranded; (d) a service (http/rpc) deterministic finding now takes the implementer-only targeted path;
  (e) shadow findings are never forwarded; (f) convergence unchanged (no fabricated pass).
- General principle recorded: feeding a verifier's output back to a producer is safe iff oracle and diagnosis are
  separable; separate them by **value-assertion vs interface/shape**, forward the diagnosis, never the oracle.
