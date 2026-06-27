# Agent skills for the implementer — weak by the evidence, worth it only trade-off-free


Companion to `docs/implementer-defect-taxonomy-and-gate-skill-strategy.md`. That doc covers the **gate** (hard
enforce). This covers the **skill** (soft prime): what the evidence actually says, and the only way it pays.

## What the evidence says: skills are weak for software engineering, and self-generated skills can hurt

The 2026 skill evals are unrefereed and vendor-adjacent, but directionally consistent and sobering:

- **SWE-Skills-Bench** ("Do Agent Skills Actually Help in Real-World SE?"): **39 of 49 skills give zero pass-rate
  improvement, avg +1.2%, token overhead up to +451%, 3 skills *hurt*** (arXiv 2603.15401).
- **SkillsBench**: curated skills +16.6 pts on average but with huge variance (+4.5 software-eng … +51.9 healthcare),
  ~16/84 tasks *negative*; **self-generated skills ≈ −1.3 pts** (arXiv 2602.12670).
- **"In the Wild"**: benefit collapses as realism rises — force-loaded 55.4% → agent-selects 51.2% → +distractors
  43.5% → retrieve-from-collection 40.1% → no-skills 35.4%; "loading ≠ utilizing" (arXiv 2604.04323).

So a naive "generate a Skill.md per task" plan is *risky*: mass / self-generated skills are exactly the configuration
the evidence says is weak-to-harmful, and the token overhead is real.

## The principle: a weak skill is still a free win if it carries no trade-off

The harm in the evidence is the **trade-off**, not the skill: token overhead, distractor noise, and auto-generated
quality. Each is eliminable, and once eliminated even a weak +10% (or just a single defect-class) is pure upside —
the same logic by which a cheap gate that captures 10% of a rejection class still pays.

| The harm | Its source | How it is removed |
|---|---|---|
| +451% token overhead | the skill body sits in context | **progressive disclosure** — only L1 metadata (~100 tok) is always loaded; the body loads on trigger |
| benefit collapses with distractors | irrelevant skills dilute attention | **deterministic routing** — load a skill *only* when it actually applies, so there are no distractors |
| self-generated ≈ −1.3 pts | auto-generated skills are noise | **curated + narrow** — a small library, one skill per defect class, human/spec-authored, not mass-generated |
| a skill that primes wrong | soft guidance is not enforcement | **pair every skill with its gate** — the skill primes, the deterministic gate backstops; a mis-prime is harmless |

## How the implementer shell injects skills (deterministic, before the carrier launches)

The implementer host is a Python-cored shell around the carrier. Its skill step is deterministic — no LLM routing:

1. **Classify the task** deterministically: the deliverable-kind classifier + task-type signals (rename / integration
   / migration, surfaced from the objective and scope) + language and touched paths.
2. **Select** the matching curated skill(s) from the library by that classification (a lookup keyed on
   defect-class / task-type / `paths:` globs — not a description-match the carrier might miss).
3. **Write** the selected `SKILL.md`(s) to the carrier's *actual* skill path before launch. Paths are **not**
   portable: Codex reads `.agents/skills/` (and nested `AGENTS.md`), the sibling-carrier runtime reads only `.sibling/skills/`.
   Target the runtime in use; do not assume cross-tool portability.
4. **Scope** the carrier with `--cd <subtree>` from the localizer (PreLocalization / defect-locus) — the strongest
   single reliability lever (multi-file tasks fail; small file sets succeed) and the fix for the large-repo no-op
   (auto-compaction can't be disabled, so the cure is never letting context grow that far).
5. **Launch** the carrier. It reads the skills natively, builds in a scoped working set, and the gates enforce the
   outcome afterward.

## Where skills sit among the three levers

- **Scope (`--cd` / localize)** — the #1 reliability factor by the evidence. Build this first.
- **Gate (deterministic, ~0-FP)** — the hard enforce; reward-hack-resistant; each gate harvests a Linon skip.
- **Skill (curated, narrow, deterministically routed, progressively disclosed, gate-paired)** — the weak-but-free
  prime. Add to the extent it carries no trade-off; even a small gain is upside.

Each defect class becomes a **gate + skill + shell-injection** triple: e.g. *incomplete rename* → forbidden_patterns
gate (enforce) + a narrow rename skill primed only on rename tasks ("handle snake_case; the gate greps the old token").

## Sources

- SKILL.md / Agent Skills: (vendor)/engineering/equipping-agents-…; (vendor docs) agent-skills overview &
  best-practices; agentskills.io/specification; developers.openai.com/codex/skills; the sibling-carrier runtime issue #31005
  (`.agents/skills` portability, unanswered).
- Skill evals: arXiv 2603.15401 (SWE-Skills-Bench), 2602.12670 (SkillsBench), 2604.04323 ("In the Wild").
- Scope / no-op: SWE-Bench Pro multi-file (arXiv 2509.16941); Codex auto-compaction not-disableable (issue #11716,
  #16068 the context-window trap); localize-then-edit / Agentless (arXiv 2407.01489).
