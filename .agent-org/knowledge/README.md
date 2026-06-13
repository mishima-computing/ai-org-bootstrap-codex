# Knowledge Substrate

## Layout

- `repo-map.md`: one compact pointer-style architecture map for the target repository.
- `cards/*.md`: one durable fact per card; targets create this directory when cards exist.
- `ui/*.md`: pack-level UI/UX profile cards available only when an objective names them.

## Card Format

Each card is Markdown with YAML frontmatter:

- `name`: kebab-case slug.
- `type`: `constraint`, `pitfall`, `mechanism`, or `decision`.
- `source`: originating `run_id`, PR, or URL.
- `status`: `active` or `superseded`.

The body stays at most about 10 lines: state the fact, why it matters, and pointers.

```markdown
---
name: example-validator-frontmatter
type: mechanism
source: 20260611-081949-a52ec25
status: active
---
Fact: Target knowledge cards use strict frontmatter keys.
Why: The bootstrap validator can reject malformed cards cheaply.
Pointers: `.agent-org/knowledge/README.md`, `scripts/validate-bootstrap-pack.py`.
```

## Ownership

The controller or supervisor writes cards through post-adoption distillation. `genius` and designers read cards. `genius` may propose card-worthy facts in handoff text, but never writes cards.

## UI Profile Cards

`cards/` and `ui/` intentionally use different frontmatter grammars. Repo-local `cards/` store durable adopted facts. Pack-level `ui/` cards store objective-declared UI/UX capability pointers for `conservative-designer`; see `.agent-org/knowledge/ui/README.md`.

## Updates

Append cards per adopted cycle. Supersede by flipping the old card `status` to `superseded` and adding a replacement card rather than editing history. Deduplicate during review.
