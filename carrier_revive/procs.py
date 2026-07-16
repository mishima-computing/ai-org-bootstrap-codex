"""Best-effort liveness detection for Codex sessions.

Heuristic (documented, best-effort — false negatives are possible):
  A session counts as LIVE when a currently running "codex" process matches:
    (A) its command line contains the full session uuid
        (a resumed session runs as `codex exec resume <uuid> ...`), or
    (B) its command line declares the session's recorded cwd via `-C <dir>` /
        `--cd <dir>` (verified live: the ai-org engine launches carriers as
        `codex exec ... -C /tmp/engine_cue_phase2 ...` from a different
        process cwd, so lsof alone would miss them), or
    (C) the process's current working directory (via
        `lsof -a -p PID -d cwd`) equals the session's recorded cwd
        (a fresh `codex exec` in-place carries no uuid or -C on its argv).

INVARIANTS (Memento tattoo):
  - This module only OBSERVES processes (ps / lsof). It never sends signals.
  - There is deliberately no kill/terminate code anywhere in this package.
  - PID 10882 is on a hard do-not-touch list; even observation results for it
    are never acted upon (we act on nothing — we only report).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

# Hard protection: never interact with these pids in any way beyond reading ps
# output that happens to include them. (Operator-mandated; see README.)
FORBIDDEN_PIDS = frozenset({10882})

_CODEX_WORD = re.compile(r"(?:^|[/\s])codex(?:\s|$)")

# `-C <dir>` / `--cd <dir>` on a codex command line declares the working dir.
# Best-effort: paths containing spaces are not recoverable from ps output.
_CD_FLAG = re.compile(r"(?:^|\s)(?:-C|--cd)\s+(\S+)")


@dataclass
class ProcEntry:
    pid: int
    command: str


def parse_ps_output(text: str) -> list[ProcEntry]:
    """Parse `ps -axo pid=,command=` output into ProcEntry rows."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, cmd = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        entries.append(ProcEntry(pid=pid, command=cmd))
    return entries


def ps_snapshot() -> list[ProcEntry]:
    """Read-only snapshot of running processes. Empty list on failure."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_ps_output(out)


def is_codex_command(command: str) -> bool:
    """True when the command line looks like a codex CLI process."""
    return bool(_CODEX_WORD.search(command))


def declared_cd(command: str) -> Optional[str]:
    """Extract the `-C`/`--cd` working-dir argument from a codex command line."""
    m = _CD_FLAG.search(command)
    return m.group(1) if m else None


def pid_cwd(pid: int) -> Optional[str]:
    """Return a process's cwd via lsof (macOS has no /proc). None on failure."""
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def find_live_matches(
    session_uuid: str,
    session_cwd: str,
    entries: Optional[list[ProcEntry]] = None,
    cwd_of: Callable[[int], Optional[str]] = pid_cwd,
) -> list[tuple[ProcEntry, str]]:
    """Return (process, reason) pairs indicating the session appears live.

    `entries` and `cwd_of` are injectable so tests never touch real processes.
    """
    if entries is None:
        entries = ps_snapshot()
    matches: list[tuple[ProcEntry, str]] = []
    uuid = session_uuid.lower()
    codex_entries = [e for e in entries if is_codex_command(e.command)]
    for e in codex_entries:
        if uuid and uuid in e.command.lower():
            matches.append((e, "uuid-in-command-line"))
    matched_pids = {e.pid for e, _ in matches}
    if session_cwd:
        # (B) -C/--cd declared on the command line (cheap, no lsof needed).
        for e in codex_entries:
            if e.pid in matched_pids:
                continue
            declared = declared_cd(e.command)
            if declared is not None and _same_path(declared, session_cwd):
                matches.append((e, "cd-flag-matches"))
                matched_pids.add(e.pid)
        # (C) actual process cwd via lsof.
        for e in codex_entries:
            if e.pid in matched_pids:
                continue
            cwd = cwd_of(e.pid)
            if cwd is not None and _same_path(cwd, session_cwd):
                matches.append((e, "codex-process-cwd-matches"))
    return matches


def _same_path(a: str, b: str) -> bool:
    """Compare paths tolerating the macOS /tmp -> /private/tmp symlink."""
    def norm(p: str) -> str:
        p = p.rstrip("/")
        for prefix in ("/tmp/", "/var/"):
            if p.startswith(prefix) or p == prefix.rstrip("/"):
                p = "/private" + p
                break
        return p

    return norm(a) == norm(b)
