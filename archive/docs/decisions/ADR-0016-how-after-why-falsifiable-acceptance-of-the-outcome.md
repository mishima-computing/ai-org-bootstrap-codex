# ADR-0016: How after Why — the implementation is untrusted until a *falsifiable* acceptance proves it serves the intent

## Status

Accepted (the principle and its gates; the engine wiring is forward work). Refines ADR-0009 (the verification
boundary — design chooses an *executable* contract, deterministic machinery enforces it), ADR-0011 (a small
trusted kernel over untrusted generators — unproven never passes), and ADR-0012 (the synthesizer is a proposer,
not an authority). This ADR governs the **engine only** — the relationship between a goal's intent, the
implementation a role produces, and the verification that gates it. It deliberately makes no reference to any
downstream product or operator; those compose the engine, they do not appear in it.

## Context

A goal carries a **WHY** (the intent — the outcome the goal-setter wants in the world) and is realized by a
**HOW** (the implementation a role produces). Two failures recur, and they are the same failure seen from two
sides:

1. When the goal over-specifies the HOW (dictates the mechanism), an error in that dictated mechanism propagates
   — the role faithfully builds the wrong thing, and the verification, if it tests the mechanism's internals,
   passes anyway.
2. When the acceptance test exercises a *proxy* (a pure helper, a mock, an internal symbol) instead of the
   user-visible outcome, it stays **green while the real outcome is broken**. In mutation-testing terms this
   acceptance is a *survived mutant*: it does not detect the defect it claims to guard.

The unifying principle from requirements engineering (Jackson–Zave: a specification S is correct only relative
to requirements R and domain knowledge K — `K ∧ S ⊨ R`) is: **a HOW can only be judged relative to a WHY, and
the WHY must be explicit first.** "How after Why." A skeptical check of the HOW is meaningless without an
explicit, *executable* WHY to be skeptical against — which is exactly ADR-0009's executable contract, now made
**falsifiable**: a check that cannot fail when the WHY is unmet proves nothing.

## Decision

### D1 — A *raw* goal is refined into a *structured* goal (WHY + falsifiable acceptance) at intake; the HOW is derived and untrusted

What the engine receives is a **raw goal** — free text. Before any decomposition, the engine refines it into a
**structured goal**: the **intent + the outcome it must produce**, turned into an executable acceptance that
exercises the **outcome a consumer observes**, not the implementation. The HOW (which mechanism, which path,
which symbols) is the role's to choose and is **untrusted** (ADR-0011). The structured goal SHOULD state
ground-truth facts and hard constraints the role cannot easily discover, but SHOULD NOT dictate the solution
steps — a dictated HOW propagates the author's error and suppresses the role's discovery.

### D1b — Sufficiency gate: a structured goal is sufficient only when a falsifiable acceptance can be NAMED; else the engine REQUESTS, it does not guess

Refining raw → structured is itself gated, and the gate is **concrete, not reviewer taste**. A *candidate*
structured goal is **sufficient** only when one can name:
(a) the observable, **consumer-visible outcome**;
(b) a **falsifiable success condition** over it;
(c) a **negative control** the acceptance must reject — the D2 precondition: if you cannot state what would make
    the check go *red*, there is no check;
(d) the **owner/oracle** of correctness.
If any of (a)-(d) is unnameable, the raw goal **underdetermines** the structured goal: the engine **requests the
missing information or HOLDs** — it does NOT silently fabricate the WHY (choosing *ends*, forbidden by D5).

This gate proves **checkability, never intent-correctness**: a candidate that satisfies (a)-(d) may still encode
a *wrong* WHY — that is D5's residual, owned by the goal-setter. A **consumer** may pre-complete the missing
parts before submission so its own users are not blocked; the completion, and the risk of completing the WHY
*wrong*, belong to the consumer, not the engine. The engine's contract is only: a candidate structured goal that
**cannot name a falsifiable acceptance** must not proceed to decomposition.

### D2 — The falsifiability gate: the acceptance must FAIL on a negative control and PASS on the change

An acceptance is admitted as a guard only when it is shown to **fail on a negative control and pass on the
change** — the "kill the mutant" / red-then-green discipline. An acceptance that is green on the negative
control is a **fake guard** and is rejected; the role is sent back to produce a check that actually exercises
the outcome. A check that never demonstrably failed is not trusted (ADR-0011's "unproven never passes",
applied to the *check itself*).

The negative control must always exist, including in greenfield work where there is no natural "defective"
prior state: it is the pre-change state for a repair, and otherwise a **withheld counter-example / no-op /
injected mutant the acceptance MUST reject**. Without a required negative control, a fake guard slips through.

Crucial limit: red-then-green proves the check is **live** (it can fail), NOT that it targets the **right
observable**. A check can be red-then-green while exercising a *proxy correlated with* the outcome — green for
the wrong reason. The falsifiability gate cannot close this "right-target" gap; it is backstopped only by the
adversarial trace (D4) and ultimately by the goal-setter's judgment (D5).

### D3 — The acceptance is authored OUTSIDE the role under test (the HOW is adversarial toward the test)

Under optimization pressure a role does not merely produce an untrusted HOW — it is **adversarial toward the
acceptance itself** (it can weaken the test, special-case the green, or redefine the term). So the acceptance's
trust root must live **outside the generator that writes the implementation**: the role under test may neither
author nor edit the acceptance that gates it. This is ADR-0011's kernel/generator separation hardened into a
boundary, and ADR-0009's executable contract treated as **immutable to the party it judges** — not a layering
nicety but a trust boundary.

### D4 — An adversarial trace tries to REFUTE that the HOW achieves the WHY

Beyond the deterministic gate, an **independent skeptic** attempts to refute "this mechanism achieves the WHY"
by tracing it end-to-end before the artifact is accepted. Independence is load-bearing: the skeptic must not
share the context/seed of the role that produced the HOW, or its holes correlate with the producer's. Its
honest claim is bounded — *"the mechanism is consistent with the stated WHY"* — never *"the WHY is right"*
(D5). This is the independent V&V / red-team form of review. NOTE on readiness: ADR-0009 currently *defers*
exactly this judgment-half skeptic (the contract-vs-goal critic) as telemetry-gated, role-layer work — so D4
is forward work. **First increment wired** (2026-06-22): the goal-level **shadow acceptance obligation** (D7) —
a `goal_acceptance` needs-info record marking a `done` goal's composed outcome as *unverified against the WHY*
(never a fabricated green). **Still forward work (NOT yet built):** the judgment-half itself — a **WHY-bound
Linon** (the least session-correlated reviewer the engine already isolates) and a **shadow contract-vs-goal
critic** in the pre-implementation slot, both advisory until effective-FP ~0 — and the **executable** goal-level
acceptance run. Until they land, the right-target gap (D2) and the goal-setter (D5) remain the backstops.
Placement is the **composing layer**, per D7.

### D5 — The harness makes an UNMET stated WHY fail; a WRONG WHY it can only surface, never catch

Distinguish two cases. An **unmet** WHY — the artifact fails to satisfy the *stated* intent — is mechanically
fail-able by the falsifiable acceptance (D2). A **wrong** WHY — the stated intent is itself not what the
goal-setter truly wanted — is NOT mechanically catchable: the correctness of the WHY is owned by whoever sets
the goal (the oracle problem — the true outcome lives in the goal-setter's head; no machinery recovers an
unstated or wrong R, and an artifact that satisfies the wrong acceptance ships green). The engine's job is
therefore to (a) make an unmet stated WHY **fail the falsifiable acceptance**, fast and visibly, and (b)
**surface decision-relevant ambiguity for the goal-setter to resolve, never silently choose the goal-setter's
utility function.** A role may choose *means*; it must not choose *ends*. A wrong WHY is the goal-setter's
residual risk; the engine can only make it cheap to discover (fail fast) and reverse, not catch.

### D6 — This is a V&V stack, not one gate; the layers must be genuinely independent

WHY (validated by the goal-setter, D5) → falsifiable outcome-acceptance (D1/D2, authored outside the role D3,
run on the real outcome) → untrusted HOW → adversarial trace (D4) → goal-setter judgment. It is defence in
depth; it only works if the layers are independent. **If one generator authors the HOW, the acceptance, and the
trace, the holes align and the stack is solid — the separation in D3/D4 is what makes the depth real.**

### D7 — The WHY is verified at the COMPOSING layer; a per-leaf check proves leaf-obeys-contract, not goal-obeys-WHY

When a goal is decomposed into leaves, its WHY is verified where the outcome actually exists — at the
**composing (goal) layer**, on the merged artifact — not inside each leaf. The reason is structural: the goal's
outcome/`negative_control` is authored once, **outside every leaf's dialectic, by the intake refiner** (the D3
trust root, upstream and for free), and it is observable in at most the one or two leaves whose interface
exposes it. Pushing a slice of it into every leaf collapses to a self-served *"this leaf doesn't observe it"*
toggle (a renamed right-target gap) and a per-leaf gate that almost never fires. So the division is:

- the **per-leaf** conformance gate proves only that the leaf **obeys its own contract** (ADR-0009) — it does
  not pretend to prove the goal;
- a single **goal-level acceptance run** on the merged artifact proves the **composed outcome** — the goal's
  `negative_control` must go **RED, then GREEN** on the change (D2), reusing the existing conformance harness.

When the goal outcome is **not cheaply executable**, that run is **advisory / needs-info — never a fabricated
green** (D5). This is D6's "the outcome lives at the composing layer" made concrete; it adds no new role and no
acceptance-authoring generator (the trust root is the already-upstream refiner). The honest residual: a
goal-level red-then-green proves the composed acceptance is *live and targets the stated `negative_control`*,
never that the `negative_control` itself is the right one — that is the goal-setter's residual (D5), backstopped
by the D4 critic.

## Consequences

- A localized authoring error in a goal's *HOW* no longer silently propagates: the role owns the HOW and the
  falsifiable, outside-authored acceptance catches a HOW that does not produce the outcome.
- Extends "unproven never passes" from the artifact to the **HOW-choice and to the check itself.**

## Risks (named, not solved)

- **Outcomes that are not cheaply executable** (taste, feel, aesthetics, some non-functional ends) have no cheap
  oracle; the falsifiability gate degenerates to a proxy there. Such outcomes must fall back to the goal-setter's
  judgment (D5), not a fabricated green. This is the load-bearing limit.
- **A grounded-but-wrong canon** amplifies a false premise with convincing confidence — grounding is only as good
  as the ground truth; the canon is not infallible.
- **Equivalent mutants are undecidable**, so some defects are unfalsifiable in principle; a suspiciously-easy
  green is a signal for the adversarial trace, not a pass.

## Prior art (so this is recognized as composition, not invention)

WHY-first = validation / Goal-Oriented Requirements Engineering / Specification-by-Example; outcome-not-
implementation = behavioral/black-box acceptance; the falsifiability gate = mutation testing ("kill the mutant",
the survived-mutant gap) + the red phase of TDD; D3 = the reward-hacking / specification-gaming result that a
generator games the test it can reach; D4 = independent V&V / red-team / metamorphic testing; D5 = the oracle
problem. This ADR composes them at the engine's verification boundary.
