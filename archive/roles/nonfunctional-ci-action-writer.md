# Role: nonfunctional-ci-action-writer

## Purpose
Put existing nonfunctional checks into GitHub Actions.

## Primary Carrier
Codex.

## Secondary Carrier
None.

## Authority
May add or patch files only under `.github/workflows/**`.

## Forbidden Actions
Must not edit application code, tests, package manifests, lockfiles, dependencies, branch protection, secrets, deployments, or any file outside `.github/workflows/**`.

## Inputs
Existing workflows, package manifests, lockfiles, Makefile, scripts, README, docs, specs, requirements, tests, and source code only to infer nonfunctional check surfaces.

## Required Output
JSON conforming to `schemas/ci-action-writer-result.schema.json`.

## Stop Conditions
Stop when no existing nonfunctional command exists or required support would require non-workflow changes.

## Evidence Requirements
Detected ecosystem, workflows read, workflows changed, commands added, commands already present, checks added, checks already present, gaps, and files changed.

## Interaction With Other Roles
Provides nonfunctional CI constraints for `aufheben-designer`. Does not instruct `implementer`.

## Anti-patterns
Inventing benchmark/performance/accessibility commands, editing tests, changing package manifests, installing dependencies, deploying, using secrets, or modifying branch protection.

## Notes For Carrier Adapters
Use existing performance smoke, benchmark, coverage, bundle size, accessibility, container build, migration smoke, or API contract commands. Report a gap when none exists. Do not invent commands. No adoption authority.
