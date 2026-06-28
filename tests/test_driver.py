from __future__ import annotations

from types import SimpleNamespace
import json
import subprocess

from ai_org import driver
from ai_org.platform import state
from ai_org.rfc.receive import RFC
from ai_org.rfc.task import Task


def test_repeated_advance_drives_fresh_repo_to_done_with_committed_state_files(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    rfc = RFC(title="T", problem="P", proposed_change="C")
    rfc_id = driver._rfc_id(rfc)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    calls: list[str] = []

    monkeypatch.setattr(driver.review, "run_rfc_review", lambda _rfc, _repo: _review_result("direction-ok"))
    monkeypatch.setattr(driver.decompose, "decompose", lambda _rfc, _repo: [Task(id="p1", objective="O", base_sha=head)])

    def make(_rfc, task):
        calls.append(f"contribution:{task.id}")
        return _commit_on_branch(repo, f"refs/heads/ai-org/contrib/{task.id}", f"{task.id}.txt")

    def integrate_subsystem(branch, _repo):
        calls.append(f"subsystem:{branch}")
        ref = branch.replace("refs/heads/ai-org/contrib/", "refs/heads/ai-org/subsystem/")
        _git(repo, "update-ref", ref, branch)
        return ref

    def integrate_mainline(refs, _repo):
        calls.append("mainline")
        _git(repo, "update-ref", driver.MAINLINE_REF, refs[-1])
        return driver.MAINLINE_REF

    monkeypatch.setattr(driver.patch, "make", make)
    monkeypatch.setattr(driver.subsystem, "review_and_integrate", integrate_subsystem)
    monkeypatch.setattr(driver.mainline, "review_and_integrate", integrate_mainline)

    records = [driver.advance(rfc, repo) for _ in range(5)]

    assert [record["action"] for record in records] == [
        "review",
        "decompose",
        "contribution",
        "subsystem",
        "mainline",
    ]
    assert records[-1]["terminal"] is True
    assert records[-1]["status"] == "integrated"
    assert _ref_exists(repo, driver.STATE_REF)
    assert _state_json(repo, rfc_id, "rfc.json")["status"] == "done"
    assert _state_json(repo, rfc_id, "verdict.json")["status"] == "direction-ok"
    assert _state_json(repo, rfc_id, "plan.json")["patches"]["p1"]["phase"] == "mainline"
    assert _state_text(repo, rfc_id, "events.ndjson").count("\n") == 5
    assert _state_file_exists(repo, rfc_id, "rfc.json")
    assert _state_file_exists(repo, rfc_id, "verdict.json")
    assert _state_file_exists(repo, rfc_id, "plan.json")
    assert not _custom_ai_org_refs(repo)
    assert _ref_exists(repo, "refs/heads/ai-org/contrib/p1")
    assert _ref_exists(repo, "refs/heads/ai-org/subsystem/p1")
    assert _ref_exists(repo, driver.MAINLINE_REF)
    assert calls == ["contribution:p1", "subsystem:refs/heads/ai-org/contrib/p1", "mainline"]

    report = state.status(repo)
    assert report["state_branch"] == driver.STATE_BRANCH
    assert report["rfcs"][0]["phase"] == "done"
    assert report["rfcs"][0]["review"]["status"] == "direction-ok"
    assert report["rfcs"][0]["patches"] == [
        {
            "id": "p1",
            "phase": "mainline",
            "review_verdict": "direction-ok",
            "contribution_ref": "refs/heads/ai-org/contrib/p1",
            "contribution_status": "present",
            "subsystem_ref": "refs/heads/ai-org/subsystem/p1",
            "subsystem_status": "present",
            "mainline_status": "integrated",
        }
    ]


def test_concurrent_state_update_triggers_cas_retry(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    rfc = RFC(title="T", problem="P", proposed_change="C")
    review_calls = 0
    cas_calls = 0
    original_cas = state.update_ref_cas

    def review_once(_rfc, _repo):
        nonlocal review_calls
        review_calls += 1
        return _review_result("direction-ok")

    def racing_cas(repo_arg, ref, new_oid, expected_old_oid):
        nonlocal cas_calls
        cas_calls += 1
        if cas_calls == 1:
            race_commit = state.build_state_commit(
                repo_arg,
                expected_old_oid,
                {"race.txt": "concurrent update\n"},
                "race state update",
            )
            assert original_cas(repo_arg, ref, race_commit, expected_old_oid)
            return False
        return original_cas(repo_arg, ref, new_oid, expected_old_oid)

    monkeypatch.setattr(driver.review, "run_rfc_review", review_once)
    monkeypatch.setattr(state, "update_ref_cas", racing_cas)

    record = driver.advance(rfc, repo)

    assert record["action"] == "review"
    assert record["status"] == "direction-ok"
    assert review_calls == 1
    assert cas_calls == 2
    assert _state_json(repo, driver._rfc_id(rfc), "verdict.json")["status"] == "direction-ok"
    assert int(_git(repo, "rev-list", "--count", driver.STATE_REF).stdout.strip()) == 2


def test_advance_resumes_from_contribution_branch_without_redoing_patch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    rfc = RFC(title="T", problem="P", proposed_change="C")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(driver.review, "run_rfc_review", lambda _rfc, _repo: _review_result("direction-ok"))
    monkeypatch.setattr(driver.decompose, "decompose", lambda _rfc, _repo: [Task(id="p1", objective="O", base_sha=head)])

    assert driver.advance(rfc, repo)["action"] == "review"
    assert driver.advance(rfc, repo)["action"] == "decompose"
    _commit_on_branch(repo, "refs/heads/ai-org/contrib/p1", "p1.txt")

    def fail_if_redone(_rfc, _task):
        raise AssertionError("contribution.make must not run when the contribution branch already exists")

    def integrate_subsystem(branch, _repo):
        ref = branch.replace("refs/heads/ai-org/contrib/", "refs/heads/ai-org/subsystem/")
        _git(repo, "update-ref", ref, branch)
        return ref

    monkeypatch.setattr(driver.patch, "make", fail_if_redone)
    monkeypatch.setattr(driver.subsystem, "review_and_integrate", integrate_subsystem)

    record = driver.advance(rfc, repo)

    assert record["action"] == "subsystem"
    assert record["status"] == "integrated"
    assert state.status(repo)["rfcs"][0]["patches"][0]["phase"] == "subsystem"


def test_nak_verdict_is_terminal_and_does_not_decompose(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    rfc = RFC(title="T", problem="P", proposed_change="C")
    rfc_id = driver._rfc_id(rfc)

    monkeypatch.setattr(driver.review, "run_rfc_review", lambda _rfc, _repo: _review_result("nak"))

    def fail_decompose(_rfc, _repo):
        raise AssertionError("decompose must not run after a NAK")

    monkeypatch.setattr(driver.decompose, "decompose", fail_decompose)

    first = driver.advance(rfc, repo)
    second = driver.advance(rfc, repo)

    assert first["action"] == "review"
    assert first["terminal"] is True
    assert first["status"] == "nak"
    assert second == {
        "action": "none",
        "status": "rejected",
        "terminal": True,
        "rfc_id": rfc_id,
    }
    assert _state_json(repo, rfc_id, "rfc.json")["status"] == "rejected"
    assert _state_json(repo, rfc_id, "verdict.json")["status"] == "nak"
    assert not _state_file_exists(repo, rfc_id, "plan.json")
    assert state.status(repo)["rfcs"][0]["status"] == "rejected"


def _review_result(status: str):
    return SimpleNamespace(
        status=status,
        rounds=1,
        final_view="",
        resolved=[],
        unresolved=[],
        history=[],
        escalation_reason="",
    )


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _commit_on_branch(repo, ref, filename):
    branch = ref.removeprefix("refs/heads/")
    _git(repo, "checkout", "-B", branch, "main")
    (repo / filename).write_text(f"{filename}\n", encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"add {filename}")
    _git(repo, "checkout", "main")
    return ref


def _state_json(repo, rfc_id, filename):
    return json.loads(_state_text(repo, rfc_id, filename))


def _state_text(repo, rfc_id, filename):
    return _git(repo, "show", f"{driver.STATE_REF}:state/{rfc_id}/{filename}").stdout


def _state_file_exists(repo, rfc_id, filename):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{driver.STATE_REF}:state/{rfc_id}/{filename}"],
            check=False,
        ).returncode
        == 0
    )


def _custom_ai_org_refs(repo):
    refs = _git(repo, "for-each-ref", "--format=%(refname)").stdout.splitlines()
    custom_prefix = "refs/" + "ai-org/"
    return [ref for ref in refs if ref.startswith(custom_prefix)]


def _ref_exists(repo, ref):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
            check=False,
        ).returncode
        == 0
    )


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
