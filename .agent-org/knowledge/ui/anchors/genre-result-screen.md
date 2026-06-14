# Result Screen Anchors

Scope: dated, source-verified pointers for a post-battle / post-pull game result screen on a 1920×1080 console/10-foot surface (controller + mouse). Pointer notes only, zero copied source excerpts. These pointers supply the *verifiable-standard* values; concrete coordinates and underivable numbers live in `docs/uiux-knowledge/result-screen-reference.md` and are derived per-artifact, not stored here.

Provenance note: codifies issue #4 (`ResultScreen_1920x1080_Console_v1`, an independent console-1080p work spec) and issue #5 (`ゲームリザルト画面制作標準 v2`, the generalized standard). Both were authored by an interested party (ChatGPT) and an earlier round of the same lineage was self-reported as arbitrary; every figure below was independently re-verified against its primary source on 2026-06-14 before being anchored (NN1). Three framing corrections are recorded inline.

## xag101-text-size
Pointer: https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/101 | Date/version: Microsoft Game Accessibility Guideline 101, ms.date 2022-05-09 (re-verified 2026-06-14) | Scope note: minimum default text size ≥ 26px on console 1080p and ≥ 52px on 4K; text must scale to 200% without loss of content, function, or meaning. | Local use boundary: cite for the minimum-text-size floor and scale test; concrete per-element sizes are work-values in result-screen-reference.md. | Stable ID: #xag101-text-size

## xag101-text-block
Pointer: https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/101 | Date/version: same page (re-verified 2026-06-14) | Scope note: for text blocks over 2 lines, line WIDTH ≤ 80 characters in general and ≤ 40 for Chinese/Japanese/Korean; line spacing ≥ 1.5; paragraph spacing ≥ 2× line spacing. | Correction: the 40-char limit is the CJK-specific case of the 80-char general line-WIDTH rule; do not present 40 as the universal limit or call it "line length." | Local use boundary: cite for line-width and spacing floors. | Stable ID: #xag101-text-block

## xag102-contrast
Pointer: https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/102 | Date/version: Microsoft Game Accessibility Guideline 102, ms.date 2023-06-08 (re-verified 2026-06-14) | Scope note: standard important text/visuals ≥ 4.5:1; large text/visuals ≥ 3:1; inactive elements ≥ 3:1; high-contrast mode ≥ 7:1; on a non-solid background, measure between the text and the lowest-contrasting area behind it; do not rely on color alone; images should not contain text except logotypes. | Local use boundary: cite for contrast floors and the lowest-contrast-point measurement rule; the actual measured contrast is derived on the composited render, not asserted. | Stable ID: #xag102-contrast

## xag112-navigation
Pointer: https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/112 | Date/version: Microsoft Game Accessibility Guideline 112 (re-verified 2026-06-14) | Scope note: UI navigation order is logical and consistent; the UI is fully navigable by keyboard and controller digital input alone; focus order aligns with the meaning/operation of the UI, falling back to visual flow. | Local use boundary: cite for focus-order and controller-only-operability obligations. | Stable ID: #xag112-navigation

## xag117-motion
Pointer: https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/117 | Date/version: Microsoft Game Accessibility Guideline 117 (re-verified 2026-06-14) | Scope note: when moving, blinking, scrolling, or auto-updating content shares a screen with text, provide a mechanism to entirely disable it and to pause or hide it. | Correction: XAG 117 also covers motion-sickness triggers (camera shake, FOV, head-bob); the stop/hide/disable requirement cited here is genuinely present, but 117 is not titled "flashing." | Local use boundary: cite for the stop/hide/disable obligation on animated backgrounds. | Stable ID: #xag117-motion

## firetv-safe-text
Pointer: https://developer.amazon.com/docs/fire-tv/design-and-user-experience-guidelines.html | Date/version: Amazon Fire TV design and UX guidelines, live resource re-verified 2026-06-14; re-check on Fire TV doc revision. | Scope note: keep UI out of the outer 5% of every edge and inside the inner 90% safe zone; body text at least 14sp, approximately 19px at 720p and 28px at 1080p. | Correction: 28px is the doc's approximation of a 14sp MINIMUM (a floor), not a soft target; treat 28px as the body-text minimum. | Local use boundary: cite for the 5% safe-zone margin and the 28px body-text floor. | Stable ID: #firetv-safe-text

## wcag-three-flashes
Pointer: https://www.w3.org/WAI/WCAG21/Understanding/three-flashes-or-below-threshold.html | Date/version: WCAG 2.1 SC 2.3.1 Three Flashes or Below Threshold, Level A (re-verified 2026-06-14) | Scope note: content must not flash more than three times in any one-second period, unless below the general-flash and red-flash thresholds. | Local use boundary: cite for the flash ceiling; the actual flash rate is measured across all rendered frames. | Stable ID: #wcag-three-flashes

## nng-animation-duration
Pointer: https://www.nngroup.com/articles/animation-duration/ | Date/version: Nielsen Norman Group, Page Laubheimer, 2020-02-09 (re-verified 2026-06-14) | Scope note: most UI animation should fall in the 100–500ms range; substantial transitions such as a modal moving into view are appropriately 200–300ms. | Local use boundary: cite as the timing band source; specific durations are work-values held within these bands. | Stable ID: #nng-animation-duration

## platform-touch-targets
Pointer: https://developer.apple.com/design/human-interface-guidelines/accessibility | Date/version: Apple HIG accessibility (re-verified 2026-06-14); Material/Android target https://support.google.com/accessibility/android/answer/7101858 | Scope note: Apple minimum hit target 44×44 pt; Material/Android minimum touch target 48×48 dp separated by ≥ 8 dp. | Local use boundary: cite for touch-port target sizing; the console build is controller-first, these are the touch-migration floors. | Stable ID: #platform-touch-targets

## game-ui-database
Pointer: https://www.gameuidatabase.com | Date/version: Game UI Database 2.0 (Edd Coates), relaunched 2024-08; 55,000+ screenshots, 1,700+ videos, 1,341 games (figures via https://www.gamedeveloper.com/design/game-ui-database-relaunches-with-new-features-video-support-and-over-55-000-screenshots, re-verified 2026-06-14) | Scope note: screenshot/video UI reference corpus for building a labeled result-screen reference set. | Local use boundary: cite as the reference-corpus source for saliency calibration; do not copy screenshots into the pack. | Stable ID: #game-ui-database
