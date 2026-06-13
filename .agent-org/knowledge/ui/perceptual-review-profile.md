# Perceptual Review Profile

## Purpose

Verifier-pattern review profile for Stage-B closeout on human-facing surfaces. The profile reviews controller-executed captures against ratified spec composition propositions and date-pinned exemplars; it does not change roster or adoption authority.

## Packet

The controller supplies one packet per surface:

- capture set from `scripts/capture-screens.py`, including `capture-metadata.json`, desktop fold, full-page capture or slices, and sub-500px iframe-harness PNGs carrying the vh/position:fixed divergence caveat.
- ratified spec composition propositions as the review rubric; register and feel-class propositions require comps when prose does not make the proposition decidable.
- exemplar PNG references copied under the run directory from the date-pinned `exemplars.md` registry; registry pointers alone are not capture evidence.

## Codex Adapter Mechanics

The controller is the sole live-capture executor. Reviewer Codex adapters receive only packet paths, PNG read access, and the spec text; the packet metadata field `controller_only_live_capture: true` is the structural check for this boundary.

## Output

The reviewer returns:

- absolute verdict for each rubric proposition: `pass`, `fail`, or `indeterminate`, with no relative softening against implementation effort.
- exemplar-anchored comparison for every proposition that cites an exemplar reference.
- findings where every item cites a screenshot path and bounded region, for example `desktop-fold-1280.png region top nav, x=0-1280 y=0-96`.
- no-finding result only when each proposition has an explicit pass or indeterminate explanation.

## Zero-Finding Spot Check

A first-pass zero-finding review on a human-facing surface triggers controller spot-check against the capture set and rubric before closeout. If three consecutive spot-check cycles show the reviewer finding only noise or no additional perceptual value, demote this profile from required closeout to optional evidence for that surface class; this is the recorded rejection condition.
