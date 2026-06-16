# ADR-0007: The cockpit — editing is demoted; the codebase-city is the human interface to the AI org

## Status

Accepted. (Owner-articulated product positioning for the Shagiri observation surface + the AI org.)

## Context

As the AI org authors more of the code, the human bottleneck moves from **writing** to
**understanding, steering, and reviewing**. The product direction is decided even where the
supporting measurements are still evidence-gated: the cockpit assumes the human's primary
unit of action is *express intent + judge the result*. Session anecdotes that motivated this
ADR are treated as hypotheses unless they are backed by committed, replayable artifacts; see
the claim ledger in `docs/evidence/ADR-0011-claim-ledger.md`.

So hand-editing code is **demoted** — from the centre of the workflow to a detail you
rarely open. Like assembly: still reachable, you can drop to it, but you do not live there.
**Code-editing is the new assembly.** The human's unit of action flips from *edit a line* to
*express intent + judge the result*.

The dominant AI-coding tools (Cursor and kin) inherit VSCode's **file-tree-and-buffer**
paradigm: an *editor* with AI bolted on. If editing is no longer the centre, the editor is
the legacy paradigm, not the frontier. Competing on the editing loop is competing for the
thing that is ending.

## Decision

Position the product **not as a better editor but as the cockpit for an AI software org** —
a comprehension + orchestration surface for the era when humans do not write most of the
code. A different category, not a Cursor competitor: its successor. Its centre is **not a
file buffer** but four channels:

1. **Map — comprehend.** The deterministic codebase-city. Buildings = modules (height ∝
   size); **roads = package structure** (the address system: where a thing lives); **hover =
   the dependency cone** (imports amber / importers cyan — what a thing uses / is used by);
   **click = what-it-does + change history**. Roads and hover are deliberately *orthogonal
   channels* — structure vs coupling — because coupling cannot be honestly drawn as roads.

2. **Intent — steer.** A chat anchored to a map selection. Click a building and the
   conversation is scoped to that module (its code, cone, history); "fix this here"
   dispatches the AI org on that node.

3. **Review — judge.** The build is watched (the god-hand growth) and adversarially reviewed
   (Linon), surfaced inline. Correction is **re-steering, not hand-editing**.

4. **Provenance — remember.** Every utterance is persisted: the human↔orchestrator
   conversation AND every worker-agent's log (designer proposals, carrier reasoning, Linon
   findings). This inverts what "source" means: code is the *output*; the **conversation is
   the source** (the WHY/intent) and the agent logs are the HOW (the org's reasoning), while
   git keeps only the WHAT. Each building carries its **story** — the conversation that asked
   for it, the agents that built it, the review that passed it — so click-detail history is
   dialogue + provenance, not just commits. It also adds a navigation axis: query the map by
   the *intent* that produced a region ("what came from the score-signing discussion?" →
   those buildings light up). Persisted provenance is also what makes lower-trust carriers
   auditable — trust by inspection, not faith.

## The product is the BINDING

The decisive direction is integration: bind map -> chat -> org so the conversation is
spatial and results render back onto the city. The cockpit is not a claim that every
component has already been validated as a live integrated product.

Evidence status:

- **Committed direction:** the cockpit consists of map, intent, review, and provenance.
- **Committed evidence reference:** claims retained from the motivating sessions are tracked
  in `docs/evidence/ADR-0011-claim-ledger.md`.
- **Hypothesis:** the existing map, review, growth view, role-timing, and org-dispatch
  pieces can be bound into a live cockpit without discovering a new product primitive.
- **Not claimed:** static Linon review or terminal-session output proves live runtime
  compatibility of the integrated cockpit.

## Consequences

- **Build order: bind, don't invent.** Wire (a) map selection -> chat context, (b) chat
  intent -> scoped org dispatch, (c) org result (diff + Linon verdict + god-hand build) ->
  live city update. The hard parts exist; the work is the wiring.
- **The file buffer becomes a drop-to detail**, not the centre — the assembly analogy made
  literal in the UI.
- **Determinism is a hard requirement, not polish.** An interactive map needs stable
  positions for spatial memory and reliable hover/click targets. **Hypothesis:** the
  deterministic-layout claim remains unpromoted until a committed replayable artifact is
  referenced; see `docs/evidence/ADR-0011-claim-ledger.md`.
- **Honest risk — the niche graveyard.** Software-city visualisations have repeatedly stayed
  niche demos (CodeCity, Sourcetrail, and others). Pretty ≠ daily driver. The city earns
  daily use **only** as the cockpit — where AI work is *done* and *understood* — never as a
  picture. Stickiness comes from steering and comprehension, not aesthetics. (This is the
  grounding-not-illustration discipline applied to the product.)
- **Relationship to Shagiri.** The cockpit is the productised Shagiri **observation** axis
  (B) fronting the AI-org **execution** axis (A). The city is the human surface; heterogeneous,
  lower-trust carriers are product-level only under the containment boundary of ADR-0012,
  not new invocation paths in this repository.
- **Persistence is foundational, not a feature.** The provenance channel requires durable
  storage of conversations + agent logs, linked to the artifacts (and buildings) they
  produced. Volatile files and chat memory are not proof under ADR-0011.
- **Extends ADR-0005 and ADR-0006.** There we refused to dumb the *implementer* or narrow
  the *verifier* — protecting the org's intelligence. Here we name the **human's** new role:
  steer and judge, not edit — and define the surface built for it.
