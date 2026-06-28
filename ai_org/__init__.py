"""AI Org — clean rebuild (2026-06-28), designed on the Git / Linux-community model.

The prior engine (all executor generations + its ADRs) is under ../archive/ and is NOT reused.

Stages and roles (the only code author/fixer is the Contributor — everything else reviews):

  rfc                           the proposal/review stage: RFC creation, review, and decomposition.
  patch                         the write-the-patch stage: Contributor implementation plus independent
                                acceptance; internal revise loop.
  merge                         the integrate-to-mainline stage: subsystem and mainline maintainers review
                                accepted contributions and merge them onward.

Every fail/reject routes back to the Contributor. Two integration tiers (subsystem + mainline); deeper
nesting only if a subsystem needs it. LLM-backed roles run their stage calls directly.

Everything here is currently a STUB: orchestration shape is real.
"""
