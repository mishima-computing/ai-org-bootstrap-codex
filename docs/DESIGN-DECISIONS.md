# Design Decisions — AI Org Codex (clean-slate-rebuild)

This document records what AI Org Codex ADOPTED and what it REJECTED while building the
current engine, distilled from the commit messages of the full 544-commit history of the
`clean-slate-rebuild` branch (scanned oldest→newest in five chunks, consolidated 2026-07-10).
Reconciliation rule: when a decision was later reversed, the LATEST state wins — the entry
appears once under its final status with a `History:` line citing both commits.
Watershed: commit **49a9921** archived the ENTIRE pre-reset engine (all executor generations
plus their ADRs/design docs) to `archive/`; the current engine is the Git-community-model
rebuild (receive → review → patchwork_queue → patch authoring → maintainer merge).
Entries about the archived engine are collected in "Pre-reset engine: lessons retained" —
their mechanisms are gone, but their measured lessons still bind and must not be re-litigated.
Every entry keeps at least one short-sha citation; nothing here goes beyond what the commits say.

---

## 1. Engine architecture & the clean-slate reset

### Adopted

- **Clean-slate archive + Git-community-model rebuild** (49a9921, 0bfa7b7): entire prior engine moved to `archive/` (git mv, nothing deleted); rebuilt as RFC→review→decompose→contribution→maintainers modeled on real Linux kernel development. Why: the prior model was judged contaminated/broken; build the next executor uncontaminated.
- **Pull model, no central orchestrator** (958fa2a, f9fe246): each role (rfc/patch/merge) pulls its own work from git state via git_wrapper; an architecture test asserts no module imports all three phases.
- **3-layer structure: domain/platform/composition-root, acyclic enforced** (f9fc247): rfc/patch/merge are absolute boundaries; platform depends on no domain; a test enforces the acyclic import graph. Stage naming unified to rfc/patch/merge (b3e9a4d).
- **Flat flow, no Task Graph assumed** (b6e797f): one contributor-sized task → one PR; depends_on/graph structure is a hook added only when a real dependency or oversized task appears. Why: don't assume structure the RFC's actual split doesn't require.
- **Kernel vocabulary: patchwork_queue / patch_author / maintainer_merge** (d7eabb2): repo-wide mechanical rename (git-mv + exact-token substitution, not an LLM wave). Why: names are prompts — kernel vocabulary measurably made a blank model recognize LKML/Patchwork workflow unprompted. History: RFC/lineage → work_order_network (ab3811e) → patchwork_queue (d7eabb2).
- **git-read → codex(workspace-write worktree) → git-write** (fdcf2b6): the canonical contribution shape — python reads converged state, codex edits an isolated worktree (fail-closed on nonzero), python commits since codex cannot write .git. Why: proven, git-native, no ledger.

### Rejected

- **The entire pre-reset engine** (49a9921): controller/splitter/frontier autonomous-builder generation (2058229), in-process recursive TaskExecutor (61b0d6f, 22ab830), distributed branch-integration retrofit (3de509e), plus their ADRs/design docs. Rejected because: judged a contaminated/broken model overall. Replaced by: the clean-slate Git-community-model rebuild (archived, not deleted). History: the TaskExecutor's shared locks caused a real runtime deadlock, located by a purpose-built static deadlock gate (dba991e) → replaced by Git-as-parallelism-boundary (3de509e) → whole line archived.
- **Rebuild-era carrier/git/state super-modules** (1e80d5c): carrier.py hang-safe wrapper, platform/git.py, state JSON/CAS ledger. Rejected because: "garbage distributed evenly" — thread_id handling broken, hang-safety unverified, state duplicated git. Replaced by: each stage calls codex via a plain subprocess; git wrapping done in unsandboxed python around the call. History: reverses the pre-reset deterministic carrier harness line (72da1c4).
- **Central orchestrator (ai_org.driver)** (f9fe246): sole all-phase importer. Replaced by: the pull model, enforced by architecture test.
- **tracking.py as a whole-pipeline stage machine** (61ce18a): stage_of/next_action re-centralization. Replaced by: a thin read-only git-primitives wrapper; roles compose their own preconditions.
- **work_order_network naming (first rename pass)** (ab3811e→d7eabb2): diffuse GitHub/Jira/Gerrit/Airflow associations, no kernel recognition per blank-context probe. Replaced by: patchwork_queue kernel vocabulary.
- **Product-vision ADRs (0007–0015) in the AI Org repo** (4516c36): rolled back out of scope. Replaced by: product ADRs re-homed to the Shagiri repo, one-way dependency (the worker doesn't know the product).
- **Competing head-on with a vertically-integrated runtime/cockpit** (cb46dff): conceded once a leading tool shipped nearly the same design. Replaced by: be the OPEN, runtime-agnostic org-layer on commodity runtimes.

---

## 2. Durable principles (carried across the reset)

### Adopted

- **Deterministic code executes, the LLM judges** (1f12784 ADR-0004; reasserted fdcf2b6, a33215a): orchestration/structure (ids, slots, repair deltas, gates) is mechanical Python; the model owns only named semantic judgment. Why: zero LLM tokens on plumbing; "code owns structure, model owns judgment."
- **Settledness, not dumbing** (786435e ADR-0005): gain speed by shrinking the decision surface, never the decider; agents stay at full reasoning.
- **Don't narrow the unknown-handler** (dbac88a ADR-0006; echoed c22c07b): spend review-speedup effort only on field-preserving levers; the adversarial reviewer's breadth is the point. Sharding later reapplied this "with zero softening of the reviewer's sharpness."
- **Executable contract: judgment chooses the contract, deterministic machinery checks it** (de5001f ADR-0009; reasserted f4f5ad5, 77a25b0, 04f9d8b): the verification boundary is "B produces an executable A"; prompt tuning is the last-resort investment.
- **Untrusted generators over a small trusted kernel** (0e1ec00 ADR-0011, 479f143, b56d72a): verify-per-artifact, unproven is not pass, diversity must be mechanism-orthogonal; intelligence relocates into contract/oracle, it doesn't vanish.
- **Log is the state source** (272c64f, 834077f; re-founded e76cfcb): every agent's speech and every stage event flows to the shared stream; state/views are rebuildable projections of the log.
- **Structural fixes over prompt prohibitions** (f847cc3, e9e2134, f4f5ad5, 7449cf2): hide files structurally, render untracked files as real diffs, enforce grounding with executable lints — a conditional sentence in a prompt "is not a contract" (条件文は契約ではない).
- **Fail-closed everywhere, three-valued results** (3e47c35, a90cdf8, 81c56b6, 423b17a): a crashed checker is a critical finding, not "clean"; PASS/FAIL/ERROR, never two-valued; typed fail-closed results at every boundary.
- **Memento: carve proven constraints into the code itself** (b172810, 64eadd9): proven codex/schema/sandbox constraints recorded as comments next to the mechanism; module docstring headers deterministically projected into formation context, because carriers skim code and skip prose.
- **Autonomy is the differentiator** (baa7644): the org self-drives to completion; human confirm-back is dormant, questions/assumptions recorded non-blocking. History: the pre-reset engine had blocked_hitl ask-and-wait (7ceac15), archived with it.

### Rejected

- **Structural-vs-contextual defect boundary** (de5001f): the axes were wrong (a SAST rule is deterministic-yet-noisy; a contextual requirement is deterministic once contracted). Replaced by: ADR-0009's executable-contract framing.
- **"Mechanism over minds" / "dumb agents" as literal framing** (b56d72a): imprecise — weakening the spec or certifier still yields an efficiently-wrong artifact. Replaced by: ADR-0011's trusted-kernel framing.
- **Pre-LLM automation techniques imported wholesale** (e0faaa2): full formal verification, GP/AST search, majority-vote N-version, self-owned weak test oracles, hand-curated idiom KBs, round-trip generation, symbolic execution as primary oracle — each rejected with a named replacement (executable contract, directed dialectic, orthogonal gates, withheld oracle, per-goal contracts, one-way + regression corpus, black-box conformance).
- **Cone-scope (narrowed) reviewer** (dbac88a): unenforceable, against-grain, evidence-lossy. Replaced by: full-field review, field-preserving speedups only.

---

## 3. Intake & grounding (receive)

### Adopted

- **receive.py is the intake GATE, not a loader** (633827e, 69d6c60): grounding belongs to intake; review only debates direction of an already-formed RFC. Intake returns promoted / needs_clarification / grounded rfc.json.
- **Real entrance: submit CLI + off-git inbox + pull-intake** (76f8e50): raw requests never create git branches; `.ai-org/inbox/<id>.json` holds them until rfc.pull runs full intake. "Git stores ONLY the result."
- **Self-grounding research before review** (0f02290; also 5beceb7): a web-enabled read-only codex step researches subject/genre/prior-art and corrects the request before the review loop; fail-closed to the original view.
- **Best-guess proposal, not blank questionnaire** (2f1cb70): under-specified requests get a proposed_rfc + assumptions to confirm/correct + narrow questions.
- **Ground down, full scope, no legal dept** (a65a937): ground to concrete defining signatures (never up to genre), commit to FULL scope (never MVP slice), zero IP/trademark analysis.
- **Executable-contract grounding gate** (f4f5ad5): deterministic lints + one codex semantic verifier, bounded reground loop, fail-closed. History: hardcoded RETRO_MARKERS deleted, C4 moved to the semantic verifier (d4069ed); C1/C2 marker-substring lints replaced by a fail-closed semantic verifier with atomized defect fingerprints (64fb124); lints scoped to registry-declared target fields (4c81515); granular rejection reasons replace bare booleans (4027ba7).
- **RFC formation = promote request to contributor-takeable (5 過程 inside one stage)** (24d2c1e, b63e100): grounded in kernel.org early-stage process, run inside one codex-driven formation stage so git state doesn't explode.
- **Research-derived field registry as single source of truth** (69315e0d): role/belongs/must_not/owner/required_at per field, grounded in IETF/PEP/Rust-RFC/ADR/PRD practice; entrance vs handoff validation tiers. History: replaces the ungrounded common-8 field contract (b6fa766).
- **Technical Approach: 10-step formation → derivation tree** (b069b39, f09de5f, 7996a09e, ff18767): normalize → constraints → prior-art → 2-3 candidates → trade-offs → select → strategy → right-size (YAGNI) → risks → emit; re-architected as an IBIS/QOC/Toulmin-grounded typed tree; wired into the real entrypoint; build keys == read keys.
- **Propose ≠ review boundary** (e84192b): receive forms the technical approach (Reference-driven when the requester supplies none); review only critiques, never invents.
- **Research timing split** (65ddbb2): design research synchronous (blocks step ③); implementation research backgrounded and drained later by patch.
- **ORG_BUILDER_PROFILE as standing hard constraints** (f219209): org authoring/verification capabilities merged into constraint extraction so infeasible precedent stacks (e.g. "franchise uses UE, so UE") can't survive; requester-specified stack stays authoritative.
- **Stack candidates judged by structured requirements** (170afb1): authoring_model/verification_model fields, not text-mention scans.
- **Candidate pruning records, does not punish** (c18ff66): infeasible candidates removed AND recorded as rejected alternatives with concrete conflicts; retry only when the feasible set is too small.
- **Nested-template success criteria** (a4441d9): {actor, capability, verifiable_outcome, verification} nesting forces measurability; deterministic lint rejects empty slots.
- **user_experience_requirements as a research-grounded presentation contract** (6a52b0d): derived by the org itself, because a faithful implementer of mechanics-only specs produced invisible game state. Committed UX surfaces later became a facet source closing a GIGO bootstrap loop (9a9d5a8).
- **Kind-neutral approach schema, no silent truncation, git history as evidence** (6fa57bd): deliverable-neutral fields; all silent `[:2000]` slices removed; recent commit history injected into grounding/prior-art.
- **Gap-directed specification-completeness lookup** (ab69f04): research what a complete spec of this deliverable KIND must declare. History: the grown two-stage checklist/journey apparatus was ratifier-rejected as over-engineering (270cf42) and reduced to a minimal non-gating store-lookup in receive.
- **not_user_facing content accepted-and-cleared; open_questions restored non-blocking** (a6661d8): mode-irrelevant content normalized, not punished; a silent `open_questions = []` reset hidden in a re-architecture was deleted.

### Rejected

- **Old thin rfc_view schema and intake-time RFC dataclass** (fdb03ce, 7ebee88): the RFC is the formation's OUTPUT. Replaced by: a plain validated rfc.json dict (now field-registry-governed).
- **Prompt-only enforcement of grounding rules** (f4f5ad5): "the weakest lever, varies run-to-run." Replaced by: executable-contract lints + semantic verifier.
- **Marker-substring lints for faithfulness/scope, and polarity-aware regex variants** (64fb124): fired twice in 278 runs, both false positives; regex variant rejected as exception-accreting. Replaced by: fail-closed semantic verifier judgment.
- **Hardcoded RETRO_MARKERS** (d4069ed): test-subject contamination ('1986', 'famicom') baked into runtime logic; false-fired on its own namesake. Replaced by: C4 judged solely by the semantic verifier.
- **Mention-scan lint for stack conflicts** (170afb1): pruned Phaser for merely mentioning Unity/Unreal in comparison prose. Replaced by: structured stack-requirement fields.
- **One-shot stack decision by fidelity mimicry** (f219209): "make Dragon Quest → Unreal Engine," zero regard for org authoring/verification capability. Replaced by: provenance discipline + ORG_BUILDER_PROFILE constraints.
- **23 throwaway driver scripts as the submission channel** (76f8e50): replaced by the submit CLI + inbox + pull-intake.
- **DQ-era game-shaped approach schema** (6fa57bd): forced a lineage-machinery RFC into game vocabulary. Replaced by: deliverable-kind-neutral fields.
- **#18 completeness two-stage checklist/journey apparatus** (270cf42): 8 defects, 0 live successes, blocked its one deliverable. Replaced by: minimal store-lookup, no gating, misses are no-ops.

---

## 4. Review & adjudication

### Adopted

- **LKML-grounded direction review: typed objections, serial-per-axis verdicts** (77183e9): review is the group-internal consensus milestone on need/approach/compat/scope/maintenance axes; Aufheben demoted to consolidation only (never authors). History: fixed "5 reviewers + Aufheben" checklist slots (0bfa7b7, 07ffa1c) rejected as invented structure (e84192b) — axes + judgment synthesis replaced them; earlier still, ADR-0012 rejected trusting Aufheben's fusion as a merge (80432a4): no fair lossless merge operator exists, so Aufheben proposes/consolidates but never decides.
- **Review = the formation/maturation engine** (c9f39da): review IS what turns a request into a contributor-takeable RFC, not a separate direction-review phase.
- **Judgment vs fixing kept separate** (89f932e): only the Contributor fixes code; everyone else judges (rebase-and-resend is the Contributor's job, send-back suspicion the maintainer's).
- **Author-side v2 re-formation + serial numbering** (d76d916): reform re-runs only objected forming steps with feedback ("rfc vN+1", same branch); direction-ok assigns an `ai-org/serial/NNNN` tag.
- **Revision continuity: node identity contract + machine delta ledger + dangling-reference lint** (6337fa6, 9ab2c7e): surviving nodes keep ids; replacements declare {replaces, replacement_reason}; a mechanical ledger classifies prior objections superseded/addressed/unaddressed; fail-closed lint before commit. Why: three live failures of LLM-renamed ids culminating in an unresolvable-forever NAK trap.
- **Delta-scoped rounds with 1-hop neighborhood + objection lifecycle** (9ab2c7e): round N reviews changed/added nodes plus 1-hop referential neighbors; superseded objections force-include successors.
- **Round-1 sharding + rotating unchanged-node patrol** (c22c07b): per (dimension × step-subtree) shards against attention saturation; later rounds add a rotating K-node unchanged slice.
- **Bounded-rounds cap as a terminal hearing** (eed2458): the round entering at the cap still runs a real delta-scoped review; only its own verdict decides nak vs direction-ok. History: replaces the instant stale-list nak, which fired in zero seconds against a tree that had addressed both blockers.
- **Asymmetric review: reviewer never the ceiling** (2925692): objections carry self-declared reviewer_confidence/reviewed_scope; author defense-with-citations is first-class; a re-raised objection with no new evidence is stamped and excluded from blocking. Ratified requester ruling; grounded in kernel explain-duty and measured LLM sycophancy loss.
- **Defer-to-code with teeth** (77a25b0, 4d37422): author proposes {objection_id, executable_check, defer_reason}; accepted deferrals commit as an obligation artifact the patch author must answer, fail-closed backstop at acceptance. Why: decide irreversible questions on paper, empirical uncertainty in code (Rust RFC / IETF running-code canon).
- **Rewind depth decided by node position** (4d37422): in_patch fixes stay bounded; requires_direction_change commits a verbatim experience report routed back as an objection-grade record — a worker never improvises design distortion outside its jurisdiction.
- **Open-question closure: closure is judgment, retirement is code** (94c7ca0): targeted-revision fragments declare closed_questions (byte-matched); code retires and records disposition in the delta ledger. Why: append-only open_questions had become a contradiction generator (three round-6 NAKs).
- **Targeted node revision in bounded windows** (4008f88): per affected step, only anchored nodes + 1-hop neighborhood sent for revision; fragments merged code-side. Why: a one-fact demand survived five whole-step regenerations; two series died at the round cap.
- **Reform correctness guards** (3a18a1d, a62e07c, 7cb93ae, faef69e, 7449cf2, 008e144): coverage (not emptiness) guards decision evaluation; decision-derived view fields re-derived on change (fixing a stale-tech_stack objection loop); root-held reference remap for legacy danglings; one bounded reference-reconciliation repair round on the gadget pattern; reform routing derived from tree position, not id-prefix guessing.
- **Excise enterprise-SI ceremony from the objection space** (73d3d63, 792897d): a stated review doctrine bounds evidence horizon/genre (Linux-kernel grounding); decade-scale stewardship demands excluded by default, overridable only by a cited request passage.
- **Requester intervention ports on committed round records** (40098c4): annotation (attributed note on an objection) and demotion (flip resolution_authority to requester) — the two ratified manual ports, tattooed as precursor of a future human/lawyer-agent HITL mechanism.

### Rejected

- **Fixed 5-reviewer/checklist-scoring review structure** (e84192b): invented structure (per-concern reviewer slots, checklist scoring, review-invents-approach). Replaced by: axes + judgment synthesis; maintainers judge, structure aids judgment.
- **"The Aufheben said so" trusted as a merge** (80432a4, ADR-0012): no general lossless fuse operator exists. Replaced by: Aufheben as proposer/consolidator only.
- **Instant stale-list nak at the round cap** (eed2458): judged without hearing the current tree. Replaced by: terminal hearing.
- **Brief-21 in-prompt "UNRESOLVED INHERITED REFERENCES" scaffolding** (7449cf2): a conditional instruction buried in a 725KB prompt got zero engagement. Replaced by: reactive bounded reconciliation repair round.
- **Inline "Plan B" schema field on revision output** (77a25b0): re-inflated the output schema — the cognitive-debt disease the wave was curing. Replaced by: dedicated bounded contingency-elaboration calls as their own partition items.
- **licensing_cost lesson generalized to product-IP stance** (cc53787): re-imported a legal-department axis through the side door, burning 14 revision rounds on an unsettleable objection. Replaced by: scoped strictly to stack/tooling license+cost; product IP is a recorded requester-authority assumption.

---

## 5. Patchwork queue: decomposition, lineage & dependencies

### Adopted

- **Lineage machinery: typed-relation ledger, rolling-wave elaboration, AND roll-up** (fef1817): version-of/split-into/depends-on/supersedes relations, deterministic 100%-coverage ledger validated before any child branch, near-horizon leafing. History: supersedes the decompose() patch-series splitter (61d8264, deprecated fef1817); implementation A retired after an A/B clean-room experiment promoted Reference-informed B (673a812); the whole module family later renamed to patchwork_queue (d7eabb2).
- **Dependency network rebuild: single-source edges + state variables + total gate** (47958c2): an edges block (`depends|excludes|part_of` + `when` mini-language over declared state variables) is the one declared authority; readiness/topo-order are computed projections. Hardened by adversarial-review closure rounds — crash-proof when-eval, iterative cycle detection, guarded ingestion chokepoints, capped recursion (0ab41db, e709455, 9a812f3, 07c2c8f).
- **Strict `when`: typed variable registry, undeclared vars are form errors** (423b17a): ratified — "存在しない変数なんてもってのほか."
- **Structural state delegated entirely to git** (c4f8281): the dependency DAG is DERIVED from branch ancestry via git_wrapper; only irreducible semantic labels live in git notes.
- **Linux mirror Memento (P1–P7)** (f0c2b2f): the seven painful lineage phenomena are managed by convention in the kernel community, not mechanized; carved into lineage.py. Why: solving a managed-by-convention problem with machinery is the SAP failure mode.
- **Explicit verdict enums and directional edge names** (09aedbe, 5b2e0c5): `verdict: split|already_right_sized` replaces a two-reading boolean; `dependent_child_key`/`prerequisite_child_key` replace from/to arrows, each with worked examples and contradiction-retry guards.
- **Ledger never inherited by children** (5b2e0c5): child branches start ledger-stripped; foreign ledgers treated as absent; depth guard. Why: inheritance caused infinite recursion in resolved().
- **required-all-props schema artifact tolerance** (0a9dc6a): a right_sized answer legitimately carries a populated children array (schema forces it); tolerate and record as surplus.
- **Group tree merges implementations, never child doc nodes** (e1f1b29): child RFC branches share identical file paths, so doc merges conflict structurally; leaf-resolution requires the child's CONTRIB branch as ancestor.
- **Contribution branch named by stable RFC id** (cbec225): title-based naming broke branch_exists idempotency.
- **Generated content may cite only files existing at authoring time** (e253785): spine_contract_refs threads actual branch-file existence through generation — manifests had promised files nobody authored.
- **Never fabricate references from prose provenance** (9a6c336): a `draws_on` slug emits a cross_link only when it resolves to a real node id; otherwise the prose stays verbatim, unlinked.

### Rejected

- **decompose.py's bespoke rfc-metadata/rfc-decomposition state files** (c4f8281): double-managed structural state against git. Replaced by: git-derived dependency graph.
- **Dual dependency representation (raw declared list vs code-derived merge)** (47958c2): diverged on real data, no checkers. Replaced by: single declared edges block + total deterministic gate.
- **Change-Id-style amendment ledgers / central lineage planner** (f0c2b2f): the kernel community deliberately mechanizes none of these. Replaced by: nothing — ruled managed-by-convention.
- **Underscore-sanitizing of invalid stamped child keys** (f595e6b): silent renaming was already a rejected principle. Replaced by: typed `child_key_invalid` rejection.
- **Kind-era commission classification (text-sniffed template selection)** (bb7b218): a vibes classifier choosing a schema by content sniffing. Replaced by: shared generic rows + existence-gated references; commissions declare their own format in request text.
- **Child branches inheriting the parent's lineage ledger** (5b2e0c5): infinite recursion. Replaced by: stripped initial commit + absence defense + depth guard.

---

## 6. Implementation workers & carrier discipline (patch)

### Adopted

- **Contributor pull-model: claim/announce, own-clone work, submission, canonical-side acceptance** (202c453, 55c80d9): git is the only transport and arbiter; every binding state readable from pushed refs; projections are ref-derived. Realizes the ratified pull model — a series never selects a contributor.
- **No-lock per-author announcement refs** (c2cb17f): two authors on one series both succeed by construction; family-level collision resolved downstream at review, not at claim time.
- **Contribution loop: implement → acceptance, revise with blockers, capped** (4db202f): acceptance blockers feed back into implement up to a cap; state = git branches/commits, no ledger.
- **Contributor self-check loop** (ddeddb1): deterministic shell checks (build/test/lint/grep) after implement, resuming the same codex session on failure (SELF_CHECK_CAP=3); still re-verified independently downstream.
- **functional_check = Mona two-agent walkthrough** (f7a12a1, cd6cea9, c6e1e6a): stubborn-user persona vs. source-grounded app trace (file:line, no launch) checks a USER can reach the RFC goal beyond "tests pass"; read-only worktree, verdict recorded fail-closed (reachable=false). History: renamed from "acceptance" — this is the Contributor's own 動作確認, not independent approval.
- **patch_author code_worker: bounded per-item windows** (53aaad6): item = plan node + 1-hop referenced approach nodes + prior touched files, never the whole tree; implementation-result.json is harness-computed, never model-written. Why: whole-brief single-shot delegation had no conflict prevention or context discipline.
- **Precedent facets consumed in every item window** (709d55f): mechanical lookup-term derivation per plan item reads the store's implementation-lane research inside the bounded window — the knowledge pipeline had been write-only.
- **codex_exec rejection-fingerprint fail-fast** (18263f0): transient / stochastic-recoverable (inject the concrete rejection into the prompt) / permanent (identical fingerprint twice = abort). Why: runs died 75-77-minute slow deaths retrying the same deterministic rejection.
- **Worktree preserved on item failure** (3547ad0): success-only cleanup; failure paths detach, preserve, and report the checkout. Why: transient carrier usage limits destroyed multi-hour uncommitted trees. History: restores the pre-reset preserve-and-resume lesson (4be7a66).
- **Implementer2: CUE formal-frame path only** (04f9d8b): per item, author a CUE frame (state space/invariants/transitions), `cue vet` PASS/FAIL on real positive/negative states, non-vacuous, then implement.
- **Computed check facts in author briefs; acknowledgement cross-checked at delivery** (8691bac, 3129e01, ae688cc): the engine computes patchwork-check facts once and embeds them verbatim; functional_check recomputes and rejects contradictory acknowledgements. Why: patch-author LLMs must never read/interpret raw event logs directly.

### Rejected

- **Reservation-protocol claims (CAS lock on task-taking)** (c2cb17f): create-only push CAS with lease/renew/staleness machinery. Replaced by: no-lock announcement refs.
- **Signed-off-by / DCO trailer** (202c453): considered per kernel precedent, dropped per ruling collision. Replaced by: chain-of-custody = commit authorship + claim/announcement records.
- **Whole-brief single-shot delegation to the coding carrier** (53aaad6): no structural conflict prevention or bounded context. Replaced by: bounded per-item windows.
- **Impl2 evidence-verification apparatus (proof bundles, tier-honesty gate, scaffold-worklist)** (04f9d8b): injected scope beyond the actual request (GIGO). Replaced by: the CUE formal-frame path alone.
- **Deletion-on-failure worktree cleanup** (3547ad0): destroyed contributors' uncommitted trees on transient infra failures. Replaced by: success-only cleanup, preserve-and-report on failure.
- **"acceptance" as the name for the Contributor's own check** (f7a12a1): acceptance/UAT is an independent-of-author term. Replaced by: functional_check.

---

## 7. Acceptance & merge (maintainers)

### Adopted

- **Two-tier real git merge: subsystem then mainline** (1357dfb, c7dbf90): read-only worktree review by codex (maintainer, then Linus-analog), real `git merge --no-ff` on accept, fail-closed to reject; state = git refs only. Roles folded per tier — review+integrate is one act; Contribution is the internal implement/acceptance loop (533c487).
- **Rejection at a tier is a bounded revise loop, not terminal** (d37996e): route back downstream for fix and re-review up to a cap; only cap-exhaustion or fundamental NAK is terminal.
- **Goal acceptance after merge** (fb93c7c): acceptance.goal_met(rfc, mainline) verifies the merged result satisfies the RFC's intent end-to-end — "merged" is not "goal achieved."
- **Merge conflicts: abort+reject → rebase & resend** (6068648): conflicts only arise in merge stages (implement runs isolated); `git merge --abort` then send back; no self-written locks (git serializes per-worktree).
- **Worktree-discard as the proven conflict-safety mechanism** (585cbad): a real induced conflict left the ref untouched and zero leftover worktrees (throwaway worktree + `finally` force-remove). Empirically disproved Mona's claim of a half-merged state.

### Rejected

- **Stefan, the code-only LLM aesthetic verifier** (8de602d): gated OFF by default (`_stefan_enabled()`, env opt-in). Rejected because: a code-only model can't see rendered UI — structurally hollow judgment; don't make an LLM verifier do what it structurally can't. History: adopted pre-reset as Linon's design counterpart (43c01a7) → off by default (8de602d).

---

## 8. State on git & logging

### Adopted

- **Git-driven state: committed files on a dedicated ai-org-state branch** (d656cb8): state/<id>/{rfc,verdict,plan}.json + events.ndjson, CAS-protected; code progress on standard contrib/subsystem/mainline branches. Why: chosen over custom refs/git-notes so the system pushes to any standard Git host and gets UI/CI/backup free. History: supersedes the pre-reset in-org GoalStore/state-store line (a52e9e5 ADR-0007, 584c55d, cea0f83).
- **Host-agnostic push via plain `git push`** (d15a0d8): no gh/glab/REST — Mona caught that nothing was ever actually pushed; backs the any-Git-host portability claim.
- **PR = git branch, not a Python object** (c33628c): a contributor returns the pushed branch ref; the reviewer reads diff/commits straight from git.
- **git_wrapper as the one sanctioned READ-ONLY git super-module** (bc0223e, 859a347, c4f8281): git itself is the super-state; the wrapper is a lens storing nothing; single git-status window.
- **git_wrapper remote layer: typed fail-closed results + explicit engine git identity** (81c56b6): clone/fetch/push-CAS return typed reports, never raise; every engine commit passes explicit `-c user.name/email`. Why: CI has no git config; developer machines were leaking real emails into engine history.
- **Governance state lives on git: review rounds committed as branch files** (70f3245): round records, provenance, rejected-request outcomes committed on/near the series branch. Why: a live branch transplant carried only a status marker while objections stayed behind — everything resume needs must be reachable from a pushed ref. History: reverses 77183e9's off-git round-record store.
- **log super-module: events are source, views are projections** (e76cfcb): append-only JSONL stream under `.ai-org/log/runs/`; progress/timing/status/attempt-history rebuildable. Why: git keeps results, the log keeps process history — a silent process death took an hour of filesystem forensics.
- **launch: the org owns its own run entry point** (a070749): `python -m ai_org.launch` double-forks + os.setsid. Why: a run died to a SIGKILL job-control teardown race from ad-hoc heredoc scripts.
- **launch: fresh workspace by default; external API contracts enforced at construction** (6db27e9): no repo_path → disposable workspace; gh queries built through a capped splitter respecting GitHub's 5-operator limit (482 subprocess failures had burned on deterministically invalid queries).
- **Vessel discipline: the engine repo is never a vessel** (3fe3868): 230 tracked run-residue files evicted and gitignored. Why: running the org against its own engine repo poisoned artifact context (self-referential review drift).

### Rejected

- **Custom Git refs / git-notes for AI Org state** (d656cb8): invisible/not fetched by default; notes fragile. Replaced by: committed state files on the ai-org-state branch.
- **PR as a Python object (pr.py)** (c33628c): side-ledger anti-pattern duplicating git. Replaced by: plain branch refs.
- **Off-git review round store** (70f3245): objections stayed behind on transplant. Replaced by: governance records committed on git (see Adopted).

---

## 9. Engineering precedent store (formerly Reference)

### Adopted

- **Second super-module: off-git org knowledge store** (4a67abc): lookup+expand with delta-only inclusion over an LLM baseline, annotated from code merit not popularity; isolated from rfc/patch/merge. History: renamed reference.py → engineering_precedent_store.py after blank-model hallucination-risk probes — "reference" polysemy invited invented responsibilities (4107573, 3ea0e1a, 36dbe33).
- **Expand pipeline: repo search + strict delta + real distillation** (0f06122): search REPOS (stars as prefilter only) with broader derived concept keywords, extract patterns from likely files, keep only genuinely-better-than-baseline.
- **Keywords: general concepts, no domain/language baking-in, no hard language filter** (b539051, b7db334): over-qualified keywords matched zero repos; language recorded per-candidate as metadata only.
- **Append-only persistence, never delete** (ecf4a09): knowledge captured once survives even if the source vanishes; re-expand appends and dedupes.
- **Provenance and honest misses** (b3c0652, 8dd7a7e): kept=0 persisted with search_keywords + examined; per-candidate found_via — distinguishes bad query / genuinely nothing / baseline-sufficient.
- **SQLite store with consumption/management read split** (ca6f688): lookup returns applicability-filtered consumption fields; audit/query expose the maintenance record.
- **Single-pass term extraction + conservative term_key normalization** (b320393, 7dafbd8): one codex call returns implementation-bearing terms; NFKC/lowercase/punctuation-collapse normalization — deliberately no synonym tables/stemming/embeddings.
- **Concurrency + rate discipline** (5f23d86, b31df82, 90ecf7c): bounded thread pool, WAL-safe SQLite, shared 28/min gh-search limiter; later a cross-process file-based token bucket + 403 backoff-to-reset + memoized searches (264 HTTP 403s in one run from an in-process-only throttle).
- **DESIGN facet alongside implementation** (e4a4784): one store, `kind: implementation|design`; separate design search lane (ADR/RFC/PEP/pattern catalogs), own rate track. TTL-based re-search skip (46dc24a).
- **The consumer researches; the store repositories** (6d62817): step ③ does research on miss; the store is the repository of that research.
- **add_preheld: the org's own hard-won lessons become pre-held facets** (c201981): externally researchable knowledge reached implementations via the store; org-experiential knowledge did not until seeded.
- **Write-exact / read-paraphrase-tolerant, keys never destroyed** (3000e8b, aee16b9, 29fdb1b, 4736646): keys stored verbatim; fuzzy token-overlap fallback only on read-side exact-miss over a derived rebuildable index. Why: wording drift caused duplicate research pipelines (二重検索) on reworded queries.
- **AI_ORG_PRECEDENT_READ_DISABLED emergence switch** (3000e8b): every read reports miss while writes still bank, forcing fresh research — the ratified requester mechanism for controlled emergence experiments.
- **Drain the warmed implementation lane before pull exits** (bf5444a): bounded drain-or-record of background precedent builds — orphaned threads were losing banked knowledge.

### Rejected

- **gh search CODE literal text match** (0f06122): pure noise (an unrelated blackjack bot for "HP"). Replaced by: repo search + read-and-extract.
- **Hard --language filter** (b7db334): excluded quality cross-language patterns, kept same-language junk. Replaced by: unfiltered search, language as metadata.
- **Overwrite-per-term persistence (DELETE FROM candidates)** (ecf4a09): a nothing-found re-fetch erased prior knowledge. Replaced by: append-only + dedup.
- **N-gram + per-candidate codex term extraction** (b320393): ~179 noise phrases per RFC, one call per candidate. Replaced by: single-pass extraction.
- **"word salad" naming for the retrieval layer** (aee16b9): named for its mechanism, not its purpose. Replaced by: paraphrase-tolerant retrieval vocabulary throughout.

---

## 10. Schema & carrier I/O (codex constraints)

### Adopted

- **Codex --output-schema safe subset** (85e3d53, b172810, ee8882f): no allOf/if-then (HTTP 400); all properties required; `description` must be a string — the true root cause of the schema rejections, fixed via schema_description() and guarded by a structural test scanning every schema so the bug class cannot return. History: minLength (3777ed3) and const/if (88a8c9d) were "chased ghosts" masked by monkeypatched tests until ee8882f found the non-string description.
- **Schema-imported tests** (01d6f40): fake-dispatcher tests match imported module schemas, not hard-coded copies that go stale and mask real 400s.
- **Disposable schemas generated per use from deterministic builders** (6febeab): ~38 module-level schema dict constants replaced by builder functions discarded after use; deep-equality pinned against a pre-refactor snapshot. Why: the constant ledger was where workload-vocabulary residue hid.
- **Enum-only id references, never free strings** (d6a0981): anchor fields carry an ENUM of actually-valid node ids. Why: third live failure of the free-reference class — models cannot reliably transcribe ids from a large prompt; dangling lint kept as defense in depth.
- **Value constraints outside the schema** (3777ed3): non-empty enforcement moved to prompt instruction + contract-gate lint + deterministic fallback (working_title), since the schema subset can't express it.
- **Deterministic structure gadgets + reactive repair rounds** (a33215a, 5989b21, 51a0624): code owns structure (ids/slots/placeholders/repair deltas); gadgets fire reactively by error type, never as always-on scaffolding; review-side hints are non-binding "rustc fix-it" style. Why: contracts checked only after generation discarded a 38-minute revision on one cardinality mismatch.
- **Targeted-revision schema/parser parity walker** (4f46ff9): a test proves every schema builder's parser accepts its own required envelope — a stale strict keyset had permanently killed a reform on schema-exact output.
- **Granular rejection reasons, booleans derived** (4027ba7): `explain_tech_stack_violation` derives the boolean so they can't drift; a bare-boolean rejection converts model self-correction into permanent_rejection.
- **[pre-reset facts, still true of codex]** `.codex/agents/*.toml` is NOT loaded by `codex exec` — role/authority text must be injected into the prompt (8bc5154); nonzero exec exit is a transport failure, not a verdict on the work (c674b5a).

### Rejected

- **allOf/if-then conditional schemas** (85e3d53): rejected by codex outright, masked by mocks. Replaced by: flat schema, conditional fields optional.
- **minLength / const-if blamed as schema-rejection root cause** (3777ed3, 88a8c9d): ghosts. Replaced by: the non-string description root fix + guard test (ee8882f).
- **38 module-level schema dict constants** (6febeab): hand-written ledger hiding workload residue. Replaced by: per-use builder functions.
- **Free-string id references** (d6a0981): replaced by enum-constrained anchors.

---

## 11. Graphicist & the general artifact engine

### Adopted

- **Graphicist as an isolated module** (670fa05): constructive SVG + fetch + render + QA, not wired into rfc/patch/merge (architecture-test enforced). "codex = pipeline engineer, not blind illustrator."
- **Cute canon keeps subject fidelity** (ce2c125): stylization must not erase defining/counted features — apply cuteness BY stylizing them.
- **Animation via JS rig, not path geometry** (4a77bcd): codex generates rig.json keyframes over a fixed FK runtime; segmented SVG with stable part ids + pivot manifest.
- **autonomous_create: the self-driving artist** (5beceb7, df49925): the artist runs its own web research to derive its own art brief; quality is higher when the system self-grounds than when a human hand-feeds direction.
- **Axioms from masters; construction as geometry** (ab1ca74, 4498c1e, 48327ea, 4b7bea4): yaw as geometry not prose, deterministic head-count measurement/correction, deformation canons projecting one identity across proportion systems. History: canons distilled from professional packs (Open Peeps/Humaaans) were themselves poisoned when re-distilled from amateur clip-art ("the bullseye incident") and replaced by source-backed logo/mascot axioms (4b7bea4).
- **Commissioned head count is the contract** (ec087cd): explicit requested_head_units beats style-derived defaults — requester-specified values must never be silently overridden by machinery (same disease class as tech_stack provenance).
- **Platform vocabulary scoped by deliverable applicability** (5245b57): org-internal-token rejection (e.g. "worktree") applies only to user_facing deliverables; not_user_facing spec deliverables get their own target-surface vocabulary. Why: a correctly-grounded spec deliverable died permanently on false "org-verifier leakage."

### Rejected

- **Canons distilled from amateur clip-art exemplars** (4b7bea4): the well carries poison as faithfully as wisdom. Replaced by: source-backed axioms + canons from professional modular packs.
- **Hand-fed, human-authored per-asset art direction** (5beceb7, df49925): "cheating + human-rate-limited + lower quality." Replaced by: autonomous_create + intake grounding.

---

## 12. Pre-reset engine (archived at 49a9921): lessons retained

The mechanisms below live in `archive/`; they are recorded because their measured lessons
were paid for and must not be re-derived or re-litigated.

### Lessons from what worked

- **Linon made real, with fail-closed mechanical gates** (825ff19, 1ca19db, 60815ab, 50d46c3): a prior Linon was a facade (0 wiring hits); provenance/structure checks stay mechanical and forgery-resistant, semantics live in the reviewer's charter; merge readiness re-runs the gates rather than trusting stored records. "NN1 — tests aren't proof" (f6c5c91).
- **Reviewer is an unanchored adversary** (b0b8140): Linon deliberately never saw goal or contract — anchoring makes it confirm intent instead of finding unframed defects; intent-satisfaction belongs to deterministic conformance.
- **Worktree isolation as the parallelism boundary** (6987eef, 9a94b04, 8f4d332, 1cde373): independent roles/leaves in detached worktrees, serial only at merge-fold — the lesson the current engine's branch-based contribution model inherits.
- **Per-kind executable conformance, deliverable_kind mandatory** (16c937d, 91dfc26, 380fe7a, adcee90, c89b95f, a90f82f, 2ec082a): black-box checkers per kind (cli/http/library/json/batch/rpc), honest `none`/`undetermined` slots streamed unchecked but never silently passed (176bbce, 29cd7a4, 6f8391f).
- **Withheld oracle / principle-based withholding** (dbe4061, e451039 ADR-0018): an implementer that sees its own acceptance examples builds to match them; redact value-output assertions, never interface/shape.
- **Closed verification loop + three-valued gates** (489e64a, 3e47c35, ba3d9bf, 638dbec): re-run every block-mode gate on the current artifact each repair round; scanner crash = critical finding; a leaf is converged only when verified; only non-code dimensions ever get bounded clean retries.
- **Audit before promotion, shadow-first** (3341784, 22bf85f): gates flip shadow→block only after an LLM-free FP/catch-rate audit (FP=0%/catch=100%); unaudited/stochastic gates stay shadow.
- **Gate-behind + gate-fattening** (7e4b4c8 ADR-0017, b0827a0, a24aa12, 9ec2db1, 7e43f1e): cheap deterministic gates run first and skip the expensive reviewer; forbidden_patterns, regression_suite, static_checks, declared-kind e2e boundary probes moved dominant rejection classes off the reviewer.
- **Deterministic scaffold + recovery without anybody** (faa0cf6 ADR-0008, d05ffad, 2c4dd23): LLM-free skeleton seeding, mechanical scope-strip (trim not fail), no-progress circuit breaker, severity-weighted repair budget (b27269f) — most recovery holes need no human AND no LLM.
- **Python-cored role hosts** (ca54c0b ADR-0014, e05b730): deterministic spine (pre-localizer/guard-map/build-map) grounds the carrier prompt — "Codex on genius.py, not Codex as genius"; 9/9 gating rejections had been implementer-caused for lack of grounding.
- **Repair-loop economics: session reuse + delta prompts** (4420a66, 8ba38ca): resumed sessions plus "apply only the delta" instructions; session reuse alone didn't cut output tokens.
- **Deterministic CI-writer with negative-control proof** (17495c0 ADR-0019): the kernel authors the workflow; trust requires red-on-bad AND green-on-good before commit — open-loop LLM dependency lists produced red CI on healthy code.
- **Goal-level acceptance of the composed artifact** (2a91dca): intake-authored executable acceptance_profile boots the COMPOSED artifact against the user's WHY — every leaf can be green while the goal was never checked (Mona-found hole).
- **Org owns its own state; rich log or time bomb** (a52e9e5 ADR-0007, 584c55d, cea0f83): a goal becomes the org's own state, the log must reconstruct it, store and log are complements — superseded in mechanism by git-driven state (d656cb8), preserved in principle.
- **Node-targeted, additive steering** (ee4e6c6, be05d29): steering notes target goal|node and fold in via an append-only sidecar; kill-and-resume and goal-level-only steering were the rejected degenerate cases.
- **Carrier hang discipline** (19d07e2, 3906bf6, c4905e2, 6f3a68d, 25192b3): hard-wall timeout independent of poll, process-group reap, exit on turn-completed, watchdog thresholds tuned from live streams (the old 120s threshold was killing ~9% of healthy carriers); un-hardened subprocesses leak grandchildren.
- **Intake sufficiency + ask-and-wait, search-then-confirm** (7781c5e ADR-0016, 7ceac15, 0741e1e): deterministic sufficiency kernel, parked blocked_hitl leaves instead of endless re-splits, search own ADRs before asking bare questions — the HITL mechanism is dormant in the current engine per baa7644.
- **Border Collie: advisory anti-pattern patroller with habituation** (650bc81, 6588c31, 77516b6): read-only smell-dog pack barking corrective steers with an adaptive-threshold LIF habituation curve, so advisory notes don't accrete as permanent prompt sediment.
- **Measure before committing to skills** (3f759ab, 42d4b91): cassette routing was deterministic with a non-blocking shadow A/B against the LLM's own pick — skill evidence is weak/can hurt, so measure first.

### Lessons from what failed

- **Semantic judgment in token-checkers** (60815ab): a mechanical checker doing relevance/polarity judgment got bypassed AND false-rejected honest work. Semantics belong to the reviewer's charter.
- **Splitter granularity by count or diff size** (eb650e5→80214c7, 88e1c0e): "prefer more, smaller tasks" over-split an inherently-serial feature ~11 ways; diff-size shrinking never converges because review cost tracks blast radius. Lesson: decompose by IMPACT, start coarse, floor self-declared-minimal tasks (a3d76e8).
- **Linon prefetch (warm-then-resume the reviewer)** (c6a301c): the warmed reviewer under-rejected — flipped a true REJECT to ACCEPT. Never split comprehension from verdict.
- **Producer self-adversary review** (f6beab5, aa0fa8f ADR-0010): self-review before Linon flagged findings stricter than Linon's real bar at zero measured benefit — cost without value.
- **Destructive TCR auto-revert in a quality gate** (d309104): left untracked files, misreported reverts; revert decisions belong to the loop, not the gate.
- **Prompt prohibitions for structural problems** (f847cc3, d1e3693/e9e2134): "don't touch scratch" and "don't git-diff your own work" both papered over structural gaps that structural fixes (file-hiding, --no-index rendering) closed.
- **Regex/prose classifiers in blocking gates** (7e43f1e, 11fffb6): service-ness inferred from prose text and per-language binding resolution in the trusted kernel were both rejected — blocking gates must stay purely factual; classification/re-authoring routes to the designer.
- **The controller's hand-built UI** (6278785): retired by owner ruling after a carrier-produced version was judged better — capability must live in the pipeline, not the controller's hand.

---

## 13. Open / not built (explicitly deferred in the history)

- **contract_trace structured self-declaration (Alt 2)** (64fb124): deferred with an explicit wake condition — "no machinery ahead of an observed need."
- **depends_on / task-graph structure in the flat flow** (b6e797f): a hook to be added only when a real dependency or oversized task appears; not built up front.
- **Human/lawyer-agent HITL adjudication** (40098c4): the requester intervention ports (annotation, demotion) are tattooed as the precursor of a future in-the-loop mechanism; the mechanism itself is not built.
- **needs_confirmation opt-in** (baa7644): env-gated, kept dormant by design; autonomy remains the default.
