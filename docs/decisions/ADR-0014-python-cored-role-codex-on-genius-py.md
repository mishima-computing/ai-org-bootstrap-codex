# ADR-0014: A role's spine is Python; the carrier is a class behind an interface — "Codex on genius.py", not "Codex as genius"

## Status

Accepted (genius is the first instance; the host generalizes to aggressive/conservative-designer — all three
are wired). Refines ADR-0009 (the executable contract + deterministic pre-flight), ADR-0011 (untrusted
generators over a small trusted kernel — unproven never passes), and ADR-0012 (the generating roles are
proposers, not authorities). Implemented as the shared host `scripts/design_host.py` + the deterministic
`scripts/pre_localizer.py`, wired in `scripts/controller_run.py` as a `carrier_runner`. The first skeleton
(`scripts/genius.py`) was reviewed by two independent carriers and rebuilt as PLAN A after they converged on
its blockers (see Revision).

## Context

A design role today **is** its carrier: the controller hands `roles/genius.md` to a Codex carrier and the
carrier *is* the genius — the whole role lives inside the LLM. The genius's own 6-step contract (intake →
localize → hypothesize → score → verify → handoff) charters it to "localize evidence by file, symbol, module,
**decision (= ADR)**, run artifact" — i.e. grounding a proposal in the repository's actual law is the genius's
defining work.

But the genius can only localize what is **in the compact substrate the controller hands it**, and its charter
**forbids search-first exploration** (retrieval is limited to named-interface ground truth). So when the
repository's binding constraints are *not* in the substrate, the genius is blind to them **by charter** — not
because the model is weak.

This showed up as waste downstream. A live example: `cockpit/clay/seller-dashboard.test.js` is a **structural
guard** — it pins the exact text and ordering of `index.html`'s `<script>` tags and the presence of specific
strings in `clay-live.js`. A chat-view objective *must* edit that guarded region, and the design roles, ungrounded
in the guard, repeatedly proposed the conventional polling-rebuild-fail-soft shape (and reformatted/reordered the
pinned tags). Linon — the trusted deterministic kernel — correctly rejected each one, but only **after** a full
design→implement→review cycle. The kernel guarantees *safety*; the repeated rejection is pure *waste*, and its
root is a generating role that never received the constraint its own job depends on.

"Make the designer smarter" is the wrong lever (a genius ignorant of the guard still trips it). The right move is
to **give the role's spine to Python**, so the deterministic inputs the role's charter assumes are present **by
construction**, and the LLM is reserved for judgment.

## Decision

### D1 — Invert the host: the carrier runs *under* the Python role, not the other way around

The role's identity moves from the LLM to a Python host. `Genius` (`scripts/genius.py`) is the role; a carrier is
a dependency it calls for the judgment slots only. **"Codex on genius.py", not "Codex as genius."** The
controller invokes `genius.py`; `genius.py` invokes the carrier.

### D2 — The carrier is a class behind an interface; Codex is one implementation

`JudgmentCarrier` is an interface — `run(prompt, *, sandbox, session) -> CarrierResult`. `CodexCarrier`
implements it by wrapping the existing `carrier_harness.run_carrier` (`carrier_harness.py:416`), inheriting the
no-output-timeout watchdog and post-exit drain unchanged, and preserving session reuse (genius ∈
`SESSION_REUSE_ROLES`) via the resume argv. The carrier is **injected**, so the deterministic spine is
carrier-agnostic — Codex and a future second-carrier impl swap without touching it. The role's spine never
re-implements carrier mechanics.

### D3 — A deterministic class populates the substrate BEFORE the carrier runs (the load-bearing decision)

`GuardScan` builds a **guard-map** over the objective's declared target files (deliverables /
`files_allowed_to_change`): the tests that assert on those files **and their pinned assertions** (e.g. "index.html
script order", "clay-live.js must contain `/api/goal`"), the ADRs/design docs that govern them, and the existing
exports the new code must not clobber. The guard-map is folded into the carrier prompt and into the schema-required
`repo_evidence` / `substrate_inputs` fields.

This is deterministic **at invocation**, which is the whole point. A guard-scan offered as a *tool the carrier may
call* re-introduces a non-deterministic gate — "will it call it, with the right args?" — in front of a
deterministic tool, and collides with the genius's no-search-first charter. Running the scan as a fixed Python
step before the carrier makes the guard-map **always present**, never discretionary.

### D4 — A deterministic class validates the carrier's output AFTER it runs

`PacketValidator` checks the returned packet against `schemas/genius-packet.schema.json`, enforces the output
budget and evidence-pointer requirements, and re-runs the carrier deterministically on a violation. "Unproven
never passes" (ADR-0011) stops being a hope pinned on a verbose role and becomes an enforced Python boundary.

### D5 — Python owns the spine; the LLM owns only judgment

The split is strict. **Python (deterministic):** guard-map, substrate assembly, schema/budget/evidence
enforcement, session + hang handling (via the carrier). **Carrier (judgment):** localize / hypothesize / score —
the irreducibly non-deterministic grounding the genius exists to do. The Python never authors judgment (that would
be dumbing); the carrier never re-derives the deterministic spine (that is what kept failing). Mechanism in the
host, minds behind the interface.

## Consequences

- The generating role receives the constraints its charter assumes, by construction — born-compliant proposals
  instead of post-hoc Linon rejection. Waste (full review cycles spent re-discovering a readable guard) drops;
  safety is unchanged (Linon still the kernel).
- Carrier-agnostic by construction: the dual-carrier stance becomes a one-line injection, not a fork.
- First instance of a **Python-cored role host**. The shape (interface + deterministic spine + injected carrier)
  generalizes to the other design roles, but is proven on genius before extraction to a shared host.
- `roles/genius.md` keeps governing the *judgment* sub-prompt; the Python enforces the contract *around* it.

## Revision (PLAN A, after the two-carrier review)

The first skeleton assumed the touched files were known at genius-time; they are not (aufheben decides the
write-scope downstream), so its guard scan was a no-op on a normal objective. Two independent carriers converged
on that and four other blockers. PLAN A fixes them: (a) a deterministic `PreLocalizer` maps the objective + a
cheap `RepoIndex` of the TARGET repo (with reference-graph propagation) to candidate touched files, so a
no-literal-path objective ("add a live chat view to the seller dashboard") still surfaces `index.html`; (b) the
host is wired as a `carrier_runner` into `controller_workflow.run_contract`, so workflow keeps owning the cache,
the schema gate (`controller_output.gate_output`, full jsonschema), journaling, and the `ControllerRunReport`
(the return-shape blocker is gone); (c) the guard-map is carried as SCHEMA-VALID evidence (genius `repo_evidence`
items; designer string arrays) plus a full `guard-map.json` artifact, re-gated after injection; (d) org assets
resolve under `org_root`/`AI_ORG_ROOT` while the scan reads the target `--repo`; (e) the carrier output file is
unlinked before each run and `result.ok` is required before validation.

## Review fixes and known follow-ups

A second two-carrier review (of the implementation) converged on a cache BLOCKER and several MAJORs, now
fixed: the runner returns `ok=False` on a final schema failure and **cache is disabled for the design roles**
(run_contract stores before the output_gate, and a replay carries neither `session_id` nor the guard-map
artifact — so a cached design stage could replay a schema-failed packet, lose session-reuse, and dangle the
evidence pointer); transport-empty and schema-fail now share **one** retry loop with `retries=0` per launch
(no compounding) and aggregated attempts in per-attempt log subdirs; a missing/unsalvageable `result.json` is
retried, not failed immediately; `RepoIndex` is memoised per `(repo, HEAD)` so the three design roles share one
full-tree scan.

A third review (of these fixes) confirmed the BLOCKER is resolved end-to-end and converged on two more, also
fixed: a final in-runner schema failure now surfaces its gate errors into `unresolved_failures` (it was
mislabeled as a transport hang because `ok=False` skips workflow's `output_gate`); and `RepoIndex.cached` keys
on a `git status --porcelain` digest as well as HEAD, so a later leaf that dirties the tree under an unchanged
HEAD cannot be served a stale index. (A suspected repair-iteration guard-map degradation was **refuted**: both
`_prompt` and `_delta_prompt` embed the original objective every iteration, so the pre-localizer always sees
it.)

Open follow-ups: (1) GuardScan's basename fallback can cross-bind guards in a repo with many same-named files —
prefer full-path/same-dir resolution and mark basename hits low-confidence; (2) re-enable the cache for design
roles once the bundle carries `session_id` + the guard-map artifact and stores only post-gate.

## Test status

`scripts/test_pre_localizer.py` (3) — a no-literal-path objective surfaces `index.html` via reference-graph
propagation; a literal path ranks first; output is deterministic. `scripts/test_design_host.py` (6) — GuardScan
returns the seller-dashboard structural guard with per-target pins (alias-tracked); guard-map injection keeps the
packet schema-valid for both the genius packet and the design-proposal (real jsonschema gate); the carrier_runner
folds the guard-map into the prompt BEFORE the carrier runs, writes `guard-map.json`, injects evidence into
`result.json`, re-prompts deterministically on a schema-gate failure, and reports transport failure as not-ok. An
injected stub carrier substitutes for Codex (carrier-agnostic).
