from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess

from ai_org import git_wrapper, mailing_list
import ai_org.patchwork_queue.receive as receive_module


def test_ensure_list_is_idempotent_orphan_empty_genesis(tmp_path):
    repo = _init_repo(tmp_path)
    seed_commit = git_wrapper.head_sha(repo, "main")
    seed_tree = git_wrapper.tree_sha(repo, "main")
    assert seed_commit is not None
    assert seed_tree is not None

    first = mailing_list.ensure_list(repo)

    (repo / "README.md").write_text("base\nafter genesis\n", encoding="utf-8")
    _git(repo, "commit", "-am", "advance default branch")
    second = mailing_list.ensure_list(repo)

    assert first == {"ok": True, "created": True, "head": first["head"]}
    assert second == {"ok": True, "created": False, "head": first["head"]}
    assert git_wrapper.parent_commits(repo, first["head"]) == []
    assert git_wrapper.tree_files(repo, first["head"]) == []
    assert git_wrapper.log_subjects(repo, mailing_list.LIST_BRANCH) == ["list: genesis"]
    assert _git(repo, "show", "-s", "--format=%B", first["head"]).rstrip("\n") == (
        "list: genesis\n\n"
        f"Seed: {seed_commit}\n"
        f"Seed-Tree: {seed_tree}"
    )
    assert mailing_list.genesis_seed(repo) == {"commit": seed_commit, "tree": seed_tree}
    assert mailing_list.read(repo) == []
    assert git_wrapper.merge_base(repo, "main", mailing_list.LIST_BRANCH) is None
    assert git_wrapper.current_branch(repo) == "main"
    assert git_wrapper.commit_author(repo, first["head"]) == git_wrapper.engine_identity()


def test_post_read_round_trip_multiline_body_and_refs(tmp_path):
    repo = _init_repo(tmp_path)
    body = "First paragraph.\nSecond line.\n\nFinal paragraph."
    refs = {"series": "ai-org/patch-series/demo", "revision": "v2"}

    result = mailing_list.post(
        repo,
        kind="review",
        coordinate="series/demo",
        subject="Demo needs revision",
        body=body,
        refs=refs,
    )

    assert result["ok"] is True
    assert result["message_id"] == "review.series%2Fdemo.1@ai-org-list"
    genesis = git_wrapper.parent_commits(repo, result["commit"])[0]
    assert git_wrapper.tree_sha(repo, result["commit"]) == git_wrapper.tree_sha(repo, genesis)
    assert _git(repo, "show", "-s", "--format=%B", result["commit"]).rstrip("\n") == (
        "REVIEW: Demo needs revision\n\n"
        f"{body}\n\n"
        "Kind: review\n"
        "Message-Id: review.series%2Fdemo.1@ai-org-list\n"
        "Ref: revision=v2\n"
        "Ref: series=ai-org/patch-series/demo"
    )
    posts = mailing_list.read(repo)
    assert posts == [
        {
            "commit": result["commit"],
            "kind": "review",
            "subject": "Demo needs revision",
            "body": body,
            "body_text": body,
            "message_id": "review.series%2Fdemo.1@ai-org-list",
            "refs": {"revision": "v2", "series": "ai-org/patch-series/demo"},
            "author_date": posts[0]["author_date"],
        }
    ]
    assert datetime.fromisoformat(posts[0]["author_date"]).tzinfo is not None


def test_post_retries_when_competing_append_wins_first_cas(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    mailing_list.ensure_list(repo)
    real_update = mailing_list._update_ref_cas
    race: dict[str, str | bool] = {"injected": False}

    def inject_winner(repo_path: Path, ref: str, commit: str, expected: str) -> bool:
        if ref == mailing_list.LIST_REF and not race["injected"]:
            race["injected"] = True
            tree = git_wrapper.tree_sha(repo_path, expected)
            assert tree is not None
            winner = mailing_list._commit_tree(
                repo_path,
                tree,
                (
                    "RIVAL: Competing post\n\n\n\n"
                    "Kind: rival\n"
                    "Message-Id: rival.competing.1@ai-org-list\n"
                ),
                parent=expected,
            )
            race["winner"] = winner
            assert real_update(repo_path, ref, winner, expected) is True
        return real_update(repo_path, ref, commit, expected)

    monkeypatch.setattr(mailing_list, "_update_ref_cas", inject_winner)

    result = mailing_list.post(repo, kind="notice", coordinate="requested", subject="Requested post")

    assert result["ok"] is True
    assert result["message_id"] == "notice.requested.2@ai-org-list"
    assert git_wrapper.parent_commits(repo, result["commit"]) == [race["winner"]]
    assert [(post["kind"], post["subject"]) for post in mailing_list.read(repo)] == [
        ("rival", "Competing post"),
        ("notice", "Requested post"),
    ]


def test_message_id_is_deterministic_across_rerooted_archives(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    rerooted = tmp_path / "rerooted"
    _git(tmp_path, "clone", "--quiet", "--no-hardlinks", str(repo), str(rerooted))

    monkeypatch.setenv("GIT_AUTHOR_DATE", "2001-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2001-01-01T00:00:00+00:00")
    original = mailing_list.post(
        repo,
        kind="offer",
        coordinate="series/demo v2",
        subject="Original archive",
    )

    monkeypatch.setenv("GIT_AUTHOR_DATE", "2002-02-02T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2002-02-02T00:00:00+00:00")
    rehosted = mailing_list.post(
        rerooted,
        kind="offer",
        coordinate="series/demo v2",
        subject="Original archive",
    )

    expected = "offer.series%2Fdemo%20v2.1@ai-org-list"
    assert original["message_id"] == expected
    assert rehosted["message_id"] == expected
    assert original["commit"] != rehosted["commit"]
    assert git_wrapper.parent_commits(repo, original["commit"]) != git_wrapper.parent_commits(
        rerooted, rehosted["commit"]
    )
    assert mailing_list.read(repo)[0]["message_id"] == expected
    assert mailing_list.read(rerooted)[0]["message_id"] == expected


def test_body_correspondence_and_threading_are_words_and_links_only(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\nartifact change\n", encoding="utf-8")
    _git(repo, "commit", "-am", "build artifact")
    artifact = git_wrapper.head_sha(repo, "main")
    assert artifact is not None
    words = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " base\n"
        "+artifact change"
    )
    correspondence = {
        "commit": artifact,
        "series": "main",
        "node": "README.md",
    }

    offer = mailing_list.post(
        repo,
        kind="offer",
        coordinate="series/demo v2",
        subject="Artifact offer",
        body_text=words,
        corresponds_to=correspondence,
        refs={"revision": "v2"},
    )
    reply = mailing_list.post(
        repo,
        kind="review",
        coordinate="series/demo v2/review",
        subject="Review objection",
        body_text="Please add a regression test.",
        in_reply_to=offer["message_id"],
    )

    assert offer["message_id"] == "offer.series%2Fdemo%20v2.1@ai-org-list"
    assert reply["message_id"] == "review.series%2Fdemo%20v2%2Freview.2@ai-org-list"
    assert _git(repo, "show", "-s", "--format=%B", offer["commit"]).rstrip("\n") == (
        "OFFER: Artifact offer\n\n"
        f"{words}\n\n"
        "Kind: offer\n"
        "Message-Id: offer.series%2Fdemo%20v2.1@ai-org-list\n"
        f"Link: commit={artifact}\n"
        "Link: node=README.md\n"
        "Link: series=main\n"
        "Ref: revision=v2"
    )
    assert "In-Reply-To: offer.series%2Fdemo%20v2.1@ai-org-list" in _git(
        repo, "show", "-s", "--format=%B", reply["commit"]
    )

    posts = mailing_list.read(repo)
    assert posts[0]["body"] == words
    assert posts[0]["body_text"] == words
    assert posts[0]["corresponds_to"] == correspondence
    assert posts[0]["refs"] == {"revision": "v2"}
    assert "in_reply_to" not in posts[0]
    assert posts[1]["body"] == "Please add a regression test."
    assert posts[1]["body_text"] == "Please add a regression test."
    assert posts[1]["in_reply_to"] == offer["message_id"]
    assert "corresponds_to" not in posts[1]

    genesis = git_wrapper.parent_commits(repo, offer["commit"])[0]
    assert git_wrapper.parent_commits(repo, offer["commit"]) == [genesis]
    assert git_wrapper.parent_commits(repo, reply["commit"]) == [offer["commit"]]
    assert git_wrapper.tree_sha(repo, offer["commit"]) == git_wrapper.tree_sha(repo, genesis)
    assert git_wrapper.tree_sha(repo, reply["commit"]) == git_wrapper.tree_sha(repo, genesis)
    assert git_wrapper.tree_files(repo, offer["commit"]) == []
    assert git_wrapper.tree_files(repo, reply["commit"]) == []
    assert git_wrapper.is_ancestor(repo, artifact, offer["commit"]) is False
    assert git_wrapper.is_ancestor(repo, artifact, reply["commit"]) is False


def test_format_patch_text_is_deterministic_and_git_am_applyable(tmp_path):
    repo = _init_repo(tmp_path)
    base = git_wrapper.head_sha(repo, "main")
    assert base is not None

    (repo / "README.md").write_text("base\nfirst change\n", encoding="utf-8")
    _git(repo, "commit", "-am", "first change")
    (repo / "notes.txt").write_text("second change\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-m", "second change")
    tip = git_wrapper.head_sha(repo, "main")
    assert tip is not None

    first = mailing_list.format_patch_text(repo, base, tip)

    _git(repo, "config", "format.from", "true")
    _git(repo, "config", "format.notes", "true")
    _git(repo, "config", "diff.context", "0")
    _git(repo, "config", "diff.algorithm", "histogram")
    _git(repo, "config", "diff.indentHeuristic", "true")
    _git(repo, "config", "diff.mnemonicPrefix", "true")
    _git(repo, "config", "diff.noprefix", "true")
    _git(repo, "notes", "add", "-m", "ambient note must not enter the patch", tip)
    refs_before = _git(repo, "show-ref")
    status_before = _git(repo, "status", "--porcelain")
    second = mailing_list.format_patch_text(repo, base, tip)

    assert first == second
    assert "ambient note must not enter the patch" not in second
    assert "Subject: [PATCH" in first
    assert "diff --git a/README.md b/README.md" in first
    assert "diff --git a/notes.txt b/notes.txt" in first
    assert _git(repo, "show-ref") == refs_before
    assert _git(repo, "status", "--porcelain") == status_before

    applied = tmp_path / "applied"
    _git(tmp_path, "clone", "--quiet", "--no-hardlinks", str(repo), str(applied))
    _git(applied, "checkout", "--quiet", "--detach", base)
    _git(applied, "config", "user.name", "Patch Applier")
    _git(applied, "config", "user.email", "patch-applier@example.invalid")
    subprocess.run(
        ["git", "-C", str(applied), "am", "--quiet"],
        input=first,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert git_wrapper.tree_sha(applied, "HEAD") == git_wrapper.tree_sha(repo, tip)


def test_cursor_is_per_reader_and_moves_only_forward(tmp_path):
    repo = _init_repo(tmp_path)
    first = mailing_list.post(repo, kind="notice", subject="First")["commit"]
    second = mailing_list.post(repo, kind="notice", subject="Second")["commit"]

    assert mailing_list.cursor(repo, "alice") is None
    assert mailing_list.cursor(repo, "bob") is None
    assert mailing_list.advance_cursor(repo, "alice", first)["ok"] is True
    assert mailing_list.cursor(repo, "alice") == first
    assert git_wrapper.head_sha(repo, "refs/ai-org/list-cursors/alice") == first
    assert mailing_list.cursor(repo, "bob") is None
    assert mailing_list.advance_cursor(repo, "alice", second)["ok"] is True
    assert mailing_list.cursor(repo, "alice") == second
    assert mailing_list.advance_cursor(repo, "alice", first)["ok"] is False
    assert mailing_list.cursor(repo, "alice") == second


def test_read_since_is_exclusive_and_kinds_filter_preserves_order(tmp_path):
    repo = _init_repo(tmp_path)
    first = mailing_list.post(repo, kind="notice", subject="First")["commit"]
    mailing_list.post(repo, kind="review", subject="Second")
    third = mailing_list.post(repo, kind="notice", subject="Third")["commit"]

    assert [post["subject"] for post in mailing_list.read(repo, kinds={"notice"})] == ["First", "Third"]
    assert [post["subject"] for post in mailing_list.read(repo, since=first)] == ["Second", "Third"]
    assert mailing_list.read(repo, since=third) == []


def test_receive_intake_creates_list_before_processing(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    expected = {"ok": True, "status": "stubbed"}

    def assert_list_exists(*args, **kwargs):
        assert git_wrapper.head_sha(repo, mailing_list.LIST_BRANCH) is not None
        return expected

    monkeypatch.setattr(receive_module, "produce_patch_series", assert_list_exists)

    result = receive_module.intake({"raw_request": "Build the requested change."}, repo)

    assert result is expected
    assert git_wrapper.head_sha(repo, mailing_list.LIST_BRANCH) is not None


def test_receive_continues_and_warns_when_list_genesis_fails(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    expected = {"ok": True, "status": "stubbed"}
    warnings: list[tuple[str, dict, str]] = []
    monkeypatch.setattr(
        receive_module.mailing_list,
        "ensure_list",
        lambda repo: {"ok": False, "created": False, "head": "", "error": "injected failure"},
    )
    monkeypatch.setattr(receive_module, "produce_patch_series", lambda *args, **kwargs: expected)
    monkeypatch.setattr(
        receive_module.org_log,
        "debug_emit",
        lambda event_type, payload, *, ctx, severity="debug": warnings.append((event_type, payload, severity)),
    )

    result = receive_module.intake({"raw_request": "Build the requested change."}, repo)

    assert result is expected
    assert warnings == [("mailing_list.genesis.failed", {"error": "injected failure"}, "warning")]


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Mailing List Test")
    _git(repo, "config", "user.email", "mailing-list@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout
