# ADR-0015: A deterministic operability scan grounds the conservative-designer — and one shared deliverable-kind classifier

## Status

Accepted. Extends ADR-0014 (the Python-cored design-role host): the conservative-designer becomes the second
role whose substrate is built deterministically. Implemented in `scripts/deliverable_kind.py` (the shared
classifier), `scripts/operability_scan.py` (the operability detectors), and the conservative branch +
`format_operability_section` / `inject_operability_evidence` in `scripts/design_host.py`. Tests:
`scripts/test_deliverable_kind.py`, `scripts/test_operability_scan.py`, `scripts/test_design_host.py`.

## Context

The conservative-designer's lens is OPERABILITY: once built, can the result be deployed, run, observed,
bounded, and rolled back. `roles/conservative-designer.md` asks the LLM to *evaluate* an operability surface
and *declare* gaps. But most of that surface is a deterministically-detectable repo fact, and the
design-proposal schema's `continuity` block has fields that are pure retrieval (dependency pinning, the
governing-doc set, the absent-but-decidable checks). The same lesson as ADR-0014's guard-map applies: move the
retrieval to a deterministic scan that fills the substrate, and reserve the LLM for judgment.

One problem blocked a clean design: the conservative-designer runs BEFORE the aufheben-designer declares
`deliverable_kind`, so at design time there is no contract to read — yet the operability lens is kind-dependent
(an http_service must expose a health/readiness signal; a CLI's "readiness" is a clean exit; a library has no
run convention). A web survey of production source-to-deploy systems (Cloud Native Buildpacks `detect`, Heroku
`bin/detect`, Nixpacks/Railpack, Cloud Run, Kubernetes probes) showed the answer is universal and deterministic:
detect the run convention from filesystem signals, race detectors under fixed precedence, first match wins,
carry evidence, and refuse rather than guess when nothing matches.

## Decision

### D1 — The operability scan is the conservative-designer's second substrate

`OperabilityScan` detects the target repo's operability surface (start/run convention, config & secrets — reusing
`secret_scan` — health signal, resource bounds, observability, state/migration, dependency pinning), folded into
the carrier prompt before the carrier runs, exactly like the guard-map. genius/aggressive stay guard-only; the
scan is gated to `conservative-designer` and degrades to guard-only on any failure (it never sinks the stage).

### D2 — Infer `deliverable_kind` deterministically, advisory, first-match-under-precedence

`deliverable_kind.classify_kind(surface)` infers the kind with ordered detectors (rpc → http → batch → cli → json
→ unknown_service_like → library), **first match wins** (CNB/Heroku), carrying evidence. Confidence is **not a
fabricated 0-1 score** (CNB/Heroku emit none) — it is the precedence margin: `high` (explicit deploy config or two
independent signals), `low` (code-pattern only), `unknown` (a different-kind runner-up at least as strongly
evidenced), plus an `ambiguous` flag when a runner-up fired. When evidence is contradictory or absent-but-an-
interface-exists, it returns `undetermined` (the vocabulary `conformance.py` already uses), and a bare listener
that is not demonstrably http/rpc is `unknown_service_like` — never forced into a contract kind.

### D3 — The inferred kind makes the operability gap-list kind-aware

`required_operability(kind)` maps each kind to its mandatory checks. Health/readiness is required only for the
service kinds; a CLI/batch discharges readiness by clean exit / completion (Kubernetes draws the same line —
Jobs have no readiness probe). So `missing_safety_checks` no longer faults a CLI for lacking `/health` — the
false positive that motivated the scan.

### D4 — Advisory at design time, authoritative only at the contract

The inferred kind never overrides the contract. `contract_preflight` / `conformance` stay authoritative on the
aufheben-declared `deliverable_kind`. The same classifier can later be re-run on the surface as a
declared-vs-inferred *consistency* check (a library declared over a bound-port server is a deterministic
contradiction) — one vocabulary, two roles (infer vs verify), the ADR-0009 boundary.

### D5 — Carriage fills the factual continuity fields, never `selected_profiles`

`inject_operability_evidence` overwrites the four FACTUAL continuity fields (version_constraints,
ecosystem_facts_used, forbidden_expansions, missing_safety_checks) with the deterministic facts, preserves the
four judgment fields (safe_change_path, reversibility_plan, knowledge_gaps — and selected_profiles), rebuilds the
block from exactly the eight schema keys, and clamps every value to its schema cap (maxItems, item maxLength 200,
judgment strings 600) so a deep repo path can never make the deterministic overwrite schema-invalid. It does
**not** fill `selected_profiles`: that is a plain string list authorization-validated against authorized profile
ids, so an inferred-kind candidate cannot ride in it — the inferred kind goes into `ecosystem_facts_used`.

## Consequences

- The conservative-designer reasons against detected operability facts and a kind-scoped required-set, instead of
  guessing the surface or over-reporting gaps. Same waste-reduction as the guard-map, same safety boundary (Linon
  + preflight unchanged).
- The classifier is reusable across the pipeline (design-time inference and a future consistency gate).
- The work was built and then reviewed by two independent carriers, which caught a schema-overflow blocker (long
  paths), a bare-`PORT` mis-detection that misclassified libraries, an unimplemented secret-scan reuse, and
  several detector-precision bugs — all fixed, with lock tests.

## Known follow-up (pre-existing, out of scope)

`scripts/profile-evidence-check.py` validates a TOP-LEVEL `proposal.selected_profiles`, but the design-proposal
schema only allows `continuity.selected_profiles` (top-level additionalProperties is false). So selected-profile
authorization is currently inert for design proposals — a pre-existing inconsistency, independent of this change,
to fix separately.

## Test status

`scripts/test_deliverable_kind.py` — precedence, the kind-aware required-set (health is service-only), confidence
tie → unknown, the unknown_service_like / undetermined refusals. `scripts/test_operability_scan.py` — http vs cli
surface + kind, a CLI is never faulted for health, the continuity_prefill excludes selected_profiles, a
REPORT-mentioning library stays a library (the PORT-boundary fix). `scripts/test_design_host.py` — conservative
gets the operability substrate folded in and schema-valid, genius/aggressive stay guard-only, and a long path is
clamped so the injected packet stays schema-valid. Full suite green; the `validate` discovery + Codex-only
residue check pass.

## Addendum (2026-06-25): `deliverable_kind` is ONE axis — verification is multi-dimensional (primary-source grounding for a step-back)

A real run exposed the limit of this ADR's model. A leaf whose entire task was a RENAME/REFACTOR (it produces no
new interface, only changes existing code) was inferred — deterministically, from the **whole-repo** operability
surface — as `http_service`; conformance then tried to boot a service the leaf never built
(`uvicorn app.main:app` → exit 127), so the leaf could never converge. The deterministic inference was
*consistently* wrong for this class, because `deliverable_kind` answers only "what interface does the artifact
expose" (the source-to-deploy / Cloud Native Buildpacks lens), while the leaf's real deliverable was a
**behavior-preserving change**. This is a CATEGORY CONFLATION, not a tuning bug.

`deliverable_kind` is one axis (the deliverable/interface axis). Verifying software has several established,
ORTHOGONAL dimensions. Dug from the ORIGINAL sources (not secondary summaries):

| Dimension | Categories | Primary source |
|---|---|---|
| Maintenance purpose/type | corrective / adaptive / perfective / preventive | Swanson, "The Dimensions of Maintenance," ICSE 1976 → ISO/IEC/IEEE 14764 |
| Evolution / system class | S-type (correctness vs a fixed spec) / P-type (validity vs a problem model) / E-type (coupled to a changing world, must keep evolving) | Lehman, "Programs, Life Cycles, and Laws of Software Evolution," Proc. IEEE 1980 |
| V&V reference criterion | verify (vs spec/design — "build it right") / validate (vs user need — "build the right thing") | Boehm |
| Lifecycle / artifact phase | requirements … design … implementation … integration … verification … validation … operation … maintenance … disposal | ISO/IEC/IEEE 12207; IEEE 1012 |
| Testing level / object | unit / integration / system / acceptance; test object (SUT) | ISO/IEC/IEEE 29119 |
| Testing technique basis | specification- / structure- / experience-based | ISO/IEC/IEEE 29119-4 |
| Change taxonomy facets (explicitly multi-axis) | temporal properties / object of change / system properties / change support | Buckley et al., "Towards a Taxonomy of Software Change," 2005 |
| Maintenance ontology (entities) | change request / product / activity / maintainer / environment | Kitchenham et al., 1999 |

Notes from the primaries: Swanson's original is a SINGLE-axis maintenance-type typology, not a broad taxonomy.
**Buckley et al. 2005 is the explicit MULTI-facet taxonomy of change** (the clearest "how many axes" answer).
Chapin et al. 2001 re-bases change classification on *observed change evidence* rather than maintainer intent.
Lehman's S/P/E is a distinct "what is correctness judged against" dimension that maps onto the
finished-product-vs-evolving question this run surfaced.

Implication (recorded for the step-back, NOT yet decided): a refactor/rename leaf is "perfective" maintenance
(Swanson), whose verification target is behavior preservation + regression + change-impact + characterization
(Fowler; Rothermel & Harrold; Bohner & Arnold; Feathers) — NOT interface conformance. The engine already owns
the kind-agnostic pieces (`forbidden_patterns`, `regression_suite`, `static_checks`). Whether to add a
maintenance/`change_kind` axis, lean on the existing `none`, or restructure more deeply is deferred to the
step-back; this addendum records the grounding so that redesign starts from the primary literature, not from
scratch.
