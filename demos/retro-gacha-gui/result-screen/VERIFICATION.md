# Result Screen — Verification (cartridge:result-screen)

This screen is the deep test of the Game Designer console: a 1920×1080 result screen whose quality
is decided by numbers that are **derived on the rendered composite and measured on real pixels**,
not asserted in a spec. Sources for the standard-backed floors are issue #4 / #5, independently
re-verified against primary docs on 2026-06-14 (`anchor:genre-result-screen`).

## What is derived vs measured (NN1)

- The screen **derives** `--bg-dim-alpha` from its own scene composite so every text rect over the
  translucent panel clears its contrast floor at the brightest pixel behind it, and **derives**
  `--bg-face-dim-alpha` so the character face cannot out-salience the reward. It self-reports these
  in `window.__resultMeasure`.
- A self-report is not the verification. `scripts/measure-result-screen.py` renders each variant to
  a real screenshot and **independently re-measures on the pixels**: per-text-rect contrast
  (background = the dominant colour cluster behind the text), the saliency hierarchy
  (S_reward > S_face and S_reward > S_background), the focus ring on the initially-focused control,
  and flash safety. Run: `python3 scripts/measure-result-screen.py` → OVERALL PASS.

## Calibration fairness (the gate bites): RED → GREEN

A finding is a self-report until a RED test confirms it. The derivation was proven load-bearing:

- **RED** (`?forcedim=0`, derivation bypassed, bright `normal` scene): instrument reports
  `title` contrast 1.0:1 (floor 3) and `stage-name` 3.93:1 (floor 4.5) → FAIL.
- **GREEN** (derived `--bg-dim-alpha` ≈ 0.58): all text clears its floor → PASS.

The derived dim genuinely varies with the scene: ~0.58 (normal), 0.66 (maxrewards), 0.62 (defeat),
0 (error — modal text sits on an opaque box, scene occluded by the scrim).

## Findings caught and fixed during the measure-fix loop

1. Button text was measured against the scene instead of the button's own opaque fill → added an
   ancestor-background composite (`effectiveBg`); dim is now load-bearing only for translucent-panel text.
2. The bright celebratory sky out-salienced the reward → added a vignette so edges fall away.
3. The character face out-salienced the reward (defeat) → face-dim derived to an absolute budget
   under reward salience.
4. Panel-child coordinates were panel-relative (+288/+120 offset from the spec's screen coordinates)
   → all content re-parented to the stage so 1 stage px == 1 spec px == 1 screenshot px.
5. Modal-open background text was scored as if active → scoped contrast to the active modal layer.

## Disclosed limitations (NN4)

- The instrument trusts the page's self-reported **rect geometry and text colour** (to know where and
  against what to measure); it measures the contrast itself from pixels. An independent DOM probe could
  re-derive geometry; not done here.
- Saliency uses a luminance×saturation×local-variance **proxy**, not a validated perceptual model; it
  encodes the direction of the #5 hierarchy, not perceptual ground truth. The human/perceptual review
  remains the taste backstop.
- Flash is checked **structurally** (no full-screen white/red flash keyframe, no infinite background
  animation, reveal burst constant ≤500ms), not by a per-frame pixel diff.

## Deterministic spec

`result-state.spec.js` (node) covers the rendering-independent logic: value formatting (+12,400,
clamp to +999,999,999+, negatives), reward partition (≤8, 9+ → 7 + 他N件, empty), rarity tier map,
reveal-burst band.
