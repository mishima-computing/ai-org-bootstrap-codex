# Run Lifecycle

The controller is Codex main. It invokes Codex adapters, records artifacts, and
stops when required evidence is missing.

Default run sequence:

1. CI writers when the objective needs workflow coverage.
2. `aggressive-designer`, `conservative-designer`, and `genius`.
3. `aufheben-designer`.
4. `implementer` only when a contract is produced.
5. Reviewer profiles when configured.
6. Local checks.
7. PR creation.
8. Merge gate.

Runtime artifacts belong under `.agent-runs/<run_id>/` and must not be committed.
