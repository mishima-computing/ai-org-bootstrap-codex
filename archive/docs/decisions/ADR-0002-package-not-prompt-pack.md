# ADR-0002: Package, Not Prompt Pack

## Status

Accepted.

## Decision

The shipped artifact is an importable Python package with an `aob` CLI. Markdown
remains role and process documentation; deterministic behavior belongs to code.

## Consequences

- CI validates package importability and registry consistency.
- Merge-gate behavior is exposed as a command.
- Future install flows should target `aob install --target <repo>`.
