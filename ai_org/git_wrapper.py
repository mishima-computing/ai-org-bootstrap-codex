"""The single git gateway and status window for shared repository state.

Git-derivable status comes from topology here: refs, ancestry, merge bases, and
commit reachability. Git-uncapturable semantic status also lives here uniformly
as git notes, so consumers never invent parallel ledgers or sidecar JSON files.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping


SEMANTIC_NOTE_REF = "ai-org/semantic-status"
SEMANTIC_FIELDS = ("change_kind", "subsystem", "owner", "working_state")


def branches(repo, pattern: str = "*") -> list[str]:
    """Return local branch names matching a git branch --list pattern."""
    result = _git(Path(repo), "branch", "--list", pattern, "--format=%(refname:short)")
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def branch_exists(repo, name: str) -> bool:
    """Return whether a local branch exists."""
    result = _git(Path(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{name}")
    return result.returncode == 0


def current_branch(repo) -> str:
    """Return the current branch name, or an empty string when HEAD is detached."""
    result = _git(Path(repo), "symbolic-ref", "--short", "HEAD")
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def default_branch(repo) -> str:
    """Return the repository's default branch ref using stable local fallbacks."""
    repo_path = Path(repo)
    origin_head = _git(repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if origin_head.returncode == 0:
        ref = origin_head.stdout.strip()
        if ref.startswith("origin/"):
            return ref

    for candidate in ("main", "master"):
        if branch_exists(repo_path, candidate):
            return candidate

    current = current_branch(repo_path)
    if current:
        return current

    raise RuntimeError("could not determine repository default branch")


def log_subjects(repo, ref: str) -> list[str]:
    """Return commit subjects reachable from ref, newest first."""
    result = _git(Path(repo), "log", ref, "--format=%s")
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def has_subject(repo, ref: str, substring: str) -> bool:
    """Return whether any commit subject on ref contains substring."""
    return any(substring in subject for subject in log_subjects(repo, ref))


def is_ancestor(repo, a: str, b: str) -> bool:
    """Return whether commit/ref a is an ancestor of commit/ref b."""
    result = _git(Path(repo), "merge-base", "--is-ancestor", a, b)
    return result.returncode == 0


def merge_base(repo, a: str, b: str) -> str | None:
    """Return the merge base commit for two refs, or None when git cannot find one."""
    result = _git(Path(repo), "merge-base", a, b)
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def head_sha(repo, ref: str) -> str | None:
    """Return the full object id for ref, or None when ref is missing."""
    result = _git(Path(repo), "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def parent_commits(repo, ref: str) -> list[str]:
    """Return the first-parent list for a commit/ref."""
    result = _git(Path(repo), "rev-list", "--parents", "-n", "1", ref)
    if result.returncode != 0:
        return []
    parts = result.stdout.strip().split()
    return parts[1:]


def show_file(repo, ref: str, path: str) -> str | None:
    """Return a file's text from a ref, or None when it is unavailable."""
    result = _git(Path(repo), "show", f"{ref}:{path}")
    if result.returncode != 0:
        return None
    return result.stdout


def file_exists(repo, ref: str, path: str) -> bool:
    """Return whether a path exists at a ref."""
    result = _git(Path(repo), "cat-file", "-e", f"{ref}:{path}")
    return result.returncode == 0


def create_branch_with_files(
    repo,
    branch: str,
    base: str,
    files: Mapping[str, Any],
    *,
    commit_message: str,
    extra_parents: list[str] | None = None,
) -> dict[str, str]:
    """Create or replace a branch at base with the given files committed.

    Additional parents are encoded on the resulting commit, which lets branch
    ancestry represent dependency on multiple prerequisite branches without
    sidecar graph data.
    """
    repo_path = Path(repo)
    original = current_branch(repo_path)
    parents = _dedupe_refs(repo_path, [base, *(extra_parents or [])])
    checkout_base = parents[0] if parents else base
    try:
        _git_required(repo_path, "checkout", "-B", branch, checkout_base)
        add_paths: list[str] = []
        for rel_path, payload in files.items():
            path = repo_path / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, str):
                content = payload
            else:
                content = json.dumps(payload, indent=2) + "\n"
            path.write_text(content, encoding="utf-8")
            add_paths.append(rel_path)
        _git_required(repo_path, "add", *add_paths)
        _git_required(repo_path, "commit", "--allow-empty", "-m", commit_message)
        if len(parents) > 1:
            tree = _git_required(repo_path, "rev-parse", "HEAD^{tree}").stdout.strip()
            args: list[str] = ["commit-tree", tree]
            for parent in parents:
                args.extend(["-p", parent])
            args.extend(["-m", commit_message])
            commit = _git_required(repo_path, *args).stdout.strip()
            _git_required(repo_path, "update-ref", f"refs/heads/{branch}", commit)
        else:
            commit = _git_required(repo_path, "rev-parse", "HEAD").stdout.strip()
        return {"branch": branch, "commit": commit}
    finally:
        if original:
            _git_required(repo_path, "checkout", original)


def commit_empty(repo, branch: str, subject: str, *, body: str = "") -> dict[str, str]:
    """Commit an empty semantic marker on a branch and restore the original checkout."""
    repo_path = Path(repo)
    original = current_branch(repo_path)
    message_args = ["-m", subject]
    if body:
        message_args.extend(["-m", body])
    try:
        _git_required(repo_path, "checkout", branch)
        _git_required(repo_path, "commit", "--allow-empty", *message_args)
        commit = _git_required(repo_path, "rev-parse", "HEAD").stdout.strip()
        return {"branch": branch, "commit": commit}
    finally:
        if original:
            _git_required(repo_path, "checkout", original)


def commit_files(
    repo,
    branch: str,
    files: Mapping[str, Any],
    *,
    subject: str,
    body: str = "",
    allow_empty: bool = False,
) -> dict[str, str]:
    """Commit file updates on a branch and restore the original checkout."""
    repo_path = Path(repo)
    original = current_branch(repo_path)
    message_args = ["-m", subject]
    if body:
        message_args.extend(["-m", body])
    try:
        _git_required(repo_path, "checkout", branch)
        add_paths: list[str] = []
        for rel_path, payload in files.items():
            path = repo_path / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, str):
                content = payload
            else:
                content = json.dumps(payload, indent=2) + "\n"
            path.write_text(content, encoding="utf-8")
            add_paths.append(rel_path)
        _git_required(repo_path, "add", *add_paths)
        commit_args = ["commit"]
        if allow_empty:
            commit_args.append("--allow-empty")
        commit_args.extend(message_args)
        _git_required(repo_path, *commit_args)
        commit = _git_required(repo_path, "rev-parse", "HEAD").stdout.strip()
        return {"branch": branch, "commit": commit}
    finally:
        if original:
            _git_required(repo_path, "checkout", original)


def list_serials(repo) -> list[dict[str, str | int]]:
    """Return the serial tag registry, ordered by serial number."""
    result = _git(Path(repo), "for-each-ref", "refs/tags/ai-org/serial", "--format=%(refname:short) %(objectname)")
    if result.returncode != 0:
        return []
    serials: list[dict[str, str | int]] = []
    for line in result.stdout.splitlines():
        tag, _, commit = line.partition(" ")
        number_text = tag.removeprefix("ai-org/serial/")
        try:
            number = int(number_text)
        except ValueError:
            continue
        serials.append({"tag": tag, "number": number, "commit": commit.strip()})
    return sorted(serials, key=lambda item: int(item["number"]))


def next_serial(repo) -> str:
    """Return the next zero-padded org serial from the tag registry."""
    existing = [int(item["number"]) for item in list_serials(repo)]
    return f"{max(existing, default=0) + 1:04d}"


def serial_for_ref(repo, ref: str) -> str | None:
    """Return an existing org serial reachable from ref, newest serial first."""
    serials = sorted(list_serials(repo), key=lambda item: int(item["number"]), reverse=True)
    for item in serials:
        tag = str(item["tag"])
        if is_ancestor(repo, tag, ref):
            return tag.removeprefix("ai-org/serial/")
    return None


def tag_serial(repo, serial: str, ref: str) -> dict[str, str]:
    """Create the serial tag at ref unless it already exists."""
    repo_path = Path(repo)
    tag = f"ai-org/serial/{serial}"
    existing = _git(repo_path, "rev-parse", "--verify", f"refs/tags/{tag}")
    if existing.returncode == 0:
        return {"tag": tag, "commit": existing.stdout.strip()}
    target = head_sha(repo_path, ref)
    if target is None:
        raise RuntimeError(f"cannot tag missing ref {ref}")
    _git_required(repo_path, "tag", tag, target)
    return {"tag": tag, "commit": target}


def ensure_serial(repo, ref: str) -> str:
    """Return the existing reachable serial or assign the next one to ref."""
    existing = serial_for_ref(repo, ref)
    if existing:
        return existing
    serial = next_serial(repo)
    tag_serial(repo, serial, ref)
    return serial


def read_semantic(repo, branch: str) -> dict[str, str]:
    """Read git-note semantic labels for a branch head."""
    target = head_sha(repo, branch)
    if target is None:
        return {}
    result = _git(Path(repo), "notes", f"--ref={SEMANTIC_NOTE_REF}", "show", target)
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {key: value for key, value in parsed.items() if key in SEMANTIC_FIELDS and isinstance(value, str)}


def write_semantic(repo, branch: str, labels: Mapping[str, str]) -> dict[str, str]:
    """Write git-note semantic labels for a branch head."""
    target = head_sha(repo, branch)
    if target is None:
        raise RuntimeError(f"cannot write semantic labels for missing branch {branch}")
    semantic = {
        key: str(labels[key])
        for key in SEMANTIC_FIELDS
        if key in labels and isinstance(labels[key], str)
    }
    payload = json.dumps(semantic, indent=2, sort_keys=True)
    _git_required(Path(repo), "notes", f"--ref={SEMANTIC_NOTE_REF}", "add", "-f", "-m", payload, target)
    return {"branch": branch, "commit": target}


def dependency_graph(repo, refs: list[str]) -> list[dict[str, str]]:
    """Derive the minimal dependency graph encoded by branch ancestry."""
    existing = [ref for ref in refs if head_sha(repo, ref) is not None]
    edges: list[tuple[str, str]] = []
    for ancestor in existing:
        for descendant in existing:
            if ancestor == descendant:
                continue
            if is_ancestor(repo, ancestor, descendant):
                edges.append((ancestor, descendant))

    reduced: list[tuple[str, str]] = []
    for ancestor, descendant in edges:
        transitive = any(
            middle not in {ancestor, descendant}
            and is_ancestor(repo, ancestor, middle)
            and is_ancestor(repo, middle, descendant)
            for middle in existing
        )
        if not transitive:
            reduced.append((ancestor, descendant))

    return [{"from": source, "to": target} for source, target in sorted(set(reduced))]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(["git", "-C", str(repo), *args], 127, "", str(exc))


def _git_required(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = _git(repo, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result


def _dedupe_refs(repo: Path, refs: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        key = head_sha(repo, ref) or ref
        if key not in seen:
            seen.add(key)
            deduped.append(ref)
    return deduped
