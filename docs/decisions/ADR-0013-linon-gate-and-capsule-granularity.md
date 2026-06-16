# ADR-0013: Linon gate and capsule granularity

## Status

Accepted. (Verification model for Linon claims and capsule/core architecture.)

## Context

ADR-0008 made Linon the registration gate and ADR-0009 made code buildings metabolize
through cores and capsules. Two ambiguities remained:

- A static Linon review can inspect files and contracts, but it cannot prove live runtime
  compatibility.
- Capsule granularity was left open between symbols, fixed LOC chunks, and whole modules.

## Decision

Use **one verification gate** with **two regimes**:

1. **Static Linon regime:** verifies static claims about files, contracts, dependencies,
   review findings, and repository evidence.
2. **Live smoke/battery regime:** verifies runtime compatibility by executing the relevant
   live path or a documented smoke/battery for the claimed composition.

No static Linon result may be described as proving live runtime compatibility. Static review
can authorize registration for static properties; runtime claims require the live regime.
The current live-regime placeholder is `docs/evidence/ADR-0013-live-smoke-battery-stub.md`.

Set capsule granularity as follows:

- **Default capsule:** a symbol-level implementation cell, meaning a function or class.
- **Core:** the module's public API, contract, and address.
- **Fallback capsule:** a fixed LOC chunk or whole module only when parsing cannot identify
  functions or classes reliably.

## Consequences

- ADRs and marketplace listings must say whether evidence is static or live.
- A composition that only passed static Linon can be described as statically reviewed, not
  runtime-compatible.
- The codebase-city's building model has a stable default: public API/contract/address as
  core, functions/classes as capsules.
- Coarser capsules are an implementation fallback, not the conceptual model.
