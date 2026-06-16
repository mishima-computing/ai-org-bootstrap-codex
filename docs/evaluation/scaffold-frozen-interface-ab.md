# Frozen-interface handoff + deterministic scaffolding — what it actually buys (measured)

ADR-0005 names two mechanisms for shrinking the implementer's decision surface:

- **③ frozen-interface handoff** — the designer emits a boundary as a frozen `.pyi` stub
  (signatures + docstrings, no bodies); downstream receives only that, instead of re-deriving the API
  by re-scanning the repo.
- **④ deterministic scaffolding** — a program (`scripts/controller_scaffold.py`), not the model,
  generates the module skeleton from that stub: exact signatures, imports, docstrings, and
  `raise NotImplementedError` bodies. The carrier then fills only the bodies.

`controller_scaffold.scaffold_module(interface_pyi, target_py)` parses the stub with `ast` and emits the
skeleton deterministically (source order, no timestamps); `scaffold_tests(...)` emits a matching test
skeleton. Both are exercised by the module's hermetic self-test.

## The A/B (honest result)

Same module (`geom.py`: `area_rectangle`, `area_circle`, `bounding_box_area`) built two ways, real
codex carriers:

| Path | wall | honors the frozen interface |
|------|------|------------------------------|
| **A — ③④ scaffold + fill bodies** | 76.9s | **yes** (exact), and functionally correct |
| **B — from a prose description** | 38.9s | **no** — drifted: `bounding_box_area(points: list[tuple[float,float]])` came back as `Iterable[tuple[float,float]]` |

**The surprise, recorded honestly:** ③④ was *not* faster per call — it was slower (77s vs 39s). The
hypothesis "scaffolding makes the call faster" did not hold for a single small module: the from-scratch
carrier writes freely and fast; the scaffold+fill carrier must respect a "change only the bodies"
constraint, which costs a little.

## So what ③④ actually buys

Not single-call latency. Two structural properties:

1. **Boundary integrity.** The downstream *cannot* drift from the frozen interface — it never decides
   the API, so it cannot get it subtly wrong. B's `list` → `Iterable` drift is exactly the kind of
   mismatch that, across a real multi-module build (one engine interface, six game consumers), breaks
   integration and triggers verify/revise loops. ③④ removes that class of rework. The speedup is the
   *avoided revise loop*, not the call.
2. **Parallel enablement.** Freeze the interface first and every downstream contract can be built
   against it *concurrently*, without waiting for the upstream implementation. The measured wall-clock
   win then comes through mechanism ② (parallel independent contracts — measured 2.40x), which ③
   unlocks.

This refines ADR-0005's claim: deterministic scaffolding + frozen handoff shrink the decision surface
(the implementer no longer decides the boundary, so it can't drift), but their payoff is a **system
property** (no drift → no rework; freeze-first → parallel), not a per-call latency reduction. Reach for
them to make boundaries *guaranteed* and downstream *parallelizable*, not to make one carrier call
quicker.
