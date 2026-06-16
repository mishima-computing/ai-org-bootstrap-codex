# ADR-0012: Carrier containment boundary

## Status

Accepted. (Product policy for lower-trust carriers; repository policy for this edition.)

## Context

ADR-0001 says this private rebuild supports Codex adapters only. That remains true for this
repository. At the same time, ADR-0008 through ADR-0010 describe a product direction where
users can assemble, buy, and contribute agents and orgs, including lower-trust carriers.

The tension resolves by separating edition runtime from product policy. This repo is an
edition runtime with one carrier family. The product may admit other carriers only behind an
explicit trust-by-constraint boundary.

## Decision

ADR-0001 is **preserved** as this repository's edition-runtime rule:

- Codex-only adapters and invocation paths here.
- No non-Codex adapter directories.
- No fallback carriers.
- No extractor tooling.
- No registry changes to admit additional carriers.

ADR-0001 is **superseded as product-wide policy**. Product-level carrier support is allowed
only when a non-Codex carrier is contained by all of the following:

1. **Containment:** the carrier runs in an explicit isolation boundary appropriate for
   untrusted code-writing work.
2. **Logging:** all prompts, tool calls, outputs, diffs, verifier findings, and controller
   decisions are persisted as inspectable evidence.
3. **Verification:** static claims pass static Linon review and runtime compatibility passes
   the live smoke/battery regime defined by ADR-0013.

Containment generalizes ADR-0001's trust-by-constraint intent. ADR-0001 constrained trust by
admitting only the local carrier family. The product constrains trust by admitting other
carriers only inside containment, logging, and verification.

### Plane Boundary

Codex-only purity governs tracked runtime and shipped artifacts: code, registry entries,
adapters, invocation paths, and packaged repository content. Git authorship metadata is a
separate development-provenance plane, not a runtime or shipped-artifact carrier surface.
Honest attribution, including a non-Codex AI coauthor trailer, is preserved in that
provenance channel under ADR-0007. The residue checker intentionally excludes `.git` because
policing authorship metadata would collapse provenance into runtime purity and would invite
history rewrite instead of evidence discipline.

## Consequences

- This repository does not gain non-Codex runtime paths.
- The open org economy remains a product direction, not a license to edit this repo's
  adapters, registry, scripts, dependencies, or workflows.
- Claims about lower-trust carriers must name the containment, logs, and verification
  evidence that justify them, or be labeled **Hypothesis** under ADR-0011.
