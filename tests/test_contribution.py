from __future__ import annotations

from ai_org import contribution
from ai_org.rfc.receive import RFC
from ai_org.rfc.task import Task


def test_make_resumes_contributor_session_after_acceptance_failure(monkeypatch):
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

    verdicts = iter(["fail: missing edge case", "ok"])

    monkeypatch.setattr(contribution.implement, "run", fake_run)
    monkeypatch.setattr(contribution.acceptance, "check", lambda rfc, branch: next(verdicts))

    rfc = RFC(title="T", problem="P", proposed_change="C")
    task = Task(id="task-1", objective="O")

    assert contribution.make(rfc, task) == "refs/heads/contrib/task-1"
    assert calls == [
        {"feedback": None, "resume_session": None, "branch_ref": None},
        {
            "feedback": "fail: missing edge case",
            "resume_session": "sess-1",
            "branch_ref": "refs/heads/contrib/task-1",
        },
    ]
