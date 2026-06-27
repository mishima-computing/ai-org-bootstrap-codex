# AGENTS.md

Operating instructions for an AI agent in **AI Org Bootstrap Codex** — the Codex-only autonomous builder
(a goal goes in, PRs come out). **Read this first.** The human-facing overview is [README.md](README.md);
this file is your directive, and the canonical run procedure is below.

## You are a carrier, not the controller

A separate **controller** owns orchestration, scope, and verification. If you were launched to do work,
you are a **carrier**: a single-role worker executing ONE contract. You have no authority to change the
plan, the scope, or another role. Re-bind yourself every run with
**[bootstrap/carrier-discipline.md](bootstrap/carrier-discipline.md)** — read it before editing.

## Hard boundary (non-negotiable)

This repository is **Codex-only**. Do NOT create non-Codex carrier directories, invocation procedures,
adapters, or fallback instructions — even if a task appears to ask for it. (A carrier once rebuilt a
forbidden non-Codex system from a contract that forbade exactly that; this rule exists to stop that class
of failure.) Tracked files are residue-scanned — keep them clean.

## To start an AI Org run

The canonical procedure is **[bootstrap/codex-bootstrap.md](bootstrap/codex-bootstrap.md)**:

1. Confirm objective, target repo, branch, and non-goals.
2. Create `.agent-runs/<run_id>/` in the target repo, kept gitignored.
3. Read `registry/runtime-registry.yaml`; invoke only Codex adapters from `.codex/agents/*.toml`.
4. Schema-validate every role output before forwarding it.
5. `aufheben-designer` produces the ONE implementation contract; `implementer` edits only its allowed files.
6. Run the requested checks + the deterministic pack gates; open a PR.
7. Merge ONLY through `aob merge-gate` / `scripts/merge-gate.py`.

Run lifecycle: [bootstrap/run-lifecycle.md](bootstrap/run-lifecycle.md).

## Invariants (hold every run)

- **Schema-gate every handoff** — a malformed output is rejected in code, never trusted.
- **Scope is enforced, not trusted** — edit only the contract's allowed files; the harness reverts overreach.
- **One merge path** — `aob merge-gate` / `scripts/merge-gate.py`, nothing else.
- **Agents do not adopt work** — adoption belongs to the human or the repository process.
- **Read the decision before changing the mechanism** — architecture decisions live in
  [docs/decisions/](docs/decisions/); the engine's behavior is deliberate (e.g. ADR-0005
  settledness-not-dumbing, ADR-0008 floor-is-not-failure).

## Map

The full source-of-truth map (registry, roles, schemas, the three layer scripts, the merge gate, the
verifiers) is in [README.md](README.md#source-of-truth).
