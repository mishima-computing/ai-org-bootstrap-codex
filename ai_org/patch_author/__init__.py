"""Patch author pull model: take an OPEN series, author in your OWN clone.

Memento: the series never selects an author [RATIFIED]. A patch author
discovers open series from pushed refs and committed artifacts alone,
announces authoring intent on its OWN visibility ref
(refs/ai-org/authoring-announcements/v2/<coordinate-sha256>, with the former
readable coordinate retained only as a migration alias),
implements in a private clone, and publishes exactly two things back: the
announcement ref and its own contribution branch. Acceptance and merge stay
canonical-side (an independent judge, never self-issued).

NO LOCK [RATIFIED requester, brief 23 addendum]: "open" is the TASK's status;
taking does not change it. 家族間重複=競争、家族内重複=無駄 — parent A and
parent B on the same task is pure competition (resolved downstream at review:
accept/superseded, kernel canon); only WITHIN a family does the parent
partition work among its children to avoid waste. No inter-family arbitration
code exists anywhere in this package, and none may be added.

Governance-on-git: every binding state in this package is readable from pushed
refs — no side database, no server API, no log-as-state. Discovery is
ls-remote + committed artifacts; an announcement is a commit on the author's
own ref whose first parent is the series head it was read against; withdrawal
is ref REMOVAL (absence is the state, no ledger).

Module map (announcements remains below the patchwork gate so the gate can
project lifecycle states from announcement refs without an import cycle;
discovery/work/submission may import the gate):
  announcements.py — registered root/child announcement variants (v2) plus
                     strict v1 read compatibility,
                     fail-closed parsing, announce/withdraw, address listing.
  contract.py      — the committed implementation-result contract schema.
  discovery.py     — open-series projection from a canonical remote
                     (OPEN = task status only; announcements are visibility).
  work.py          — the PARENT: author identity holder, series
                     materialization, entry into the code worker.
  code_worker.py   — the patch-plan drive: bounded per-item windows, one
                     commit per item, declared checks, fingerprinted repair,
                     harness-computed result artifact; children = per-partition
                     worker invocations under the parent identity.
  submission.py    — create-only contribution branch push (submitted projection).
"""
