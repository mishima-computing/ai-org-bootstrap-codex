# ADR-0007: The cockpit — editing is demoted; the codebase-city is the human interface to the AI org

## Status

Accepted. (Owner-articulated product positioning for the Shagiri observation surface + the AI org.)

## Context

As the AI org authors more of the code, the human bottleneck moves from **writing** to
**understanding, steering, and reviewing**. This is not a forecast — it is what already
happens. The session that produced this ADR built an arcade, fixed its roads, ran a Linon
recall experiment, measured role-level timing, and wrote ADR-0006 — and the owner **never
hand-edited a file**. He steered in a chat box ("the roads don't connect", "fix the
camera"); the org dispatched carriers; the god-hand growth showed it building; Linon
reviewed. The working session was a manual, terminal-bound run of the very tool described
here.

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
file buffer** but three channels:

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

## The loop already runs — the product is the BINDING, not new capability

Every component was validated in the session that wrote this:

- the org that **builds** (carriers, host or boxed),
- the **verifier** (Linon — and ADR-0006 established that its sharpness is load-bearing and
  must not be narrowed),
- the **map** (a deterministic, interactive codebase-city; positions stable run-to-run),
- the **growth view** (the god-hand placing/relocating buildings),
- the cost/role substrate (role-level timing; build∥review pipelining).

What remains is **integration**: bind map ↔ chat ↔ org so the conversation is spatial and
results render back onto the city live. No new capability is invented. Today's terminal
session is a working, unbound prototype of the cockpit.

## Consequences

- **Build order: bind, don't invent.** Wire (a) map selection → chat context, (b) chat
  intent → scoped org dispatch, (c) org result (diff + Linon verdict + god-hand build) →
  live city update. The hard parts exist; the work is the wiring.
- **The file buffer becomes a drop-to detail**, not the centre — the assembly analogy made
  literal in the UI.
- **Determinism is a hard requirement, not polish.** An interactive map needs stable
  positions for spatial memory and reliable hover/click targets. Already secured: a layout
  that is a pure function of the repo (package-hierarchy roads; set-order non-determinism
  removed).
- **Honest risk — the niche graveyard.** Software-city visualisations have repeatedly stayed
  niche demos (CodeCity, Sourcetrail, and others). Pretty ≠ daily driver. The city earns
  daily use **only** as the cockpit — where AI work is *done* and *understood* — never as a
  picture. Stickiness comes from steering and comprehension, not aesthetics. (This is the
  grounding-not-illustration discipline applied to the product.)
- **Relationship to Shagiri.** The cockpit is the productised Shagiri **observation** axis
  (B) fronting the AI-org **execution** axis (A). The city is the human surface; the box /
  microVM containment is what makes heterogeneous, lower-trust carriers (e.g. a DeepSeek
  edition) safe to run beneath it.
- **Extends ADR-0005 and ADR-0006.** There we refused to dumb the *implementer* or narrow
  the *verifier* — protecting the org's intelligence. Here we name the **human's** new role:
  steer and judge, not edit — and define the surface built for it.
