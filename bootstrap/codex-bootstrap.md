# Codex Bootstrap

Use this when starting an AI Org run in a target repository.

1. Confirm objective, target repo, branch, and non-goals.
2. Create `.agent-runs/<run_id>/` in the target repo and keep it ignored.
3. Record Experience Constraints, including any named UI/UX profile IDs, as intake facts.
4. Read `registry/runtime-registry.yaml` from this pack.
5. Invoke only Codex adapters from `.codex/agents/*.toml`.
6. Validate each role output against its schema before forwarding.
7. Forward named profiles verbatim to `conservative-designer` and validate its `continuity.selected_profiles` before synthesis.
8. Let `aufheben-designer` produce the only implementation contract; profile-shaped directions must be expressed in `profile_applications`.
9. Let `implementer` edit only files allowed by that contract and return `implementation_evidence` for non-empty `profile_applications`.
10. Run `scripts/profile-evidence-check.py` to verify declared profiles, selected profiles, contract applications, and implementation evidence agree.
11. Run requested checks and deterministic pack gates.
12. Open a PR.
13. Merge only through `aob merge-gate` or `scripts/merge-gate.py`.

Agents do not adopt work. Adoption belongs to the human or repository process.
