---
profile_id: htmlcss-modern-layout
scope: Conservative modern HTML/CSS layout choices for production UI implementation.
covers: grid/flex choice; container queries; cascade layers; logical properties; clamp fluid type; Tailwind v4 theme tokens.
freshness: Medium horizon; re-check on Tailwind major release, Baseline status change, or WCAG revision.
supersede_trigger: Supersede when Baseline status or Tailwind v4 token guidance contradicts this card.
evidence_refs: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_cascade/At-rule/@layer; https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_containment/Container_queries; https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_logical_properties_and_values; https://developer.mozilla.org/en-US/docs/Web/CSS/clamp; https://tailwindcss.com/docs/theme
---

Rule: Use CSS grid for two-axis page or panel placement and flexbox for one-axis distribution; keep source order meaningful.
Rule: Container queries are allowed only as Baseline widely available layout adaptation, not as browser-risk novelty.
Rule: Cascade layers are allowed as Baseline widely available ordering boundaries; keep layer ownership explicit.
Rule: Prefer logical properties where direction, writing mode, or inline/block semantics can vary.
Rule: Use `clamp()` for bounded fluid type or spacing only when min and max bounds are explicit.
Tailwind v4 rule: Map repo tokens through CSS-first `@theme` variables; re-check on Tailwind major release.
Claim limit: Only Baseline widely available features are named here.
