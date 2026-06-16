# ADR-0010: The engagement layer — Gems are metered compute; reliable floor, gacha for delight

## Status

Accepted. (Owner-articulated engagement / game-economy layer for the cockpit.)

## Context

ADR-0007 fixed the cockpit, ADR-0008 the open economy, ADR-0009 the feel (Pikmin × RPG) and
the roster. Two things were still open: how to drive engagement and **on-ramp the amateur
market**, and how amateurs **acquire and manage the real resource the product consumes** —
compute (tokens). This org's own measured work established that tokens / compute, against the
API rate ceiling, are the actual cost driver.

Familiar mobile-game mechanics — gacha, login bonus, events, collectible skins — are the
accessible, sticky loop for that market. But normal gacha currency is free to mint, whereas
the resource users actually need here is real, paid compute.

## Decision

Add an engagement layer, designed so the game economy and the product's real economy are the
**same** economy.

- **Gems = metered compute (a token allowance).** The mechanics dispense the *actual fuel*
  that runs the user's org, not a fake currency. This grounds the loop in reality and turns
  "how do I get and manage a compute budget?" from friction into play.

- **Reliable floor, variable delight.** Core compute is **never gated behind RNG** — without
  tokens the product cannot be used at all, so gating it would be hostile. The **floor**
  (login bonus, events) grants compute *reliably*; **gacha** grants *bonus* tokens plus
  **collectible agent skins** — the variable delight and the cosmetic monetisation. Utility is
  never the gacha prize; bonuses and cosmetics are.

- **Skins ride the persistent roster.** Agents are a kept party (ADR-0009), so limited skins
  are meaningful collection — you skin characters you keep, not expendable workers.
  Cosmetic-only; no pay-to-win.

- **The game economy interlocks with the carrier economy.** Because Gems are real compute,
  generosity is bounded by real unit cost — and a cheaper, boxed carrier (e.g. a DeepSeek
  edition) costs fewer Gems to run, so containment + carrier choice (ADR-0008) directly set
  how generous the floor and the gacha can be. The token economy this org measures is the
  economy the game dispenses.

- **Two-market tone.** Amateurs get the full game loop (the on-ramp and retention); pros get
  it toned down or cosmetic-only — a serious tool must not feel like a slot machine.
  (ADR-0008's two markets, applied to engagement.)

## Consequences

- **No loot-box-for-compute dark patterns.** The line is explicit and load-bearing: a reliable
  compute floor plus cosmetic/bonus gacha. Gating the resource a user needs *to use the
  product at all* behind RNG is out of bounds.
- **Unit economics are designed against real compute cost.** Grant rates (the login/event
  floor, the gacha bonus) are a function of carrier cost and margin, not free minting; cheaper,
  contained carriers widen the generosity budget.
- **Engagement serves the amateur on-ramp and retention**, while the pro segment is protected
  by the tone-down — the two markets stay coherent under one product.
- Builds on **ADR-0007** (cockpit), **ADR-0008** (open economy, two markets, carrier
  containment), **ADR-0009** (the roster the skins ride on).
