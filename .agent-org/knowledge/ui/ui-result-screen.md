---
profile_id: ui-result-screen
scope: Human-facing objectives that declare ui-result-screen or game-designer with the result-screen cartridge; a post-battle/post-pull result screen on a 1920×1080 console/10-foot surface (controller + mouse) whose quality is verified on rendered pixels.
covers: standard-backed floors (text size, safe zone, contrast, flash, animation bands, focus order); concrete 1920×1080 work-value layout; graphics/VFX integration (UI_RESERVED_MASK, saliency hierarchy, color reservation, no-text-in-image); derived-on-composite numbers (dim alpha, VFX opacity, face salience, particle budget).
freshness: Short horizon for layout/VFX grammar; standard-backed floors track their dated anchors. Re-check before material product use or after six months.
supersede_trigger: Supersede on Stage-A pilot contradiction, a platform-guidance/standard revision behind a cited anchor, or an owner ruling on the screen's job statement.
evidence_refs: anchor:genre-result-screen#xag101-text-size; anchor:genre-result-screen#firetv-safe-text; anchor:genre-result-screen#xag102-contrast; anchor:genre-result-screen#xag112-navigation; anchor:genre-result-screen#wcag-three-flashes; anchor:genre-result-screen#nng-animation-duration
---

Cartridge: this card is `cartridge:result-screen` under the `game-designer` console (`game-designer.md`); reference handoff doc is `docs/uiux-knowledge/result-screen-reference.md`. It specializes the console for the result screen and never relaxes a console value class or the measured-on-render rule.

Apply-only: applies when an objective declares ui-result-screen (or game-designer with the result-screen cartridge); never inferred from "it shows rewards."

Job statement: a result screen makes a player who just finished a battle/pull understand outcome, primary reward, and growth, and move to the next action. Anything off this one-sentence job is weakened or cut.

Admissibility: a result-screen term is admissible only when mapped to a trigger, the state/proof it establishes, a guard, a render hook, a bounded cadence, a fallback, and a verification measured on the rendered composite. A coordinate or a swatch is not a verified screen (NN3).

Floors (standard-backed, cite the dated anchor): body text ≥ 28px and never below 26px; inner-90% safe zone (outer 5% clear); contrast measured at the lowest-contrast composite pixel — standard ≥ 4.5:1, large ≥ 3:1, inactive ≥ 3:1, high-contrast ≥ 7:1; flash ≤ 3/sec across all frames; animation 100–500ms, modal 200–300ms; initial focus = primary action (Next), controller/keyboard-only operable, modal focus trapped; animated background stoppable + reduce-motion stops decorative motion; color never sole meaning; no text baked into images except logotypes.

Graphics integration: UI_RESERVED_MASK (union of UI + 200% text + focus-ring + pressed + animation sweep + forbidden-VFX) excludes faces/hands/weapon-tips/strong-glow/fine-pattern behind UI; saliency hierarchy S_reward > S_face and S_reward > S_background and S_next in top 3 (face is priority rank 5); reserved colors (gold/green/red) not placed as strong glow behind reserved-color UI zones.

Derived on composite (never fixed by feel): dim alpha from per-text-rect contrast at the lowest-contrast pixel (adopt max across rects); background-VFX opacity as the largest all-frame-passing value; face brightness/saturation to hold the saliency hierarchy; particle count from the real frame budget.

Evidence: a measured-pixel report (contrast per text rect on the render, derived dim alpha + the binding rect, saliency scores, per-frame flash), a focus/navigation trace, and the layout check against the work-value coordinates; absence is a declared gap, never assumed.

Risk: result-screen grammar is taste-laden and stale-prone, and the deciding numbers are derived not stated; treat as short-horizon and verify on pixels until a product Stage-A spec ratifies it.
