from __future__ import annotations

from ai_org import patch
from ai_org.rfc.receive import RFC
from ai_org.rfc.task import Task


def test_make_resumes_contributor_session_after_functional_check_failure(monkeypatch):
    calls = []

    def fake_run(task, *, feedback=None, resume_session=None, branch_ref=None):
        calls.append(
            {
                "feedback": feedback,
                "resume_session": resume_session,
                "branch_ref": branch_ref,
            }
        )
        return {
            "branch": branch_ref or "refs/heads/contrib/task-1",
            "session_id": "sess-1",
            "ok": True,
        }

    failed_verdict = {
        "ok": False,
        "reachable": False,
        "blockers": [{"where": "app.py:12", "why": "missing edge case"}],
        "notes": "Mona found a missing edge case.",
    }
    verdicts = iter(
        [
            failed_verdict,
            {"ok": True, "reachable": True, "blockers": [], "notes": "reachable"},
        ]
    )

    monkeypatch.setattr(patch.implement, "run", fake_run)
    monkeypatch.setattr(patch.functional_check, "check", lambda rfc, branch: next(verdicts))

    rfc = RFC(title="T", problem="P", proposed_change="C")
    task = Task(id="task-1", objective="O")

    assert patch.make(rfc, task) == "refs/heads/contrib/task-1"
    assert calls == [
        {"feedback": None, "resume_session": None, "branch_ref": None},
        {
            "feedback": failed_verdict,
            "resume_session": "sess-1",
            "branch_ref": "refs/heads/contrib/task-1",
        },
    ]
