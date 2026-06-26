# ADR-0019: The CI-writer is a deterministic workflow AUTHOR — declare deps, fail-closed at the unknown, and never commit a workflow that was only seen GREEN

## Status

Accepted (the architecture and the gates; the engine wiring is forward work). Governs the **engine only** —
the CI-writer roles (`functional-ci-action-writer`, `security-ci-action-writer`,
`nonfunctional-ci-action-writer`) and the root CI stage that runs them
(`scripts/controller_goal.py:_run_root_ci_writers` ~:1295). Applies, refines, and is constrained by:

- **ADR-0009** — the verification boundary: *judgement chooses an executable contract; deterministic
  machinery enforces it* (`B produces an executable A`). The CI-writer is exactly a "B produces A" device:
  it AUTHORS an executable artifact (a workflow) that a deterministic executor (GitHub Actions) enforces.
- **ADR-0016** — *How after Why*, the falsifiability gate: an acceptance that "cannot fail when the WHY is
  unmet proves nothing"; a check observed only GREEN is a **survived mutant** and is rejected (D2). A
  generated CI is itself such a check, and is governed by the same red-then-green rule.
- **ADR-0011 / ADR-0012** — untrusted generators over a small trusted kernel; the synthesizer is a proposer,
  not an authority; **unproven never passes**.
- **ADR-0008 / ADR-0005** — a deterministic, LLM-free scaffold primitive is the trusted way to instantiate
  structure; *settledness, not dumbing* — remove the LLM only from the parts that are mechanically decidable.

## Context

The current CI-writer role does what ADR-0009 forbids: it lets an LLM **hand-list** the facts a machine
should derive. The live evidence is `shagiri/.github/workflows/functional-checks.yml`. Its own header
comment proves the role *understood* the problem — "the tests import jsonschema / stripe; the repo declares
no manifest, so CI installs them here" — and then solved it by emitting

```yaml
run: python3 -m pip install --upgrade pip jsonschema stripe
```

That hand-listed set is **incomplete**, so the workflow is RED on `main` (every recent run fails) while the
same tests PASS locally. This is open-loop LLM prediction of a dependency closure: incomplete by
construction, and worse than no CI because *a check that fails on a missing dependency is a broken workflow,
not a signal*. The role file
(`roles/functional-ci-action-writer.md`) and its result schema
(`schemas/ci-action-writer-result.schema.json`) today instruct the carrier to "install (in the workflow) the
dependencies the checks import … detect them from the checks' own imports" — i.e. they *codify* the
open-loop prediction as the contract. That is the defect to remove.

Five web-grounded findings frame the fix (surveyed 2026-06; used as grounding, not re-researched here):

1. **Determinism wins on the strict metric.** On EnvBench ("imports-satisfied + clean-exit") a deterministic
   script beat the best LLM agent (15.9% vs 6.69% Python). Repo2Run reaches 86%, but its single biggest
   lever is a **rule-based** Dockerfile synthesizer (−72.2% without it), not the LLM. No system reliably
   generates first-run-passing CI; the universal weakest link is **undeclared dependencies**.
2. **The 90/10 split.** Fully deterministic, zero-LLM: (a) stack detection (Linguist-style ordered
   signature), (b) **declared**-dependency install (a real manifest/lockfile), (c) test discovery (every
   framework defines it by convention — pytest `test_*.py`, `go test ./...`, Jest/Vitest/Surefire), (d) CI
   skeleton selection. The irreducible ~10% is **undeclared system/native deps** (libpq, cgo `.so`) — every
   deterministic tool (CNB, Nixpacks, Heroku) hits this wall and exposes a manual escape hatch.
3. **The fail-closed dependency fixpoint** for missing *language* deps (emitted as a step GitHub runs): run
   → catch `ModuleNotFoundError` → read `e.name` (don't string-parse) → `mod = e.name.split('.')[0]` → if
   `mod in sys.stdlib_module_names` **ESCALATE** → if first-party repo module **ESCALATE** (PYTHONPATH) →
   resolve module→distribution in priority order [`importlib.metadata.packages_distributions()`; a curated
   alias table (cv2→opencv-python, PIL→Pillow, yaml→PyYAML, sklearn→scikit-learn); Wheelodex] → if AMBIGUOUS
   or EMPTY **ESCALATE** (never guess — pipreqs' name==import fallback is CVE-2023-31543 dependency
   confusion) → `pip install <dist>` → if `ResolutionImpossible`/downgrade **ESCALATE** → add `mod` to a
   permanent attempted-set (monotone guard) → retry. Terminate on green or no-progress. Import→distribution
   is **not** deterministically solvable for *uninstalled* packages (PEP 794), so the curated table +
   escalation are load-bearing — but a healthy mainstream repo whose only sin is a missing manifest
   (Shagiri's exact case) is nearly fully covered.
4. **Trustworthiness = negative control.** A CI observed only GREEN is UNVERIFIED. Before committing, prove
   it can BOTH pass on good input AND fail on bad input (TDD-red / mutation / scientific negative-control
   converge here). Statically reject false-green constructs an LLM emits to "make CI pass": missing
   `set -euo pipefail`, `|| true`, `continue-on-error: true`, assertion-free / silently-skipped steps. Lint
   with `actionlint`; dry-run with `act` (necessary, not sufficient — `act` diverges from GitHub). No single
   scanner suffices; layer them.
5. **Author-knows-its-deps.** When the AI Org **authors** the repo (its dogfood, e.g. Shagiri), the deps are
   not an inference problem — the org wrote the imports. The primary path is to **declare a real manifest**
   as a first-class deliverable and install from it; the runtime fixpoint (3) is the *fallback*.

## Decision

**The CI-writer is a deterministic workflow AUTHOR, not a CI engine.** GitHub Actions is the hermetic
executor. The CI-writer's single job is to WRITE a `.github/workflows/*.yml` that is GREEN on healthy code
and RED only on real failure. The deterministic pipeline runs as **steps GitHub executes**, never inside the
AI Org's box. An LLM must not hand-list dependencies, commands, or any mechanically-derivable fact into the
YAML (ADR-0009: judgement chooses the contract; the machine derives and enforces it).

### D1 — Two clocks: AUTHOR-time (in the box, deterministic) vs CI-runtime (on GitHub)

The boundary is explicit and load-bearing.

**AUTHOR-time stages** (run once in the AI Org box, by the deterministic CI-writer kernel — zero LLM for
anything decidable):

1. **Stack detection** — Linguist-style ordered signature over the repo (extensions, shebangs, manifest
   presence). Output: the ordered set of ecosystems (python, node, go, …).
2. **Declare-or-detect deps** — see D2. The *preferred* output is a committed manifest the org authored;
   the *fallback* output is a self-bootstrapping resolution step embedded in the workflow.
3. **Test discovery** — derive the invocation by each framework's *convention*, not by asking an LLM:
   pytest `test_*.py` / `unittest discover`, `go test ./...`, Jest/Vitest/Surefire patterns. Where the repo
   already lists explicit check commands (Shagiri's matrix), preserve them verbatim — discovery only fills
   gaps, never overrides an author's explicit list.
4. **Skeleton selection** — pick a trusted, parameterized workflow template per ecosystem (the ADR-0008 /
   ADR-0005 deterministic scaffold move, applied to `.github/workflows`), fill the holes from 1–3.
5. **Static false-green rejection + lint** — see D4 (the negative-control gate's static half).
6. **Negative-control proof** — see D4 (the dynamic half). **A workflow that is not proven red-then-green is
   never committed.**

**CI-runtime stages** (emitted *as steps in the YAML* for GitHub to run on every push/PR): `checkout` →
`setup-<runtime>` → install-from-manifest (D2 declared path) **or** the fail-closed resolution fixpoint (D2
fallback path) → run discovered/explicit checks under `set -euo pipefail`. These are the steps; the AI Org
never runs them itself — it only proves, at author-time, that they will behave.

The mechanically-derivable facts (the dependency closure, the test invocation, the runtime version) are
derived by the kernel or resolved at CI-runtime by the fixpoint — **never predicted by an LLM into the
YAML**. The LLM's residual role is judgement only (D5): naming the ecosystem's intent when the signature is
genuinely ambiguous, and authoring the structured escalation when a fact is undecidable.

### D2 — Dependency handling: DECLARE first, fixpoint as fallback, ESCALATE at the unknown (fail-closed)

Three tiers, in strict priority:

- **DECLARE (primary, author-time).** If the org authored the repo (dogfood) or the repo lacks a manifest
  for an ecosystem it clearly uses, the CI-writer's preferred output is to **commit a real manifest**
  (`requirements.txt` / `pyproject.toml` dependency list) as a first-class deliverable, derived from the
  actual imports the org itself wrote, and have the workflow `pip install -r requirements.txt`. This is the
  author-knows-its-deps reframe (finding 5). NOTE: this **widens the CI-writer's write authority** — see D6.
- **FIXPOINT (fallback, CI-runtime step).** When a manifest is absent or known-incomplete and the org did
  not author the imports, the workflow embeds the finding-3 fail-closed resolver as a step: run → catch
  `ModuleNotFoundError` → `e.name` → stdlib/first-party ⇒ escalate → resolve via
  `packages_distributions()` + curated alias table → ambiguous/empty ⇒ escalate → install → resolution-
  impossible/downgrade ⇒ escalate → monotone attempted-set → retry; terminate on green or no-progress. This
  replaces the hand-listed `pip install jsonschema stripe` with a closed loop that converges on the *actual*
  closure or escalates.
- **ESCALATE (the fail-closed terminus).** At every ambiguous branch the resolver does **not guess** (guess
  = typosquat/dependency-confusion, CVE-2023-31543). Escalation is a **first-class structured outcome**, not
  a silent skip and never a fabricated green:
  - the author-time kernel emits a **needs-info finding** (`ci_writer_escalation`, severity-tagged) into the
    role's result and the goal's findings stream — the same shape as ADR-0016's `goal_acceptance` shadow
    record;
  - the emitted workflow, for an undecidable CI-runtime case, **fails the step explicitly** (`exit 1` with
    a diagnostic naming the unresolved module/distribution) — a RED that means "human must declare this",
    never a `|| true` skip;
  - the author-time gate **HOLDs the commit** of any workflow whose negative-control proof did not pass
    (D4). An escalation is therefore a *flagged finding + an honest RED*, never a quietly-green YAML.

### D3 — The emitted workflow's concrete shape

Per detected ecosystem the template fills to:

```yaml
on: { pull_request: {}, push: { branches: [main] } }
permissions: { contents: read }
jobs:
  <ecosystem>:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-<runtime>@vN      # version from detection, not from an LLM
      - run: |                                # declared path
          set -euo pipefail
          pip install -r requirements.txt
      - run: |                                # discovered/explicit checks
          set -euo pipefail
          <discovered or author-listed invocation>
```

Hard constraints the static gate (D4) enforces on the emitted YAML: `set -euo pipefail` on every multi-line
`run`; **no** `|| true`, `continue-on-error: true`, or assertion-free/silently-skipped step; every check
either passes truthfully or fails loudly. The fixpoint fallback substitutes the resolver script for the
`pip install -r` line; everything else is identical.

### D4 — The negative-control gate: PROVE red-then-green before committing (the meta-gate)

A workflow the CI-writer has only seen GREEN is UNVERIFIED and **must not be committed** (ADR-0016 D2;
ADR-0011 "unproven never passes", applied to *the workflow as a check*). Before `_commit_root_ci_changes`,
the author-time kernel runs a two-sided proof, layered (finding 4 — no single scanner suffices):

- **Static half (necessary).** `actionlint` for schema/shell correctness; a deterministic false-green
  scanner that REJECTS the constructs in D3. Failure here ⇒ HOLD, do not commit.
- **Dynamic half (the negative control).** Reproduce the workflow's check steps in a hermetic sandbox (via
  `act` and/or a local container that mirrors the runtime), and require **both**:
  - **GREEN on good input** — the unmodified repo at HEAD passes (proves the workflow is not gratuitously
    red);
  - **RED on a negative control** — inject a withheld counter-example the check MUST reject (a deleted
    symbol / a planted failing assertion / a removed declared dependency) and require the workflow to go
    RED. A workflow that stays green on the negative control is a **fake guard** and is rejected.

  `act` diverges from GitHub, so it is *necessary, not sufficient*: the first real GitHub run on the merge
  commit is the confirming oracle. Until that run is observed green-on-good, the committed workflow's trust
  is **advisory** (ADR-0016's "advisory / needs-info — never a fabricated green"), and a non-green first run
  re-opens the gate as a finding rather than silently passing.

If either side of the negative-control proof cannot be established (e.g. the outcome is not cheaply
executable, or `act`/sandbox is unavailable), the gate is **fail-closed**: HOLD the commit and emit the
escalation finding. It never degrades to "commit it and hope."

### D5 — The LLM's residual role is judgement only

After D1–D4 the carrier (Codex) does not author facts. It is invoked only where the boundary genuinely
requires judgement: disambiguating a stack signature the deterministic detector reports as tied; choosing
the ecosystem template when several plausibly apply; and **authoring the human-readable escalation** when a
fact is undecidable (which module, why it could not be resolved, what a human must declare). Choosing
*means* (which template, how to phrase the escalation) is the role's; it must never choose *ends* (fabricate
a dependency name, weaken a check, or mark an unproven workflow green) — ADR-0016 D5.

### D6 — Integration: what stays, what changes

- **Stays.** The three CI-writer roles and their identities; `CI_WRITER_ROLES`
  (`scripts/controller_pipeline.py:47`); the opt-in control shell (`_ci_writers_enabled` ~:112,
  `_root_ci_writers_enabled` `scripts/controller_goal.py` ~:1271, default OFF via `CI_WRITERS_ENABLED`); the
  single ROOT CI stage that runs after composition and before acceptance (`_run_root_ci_writers` ~:1295,
  commit "root ci workflows" via `_commit_root_ci_changes` ~:1279); write-scope isolation in disjoint
  worktrees (`_apply_worktree_changes` ~:1422) so CI edits never collide with the implementer's.
- **Changes.**
  1. The role becomes a **deterministic kernel + judgement adapter**, not an LLM that hand-lists deps. The
     kernel (a new `scripts/ci_writer_kernel.py` invoked by `_run_root_ci_writers`) performs D1's stages and
     D4's gate; the carrier is consulted only for D5 judgement.
  2. `roles/functional-ci-action-writer.md` (and the security/nonfunctional siblings) drop the "detect them
     from the checks' own imports" instruction — that open-loop prediction is exactly the defect — and
     instead instruct: declare a manifest where the org owns the imports, else emit the fixpoint step, and
     **escalate, never guess**.
  3. The result **schema** (`schemas/ci-action-writer-result.schema.json`) gains a required
     `negative_control` block (good-input result, negative-control description, red/green observed) and an
     `escalations` array (`ci_writer_escalation` findings), so a result that committed an unproven workflow
     is schema-invalid. `files_changed`'s `^\.github/workflows/` pattern is **relaxed** to also permit the
     declared manifest paths (D2 DECLARE / D6 write-authority widening), gated to manifest filenames only.
  4. `_commit_root_ci_changes` is gated on the D4 proof: it commits only when the negative-control gate
     passed; otherwise it emits the escalation finding and commits nothing.
- **Backward compatibility.** CI writers remain default-OFF; nothing changes for runs that don't opt in. The
  emitted YAML shape is a superset of today's (adds `set -euo pipefail`, a manifest-install or fixpoint step,
  and removes hand-listed installs), so an opted-in run produces a *stricter* workflow, not an incompatible
  one.

## Consequences

- The Shagiri failure class is closed at the root: the incomplete `pip install jsonschema stripe` is
  replaced by either a committed manifest (org authored the imports) or a converging fixpoint, and a workflow
  is never committed until proven red-then-green. RED on `main` stops meaning "the CI-writer guessed wrong."
- "Unproven never passes" (ADR-0011) extends from the artifact to **the workflow that checks the artifact**:
  a generated CI is itself a check, gated by the same falsifiability discipline as any acceptance (ADR-0016).
- The LLM is removed from every mechanically-decidable step (ADR-0005 settledness) and confined to judgement
  + escalation authoring, where it cannot fabricate a false-green.
- Widening write authority to manifests (D2) is a real boundary move: the CI-writer now touches repo deps,
  not only `.github/workflows`. It is constrained to *adding* a derived manifest and gated by the same
  isolation (D6) — but it is a genuine increase in the role's blast radius and is recorded as such.

## Scope & limits (named, fails CLOSED — never a false-green)

These stay irreducibly hard; at each, the design escalates rather than emits a wrong-package or false-green
workflow:

- **Undeclared system / native deps** (libpq, cgo `.so`, a C toolchain) — the universal ~10% wall (finding
  2). No language-level resolver recovers them. The workflow's step **fails loudly** naming the missing
  native dependency, and the kernel emits an escalation pointing at the manual escape hatch (Aptfile /
  image extension). It never silently `apt-get`-guesses.
- **Namespace-package / ambiguous module→distribution** — one import maps to many distributions; PEP 794
  says this is undecidable for uninstalled packages. The resolver ESCALATES on ambiguity rather than picking
  one (the pipreqs name==import fallback is CVE-2023-31543). Bounded recall, high precision, by design.
- **Version conflicts** — a `ResolutionImpossible` or a satisfied-package downgrade is an ESCALATE, never an
  override; the CI-writer does not edit the repo's *existing* constraints (D6 widens to *adding* a manifest,
  not rewriting locked constraints).
- **Non-cheaply-executable outcomes** (D4's dynamic half is unavailable) — the gate HOLDs the commit and
  escalates; it never degrades to commit-and-hope (ADR-0016 risk: the falsifiability gate degenerates to a
  proxy where there is no cheap oracle; there it defers to a human, not a fabricated green).
- **`act` ≠ GitHub** — local proof is necessary, not sufficient; the first real GitHub run is the confirming
  oracle and a non-green first run re-opens the gate as a finding (D4).
