"""AI Org — clean rebuild (2026-06-28), designed on the Git / Linux-community model.

The prior engine (all executor generations + its ADRs) is under ../archive/ and is NOT reused.

Design premise: Git was built for a distributed OSS community (Linux). We model the system on
the *people* that appear in such a community rather than on invented abstractions:

  - Contributor   — does one scoped piece of work on its own branch, submits a patch for review.
  - Reviewer(s)   — review the direction (RFC) and later the patch.
  - (maintainer-role — to be named later; owns an area, integrates upward. NOT called "maintainer"
                       in code, by request — like "Linon" for the reviewer.)
  - Top integrator — pulls subsystem trees into mainline (Linus).

Flow being built, top-down:

  RFC (apex; for now inserted manually)
    -> RFC review: 5 independent reviewers debate the DIRECTION, an (archived) aufheben
       consolidates, the 5 re-critique, looping until no unresolved objection (no cap)
    -> [next: a real patch series written by Contributor(s)]  <-- not built yet

Everything here is currently a STUB: orchestration is real Python; LLM-backed roles go through
the carrier seam in ``llm.py`` (subprocess to a coding-agent CLI), which is not wired yet.
"""
