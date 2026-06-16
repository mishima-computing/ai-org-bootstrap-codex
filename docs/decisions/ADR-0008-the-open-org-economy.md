# ADR-0008: The open org economy — composable, buyable, contributable agents and orgs

## Status

Accepted. (Owner-articulated economic layer for the cockpit of ADR-0007.)

## Context

ADR-0007 defines the cockpit: the human steers an AI org and judges the result. That raises
the supply question — where do the agents and orgs come from? Three facts already in hand:

- **Orgs are configuration, not code we hard-wire.** An agent is a role definition
  (`roles/*.md`) plus carrier-independent tools (`ai-org-tools` / Corps) plus a verifier
  spec. An org is a manifest of such roles, a topology (who hands to whom, who verifies whom,
  serial vs parallel), and the design rules of ADR-0005/0006. The controller composes them as
  a workflow.
- **Composing orgs is proven, not speculative.** The claudecode edition was built by the
  codex org (dogfood) — an org composed another org. So "define your own org" is the
  productisation of something already done, and an org can help you build an org (meta).
- **The dangerous part is already solved.** Running arbitrary third-party code-writing agents
  is normally a security nightmare; the box / microVM containment plus full-log observation
  plus Linon verification neutralise it (the same containment that makes a lower-trust
  DeepSeek edition safe to run).

## Decision

Build an **open economy** of agents and orgs on top of the cockpit.

1. **Composable.** Users assemble their own org — which agents, wired how. The ADR-0005/0006
   results ship as the **safe defaults / design rules** that protect non-experts from
   footguns: don't dumb the implementer, don't narrow the verifier, keep a strong verifier
   even when the implementer is cheap (carrier asymmetry).

2. **Buyable.** Agents and orgs are sellable artifacts. An **agent** (a role + tools +
   verifier) slots into a user's org; an **org** (a full, pre-composed team) is turnkey. This
   serves both markets ADR-0007 implies: amateurs **buy** a turnkey org and steer it; pros
   **buy** specialised agents and compose. First-party listings seed the quality bar and act
   as reference.

3. **Open / contributable.** Anyone can publish. First-party supply only seeds it; the value
   and network effects live in the long tail of community agents and orgs. Open contribution
   is what makes it a **platform, not a catalogue** — and the supply side **self-accelerates**
   because contributors use AI orgs to build the agents/orgs they publish.

4. **Linon is the registration gate.** Submission triggers a Linon scan; its findings (under
   NN1 — the controller independently confirms them, so the gate is not a self-report)
   determine whether the artifact may register, and surface as the listing's evidence. The
   adversarial verifier becomes the marketplace's **quality and safety floor**, turning the
   "trust-nothing" stance into the registration mechanism. Lower-trust submissions still run
   only in containment.

## Consequences

- **Containment is the moat.** An open marketplace of executable, code-writing agents is
  viable for whoever can run untrusted submissions **safely** — box isolation + full-log
  observation + Linon verification. "Anyone can add" must not become "anyone can harm you";
  containment is what keeps those separate, and it is the differentiator competitors lack.
- **The enabling work is a manifest standard** — the "package.json for an org": role
  definitions, tools, topology, verifier specs, carrier requirements, and the evidence bundle
  format. The pieces exist (`roles/*.md`, `ai-org-tools`, controller-as-workflow, evidence
  bundles); standardising the open format is the task that lets everyone contribute
  interoperably.
- **Quality control against the lemon-market failure** = the Linon gate + persisted logs
  (inspection) + reputation, not first-party curation alone. Trust by
  containment-and-verification, not by faith — the `ai-org-bootstrap-codex-facade` discipline
  generalised into a market mechanism.
- **Built on ADR-0005/0006/0007.** The protected implementer and verifier intelligence
  become the design rules sold as safe defaults; the cockpit is the runtime; this is the
  supply and the economy around it.
