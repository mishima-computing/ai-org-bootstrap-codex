"""Revive a dead Codex session against its recorded work tree.

VERIFIED SEMANTICS (live experiment E1/E2, 2026-07-11, codex-cli 0.144.1):
  E1: `codex exec resume <uuid>` operates in the INVOKING process's cwd, not
      the session's recorded cwd. (Resumed a session recorded in dir A from
      dir B; the new file landed in dir B while conversational context from
      dir A was fully remembered.) Therefore revive MUST launch codex with
      cwd set to the session's recorded cwd.
  E2: Resume-by-uuid works from a foreign cwd WITHOUT `--all` (that flag only
      widens `--last` picking, which is cwd-filtered by default).
  Bonus: resuming APPENDS to the same rollout file (no fork); the same uuid
      remains valid for repeated revives.

Safety gates, enforced IN ORDER (Memento tattoo — do not reorder):
  (a) the uuid prefix must resolve to exactly one session;
  (b) REFUSE if the session appears live (never double-drive a carrier);
  (c) the recorded cwd must exist — otherwise print preserved-worktree
      candidates and exit nonzero (never guess a replacement cwd);
  (d) only then run `codex exec resume <uuid> "<prompt>"` with cwd = the
      recorded cwd, adding --skip-git-repo-check only when that cwd is not
      inside a git repository.

INVARIANTS: never kill processes; never write under ~/.codex; the only file
this module ever creates is its own --detach log inside the target cwd.
"""

from __future__ import annotations

import glob
import os
import shlex
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .scanner import Session

DEFAULT_PROMPT = "Continue where you left off."


class ReviveError(Exception):
    """Raised when a safety gate refuses the revive. .code is the exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def is_inside_git_repo(path: str) -> bool:
    """True when `path` is inside a git repository (walks up for .git).

    .git may be a directory (normal repo) or a file (worktree/submodule).
    """
    p = Path(path).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / ".git").exists():
            return True
    return False


def build_command(uuid: str, prompt: str, cwd: str) -> list[str]:
    """Exact argv for the resume, per verified E1/E2 semantics.

    --all is NOT needed for resume-by-uuid (E2). --skip-git-repo-check is
    added only when the target cwd is not inside a git repo, because codex
    refuses to run in a non-trusted (non-git) directory otherwise.
    """
    cmd = ["codex", "exec", "resume", uuid]
    if not is_inside_git_repo(cwd):
        cmd.append("--skip-git-repo-check")
    cmd.append(prompt)
    return cmd


def preserved_worktree_candidates() -> list[str]:
    """Preserved implementer worktrees the operator might mean instead.

    The engine preserves failed implementer worktrees at paths like
    $TMPDIR/ai-org-patch-author-*/worktree.
    """
    roots = {tempfile.gettempdir(), "/tmp", "/private/tmp"}
    hits: set[str] = set()
    for root in roots:
        for p in glob.glob(os.path.join(root, "ai-org-patch-author-*", "worktree")):
            if os.path.isdir(p):
                hits.add(p)
    return sorted(hits)


def make_log_path(cwd: str, uuid: str, now: Optional[datetime] = None) -> Path:
    """Log file for --detach, inside the target cwd (the only file we write)."""
    now = now or datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    return Path(cwd) / f"carrier-revive-{uuid[:8]}-{stamp}.log"


def check_gates(
    session: Session,
    live_matches: list,
) -> None:
    """Gates (b) and (c). Gate (a) — unique resolution — happens in the CLI
    before a Session object even exists. Raises ReviveError on refusal."""
    # Gate (b): never double-drive a live carrier.
    if live_matches:
        lines = [
            f"REFUSED: session {session.short_uuid} appears LIVE; will not double-drive.",
        ]
        for entry, reason in live_matches:
            lines.append(f"  pid {entry.pid} ({reason}): {entry.command[:120]}")
        raise ReviveError("\n".join(lines), code=3)
    # Gate (c): cwd must exist; never guess a replacement.
    if not session.cwd_exists:
        lines = [
            f"REFUSED: recorded cwd does not exist: {session.cwd or '(empty)'}",
            "Preserved ai-org worktree candidates (pick one manually; this tool never guesses):",
        ]
        candidates = preserved_worktree_candidates()
        if candidates:
            lines.extend(f"  {c}" for c in candidates)
        else:
            lines.append("  (none found)")
        raise ReviveError("\n".join(lines), code=4)


def run_revive(
    session: Session,
    prompt: str = DEFAULT_PROMPT,
    dry_run: bool = False,
    detach: bool = False,
    live_matches: Optional[list] = None,
) -> int:
    """Execute gates (b)-(d) and launch the resume. Returns an exit code.

    `live_matches` is injectable for tests; production callers pass the real
    ps-based matches (computed by the CLI before calling here).
    """
    check_gates(session, live_matches or [])

    cmd = build_command(session.uuid, prompt, session.cwd)

    if dry_run:
        print(f"cwd: {session.cwd}")
        print(f"cmd: {shlex.join(cmd)}")
        if detach:
            log = make_log_path(session.cwd, session.uuid)
            print(f"detach: nohup {shlex.join(cmd)} </dev/null >>{shlex.quote(str(log))} 2>&1 &")
        return 0

    if detach:
        # Equivalent of `nohup ... </dev/null >>log 2>&1 &`: new session so the
        # child survives our exit, stdio redirected to the log in the target cwd.
        log = make_log_path(session.cwd, session.uuid)
        with open(log, "ab") as logf:
            proc = subprocess.Popen(
                ["nohup", *cmd],
                cwd=session.cwd,
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=logf,
                start_new_session=True,
            )
        print(f"detached: pid {proc.pid}")
        print(f"log: {log}")
        return 0

    # Foreground: inherit stdio so the operator watches the carrier work.
    proc = subprocess.run(cmd, cwd=session.cwd)
    return proc.returncode
