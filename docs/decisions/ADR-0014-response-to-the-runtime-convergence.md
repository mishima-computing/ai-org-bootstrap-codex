# ADR-0014: Response to the runtime convergence — own the open org-layer, not the runtime race

## Status

Accepted. **Amends ADR-0007's competitive positioning** (withdraws the "successor to a dead
editor" framing; keeps the cockpit's internal design). Builds on ADR-0008 (open economy) and
ADR-0012 (containment), generalising the latter from carrier-agnostic to runtime-agnostic.

## Context

ADR-0007 framed the cockpit as the **successor** to a "legacy file-tree-and-buffer editor",
naming a specific incumbent as the dead paradigm. The market has overtaken that framing. Over
2025→2026 the leading AI-coding tool shipped, in production, almost exactly the cockpit this
ADR series describes (dates per public posts; treated here as **observed external evidence**,
not our measurement):

- agent-centric UI replacing the file-centric IDE (the "agents window" / unified workspace);
- many agents in parallel via git worktrees / remote machines;
- **Cloud Agents** — each agent in its own isolated VM with terminal/browser (our box /
  containment, at scale);
- **Automations** — event-driven agents (schedule, webhook, chat, issue, incident);
- **review-agent that fixes** — a review bot that spawns an agent to produce and test a fix
  (our "Linon gate that closes the loop");
- **self-hosted** execution for enterprise security boundaries;
- an **SDK** exposing the same runtime/harness, with nested subagents (an orchestration API);
- **Auto-review** — a classifier that dials agent autonomy by risk rather than allow/deny;
- a long-rollout agent **model**, and a stated "self-driving codebases" direction;
- published research converging on **Planner / Executor / Workers / Judge** role orgs after
  finding that "shared-file self-coordination fails" — independently matching our role roster
  and our "structured artifact, not free chat" conclusion.

This **negates** ADR-0007's claim that the incumbent is the dead editor and we are its
successor. The incumbent shed the editor and is shipping the agent-workforce system ahead of
us on capability, model, fleet infrastructure, distribution, and funding.

## Decision

Sublate, do not retreat or cope.

1. **Concede the capability race.** Withdraw "successor to a dead editor." Do **not** build a
   competing agent runtime (cloud VMs, a frontier coding model, fleet management) head-on
   against a funded leader already shipping them. Treat the agent **runtime** as commodity
   substrate.

2. **Preserve what is confirmed.** The *direction* — editing demoted, the human orchestrates /
   observes / verifies / controls a fleet of agents — is **validated** by the leader converging
   on it. Our org-design conclusions (role org over free-chat swarm; the verifier is
   load-bearing, ADR-0006; coordinate through structured artifacts; evidence discipline,
   ADR-0011; the Linon→designers→aufheben dialectic) independently match the incumbent's own
   research. Convergence is confirmation, not invalidation. Our **discipline** (grounding,
   adversarial self-correction) is a real quality edge — we out-think where they out-ship.

3. **Lift: be the open org-layer on commodity runtimes.** Win where a vertically-integrated,
   model-selling, pro/enterprise leader structurally will not go:
   - **Open / runtime-agnostic** vs walled and model-locked. Generalise ADR-0012: containment
     governs *any* contained runtime, so the layer rides on any of them (a vendor SDK, Codex,
     other carriers). The open, contributable **marketplace of agents and orgs** (ADR-0008),
     made safe by containment + the Linon registration gate, is a moat a model-seller will not
     replicate because true runtime-openness cannibalises its lock-in.
   - **Amateur / mass segment** vs up-market pro/enterprise. The Pikmin × RPG feel and the
     gamified compute economy (ADR-0009/0010) target the broad "AI builds my app" market the
     leader does not serve.
   - **Comprehension-first** vs artifact-first. The spatial codebase-city + provenance
     (ADR-0007 channels 1 and 4) is unclaimed by the incumbent's PR/diff/artifact surface —
     held with the honest niche-risk of software-city visualisations (ADR-0007).

Reposition: **not "the successor to the incumbent" but "the open org-layer that rides on
commodity runtimes."**

## Consequences

- **Build priority shifts** from runtime parity to the three differentiators: the open
  contributable org market (with containment + Linon gate), the amateur experience, and the
  spatial-comprehension cockpit — runtime-agnostic throughout.
- **The incumbent becomes a substrate, not only a rival**: its SDK / runtime is one more
  contained runtime the open layer can sit on.
- **Honest residual (no cope):** this is a wedge, not a guaranteed win. The resource and
  distribution gap is real; the city is unproven; an open market needs supply and trust —
  which containment + the Linon gate provide, but must still be built. Discipline does not beat
  distribution by itself.
- Amends **ADR-0007** (competitive framing only). Builds on **ADR-0008** and **ADR-0012**.
