from __future__ import annotations

import subprocess

from ai_org.maintainers import mainline


def test_mainline_accept_merges_subsystem_ref(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    subsystem_ref = _commit_on_branch(repo, "refs/heads/ai-org/subsystem/p1", "p1.txt")

    monkeypatch.setattr(
        mainline.carrier,
        "run_codex",
        lambda *_args, **_kwargs: {"ok": True, "last_message": '{"accept": true, "reasons": []}'},
    )

    result = mainline.review_and_integrate([subsystem_ref], repo)

    assert result == mainline.MAINLINE_REF
    assert _ref_exists(repo, mainline.MAINLINE_REF)
    assert _is_ancestor(repo, subsystem_ref, mainline.MAINLINE_REF)


def test_mainline_rejects_garbled_verdict(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    subsystem_ref = _commit_on_branch(repo, "refs/heads/ai-org/subsystem/p1", "p1.txt")

    monkeypatch.setattr(
        mainline.carrier,
        "run_codex",
        lambda *_args, **_kwargs: {"ok": True, "last_message": "not json"},
    )

    assert mainline.review_and_integrate([subsystem_ref], repo) == "reject"
    assert not _ref_exists(repo, mainline.MAINLINE_REF)


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


def _ref_exists(repo, ref):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
            check=False,
        ).returncode
        == 0
    )


def _is_ancestor(repo, ancestor, descendant):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
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
