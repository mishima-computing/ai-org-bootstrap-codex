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
