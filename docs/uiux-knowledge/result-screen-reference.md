# Result Screen Reference (1920×1080 console)

This reference supports downstream implementation and review when an objective declares a game **result screen** (post-battle or post-pull) on a 1920×1080 console/10-foot surface, controller + mouse. It is a handoff document, not a profile card: form contract obligations and evidence requests from it, then enforce them through `profile_applications`, `implementation_evidence`, and `scripts/profile-evidence-check.py`, with rendered-pixel measurement gated by Linon.

## Provenance (NN1)

Codifies two independent, interested-party (ChatGPT) reports, both source-bearing:

- **Issue #4 `ResultScreen_1920x1080_Console_v1`** — an independent, self-standing console-1080p work spec: concrete coordinates, sizes, colors, focus map, plus its own citations. An earlier round in this lineage was self-reported as arbitrary ("前回の数値は適当でした"); #4 is the corrected, console-1080p report and is evaluated on its own merits.
- **Issue #5 `ゲームリザルト画面制作標準 v2`** — a re-investigation that adds the graphics/illustration/VFX/saliency integration layer and the derive-from-composite methodology on top of #4.

Relationship: **#5 = #4 + graphics re-investigation.** #4 retains independent value (concrete coordinates + sources); #5 is not a full replacement. Every standard-backed figure used here was independently re-verified against its primary source on 2026-06-14 (see `anchor:genre-result-screen`); three framing corrections are recorded in that anchor.

## Reading Rule

A result-screen claim only survives when it becomes a property that can be **measured on the rendered, composited, animated screen** — not asserted in a coordinate list. A term such as readable, juicy, premium, hierarchy, or "reward is the star" must map to a trigger, state/proof, guard, render hook, a bounded cadence, a fallback, and a **measured verification on actual pixels**. A coordinate spec is not a verified screen (NN3). If a property cannot be measured on the render, mark it a gap instead of preserving it as prose.

## Value Classes

Every number on this screen belongs to exactly one class. The class decides how it is justified and verified.

| Class | Definition | How it is justified | How it is verified |
| --- | --- | --- | --- |
| Standard-backed | Floor/ceiling fixed by an external standard | Cite the dated `anchor:genre-result-screen` pointer | Assert + check the rendered value clears the floor |
| Work-value | Concrete coordinate/size chosen to satisfy constraints | Labeled non-canonical; must sit inside the safe zone and on the 8px grid | Layout check on the render |
| Empirical (held) | Number with no objective function (e.g. a chosen stagger feel) | Held static inside a named band; not derived | In-band check; not eliminated |
| Derived (computed) | Number with an objective function on the composite | **Derived from the rendered composite**, never fixed by feel | Recompute on the actual pixels and confirm the objective holds |

The fourth class is the depth of this reference: the numbers that decide whether the screen is good — `bg_dim` opacity, VFX opacity, face brightness/salience, particle count — are **Derived**, and they are absent from any coordinate spec by construction.

## Standard-backed floors (verified)

- Minimum body text ≥ 28px (Fire TV 14sp floor) and never below 26px (`#firetv-safe-text`, `#xag101-text-size`); text scales to 200% intact.
- Safe zone: inner 90%, outer 5% kept clear → x≥96, y≥54 at 1920×1080 (`#firetv-safe-text`).
- Contrast measured on the composite at the lowest-contrast point: standard text ≥ 4.5:1, large text/visuals ≥ 3:1, inactive ≥ 3:1, high-contrast ≥ 7:1 (`#xag102-contrast`).
- Color is never the sole carrier of meaning; no text baked into image files except logotypes (`#xag102-contrast`).
- Flash ≤ 3 per second across all frames (`#wcag-three-flashes`).
- Animation 100–500ms; modal enter 200–300ms (`#nng-animation-duration`).
- Focus order is logical, controller/keyboard-only operable; initial focus is the primary action (Next) (`#xag112-navigation`).
- Animated/auto-updating background must be stoppable/hideable/disable-able when text shares the screen; reduce-motion stops decorative motion (`#xag117-motion`).
- Touch-migration target floors: 44pt (Apple) / 48dp + 8dp spacing (Material) (`#platform-touch-targets`).

## Work-values (#4 concrete layout, non-canonical)

These satisfy the floors above on a 1920×1080 canvas. They are work-values, not standard-derived; the standard gives 28px/4.5:1/5%/focus-order, not `x=704`.

- Main panel x=288 y=120 w=1344 h=840; content x=352 y=160 w=1216 bottom=920.
- Result title (WIN/DEFEAT) 80px; featured reward card x=704 y=304 w=512 h=272.
- EXP box x=560 y=600 w=384 h=72; GOLD box x=976 y=600 w=384 h=72.
- Reward list: 8 slots, 112×112, gap 24, row at y=696, slots x=424…1376; 9+ rewards → show 7 + a "他N件" more-card; 0 rewards → "追加報酬はありません".
- Detail button x=352 y=832 w=256 h=88; Next button x=1280 y=832 w=288 h=88 (88 = 40 line-height + 24+24 padding).
- Text size set in use: 80 / 40 / 36 / 32 / 28 only; 26px is the hard floor; 12–24px forbidden.

## Integration rules (#5 graphics/VFX layer)

- **UI_RESERVED_MASK**: behind UI, exclude faces, hands, weapon tips, strong glows, fine patterns, and anything that reads as text/button/selection-frame. The mask is the union of the normal UI rect, the 200%-scaled text rect, focus-ring, pressed state, animation sweep, and forbidden-VFX rect — not a fixed 48px pad.
- **Saliency hierarchy** (the "reward is the star" claim made measurable): S_reward > S_face, S_reward > S_background-highlight, S_next in the top 3. Character face is priority rank 5 — the better the art, the more it must be held back.
- **Color reservation**: gold = featured reward / primary button only; green = growth/increase; red = error/insufficient/danger only; the art may not place strong gold/red/green glow behind reserved-color UI zones.
- **No text in images**: WIN, SSR, item names, button labels are UI text elements, never baked into art (translation/contrast/scaling/readout).
- **VFX**: reward burst ≤ 500ms, trailing particles ≤ 1000ms, no full-screen white/red flash, VFX stays inside its range and never overlaps title/Next/error-modal.
- **Background motion**: default 0%; if animated, every frame clears contrast and flash, and reduce-motion fully stops it.

## Derive-from-composite (the underivable numbers)

These have an objective function and MUST be computed on the rendered composite, never fixed by feel:

- `bg_dim` opacity = the smallest dim alpha such that every text rect clears its contrast floor at its lowest-contrast background pixel. Solve `alpha ≥ (L_before − L_target) / (L_before − L_dim)` per text rect; adopt the max across all rects.
- Background-VFX opacity = the largest opacity that passes contrast + flash + forbidden-overlap on **every** frame.
- Face brightness/saturation = whatever makes the saliency hierarchy hold (S_reward > S_face), calibrated against a labeled reference set, not a fixed −12.
- Particle count = derived from the real frame budget (`max_particles = floor(available_ms / cost_per_particle_ms)`), not a fixed 96.

## Obligation Forms

- `result-screen`: every text element ≥ 28px and, when composited over the background art + dim, clears its contrast floor measured at its lowest-contrast pixel; evidence cites the measured contrast per text rect on the render, not the panel swatch.
- `result-screen`: `bg_dim` opacity is derived from the composite so all text passes, and the evidence reports the derived alpha and the binding (worst) text rect.
- `result-screen`: the saliency hierarchy S_reward > S_face and S_next-in-top-3 is measured on the render; evidence reports the saliency scores, not an assertion that "the reward stands out."
- `result-screen`: initial focus is Next, all controls are reachable by D-pad/keyboard alone, and modal focus is trapped; evidence cites the focus map and a navigation trace.
- `result-screen`: reward VFX burst ≤ 500ms and flashes ≤ 3/sec measured across all rendered frames, with reduce-motion stopping decorative motion; evidence cites the per-frame measurement.

## Boundary

Use the pointers and the labeled reference corpus (`#game-ui-database`) to ask better questions and calibrate saliency. Do not copy game source, screenshots, assets, dialogue, audio, or commercial canon into the pack. Concrete coordinates here are work-values for the demo target; a different surface needs its own derived numbers and its own measured evidence.
