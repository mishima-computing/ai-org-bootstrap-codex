---
profile_id: ui-feel-foundations
scope: Human-facing interaction feel for screens, controls, feedback, and state changes.
covers: feel surfaces; feedback proof; timing-ranges; multimodal coherence; silent fallback.
freshness: Medium horizon; re-check when platform motion guidance or accessibility rules change.
supersede_trigger: Supersede on contradiction from product Stage-A tests, accessibility review, or platform guidance.
evidence_refs: anchor:interaction-feedback#apple-hig-feedback; anchor:interaction-feedback#m3-interaction-states; anchor:interaction-feedback#nng-visibility-system-status; anchor:motion#nng-animation-duration; anchor:motion#m3-easing-duration-tokens; anchor:accessibility#wcag22-recommendation
---

Fact: Feel surfaces are where users perceive cause, state, response, continuity, and recovery.
Rule: Feedback-as-proof uses `proves:` statements tying each effect to the state it confirms.
Timing: Use timing-ranges from anchor:motion#nng-animation-duration bands and product tests; record range rationale.
Coherence: Motion, audio, haptics, copy, and visual state must tell the same story.
Fallback: Every multimodal cue needs silent fallback and reduced-motion-safe behavior.
Claim class: Juiciness may increase appeal or expressiveness; usability-performance gains require product measurement per anchor:evaluation-instruments#claim-classes.
Pointers: Hicks et al. juiciness finding; cite anchor:interaction-feedback#nng-visibility-system-status and anchor:motion#m3-easing-duration-tokens.
