from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from ai_org import mailing_list
from judge import rollout


SESSION_A = "019f0000-aaaa-7000-8000-000000000001"
SESSION_B = "019f0000-bbbb-7000-8000-000000000002"
ITEM_ID = "patch_plan:provided_approach#first_proof_moment"
CONTRIB_BRANCH = "ai-org/contrib/demo"
RUN_ID = "run-20260713T000000Z-qa-fixture"
FIXTURE_SESSIONS = Path(__file__).parent / "fixtures" / "codex_sessions"


def test_harvest_posts_recorded_checks_narration_and_refs(tmp_path, monkeypatch):
    repo, item_sha = _repo_with_item(tmp_path)
    _write_run(repo)
    monkeypatch.setattr(rollout, "DEFAULT_SESSIONS_ROOT", FIXTURE_SESSIONS)

    result = mailing_list.harvest_and_post_qa(repo, CONTRIB_BRANCH)

    assert result == {
        "ok": True,
        "posted": [ITEM_ID],
        "skipped_existing": [],
        "unavailable": [],
    }
    posts = mailing_list.read(repo, kinds={"qa"})
    assert len(posts) == 1
    post = posts[0]
    assert post["kind"] == "qa"
    assert post["subject"] == ITEM_ID
    assert post["refs"] == {
        "contrib": CONTRIB_BRANCH,
        "item": item_sha,
        "run": RUN_ID,
        "session": [SESSION_A, SESSION_B],
    }

    body = post["body"]
    assert body.startswith(
        "Checks:\n"
        "- python -m pytest -q tests/test_mailing_list_harvest.py — "
        "Process exited with code 0 | 2 passed in 0.03s\n"
        "- python -m pytest -q tests/test_slow.py — "
        "Process exited with code 1 | 1 failed, 4 passed in 61.20s\n\n"
        "Recorded-Narration (recorded self-narration, verbatim):\n"
    )
    first = "First recorded line.\n\n# preserved hash line\n  preserved indentation\nUnicode: 記録"
    second = "Second-session first part.\nSecond-session second part."
    assert first in body
    assert "Long recorded message:" in body and "tail-marker" in body
    assert second in body
    assert body.index(first) < body.index(second)
    assert "DECOY USER TEXT" not in body
    assert "DECOY REASONING TEXT" not in body
    assert "sed -n" not in body and "99 passed" not in body
    assert "collecting ..." not in body and "88 passed" not in body
    assert "test_incomplete.py" not in body


def test_harvest_is_idempotent_for_same_item_ref(tmp_path, monkeypatch):
    repo, _item_sha = _repo_with_item(tmp_path)
    _write_run(repo)
    monkeypatch.setattr(rollout, "DEFAULT_SESSIONS_ROOT", FIXTURE_SESSIONS)

    first = mailing_list.harvest_and_post_qa(repo, CONTRIB_BRANCH)
    second = mailing_list.harvest_and_post_qa(repo, CONTRIB_BRANCH)

    assert first["posted"] == [ITEM_ID]
    assert second == {
        "ok": True,
        "posted": [],
        "skipped_existing": [ITEM_ID],
        "unavailable": [],
    }
    assert len(mailing_list.read(repo, kinds={"qa"})) == 1


def test_harvest_cas_race_observes_competing_same_item_post(tmp_path, monkeypatch):
    repo, item_sha = _repo_with_item(tmp_path)
    _write_run(repo)
    monkeypatch.setattr(rollout, "DEFAULT_SESSIONS_ROOT", FIXTURE_SESSIONS)
    mailing_list.ensure_list(repo)
    real_update = mailing_list._update_ref_cas
    injected = False

    def inject_same_item(repo_path: Path, ref: str, commit: str, expected: str) -> bool:
        nonlocal injected
        if ref == mailing_list.LIST_REF and not injected:
            injected = True
            tree = mailing_list.git_wrapper.tree_sha(repo_path, expected)
            assert tree is not None
            winner = mailing_list._commit_tree(
                repo_path,
                tree,
                mailing_list._post_message(
                    "qa",
                    ITEM_ID,
                    "competing subscriber",
                    (("item", item_sha),),
                ),
                parent=expected,
            )
            assert real_update(repo_path, ref, winner, expected) is True
        return real_update(repo_path, ref, commit, expected)

    monkeypatch.setattr(mailing_list, "_update_ref_cas", inject_same_item)

    result = mailing_list.harvest_and_post_qa(repo, CONTRIB_BRANCH)

    assert result == {
        "ok": True,
        "posted": [],
        "skipped_existing": [ITEM_ID],
        "unavailable": [],
    }
    assert len(mailing_list.read(repo, kinds={"qa"})) == 1


def test_harvest_reports_unavailable_without_posting(tmp_path, monkeypatch):
    repo, _item_sha = _repo_with_item(tmp_path)
    _write_run(repo)
    empty_sessions = tmp_path / "empty-sessions"
    empty_sessions.mkdir()
    monkeypatch.setattr(rollout, "DEFAULT_SESSIONS_ROOT", empty_sessions)

    result = mailing_list.harvest_and_post_qa(repo, CONTRIB_BRANCH)

    assert result == {
        "ok": True,
        "posted": [],
        "skipped_existing": [],
        "unavailable": [ITEM_ID],
    }
    assert mailing_list.read(repo, kinds={"qa"}) == []


def _repo_with_item(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "QA Fixture")
    _git(repo, "config", "user.email", "qa@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base", timestamp="2026-07-12T23:59:00+00:00")
    _git(repo, "branch", "-M", "main")
    _git(repo, "switch", "-q", "-c", CONTRIB_BRANCH)
    (repo / "feature.py").write_text("FEATURE = True\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(
        repo,
        "commit",
        "-m",
        f"patch: Demo feature [{ITEM_ID}]",
        timestamp="2026-07-13T00:05:08+00:00",
    )
    item_sha = _git(repo, "rev-parse", "HEAD").strip()
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "patch: implementation result for demo",
        timestamp="2026-07-13T00:05:09+00:00",
    )
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "acceptance: reachable",
        timestamp="2026-07-13T00:05:10+00:00",
    )
    return repo, item_sha


def _write_run(repo: Path) -> None:
    run_dir = repo / ".ai-org" / "log" / "runs" / "2026-07-13" / RUN_ID
    artifacts = run_dir / "artifacts" / "subprocess"
    artifacts.mkdir(parents=True)
    events: list[dict] = []
    calls = [
        ("a", SESSION_A, "2026-07-13T00:00:01Z", "2026-07-13T00:00:02Z"),
        ("b", SESSION_B, "2026-07-13T00:05:01Z", "2026-07-13T00:05:02Z"),
    ]
    for label, session, started_at, completed_at in calls:
        argv = [
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            f"Implement ONLY this item ({ITEM_ID}); fixture attempt {label}.",
        ]
        start_id = f"event-{label}-start"
        events.append(
            _event(
                start_id,
                "subprocess.started",
                started_at,
                {"argv": argv},
                stage="patch_author.code_worker.codex",
            )
        )
        stdout_name = f"{label}-stdout.txt"
        stderr_name = f"{label}-stderr.txt"
        (artifacts / stdout_name).write_text(f"implemented {ITEM_ID}\n", encoding="utf-8")
        (artifacts / stderr_name).write_text(f"session id: {session}\n", encoding="utf-8")
        events.append(
            _event(
                f"event-{label}-done",
                "subprocess.completed",
                completed_at,
                {
                    "argv": argv,
                    "exit_code": 0,
                    "stdout_ref": {"path": f"artifacts/subprocess/{stdout_name}"},
                    "stderr_ref": {"path": f"artifacts/subprocess/{stderr_name}"},
                },
                causation_event_id=start_id,
                stage="patch_author.code_worker.codex",
            )
        )
    events.append(
        _event(
            "event-run-done",
            "patch_author.code_worker.completed",
            "2026-07-13T00:05:09Z",
            {"branch": CONTRIB_BRANCH},
        )
    )
    (run_dir / "supervisor.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )


def _event(
    event_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict,
    *,
    causation_event_id: str = "",
    stage: str = "",
) -> dict:
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "payload": payload,
        "patch_series_id": "demo",
    }
    if causation_event_id:
        event["causation_event_id"] = causation_event_id
    if stage:
        event["stage"] = stage
    return event


def _git(repo: Path, *args: str, timestamp: str | None = None) -> str:
    env = os.environ.copy()
    if timestamp is not None:
        env["GIT_AUTHOR_DATE"] = timestamp
        env["GIT_COMMITTER_DATE"] = timestamp
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return result.stdout
