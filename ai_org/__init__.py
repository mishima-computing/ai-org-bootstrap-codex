"""AI Org — clean rebuild (2026-06-28), designed on the Git / Linux-community model.

The prior engine (all executor generations + its ADRs) is under ../archive/ and is NOT reused.

Roles (named so each is clear; the only code author/fixer is the Contributor — everything else reviews):

  RFC Creation                  the apex: the translated, implementable requirement (manual for now).
  RFC reviewers (5) + Aufheben  judge the RFC's direction (need/approach/compat/scope/maintenance);
                                Aufheben consolidates objections into a revised RFC; loop to convergence.
  decompose                     materialize the converged RFC's split into Contributor-sized Tasks.
  Contribution { Implement + Acceptance }
                                Contributor implements (the sole code author/fixer); Acceptance
                                independently checks the user can reach the goal; internal revise loop.
  Subsystem_tree_maintainer     layer 1: review a Contribution + (on accept) integrate to the subsystem tree.
  Mainline_maintainer (Linus)   layer 2: review the subsystem tree + (on accept) pull to mainline.

Every fail/reject routes back to the Contributor. Two integration tiers (subsystem + mainline); deeper
nesting only if a subsystem needs it. LLM-backed roles go through the carrier seam (``carrier.py``).

Everything here is currently a STUB: orchestration shape is real; the carrier is not wired.
"""
