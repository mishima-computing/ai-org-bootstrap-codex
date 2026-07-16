from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_org import git_wrapper, mailing_list
from ai_org.patch_author import announcements, list_contributor


SERIES_BRANCH = "ai-org/patch-series/list-ring"
NODE_PATH = "sub/codec"
ADDRESS = f"{SERIES_BRANCH}:{NODE_PATH}"


def test_fresh_offer_posts_take_announces_and_advances_reader_cursor(tmp_path):
    origin, author_repo = _origin_and_author_clone(tmp_path)
    offer = _post_offer(origin)

    result = list_contributor.take_open_offers(author_repo)

    assert result == {
        "ok": True,
        "taken": [ADDRESS],
        "announced_refs": result["announced_refs"],
        "skipped": [],
    }
    assert len(result["announced_refs"]) == 1
    announcement_ref = result["announced_refs"][0]
    assert announcement_ref.startswith(announcements.CANONICAL_REF_PREFIX)
    assert git_wrapper.ls_remote(author_repo, "origin", announcement_ref)["refs"] == {
        announcement_ref: git_wrapper.head_sha(author_repo, announcement_ref)
    }

    takes = mailing_list.read(origin, kinds={"take"})
    assert [(take["subject"], take["refs"]) for take in takes] == [
        (
            ADDRESS,
            {
                "confidence": "unscored",
                "family": "ai-org-engine",
                "node": NODE_PATH,
                "series": SERIES_BRANCH,
            },
        )
    ]
    assert mailing_list.cursor(origin, "patch_author") == offer["commit"]

    repeated = list_contributor.take_open_offers(author_repo)

    assert repeated == {
        "ok": True,
        "taken": [],
        "announced_refs": [],
        "skipped": [],
    }
    assert len(mailing_list.read(origin, kinds={"take"})) == 1


def test_competing_family_take_does_not_suppress_patch_author(tmp_path):
    origin, author_repo = _origin_and_author_clone(tmp_path)
    offer = _post_offer(origin)
    competitor = mailing_list.post(
        origin,
        kind="take",
        subject=ADDRESS,
        refs={
            "node": NODE_PATH,
            "series": SERIES_BRANCH,
            "family": "competing-family",
            "confidence": "high",
        },
    )
    assert competitor["ok"] is True

    result = list_contributor.take_open_offers(author_repo)

    assert result["ok"] is True
    assert result["taken"] == [ADDRESS]
    assert mailing_list.cursor(origin, "patch_author") == offer["commit"]
    families = [take["refs"]["family"] for take in mailing_list.read(origin, kinds={"take"})]
    assert families == ["competing-family", "ai-org-engine"]


def test_nonlocal_origin_returns_typed_failure_without_reading_list(tmp_path):
    _origin, author_repo = _origin_and_author_clone(tmp_path)
    _git(author_repo, "remote", "set-url", "origin", "https://example.invalid/ai-org.git")

    result = list_contributor.take_open_offers(author_repo)

    assert result == {
        "ok": False,
        "status": "origin_not_local_path",
        "failure": {
            "type": "origin_not_local_path",
            "detail": "the origin remote must be a local filesystem path",
        },
        "taken": [],
        "announced_refs": [],
        "skipped": [],
    }


def _origin_and_author_clone(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    _run("git", "init", "--bare", str(origin))
    _run("git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _commit(seed, "base")
    _git(seed, "branch", "-M", "main")
    _git(seed, "checkout", "-B", SERIES_BRANCH, "main")
    (seed / "patch-series-cover-letter.json").write_text(
        json.dumps({"working_title": "List ring"}) + "\n",
        encoding="utf-8",
    )
    _git(seed, "add", "patch-series-cover-letter.json")
    _commit(seed, "patch_series: receive List ring")
    _commit(seed, "patch_series: direction-ok", allow_empty=True)
    _git(seed, "checkout", "main")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main", SERIES_BRANCH)

    author_repo = tmp_path / "author"
    _run("git", "clone", str(origin), str(author_repo))
    return origin, author_repo


def _post_offer(origin: Path) -> dict:
    cover = git_wrapper.head_sha(origin, SERIES_BRANCH)
    assert cover is not None
    posted = mailing_list.post(
        origin,
        kind="offer",
        subject=ADDRESS,
        refs={"series": SERIES_BRANCH, "node": NODE_PATH, "cover": cover},
    )
    assert posted["ok"] is True
    return posted


def _commit(repo: Path, subject: str, *, allow_empty: bool = False) -> None:
    args = [
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        subject,
    ]
    if allow_empty:
        args.insert(5, "--allow-empty")
    _git(repo, *args)


def _git(repo: Path, *args: str) -> str:
    return _run("git", "-C", str(repo), *args)


def _run(*args: str) -> str:
    return subprocess.run(
        args,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
