---
profile_id: htmlcss-motion-implementation
scope: Conservative CSS motion implementation for human-facing interface feedback.
covers: transform/opacity motion; reduced-motion fallback; easing vocabulary; timing-range pointers; state continuity.
freshness: Medium horizon; re-check on Baseline status change, platform reduced-motion guidance change, or UI profile supersession.
supersede_trigger: Supersede when ui-feel-foundations timing guidance or prefers-reduced-motion platform behavior changes.
evidence_refs: .agent-org/knowledge/ui/ui-feel-foundations.md; https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion; https://developer.mozilla.org/en-US/docs/Web/CSS/transform; https://developer.mozilla.org/en-US/docs/Web/CSS/opacity; https://developer.mozilla.org/en-US/docs/Web/CSS/easing-function
---

Rule: Prefer transform and opacity for motion surfaces; justify layout-affecting animation with an implementation reason.
Required: Provide `prefers-reduced-motion` behavior for every nontrivial motion path.
Rule: Easing vocabulary should name intent, such as enter, exit, settle, or emphasis, before choosing a timing function.
Pointer: Timing ranges live in `.agent-org/knowledge/ui/ui-feel-foundations.md`; this card must not encode fixed constants.
Rule: Motion must preserve state continuity and must not hide loading, error, or disabled states.
Rule: Reduced-motion fallback must preserve the same information as the animated path.
Claim limit: This is implementation craft guidance, not perceptual-gate evidence.
