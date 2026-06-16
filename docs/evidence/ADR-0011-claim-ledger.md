# ADR-0011 Claim Ledger

This ledger tracks retained measured, proven, validated, already-working, live-compatible, or
cost-established claims from ADR-0007 through ADR-0010. Under ADR-0011, each claim must point
to a committed artifact or be labeled **Hypothesis**. Three grades are used: **Hypothesis** (no
committed artifact), **Evidenced (harness external)** (the raw measurement data is committed and
inspectable here, but full re-execution needs a sibling-repo harness per ADR-0012's plane boundary;
honestly noted with sample size), and **Rejected wording** (the claim was removed/replaced). The goal
is better, honest ADRs — ground what is real, hedge only what is genuinely unproven; do not blanket-hedge.

| Claim | ADR | Evidence status |
| --- | --- | --- |
| A motivating session used chat steering rather than hand-editing as the owner-facing action. | ADR-0007 | **Hypothesis.** No committed replayable artifact in this repository. |
| The cockpit components -- map, org dispatch, Linon review, growth view, and role/cost substrate -- have all been validated together. | ADR-0007 | **Hypothesis.** ADR-0007 now states the integration direction and does not claim live integrated validation. |
| The deterministic codebase-city layout is already secured as a pure function of the repo. | ADR-0007 | **Hypothesis.** No committed replayable artifact in this repository. |
| Existing map, review, growth view, role-timing, and org-dispatch pieces can be bound into a live cockpit without a new product primitive. | ADR-0007 | **Hypothesis.** Pending implementation and live verification. |
| A sibling edition was built by this org, so one org composed another org. | ADR-0008 | **Hypothesis.** External/pending evidence; no committed replayable artifact in this repository. |
| `ai-org-tools` and Corps are referenced as files or packages present in this repository. | ADR-0008 | **Rejected wording.** ADR-0008 now identifies them as external/sibling references, not in-repo files or packages. |
| Lower-trust carriers can be safe under containment, logging, and verification. | ADR-0008, ADR-0012 | **Hypothesis** until a committed containment design, log bundle, and ADR-0013 live verification artifact exist. ADR-0012 is the policy boundary, not proof. |
| Swapping an agent can be checked as runtime-compatible by Linon alone. | ADR-0009 | **Rejected wording.** ADR-0013 separates static Linon from live smoke/battery verification. |
| Parallel carriers produced 2-3 effective lanes in the motivating work. | ADR-0009 | **Hypothesis.** No committed replayable measurement artifact in this repository. |
| Role-level timing was 45% implementer, 30% Linon, and 24% conservative-designer. | ADR-0006, ADR-0007 | **Evidenced (raw data committed; harness external, n=1).** `docs/evidence/role-timing-and-pipelining.md` + `docs/evidence/data/role-timing-serial.json` reproduce 45/30/24. |
| Build/review pipelining reclaimed 23.3% wall clock and hid 77% of Linon time. | ADR-0006, ADR-0007 | **Evidenced (raw data committed; harness external, n=1).** `docs/evidence/role-timing-and-pipelining.md` + `data/role-timing-serial.json` + `data/role-timing-pipelined.json` (4074s → 3124s). |
| The cone-recall experiment rejected dependency-cone scoping. | ADR-0006 | **Evidenced, with a documented confound.** `docs/evidence/cone-recall-experiment.md` + `data/cone-recall-summary.json`. The rejection rests on *unenforceability* (sandbox read leak) and *against-grain* (broad scan load-bearing), not on a clean recall delta; n=1 per cell. |
| Tokens, compute, or API rate ceilings are the actual cost driver. | ADR-0010 | **Hypothesis.** Pending committed replayable cost measurement. |
| Cross-carrier unit economics allow cheaper contained carriers to widen Gem generosity. | ADR-0010 | **Hypothesis.** Pending committed carrier-cost comparison and live containment evidence. |

## Pending Evidence Stubs

- Live runtime compatibility evidence stub: `docs/evidence/ADR-0013-live-smoke-battery-stub.md`.
- Any future measurement artifact must be committed under an evidence path and referenced
  from this ledger before the related claim is promoted out of **Hypothesis**.
