# Run Lifecycle

The controller is Codex main. It invokes Codex adapters, records artifacts, and
stops when required evidence is missing.

Default run sequence:

1. CI writers when the objective needs workflow coverage.
2. Record objective-declared Experience Constraints, including verbatim UI profile IDs.
3. `aggressive-designer`, `conservative-designer`, and `genius`; controller forwards named profiles verbatim to `conservative-designer`.
4. Validate `conservative-designer.continuity.selected_profiles` against the verbatim objective-declared profiles plus mechanically selected cards.
5. `aufheben-designer`; any profile shaping the selected direction must appear in contract `profile_applications`, or the result is a redo/escalate verdict.
6. `implementer` only when a contract is produced; non-empty `profile_applications` require corresponding `implementation_evidence` in the implementation result.
7. Run `scripts/profile-evidence-check.py` with the declared profile IDs, conservative result, contract, and implementation result.
8. Reviewer profiles when configured.
9. Local checks.
10. PR creation.
11. Merge gate.

Runtime artifacts belong under `.agent-runs/<run_id>/` and must not be committed.
