# ADR-0001: Codex Only

## Status

Accepted.

## Decision

This private rebuild supports Codex adapters only. Non-Codex invocation paths,
fallback carriers, adapter directories, and extractor tooling are out of scope.

## Consequences

- The runtime registry maps every agent to `.codex/agents/*.toml`.
- Validation fails on non-Codex carrier residue.
- The repository can optimize for Codex semantics without preserving portability.
