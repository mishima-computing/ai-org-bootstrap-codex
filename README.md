# ai-org-bootstrap-codex — clean slate (2026-06-28)

Direction change. The prior AI Org engine (all generations of the executor — legacy splitter/frontier, the
in-process recursive TaskExecutor, and the distributed-branch retrofit — together with its ADRs and design docs)
was judged a contaminated/broken model. To build the next executor **new and uncontaminated**, the entire prior
codebase and its design record have been moved under [`archive/`](archive/) — preserved, not deleted.

## What is preserved (nothing is lost)

- Everything previously at the repo root is under `archive/` on this branch (`clean-slate-rebuild`).
- The full prior history remains on its branches (e.g. `main`, `feat/distributed-branch-executor`,
  `feat/deterministic-transform-tools`, …) and on `origin` (43 remote branches), plus stashes.

## Why

A fresh build must not *mix* with the broken model. The old code and the old ADRs (which encode the confused
lineage) are archived so the new executor is designed and implemented clean.

## Next

Design and build the new executor here, on a clean tree, from a fresh design conversation — not by retrofitting
anything under `archive/`.
