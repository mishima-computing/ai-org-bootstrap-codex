# Codex Bootstrap

Use this when starting an AI Org run in a target repository.

1. Confirm objective, target repo, branch, and non-goals.
2. Create `.agent-runs/<run_id>/` in the target repo and keep it ignored.
3. Read `registry/runtime-registry.yaml` from this pack.
4. Invoke only Codex adapters from `.codex/agents/*.toml`.
5. Validate each role output against its schema before forwarding.
6. Let `aufheben-designer` produce the only implementation contract.
7. Let `implementer` edit only files allowed by that contract.
8. Run requested checks and deterministic pack gates.
9. Open a PR.
10. Merge only through `aob merge-gate` or `scripts/merge-gate.py`.

Agents do not adopt work. Adoption belongs to the human or repository process.
