# ADR-0009: The cockpit's feel and the buildings' architecture — Pikmin × RPG, modules as Metabolism

## Status

Accepted. (Owner-articulated UX north star + building-design model for the ADR-0007 cockpit.)

## Context

ADR-0007 fixed the cockpit's *structure* (map / intent / review / provenance) and ADR-0008
its *economy*. Two things were still open: how the cockpit should **feel**, and how the
buildings (modules) should be **designed and behave**.

The closest mental model for the feel is **Pikmin**: a commander directs a swarm of small
workers across a spatial map — points at a target, the swarm does the labour, and watching
them work is the pleasure. This is the right feel because editing is demoted (ADR-0007): the
human commands rather than labours; it is gentle enough to be approachable (Pikmin works for
children), which serves the amateur market; and it is the opposite of a developer text
console, which is the differentiation.

But Pikmin differs in one decisive way: its workers are **expendable** and what they carry is
**loot**. Here both sides are first-class, persistent, and valued — agents are a roster
(named, specialised, with a track record, composable and buyable per ADR-0008), and buildings
are the codebase (provenance, history, the product itself; kept and navigated, never
consumed).

## Decision

### A. Feel — Pikmin × RPG

- **Pikmin** supplies the interaction: command a swarm at a spatial map; throw it at a target;
  watching the swarm is the joy.
- **RPG** supplies the persistence: the agents are your *party* (identity, growth,
  reputation), the buildings are your *world* (kept, evolving). Neither is expendable.
- **The canonical loop:**
  1. From the **Roster**, compose a team — agents **and** harness (which roles, which carrier,
     which topology).
  2. Swapping an agent triggers a **Linon check that the composition actually works** — the
     ADR-0008 registration gate applied at team-assembly time, NN1-confirmed so it is not a
     self-report.
  3. Type a command in **chat**.
  4. **Greenfield** → the swarm builds on empty terrain, from zero.
  5. **Existing repo** → it loads as the deterministic city; the agents **descend onto the
     targeted buildings (and their cone) and swarm**. The bustle is the **parallel carriers
     made visible** — the measured 2–3 effective lanes rendered as workers on the worksite.

### B. Buildings — Metabolism

Design the buildings from the 1960s Japanese **Metabolist** movement (Kurokawa, Kikutake,
Maki, Otaka, with Tange; the Nakagin Capsule Tower its icon): architecture as a living
organism of a permanent **core** plus replaceable **capsules**, with parts living on
different metabolic cycles.

- **Mapping.** The **core / trunk** (long cycle, persistent) = the module's interface /
  contract / address — its public API and identity, which rarely changes. This is ADR-0005's
  frozen-interface, the contract-first spine. The **capsules** (short cycle, replaceable) =
  the implementation cells (functions, classes, LOC chunks) the org swaps.
- **Behaviour.** Code is not demolished and rebuilt; it **metabolises**. A change swaps a
  capsule while the building lives; a refactor rearranges capsules; dead code is a capsule
  that decays and detaches; growth plugs in capsules (the LOC-stacked floors already rendered
  are proto-capsules). The god-hand's act becomes capsule *replacement*, not only construction.
- **Time.** A building's provenance (ADR-0007, channel 4) **is** its metabolic history — which
  capsule was swapped, when, by which agent. The architectural time-axis and the audit log are
  the same artifact.

## Consequences

- **Open design decisions to settle:** capsule granularity (function vs class vs fixed LOC
  chunk); how to identify and render the unchanging core (public API / types); how the tiles
  depict plug / swap / decay of a capsule.
- The **feel** is both the accessibility lever (amateurs) and the differentiation (vs editor
  consoles). The **Metabolism** model is what keeps the city honest about *change over time*
  rather than a static snapshot — the building is never "finished".
- Builds on **ADR-0005** (frozen interface = the core), **ADR-0007** (cockpit + provenance =
  metabolic history), **ADR-0008** (agents as a valued, gated roster).
