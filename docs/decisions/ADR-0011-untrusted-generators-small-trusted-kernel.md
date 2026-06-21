# ADR-0011: Reliability is a small trusted kernel over untrusted generators — the verification architecture and its pre-LLM lineage

## Status

Accepted. Refines and extends **ADR-0009** (the verification boundary: design chooses an executable contract;
deterministic machinery enforces it), builds on **ADR-0008** (the floor / deterministic scaffold), and records
the general principle behind **ADR-0010** (the producer self-adversary was rejected). Some decisions here are
already in place; the rest are forward decisions, telemetry- or engine-gated in the same way ADR-0009's
investments are. This ADR exists because the working slogan "capability lives in the *mechanism*, not the
*minds*" is imprecise, and the imprecision matters for what we build and how we measure it.

## Context

Two related claims have been driving the architecture:

1. **mechanism over minds** — the system's capability should come from its *structure* (decomposition, an
   adversarial dialectic, deterministic gates), not from any single agent being intelligent; so a "fleet of
   ordinary agents" plus strong structure should outperform one expensive smart agent.
2. **the all-dumb benchmark** — once the structure is solid, drop per-agent intelligence drastically and see
   whether the structure still carries the result; the gap between all-dumb and all-smart measures how much
   capability is structural.

Both are directionally right, and both are *imprecise* in a way the pre-LLM record corrects sharply. The
question every pre-LLM coding-automation system fought — *how do you get reliable output from an unreliable
generator?* — has a settled shape of answer, and that shape narrows our claims:

- **Intelligence does not vanish; it relocates.** Across program synthesis, deductive synthesis,
  knowledge-based automation, and model-driven engineering, "automating programming" never removed the
  intelligence — it *moved* it into the DSL/grammar, the candidate space, the specification and the oracle, the
  decomposition, the fitness function, the abstraction, and the verifier. "Dumb agents" is therefore precise
  only about the **proposal** layer. If the **specification** layer or the **certification** layer is also weak,
  the mechanism does not fail loudly — it converges *efficiently on a wrong artifact*. A mechanism is a faithful
  amplifier of its oracle; a weak oracle is amplified into confident error.

- **The strongest prior is not redundancy; it is a small trusted kernel.** The most reliable historical
  structures are not N-version voting but: an arbitrarily complex, *untrusted* search that produces a candidate,
  and a *small, trusted* checker that admits it — and verification done **per artifact**, with the artifact
  carrying its own evidence. This is a stronger and cheaper invariant than diversity, and it is what the gates
  here actually are.

- **An all-dumb benchmark that varies only agent IQ is mis-specified.** Because intelligence relocates, holding
  the spec/DSL/oracle fixed while lowering agent IQ measures the wrong thing — it credits the structure for work
  that is actually pre-paid into a hand-built oracle. The embedded knowledge must itself be an independent
  variable.

The defect distribution that motivated ADR-0009 still holds (≈55% of work units miss first review; ≈40% of
steps are repair). This ADR records the trust model those gates implement and the upgrades the lineage mandates.

## Decision

### D1 — Trust model: untrusted generators, a small trusted kernel, verification per artifact

Treat every generative role (the three designers, `aufheben`, the `implementer`, repair) as **arbitrarily
untrusted**. Concentrate trust in a **small certifier** — the deterministic gates — that verifies the **built
artifact**, never the model that produced it, and keep that trusted base small and auditable. Do not try to make
the model trustworthy; make the checker small and the verification local.

- *Grounding.* LCF — an arbitrarily complex, possibly buggy *tactic* may search for a proof, but a theorem
  exists only if it passes a tiny inference **kernel** (Milner, 1972, *Logic for Computable Functions:
  Description of a Machine Implementation*). Proof-carrying code — the producer ships the artifact together with
  a proof that a *small* checker validates, so the consumer trusts the checker, not the producer (Necula, 1997,
  *Proof-Carrying Code*). Translation validation — rather than prove a whole generator/compiler correct, verify
  *each individual output* (Pnueli, Siegel & Singerman, 1998, *Translation Validation*).
- *Status.* In place in spirit (gates verify the artifact, not the agent); the explicit "small, audited trusted
  base" framing is the new commitment.

### D2 — Evidence-carrying artifacts

An accepted artifact must **carry its evidence**: the contract it was checked against, the gate results, the
counterexamples that were turned into regressions, the toolchain/dependency hashes, the assumptions made, and
the list of properties left *unproven*. A re-run or a downstream consumer re-validates with the small checker,
not by re-trusting the producer.

- *Grounding.* Proof-carrying code (Necula, 1997) — the artifact carries the proof; modern provenance (in-toto /
  SLSA-style signed evidence binding, already an open item in ADR-0009) is the same idea for supply chains.
- *Status.* Forward. Generalises ADR-0009's `GateResult{status, contract_sha, artifact_sha}` into a full,
  content-addressed evidence bundle attached to the leaf commit.

### D3 — Counterexample classification before repair (CEGAR, not just CEGIS)

A counterexample is **not** fed blindly back to the generator. It is first **classified** — implementation
defect / specification defect / abstraction (gate-model) defect / environment difference / verifier defect — and
routed accordingly: an implementation defect re-runs the implementer; a specification defect re-runs `aufheben`;
a *verifier or oracle* defect repairs the **gate**, not the artifact; an environment difference is quarantined,
not "fixed" in the code.

- *Grounding.* Counterexample-guided **abstraction refinement** decides whether a counterexample is *real* or
  *spurious* and, if spurious, refines the abstraction rather than blaming the implementation (Clarke, Grumberg,
  Jha, Lu & Veith, 2000, *Counterexample-Guided Abstraction Refinement*). Contrast counterexample-guided
  inductive **synthesis**, which uses the counterexample to constrain the next candidate (Solar-Lezama et al.,
  2006, *Combinatorial Sketching for Finite Programs*). We need both moves, and we need to tell them apart.
- *Status.* Forward. The existing targeted repair routing (`_repair_roles_for`) and the "repair the oracle, not
  the product" clause in ADR-0009 are the seed; this names the discipline and adds the explicit classifier.

### D4 — Unproven is not pass (safe-baseline fallback)

A timeout, an SMT "unknown", a flaky test, a crashed validator, or a skipped gate is **not** a pass. It is
**unproven**, a distinct verdict that routes to a safe baseline, a no-merge, or human escalation — never silent
acceptance. Guessing on an unproven check is forbidden.

- *Grounding.* Recovery blocks — on acceptance-test failure, roll back to an alternate implementation (Randell,
  1975, *System Structure for Software Fault Tolerance*). The Simplex architecture — wrap an unverified,
  high-performance controller in a *verified* safe baseline plus a decision module that falls back when the
  unverified path cannot be trusted (Seto, Krogh, Sha & Chutinan, 1998, *The Simplex Architecture for Safe
  Online Control System Upgrades*).
- *Status.* Partly in place. ADR-0009's fail-closed-on-gate-ERROR is exactly this for the ERROR case; ADR-0008's
  floor is the safe-baseline fallback. The forward step is making *every* unproven state (timeout / unknown /
  flaky / skipped) a first-class non-pass verdict, with the floor as the baseline it falls to.

### D5 — Diversity must be orthogonal by mechanism; a vote may rank, never accept

Independent agents that share a model, a prompt family, or a training distribution **fail in a correlated way**;
a majority vote over them launders a shared blind spot rather than catching it. The load-bearing diversity is
therefore the **orthogonal** deterministic gate (a mechanism unlike the generator), paired with an unanchored
adversary — not a vote of look-alikes. Disagreement among candidates is a *signal to build a new
property/test*, not a ballot.

- *Grounding.* N-version programming assumed independent implementations fail independently (Avizienis, 1985,
  *The N-Version Approach to Fault-Tolerant Software*); the assumption was disproved experimentally — isolated
  teams make the same mistakes at the same edge cases (Knight & Leveson, 1986, *An Experimental Evaluation of the
  Assumption of Independence in Multi-Version Programming*; Brilliant, Knight & Leveson, 1990, *Analysis of
  Faults in an N-Version Software Experiment*). "N prompts of one model" is the same fallacy in modern dress.
- *Status.* In place. This is the general principle behind ADR-0010 (the producer self-review — a second pass of
  the *same* reviewer over the *same* diff — was measured and rejected for exactly this correlation), and behind
  keeping `linon` an unanchored adversary while the deterministic gates carry the orthogonal check.

### D6 — The all-dumb benchmark ablates embedded knowledge; the primary metric is false-accept rate

The benchmark for "how much capability is structural" varies **two** independent variables, not one:

1. **proposal-agent IQ** (the carrier strength of the generative roles), and
2. **embedded knowledge** — the strength of the spec / DSL / solver / oracle the structure is handed.

Run a fixed task corpus arranged as a **ladder of oracle fidelity**: a full formal specification → properties
only → input/output examples → ambiguous natural-language intent → a deliberately incomplete/contradictory
specification. Ablate the structure in order: a single dumb generator → + decomposition → + independent
multi-version → + deterministic gate → + CEGIS repair → + counterexample memory → + diversified verifier → +
proof-carrying / small kernel.

The **primary metric is false-accept rate** — a *wrong* artifact that *passed* — not solve rate. Secondary
metrics: verified-solve-rate per unit compute, the **unknown rate** (published, not hidden), repair count,
counterexample novelty, regression stability, inter-version failure correlation, artifact reproducibility, and —
crucially — the **amount of human-supplied spec / grammar / invariant**, because that is the relocated
intelligence, and measuring it is how we tell mechanism from a pre-paid oracle.

- *Grounding.* The corrective is the synthesis of the whole record: capability ≈ representation-bias × oracle-
  fidelity × search/control × evidence-memory; reliability ≈ verifier-strength ÷ trusted-base-size. A weak
  generator suffices only when the candidate space is narrow or compositional, the oracle is *semantically
  stronger* than the generator (not a reviewer that shares its blind spots), the trusted base is small, the
  counterexamples monotonically shrink the space, the environment is reproducible, and *unproven is not pass*.
- *Status.* Forward. This is the design of the engine-eval harness (an open item since ADR-0009's Tier-2);
  all-dumb runs are cheap, so the ladder can be run often.

## Grounding — the pre-LLM lineage (sources, by what they ground)

These citations are the traceable backbone; each names what in this architecture it earns and, where relevant,
the failure that shaped the choice. (Recorded under the same *grounding, not illustration* discipline as
ADR-0009.)

**The trusted-kernel / per-artifact-verification spine (D1, D2):**
- Milner, 1972 — *Logic for Computable Functions: Description of a Machine Implementation* (LCF: untrusted
  tactics, tiny trusted kernel).
- Necula, 1997 — *Proof-Carrying Code* (the artifact carries a proof a small checker validates).
- Pnueli, Siegel & Singerman, 1998 — *Translation Validation* (verify each output, not the generator).

**Counterexample-driven loops (D3):**
- Solar-Lezama, Tancau, Bodík, Seshia & Saraswat, 2006 — *Combinatorial Sketching for Finite Programs* (SKETCH;
  the counterexample-guided inductive-synthesis lineage — a weak/randomised generator converges given a complete
  verifier). The "CEGIS" *name* has contested first attribution; *noted as disputed*.
- Clarke, Grumberg, Jha, Lu & Veith, 2000 — *Counterexample-Guided Abstraction Refinement* (classify real vs
  spurious; refine the abstraction, not the implementation).
- Manna & Waldinger, 1980 — *A Deductive Approach to Program Synthesis*; Gulwani, 2011 — *Automating String
  Processing in Spreadsheets Using Input-Output Examples* (PBE; examples under-determine intent); Alur et al.,
  2013 — *Syntax-Guided Synthesis*; Massalin, 1987 — *Superoptimizer*; Schkufza, Sharma & Aiken, 2013 —
  *Stochastic Superoptimization* (STOKE: random search + an exact verifier still converges).

**The oracle-completeness / overfitting corollary (D1, D6):**
- Weimer, Nguyen, Le Goues & Forrest, 2009 — *Automatically Finding Patches Using Genetic Programming* (GenProg;
  a weak test suite is gamed — patches that pass the tests by breaking behaviour). Nguyen, Qi, Roychoudhury &
  Chandra, 2013 — *SemFix*; Mechtaev, Yi & Roychoudhury, 2016 — *Angelix* (extract a *repair constraint* from
  symbolic analysis — feed a constraint, not an error string).

**Diversity and its failure mode (D5):**
- Avizienis, 1985 — *The N-Version Approach to Fault-Tolerant Software*; Knight & Leveson, 1986 — *An
  Experimental Evaluation of the Assumption of Independence in Multi-Version Programming*; Brilliant, Knight &
  Leveson, 1990 — *Analysis of Faults in an N-Version Software Experiment*. (Generalisability of the experiment
  has been debated — *noted as disputed* — but the safe reading is: do not assume independent agents fail
  independently.)
- Harman & Jones, 2001 — *Search-Based Software Engineering*; Koza, 1992 — *Genetic Programming* (population
  search; but code's fitness landscape is discontinuous, and population ≠ reliability).

**Unproven-is-not-pass / safe fallback (D4):**
- Randell, 1975 — *System Structure for Software Fault Tolerance* (recovery blocks); Seto, Krogh, Sha &
  Chutinan, 1998 — *The Simplex Architecture for Safe Online Control System Upgrades*.

**The verifiers themselves (the certifier ladder):**
- Hoare, 1969 — *An Axiomatic Basis for Computer Programming*; Meyer, 1992 — *Applying "Design by Contract"*;
  Freeman & Pfenning, 1991 — *Refinement Types for ML*; Clarke & Emerson, 1981 (model checking); Holzmann, 1997
  — *The Model Checker SPIN*; de Moura & Bjørner, 2008 — *Z3* — full formal verification is the ideal we
  deliberately do **not** reach for (annotation burden / state explosion never scaled), in favour of a cheaper
  *executable* contract (ADR-0009).
- Claessen & Hughes, 2000 — *QuickCheck* (property-based testing); Godefroid, Klarlund & Sen, 2005 — *DART*;
  Cadar, Dunbar & Engler, 2008 — *KLEE*; Godefroid, Levin & Molnar, 2008 — *Automated Whitebox Fuzz Testing*
  (SAGE); Zeller & Hildebrandt, 2002 — *Simplifying and Isolating Failure-Inducing Input* (delta debugging,
  i.e. counterexample minimisation); Chen, Cheung & Yiu, 1998 — *Metamorphic Testing* (a relation-based oracle
  where no exact oracle exists); Cousot & Cousot, 1977 — *Abstract Interpretation* (sound finite approximation;
  the soundness/precision trade-off behind the false-positive discipline).
- Johnson, 1977 — *Lint*; Bessey et al., 2010 — *A Few Billion Lines of Code Later* (Coverity); Sadowski et al.,
  2015 — *Tricorder*; Sadowski et al., 2018 — *Lessons from Building Static Analysis Tools at Google* (the
  *effective false positive* discipline: a finding the developer will not act on is a false positive even if
  technically correct — the basis for shadow-first, ~0-FP-to-block).

**Decomposition, scheduling, and knowledge (the rest of the structure):**
- Wirth, 1971 — *Program Development by Stepwise Refinement*; Parnas, 1972 — *On the Criteria To Be Used in
  Decomposing Systems into Modules* (information hiding); Feldman, 1979 — *Make* (a dependency DAG re-runs only
  what changed) — decomposition needs *two* layers: Parnas-style semantic module boundaries and a Make-style
  execution DAG.
- Erman, Hayes-Roth, Lesser & Reddy, 1980 — *The Hearsay-II Speech-Understanding System*; B. Hayes-Roth, 1985 —
  *A Blackboard Architecture for Control* (independent knowledge sources over a shared blackboard; but the
  scheduler becomes a hidden central intelligence, and a shared blackboard *synchronises blind spots* unless
  proposals are made in sealed branches and published only after commit).
- Rich & Waters, 1988 — *The Programmer's Apprentice*; Smith, 1990 — *KIDS* (operate on plan/idiom units, not
  tokens; separate correctness-preserving from efficiency transforms) — limited by the knowledge-acquisition
  bottleneck.
- Neighbors, 1980 — Draco; Hudak, 1996 — *Building Domain-Specific Embedded Languages*; OMG, 2003 — *MDA Guide*;
  Czarnecki & Eisenecker, 2000 — *Generative Programming* (normalise to a typed intermediate / DSL before
  generating; limited by round-trip drift — hence the one-way rule).

## The non-decisions

The techniques this architecture deliberately rejects, each with the recorded reason, are listed in the README
("What this deliberately does not do (and why)"): full formal verification as the gate; evolutionary AST search;
majority voting as acceptance; a weak test suite as the implementer's oracle; a hand-curated idiom knowledge
base; round-trip edits on generated artifacts; symbolic execution as the primary oracle; an unscheduled
blackboard.

## Consequences

- **Changes (forward):** the repair loop gains a counterexample classifier (D3); accepted artifacts gain a
  content-addressed evidence bundle (D2); every unproven verdict routes to the floor/no-merge/escalation, not a
  pass (D4); the engine-eval harness is designed as a two-variable ablation with false-accept rate primary (D6).
- **Stays:** the executable contract and shadow-first gates (ADR-0009); the unanchored adversary plus orthogonal
  deterministic gates and the rejection of self-review (ADR-0010); the floor as safe baseline (ADR-0008).
- **Honest status:** D1 and D5 are in place; D2, D3, D4, D6 are forward decisions, gated on telemetry / the
  engine-eval harness exactly like ADR-0009's investment sequence. This is a deterministic verification spine
  *closing on* a reliability architecture, not a finished one.

## The corrected thesis, in one line

Search comes from many weak proposers; reliability comes from a structured specification, an independent
verifier semantically stronger than the generator, a small trusted kernel, and persisted counterexamples — and
the mechanism cannot generate the ambiguous human intent itself, only enforce a contract once that intent has
been made checkable.
