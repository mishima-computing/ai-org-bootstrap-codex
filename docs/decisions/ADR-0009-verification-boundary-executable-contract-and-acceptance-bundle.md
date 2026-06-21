# ADR-0009: The verification boundary — design chooses an *executable* contract; deterministic machinery enforces it

## Status

Accepted. Supersedes the working hypothesis (recorded in the ADR-0008 addendum and in session notes)
that the structural-vs-model split runs along a "binary defect vs contextual defect" line. That line is
wrong. This ADR records the corrected boundary and the investment sequence that follows from it. It is the
control-plane companion to the execution-substrate work (the *execution* substrate / inner-box dynamic verification):
ADR-0022 says *where* code runs; this ADR says *what is proven about it and by whom*.

## Context

Observed defect distribution over the autonomous build pipeline (per work-unit denominator):

- **55%** of work units do **not** pass first review.
- Of those, **~42%** (23%/55%) subsequently **exhaust the repair cap**.
- **40%** of all agent steps are **repair**, and each repair currently re-runs ~the full design+review wave.

Repair-fix class frequency (what the review actually catches and sends back):
`contract/interface precision (22) > edge/robustness (15) > operability/security (9) > prose (7) = correctness (7)`.
The largest leak is **aufheben contract interface precision**.

The review itself is, today, **almost entirely static**: Linon = `codex review` (reads the diff + cross-file
dependents); the designers reason over prose; the implementer **self-reports** its own test results. The
controller re-reads the diff and re-runs Linon, but does **not** independently re-execute the built
artifact or its tests. There is **no independent dynamic execution** wired into the per-leaf review. (That
gap is what ADR-0022's inner box is built to close; this ADR is what *runs* in it.)

The tempting fix — "the misses are interface/edge-case judgement, so improve the design prompts" — is the
**lowest-return** move. Prompt tuning may raise recall but creates no stable oracle and stays vulnerable to
**correlated omissions** across design agents (all three designers share the same blind spot, so adding a
fourth reminder does not help). The session's premature `aufheben-designer.md` "pin the interface" prose
edit was exactly this mistake and was reverted.

## Decision

**The dividing line is not "binary vs contextual." It is: choosing the intended contract/policy requires
judgement; checking and enforcing the chosen contract should be deterministic whenever practical.**

The architecture is therefore not "structural **A** vs model **B**." It is **B produces an executable A**:
the design agents emit a *typed, executable* implementation contract + acceptance bundle, and deterministic
machinery proves the implementation obeys it.

```
judgement (model/human)                    deterministic (machine)
─────────────────────────                  ──────────────────────────────
WHAT must be true; does the      ───────►  PROVE / TEST / ENFORCE that the
contract fit the goal?                     implementation obeys the contract.
```

### Defect-class allocation

| Class | Primary investment | Design agents (judgement) | Deterministic machinery |
|---|---|---|---|
| **Interface / contract precision** | structural-first | choose the interface and **encode it completely** | enforce packaging, exports, invocation, I/O, **exit codes**, examples, compatibility — black-box against the built artifact |
| **Edge cases / robustness** | **hybrid**: design the oracle, automate the search | define error semantics, invariants, input partitions | generate + execute **property tests, fuzzing, fault injection, schema validation** |
| **Operability / security** | structural-first for hard invariants | choose threat model + resource budgets | drop capabilities, contain resources (cgroups), scan artifacts, block high-confidence violations |

### The contract becomes a discriminated schema, not prose

For each `deliverable_kind` (`cli | library | http_service | rpc_service | batch_job | json`), the contract carries
a `oneOf`-profiled schema (CLI fields required only for CLI deliverables, so it never degrades to a
universal checklist): `build_and_install`, `entrypoint`, `public_surface` (packages/modules/exported
symbols/signatures), `io_contract` (stdin/stdout/stderr/files/network), `status_and_errors`
(success/invalid-input/operational-failure codes, partial-success policy), `compatibility` (baseline +
allowed breaking changes), and concrete `examples` (invocation → expected output/stderr/status). Robustness
adds an explicit error model: empty/oversized/malformed/encoding-failure/timeout/cancellation/duplicate
inputs each get a declared outcome **or `not_applicable` with rationale** — never "handles errors robustly."

**Requiring this schema is itself a design-quality intervention** — it forces the designers to confront the
omissions (exit codes, exports, failure semantics, resource policy) *before* implementation. The schema
controls what the model must reason about; the gate checks the result. This is why interface work is not
"structural vs model" — it is both.

### Two structural mechanisms this adds to the pipeline

1. **Pre-implementation contract review.** Review the *structured contract against the original goal*
   **before code exists** — catching "wrong interface," omitted edge semantics, and unsuitable resource
   policy at design time, where they are cheapest. (Today the reviewer only sees misses *after*
   implementation.)
2. **The acceptance bundle is immutable to the implementer** (or generated independently from the
   contract). Otherwise the implementation and its oracle reproduce the *same* misunderstanding and the
   gate proves nothing.

### Gate policy (Tricorder discipline)

A **build-blocking** check must have **effectively zero** false positives in our environment (a finding the
org would not act on counts as a false positive even when technically correct). **Review-time** advisory
checks stay **< 10%**. Therefore:

- **Hard-block**: schema validity, exact black-box conformance (install/launch/import/signature/exit-code/
  golden-example), *definite* compatibility breaks, reproducible property/sanitizer/crash counterexamples,
  known provider-token / private-key / verified-credential secret hits, declared-resource-budget breaches.
- **Advisory**: naming/style, inferred/possible compatibility, generic-entropy secret candidates, broad
  taint/injection SAST.
- **Shadow-first**: every new gate runs in shadow mode and is promoted to blocking only once its violations
  are reproducible and almost always acted upon.

### Repair routing (not automatic full-wave)

Route by *failure type*, so a typo fix does not re-run the designers:

- contract missing / semantically wrong → **redesign + resynthesise**;
- implementation violates a *correct* contract → **rerun implementer only**;
- deterministic property fails → hand the **minimised counterexample** to a focused repair agent;
- security/operability policy violated → targeted implementation/config repair;
- the *gate or oracle* is wrong → repair the **oracle**, not the product.

### Finding-to-regression conversion

Every accepted review finding **must** become a contract clause, golden example, property, generator
partition, fault-injection scenario, or deterministic rule — else the pipeline re-pays LLM review+repair
cost for knowledge it already acquired. (This is the structural answer to "40% of steps are repair.")

## Evidence (grounding, not illustration)

- **Tricorder** (Google): build-breaking analyses needed ~**0** effective false positives; review-time kept
  **< 10%** — the basis for the block/advisory split above.
- **oasdiff** distinguishes *definite* from *potential* breaking changes — the model for tiered-confidence
  gates (don't pretend every compatibility judgement is equal-confidence).
- **Static types as contracts**: a repository-mining study found the evaluated TypeScript/Flow caught
  ~**15%** of sampled public JS bugs — types are *one* layer, not a behavioural spec.
- **OSS-Fuzz** (May 2025): >**13,000** vulns / **50,000** bugs across ~1,000 projects — strong evidence for
  fuzzing parsers/codecs/protocol handlers.
- **Amazon ShardStore**: executable reference model + property testing prevented **16** production issues
  (incl. crash-consistency/concurrency) at ~13% of codebase size, ~9 person-months of specialist setup —
  evidence for **selective** use on high-risk stateful components, not universal formal modelling.
- Secret leakage affected >100,000 public GitHub repos with thousands of new unique secrets/day — prevention
  (push-protection + artifact scan *before exposure*) beats review-only detection. A real leaked credential
  must be **rotated/revoked**, not merely deleted from the current file.

## Investment sequence (highest return first)

1. **Executable contract profiles + black-box conformance harness** (CLI, library, service). Targets the
   largest defect class *and* raises design completeness. Highest return.
2. **High-confidence security controls**: source+artifact secret scanning; a common, language-independent
   sandbox with time/memory/process/file/network limits (the ADR-0022 inner box already provides the
   containment seam).
3. **Robustness oracle generation**: require input partitions + failure semantics + invariants; generate
   schema cases, property tests, fuzz harnesses. Start with parsers, codecs, CLIs, stateful components.
4. **Finding-to-regression conversion** wired into the repair path.
5. **Targeted prompt tuning — last**, only after structural telemetry shows which omissions remain.

## Consequences

- The highest-return move for the observed distribution is an **executable contract + acceptance bundle**,
  **not** broader prompt tuning and **not** a blanket expansion of rigid linters.
- This ADR + ADR-0022 compose: the **inner box** (ADR-0022) is *where* conformance/property/fuzz/sandbox
  gates execute; **this ADR** is *what* they prove and how findings route. "Run it, don't just read it"
  becomes a first-class, deterministic verification tier alongside static Linon.
- Validation plan: four randomised arms — baseline, prompt-only, structure-only, combined — stratified by
  deliverable type and language; measure first-pass pass rate, repair-step share, cap-exhaustion, per-class
  findings, escaped defects, gate actionability/override rate, latency, total agent cost. New gates run in
  shadow before blocking.
- Boundary, stated once: **the model/human decides what must be true and whether the contract fits the goal;
  deterministic systems prove, test, or enforce that the implementation obeys it.**

## Addendum — investment #1 status (structurally complete)

The structural backbone of investment #1 (choose → encode → check) is **built and, for the CLI path, proven
live** (a goal run where an unchanged org emitted a `conformance.cli` profile, built a contract-honouring CLI,
and the gate ran the artifact and passed 6 examples). The pieces:

- **Executable CLI contract + black-box conformance gate** — `cli_profile` in the schema; `conformance.py`
  installs + runs the declared examples and checks exit status + stdout/stderr against the contract; wired
  **shadow-first** into the per-leaf pipeline (`CONFORMANCE_GATE`). Done; live-proven.
- **Real checkers for every deliverable kind** — `http_service` (boot + request/response), `library`
  (import-probe the declared public surface), `batch_job` (run + exit status + produced artifacts), `json`
  (parse + JSON-Schema + key paths, no execution), and `rpc_service` (boot + real call/response over
  json_rpc_http stdlib or lazily-loaded gRPC) are all built. `deliverable_kind` is now **schema-required**:
  `none` declares no checkable interface, and `undetermined` declares an interface of a kind no checker
  supports yet — recognised, streamed as `slot_unchecked` (no silent pass), the live entry to the empty-slot
  mechanism for the next new kind.
- **Deterministic pre-implementation contract review** (`contract_preflight.py`) — fires the moment aufheben
  produces the contract, *before* the implementer's wave: completeness (acceptance_criteria; CLI help/success/
  error coverage; entrypoint) + self-consistency (example exit codes ⊆ declared policy). Shadow-first
  (`CONTRACT_PREFLIGHT`); block folds into the repair loop, re-running aufheben.
- **Immutable acceptance bundle** — the golden examples are **withheld from the implementer**
  (`WITHHOLD_ACCEPTANCE_BUNDLE`, on by default): it builds to the spec it sees (acceptance_criteria,
  entrypoint, exit-code policy) but not the goldens, so the implementation and its oracle cannot share the
  same misunderstanding and the implementer cannot hard-code to the goldens; the controller/gate keep the
  full bundle. A `_examples_withheld` marker keeps the withholding visible.

**Consciously deferred (not part of "structurally complete"):**

- **The judgment half of pre-implementation review** — an independent LLM critic of *contract-vs-goal* ("is
  this the right interface for the goal?", omitted edge semantics, unsuitable resource policy). It overlaps
  what the designers/aufheben already do and is closer to the role/prompt layer the sequence ranks last; add
  it as a carrier-backed reviewer **only if telemetry shows contract-vs-goal misses** the deterministic
  preflight and the designers do not catch.
- **Per-kind real checkers** (library API-diff; service boot + protocol-driven testing) — built when the
  first deliverable of that kind needs one.
- **Shadow → block promotion** — flip each gate to blocking only after its effective-FP is shown ~0 (Tricorder).
  **Done (2026-06-21) for conformance and contract-preflight** — see the promotion-record addendum below. The
  fuzz and secret gates stay shadow until their own FP is measured (fuzz is stochastic; secret scanning has
  not been FP-audited yet).

So #1 is at a clean stopping point: the deterministic, structural spine is in place and exercised; what
remains is either telemetry-gated (promotion), demand-gated (other-kind checkers), or layer-deferred (the
LLM judgment review).

**Investments #2 and #3 (built):**

- **#2 high-confidence security controls** — `secret_scan.py` (validity-tiered: provider tokens / private
  keys block, generic entropy advises; scans source **and built-artifact archives**; the value is never
  emitted; gitleaks backend + pure-Python fallback) and resource limits (the gate's subprocess runner is
  rlimit/timeout/output-bounded; the Kata box pod caps cpu/memory/ephemeral-storage and drops capabilities —
  live-verified). Deferred with reason: SAST tiering and sanitizers need an engine in the box image (and FP
  benchmarking before any rule blocks — a build-breaking rule needs ~0 effective FP, which can't be measured
  without the engine); network default-deny / read-only-root belong to the inner build sandbox, not the
  carrier's outer box (it needs LLM egress and a writable fs).
- **#3 robustness oracle (edge-case half)** — `fuzz_cli.py`: black-box property fuzzing of the built CLI.
  Judgment defines the invariants (no crash, exit-in-policy, no hang); the generator searches adversarial
  inputs for a counterexample and minimizes it. Deeper backends (in-process Hypothesis, coverage-guided
  Atheris, fault injection, metamorphic/differential, mutation testing) are a future-box-image follow-up.

- **#4 finding → regression conversion** — `regression_corpus.py`: an append-only, deduped, bounded corpus
  of gate counterexamples. The deterministic gates self-regress by construction (they re-check the same
  contract/scan each leaf); the exception is fuzzing, so its counterexamples are persisted and **replayed
  first** on every later run (a reappearing crash regresses deterministically instead of being re-discovered
  by random chance or an LLM — the structural answer to "40% of steps are repair"). The corpus lives beside
  the shared stream so it persists across leaves and runs.

All gates are shadow-first with the same one-line promotion to `block`, and findings route through the
shared severity budget / repair loop. Test suites: conformance 14, contract-preflight 7, secret-scan 8,
cli-fuzz 7, regression-corpus 4, plus the box-manifest tests in the execution runtime.

With #1–#4 built (and #2's engine-gated items and #1's judgment review consciously deferred), ADR-0009 is at
a clean stopping point: the deterministic verification spine — choose → encode → check → contain → fuzz →
regress — is in place and exercised. The conformance and contract-preflight gates have now been promoted from
shadow to blocking on measured FP evidence (addendum below); fuzz and secret remain shadow pending their own.

## Addendum — shadow → block promotion of conformance + contract-preflight (2026-06-21)

The two gates that run on every leaf inside `controller_pipeline.run_pipeline` were promoted from shadow to
**blocking by default** (`CONFORMANCE_GATE_MODE` and `PREFLIGHT_MODE` default to `block`; reversible by env
with `CONFORMANCE_GATE=shadow|off` / `CONTRACT_PREFLIGHT=shadow|off`, no code change). A failing conformance
finding now folds into the convergence `findings` (routing a pure-artifact defect to the implementer only); a
failing preflight routes the contract back to aufheben up to `PREFLIGHT_AUFHEBEN_CAP` before the implementer
runs at all.

**Why now — the Tricorder bar was finally measured, not assumed.** The promotion criterion ("effective-FP ~0")
had no instrument, so the gates sat shadow. `scripts/gate_fp_audit.py` is that instrument — a small, LLM-free,
re-runnable harness over a curated corpus of `(contract, artifact)` fixtures with declared expectations. It is
part of the trusted kernel (ADR-0011): the gate that certifies the certifier is hand-built and tested, not
generated by the pipeline it audits. The promotion bar is non-gameable: a gate is `PROMOTABLE` only when
effective-FP == 0 over the good fixtures **and** every bad fixture is caught (an FP-free gate that catches
nothing is useless) **and** there is at least one good and one bad fixture.

**Evidence at promotion.** The corpus spans every gate-bearing kind with both synthetic and **real in-repo**
artifacts (audited in place via a fixture `workdir`):

- conformance — good = 7 (synthetic cli/library/json + real `scripts/merge-gate.py`,
  `scripts/validate-bootstrap-pack.py`, `scripts/frontier.py`, `schemas/implementation-contract.schema.json`),
  bad = 3; **effective-FP 0%, catch 100%**. CLI — the highest-machinery checker (process exec + exit codes +
  stdout/stderr capture) — has real-artifact coverage; library (import-probe) and json (schema-validate) are
  fully exercised by synthetic good+bad and one real artifact each.
- contract-preflight — good = 10 (all the well-formed contracts above), bad = 1; **effective-FP 0%**; its catch
  side additionally rests on `test_contract_preflight`'s 14 unit tests over the contract-shape defects.

**Limits, recorded.** The corpus is curated, not a production sample; "PROMOTABLE" means zero FP on canonical
real + synthetic cases, not a proof of production FP~0. The harness makes growing the corpus with real leaf
artifacts cheap, so the evidence strengthens over time; the flip is env-reversible if a real-world FP appears.
Fuzz stays shadow (stochastic inputs — its determinism is handled by the regression corpus, not an FP audit);
secret scanning is not yet FP-audited.

**Test status.** Full scripts suite green after the flip (conformance 54, contract-preflight 14, controller-goal
21, controller-workflow 11, gate-fp-audit 11, + others); the two suites that asserted the old shadow defaults
were updated to assert the promoted defaults (and to set the mode explicitly where they test shadow streaming).
