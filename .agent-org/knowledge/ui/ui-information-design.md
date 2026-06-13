---
profile_id: ui-information-design
scope: Human-facing information arrangement where lists, figures, and explanation density affect comprehension.
covers: figure selection; list limits; diagram intent; anti-monotony; labels; comparison structure.
freshness: Medium horizon; re-check when product Stage-A tests or accessibility review contradict figure/list guidance.
supersede_trigger: Supersede on product Stage-A contradiction or corpus evidence that reverses list-to-figure thresholds.
evidence_refs: anchor:information-design#tufte-vdqi-info-design; anchor:information-design#cairo-functional-art; anchor:information-design#few-show-me-numbers-2e; anchor:information-design#nng-data-tables; anchor:information-design#wcag22-text-alternatives; anchor:grid-layout#m3-layout-overview
---

Fact: 図解 is warranted when relationships, flow, containment, or comparison are the object of understanding; cite anchor:information-design#cairo-functional-art.
Rule: A list must become a figure when scan order alone hides dependency, hierarchy, sequence, or tradeoff; cite anchor:information-design#tufte-vdqi-info-design.
Advisory check: keep a list when items are independent and the reader's job is selection, not structure building; cite anchor:information-design#few-show-me-numbers-2e.
Decidable check: anti-monotony rules declare allowed section-shape ranges before layout; validate variation by role, not decoration; cite anchor:grid-layout#m3-layout-overview.
Rule: Labels name the relation being shown; captions name the relation or source; cite anchor:information-design#nng-data-tables.
Advisory check: figures must preserve text alternatives and source-of-truth copy for accessibility and localization; cite anchor:information-design#wcag22-text-alternatives.
Boundary: This card chooses representation form; it does not authorize new facts, claims, or product promises.
Claim class: Focus, scanability, and credibility are design-knowledge effects; conversion, engagement, and SEO are product-measurement effects per anchor:evaluation-instruments#claim-classes.
