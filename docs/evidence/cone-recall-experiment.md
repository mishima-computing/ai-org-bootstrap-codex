# Evidence: the dependency-cone Linon-scoping experiment

Grounds the ADR-0006 decision to REJECT dependency-cone scoping of Linon.

## What was tested

Whether scoping Linon's reads to a changed module's import/importer **cone** loses bug-catching versus a
full-repo read. Two seeded bugs on a built ~60-module repo, each reviewed by Linon at two scopes:

- **G (in-graph control):** rename a dataclass field, breaking a direct importer (in the cone).
- **N (non-graph):** add an extra payload key that only violates a JSON Schema referenced by path — the
  schema is not a module, so it is outside every import cone.

## Committed raw artifact

`data/cone-recall-summary.json` — per-run seconds, web_search count, module count, schema presence:

| run | seconds | web_search | py modules | schema present |
| --- | --- | --- | --- | --- |
| G-ingraph-full | 112 | 0 | 47 | yes |
| G-ingraph-cone | 189 | 0 | 13 | no |
| N-nongraph-full | 129 | 0 | 47 | yes |
| N-nongraph-cone | 254 | 0 | 4 | no |

## Findings — and an honest confound

- **web_search fired 0×** in all four runs (these are repo-grounded bugs, not external-fact bugs), so the
  "pruning reduces web cost" idea is **untested**, not confirmed.
- The experiment was **confounded**: the read-only carrier sandbox reads the whole filesystem, so the
  cone runs could read the pruned files from sibling checkouts. That confound **is** the load-bearing
  finding: **cone scoping is not enforceable** without filesystem isolation (a box/microVM with only the
  cone mounted). Separately, full-Linon cited ~48 unchanged files for a one-line diff, indicating the
  broad scan is load-bearing.
- Cone runs were also **slower** than their full counterparts here (189>112, 254>129), so the
  speed rationale for cone scoping is not supported by this run.
- **n = 1 per cell**, confounded — a directional probe, not a clean recall comparison. The ADR-0006
  rejection rests on the *unenforceability* + *against-grain* findings, which this artifact supports;
  it does **not** rest on a clean recall delta.

## Harness

External (sibling plane, per ADR-0012): the `linon_experiment.py` harness in the sibling tooling repo
seeded the bugs, pruned the cone copies, ran live Linon, and recorded the table. Re-execution requires
that harness plus live carriers and, for a clean result, filesystem-isolated runs.
