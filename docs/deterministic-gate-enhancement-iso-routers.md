# Research basis: ISO/IEC standards as verification "routers" for enhancing the deterministic gates

**Status: RESEARCH BASIS, not a decision.** Recorded from a 2026-06-26 step-back as the grounded starting
point for FUTURE enhancement of the deterministic gates. No architecture is committed here. Companion to the
ADR-0015 addendum (which records the academic primary-source *dimensions* — Swanson, Lehman S/P/E, Boehm,
Buckley et al. 2005, etc.); this doc records the *operational ISO-standard routers* that would fire concrete
exploration/verification directions during gate enhancement.

## The reframe

`deliverable_kind` (the interface taxonomy: cli/http_service/library/…) is ONE slice — roughly the
test-object/test-level dimension of ISO/IEC/IEEE 29119. The refactor-leaf failure (a rename inferred as
http_service, conformance booted a service it never built) was the symptom of treating that single axis as the
whole model.

The grounded model: a change request flows through a PIPELINE OF ROUTERS, each an established standard that
*deterministically classifies* and *fires the right exploration/verification direction*. The LLM then solves
within the fired direction. This keeps the ADR-0009 boundary: deterministic routing/verification, LLM judgment
for the solution.

## Question → standard

| Question | Standard |
|---|---|
| Why change? (maintenance intent) | ISO/IEC/IEEE 14764 — corrective / adaptive / perfective / preventive |
| What must it satisfy? (requirements) | ISO/IEC/IEEE 29148 |
| Where does it impact? (architecture) | ISO/IEC/IEEE 42010 |
| Which quality to improve? | ISO/IEC 25010:2023 |
| Which code hazard to avoid? | ISO/IEC 24772-1:2024 |
| How to prove it? (test design) | ISO/IEC/IEEE 29119-4 |
| Final source-code gate | ISO/IEC 5055:2021 |

One-word summary: 14764 = Why, 29148 = What, 42010 = Where, 25010 = Which quality, 24772 = Which code hazard,
29119-4 = How to prove, 5055 = Final code gate.

## The candidate routers (what each fires)

- **ISO/IEC 25010:2023 — quality-characteristic router.** 9 characteristics + sub-characteristics. Routes a
  symptom to a quality direction and fires the exploration list: "slow" → performance efficiency (DB access,
  algorithm, I/O, cache, batching, parallelism, resource use); "changing X breaks Y" → maintainability
  (coupling, cohesion, separation of concerns, testability, cyclic deps, change locality); "work sometimes
  dropped" → reliability (exception handling, retry, idempotency, transactions, state transitions, races).
  Pairs with 14764: 14764 = what the change is *for*, 25010 = which quality to *improve*.
- **ISO/IEC 24772-1:2024 — language-vulnerability-pattern router.** Closer to code than the others. Fires
  hazard lenses: input values, numeric ops, type conversion, array/bounds, memory, exceptions, concurrency,
  resource management, initialization, scope, implicit language behavior. Language annexes exist (e.g. TR
  24772-3 for C). With 14764: corrective → the vulnerability pattern of the bug; preventive → the same class
  in surrounding code; adaptive → behaviors that surface under language/compiler/runtime change.
- **ISO/IEC 5055:2021 — source-code-quality-risk router AND post-generation gate.** Detects/counts violations
  of good architectural/coding practice across reliability, security, performance efficiency, maintainability.
  Two uses: pre-code (classify which violation classes the target has → recall improvement patterns);
  post-code (check the generated change introduced no new violations). 14764 routes the maintenance task; 5055
  routes the code-structural risk.
- **ISO/IEC/IEEE 29119-4:2021 — test-design router.** Defines test-design techniques. Stronger than "add a
  unit test": classify the test conditions the change creates (spec, state, input ranges, branches,
  combinations, failure modes), select the technique, generate the cases the technique demands. With 14764:
  corrective → defect-reproduction + regression; adaptive → old-vs-new environment compatibility; perfective →
  performance comparison + quality-characteristic regression; preventive → latent-failure + abnormal-path.

### Design / requirements side
- **ISO/IEC/IEEE 29148:2018 — requirements router.** Turns a vague request ("make search easier", "improve
  logging", "handle errors properly") into requirements / constraints / preconditions / I/O / normal & abnormal
  paths / interfaces / acceptance conditions / traceability. Sits before code generation.
- **ISO/IEC/IEEE 42010:2022 — architecture-impact router.** Fires, before change: which concerns are affected,
  which architecture views to check, which boundaries/dependencies/interfaces change, whether it conflicts with
  existing design decisions, local fix vs structural change. (Large changes: ISO/IEC/IEEE 42030:2019 for
  post-change architecture evaluation.)

### Domain routers (fired conditionally)
- Security: **ISO/IEC 27034-1** (application-security: assets, threats, trust boundaries, controls, evidence,
  roles, lifecycle — higher-level than 24772); **ISO/IEC 30111** (vulnerability handling/remediation — branch
  the security subset of 14764-corrective here).
- Privacy (PII): **ISO/IEC 29100:2024**.
- Data: **ISO/IEC 25012** (15 data-quality characteristics for structured data).
- AI/ML: **ISO/IEC 5259** series (data quality for analytics/ML), **ISO/IEC 5338:2023** (AI lifecycle ≈ 12207
  for AI), **ISO/IEC 25059:2023** (AI quality ≈ 25010 for AI; revision in progress as of 2026-06).

## Recommended pipeline (existing code)

```
Issue / fault / change request
  → 29148  : clarify the requirement
  → 14764  : classify the maintenance purpose
  → 42010  : identify impacted structure & boundaries
  → 25010  : pick the quality characteristic to protect / improve
  → domain routers (27034/30111 security · 29100 privacy · 25012 data · 5338/25059/5259 AI)
  → 24772 + in-repo patterns : explore code-level solution candidates
  → code change
  → 29119-4 : select the verification method
  → 5055    : re-inspect source-code quality
```

## MVP (first to implement, when gate enhancement starts)

Four routers — one full loop "why fix → what to improve → what to guard against while implementing → how to
prove correctness":
1. **ISO 14764** — classify change intent.
2. **ISO/IEC 25010** — classify the quality exploration direction.
3. **ISO/IEC 24772** — explore code-level hazard patterns.
4. **ISO/IEC/IEEE 29119-4** — select the verification pattern.

## Caveats (owner)

- **Do not pin to deprecated clause numbers.** 14764:2022 is based on 12207:2017, which is already superseded
  by 12207:2026. Use stable CONCEPT IDs + the router's own stable classification, not edition-specific clause
  numbers.
- **12207 is a top-level router, not a code-firing one.** 12207:2026 covers acquisition/development/operation/
  maintenance/disposal — too broad to fire code solutions directly; it belongs as the top router that decides
  *which specialist router to call*.

## Mapping to today's engine (where the fragments already live)

- `forbidden_patterns` / `static_checks` ≈ 24772 / 5055 territory.
- `conformance.py` (interface dispatch) ≈ 29119-4 / 29119 test-object territory.
- **Missing routers**: 14764 (Why / change type), 25010 (Which quality), 42010 (Where), 29148 (What). The
  refactor-leaf failure is the visible cost of the 14764 router's absence — change intent was never classified,
  so a perfective refactor was forced into the interface axis.

## Backlog: deterministic code-quality axes — signals INTO Linon, not gates before it

Question raised: are the deterministic code-quality axes (CISQ/ISO 5055, ISO 24772 language-vulnerability
patterns, cyclomatic complexity, Halstead, CK metrics, module coupling / dependency cycles, structural
coverage, dead/unreachable code, duplication, error/resource-handling completeness) worth adding as a GATE
BEFORE Linon?

Conclusion (deferred to backlog, not built now): **mostly NO — feed them to Linon as deterministic SIGNALS
(routers), do not hard-gate before it.** The reasoning, which sharpened over the discussion:

1. These are FINISHED-PRODUCT quality measures. A leaf is a work-in-progress (a step in a larger build), so a
   hard quality gate repeats the finished-vs-WIP category error (the same error that produced the
   refactor→http_service failure).
2. The natural split looked like "incompleteness (WIP-normal, don't gate) vs incorrectness (broken, gate-able)"
   — e.g. dead/unreachable code is normal mid-build (a helper written before a later leaf wires it; gating it is
   harsh), whereas a genuine bug is wrong regardless.
3. BUT the crux is MACHINE-DECIDABILITY, and **incompleteness vs incorrectness is NOT generally machine-decidable**:
   a static check sees the PATTERN's presence, not the INTENT/plan. "Dead code that a later leaf will wire" and
   "orphan dead code" look identical statically; an unchecked return may be "handle later" or a bug. So a
   deterministic gate can decide PRESENCE but not PROBLEM-ness (which needs intent) → gating on presence blocks
   WIP-normal incompleteness → harsh / false-positive → never-converge risk.
4. Therefore these belong as deterministic SIGNALS surfaced to the SEMANTIC judge (Linon), who CAN apply intent —
   i.e. fed INTO Linon as a router, not gated BEFORE it. (This is exactly Linon's role: judgment over deterministic
   signals.)
5. The ONLY true machine-gate sliver: patterns where PRESENCE == broken-if-run REGARDLESS of intent — memory
   safety / undefined behavior / use-after-free. Tiny, language-specific, FP-prone; would need its own
   evaluation before being trusted as a hard gate.

Backlog items (when gate enhancement starts): (a) wire the quality-axis measures as deterministic signals into
the Linon review (router), with suppression/intent left to Linon; (b) evaluate whether a narrow
"broken-if-run regardless of intent" gate (memory-safety/UB class) is machine-decidable and FP-safe enough to
hard-gate; (c) avoid hard-gating any completeness/metric measure on a WIP leaf. (Owner discussion 2026-06-26.)
