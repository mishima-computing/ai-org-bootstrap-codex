"""judge: the deterministic judge of the AI Org's court.

It adjudicates provenance disputes -- which carrier (Codex) session produced
which committed body text, when, reading what, with what recorded
deliberation -- purely from the records: the vessel's git history, the
run logs under <vessel>/.ai-org/log/runs, and the carrier rollouts under
~/.codex/sessions. It renders no opinions; it renders the record.

INVARIANTS (Memento tattoo):
  * READ-ONLY everywhere. This package never creates, modifies, or deletes
    anything inside vessel repos, run logs, or ~/.codex.
  * NO LLM anywhere. Every link in the chain is deterministic string/JSON
    processing; the same inputs always produce the same report.
  * A link that cannot be resolved is reported as broken with the exact
    reason -- never guessed.

Public API (library-first; the CLI in __main__ is a thin printer):
    judge.trace(vessel, branch, body_path, node=None, term=None,
                at_commit=None, ...)
        -> ProvenanceReport
        at_commit=<sha> is origin mode: the target is resolved AS OF that
        commit and traced to the commit that INTRODUCED the text, so text
        later deleted from the body can still be adjudicated.
    judge.calls(vessel, run_dir_or_latest, ...)
        -> list[CarrierCall]
"""

from .model import (
    CarrierCall,
    ProvenanceReport,
    RoundRecordHit,
    RunLink,
    SessionLink,
    Target,
    ThinkingItem,
    ThinkingLink,
    VersionLink,
)
from .trace import calls, trace

__all__ = [
    "trace",
    "calls",
    "ProvenanceReport",
    "Target",
    "VersionLink",
    "RoundRecordHit",
    "RunLink",
    "CarrierCall",
    "SessionLink",
    "ThinkingLink",
    "ThinkingItem",
]

__version__ = "0.1.0"

