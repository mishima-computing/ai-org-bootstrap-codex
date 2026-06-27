# Evidence: role-level timing and build∥review pipelining

Grounds the measured claims reused by ADR-0006/0007 (and referenced by ADR-0010).

## What was measured

One AI-org build of a 6-stage target, run twice:

- **Serial** — for each stage, `conservative-designer → implementer → linon` run one after another.
- **Pipelined** — `designer → implementer` on the live tree, with `linon(N)` run **concurrently**
  with the build of stage N+1 (read-only, on a git-worktree snapshot of stage N's commit).

## Committed raw artifacts

- `data/role-timing-serial.json` — per-stage, per-role wall-clock seconds for the serial run.
- `data/role-timing-pipelined.json` — the pipelined run.
- Recomputation (sum per role; totals are the `stage:"total"` rows):

| role | seconds | share |
| --- | --- | --- |
| implementer | 1849 | **45%** |
| linon | 1238 | **30%** |
| conservative-designer | 988 | **24%** |
| **serial total** | **4074** | |
| **pipelined total** | **3124** | reclaim **23.3%**, i.e. **77% of Linon's time hidden** under the next build |

These reproduce the figures stated in ADR-0006 (45/30/24; 23.3%; 77%).

## Harness, scope, honesty

- **Harness is external (sibling plane, per ADR-0012):** the measurement was driven by the
  `record_kenney.py` `role_build` / `role_build_pipelined` driver in a sibling tooling repo, which
  ran live Codex carriers through the role pipeline and recorded each turn's wall-clock. The **data
  about this org's behaviour is committed here**; full re-execution requires that external harness plus
  live carriers.
- **n = 1** (a single 6-stage build per arm). This is a **directional** measurement, not a statistical
  one; carrier latency and API-rate contention vary run to run.
- The pipelining reclaim (23.3%) is **below** Linon's 30% share because the final stage's review has
  nothing to hide under and two concurrent carriers contend on the same API rate limit — recorded as
  expected, not as a clean 30%.
