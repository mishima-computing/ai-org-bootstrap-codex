# ADR-0012: The synthesizer is a proposer, not an authority — preserve N contracts; collapse only by proof

## Status

Accepted. Refines the dialectic (`aggressive` / `conservative` / `genius` → `aufheben`) under the trust model
of ADR-0011 (untrusted generators over a small trusted kernel), the rejection of self-review in ADR-0010
(correlated failure), the executable contract + pre-flight of ADR-0009, and the floor of ADR-0008. Grounded in
a two-source archaeology of the pre-LLM record (synthesised below); where the two sources differed, the more
formal one is taken and the difference is noted.

This ADR does **not** remove `aufheben`. It removes the assumption that `aufheben` performs a *trustworthy
merge*, and replaces "synthesise into one contract" with "preserve the inputs; collapse only what can be proven
compatible; keep the rest."

## Context

The design wave produces three intentionally divergent designs and then `aufheben` combines them into one
implementation contract — deliberately **not** by majority vote (voting launders correlated blind spots:
Knight & Leveson, 1986; ADR-0010/ADR-0011 D5). *Aufheben* (cancel-and-preserve) is richer than a vote or
best-of-N selection.

But the historical record is decisive and it lands exactly here: **a general "fuse competing semantics/specs
into one form without information loss" operation does not exist.** Every attempt hit a mathematical wall.

- **Fair fusion is impossible.** A function that aggregates a set of *logically connected propositions* (which
  is what design claims are) while staying neutral, anonymous, systematic, and collectively consistent does not
  exist in general — the discursive-dilemma / judgment-aggregation result (List & Pettit, 2002, *Aggregating
  Sets of Judgments: An Impossibility Result*), the propositional analogue of Arrow (1950/1951) that is the
  *directly* applicable theorem here, because contracts are proposition-sets with dependencies, not preference
  orderings.
- **The only verifiable "merge" is a logical conjunction of jointly-satisfiable hard guarantees** (Abadi &
  Lamport, 1993, *Composing Specifications*; assume/guarantee contract algebra: Bauer et al., 2012). Anything
  beyond that — blending conflicting claims "sensibly" in prose — is not sublation; it is a *policy decision*
  that silently selects, weakens, or conditionalises a constraint, dressed as synthesis.
- The survivors of the field all share one move: **they do not collapse unresolved conflict.** ViewPoints keeps
  inconsistent views as a resource (Easterbrook & Nuseibeh, 1996); ATMS keeps multiple assumption environments
  and labels contradictions as nogoods (de Kleer, 1986); argumentation keeps attack graphs and yields several
  extensions (Dung, 1995); Pareto methods keep the non-dominated set (Deb et al., 2002, NSGA-II); feature-
  oriented SPLs keep differences as features (Batory et al., 2011); semantic 3-way merge collapses *only when*
  non-interference / behavioural-delta preservation is provable (Horwitz, Prins & Reps, 1989; Sousa, Dillig &
  Lahiri, 2018, *Verified Three-Way Program Merge*).

`aufheben` sits in the **specification layer** — it produces the contract — which is the locus where
intelligence relocates and the one layer that cannot be made dumb (ADR-0011 D6). So it is simultaneously the
riskiest single funnel and a role the all-dumb benchmark is predicted to flag as critical.

## Decision

### D0 — Normalise each design before any merge

`aufheben` does not receive three prose designs; it receives three **normalised** ones,
`C_i = (A_i, H_i, O_i, D_i)`:

- `A_i` — assumptions / environment preconditions;
- `H_i` — independently-verified **hard guarantees** (e.g. `p99 latency ≤ 20 ms`);
- `O_i` — **soft objectives** (performance, cost, complexity);
- `D_i` — **implementation choices** (algorithm, data structure, library — e.g. "lock-free").

The load-bearing separation is `H` vs `D`: a hard guarantee is conjoined and preserved; an implementation
choice is a *candidate means* and must **not** enter the conjunction with other designs' guarantees.

### D1 — `aufheben` is a proposer, not an authority

`aufheben` may *propose* a merge candidate `M`; it does **not** decide that the merge is valid. Validity is
decided by the gates over the artifact (ADR-0011 D1: trust the small certifier, not the generator). "The
aufheben said so" never stands in for a check.

### D2 — The canonical internal artifact is a **contract family**, not a single contract

```
core.contract              # claims provable in every admissible resolution (the skeptical core)
sources/{aggressive,conservative,genius}.contract   # immutable, never discarded (ViewPoints)
variants/*.contract        # claims that hold only under some resolution / environment
conflicts.json             # minimal conflict cores (nogoods) + the resolution policy applied
correspondences.json       # the approved cross-source claim alignment (match ≠ merge)
selector.contract          # only when variants exist; a NEW trusted component
preservation-certificate/  # the diversity-preservation evidence (D4)
distinguishing-tests/      # inputs/traces where two designs observably differ
rationale/                 # per-clause Question / Options / Criteria / disposition (QOC)
```

This is the common shape of SPL, ATMS, argumentation, and mixture-of-experts. A single implementation contract
is a *derived* artifact, emitted only when D5 permits; the diversity is never destroyed internally.

### D3 — The merge-candidate gate ladder (order matters)

A proposed `M` passes, in order:

- **G0 Semantic alignment** — same-named state/event/unit across designs has an explicit, verified
  correspondence; unaligned ⇒ do not merge (match is harder than merge — Pottinger & Bernstein, 2003).
- **G1 Realizability FIRST** — `SAT(M ∧ μ)` for the global must-constraints `μ`. *This is checked before any
  entailment*, because an inconsistent contract entails every proposition, so an entailment-first check would
  manufacture a false proof that "all claims were preserved."
- **G2 Hard-property preservation** — for each kept hard claim `h`, prove `M ⊨ (A_h → h)` (assume/guarantee /
  refinement). The metric is `min_i HardRetention(C_i)`, never an average.
- **G3 Clause-disposition completeness** — every input claim is labelled `entailed | contradicted | independent
  | inapplicable | rejected`. One unlabelled claim ⇒ fail (silent drop is a failure).
- **G4 No silent invention** — any claim new to `M` carries a derivation, an explicit interaction rule, or an
  external provenance. "Seemed good" claims do not enter the hard contract.
- **G5 Behavioural-delta preservation** — where designs derive from a base, the merge preserves each design's
  intended observable delta (the N-way generalisation of verified three-way merge).
- **G6 Unresolved-conflict preservation** — an unresolvable conflict is kept (variation point / multiple
  maximal-consistent sets / conditional contract / Pareto frontier / explicit decision obligation), never
  flattened into a prose compromise.

### D4 — Diversity-preservation certificate (the oracle for "did it silence a view")

A single quality score is insufficient — an average stays high while one source is fully silenced. The merge
ships a certificate: the clause-disposition matrix (D3); per-source hard-retention via `UNSAT(M ∧ A_p ∧ ¬p)`;
the realizability witness (D3/G1); conflict-core coverage for every rejected hard claim; pairwise
distinguishing-witness coverage (for each design pair, `M` must *require / conditionally allow / forbid /
declare-out-of-contract* each observable difference); the skeptical-vs-contested split; and a round-trip /
lens projection back to each source view (Foster et al., 2007) as a silent-loss check. (Leave-one-view-out
influence is a *diagnostic*, not a gate — a fully redundant source also shows zero influence.)

### D5 — When a single contract may be collapsed (cost is asymmetric)

Collapse is the *cheap common case when designs are compatible*, and the expensive machinery (variants,
selector, full certificate) is spun up *only at a proven conflict*:

| State | Disposition |
|---|---|
| all validated hard constraints jointly satisfiable **and** preservation proven | collapse to one contract |
| hard constraints compatible, soft trade-off unresolved | keep the Pareto set; an explicit policy collapses it |
| conflict separable by environment | conditional / variational contract; **verify the selector** |
| hard conflict **with** an explicit source priority | collapse permitted — but it is *policy applied*, not "correct synthesis" |
| hard conflict with no priority/selector | **collapse forbidden** — keep the family |
| solver unknown / unsupported semantics / ambiguous spec | keep N contracts (unproven ≠ pass — ADR-0011 D4) |
| a strong external oracle exists | `aufheben` may *propose* candidates; the oracle selects |
| only a vote / agent consensus exists | **never** used for acceptance |

### D6 — Disagreement becomes a property, never a vote (and never silently)

Run the three designs on the same *defined* input/environment; capture observable deltas (excluding undefined
behaviour, nondeterminism, clock/network, unpinned deps — the Csmith discipline: Yang et al., 2011); minimise
each witness (delta debugging: Zeller & Hildebrandt, 2002); classify against the existing contract; mine a
candidate property / metamorphic relation (McKeeman, 1998, *Differential Testing*; Chen, Cheung & Yiu, 1998,
*Metamorphic Testing*; Ammons et al., 2002; Ernst et al., 2001, Daikon — these yield *candidates*, not proofs);
then **independently verify** the candidate before promoting it to a hard clause / regression. Limits, recorded
so they are not forgotten: disagreement is not a bug-proof, agreement is not a correctness-proof, a bug shared
by all three is invisible, a mined invariant can ossify an existing bug, and finite differential testing is not
complete. Diversity is an oracle/counterexample *generator*, never a decider.

### D7 — Acceptance rule for emitting a single implementation contract

Emit one contract only if all hold: (1) source correspondences approved (G0); (2) `M` realizable (G1); (3)
every validated hard property preserved under its assumptions (G2); (4) every claim dispositioned (G3); (5)
every rejected hard claim has a conflict core + policy (G6/D4); (6) every new claim has provenance + independent
verification (G4); (7) every known behavioural disagreement is dispositioned as test/property/variation (D6);
(8) an independent verifier re-checked the proofs; (9) zero timeouts / unknowns / unsupported-semantics. If any
fails, the output stays a *contract family*: skeptical core + unresolved variation points + distinguishing
counterexamples + explicit decision obligations.

### D8 — The LLM is the strongest aufheben *generator* the archaeology lacked — which is *why* D1, not despite it

The archaeology above has a blind spot, and naming it sharpens this ADR rather than weakening it: **every cited
field concluded "verifiable synthesis is impossible / only conjunction is safe" from inside a world that had no
strong synthesis *generator*.** Schema merge needed the correspondences supplied by hand; belief merge needed a
distance function chosen by hand; argumentation needed the attack relation built by hand; semantic merge fell
back to manual resolution (Horwitz et al., 1989, is NP-hard in general). The field-wide pain was that
*generation* of a synthesis was the manual / NP-hard / semi-automatic bottleneck. An LLM **dissolves that
generation bottleneck** — and it does not merely propose conjunctions. It can propose a genuine *Aufhebung*: a
fourth design that reframes the conflict at a higher level and dissolves it. (Lock-free vs mutex vs actor is
UNSAT as an implementation conjunction; an LLM can propose a *key-sharded mutex* that meets the aggressive p99,
the conservative reasoning-simplicity, and the genius isolation at once — a reframing none of the three stated,
and one the formal operators of §Grounding *cannot* emit.)

This does not violate the impossibility results of D-context. Those killed the existence of a *fair, neutral,
closed-form* operator. The LLM is not that — it is an **untrusted heuristic proposer**, so it sidesteps the
theorems by making *generate-and-check* viable for design contracts for the first time (the CEGIS move:
Solar-Lezama, 2008 — propose, verify, learn from the counterexample). The consequence is the central thesis of
ADR-0011 made concrete here: **generation cost collapses, so the binding constraint relocates entirely to
verification.** D1 (proposer ≠ authority) is not a hedge against a weak synthesizer — it is the *necessary*
discipline for the strongest one. A weak generator's chimera can be eyeballed and discarded; a strong
generator's chimera reads as correct while silently dropping a perspective (D3) or inventing a clause (D4). The
fluency that makes the LLM the best aufheben generator ever is exactly what makes its bad syntheses most
dangerous — so the gate ladder is needed *more*, not less, as the generator improves.

Therefore the emphasis of D2–D7 is corrected: the gate ladder does **not** exist to *demote* synthesis to
conjunction. It exists to **safely accept a sublation *richer* than conjunction** — a verified conflict-
dissolving reframing — by checking it preserved every constraint. "Collapse only by proof" (D5) is upgraded: the
*proof* may now be "the LLM found a higher unity **and** it passed G0–G6," which yields strictly more than the
pre-LLM ceiling of conjunction. The contract family (D2) is the **fallback when a proposed sublation fails to
verify**, not the expected output. Pre-LLM, "collapse by proof" could only ever yield conjunction, because that
was all the generators could produce; with an LLM generator it can yield a verified *Aufhebung*.

## Relationship to the existing pipeline

ADR-0009's contract pre-flight already gates the `aufheben` output; this ADR turns that single gate into the
G0–G6 ladder and adds the certificate. ADR-0009's finding-to-regression is exactly D6's "verified property →
regression." ADR-0008's floor is the safe baseline for D5's "keep N / unproven ≠ pass." The `aufheben` role
already records `rejected_parts` and `conflict_points` — D2/D3 make those a *checked, preserved* contract family
instead of prose that is then collapsed.

## Grounding (sources, by what they ground)

- **Impossibility of fair fusion:** List & Pettit, 2002 (judgment aggregation — the directly applicable result);
  Arrow, 1950 (preference aggregation — the classic but less directly applicable analogue); Konieczny & Pino
  Pérez, 1999 (merging with integrity constraints; the IC postulates and the arbitrariness of the distance/
  weight choice); Konieczny, Lang & Marquis, 2004 (merging is not strategy-proof in general).
- **The only verifiable merge = conjunction of jointly-realizable guarantees:** Abadi & Lamport, 1993; Back &
  von Wright, 1998 (refinement calculus); Bauer et al., 2012 (contracts in component design).
- **Keep N, do not collapse unresolved conflict:** Easterbrook & Nuseibeh, 1996 (ViewPoints); de Kleer, 1986
  (ATMS); Dung, 1995 (argumentation extensions); Deb et al., 2002 (NSGA-II / Pareto); Batory, Höfner & Kim,
  2011 and Apel et al., 2013 (feature interactions); Horwitz, Prins & Reps, 1989 and Sousa, Dillig & Lahiri,
  2018 (semantic / verified three-way merge — collapse only on provable non-interference).
- **Match before merge / preservation requirements:** Spaccapietra & Parent, 1994; Pottinger & Bernstein, 2003;
  Noy & Musen, 2000 (PROMPT — semi-automatic, source priority is explicit authority, not neutral synthesis).
- **Silent-loss oracle:** Foster et al., 2007 (bidirectional lenses, round-trip laws).
- **Rationale capture:** Conklin & Begeman, 1988 (gIBIS); MacLean et al., 1991 (QOC) — provenance, not a
  correctness proof.
- **Disagreement → property:** McKeeman, 1998 (differential testing); Yang et al., 2011 (Csmith — only compare
  defined programs); Chen, Cheung & Yiu, 1998 (metamorphic); Ammons, Bodík & Larus, 2002 and Ernst et al., 2001
  (mined specs/invariants are candidates); Zeller & Hildebrandt, 2002 (delta debugging).
- **Ensemble caveat:** Jacobs et al., 1991 (MoE — routing, not merge); Wolpert, 1992 (stacking — no general
  improvement guarantee; no meaningful "average" of discrete contracts).

## Consequences

- `aufheben` is re-scoped from *prose synthesiser* to *proposer of a normalised merge candidate* over a
  contract family; the design conflict survives as variation points + properties, not as a paragraph.
- Forward work (telemetry-/engine-gated like ADR-0009's investments): the contract-family schema (D2); the
  G0–G6 ladder with realizability-first (D3); the preservation certificate (D4); the collapse-permission policy
  + selector verification (D5); the disagreement→verified-property pipeline (D6). The **cost is asymmetric** —
  compatible designs conjoin into one contract cheaply; the family/selector/certificate machinery is paid only
  at a *proven* conflict, so the common case is not gold-plated.
- In place already, generalised here: pre-flight as the seed of the ladder (ADR-0009); finding-to-regression as
  D6's promotion; the floor as D5's safe baseline; "unproven ≠ pass" (ADR-0011 D4).

## Final verdict

Sublation is not a trustworthy general operation. What is trustworthy is **partial** unification — over inputs
mapped to a common formal semantics, collapsing only the part whose compatibility is *proven*, and preserving
the rest — together with its certificate. The default is **preserve N, collapse by proof**; a single contract is
a derived artifact, never the canonical one.
