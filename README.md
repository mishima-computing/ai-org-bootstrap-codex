# AI Org Bootstrap Codex

Private Codex-native operating kit for repo work under `mishima-computing`.

This repository is not a multi-carrier prompt pack. It is the Codex-only rebuild
of AI Org Bootstrap: role contracts, Codex adapters, schema-gated handoffs,
deterministic validation, and one merge-gate path.

## Operating Model

Codex main is the controller. Specialized Codex agents produce bounded artifacts:

| Agent | Role |
| --- | --- |
| `aggressive-designer` | pressure-tests scope, sequencing, and hidden assumptions |
| `conservative-designer` | preserves repo continuity, CI, dependencies, and rollback paths |
| `genius` | evidence-gated outside insight after local substrate intake |
| `aufheben-designer` | synthesizes design tension into one implementation contract |
| `implementer` | edits only files allowed by the implementation contract |
| `linon` | read-only adversarial CODE verifier (NN1–NN4 + RED tests) before PR |
| `stefan` | read-only aesthetic verifier for human-facing surfaces (design counterpart to Linon) |
| `functional-ci-action-writer` | wires existing functional checks into Actions |
| `security-ci-action-writer` | wires security checks into Actions without secrets or app edits |
| `nonfunctional-ci-action-writer` | wires existing nonfunctional checks into Actions |

The roster is 10 agents. `linon` and `stefan` are the verifier pair: Linon judges code,
Stefan judges design on rendered pixels. Both return findings that drive re-implementation;
neither claims adoption, and the owner taste-gate is final for aesthetics.

Human adoption remains outside the agents. Agents produce evidence, contracts,
patches, reviews, and gate reports; humans or repository policy decide adoption.

## Controller: semantic core + deterministic harness

The controller is two things. The **semantic core** (authoring contracts, synthesizing design
tension, judging the deliverable) needs an LLM. The **mechanical harness** must be right every
time, so it is code, not prompt:

```sh
# launches a carrier with stdin closed (no stdin-wait hang), pinned flags, carrier-discipline
# prepended, a bounded timeout with retry, and post-run scope-deviation enforcement.
python3 scripts/carrier_harness.py run --repo . --sandbox workspace-write \
    --prompt-file <contract> --allowed "demos/**" --timeout 600
python3 scripts/carrier_harness.py --self-test
```

`scripts/carrier_harness.py` owns the single carrier subprocess boundary and enforces
`bootstrap/carrier-discipline.md` and the invocation rules as code (an LLM controller forgets
`< /dev/null`; the harness cannot).

## Package

The installable artifact lives at `packages/codex-org-bootstrap` and exposes:

```sh
aob validate
aob registry check
aob merge-gate <pr> --repo <owner/name> --out .agent-runs/<run>/gates/merge-gate.json
```

For source checkout validation:

```sh
python3 scripts/validate-bootstrap-pack.py
python3 -m unittest discover -s packages/codex-org-bootstrap/tests
```

## Source Of Truth

- `registry/runtime-registry.yaml`: role, adapter, schema, write scope, and output target map.
- `roles/*.md`: human-readable role contracts.
- `.codex/agents/*.toml`: Codex adapter instructions.
- `schemas/*.json`: handoff and report validity.
- `scripts/merge-gate.py`: sole merge path.
- `scripts/carrier_harness.py`: deterministic carrier launcher + scope enforcement.
- `scripts/verify-linon-packet.py`, `scripts/stefan-aesthetic-review.py`, `scripts/measure-result-screen.py`: verifier instruments (code / aesthetics / rendered-pixel measurement).
- `THIRD_PARTY_NOTICES.md`: MIT attribution for Stefan's aesthetic libraries.
- `packages/codex-org-bootstrap`: importable deterministic runtime.

## Hard Boundary

This repository is Codex-only. It must not contain non-Codex carrier directories,
invocation procedures, adapters, or fallback instructions.
