---
profile_id: game-designer
scope: Human-facing objectives that declare game-designer or any game cartridge; the umbrella game-definition language the conservative-designer loads, with swappable genre/style cartridges.
covers: the console-level game-definition language (value classes, reading rule, derive-on-render rule, measured-on-pixels verification) plus a registry of cartridges that specialize it per game profile.
freshness: Short horizon for cartridge grammar (taste-laden, stale-prone); the console-level language is stable. Re-check cartridges before material product use or after six months.
supersede_trigger: Supersede a cartridge on Stage-A pilot contradiction or current platform-guidance/research contradiction; supersede the console only on a value-class or verification-model change.
evidence_refs: anchor:genre-result-screen#xag102-contrast; anchor:genre-result-screen#firetv-safe-text; anchor:genre-result-screen#nng-animation-duration; anchor:motion#nng-animation-duration
---

Apply-only: applies when an objective explicitly declares game-designer or a named cartridge; never inferred from pixels, nostalgia, or "it's a game" labels.

## The console (core language)

Game Designer is a game-definition language the conservative-designer loads. The console is genre-agnostic; it fixes how any game claim is admitted and verified. Cartridges (below) supply genre grammar; they never weaken the console.

Value classes — every number on a game surface belongs to exactly one, and the class decides justification and verification:
- Standard-backed: a floor/ceiling fixed by a dated external standard (cite an anchor). Verify the rendered value clears the floor.
- Work-value: a concrete coordinate/size chosen to satisfy constraints; non-canonical; must sit in the safe zone and on the grid. Verify by layout check.
- Empirical (held): a number with no objective function; held static inside a named band, not derived, not eliminated. Verify in-band.
- Derived (computed): a number with an objective function on the rendered composite (contrast, saliency, all-frame flash, frame budget); derived from the pixels, never fixed by feel. Verify by recomputing on the actual render.

Reading rule: a game term (juicy, punchy, premium, "reward is the star", readable, ceremony) survives only when it maps to a trigger, state/proof, guard, render hook, a bounded cadence, a fallback, and a verification measured on the rendered/composited/animated surface. A coordinate spec or a profile sentence is not a verified surface (NN3). Unmappable terms are marked gaps, not implemented.

Derive-on-render rule: the numbers that decide whether a game surface is good are usually Derived, and are absent from any spec by construction. Compute them on the composite (e.g. dim-alpha from per-text-rect contrast at the lowest-contrast pixel; background-VFX opacity as the largest all-frame-passing value; face salience to hold the saliency hierarchy; particle count from the frame budget).

## The cartridge model

Each genre/style is a swappable cartridge: a self-contained module that specializes the console for one kind of game. Swap the cartridge to match the declared game profile; the console stays the same. A cartridge declares its slot, its anchors, its genre obligation forms, and its measured verifications — and may add Empirical bands and Derived objectives, but may not relax a console value class or the measured-on-render rule.

Cartridge registry (current):
- `cartridge:retro-gamer` — retro game-feel surfaces (input cadence, scene/state, message cadence, reveal ceremony). Card: `ui-retro-gamer.md`. Reference: `docs/uiux-knowledge/retro-game-experience-reference.md`. Anchors: `anchor:genre-retro-game-experience`.
- `cartridge:gacha` — gacha/reveal economy (pre-draw audit, odds disclosure, rarity/identity ceremony). Card: `ui-gacha-genre.md`. Anchors: `anchor:genre-gacha`.
- `cartridge:result-screen` — post-battle/post-pull result screen on a 1920×1080 console surface (saliency hierarchy, UI_RESERVED_MASK, color reservation, VFX timing/range, focus map). Reference: `docs/uiux-knowledge/result-screen-reference.md`. Anchors: `anchor:genre-result-screen`.

Future cartridges (RPG menu, FPS HUD, puzzle board, etc.) plug into the same console: declare slot + anchors + genre obligations + measured verifications.

## Loading protocol (conservative-designer)

The conservative-designer selects the cartridge(s) matching the objective-declared game profile (objective-declared first, then selector output, then repo-local cards), loads the console language plus those cartridges, and grounds the proposal in them. The deterministic selector never guesses which cartridge fits; an unmatched game profile is a declared knowledge gap, not an invented cartridge.

## Verification & fallback

Every kept game term carries a fallback that is itself an observable state (silent / reduced-motion / no-audio / unavailable-render), never an absence. Evidence is runner/replay/measured: input trace, message-cadence proof, state-transition log, GUI/test spec, or a measured-pixel report (contrast per text rect, saliency scores, per-frame flash). Absence is declared a gap, never assumed. Cartridge grammar is short-horizon until a product Stage-A spec ratifies it.
