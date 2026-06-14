# ADR-0004: Controller Python-ification

## Status

Proposed. (Designed by the agent team — aggressive / conservative / genius, launched through
`scripts/carrier_harness.py` itself — and synthesized by aufheben. Implementation pending owner.)

## Context

The controller is two things: a **semantic core** (author contracts, synthesize design tension,
judge deliverables) that needs an LLM, and a **mechanical harness** that must be right every time.
An LLM controller forgets mechanical details — this rebuild hung a carrier twice by omitting
`< /dev/null` (codex then blocks on stdin). `scripts/carrier_harness.py` (ADR follow-on, merged)
began the harness: it closes stdin, pins flags, prepends carrier-discipline, bounds runs with
timeout+retry, enforces scope, and hashes provenance. This ADR decides how much further the
controller's mechanical half should become deterministic Python, and where the boundary sits.

## Decision

Adopt a **Workflow / Activity split** (the Temporal pattern named by `genius`):
deterministic sequencing and replay live in a Python *workflow runner*; side effects with
judgment — LLM calls — are *activity-like* steps whose inputs/outputs are typed and recorded.

**Python owns (deterministic controller package):**
- `controller_workflow.py` — the phase state machine: `prepare → validate_contract → run_carrier
  → enforce_scope → run_verifiers → package_evidence → await_semantic_judgment → merge_gate`.
  It advances on structured inputs and exit codes; it never authors contracts or judges quality.
- `controller_models.py` — typed `CarrierContract`, `AllowedChangeSet`, `CarrierRun`,
  `ScopeReport`, `VerifierRun`, `SemanticDecision`, `ControllerRunReport`.
- `controller_scope.py` — scope enforcement stronger than the harness's current `git status`
  parse: `--porcelain=v1 -z`, renames/deletes/untracked, a pre-run dirty baseline, forbidden-path
  classes, and **declared-vs-actual** touched-file comparison (makes carrier-discipline line 35
  executable).
- `controller_verifiers.py` — normalize the deterministic gates (linon packet, stefan instrument,
  profile-evidence, result-screen measurement, merge-readiness, merge-gate) to one shape
  `{status, exit_code, command, evidence_path}`.
- `controller_evidence.py` — append-only, content-addressed run journal under
  `.agent-runs/controller/<run-id>/` (prompt/discipline/diff hashes, argv, attempts, exit codes):
  replay/audit, like an event history.
- Retry policy: retry only **mechanical** failures (timeout, stdin-hang marker, transient exit,
  missing/invalid final JSON). Never retry a scope violation or forbidden-path creation.
- The **final mechanical pass/block** status (scope ∧ gates ∧ exit codes ∧ required artifacts).

**The LLM keeps (semantic, by all three lenses):**
- Owner-intent → task decomposition; the contract's semantic payload (what to accomplish,
  acceptance meaning, allowed-files intent); aufheben synthesis across conflicting outputs;
  deliverable judgment where correctness is not reducible to known checks; blocker interpretation
  (revise contract / split / escalate / stop). Aesthetic/code-review reasoning beyond instruments.

**The boundary — a strict typed handoff (the three converged on this):**
1. Semantic core emits `contract.json` (role, prompt, sandbox, timeout, retries,
   `files_allowed_to_change`, forbidden paths, expected verifiers/artifacts).
2. Python validates the shape, renders the carrier prompt, runs it through
   `carrier_harness.run_carrier`, enforces scope, runs verifiers, and emits `controller-report.json`
   (status, attempts, scope report, diff hash, verifier results, unresolved failures).
3. Semantic core consumes only that report (+ selected artifacts) and emits
   `semantic-decision.json` (`accept | revise_contract | run_next_carrier | block | merge_ready`,
   with rationale). Python validates its shape and advances — **it never makes the decision.**

### Aufheben note (reconciling the lenses)

`aggressive` pushed maximal Python (own templating, selection, the whole loop). `conservative`
warned that mechanizing judgment is "policy cosplay: green checks with the wrong work." The
synthesis: **Python owns the envelope and all mechanics and the final mechanical status; the LLM
owns the named semantic decisions; Python validates their shape but never makes them.** Contract
templating is split — Python owns the envelope, the LLM fills semantic fields (aggressive's own
handoff already concedes this). Rejected for now: building the full framework before the boundary
is proven — it is phased (below), per conservative + genius ("don't freeze a process that still
needs semantic flexibility").

## Consequences

- Invocation rules become executable, not documented; the `< /dev/null` class of bug cannot recur.
- **Scope enforcement is the highest-risk component** (rename/delete/untracked/dirty-baseline) —
  build it first and robustly; a bug here institutionalizes carrier misbehavior.
- Fail-closed on semantic output: malformed `semantic-decision.json` is blocked/retryable, never
  partial success (no LLM schema-gaming).
- Retry safety: each run/attempt directory is immutable and content-addressed, so retries do not
  duplicate side effects or corrupt provenance.
- Guard against evidence theater: compare **declared-vs-actual** changes, not merely "hashes exist".

### Phasing (don't build the framework before the boundary is proven)

- **Phase 0 (done):** `carrier_harness.py` — launch, stdin-closed, flags, discipline, timeout/retry,
  basic scope, provenance.
- **Phase 1 (done):** `controller_scope.py` (hardened — porcelain-z, rename/delete/untracked,
  dirty baseline, forbidden-path classes, declared-vs-actual) + `controller_models.py`
  (`CarrierContract` / `ControllerRunReport` / `SemanticDecision`, fail-closed validation) +
  `controller_evidence.py` (append-only content-addressed run journal). 12 tests over a temp git
  repo (add/modify/delete/rename/untracked/baseline/forbidden/allowed/declared) — all pass.
- **Phase 2 (done):** `controller_verifiers.py` (gate normalization to one `VerifierRun` shape) +
  `controller_workflow.py` (the Workflow-side runner: prepare → validate_contract → run_carrier →
  enforce_scope → run_verifiers → package_evidence → await_semantic_judgment, with an injectable
  carrier runner). 7 offline wiring tests (happy / scope-violation / carrier-failure /
  verifier-failure / journal-all-phases / fail-closed contract) all pass; suite 48 → 55. Live
  end-to-end with a real Codex carrier: ok=True, changed=['allowed.txt'], scope_ok, verifier pass,
  zero LLM tokens on orchestration (the LLM authored only the contract).
- **Phase 3 (done):** `controller_loop.py` — the semantic-decision loop. Each round runs the
  contract (Phase 2), hands the report to an injected `decider` (the LLM/Activity), validates the
  returned `SemanticDecision` fail-closed, and ADVANCES: accept/merge_ready/block terminate;
  revise_contract/run_next_carrier loop with the decider-supplied `next_contract` (or fail closed to
  block if none); bounded by `max_rounds`. 6 offline tests (accept / revise-then-accept / block /
  max-rounds / loop-without-next-contract / invalid-decision). The advancing is Python's; the
  decision is the LLM's. Suite 55 → 61.

This is also the deterministic control plane Shagiri productizes; building it serves both.

## Phase-0 integration test results (2026-06-14)

Live tests of `scripts/carrier_harness.py` against a throwaway git repo, driving real Codex carriers:

| Test | Result |
| --- | --- |
| T1 scope OK (carrier writes only the allowed file) | PASS — changed 1 file, 0 deviations, exit 0 |
| T2 scope violation (carrier writes an out-of-scope file) | PASS — deviation `extra.txt` caught, exit 1 |
| T3 timeout + retry (5s timeout) | PASS — both attempts timed out, killed + retried, no crash, exit 1 |
| T4 Codex operates the harness | PARTIAL — Codex correctly built/ran `carrier_harness.py run`, then read and interpreted the sub-carrier's `carrier-report.json`; the **operation** works |

Two bugs were found by the tests and fixed: the harness's own `.agent-runs/` scratch was mis-counted
as a scope deviation; and the timeout path crashed on bytes/str when concatenating captured streams.

Finding (T4): a Codex carrier launched **from inside another Codex sandbox** fails to initialize
("failed to initialize in-process app-server client: Operation not permitted") — codex-in-codex is
sandbox-blocked. The harness reported this honestly (ok=false). Implication: the controller harness
should be driven by a **Python controller (or an out-of-sandbox process)**, not a nested Codex —
which is exactly the direction of this ADR.
