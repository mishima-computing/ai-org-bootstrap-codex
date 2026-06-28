"""functional_check — the Contributor's own functional verification (動作確認) of its work.

Part of a Contribution (alongside implement's mechanical self-check). Here the Contributor runs its branch
the way a user would, to confirm the user can actually REACH THE GOAL — beyond "tests pass" ("all tests
passed but the user still couldn't play"). This is self-verification (a courtesy/quality step), NOT
approval; the independent judgment is downstream (the maintainers). On fail -> the Contributor fixes.

Substance = the Mona walkthrough: a two-agent static check - a stubborn USER persona that keeps trying
until truly blocked, and a code-grounded APP that traces the real source (file:line) and confesses gaps
and false successes, without launching the app. Output: a reachability verdict + where intent meets
broken reality.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Any

from ..platform import carrier
from ..rfc.receive import RFC


VERDICT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["reachable", "blockers", "notes"],
    "properties": {
        "reachable": {"type": "boolean"},
        "blockers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["where", "why"],
                "properties": {
                    "where": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


def check(rfc: RFC, branch: str) -> dict:
    """Run Mona: can a user reach the RFC goal end-to-end with this branch?"""
    repo = Path.cwd().resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-functional-check-"))
    worktree = temp_dir / "worktree"
    schema_file = temp_dir / "functional-verdict.schema.json"
    out_file = temp_dir / "functional-verdict.json"

    try:
        _git(repo, "worktree", "add", "--detach", str(worktree), branch)
        _make_read_only(worktree)
        schema_file.write_text(json.dumps(VERDICT_SCHEMA, indent=2), encoding="utf-8")

        result = carrier.run_codex(
            worktree,
            _prompt(rfc),
            "read-only",
            out_file=out_file,
            output_schema=schema_file,
        )
        if not result.get("ok"):
            return _verdict(
                reachable=False,
                blockers=[
                    {
                        "where": "functional_check",
                        "why": "Codex Mona walkthrough did not complete successfully.",
                    }
                ],
                notes=str(result.get("last_message") or ""),
            )

        return _parse_verdict(str(result.get("last_message") or ""))
    finally:
        if worktree.exists():
            _make_writable(worktree)
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        shutil.rmtree(temp_dir, ignore_errors=True)


def _prompt(rfc: RFC) -> str:
    return (
        "Conduct the Mona functional verification walkthrough for this branch.\n"
        "This is the Contributor's own 動作確認: decide whether a real USER can reach "
        "the RFC goal end-to-end with the code in this checkout.\n\n"
        "Hard rules:\n"
        "- Do not launch the app, services, tests, scripts, package managers, or build tools.\n"
        "- Do a static source walkthrough only, grounded in the files in this checkout.\n"
        "- Use exactly two personas: USER and APP.\n"
        "- USER is stubborn and tries to accomplish the RFC goal. USER keeps trying past "
        "dead-ends until truly blocked.\n"
        "- APP answers only from real source citations in file:line form. APP must not assume "
        "missing behavior exists.\n"
        "- APP must confess gaps, missing handlers, wrong wiring, missing persistence, "
        "authorization/API mismatches, and false successes such as a success toast over a "
        "backend failure.\n"
        "- Decide whether USER can reach the goal end-to-end. Passing tests alone is not enough.\n"
        "- If not reachable, list every blocker with WHERE as file:line and WHY as the exact "
        "reason the user is blocked.\n\n"
        "RFC:\n"
        f"title:\n{rfc.title}\n\n"
        f"problem / user goal:\n{rfc.problem}\n\n"
        f"proposed change:\n{rfc.proposed_change}\n\n"
        f"interface sketch:\n{rfc.interface_sketch or '(none)'}\n\n"
        f"notes:\n{rfc.notes or '(none)'}\n\n"
        "Return only JSON matching the provided schema:\n"
        '{"reachable": boolean, "blockers": [{"where": "file:line", "why": "reason"}], '
        '"notes": "brief walkthrough summary"}'
    )


def _parse_verdict(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _verdict(
            reachable=False,
            blockers=[
                {
                    "where": "functional_check",
                    "why": "Codex Mona walkthrough returned invalid JSON.",
                }
            ],
            notes=raw,
        )

    if not isinstance(parsed, dict):
        return _invalid_shape(raw)

    reachable = parsed.get("reachable")
    blockers = parsed.get("blockers")
    notes = parsed.get("notes")
    if not isinstance(reachable, bool) or not isinstance(blockers, list) or not isinstance(notes, str):
        return _invalid_shape(raw)

    normalized_blockers = []
    for item in blockers:
        if not isinstance(item, dict):
            return _invalid_shape(raw)
        where = item.get("where")
        why = item.get("why")
        if not isinstance(where, str) or not isinstance(why, str):
            return _invalid_shape(raw)
        normalized_blockers.append({"where": where, "why": why})

    return _verdict(reachable=reachable, blockers=normalized_blockers, notes=notes)


def _invalid_shape(raw: str) -> dict:
    return _verdict(
        reachable=False,
        blockers=[
            {
                "where": "functional_check",
                "why": "Codex Mona walkthrough returned JSON that did not match the verdict schema.",
            }
        ],
        notes=raw,
    )


def _verdict(*, reachable: bool, blockers: list[dict], notes: str) -> dict:
    return {
        "ok": reachable,
        "reachable": reachable,
        "blockers": blockers,
        "notes": notes,
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _make_read_only(path: Path) -> None:
    for root, dirs, files in os.walk(path):
        for name in files:
            _chmod_read_only(Path(root) / name)
        for name in dirs:
            _chmod_read_only(Path(root) / name)
    _chmod_read_only(path)


def _make_writable(path: Path) -> None:
    for root, dirs, files in os.walk(path):
        for name in dirs:
            _chmod_writable(Path(root) / name)
        for name in files:
            _chmod_writable(Path(root) / name)
    _chmod_writable(path)


def _chmod_read_only(path: Path) -> None:
    if path.is_symlink():
        return
    mode = path.stat().st_mode
    if path.is_dir():
        path.chmod((mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) & ~0o222)
    else:
        path.chmod(mode & ~0o222)


def _chmod_writable(path: Path) -> None:
    if path.is_symlink():
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IWUSR)
