# Edge Model Trial Report

## What was built

- Replaced normal network generation with a consumer-declared `edges` block in `patch-series-manifest.json`; dependency graph, status, readiness, and Mermaid output are projections.
- Added edge semantics for AND dependencies, OR groups, lifecycle and declared-milestone state targets, conditional `when` predicates, symmetric exclusions, and `part_of` provenance.
- Added a bounded recursive-descent `when` parser and iterative logical-chain evaluator. It supports integer and string literals, comparisons, `and`/`or`/`not`, and parentheses; malformed, over-nested, overlong, or over-termed predicates raise typed errors.
- Added the provisional branch-root `declared_patchwork_checks` field. It declares the closed, typed state-variable vocabulary for `when` predicates and state events: `counter` variables are unbounded integers with initial value `0`, and `text` variables are strings with initial value `""`.
- Added append-only `patchwork-check-events.jsonl` projection for unbounded integer counters and string flags.
- Added the deterministic gate: edge schema, target/state existence, citation existence, instantaneous cycle detection, drift lint, OR/exclusion checks, and Mermaid network generation.
- Added a legacy converter that maps `depends_on_child_keys`, `serial_after_child_key`, and unreconciled `depends_on_node_paths` into explicit edge declarations and tags targets found only in code-derived paths as drift.
- Removed prompt steering that named `spine/art-bible.json` and `spine/asset-manifest.schema.json` without checking existence; prompts now say to cite only files that exist.
- Review round 1 hardening added parser bounds, git-backed state-event projection, state-event error reporting, iterative cycle detection, child-key collision reporting, migration safety/deduplication, blocking-only instant-cycle detection, and stamped-edge prevalidation. Review round 2 closed the remaining unbounded logical-chain evaluator path.
- `patch-series-coverage-ledger.json` and `network-status-rollup.json` no longer persist `dependency_graph`; dependency order/status/Mermaid views are regenerated from node manifests.

## Provenance

| Tag | Decision or mechanism | Store term / basis |
| --- | --- | --- |
| R | Network is the truth-form; nesting is projection. | Human-ratified session decision. |
| R | Consumer-declared `edges` is the only dependency source. | Human-ratified Family-1 decision. |
| R | Build AND, OR, state-targeted, symmetric exclusion, and conditional edges now. | Human-ratified session decision. |
| R | Eligibility re-evaluates on state changes; re-arming is allowed. | Human-ratified session decision. |
| S | AND dependency semantics. | `debian package relationship field semantics`. |
| S | OR dependency alternatives. | `alternative OR dependency satisfaction`; `RPM rich/boolean dependencies as the single-field precedent`. |
| S | State-targeted ordering. | `lead lag partial overlap scheduling dependency`; `precedence diagramming method dependency types`. |
| S | Conditional dependencies. | `conditional dependency feature platform gating`. |
| S | Closed typed predicate vocabulary before evaluation. | `configuration language termination and decidability design` (`https://github.com/cel-expr/cel-go`, `https://kubernetes.io/docs/reference/using-api/cel/`); `conditional dependency feature platform gating` (`https://packaging.python.org/en/latest/specifications/dependency-specifiers/`, `https://peps.python.org/pep-0508/`, `https://docs.kernel.org/kbuild/kconfig-language.html`). |
| S | Universality ingredients. | `counter machines minimal ingredients for universality — Minsky 1961`. |
| S | Dynamic workflow re-evaluation. | `computational power of workflow and process models — Kubernetes controllers, Temporal, Airflow dynamic task mapping`. |
| S | Total gate language design. | `configuration language termination and decidability design — Dhall, Starlark, CEL`. |
| S | Validate/solve separation and drift lint network. | `computational complexity of package dependency resolution`; `dpkg-shlibdeps/cargo-udeps checker network`. |
| S | Conflict semantics. | `package conflict co-installability semantics — Debian Conflicts/Breaks, Mancoosi`. |
| P | Keep `patch-series-manifest.json` as the manifest filename. | Trial authoring choice. |
| P | `patchwork-check-events.jsonl` is the committed append-only event filename. | Trial authoring choice. |
| P | Lifecycle vocabulary is `requested -> elaborated -> claimed -> delivered -> accepted`. | Trial authoring choice. |
| P | DQ CI coverage uses a committed manifest-field fixture rather than `/tmp`. | Trial authoring choice required by CI determinism. |

## DQ Gate Findings

The live `/tmp/dq_refine_arena` repository was exported from branch `ai-org/patch-series/dq-honpen` into `/tmp/dq_edges_trial`, converted, and gated. The deterministic CI fixture in `tests/fixtures/dq_edges_trial_manifest_fields.json` captures the relevant manifest and request fields named in the trial spec.

Live gate results after conversion into `/tmp/dq_edges_trial2`:

- Nodes: 17 including root, 16 child nodes.
- Errors: 6, all `citation_missing`.
- Warnings: 19.
- Instantaneous cycles: 0.
- Mermaid projection: generated and contains all 16 child nodes.

Required DQ assertions:

- `audio_commission`, `decorative_animation_layers`, and `late_bilingual_copy` cite missing `spine/art-bible.json` and `spine/asset-manifest.schema.json`.
- Round-2 converter semantics treat `serial_after_child_key` as declared, so the prior three DQ path/serial-after divergences are no longer reported as code-derived-only drift.
- Well-foundedness passes: no instantaneous cycles.

## Review Round 1

Disposition of the 12 adversarial findings:

- F1 fixed: malformed committed predicates are inactive on eligibility paths instead of crashing pull. Ratification later moved normal `when` type mismatches to static `when_type_error`; `when_type_warning` remains only as a defensive runtime guard for adversarial direct evaluation.
- F2 fixed: predicates are capped at 4096 chars, recursive nesting including `not` is capped at 128, string/int literal parser escapes are wrapped, and integer literals are capped at 18 digits.
- F3 fixed: migration refuses `dest == src`, refuses non-empty destinations, and never removes a destination it did not create.
- F4 fixed: git-backed manifest/state-event tree listing is implemented; milestone-targeted eligibility sees branch `patchwork-check-events.jsonl`.
- F5 fixed: malformed JSONL and invalid state-event semantics become `state_event_invalid` report errors on filesystem and git gates.
- F6 fixed: cycle detection is iterative and covered at a 5000-node linear chain.
- F7 fixed: the committed DQ fixture now represents all six citation misses and all three migration divergences by exact equality.
- F8 fixed: migration contributes at most one depends edge per target and suppresses duplicate path-derived drift.
- F9 fixed: duplicate `child_key` values, including `root`, become `child_key_collision` errors with colliding paths.
- F10 fixed: unreachable `or_group_empty` was replaced by `or_group_singleton` and same-target OR-group warnings.
- F11 fixed: instant cycles are detected over the currently blocking graph only; satisfied dependencies and satisfied OR alternatives do not block.
- F12 fixed: persisted dependency graphs were removed from ledger/status projections, and `stamp_children` validates full edge form including `when` before committing.

## Review Round 2

Disposition of the four new findings and two observations:

- NEW-1 fixed: `when` predicates are capped at 256 parsed terms, and direct long `and`/`or` AST chains evaluate iteratively rather than recursively.
- NEW-2 fixed: drift lint precompiles one node-key mention pattern per gate run; a 1500-node full gate performance regression test covers the intended scale.
- NEW-3 fixed: `serial_after_child_key` is treated as a declared dependency source even when unrelated `depends_on_child_keys` entries exist.
- NEW-4 fixed: JSON booleans are rejected explicitly in state-event `set`, `inc`, and `dec` values.
- OBS-1 fixed: the git gate now checks branch-tree citation existence and drift warnings using git-read request text, matching filesystem gate coverage for these checks.
- OBS-2 fixed: the round-1 crash-proof wording was corrected to reflect the round-2 logical-chain fix.

## Ratification Update: Typed State Variable Registry

The old provisional rule that undeclared `when` identifiers default to integer `0` is OVERTURNED by ratification. Undefined variables are inadmissible in authored network data.

The branch root `patch-series-manifest.json` may declare:

```json
"declared_patchwork_checks": [{"name": "counter", "type": "counter"}, {"name": "flag", "type": "text"}]
```

This registry is the closed vocabulary for the whole branch. Every identifier in every `when` predicate must be declared there. Static validation rejects undeclared identifiers as `when_undeclared_variable`, identifier/literal or identifier/identifier type mismatches as `when_type_error`, and any `when` use without a root registry as `declared_patchwork_checks_missing`. State events also validate against the registry: undeclared writes, `inc`/`dec` on text variables, and `set` values of the wrong type become `state_event_invalid`.

Declared-but-unwritten variables read their initial value during gate evaluation: counters read `0`, text reads `""`. The runtime mismatch guard remains as a defensive totality layer for adversarial committed content, but gate-clean trees should not reach it.

## Provisional Decisions Needing Ratification

- Keep `patch-series-manifest.json` as the node manifest name.
- Use `patchwork-check-events.jsonl` as the append-only state-event log name.
- Use lifecycle states `requested`, `elaborated`, `claimed`, `delivered`, `accepted`.
- Keep compatibility with legacy fields only inside explicit migration code and legacy manifest reading.
- Use a compact committed DQ manifest-field fixture for CI because `/tmp/dq_refine_arena` is environment-local.
- OVERTURNED: undeclared `when` identifiers default to integer `0`. Ratified replacement: branch-root `declared_patchwork_checks` is the closed typed vocabulary.
- Predicate text is capped at 4096 characters, recursive nesting at 128, and integer literals at 18 digits.
- Parsed `when` predicates are capped at 256 terms to keep form validation bounded independently of character length.
- Singleton OR groups and OR groups whose members all target the same node are warnings, not errors.

## Known Gaps

- Drift lint is conservative text matching over request bodies; it catches the specified missing/over-declared classes but is not a semantic content reviewer.
