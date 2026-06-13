# UI/UX Absorption Ledger

Status: Cycle 7 mapping plus authorized process/evaluation absorption. Contract `uiux-cycle7-20260612-cc18fdd-process-eval-claimclass` authorizes process/evaluation anchors, five claim-class rewrites, validator data rows, URL-rider notes, UX Writing stay-stub conditions, manifest, and row-7 closure only.

Related docs: [domain map](domain-map.md), [knowledge architecture](knowledge-architecture.md), [authoring program](authoring-program.md).

Legend: `absorbed` means the rule has a target area for future anchors/cards. `superseded-later` means future authoring may replace current wording after anchor citation lands. `retained-as-hypothesis` means the rule remains narrow or unverified until stronger evidence exists.

## README Format Law

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `.agent-org/knowledge/ui/README.md:5` | UI/UX cards are pack-level pointers for explicit design-capability objectives. | Process Methodology | absorbed | Positive rewrite: future controller template says "forward named UI/UX profiles verbatim only." |
| `.agent-org/knowledge/ui/README.md:5` | Cards do not authorize selector inference of design intent. | Process Methodology | absorbed | Structural check: selector cannot add UI profiles absent objective-declared Experience Constraints. |
| `.agent-org/knowledge/ui/README.md:9` | Required card frontmatter keys are fixed. | Process Methodology | absorbed | Structural check: validator keeps required-key check. |
| `.agent-org/knowledge/ui/README.md:9` | Optional `exemplars` key is allowed. | Evaluation Instruments | absorbed | Structural check: exemplar format remains pointer-only and optional. |
| `.agent-org/knowledge/ui/README.md:11` | Evidence refs cap at 6 semicolon-separated pointers. | Process Methodology | absorbed | Structural check: validator cap remains until anchor citation resolution replaces raw evidence pressure. |
| `.agent-org/knowledge/ui/README.md:11` | Body cap is 12 nonblank lines. | Process Methodology | absorbed | Structural check: validator cap remains L3 card law. |
| `.agent-org/knowledge/ui/README.md:11` | No embedded research excerpts. | Process Methodology | absorbed | Structural check: future validator flags block quotes/long excerpts; positive rewrite is pointer plus anchor ID. |
| `.agent-org/knowledge/ui/README.md:13` | Exemplars cap at 4 pointers and use `locale-pinned-URL@YYYY-MM-DD -> pattern-slug`. | Evaluation Instruments | absorbed | Structural check: validator exemplar parser keeps cap and date-pinned format. |
| `.agent-org/knowledge/ui/README.md:13` | Examples stay subordinate to evidence refs. | Evaluation Instruments | absorbed | Positive rewrite: exemplars support hypotheses, never acceptance evidence. |
| `.agent-org/knowledge/ui/README.md:15` | Cards carry reusable UI/UX constraints only. | Process Methodology | absorbed | Structural check: pack/repo boundary review for product-specific facts. |
| `.agent-org/knowledge/ui/README.md:15` | Product-specific worldview cards stay repo-local under `.agent-org/knowledge/cards/`. | Process Methodology | absorbed | Structural check: manifest/path boundary; delete only if #32 boundary is replaced by a stronger pack policy. |
| `.agent-org/knowledge/ui/README.md:19` | Objectives may name profiles in Experience Constraints. | Process Methodology | absorbed | Positive rewrite: objective intake records named profiles. |
| `.agent-org/knowledge/ui/README.md:19` | Controllers forward profile names verbatim. | Process Methodology | absorbed | Structural check: controller disclosure compares forwarded names to objective names. |
| `.agent-org/knowledge/ui/README.md:19` | `conservative-designer` may use only named profiles. | Process Methodology | absorbed | Structural check: proposal continuity selected_profiles subset check. |
| `.agent-org/knowledge/ui/README.md:19` | `selected_profiles` cap is 5. | Process Methodology | absorbed | Structural check: existing schema/validator cap path. |

## Exemplars Index

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `.agent-org/knowledge/ui/exemplars.md:3` | Index records date-pinned corpus pointers only. | Evaluation Instruments | absorbed | Structural check: exemplar entries require URL date pins. |
| `.agent-org/knowledge/ui/exemplars.md:3` | Entries are examples for named composition patterns. | Composition | absorbed | Positive rewrite: exemplar must cite pattern slug and related anchor/card ID. |
| `.agent-org/knowledge/ui/exemplars.md:3` | Entries are not evidence excerpts or adoption claims. | Evaluation Instruments | absorbed | Positive rewrite: exemplar status is hypothesis support only; delete prohibition after validator has exemplar-purpose field. |
| `.agent-org/knowledge/ui/exemplars.md:5` | Tailscale pointer supports proof-artifact density per view and is fetch-verified. | Composition | retained-as-hypothesis | Keep as date-pinned exemplar, not acceptance evidence. |
| `.agent-org/knowledge/ui/exemplars.md:6` | DeepSeek `/en` pointer supports one-lead-language per view and root diverges. | Typography: CJK+Latin | retained-as-hypothesis | Structural check: exact locale URL required; prohibition on bare domain becomes URL equality check. |
| `.agent-org/knowledge/ui/exemplars.md:7` | Antgroup pointer supports corporate-trust register but is owner-recorded and unverified pending #44. | Genre Grammars: Corporate Trust | retained-as-hypothesis | Unverified exemplar; cannot be acceptance evidence until capture tooling or fetch verification exists. |

## ui-bilingual-typography.md

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `ui-bilingual-typography.md:3-7` | Scope, coverage, freshness, supersede trigger, and evidence refs for CJK+Latin surfaces. | Typography: CJK+Latin | absorbed | Structural check: future card cites CJK/Latin anchor IDs and keeps freshness trigger. |
| `ui-bilingual-typography.md:10` | CJK glyph density changes visual mass relative to Latin. | Typography: CJK+Latin | absorbed | Destination: `anchor:typography-cjk-latin#jlreq-line-composition`; positive rewrite keeps per-script validation. |
| `ui-bilingual-typography.md:11` | Choose one lead language per view. | Typography: CJK+Latin | absorbed | Destination: `anchor:typography-cjk-latin#jlreq-line-composition`; structural check: lead script declared per view. |
| `ui-bilingual-typography.md:11` | Use support-language treatment by default. | Typography: CJK+Latin | absorbed | Positive rewrite: secondary language role named in layout spec. |
| `ui-bilingual-typography.md:11` | Forbid both-prominent treatment with no focal point. | Hierarchy and Gestalt | absorbed | Destination: `anchor:hierarchy-gestalt#nng-visual-hierarchy`; positive rewrite requires a declared focal decision before assigning equal prominence. |
| `ui-bilingual-typography.md:12` | Maintain per-script size, leading, and measure token sets. | Typography: CJK+Latin | absorbed | Destination: `anchor:typography-cjk-latin#bringhurst-measure-rhythm` plus `anchor:typography-cjk-latin#jlreq-line-composition`; structural check: token audit requires script-specific values. |
| `ui-bilingual-typography.md:12` | Never share one token set across CJK and Latin. | Typography: CJK+Latin | absorbed | Destination: `anchor:typography-cjk-latin#bringhurst-measure-rhythm`; positive rewrite landed as "provide per-script token sets." |
| `ui-bilingual-typography.md:13` | English and Japanese measure ranges are research budgets, not constants. | Typography: CJK+Latin | absorbed | Positive rewrite: cite anchor ranges and require product validation. |
| `ui-bilingual-typography.md:13` | One container cannot serve both scripts. | Grid and Layout | absorbed | Destination: `anchor:grid-layout#css-grid-2`; positive rewrite requires mixed containers to declare per-script behavior and layout constraints. |
| `ui-bilingual-typography.md:14` | Correct mixed-script spacing and punctuation squeeze per Heti/JLREQ/CLREQ convention. | Typography: CJK+Latin | absorbed | Positive rewrite: anchor IDs for spacing classes replace local detail. |
| `ui-bilingual-typography.md:15` | Enforce contrast floors by script and display size. | Accessibility | absorbed | Structural check: WCAG 2.2 contrast criteria cited and tested on worst-case image region. |
| `ui-bilingual-typography.md:16` | Translation-only preserves structure; localization may change IA and visual design. | Information Architecture | absorbed | Positive rewrite: localization divergence decision record. |
| `ui-bilingual-typography.md:17` | Bind micro-rules to JLREQ/CLREQ instead of restating exceptions in cards. | Typography: CJK+Latin | absorbed | Destination: `anchor:typography-cjk-latin#jlreq-line-composition` and `anchor:typography-cjk-latin#clreq-mixed-spacing`; local pointer prose superseded by anchor citations. |
| `ui-bilingual-typography.md:18` | Legacy claim-limit line separated legibility/focus from conversion, engagement, and SEO outcomes. | Evaluation Instruments | absorbed | Positive rewrite: allowed-claim field lists supported effect class; delete phrase after validator enforces claim class. |
| `ui-bilingual-typography.md:19` | Pointers list CJK, cross-cultural, line length, text-over-images, and spacing sources. | Typography: CJK+Latin | absorbed | Supersede raw pointer list with anchor citations during typography cycle. |

## ui-composition-patterns.md

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `ui-composition-patterns.md:3-8` | Scope, coverage, freshness, supersede trigger, evidence refs, and exemplars for composition propositions. | Composition | absorbed | Structural check: future card cites composition anchor IDs; exemplars remain date-pinned. |
| `ui-composition-patterns.md:11` | Proof-artifact density has objective-declared floor or band validated by product tests. | Composition | absorbed | Structural check: intake requires floor/band. |
| `ui-composition-patterns.md:11` | Proof-artifact density is objective-specific rather than a pack constant. | Composition | superseded-later | Positive rewrite: "objective declares proof density band"; delete negative wording after validator checks threshold source. |
| `ui-composition-patterns.md:12` | Reject token proof-objects. | Composition | superseded-later | Structural check: proof object must map to visible state, institutional fact, or capability. |
| `ui-composition-patterns.md:12` | Proof explains a user-visible state, institutional fact, or capability. | Composition | absorbed | Positive rewrite: proof-object inventory field. |
| `ui-composition-patterns.md:13` | Consecutive same-type section cap is objective-declared. | Grid and Layout | absorbed | Citation-plan Destination for cycle 6: `anchor:grid-layout#m3-layout-overview`; structural check: section-sequence scan. |
| `ui-composition-patterns.md:13` | Cap prevents monotony while preserving narrative grouping. | Hierarchy and Gestalt | absorbed | Citation-plan Destination for cycle 6: `anchor:hierarchy-gestalt#upod-gestalt-principles`; positive rewrite: repeated sections require grouping rationale. |
| `ui-composition-patterns.md:14` | Repeated sections earn rhythm through changed task, evidence, or decision function. | Composition | absorbed | Structural check: each repeated section names function. |
| `ui-composition-patterns.md:15` | One lead language per view declares focus script and secondary support. | Typography: CJK+Latin | absorbed | Structural check: lead-language field. |
| `ui-composition-patterns.md:16` | Plain-problem lead vs jargon lead follows audience fluency and risk, not internal preference. | UX Writing | absorbed | Positive rewrite: lead-copy choice records audience fluency and risk. |
| `ui-composition-patterns.md:17` | Named propositions carry per-objective thresholds before implementation. | Process Methodology | absorbed | Structural check: intake threshold record. |
| `ui-composition-patterns.md:18` | Legacy claim-limit line separated focus, scanability, and credibility from conversion, engagement, and SEO. | Evaluation Instruments | absorbed | Structural check: claim-class allowlist replaces prose prohibition. |

## ui-corporate-trust-genre.md

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `ui-corporate-trust-genre.md:3-7` | Scope, coverage, freshness, supersede trigger, and evidence refs for corporate trust surfaces. | Genre Grammars: Corporate Trust | absorbed | Structural check: corporate anchor IDs and short-horizon freshness remain. |
| `ui-corporate-trust-genre.md:10` | K.K. provenance is owner-recorded and narrow until repeated. | Genre Grammars: Corporate Trust | retained-as-hypothesis | Positive rewrite: mark source class as hypothesis until second engagement. |
| `ui-corporate-trust-genre.md:11` | First view establishes company personhood before campaign posture when trust is the job. | Genre Grammars: Corporate Trust | absorbed | Structural check: first-view credibility job and personhood proof fields. |
| `ui-corporate-trust-genre.md:12` | Disclosure gravity changes tone and requires sober register/audit-friendly hierarchy. | Genre Grammars: Corporate Trust | absorbed | Positive rewrite: disclosure adjacency flag changes hierarchy/tone requirements. |
| `ui-corporate-trust-genre.md:13` | Decision register names proof objects supporting credibility per view. | Composition | absorbed | Structural check: proof-object register. |
| `ui-corporate-trust-genre.md:14` | Trust-vs-LP register separates assurance from persuasion before copy/layout polish. | Genre Grammars: Corporate Trust | absorbed | Structural check: register selected before implementation. |
| `ui-corporate-trust-genre.md:15` | Avoid borrowed startup landing-page tempo for corporate legitimacy judgment. | Genre Grammars: Corporate Trust | absorbed | Destination: `anchor:genre-corporate-trust#nng-corporate-credibility`; positive rewrite landed as register-selection record for primary legitimacy judgment. |
| `ui-corporate-trust-genre.md:16` | Short-horizon guidance until second engagement or Stage-A ratification. | Process Methodology | retained-as-hypothesis | Structural check: freshness date and ratification evidence required. |
| `ui-corporate-trust-genre.md:17` | Legacy claim-limit line separated focus, scanability, and credibility from conversion, engagement, and SEO. | Evaluation Instruments | absorbed | Structural check: claim-class allowlist replaces prose prohibition. |

## ui-feel-foundations.md

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `ui-feel-foundations.md:3-7` | Scope, coverage, freshness, supersede trigger, and evidence refs for interaction feel. | Interaction and Feedback | absorbed | Structural check: future card cites interaction/motion anchor IDs. |
| `ui-feel-foundations.md:10` | Feel surfaces express cause, state, response, continuity, and recovery. | Interaction and Feedback | absorbed | Positive rewrite: state matrix names each property. |
| `ui-feel-foundations.md:11` | Feedback-as-proof uses `proves:` statements. | Interaction and Feedback | absorbed | Structural check: each effect has a `proves` field. |
| `ui-feel-foundations.md:12` | Use timing ranges and product tests. | Motion | absorbed | Positive rewrite: timing band with product-test note. |
| `ui-feel-foundations.md:12` | Do not encode fixed constants in pack cards. | Motion | superseded-later | Structural check: validator rejects unanchored fixed timing constants in cards. |
| `ui-feel-foundations.md:13` | Motion, audio, haptics, copy, and visual state tell the same story. | Interaction and Feedback | absorbed | Structural check: multimodal coherence matrix. |
| `ui-feel-foundations.md:14` | Every multimodal cue needs silent fallback and reduced-motion-safe behavior. | Accessibility | absorbed | Structural check: fallback and reduced-motion fields required. |
| `ui-feel-foundations.md:15` | Juiciness may increase appeal/expressiveness; do not claim usability/performance gains. | Evaluation Instruments | absorbed | Positive rewrite: allowed effect class is appeal/expressiveness unless user test evidence is cited. |
| `ui-feel-foundations.md:16` | Pointers name juiciness, duration, and Material motion sources. | Motion | absorbed | Supersede raw pointer list with dated HIG/M3/NN-g anchor citations. |

## ui-gacha-genre.md

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `ui-gacha-genre.md:3-7` | Scope, coverage, freshness, supersede trigger, and evidence refs for gacha flows. | Genre Grammars: Game/Gacha | absorbed | Structural check: future gacha card cites genre/motion/accessibility anchors. |
| `ui-gacha-genre.md:10` | Reveal ceremony separates anticipation, rarity signal, and item identity. | Genre Grammars: Game/Gacha | absorbed | Positive rewrite: reveal sequence fields. |
| `ui-gacha-genre.md:11` | Rarity is signaled before item. | Genre Grammars: Game/Gacha | absorbed | Structural check: reveal order lists rarity before identity or records exception. |
| `ui-gacha-genre.md:11` | Never hide odds or material constraints behind spectacle. | Genre Grammars: Game/Gacha | absorbed | Destination: `anchor:genre-gacha#cesa-guideline-20160427` and `anchor:genre-gacha#joga-guideline-index`; positive structural check records pre-draw odds/material-constraint visibility. |
| `ui-gacha-genre.md:12` | Rarity language is consistent across copy, color, audio, motion, and inventory state. | Interaction and Feedback | absorbed | Structural check: rarity-token consistency matrix. |
| `ui-gacha-genre.md:13` | Entrance, suspense, rarity confirmation, item reveal, and recovery must be skippable. | Motion | absorbed | Structural check: skip/reduced-motion path for each ceremony phase. |
| `ui-gacha-genre.md:14` | Genre conventions stale quickly and vary by market. | Genre Grammars: Game/Gacha | retained-as-hypothesis | Positive rewrite: dated market-source requirement per authoring cycle. |
| `ui-gacha-genre.md:15` | Short-horizon guidance until Stage-A spec ratifies it. | Process Methodology | retained-as-hypothesis | Structural check: freshness trigger plus Stage-A ratification record. |
| `ui-gacha-genre.md:16` | Pointers name issue research, yatai pilot, and current convention source. | Genre Grammars: Game/Gacha | absorbed | Supersede raw pointers with anchor IDs and dated convention source. |

## ui-information-design.md

| Source lines | Current rule | Target area | Status | Graduation annotation |
| --- | --- | --- | --- | --- |
| `ui-information-design.md:3-7` | Scope, coverage, freshness, supersede trigger, and evidence refs for information arrangement. | Information Design | absorbed | Structural check: future card cites information-design anchor IDs. |
| `ui-information-design.md:10` | Diagrams are warranted when relations, flow, containment, or comparison are the object. | Information Design | absorbed | Structural check: representation decision records relation type. |
| `ui-information-design.md:11` | A list becomes a figure when scan order hides dependency, hierarchy, sequence, or tradeoff. | Information Design | absorbed | Structural check: list-vs-figure decision test. |
| `ui-information-design.md:12` | Keep a list when items are independent and selection is the reader job. | Information Design | absorbed | Positive rewrite: selection job maps to list representation. |
| `ui-information-design.md:13` | Anti-monotony rules declare allowed section-shape ranges before layout. | Grid and Layout | absorbed | Citation-plan Destination for cycle 4: `anchor:grid-layout#m3-layout-overview`; structural check: section-shape ranges in intake. |
| `ui-information-design.md:13` | Validate variation by role, not decoration. | Composition | absorbed | Positive rewrite: each variation names role. |
| `ui-information-design.md:14` | Labels name the relation being shown. | Information Design | absorbed | Structural check: label relation field. |
| `ui-information-design.md:14` | Decorative captions do not count as information design. | Information Design | superseded-later | Structural check: caption must name relation or source; delete negative wording after check lands. |
| `ui-information-design.md:15` | Figures preserve text alternatives and source-of-truth copy. | Accessibility | absorbed | Structural check: alt text and source copy fields. |
| `ui-information-design.md:16` | Card chooses representation form and does not authorize new facts, claims, or product promises. | Process Methodology | absorbed | Structural check: representation decision cannot add product claims; positive rewrite in intake template. |
| `ui-information-design.md:17` | Legacy claim-limit line separated focus, scanability, and credibility from conversion, engagement, and SEO. | Evaluation Instruments | absorbed | Structural check: claim-class allowlist replaces prose prohibition. |

## Unmapped Rules

Zero. Every line-bearing README rule and every line-bearing rule from the seven existing UI files has a target area and status above.

## Cycle 3 Color and Accessibility Decisions

| Item | Status | Graduation annotation |
| --- | --- | --- |
| `ui-bilingual-typography.md:15` accessibility citation plan | citation-plan | Destination: `anchor:accessibility#wcag22-recommendation`; contrast criteria stay scope-owned by `anchor:color#wcag22-contrast-criteria`; structural check: future WCAG mapping table names criterion ID and test method. |
| `ui-feel-foundations.md:14` accessibility citation plan | citation-plan | Destination: `anchor:accessibility#wcag22-recommendation` plus component planning through `anchor:accessibility#wai-aria-apg`; structural check: future fallback/reduced-motion matrix names test method. |
| `ui-information-design.md:15` accessibility citation plan | citation-plan | Destination: `anchor:accessibility#wcag22-recommendation`; use `anchor:accessibility#wcag2ict` only when non-web ICT scope is explicit; structural check: future representation record names alt/source-copy acceptance evidence. |
| Scope-keyed federation ownership | ownership-decision | WCAG IDs remain area-owned when the decision scope is criterion-specific: typography owns `anchor:typography-cjk-latin#wcag22-visual-presentation`, hierarchy owns `anchor:hierarchy-gestalt#wcag22-focus-appearance`, color owns `anchor:color#wcag22-contrast-criteria`; accessibility owns conformance-planning pointers only. |
| ISO status migration | migration-plan | `anchor:typography-cjk-latin#iso40500-status` remains typography-owned this cycle; migration is deferred to the typography cycle, with the same-ID URL corrected to `https://www.iso.org/standard/91029.html` after controller verification that `91933` returned 404 and `91029` returned 200 on 2026-06-12. |
| Card-body retirement count | retirement-record | Zero card-body lines retired in cycle 3; existing cards keep their current bodies until a future contract authorizes citation retargeting. |

## Cycle 4 IA and Information Design Decisions

| Item | Status | Graduation annotation |
| --- | --- | --- |
| Authorization | implementation-contract | `contract-20260612-091325-6e81d85-uiux-cycle4-ia-information-design` authorizes IA and information-design anchors, the bounded `ui-information-design.md` citation update, validator data deltas, manifest rows, and planning-doc status updates only. |
| `ui-information-design.md:15` accessibility citation plan supersession | supersession-record | Cycle 3 destination `anchor:accessibility#wcag22-recommendation` is superseded for figure/representation text alternatives by `anchor:information-design#wcag22-text-alternatives`; use `anchor:accessibility#wcag2ict` only when non-web ICT scope is explicit. |
| `ui-information-design.md:13` anti-monotony phrase check | retirement-record | Validator phrase requirement for `anti-monotony rules` retires because the card now cites `anchor:grid-layout#m3-layout-overview` and CARD_REQUIRED_ANCHOR_SLUGS requires a resolving `grid-layout` citation. |
| `ui-information-design.md:14` caption prohibition allowlist | retirement-record | The `do not count as information design` allowlist row retires because card wording graduated to "captions name the relation or source" with destination `anchor:information-design#nng-data-tables`. |
| `ui-bilingual-typography.md:16` IA citation plan | citation-plan | Future authorized cycle may cite `anchor:information-architecture#nng-ia-vs-navigation` for localization divergence; this cycle intentionally does not edit `ui-bilingual-typography.md`. |

## Cycle 5 Interaction, Motion, and URL Persistence Decisions

| Item | Status | Graduation annotation |
| --- | --- | --- |
| Authorization | implementation-contract | `contract-20260612-093339-f45eb86-uiux-cycle5-interaction-motion-url-persistence` authorizes interaction/motion anchors, `ui-feel-foundations.md` citations, URL-persistence policy, controller-run URL liveness script, validator deltas, manifest rows, and planning-doc status updates only. |
| `ui-feel-foundations.md:12` fixed-constant prohibition | retirement-record | Destination: `anchor:motion#nng-animation-duration` and `anchor:motion#m3-easing-duration-tokens`; card wording graduated to positive timing-ranges with range rationale, and validator keeps the fixed-ms structural check. |
| `ui-feel-foundations.md:15` usability/performance claim limit | retirement-record | Destination: `anchor:motion#m3-expressive-motion` plus evaluation-instrument claim class; card wording now federates usability-performance gains into product measurement. |
| `ui-feel-foundations.md` raw evidence refs | graduation-record | Destination: resolving citations for `anchor:interaction-feedback#apple-hig-feedback`, `anchor:interaction-feedback#m3-interaction-states`, `anchor:interaction-feedback#nng-visibility-system-status`, `anchor:motion#nng-animation-duration`, `anchor:motion#m3-easing-duration-tokens`, and `anchor:accessibility#wcag22-recommendation`. |
| Issue #51 URL-persistence rule | rule-record | Anchor pointers declare persistence class through the `Date/version` idiom: dated-permalink, edition-pinned, or living with `checked YYYY-MM-DD; re-check on <trigger>`; validator enforces the idiom offline and controller-run `scripts/check-anchor-urls.py` proves liveness only. |
| Controller dated-URL synthesis prohibition | graduation-delete | The controller prompt prohibition on dated-URL synthesis is deleted effective this ratification because the positive rule, offline validator, and controller-run liveness script land in this contract; controller executes the external prompt edit. |
| `ui-gacha-genre.md:13` motion skip citation PLAN | citation-plan | Cycle 6 Destination: `anchor:motion#wcag22-motion-criteria` for pause/stop/hide and animation-from-interactions scope, plus `anchor:motion#nng-animation-duration` for duration bands; this cycle intentionally does not edit `ui-gacha-genre.md`. |
| `ui-gacha-genre.md:13` reduced-motion citation PLAN | citation-plan | Cycle 6 Destination: `anchor:motion#apple-hig-motion` and `anchor:motion#wcag22-motion-criteria`; structural check: every ceremony phase names same-information reduced-motion behavior. |

## Cycle 6 Composition and Genre Grammar Decisions

| Item | Status | Graduation annotation |
| --- | --- | --- |
| Authorization | implementation-contract | `contract-20260612-100305-2993b5f-uiux-cycle6-composition-genre-grammars` authorizes three thin anchors, three card rewrites, validator data deltas, manifest rows, Kao correction, exemplar-class rule, and SaaS stay-stub note only. |
| `ui-composition-patterns.md:13` section-cap citation | graduation-record | Rows 69-70 landed with `anchor:grid-layout#m3-layout-overview` and `anchor:hierarchy-gestalt#upod-gestalt-principles`; section sequence and grouping rationale remain decidable from intake/audit records. |
| `ui-corporate-trust-genre.md:15` startup-tempo phrase | retirement-record | Destination: `anchor:genre-corporate-trust#nng-corporate-credibility`; allowlist row removed after card wording graduated to a register-selection check. |
| `ui-gacha-genre.md:11` odds/constraints phrase | retirement-record | Destination: `anchor:genre-gacha#cesa-guideline-20160427` plus `anchor:genre-gacha#joga-guideline-index`; allowlist row removed after card wording graduated to pre-draw visibility audit record. |
| `ui-gacha-genre.md:13` motion citation plan | graduation-record | Rows 170-171 landed with `anchor:motion#wcag22-motion-criteria`, `anchor:motion#apple-hig-motion`, and `anchor:motion#nng-animation-duration`; skip and reduced-motion paths are card-visible. |
| CHI-2024-Kao named research | refutation-record | Four-search/controller outcome found no stable usable ACM DOI for the Kao reference; substitute is Yin & Xiao CHI 2022 DOI 10.1145/3491102.3517642 for RRM reveal/player experience only. Re-verify if the owner supplies an exact 2024 Kao DOI. |
| Exemplar-class rule | rule-record | `exemplars.md` entries are hypothesis-class pointers; antgroup remains unverified pending #44 and is excluded from acceptance evidence. |
| SaaS/Operational Tools | stay-stub-record | 2026-06-12 stay-stub follows cycle-1 Latin precedent: available anchors alone are insufficient without a named deliverable decision and detection instrument. |

## Cycle 7 Process, Evaluation, and Claim-Class Decisions

| Item | Status | Graduation annotation |
| --- | --- | --- |
| Authorization | implementation-contract | `uiux-cycle7-20260612-cc18fdd-process-eval-claimclass` authorizes process/evaluation anchors, claim-class rewrites, validator data/code delta, DOI/quarto liveness rider handling, UX Writing stay-stub conditions, manifest rows, and program row-7 closure only. |
| `ui-bilingual-typography.md:18` claim-limit phrase | retirement-record | Destination: `anchor:evaluation-instruments#claim-classes`; card wording now names legibility/focus as design-knowledge effects and conversion/engagement/SEO as product-measurement effects. |
| `ui-composition-patterns.md:18` claim-limit phrase | retirement-record | Destination: `anchor:evaluation-instruments#claim-classes`; card wording now names focus/scanability/credibility as design-knowledge effects and conversion/engagement/SEO as product-measurement effects. |
| `ui-corporate-trust-genre.md:17` claim-limit phrase | retirement-record | Destination: `anchor:evaluation-instruments#claim-classes`; card wording now names focus/scanability/credibility as design-knowledge effects and conversion/engagement/SEO as product-measurement effects. |
| `ui-information-design.md:17` claim-limit phrase | retirement-record | Destination: `anchor:evaluation-instruments#claim-classes`; card wording now names focus/scanability/credibility as design-knowledge effects and conversion/engagement/SEO as product-measurement effects. |
| `ui-feel-foundations.md:15` product-measurement wording | retirement-record | Destination: `anchor:evaluation-instruments#claim-classes`; appeal/expressiveness stays design-knowledge class, while usability-performance gains require product measurement. |
| UX Writing | stay-stub-record | 2026-06-12 stay-stub follows cycle-1/6 precedent: verified anchors exist for Microsoft Writing Style Guide, Apple HIG Writing, Material 3 UX writing, and NN/g error-message guidelines, but promotion requires a named copy decision plus copy-audit instrument. |
| Process Methodology | demote-condition-record | `anchor:process-methodology#process-checklist` names row-7 closure and future intake review as citation consumers; demote to Stub if no consumer cites it by the next authoring-program revision. |
| HP benchmark readiness | owner-question-record | `anchor:evaluation-instruments#hp-benchmark-readiness` records preconditions and row-8 owner questions for scope, pass/fail rubric, and constraints; benchmark is not run in cycle 7. |
| DOI liveness rider | verification-idiom-record | `doi.org` joins documented bot-block hosts after recorded probe evidence: DOI 10.1145/3491102.3517642 redirected 302 to `dl.acm.org` then returned 403 on 2026-06-12. Compensating verification for DOI pointer changes is `https://api.crossref.org/works/<doi>`. |
| Quarto liveness rider | controller-probe-record | `anchor:hierarchy-gestalt#upod-gestalt-principles` keeps the `quarto.com` pointer; controller evidence says the page is live, so the script receives the browser-class UA plus HTTP/1.1 request profile and no documented-host fallback unless a later controller probe fails. |
| ISO 9241-210 rider | controller-probe-record | `anchor:process-methodology#iso9241-210-human-centered-design` uses ISO record id 77520 under the verified-ISO-URL idiom; genius client saw 403, so controller should record whether the post-fix probe is 200 or documented-host evidence. |

## Cycle 8 Computable Spatial Decisions

| Item | Status | Graduation annotation |
| --- | --- | --- |
| `htmlcss-computable-spatial.md` follow-up-4 instrument | graduation-record | Destination: `scripts/check-spatial.py` implements overflow/page-widening, fixed-element bounds, opaque-solid contrast, and tap-target advisory checks; spacing-token modulo, paint-based contrast #44, and safe-area assertions remain named follow-ups. |
