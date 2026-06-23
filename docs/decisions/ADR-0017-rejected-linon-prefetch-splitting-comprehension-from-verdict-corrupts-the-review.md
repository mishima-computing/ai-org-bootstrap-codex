# ADR-0017: Rejected — pre-warming the adversarial reviewer corrupts its verdict; the only safe lever is to run it *less*, not *faster*

## Status

Rejected (the prefetch / session-resume reviewer), on empirical evidence. The verdict-safe alternative
(gate-behind: deterministic gates first, the reviewer only on a gate-clean diff) is forward work. Refines
ADR-0006 (do not narrow the verifier's field of attention), ADR-0010 (a tempting shortcut to the review role is
rejected on evidence, not on taste), and ADR-0011 (unproven never passes). Engine-only: this governs the review
role inside the pipeline and makes no reference to any composing product.

## Context

The adversarial reviewer role ("linon") is the single largest wall-clock cost in the per-leaf dialectic. The
obvious instinct is to make it cheaper. Two facts shape what is possible:

1. **The reviewer is near-zero-injection.** It is not a custom role-carrier with an injected prompt/contract; it
   is Codex's native `codex review --uncommitted` run with `cwd = the leaf worktree`. What is "injected" is only
   the flag that anchors it on the leaf's uncommitted diff — no contract, no acceptance, no role spec, no repo
   digest. The review is **diff-anchored**: it starts from the change set and reads the touched files and their
   cross-file dependents *itself*.

2. **The cost is the diff-anchored reading + reasoning**, which is what makes the reviewer valuable: it catches
   the defects the cheap deterministic gates miss — a change that *passes the gates but is semantically wrong*
   (synthetic test shapes that never exercise the real wiring, a producer path that drops an error, integration
   that is asserted but not actually run).

The proposed optimization ("prefetch"): split the reviewer into a diff-independent **comprehension** phase
(read the repo + what "passing" means) started early — in parallel with the designers, so it hides behind the
long design+build window — and a diff-dependent **verdict** phase that *session-resumes* the warmed reviewer with
the implementer's diff. The goal was to shave the comprehension off the critical path *without changing the
verdict*.

## Decision

**Reject the prefetch. Splitting comprehension from verdict corrupts the verdict.**

A real session-resume proof-of-concept (warm a reviewer on repo+contract, resume the same session with a real
diff; compare to a full from-scratch reviewer on the same diff, several trials) showed the warmed-then-resumed
reviewer **under-rejects**: it found materially fewer real defects and flipped a true REJECT to ACCEPT. The
disagreement is **not** an artifact — it reproduced with a genuine session-resume, not only with a lossy summary.
A reviewer that forms expectations *before* it sees the change narrows its own field of attention — exactly the
failure ADR-0006 refuses ("do not narrow the verifier's field on assumption"). Buying wall-clock with a weaker
safety net is the ADR-0005 mistake, one role over.

Two corollaries follow:

- **There is no injection-side lever.** Because the reviewer is diff-anchored native `codex review`, comprehension
  and verdict are structurally inseparable — the reading *follows* the diff. There is no cached substrate to
  pre-build and inject (and injecting one is what corrupts the verdict). Making the reviewer "smaller/faster"
  (a cheaper model, a narrowed scope) trades detection for speed — rejected for the same reason.

- **The only verdict-safe cost lever is to run the reviewer LESS, not faster.** The reviewer is only *needed* on a
  diff that has already passed the cheap deterministic gates (a gate-failing diff is doomed regardless — it
  repairs no matter what the reviewer says). So the forward-work optimization is **gate-behind**: run the cheap
  deterministic gates (conformance / secret-scan / scope, seconds) first, and start the expensive reviewer ONLY
  if they pass. This never skips a gate-clean diff, so the verdict on everything the reviewer actually judges is
  unchanged. (A "start the reviewer in parallel and force-kill it when a cheap gate vetoes" variant was also
  considered and set aside: the cheap gates are seconds, so not-starting wastes zero tokens and needs no kill
  machinery, whereas killing wastes the partial-review tokens.) The reviewer already skips one case for the same
  reason — a greenfield scaffold seed has nothing to adversarially verify yet.

## Consequences

- The reviewer stays a fresh, full, from-scratch read of the actual diff — its verdict is preserved by
  construction. Its wall-clock cost is accepted as intrinsic to honest adversarial review.
- Real reviewer-time reclaim comes only from (a) **gate-behind** (skip the reviewer on a diff a cheap gate already
  rejected) and (b) **general leaf parallelism** (a fresh full review of leaf N overlapping the build of an
  independent leaf N+1 — no session split, identical verdict). Both are verdict-safe; neither makes the reviewer
  itself faster.
- Gate-behind's only cost is lost finding-batching on a gate-failing iteration (the reviewer's findings surface a
  round later), so its net saving scales with the cheap-gate *failure rate* — to be measured before wiring.
- General principle recorded: an adversarial verifier's speed must never be bought by changing how it reviews.
  Optimize *when* and *whether* it runs, never *how* — the moment the review behavior changes, the verdict is no
  longer trustworthy.
