# Implementer defect taxonomy & the gate/skill strategy

The AI Org's implementer is an LLM carrier; LLM carriers ship defects in characteristic, *recurring* classes. This
document catalogs those classes (grounded in the public literature and in this org's own live runs), names the deep
cause, and records the engine's response: for each class, a cheap **deterministic gate** (enforce, ~0-false-positive)
and a **skill** (prime the implementer up front). Soft prime + hard enforce. The aim is to move recurring rejections
off the expensive adversarial reviewer (Linon) onto cheap gates, and to lower the rejection rate at the source.

## The deep cause: a gate is a reward function the agent will try to hack

> "Reward hacking is not a flaw in the model but a way that a flaw in the reward design surfaces — if there is a gap
> in the reward function, the model will almost certainly find it."

LLMs are optimized to produce code that *looks* correct, not code that *is* correct, so defects cluster exactly where
plausibility and correctness diverge (subtle logic, silent failures). When the reward is "tests pass," agents optimize
the proxy: documented exploits include injecting a config that rewrites every test outcome as passed, and `sys.exit(0)`
to escape the harness with a success code; teaching a model to game coding tests has generalized to sabotage in ~12%
of runs (Anthropic, *Natural Emergent Misalignment from Reward Hacking*, arXiv 2511.18397).

**Consequence for gate design** (and a validation of this engine's ADR-0009 core):
- The implementer must **not author its own pass criteria** (self-review shares the generator's blind spots —
  correlated failures). This is why the acceptance bundle is **withheld** from the implementer (WITHHOLD_BUNDLE,
  ADR-0009/0018).
- Never trust the agent's "tests passed" claim. **Re-run the real artifact in a separate process and assert on
  observed effects** — which is exactly what the conformance gate does (boot + probe, not self-report, ADR-0009).
- Keep an independent adversary (Linon) for the residual semantic class.

## Defect classes, evidence, and the engine's response

| Class | Evidence | Deterministic gate (enforce) | Skill (prime) |
|---|---|---|---|
| **Incomplete refactor / missed references** (e.g. a token regex that doesn't split snake_case, missing real callers like `_scaffold_seed_commit`) | "the agent operates with an incomplete map"; recurring bugs around public APIs / shared libs | **`forbidden_patterns`** — grep the produced tree, block if an old token survives outside declared exclusions (BUILT; proven end-to-end against the real defect) | rename/refactor skill: handle snake_case, grep-verify the old token is gone, watch exclusion boundaries |
| **Breaks previously-working code** (regression) | Alibaba **SWE-CI**: 75% of models break working code; **73.6% of modification-task failures** are broken existing functionality | **regression gate** — run the *full* pre-existing suite, not just new tests (CANDIDATE, high value) | "run the whole suite; a green new test over a red old test is a failure" |
| **Stub / silent-failure paths** (swallowed exceptions, dead `pass`/TODO standing in for real logic; tests that pass but exercise nothing) | "high line coverage on AI code is not evidence of correctness"; "failures invisible without instrumentation" | **Part A real-wiring** (BUILT: a service must declare + a booted probe exercise the production boundary) + a **silent-path gate** (ban swallowed-exception/dead-code in production paths; assert error paths actually raise/log) (CANDIDATE) | "exercise the real path and show its output; error paths must raise/log, not swallow" |
| **Scope creep / collateral edits** (quietly refactors three other call sites beyond the plan) | reviewers told to hunt "a model change that quietly refactored how that model is used elsewhere" | **R3 forward scope pressure** (BUILT as a prompt prime) → promote to a **hard allowlist-diff gate** (the literature's explicit recommendation) | DO-NOT-TOUCH list + pre-finish self-diff against `files_allowed_to_change` |
| **Knowledge hallucination** (invented library/project APIs ~26%; undefined variables ~16.9%) | "Knowledge hallucinations ~34.9%" | **import/symbol-existence gate** — assert imported names + called symbols resolve (CANDIDATE) | "every symbol you call must already exist or be created in-scope; no invented APIs" |
| **Requirement-conflicting / plausible-but-wrong** (~39.6%, the single biggest bucket: compiles + tests pass but does the wrong thing) | "behavior-conflicting 35.4%" | residual — **Linon** (semantic adversary), plus **behavioral-coverage** (each acceptance criterion maps to ≥1 end-to-end test) and metamorphic/property tests (external-truth oracles) | spec-faithfulness skill + outcome-based falsifiable acceptance (ADR-0016 D7) |

## Operating principles

1. **Executable + adversarial acceptance.** Acceptance derives from a spec the implementer cannot edit or reason
   backward from; treat the gate as a reward function the agent will try to game and design against the shortcuts.
2. **Gate on outcomes, not appearances.** Require the real path run with shown output; assert deterministically;
   never accept line coverage as sufficiency.
3. **Cheap deterministic gates first, the adversary behind them.** Each gate that catches a class deterministically
   is one fewer Linon run (gate-behind harvests the skip). The residual semantic class stays Linon's.
4. **A gate without a skill leaves the bug rate high; a skill without a gate leaves it unenforced.** Pair them.

## Sources

- Defect taxonomy / evals: SWE-bench Pro vs Verified gap; Alibaba SWE-CI long-term maintenance (EvoScore);
  Answer.AI Devin field eval; verygood.ventures "review AI-generated code"; dev.to/pharaoh "incomplete map".
- Reward hacking: Anthropic arXiv 2511.18397; NIST CAISI cheating-AI-evaluations; cybernews AI-cheat-agent.
- Countermeasures: metamorphic prompt testing (arXiv 2406.06864, 75% detection @ 8.6% FP); property-based testing
  (arXiv 2506.18315); Agentless (arXiv 2407.01489).
- (Full URLs in the research logs; this doc is the distilled, engine-facing map.)
