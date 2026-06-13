# Motion Anchors

Scope: canonical pointers for animation purpose, duration bands, easing, choreography, transitions, reduced motion, skip controls, and multimodal coordination; pointer notes only, with zero copied source excerpts.

## apple-hig-motion
Pointer: https://developer.apple.com/design/human-interface-guidelines/motion | Date/version: Apple HIG Motion checked 2026-06-12; re-check on HIG redesign or platform motion guidance release signal. | Scope note: platform motion purpose, continuity, orientation, and reduced-motion-sensitive interface behavior. | Local use boundary: cite for Apple-platform motion decisions; local `timing-ranges` and accessibility review remain the acceptance surface. | Stable ID: #apple-hig-motion

## apple-hig-materials-liquid-glass
Pointer: https://developer.apple.com/design/human-interface-guidelines/materials | Date/version: Apple HIG Materials, Liquid Glass guidance June 2025; checked 2026-06-12; re-check on HIG materials redesign or platform release signal. | Scope note: Liquid Glass material behavior, depth, translucency, and responsive material feedback. | Local use boundary: cite for material-motion vocabulary; local reviews still require state proof, contrast, and reduced-motion-safe alternatives. | Stable ID: #apple-hig-materials-liquid-glass

## m3-motion-overview
Pointer: https://m3.material.io/styles/motion/overview | Date/version: Material 3 motion overview checked 2026-06-12; re-check on Material site restructure or motion guidance release signal. | Scope note: Material motion purpose, transition vocabulary, and movement-as-state-change framing. | Local use boundary: cite for motion-purpose vocabulary; product specs decide which state each effect proves. | Stable ID: #m3-motion-overview

## m3-motion-how-it-works
Pointer: https://m3.material.io/styles/motion/overview/how-it-works | Date/version: Material 3 motion how-it-works checked 2026-06-12; re-check on Material site restructure or choreography guidance release signal. | Scope note: transition choreography, spatial continuity, and motion relationship guidance. | Local use boundary: cite for choreography rationale; implementation evidence stays in screenshots, recordings, and reduced-motion review. | Stable ID: #m3-motion-how-it-works

## m3-easing-duration-tokens
Pointer: https://m3.material.io/styles/motion/easing-and-duration/tokens-specs | Date/version: Material 3 easing and duration tokens checked 2026-06-12; re-check on Material site restructure or token release signal. | Scope note: named easing and duration token vocabulary for ranges/bands, not pack constants. | Local use boundary: cite for token vocabulary; local cards keep `timing-ranges` and product tests as the decidable operationalization. | Stable ID: #m3-easing-duration-tokens

## m3-expressive-motion
Pointer: https://m3.material.io/blog/building-with-m3-expressive | Date/version: Material 3 Expressive motion guidance May 2025; checked 2026-06-12; re-check on Material Expressive update or blog migration signal. | Scope note: expressive motion and emphasis guidance for Material 3 expressive surfaces. | Local use boundary: cite for expressive motion vocabulary; claims remain appeal/expressiveness unless user-test evidence exists. | Stable ID: #m3-expressive-motion

## nng-animation-duration
Pointer: https://www.nngroup.com/articles/animation-duration/ | Date/version: NN/g article dated 2020-02-09 | Scope note: animation duration ranges for perceived responsiveness, orientation, and attention. | Local use boundary: cite for duration bands; fixed millisecond constants remain outside pack cards unless product tests justify them. | Stable ID: #nng-animation-duration

## wcag22-motion-criteria
Pointer: https://www.w3.org/TR/2024/REC-WCAG22-20241212/ | Date/version: W3C Recommendation dated this-version 2024-12-12 | Scope note: WCAG 2.2 SC 2.2.2 Pause, Stop, Hide; SC 2.3.1 Three Flashes or Below Threshold; and SC 2.3.3 Animation from Interactions, scope-only for motion risk. | Local use boundary: cite for motion/accessibility scope and review planning; conformance planning remains with `anchor:accessibility#wcag22-recommendation`. | Stable ID: #wcag22-motion-criteria
