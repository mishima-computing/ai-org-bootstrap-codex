# ADR-0013: Incremental repair via a deterministic contract-patch — and the audit lives beside the contract

## Status

Accepted (the patch unit is built and tested; wiring it into the live repair loop is forward work). Refines
the repair loop under ADR-0009 (the executable contract + deterministic pre-flight), ADR-0008 (budget follows
the information's importance), and ADR-0011 (untrusted generators over a small trusted kernel — unproven never
passes). Implemented in `scripts/contract_patch.py` with `scripts/test_contract_patch.py`.

## Context

Repair is the org's most expensive activity (~40% of steps; the implementer + Linon review dominate the
role-time profile). The loop already has two cost-savers: producers/implementer **resume their prior codex
session** on a repair iteration (a small delta, not an amnesiac re-derivation), and repair routing re-runs
**only the roles a finding implicates** (a pure-artifact defect re-runs the implementer alone). But for a
**contract-level** finding — one the deterministic `contract_preflight` raises against the aufheben contract
itself — the default move is still to re-run aufheben and **re-synthesize the whole contract**. That is
wasteful and unsafe: a re-synthesis is a full design re-run, and it can silently **drift fields the finding
never touched**.

Yet a large subclass of contract-preflight findings carry, in their structured detail, **exactly what to fix
and where**: `conformance_profile` names the `kind` that owes a profile; `self_overlapping_scope` names the
exact `allowed` and `forbidden` globs that collide; a missing `deliverable_kind` is unambiguous when the
contract already carries a sole substantive profile. For these, regenerating a 95%-correct contract is the
wrong tool.

## Decision

### D1 — A deterministic contract-patch is a repair tier BELOW re-synthesis

Add a tier between "resume a role's session and re-run it" and "re-synthesize the contract":
`patch_contract(contract, findings)` applies a **targeted, LLM-free** edit to **only the implicated field** of
a deterministically-patchable contract-preflight finding. No carrier, no drift. The tiers, cheapest first:

1. session-resumed role re-run (delta on a cached session);
2. **deterministic contract-patch (this ADR)** — for findings whose fix is fully determined by their detail;
3. full aufheben re-synthesis (the fallback).

This is "budget follows information" (ADR-0008) taken to its limit: a small, localized finding earns a small,
localized fix.

### D2 — Only patch what the finding fully determines; everything else escalates

A patcher acts only when the finding's structured detail fully fixes the field, and **declines (escalates)
otherwise**:

- `conformance_profile` → insert a minimal schema-valid, preflight-clean **stub** for the declared kind;
  never clobber a profile already present (a stale finding declines).
- `self_overlapping_scope` → drop **only** the one over-broad `forbidden` glob the finding names; preserve
  every other rule.
- `deliverable_kind` → derive **only** when the contract carries exactly one substantive conformance profile
  (the kind is then unambiguous); zero or many profiles is a judgment call → escalate.
- `acceptance_criteria` → **always escalate** for the real finding. What a build must satisfy is the heart of
  the contract's judgment; fabricating a placeholder criterion clears the check while asserting nothing true,
  which is worse than escalating. (Applied only in the narrow case where the finding *itself* supplies a
  concrete criteria list — then it is deterministic, not invented.)

When **no** finding is deterministically patchable, `patch_contract` returns `None` for the contract — the
signal to escalate to re-synthesis.

### D3 — The patch proposes; the deterministic checker confirms (verify-loop)

`patch_contract` is **pure**: no I/O, and it does **not** re-run preflight itself. The caller re-runs
`contract_preflight` on the patched contract to confirm the finding cleared. The "did it work" judgment stays
with the deterministic checker, never with the patcher (ADR-0011: unproven never passes). A stub that fails to
clear simply escalates — the patch can only help, never silently launder a defect.

### D4 — The audit delta is RETURNED SEPARATELY (neither in-band on the contract nor on a subclass attribute)

`patch_contract` returns `(patched_contract, audit)`. The `audit` —
`{"applied": [<delta with before/after>...], "skipped": [{check, reason}...]}` — is a plain dict the caller
persists into provenance (the ADR-0009 `spec_derivation`), where "how the spec was made" belongs. It is **not
attached to the contract.**

This is the load-bearing decision, and it was reached by building the feature **twice independently with two
different carriers and exchanging the code.** Each implementation chose a different home for the audit, and
each independently found the other's choice broken:

- an **in-band key** (`contract["_contract_patch"]`) is JSON-durable but makes the contract **schema-invalid**
  — the implementation-contract schema is `additionalProperties: false`, so the patched contract fails the
  very gates this system runs;
- an **out-of-band attribute** on a dict subclass is schema-pure but is **lost across `json.dumps`+reload and
  `dict()`** — the audit silently vanishes the moment a contract is persisted or transmitted as JSON, which it
  always is here ("the log is the state source").

Returning the audit as its own value satisfies **both** constraints at once: the contract stays schema-valid
**and** the audit is an ordinary JSON-serializable dict depending on neither an in-band key nor a fragile
attribute. The two flawed options are a genuine dilemma (durability vs schema-purity) that neither
implementation saw alone; the third option dissolves it — a small live instance of the ADR-0012 thesis (hold
the tension, then find the property that resolves it rather than collapsing to one side).

## Consequences

- Cheaper, safer contract-level repair: a localized contract defect costs a deterministic field edit, not an
  aufheben re-run, and cannot drift unrelated fields (the input is never mutated; only the implicated field
  changes).
- The promoted preflight gate (ADR-0009, now `block`) is the trigger source: its block findings carry the
  structured detail the patchers consume.
- Forward work: wire `patch_contract` into `controller_pipeline`'s repair loop **before** the re-synthesis
  fallback — patch, re-run preflight, and on success skip the aufheben re-run; persist `audit` into the leaf's
  provenance. The CEGAR-style counterexample classification (ADR-0011 forward) is the natural front-end (it
  decides "contract defect vs artifact defect" before routing to the patcher).

## Test status

`scripts/test_contract_patch.py` — 8 tests: each patcher clears its finding under a real `contract_preflight`
re-run (the verify-loop); an ambiguous `deliverable_kind` and the real `acceptance_criteria` finding escalate;
the patched contract is schema-pure (no in-band key, a plain dict) and both it and the audit survive a JSON
round-trip; the input contract is never mutated and unrelated fields do not drift.
