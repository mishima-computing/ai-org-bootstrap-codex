# Contributor Roster

A contributor is a ROLE: (toolchain) x (acceptance instruments).
It is never a model. Carriers (codex, claude, local models, humans) are interchangeable
operators who take a contributor's seat; the brief/acceptance machinery guarantees the
work, not the operator (executor-independence: a leaf brief must be dead-on
reproducible by any competent operator — the Gemini-proof bar).

Leaves are unlabeled requests. Routing is contention: any seat may claim any leaf,
and the claim is the atomic creation of that leaf's `ai-org/contrib/...` branch in
git. The request's acceptance criteria decide at delivery; capability is proven by
accepted work, not declared up front. Failed or abandoned claims release the leaf
back to the pool. Simultaneous claims on the same leaf are OR-parallel competition.
Roster seats remain defined by their toolchain and acceptance instruments.

| Contributor | Toolchain | Acceptance | Status |
|---|---|---|---|
| **Implementor** | git worktree, language toolchains, functional_check | tests / CI green | ACTIVE (since the first E2E dogfood) |
| **Graphicist** | constructive SVG, FK rig, canon injection from the Reference, head-unit measurement | QA + asset-manifest conformance, measured contracts (head count, palette) | ACTIVE as isolated tool; pull-role wiring deferred until the patch series-completion discipline lifts |
| **Sculptor** | SDXL + ControlNet (pose/depth from Graphicist base bodies) + LoRA style stones | SAME manifest conformance as Graphicist + generative-content disclosure flag in provenance | PROBATION (environment live; first style stone baking) |
| **Scribe** | carrier + text contract (string keys, speaker tags, length limits, flag references) | text checks (deterministic) + tone sampling (review) | SEAT DECLARED (contract exists in #18 checklists; generic carriers likely suffice) |
| **Musician** | TBD (chiptune generation or CC procurement; cue-list spec facet already in the Reference) | cue list conformance, loop/format checks | FUTURE |
| **Quartermaster** | CC/pack sourcing (fetch_web_image network), file-level license manifests (Valyria Tear pattern) | license record completeness, style-family conformance | SEAT DECLARED (tooling prototype exists) |

Notes:
- Graphicist and Sculptor are the modeling/carving pair (additive construction vs
  subtractive revelation). They share one manifest and one acceptance surface — that
  shared contract is what lets them coexist and compete.
- Identity-bearing assets default to Graphicist(+Sculptor under contract); commodity
  high-volume assets default to Quartermaster (evidence: identity-versus-commodity
  sourcing facet).
- Reviewers (Linon at the mainline gate, Mona walkthroughs) are NOT contributors —
  they are the judging cluster around the gate, deliberately outside this roster.
