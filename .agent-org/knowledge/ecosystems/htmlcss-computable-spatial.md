---
profile_id: htmlcss-computable-spatial
scope: HTML/CSS spatial implementation rules that can become deterministic checks.
covers: contrast math; non-text contrast; target size; spacing tokens; overflow and safe-area assertions.
freshness: Medium horizon; re-check on WCAG revision or when follow-up-4 lint diverges.
supersede_trigger: Supersede when #40 follow-up-4 lint lands or diverges from these check shapes.
evidence_refs: issue:#40-follow-up-4; scripts/check-spatial.py; https://www.w3.org/TR/WCAG22/#contrast-minimum; https://www.w3.org/TR/WCAG22/#non-text-contrast; https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html; https://developer.mozilla.org/en-US/docs/Web/CSS/env
---

Instrument: `scripts/check-spatial.py` implements the computable subset and reports bounded JSON findings.
Decidable check: opaque-solid-background contrast-ratio math floor is >=4.5:1 for text, >=3:1 for large text under WCAG 2.2 SC 1.4.3 AA.
Decidable check: non-text contrast floor is >=3:1 for graphical objects and component states under WCAG 2.2 SC 1.4.11 AA when the effective background is opaque solid; state contrast is deferred.
Advisory check: tap-target bounding-box floor is >=24x24 CSS px per SC 2.5.8 AA; record named WCAG exceptions instead of failing them.
Decidable check: spacing-scale modulo validates values against repo tokens only; label failures as repo-token convention, not a CSS or WCAG standard.
Decidable check: overflow assertions cover horizontal scroll risk, clipped focus rings, and fixed elements crossing viewport bounds.
Decidable check: safe-area assertions require viewport-edge fixed UI to account for CSS env safe-area insets when used.
Claim limit: Spacing-token and safe-area shapes remain spec-feed propositions until later lint slices.
