# ADR-0011: Claim evidence discipline

## Status

Accepted. (Evidence discipline for vision ADRs and product claims.)

## Context

The cockpit ADRs deliberately make product-direction decisions before every implementation
detail exists. That is valid for vision work, but it becomes unsafe when measured,
already-working, proven, or live-runtime claims are written with the same force as the
direction itself.

This repo also has a recurring evidence boundary: volatile files, chat memory, and local
terminal output can motivate a direction, but they are not committed replayable artifacts.
They cannot carry a measured claim after the fact.

## Decision

A vision ADR may state a decided direction decisively. Every measured claim must do one of
two things:

1. Reference a committed, replayable artifact in this repository.
2. Be explicitly labeled **Hypothesis**.

The same rule applies to claims phrased as measured, proven, validated, already working,
live-compatible, or cost-established. Evidence references must name a committed path and be
specific enough for a reviewer to replay or inspect the basis of the claim.

Files outside the repository, `/tmp` files, chat transcript, memory, and uncommitted logs are
not proof. They may be cited only as motivation if the claim itself is labeled **Hypothesis**.

The claim ledger in `docs/evidence/ADR-0011-claim-ledger.md` is the registry for retained
measured claims and their evidence status.

## Consequences

- Product direction stays sharp; evidence discipline does not weaken the decision.
- Reviewers can distinguish direction, measured fact, static evidence, live evidence, and
  hypothesis.
- Evidence stubs may record pending or external artifacts, but a stub does not convert an
  unmeasured claim into a measured claim.
- Static Linon evidence may support static claims only. Live runtime compatibility requires
  the live regime in ADR-0013.
