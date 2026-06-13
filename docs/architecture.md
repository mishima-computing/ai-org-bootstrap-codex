# Architecture

AI Org Bootstrap Codex has four layers:

1. Role contracts in `roles/`.
2. Codex adapters in `.codex/agents/`.
3. Machine contracts in `schemas/` and `registry/`.
4. Deterministic runtime commands in `packages/codex-org-bootstrap` and `scripts/`.

The registry binds the first three layers together. The package validates that
binding and exposes commands for controllers.
