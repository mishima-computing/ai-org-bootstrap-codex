"""Materialize the converged RFC into contributor-sized Tasks — FOLLOW the RFC, do not re-decide.

Input : the converged RFC (direction-ok). The RFC already owns the split.
Output: a list of Task at contributor granularity.

Flat baseline: if the RFC's split is already contributor-sized and independent, return that list
as-is (each Task -> one contributor -> one PR; no graph). Structure is introduced ONLY when it is
actually required:
  - a Task is still too big        -> split it further (recurse)        [hook, not built yet]
  - Tasks have a real dependency   -> set depends_on + ordering/base     [hook, not built yet]

This mechanism does NOT make the split decision (the RFC did). It instantiates the RFC's
instructions into concrete Tasks. Any depends_on that does appear is validated well-formed /
acyclic, fail-closed (only relevant once dependencies exist).

STUB: reads the RFC through the carrier and emits Tasks; raises until wired.
"""
from __future__ import annotations

from . import carrier
from .rfc import RFC
from .task import Task


def decompose(rfc: RFC) -> list[Task]:
    """Turn the converged RFC into a flat list of contributor-sized Tasks (independent baseline)."""
    prompt = (
        "Materialize this APPROVED RFC's split into concrete, contributor-sized tasks. Follow the "
        "RFC's stated approach and breakdown; do not re-decide the split. For each task give: a "
        "short id, the objective, the interface/contract to satisfy, and the file/symbol scope it "
        "may touch. Keep tasks INDEPENDENT unless the RFC states a real dependency.\n"
        f"RFC: {rfc.title}\nproblem: {rfc.problem}\nproposed_change: {rfc.proposed_change}\n"
        f"interface_sketch: {rfc.interface_sketch}\n"
    )
    resp = carrier.invoke(carrier.CarrierRequest(role="decompose", prompt=prompt))
    # TODO(parse): resp.text -> list[Task]; flat (depends_on empty) in the baseline.
    # TODO(hooks): if a task is oversized -> recurse; if real deps -> set depends_on (validate acyclic).
    raise NotImplementedError("decompose parse not wired (stub)")  # pragma: no cover
