"""The single git gateway and status window for shared repository state.

Git-derivable status comes from topology here: refs, ancestry, merge bases, and
commit reachability. Git-uncapturable semantic status also lives here uniformly
as git notes, so consumers never invent parallel ledgers or sidecar JSON files.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
from typing import Any, Mapping


SEMANTIC_NOTE_REF = "ai-org/semantic-status"
SEMANTIC_NOTE_FULL_REF = f"refs/notes/{SEMANTIC_NOTE_REF}"
SEMANTIC_CONTEXT_ID = "semantic-status-note-v1"
SEMANTIC_API_VERSION = "ai-org-cue-body-v1"
SEMANTIC_KIND = "SemanticStatus"
SEMANTIC_VARIANT = "git-note"

# --- Engine git identity -----------------------------------------------------
# Memento: engine commits NEVER rely on ambient git config. Two proven failure
# modes forbid it: (1) CI runners carry no user.name/user.email at all, so any
# ambient-identity commit dies with "Author identity unknown" (this kept the
# suite red from 63913ad via the transplant/fresh-clone test); (2) developer
# machines DO carry an identity — a real email — and inheriting it writes a
# crawlable address into engine-authored history (real emails are forbidden;
# noreply identities only). Every commit-creating git call in the engine
# therefore passes -c user.name/-c user.email explicitly via
# identity_config_args(). Override with AI_ORG_GIT_IDENTITY_NAME /
# AI_ORG_GIT_IDENTITY_EMAIL. Patch-author commits pass the author's noreply
# identity from the committed authoring announcement instead of the engine one.
ENGINE_IDENTITY_NAME_ENV = "AI_ORG_GIT_IDENTITY_NAME"
ENGINE_IDENTITY_EMAIL_ENV = "AI_ORG_GIT_IDENTITY_EMAIL"
DEFAULT_ENGINE_IDENTITY_NAME = "AI Org Engine"
DEFAULT_ENGINE_IDENTITY_EMAIL = "ai-org-engine@users.noreply.github.com"

# Compatibility observation for callers reporting the completed cutover.
# Publication authority still comes from the prepared, codec-admitted Git
# transaction; changing this process-local value cannot widen or narrow it.
PRODUCTION_V2_NETWORK_PUBLICATION_ENABLED = True


def engine_identity() -> dict[str, str]:
    """Return the explicit engine commit identity (env-overridable, noreply-style)."""
    return {
        "name": os.environ.get(ENGINE_IDENTITY_NAME_ENV) or DEFAULT_ENGINE_IDENTITY_NAME,
        "email": os.environ.get(ENGINE_IDENTITY_EMAIL_ENV) or DEFAULT_ENGINE_IDENTITY_EMAIL,
    }


def identity_config_args(identity: Mapping[str, str] | None = None) -> list[str]:
    """Return -c user.name/-c user.email arguments for a commit-creating git call.

    identity overrides the engine identity per field; a patch author's identity
    from a committed authoring announcement is passed here for authored commits.
    """
    resolved = engine_identity()
    if identity:
        name = str(identity.get("name") or "").strip()
        email = str(identity.get("email") or "").strip()
        if name:
            resolved["name"] = name
        if email:
            resolved["email"] = email
    return ["-c", f"user.name={resolved['name']}", "-c", f"user.email={resolved['email']}"]
SEMANTIC_FIELDS = ("change_kind", "subsystem", "owner", "working_state")
DEFAULT_HISTORY_COMMITS = 30
DEFAULT_HISTORY_CHARS = 15_000
DEFAULT_HISTORY_BODY_CHARS = 1_500
DEFAULT_CONSTITUTION_CHARS = 20_000
DEFAULT_CONSTITUTION_MODULE_CHARS = 5_500
DEFAULT_CONSTITUTION_DOCS_CHARS = 2_000
CORE_CONSTITUTION_PATHS = (
    "ai_org/patchwork_queue/receive.py",
    "ai_org/patchwork_queue/patch_series_gate.py",
    "ai_org/log.py",
    "ai_org/engineering_precedent_store.py",
    "ai_org/patchwork_queue/codex_exec.py",
    "ai_org/git_wrapper.py",
)
CONSTITUTION_LABEL = "org constitution (projected from module Memento headers; the headers are the source of truth)"


@dataclass(frozen=True)
class ExactGitBlob:
    """One raw blob selected from an immutable Git object graph."""

    oid: str
    sha256: str
    byte_length: int
    path: str
    data: bytes


@dataclass(frozen=True)
class FrozenGitBodySnapshot:
    """The complete immutable coordinate used for one semantic-note read."""

    branch: str
    target_oid: str
    notes_ref: str
    notes_ref_oid: str
    blob: ExactGitBlob | None


@dataclass(frozen=True)
class SemanticReadResult:
    """A validated semantic projection plus the Git evidence that selected it."""

    status: str
    snapshot: FrozenGitBodySnapshot | None
    body: Mapping[str, str] | None = None
    failure: Any | None = None

    @property
    def ok(self) -> bool:
        return self.status == "validated"


@dataclass(frozen=True)
class GitBodyFailure(Exception):
    """Stable data-only Git boundary failure; it never carries body bytes."""

    code: str
    location: str
    rule: str

    def __str__(self) -> str:
        return f"{self.code} location={self.location!r} rule={self.rule!r}"


@dataclass(frozen=True)
class SemanticPublicationResult:
    """Outcome of one preflighted compare-and-swap note publication."""

    status: str
    branch: str
    target_oid: str
    notes_ref_before: str
    notes_ref_after: str = ""
    blob_oid: str = ""
    sha256: str = ""
    byte_length: int = 0
    failure: Any | None = None

    @property
    def ok(self) -> bool:
        return self.status == "applied"


@dataclass(frozen=True)
class PreparedSemanticNote:
    """A fully codec-admitted note awaiting only Git CAS publication."""

    branch: str
    target_oid: str
    expected_notes_ref_oid: str
    codec_bodies: Any

    @property
    def canonical(self) -> bytes:
        return bytes(self.codec_bodies.canonical.data)


REQUEST_PROVENANCE_CONTEXT_ID = "request-provenance-custom-ref-v1"
REQUEST_OUTCOME_CONTEXT_ID = "request-outcome-custom-ref-v1"
REQUEST_PROVENANCE_CONTRACT = {
    "apiVersion": SEMANTIC_API_VERSION,
    "kind": "RequestProvenanceRecord",
    "variant": "request-outcome-custom-ref",
}
REQUEST_OUTCOME_CONTRACT = {
    "apiVersion": SEMANTIC_API_VERSION,
    "kind": "RequestOutcome",
    "variant": "request-outcome-custom-ref",
}
REQUEST_PROVENANCE_PATH = "request-provenance.cue"
REQUEST_OUTCOME_PATH = "request-outcome.cue"
REQUEST_PROVENANCE_ALIASES = ("patch-series-request-provenance.json",)
REQUEST_OUTCOME_ALIASES = ("patch-series-request-outcome.json",)
GIT_OBJECT_ID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


@dataclass(frozen=True)
class PreparedRequestBodySet:
    """Two codec-admitted terminal bodies bound to one immutable commit."""

    ref: str
    expected_ref_oid: str
    parent_oid: str
    commit_oid: str
    tree_oid: str
    provenance: bytes
    outcome: bytes
    _codec: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class PreparedNetworkBodySet:
    """One codec-admitted network tree awaiting create-or-CAS publication."""

    ref: str
    expected_ref_oid: str
    parent_oid: str
    commit_oid: str
    tree_oid: str
    publication: Any
    commit_subject: str = "patch series: publish canonical network tree"
    companion_files: tuple[tuple[str, bytes], ...] = ()
    release_enabled: bool = True
    release_rule: str = ""


@dataclass(frozen=True)
class GitPublicationResult:
    """Typed outcome for an authoritative local or remote ref publication."""

    status: str
    ref: str
    expected_oid: str
    commit_oid: str = ""
    failure: GitBodyFailure | None = None
    atomic: bool = False
    ref_outcomes: tuple[tuple[str, str], ...] = ()
    expected_ref_oids: tuple[tuple[str, str], ...] = ()
    resulting_ref_oids: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {"created", "updated", "migrated", "deleted"}


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


def commit_subject(repo, ref: str) -> str:
    """Return the exact subject of one commit-ish, or an empty string."""

    result = _git(Path(repo), "show", "-s", "--format=%s", ref)
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def commit_message(repo, ref: str) -> str:
    """Return the exact subject and body of one commit-ish, or empty text."""

    result = _git(Path(repo), "show", "-s", "--format=%B", ref)
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def first_parent_commits_between(repo, base_exclusive: str, ref: str) -> list[str]:
    """Return first-parent commits oldest-first for ``base_exclusive..ref``."""

    result = _git(
        Path(repo),
        "rev-list",
        "--reverse",
        "--first-parent",
        f"{base_exclusive}..{ref}",
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def recent_commit_history(
    repo,
    ref: str = "HEAD",
    *,
    max_commits: int = DEFAULT_HISTORY_COMMITS,
    max_chars: int = DEFAULT_HISTORY_CHARS,
    max_body_chars: int = DEFAULT_HISTORY_BODY_CHARS,
) -> dict[str, Any]:
    """Return bounded recent commit subjects and bodies, newest first."""
    if max_commits <= 0 or max_chars <= 0:
        return {"commits": [], "truncated": True, "truncation_reason": "history bounds were empty"}
    result = _git(Path(repo), "log", ref, f"-n{max_commits}", "--format=%H%x1f%s%x1f%b%x1e")
    if result.returncode != 0:
        return {"commits": [], "truncated": False, "error": result.stderr.strip() or result.stdout.strip()}

    commits: list[dict[str, Any]] = []
    used_chars = 0
    overall_truncated = False
    for record in result.stdout.strip("\x1e\n").split("\x1e"):
        if not record.strip():
            continue
        parts = record.strip("\n").split("\x1f", 2)
        if len(parts) != 3:
            continue
        commit, subject, body = parts
        body_text, body_truncated = _truncate_at_sentence(str(body).strip(), max_body_chars)
        item = {
            "commit": commit.strip(),
            "subject": subject.strip(),
            "body": body_text,
            "body_truncated": body_truncated,
        }
        projected = _history_item_size(item)
        if commits and used_chars + projected > max_chars:
            overall_truncated = True
            break
        commits.append(item)
        used_chars += projected
        if used_chars >= max_chars:
            overall_truncated = True
            break

    return {"commits": commits, "truncated": overall_truncated, "max_commits": max_commits, "max_chars": max_chars}


def repository_constitution_context(
    repo,
    *,
    max_chars: int = DEFAULT_CONSTITUTION_CHARS,
    per_module_chars: int = DEFAULT_CONSTITUTION_MODULE_CHARS,
    docs_chars: int = DEFAULT_CONSTITUTION_DOCS_CHARS,
) -> dict[str, Any]:
    """Return a bounded projection of top-of-file repository constitution text."""
    repo_path = Path(repo).resolve()
    projection, truncated = projected_repository_constitution(
        repo_path,
        max_chars=max_chars,
        per_module_chars=per_module_chars,
        docs_chars=docs_chars,
    )
    return {
        "label": CONSTITUTION_LABEL,
        "projection": projection,
        "bounds": {
            "max_chars": max_chars,
            "per_module_chars": per_module_chars,
            "docs_chars": docs_chars,
            "truncated": truncated,
        },
        "provenance_discipline": (
            "This is a deterministic projection only. The source of truth remains the repository files; "
            "use it as current project constitution and constraints, not as requester intent."
        ),
    }


def projected_repository_constitution(
    repo,
    *,
    max_chars: int = DEFAULT_CONSTITUTION_CHARS,
    per_module_chars: int = DEFAULT_CONSTITUTION_MODULE_CHARS,
    docs_chars: int = DEFAULT_CONSTITUTION_DOCS_CHARS,
) -> tuple[str, bool]:
    """Collect top-of-file Memento/docstring headers and docs patch series title/status lines."""
    repo_path = Path(repo).resolve()
    sections: list[str] = []
    section_truncated = False
    for rel_path in _constitution_module_paths(repo_path):
        section = _module_constitution_section(repo_path, rel_path, per_module_chars)
        if section:
            section_truncated = section_truncated or "[truncated:" in section
            sections.append(section)

    docs_section = _docs_patch_series_constitution_section(repo_path, docs_chars)
    if docs_section:
        section_truncated = section_truncated or "[truncated:" in docs_section
        sections.append(docs_section)

    text = "\n\n".join(sections)
    bounded, truncated = _truncate_at_paragraph(
        text,
        max_chars,
        f"{CONSTITUTION_LABEL} exceeded {max_chars} characters",
    )
    return bounded, truncated or section_truncated


def has_subject(repo, ref: str, substring: str) -> bool:
    """Return whether any commit subject on ref contains substring."""
    return any(substring in subject for subject in log_subjects(repo, ref))


def has_subject_between(repo, base_exclusive: str, ref: str, substring: str) -> bool:
    """Return whether any commit subject in base_exclusive..ref contains substring."""
    result = _git(Path(repo), "log", f"{base_exclusive}..{ref}", "--format=%s")
    if result.returncode != 0:
        return False
    return any(substring in subject for subject in result.stdout.splitlines())


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


def fork_point(repo, ref: str, branch: str) -> str | None:
    """Return Git's fork point for branch relative to ref, or None when unavailable."""
    result = _git(Path(repo), "merge-base", "--fork-point", ref, branch)
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def branch_creation_point(repo, branch: str) -> str | None:
    """Return the oldest local reflog commit for branch, or None without a reflog."""
    result = _git(Path(repo), "reflog", "--format=%H", branch)
    if result.returncode != 0:
        return None
    entries = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return entries[-1] if entries else None


def changed_paths(repo, base_ref: str, candidate_ref: str) -> list[dict[str, Any]]:
    """Return machine-parsed path changes between two tree-ish refs."""
    result = _git(
        Path(repo),
        "diff-tree",
        "--no-commit-id",
        "-r",
        "--find-renames",
        "--find-copies-harder",
        "--name-status",
        "-z",
        base_ref,
        candidate_ref,
    )
    if result.returncode != 0:
        return [{"status": "!", "paths": [f"<git-error:{result.stderr.strip() or result.stdout.strip()}>"]}]
    parts = result.stdout.split("\0")
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        index += 1
        if not status:
            continue
        paths_needed = 2 if status[:1] in {"R", "C"} else 1
        paths = [part for part in parts[index : index + paths_needed] if part]
        index += paths_needed
        if len(paths) != paths_needed:
            records.append({"status": "!", "paths": paths or ["<parse-error>"]})
            continue
        records.append({"status": status, "paths": paths})
    return records


def head_sha(repo, ref: str) -> str | None:
    """Return the full object id for ref, or None when ref is missing."""
    result = _git(Path(repo), "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def tree_sha(repo, ref: str) -> str | None:
    """Return the immutable root-tree object id for a commit/ref, or None."""
    result = _git(Path(repo), "rev-parse", "--verify", f"{ref}^{{tree}}")
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def path_last_commit(repo, ref: str, path: str) -> str | None:
    """Return the newest commit at ref that changed path, or None."""
    result = _git(Path(repo), "log", "-n", "1", "--format=%H", ref, "--", path)
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


def show_file_bytes(repo, ref: str, path: str) -> bytes | None:
    """Return exact blob bytes from an immutable tree coordinate."""

    result = _git_bytes(Path(repo), "show", f"{ref}:{path}")
    return result.stdout if result.returncode == 0 else None


def tree_files(repo, ref: str, pathspec: str = "") -> list[str]:
    """Return file paths present in a ref, optionally under a pathspec."""
    args = ["ls-tree", "-r", "--name-only", ref]
    if pathspec:
        args.extend(["--", pathspec])
    result = _git(Path(repo), *args)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def read_tree_file(repo, ref: str, path: str) -> str | None:
    """Read a file from a ref; alias kept distinct for tree-walk callers."""
    return show_file(repo, ref, path)


def file_exists(repo, ref: str, path: str) -> bool:
    """Return whether a path exists at a ref."""
    result = _git(Path(repo), "cat-file", "-e", f"{ref}:{path}")
    return result.returncode == 0


def tree_blob_oid(repo, ref: str, path: str) -> str | None:
    """Return the blob OID at one immutable tree coordinate."""

    result = _git(Path(repo), "rev-parse", "--verify", f"{ref}:{path}")
    if result.returncode != 0:
        return None
    oid = result.stdout.strip()
    return oid or None


def create_branch_with_files(
    repo,
    branch: str,
    base: str,
    files: Mapping[str, Any],
    *,
    commit_message: str,
    extra_parents: list[str] | None = None,
    deletions: list[str] | None = None,
    identity: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Create or replace a branch at base with the given files committed.

    Additional parents are encoded on the resulting commit, which lets branch
    ancestry represent dependency on multiple prerequisite branches without
    sidecar graph data. Paths in deletions are removed from the committed tree
    in the same commit, which prevents inherited branch-local files from
    appearing on the new branch without adding tombstone content.
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
        if add_paths:
            _git_required(repo_path, "add", *add_paths)
        delete_paths = list(deletions or [])
        if delete_paths:
            _git_required(repo_path, "rm", "-q", "--ignore-unmatch", "--", *delete_paths)
        _git_required(repo_path, *identity_config_args(identity), "commit", "--allow-empty", "-m", commit_message)
        if len(parents) > 1:
            tree = _git_required(repo_path, "rev-parse", "HEAD^{tree}").stdout.strip()
            args: list[str] = [*identity_config_args(identity), "commit-tree", tree]
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


def commit_empty(repo, branch: str, subject: str, *, body: str = "", identity: Mapping[str, str] | None = None) -> dict[str, str]:
    """Commit an empty semantic marker on a branch and restore the original checkout."""
    repo_path = Path(repo)
    original = current_branch(repo_path)
    message_args = ["-m", subject]
    if body:
        message_args.extend(["-m", body])
    try:
        _git_required(repo_path, "checkout", branch)
        _git_required(repo_path, *identity_config_args(identity), "commit", "--allow-empty", *message_args)
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
    identity: Mapping[str, str] | None = None,
    remove_paths: tuple[str, ...] = (),
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
        if add_paths:
            _git_required(repo_path, "add", *add_paths)
        if remove_paths:
            _git_required(
                repo_path, "rm", "--ignore-unmatch", "--", *remove_paths
            )
        commit_args = [*identity_config_args(identity), "commit"]
        if allow_empty:
            commit_args.append("--allow-empty")
        commit_args.extend(message_args)
        _git_required(repo_path, *commit_args)
        commit = _git_required(repo_path, "rev-parse", "HEAD").stdout.strip()
        return {"branch": branch, "commit": commit}
    finally:
        if original:
            _git_required(repo_path, "checkout", original)


def create_ref_with_files(
    repo,
    ref: str,
    files: Mapping[str, Any],
    *,
    subject: str,
    body: str = "",
    parent: str | None = None,
    extra_parents: list[str] | None = None,
    identity: Mapping[str, str] | None = None,
    update_ref: bool = True,
    inherit_parent_tree: bool = False,
) -> dict[str, str]:
    """Build an isolated commit and optionally create or replace its local ref.

    extra_parents encode lineage on custom refs (a replacing commit may keep
    the old tip as a parent so git history remains the amendment record — no
    side ledger). ``update_ref=False`` is the prepare-before-publication path:
    the commit object exists, but no local coordinate releases it.
    ``inherit_parent_tree=True`` overlays the supplied files on the parent's
    tree so a promise-only contribution commit remains a usable code base.
    """
    repo_path = Path(repo)
    with tempfile.TemporaryDirectory(prefix="ai-org-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if inherit_parent_tree:
            if not parent:
                raise RuntimeError("inherit_parent_tree requires a parent")
            _git_required_env(repo_path, env, "read-tree", parent)
        else:
            _git_required_env(repo_path, env, "read-tree", "--empty")
        for rel_path, payload in files.items():
            if isinstance(payload, str):
                content = payload
            else:
                content = json.dumps(payload, indent=2) + "\n"
            blob = _git_required_env(
                repo_path,
                env,
                "hash-object",
                "-w",
                "--stdin",
                input_text=content,
            ).stdout.strip()
            _git_required_env(repo_path, env, "update-index", "--add", "--cacheinfo", "100644", blob, rel_path)
        tree = _git_required_env(repo_path, env, "write-tree").stdout.strip()
    args = [*identity_config_args(identity), "commit-tree", tree]
    if parent:
        args.extend(["-p", parent])
    for extra_parent in extra_parents or []:
        args.extend(["-p", extra_parent])
    args.extend(["-m", subject])
    if body:
        args.extend(["-m", body])
    commit = _git_required(repo_path, *args).stdout.strip()
    if update_ref:
        _git_required(repo_path, "update-ref", ref, commit)
    return {"ref": ref, "commit": commit}


def read_request_provenance(repo, ref: str, *, client=None) -> Mapping[str, Any]:
    """Strictly import provenance only as a member of its validated pair."""

    provenance, _outcome = read_terminal_request(repo, ref, client=client)
    return provenance


def read_request_outcome(repo, ref: str, *, client=None) -> Mapping[str, Any]:
    """Strictly import an outcome only as a member of its validated pair."""

    _provenance, outcome = read_terminal_request(repo, ref, client=client)
    return outcome


def read_terminal_request(
    repo, ref: str, *, client=None
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Strictly import a paired provenance/outcome from one frozen ref tip."""

    repo_path = Path(repo)
    _validate_terminal_ref(repo_path, ref)
    frozen = _ref_oid(repo_path, ref)
    if not frozen:
        raise GitBodyFailure("GIT_REF", ref, "ref-missing")
    return _read_terminal_request_at_oid(repo_path, ref, frozen, client)


def _read_terminal_request_at_oid(
    repo: Path, ref: str, frozen: str, client
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Import a terminal pair from an already frozen custom-ref commit."""

    provenance_path, provenance = _read_terminal_body_entry_at_oid(
        repo, ref, frozen, REQUEST_PROVENANCE_PATH, REQUEST_PROVENANCE_ALIASES,
        REQUEST_PROVENANCE_CONTEXT_ID, REQUEST_PROVENANCE_CONTRACT, client,
    )
    outcome_path, outcome = _read_terminal_body_entry_at_oid(
        repo, ref, frozen, REQUEST_OUTCOME_PATH, REQUEST_OUTCOME_ALIASES,
        REQUEST_OUTCOME_CONTEXT_ID, REQUEST_OUTCOME_CONTRACT, client,
    )
    if (provenance_path == REQUEST_PROVENANCE_PATH) != (
        outcome_path == REQUEST_OUTCOME_PATH
    ):
        raise GitBodyFailure("PAIR_MISMATCH", ref, "paired-representation")
    provenance_id = provenance.get("request_id")
    outcome_id = outcome.get("request_id")
    if not isinstance(provenance_id, str) or not provenance_id or provenance_id != outcome_id:
        raise GitBodyFailure("PAIR_MISMATCH", "/request_id", "paired-request-id")
    return provenance, outcome


def prepare_terminal_request(
    repo,
    ref: str,
    provenance: Mapping[str, Any],
    outcome: Mapping[str, Any],
    *,
    parent: str,
    expected_ref_oid: str | None = None,
    identity: Mapping[str, str] | None = None,
    client=None,
) -> PreparedRequestBodySet:
    """Validate the pair, emit both bodies, then build an alias-free tree."""

    repo_path = Path(repo)
    _validate_terminal_ref(repo_path, ref)
    provenance_id = provenance.get("request_id")
    outcome_id = outcome.get("request_id")
    if not isinstance(provenance_id, str) or not provenance_id or provenance_id != outcome_id:
        raise GitBodyFailure("PAIR_MISMATCH", "/request_id", "paired-request-id")
    parent_check = _git(repo_path, "rev-parse", "--verify", f"{parent}^{{commit}}")
    if parent_check.returncode != 0:
        raise GitBodyFailure("GIT_PARENT", parent, "parent-commit")
    parent_oid = parent_check.stdout.strip()
    observed = _ref_oid(repo_path, ref)
    expected = observed if expected_ref_oid is None else expected_ref_oid
    if expected and (
        not GIT_OBJECT_ID_RE.fullmatch(expected) or len(expected) != len(parent_oid)
    ):
        raise GitBodyFailure("GIT_IDENTITY", ref, "expected-ref-oid")
    if expected and parent_oid != expected:
        raise GitBodyFailure("GIT_PARENT", parent, "expected-ref-parent")
    identity_args = _terminal_identity_config_args(identity, ref)

    codec = _terminal_codec(client)
    if expected:
        _read_terminal_request_at_oid(repo_path, ref, expected, codec)
    try:
        prepared_provenance = codec.prepare(
            REQUEST_PROVENANCE_CONTEXT_ID, provenance, expected=REQUEST_PROVENANCE_CONTRACT
        )
        prepared_outcome = codec.prepare(
            REQUEST_OUTCOME_CONTEXT_ID, outcome, expected=REQUEST_OUTCOME_CONTRACT
        )
    except Exception as exc:
        raise GitBodyFailure("BODY_VALIDATION", "/", "terminal-body-codec") from exc
    provenance_bytes = bytes(prepared_provenance.canonical.data)
    outcome_bytes = bytes(prepared_outcome.canonical.data)

    with tempfile.TemporaryDirectory(prefix="ai-org-terminal-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if expected:
            _git_required_env(repo_path, env, "read-tree", expected)
        else:
            _git_required_env(repo_path, env, "read-tree", "--empty")
        for alias in (*REQUEST_PROVENANCE_ALIASES, *REQUEST_OUTCOME_ALIASES):
            _git_required_env(repo_path, env, "update-index", "--force-remove", "--", alias)
        for path, content in (
            (REQUEST_PROVENANCE_PATH, provenance_bytes),
            (REQUEST_OUTCOME_PATH, outcome_bytes),
        ):
            blob = _git_required_bytes_env(
                repo_path, env, "hash-object", "-w", "--stdin", input_bytes=content
            ).stdout.decode("ascii").strip()
            _git_required_env(repo_path, env, "update-index", "--add", "--cacheinfo", "100644", blob, path)
        tree_oid = _git_required_env(repo_path, env, "write-tree").stdout.strip()
    commit = _git_required(
        repo_path,
        *identity_args,
        "commit-tree", tree_oid, "-p", parent_oid,
        "-m", f"request outcome: {outcome.get('status', 'unknown')}",
    ).stdout.strip()
    return PreparedRequestBodySet(
        ref=ref, expected_ref_oid=expected, parent_oid=parent_oid,
        commit_oid=commit, tree_oid=tree_oid,
        provenance=provenance_bytes, outcome=outcome_bytes, _codec=codec,
    )


def publish_terminal_request(
    repo,
    prepared: PreparedRequestBodySet,
    *,
    inject_failure: bool = False,
) -> GitPublicationResult:
    """Publish a prepared cohort by create or compare-and-swap exactly once."""

    repo_path = Path(repo)
    try:
        _verify_prepared_terminal_request(repo_path, prepared)
    except GitBodyFailure as failure:
        return GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_ref_oid, failure=failure,
        )
    if inject_failure:
        return GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_ref_oid,
            failure=GitBodyFailure("GIT_PUBLICATION", prepared.ref, "injected-publication-failure"),
        )
    old = prepared.expected_ref_oid or ("0" * len(prepared.commit_oid))
    result = _git(repo_path, "update-ref", prepared.ref, prepared.commit_oid, old)
    if result.returncode != 0:
        return GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_ref_oid,
            failure=GitBodyFailure("GIT_CAS", prepared.ref, "create-or-compare-and-swap"),
        )
    return GitPublicationResult(
        "updated" if prepared.expected_ref_oid else "created",
        prepared.ref, prepared.expected_ref_oid, commit_oid=prepared.commit_oid,
    )


def _verify_prepared_terminal_request(
    repo: Path, prepared: PreparedRequestBodySet
) -> None:
    """Fail closed when a prepared publication unit was forged or corrupted."""

    _validate_terminal_ref(repo, prepared.ref)
    for location, oid, rule in (
        (prepared.ref, prepared.expected_ref_oid, "expected-ref-oid"),
        ("parent", prepared.parent_oid, "parent-commit"),
        ("commit", prepared.commit_oid, "prepared-commit"),
        ("tree", prepared.tree_oid, "prepared-tree"),
    ):
        if (not oid and rule != "expected-ref-oid") or (
            oid and not GIT_OBJECT_ID_RE.fullmatch(oid)
        ):
            raise GitBodyFailure("GIT_IDENTITY", location, rule)
    object_id_width = len(prepared.commit_oid)
    for location, oid, rule in (
        (prepared.ref, prepared.expected_ref_oid, "expected-ref-oid"),
        ("parent", prepared.parent_oid, "parent-commit"),
        ("tree", prepared.tree_oid, "prepared-tree"),
    ):
        if oid and len(oid) != object_id_width:
            raise GitBodyFailure("GIT_IDENTITY", location, rule)

    commit = _git(repo, "rev-parse", "--verify", f"{prepared.commit_oid}^{{commit}}")
    if commit.returncode != 0 or commit.stdout.strip() != prepared.commit_oid:
        raise GitBodyFailure("GIT_IDENTITY", prepared.commit_oid, "prepared-commit")
    tree = _git(repo, "rev-parse", "--verify", f"{prepared.commit_oid}^{{tree}}")
    if tree.returncode != 0 or tree.stdout.strip() != prepared.tree_oid:
        raise GitBodyFailure("GIT_IDENTITY", prepared.tree_oid, "prepared-tree")
    parents = parent_commits(repo, prepared.commit_oid)
    if parents != [prepared.parent_oid]:
        raise GitBodyFailure("GIT_PARENT", prepared.commit_oid, "prepared-parent")

    for alias in (*REQUEST_PROVENANCE_ALIASES, *REQUEST_OUTCOME_ALIASES):
        if _git(repo, "cat-file", "-e", f"{prepared.commit_oid}:{alias}").returncode == 0:
            raise GitBodyFailure("GIT_IDENTITY", alias, "registered-alias-absent")
    for path, expected in (
        (REQUEST_PROVENANCE_PATH, prepared.provenance),
        (REQUEST_OUTCOME_PATH, prepared.outcome),
    ):
        actual = _git_bytes(repo, "show", f"{prepared.commit_oid}:{path}")
        if actual.returncode != 0 or actual.stdout != expected:
            raise GitBodyFailure("GIT_IDENTITY", path, "prepared-body")
    provenance_id = _prepared_terminal_request_id(
        prepared.provenance, REQUEST_PROVENANCE_PATH, REQUEST_PROVENANCE_CONTRACT
    )
    outcome_id = _prepared_terminal_request_id(
        prepared.outcome, REQUEST_OUTCOME_PATH, REQUEST_OUTCOME_CONTRACT
    )
    if provenance_id != outcome_id:
        raise GitBodyFailure(
            "PAIR_MISMATCH", "/request_id", "prepared-paired-request-id"
        )

    try:
        provenance = prepared._codec.parse(
            REQUEST_PROVENANCE_CONTEXT_ID,
            prepared.provenance,
            expected=REQUEST_PROVENANCE_CONTRACT,
        )
        outcome = prepared._codec.parse(
            REQUEST_OUTCOME_CONTEXT_ID,
            prepared.outcome,
            expected=REQUEST_OUTCOME_CONTRACT,
        )
    except Exception as exc:
        raise GitBodyFailure("BODY_VALIDATION", "/", "prepared-terminal-body-codec") from exc
    provenance_id = provenance.get("request_id") if isinstance(provenance, Mapping) else None
    outcome_id = outcome.get("request_id") if isinstance(outcome, Mapping) else None
    if not isinstance(provenance_id, str) or not provenance_id or provenance_id != outcome_id:
        raise GitBodyFailure("PAIR_MISMATCH", "/request_id", "prepared-paired-request-id")
    author = commit_author(repo, prepared.commit_oid)
    if not _is_terminal_identity(author.get("name", ""), author.get("email", "")):
        raise GitBodyFailure("GIT_IDENTITY", prepared.commit_oid, "commit-identity")


def prepare_network_publication(
    repo,
    ref: str,
    files: Mapping[str, Any],
    *,
    parent: str,
    expected_ref_oid: str | None = None,
    identity: Mapping[str, str] | None = None,
    synthetic_reviews: Mapping[str, Mapping[str, Any]] | None = None,
    client=None,
) -> PreparedNetworkBodySet:
    """Emit a network tree and its synthetic reviews before constructing it."""

    from ai_org import network_bodies, review_bodies

    repo_path = Path(repo)
    if not ref.startswith("refs/heads/ai-org/patch-series/"):
        raise GitBodyFailure("GIT_IDENTITY", ref, "network-publication-ref")
    parent_check = _git(repo_path, "rev-parse", "--verify", f"{parent}^{{commit}}")
    if parent_check.returncode != 0:
        raise GitBodyFailure("GIT_PARENT", parent, "parent-commit")
    parent_oid = parent_check.stdout.strip()
    observed = _ref_oid(repo_path, ref)
    expected = observed if expected_ref_oid is None else expected_ref_oid
    if expected and not re.fullmatch(r"[0-9a-f]{40,64}", expected):
        raise GitBodyFailure("GIT_IDENTITY", ref, "expected-ref-oid")
    if expected and parent_oid != expected:
        raise GitBodyFailure("GIT_PARENT", parent, "expected-ref-parent")
    try:
        previous_revision = None
        if expected:
            previous_ledger = network_bodies.read_network_body(
                repo_path, expected, "series-coverage-ledger.json", client=client
            )
            if previous_ledger is not None:
                revision = previous_ledger["ledger_revision"]
                previous_revision = (
                    revision.as_int_exact()
                    if hasattr(revision, "as_int_exact")
                    else int(revision)
                )
        publication = network_bodies.prepare_network_publication(
            files, previous_ledger_revision=previous_revision, client=client
        )
        review_inputs = dict(synthetic_reviews or {})
        for path, value in review_inputs.items():
            match = re.fullmatch(
                r"patch-series-review-rounds/round-([0-9]{4})-direction-review-record\.cue",
                path,
            )
            if (
                match is None
                or not isinstance(value, Mapping)
                or value.get("lineage_escalation") is not True
                or int(value.get("round", 0)) != int(match.group(1))
            ):
                raise ValueError(f"network-publication:invalid-synthetic-review:{path}")
        review_aggregate = review_bodies.prepare_marker_tree(
            review_inputs, client=client
        )
        companions = tuple(
            (path, content.encode("utf-8", errors="strict"))
            for path, content in sorted(review_aggregate.files.items())
        )
        if any(path in publication.files or path in publication.aliases for path, _ in companions):
            raise ValueError("network-publication:companion-coordinate-collision")
        commit_subject = "patch series: publish canonical network tree"
        if review_inputs:
            if len(review_inputs) != 1:
                raise ValueError("network-publication:multiple-synthetic-reviews")
            review_path, review_value = next(iter(review_inputs.items()))
            marker_round = re.fullmatch(
                r"patch-series-review-rounds/round-([0-9]{4})-direction-review-record\.cue",
                review_path,
            )
            assert marker_round is not None
            commit_subject = str(review_value.get("git_result_marker") or "")
            if commit_subject != (
                f"patch_series: needs-revision round {int(marker_round.group(1))}"
            ):
                raise ValueError("network-publication:synthetic-review-marker")
    except Exception as exc:
        raise GitBodyFailure("BODY_VALIDATION", "/", "network-publication-codec") from exc

    with tempfile.TemporaryDirectory(prefix="ai-org-network-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git_required_env(repo_path, env, "read-tree", expected or parent_oid)
        for alias in publication.aliases:
            _git_required_env(repo_path, env, "update-index", "--force-remove", "--", alias)
        for path, content in publication.files.items():
            blob = _git_required_bytes_env(
                repo_path, env, "hash-object", "-w", "--stdin", input_bytes=content.encode("utf-8")
            ).stdout.decode("ascii").strip()
            _git_required_env(repo_path, env, "update-index", "--add", "--cacheinfo", "100644", blob, path)
        for path, content in companions:
            blob = _git_required_bytes_env(
                repo_path, env, "hash-object", "-w", "--stdin", input_bytes=content
            ).stdout.decode("ascii").strip()
            _git_required_env(repo_path, env, "update-index", "--add", "--cacheinfo", "100644", blob, path)
        tree_oid = _git_required_env(repo_path, env, "write-tree").stdout.strip()
    commit = _git_required(
        repo_path, *identity_config_args(identity), "commit-tree", tree_oid, "-p", parent_oid,
        "-m", commit_subject,
    ).stdout.strip()
    return PreparedNetworkBodySet(
        ref=ref,
        expected_ref_oid=expected,
        parent_oid=parent_oid,
        commit_oid=commit,
        tree_oid=tree_oid,
        publication=publication,
        commit_subject=commit_subject,
        companion_files=companions,
    )


def publish_network_publication(
    repo, prepared: PreparedNetworkBodySet, *, inject_failure: bool = False
) -> GitPublicationResult:
    """Publish exactly one prepared network tree by create or compare-and-swap."""

    repo_path = Path(repo)
    try:
        _verify_prepared_network_publication(repo_path, prepared)
    except GitBodyFailure as failure:
        return GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_ref_oid, failure=failure,
            atomic=True,
            ref_outcomes=((prepared.ref, "rejected"),),
            expected_ref_oids=((prepared.ref, prepared.expected_ref_oid),),
        )
    if not prepared.release_enabled:
        return GitPublicationResult(
            "rejected",
            prepared.ref,
            prepared.expected_ref_oid,
            failure=GitBodyFailure(
                "GIT_PUBLICATION_GATE",
                prepared.ref,
                prepared.release_rule or "network-publication-not-enabled",
            ),
            atomic=True,
            ref_outcomes=((prepared.ref, "rejected"),),
            expected_ref_oids=((prepared.ref, prepared.expected_ref_oid),),
        )
    if inject_failure:
        return GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_ref_oid,
            failure=GitBodyFailure("GIT_PUBLICATION", prepared.ref, "injected-publication-failure"),
            atomic=True,
            ref_outcomes=((prepared.ref, "rejected"),),
            expected_ref_oids=((prepared.ref, prepared.expected_ref_oid),),
        )
    old = prepared.expected_ref_oid or ("0" * len(prepared.commit_oid))
    result = _git(repo_path, "update-ref", prepared.ref, prepared.commit_oid, old)
    if result.returncode != 0:
        return GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_ref_oid,
            failure=GitBodyFailure("GIT_CAS", prepared.ref, "create-or-compare-and-swap"),
            atomic=True,
            ref_outcomes=((prepared.ref, "rejected"),),
            expected_ref_oids=((prepared.ref, prepared.expected_ref_oid),),
        )
    return GitPublicationResult(
        "updated" if prepared.expected_ref_oid else "created", prepared.ref,
        prepared.expected_ref_oid, commit_oid=prepared.commit_oid,
        atomic=True,
        ref_outcomes=((prepared.ref, "updated" if prepared.expected_ref_oid else "created"),),
        expected_ref_oids=((prepared.ref, prepared.expected_ref_oid),),
        resulting_ref_oids=((prepared.ref, prepared.commit_oid),),
    )


def _verify_prepared_network_publication(
    repo: Path, prepared: PreparedNetworkBodySet
) -> None:
    """Fail closed if a preflighted network unit changed before publication."""

    from ai_org import network_bodies, review_bodies
    from ai_org.body_codec import BodyCodecClient

    if not prepared.ref.startswith("refs/heads/ai-org/patch-series/"):
        raise GitBodyFailure("GIT_IDENTITY", prepared.ref, "network-publication-ref")
    object_id_width = len(prepared.commit_oid)
    if object_id_width not in {40, 64}:
        raise GitBodyFailure("GIT_IDENTITY", "commit", "prepared-commit")
    for location, oid, rule in (
        (prepared.ref, prepared.expected_ref_oid, "expected-ref-oid"),
        ("parent", prepared.parent_oid, "parent-commit"),
        ("commit", prepared.commit_oid, "prepared-commit"),
        ("tree", prepared.tree_oid, "prepared-tree"),
    ):
        if (not oid and rule != "expected-ref-oid") or (
            oid and (not GIT_OBJECT_ID_RE.fullmatch(oid) or len(oid) != object_id_width)
        ):
            raise GitBodyFailure("GIT_IDENTITY", location, rule)

    commit = _git(repo, "rev-parse", "--verify", f"{prepared.commit_oid}^{{commit}}")
    if commit.returncode != 0 or commit.stdout.strip() != prepared.commit_oid:
        raise GitBodyFailure("GIT_IDENTITY", prepared.commit_oid, "prepared-commit")
    tree = _git(repo, "rev-parse", "--verify", f"{prepared.commit_oid}^{{tree}}")
    if tree.returncode != 0 or tree.stdout.strip() != prepared.tree_oid:
        raise GitBodyFailure("GIT_IDENTITY", prepared.tree_oid, "prepared-tree")
    if parent_commits(repo, prepared.commit_oid) != [prepared.parent_oid]:
        raise GitBodyFailure("GIT_PARENT", prepared.commit_oid, "prepared-parent")
    if commit_subject(repo, prepared.commit_oid) != prepared.commit_subject:
        raise GitBodyFailure("GIT_IDENTITY", prepared.commit_oid, "prepared-subject")

    publication = prepared.publication
    if not isinstance(publication, network_bodies.PreparedNetworkPublication):
        raise GitBodyFailure("GIT_IDENTITY", "/", "prepared-network-publication")
    members = tuple(publication.members)
    files = dict(publication.files)
    member_paths = [member.canonical_path for member in members]
    if len(member_paths) != len(set(member_paths)) or set(member_paths) != set(files):
        raise GitBodyFailure("GIT_IDENTITY", "/", "prepared-network-member-set")
    try:
        network_bodies.validate_carrier_recipes(
            members, tuple(publication.carrier_recipes)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise GitBodyFailure(
            "GIT_IDENTITY", "/", "prepared-network-carriers"
        ) from exc

    expected_aliases: set[str] = set()
    for member in members:
        classified = network_bodies.classify_path(member.canonical_path)
        if classified is None or classified != (
            network_bodies.classify_path(member.historical_path)
        ):
            raise GitBodyFailure(
                "GIT_IDENTITY", member.canonical_path, "prepared-network-coordinate"
            )
        _role, canonical, historical = classified
        if canonical != member.canonical_path or historical != member.historical_path:
            raise GitBodyFailure(
                "GIT_IDENTITY", member.canonical_path, "prepared-network-coordinate"
            )
        expected_aliases.add(historical)
        try:
            expected = files[canonical].encode("utf-8", errors="strict")
        except (AttributeError, UnicodeError) as exc:
            raise GitBodyFailure(
                "GIT_IDENTITY", canonical, "prepared-network-body"
            ) from exc
        if member.prepared.canonical.data != expected:
            raise GitBodyFailure("GIT_IDENTITY", canonical, "prepared-network-body")
        actual = _git_bytes(repo, "show", f"{prepared.commit_oid}:{canonical}")
        if actual.returncode != 0 or actual.stdout != expected:
            raise GitBodyFailure("GIT_IDENTITY", canonical, "prepared-network-body")

    if set(publication.aliases) != expected_aliases:
        raise GitBodyFailure("GIT_IDENTITY", "/", "prepared-network-alias-set")
    for alias in expected_aliases:
        if _git(repo, "cat-file", "-e", f"{prepared.commit_oid}:{alias}").returncode == 0:
            raise GitBodyFailure("GIT_IDENTITY", alias, "registered-alias-absent")
    try:
        network_bodies._validate_cohort_shape(list(members))
    except (AssertionError, ValueError) as exc:
        raise GitBodyFailure("GIT_IDENTITY", "/", "prepared-network-cohort") from exc

    companion_paths: set[str] = set()
    codec = BodyCodecClient()
    marker_subject = prepared.commit_subject.startswith(
        "patch_series: needs-revision round "
    )
    if marker_subject != (len(prepared.companion_files) == 1):
        raise GitBodyFailure(
            "GIT_IDENTITY", prepared.commit_oid, "prepared-network-companion-set"
        )
    for path, expected in prepared.companion_files:
        match = re.fullmatch(
            r"patch-series-review-rounds/round-([0-9]{4})-direction-review-record\.cue",
            path,
        )
        if match is None or path in companion_paths or path in files:
            raise GitBodyFailure("GIT_IDENTITY", path, "prepared-network-companion")
        companion_paths.add(path)
        try:
            body = codec.parse(
                review_bodies.SYNTHETIC_CONTEXT,
                expected,
                expected=review_bodies.REVIEW_CONTRACT,
            )
            round_value = body.get("round", 0)
            round_number = (
                round_value.as_int_exact()
                if hasattr(round_value, "as_int_exact")
                else int(round_value)
            )
            if (
                body.get("lineage_escalation") is not True
                or round_number != int(match.group(1))
                or body.get("reviewed_commit") != prepared.parent_oid
                or body.get("git_result_marker") != prepared.commit_subject
            ):
                raise ValueError("synthetic review identity mismatch")
        except Exception as exc:
            raise GitBodyFailure(
                "GIT_IDENTITY", path, "prepared-network-companion"
            ) from exc
        actual = _git_bytes(repo, "show", f"{prepared.commit_oid}:{path}")
        if actual.returncode != 0 or actual.stdout != expected:
            raise GitBodyFailure(
                "GIT_IDENTITY", path, "prepared-network-companion"
            )


def _terminal_codec(client):
    if client is not None:
        return client
    from ai_org.body_codec import BodyCodecClient

    return BodyCodecClient()


def _validate_terminal_ref(repo: Path, ref: str) -> None:
    """Reject malformed or out-of-namespace terminal refs before body work."""

    prefix = "refs/ai-org/request-outcomes/"
    if not ref.startswith(prefix) or not ref.removeprefix(prefix):
        raise GitBodyFailure("GIT_IDENTITY", ref, "request-outcome-ref")
    if _git(repo, "check-ref-format", ref).returncode != 0:
        raise GitBodyFailure("GIT_IDENTITY", ref, "request-outcome-ref")


def _terminal_identity_config_args(
    identity: Mapping[str, str] | None, ref: str
) -> list[str]:
    """Resolve a commit identity before either terminal body is emitted."""

    resolved = engine_identity()
    if identity:
        for field in ("name", "email"):
            supplied = str(identity.get(field) or "").strip()
            if supplied:
                resolved[field] = supplied
    name = resolved["name"]
    email = resolved["email"]
    if not _is_terminal_identity(name, email):
        raise GitBodyFailure("GIT_IDENTITY", ref, "commit-identity")
    return identity_config_args(resolved)


def _is_terminal_identity(name: str, email: str) -> bool:
    """Return whether a terminal custom-ref commit has a safe noreply author."""

    return bool(
        name
        and name == name.strip()
        and not any(character in name for character in "\r\n\x00")
        and re.fullmatch(r"[^@\s\x00-\x1f\x7f]+@users\.noreply\.github\.com", email)
    )


def _prepared_terminal_request_id(
    data: bytes, path: str, contract: Mapping[str, str]
) -> str:
    """Revalidate pair identity from the exact bytes selected for publication."""

    from ai_org.body_codec import strict_json_loads

    try:
        envelope = strict_json_loads(data.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        raise GitBodyFailure("BODY_VALIDATION", path, "prepared-terminal-envelope") from exc
    if not isinstance(envelope, Mapping) or any(
        envelope.get(field) != expected
        for field, expected in (
            ("apiVersion", contract["apiVersion"]),
            ("kind", contract["kind"]),
            ("variant", contract["variant"]),
        )
    ):
        raise GitBodyFailure("BODY_VALIDATION", path, "prepared-terminal-envelope")
    body = envelope.get("body")
    request_id = body.get("request_id") if isinstance(body, Mapping) else None
    if not isinstance(request_id, str) or not request_id:
        raise GitBodyFailure("BODY_VALIDATION", path, "prepared-request-id")
    return request_id


def _ref_oid(repo: Path, ref: str) -> str:
    resolved = _git(repo, "rev-parse", "--verify", ref)
    return resolved.stdout.strip() if resolved.returncode == 0 else ""


def _read_terminal_body_entry_at_oid(
    repo: Path,
    ref: str,
    frozen: str,
    canonical_path: str,
    aliases: tuple[str, ...],
    context_id: str,
    contract: Mapping[str, str],
    client,
) -> tuple[str, Mapping[str, Any]]:
    """Return one strict member together with the representation that supplied it."""

    candidates: list[tuple[str, bytes]] = []
    for path in (canonical_path, *aliases):
        read = _git_bytes(repo, "show", f"{frozen}:{path}")
        if read.returncode == 0:
            candidates.append((path, read.stdout))
    if len(candidates) != 1:
        raise GitBodyFailure("GIT_IDENTITY", ref, "exactly-one-canonical-or-historical-body")
    path, data = candidates[0]
    if path == canonical_path and not data.endswith(b"\n"):
        raise GitBodyFailure("BODY_CANONICAL", path, "terminal-newline")
    try:
        body = _terminal_codec(client).parse(context_id, data, expected=contract)
    except Exception as exc:
        raise GitBodyFailure("BODY_VALIDATION", path, "terminal-body-codec") from exc
    return path, body


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


def freeze_semantic_snapshot(repo, branch: str) -> FrozenGitBodySnapshot | None:
    """Freeze a branch target, notes-ref tip, and exact note blob once.

    The note is read through the frozen notes commit's tree, never through the
    live notes ref. A concurrent writer can therefore publish a later note but
    cannot change the bytes selected by this snapshot.
    """
    repo_path = Path(repo)
    target_oid = head_sha(repo_path, branch)
    if target_oid is None:
        return None
    notes_result = _git(repo_path, "rev-parse", "--verify", SEMANTIC_NOTE_FULL_REF)
    notes_ref_oid = notes_result.stdout.strip() if notes_result.returncode == 0 else ""
    blob = _exact_note_blob(repo_path, notes_ref_oid, target_oid) if notes_ref_oid else None
    return FrozenGitBodySnapshot(
        branch=branch,
        target_oid=target_oid,
        notes_ref=SEMANTIC_NOTE_FULL_REF,
        notes_ref_oid=notes_ref_oid,
        blob=blob,
    )


def _exact_note_blob(repo: Path, notes_commit: str, target_oid: str) -> ExactGitBlob | None:
    listing = _git_bytes(repo, "ls-tree", "-r", "-z", notes_commit)
    if listing.returncode != 0:
        raise GitBodyFailure("GIT_READ", SEMANTIC_NOTE_FULL_REF, "notes-tree")
    matches: list[tuple[str, str]] = []
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise GitBodyFailure("GIT_READ", SEMANTIC_NOTE_FULL_REF, "notes-tree-entry")
        mode, object_type, raw_oid = fields
        try:
            path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise GitBodyFailure("GIT_READ", SEMANTIC_NOTE_FULL_REF, "notes-tree-path-utf8") from exc
        if path.replace("/", "") != target_oid:
            continue
        if mode != b"100644" or object_type != b"blob":
            raise GitBodyFailure("GIT_READ", path, "note-blob-mode")
        matches.append((path, raw_oid.decode("ascii")))
    if not matches:
        return None
    if len(matches) != 1:
        raise GitBodyFailure("ALIAS_CONFLICT", target_oid, "note-target-ambiguous")
    path, blob_oid = matches[0]
    body = _git_bytes(repo, "cat-file", "blob", blob_oid)
    if body.returncode != 0:
        raise GitBodyFailure("GIT_READ", blob_oid, "note-blob")
    return ExactGitBlob(
        oid=blob_oid,
        sha256=hashlib.sha256(body.stdout).hexdigest(),
        byte_length=len(body.stdout),
        path=path,
        data=body.stdout,
    )


def _prepare_semantic_notes_commit(
    repo: Path,
    *,
    target_oid: str,
    expected_notes_ref_oid: str,
    canonical: bytes,
) -> str:
    """Build a notes commit on an isolated temporary ref.

    Creating the candidate may add unreachable objects, but it never changes
    the live notes authority, the caller's index, or any worktree. Publication
    remains a separate expected-old update of ``SEMANTIC_NOTE_FULL_REF``.
    """
    try:
        canonical.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GitBodyFailure("ENCODING", SEMANTIC_NOTE_FULL_REF, "utf8") from exc
    temp_name = f"ai-org/prepared-notes/{os.getpid()}-{uuid.uuid4().hex}"
    temp_ref = f"refs/notes/{temp_name}"
    prepared_oid = ""
    try:
        if expected_notes_ref_oid:
            _git_required(repo, "update-ref", temp_ref, expected_notes_ref_oid, "")
        result = _git_required_bytes_env(
            repo,
            {},
            *identity_config_args(),
            "notes",
            f"--ref={temp_name}",
            "add",
            "-f",
            "-F",
            "-",
            target_oid,
            input_bytes=canonical,
        )
        del result
        prepared_oid = _git_required(repo, "rev-parse", "--verify", temp_ref).stdout.strip()
        if not prepared_oid:
            raise GitBodyFailure("GIT_PREPARE", SEMANTIC_NOTE_FULL_REF, "prepared-notes-commit")
        return prepared_oid
    finally:
        current = _git(repo, "rev-parse", "--verify", temp_ref)
        if current.returncode == 0:
            removed = _git(repo, "update-ref", "-d", temp_ref, current.stdout.strip())
            if removed.returncode != 0:
                raise GitBodyFailure("GIT_PREPARE", temp_ref, "temporary-ref-cleanup")


def _publish_semantic_notes_commit(repo: Path, prepared_oid: str, expected_notes_ref_oid: str) -> str:
    expected = expected_notes_ref_oid or ""
    published = _git(repo, "update-ref", SEMANTIC_NOTE_FULL_REF, prepared_oid, expected)
    if published.returncode != 0:
        raise GitBodyFailure("GIT_CAS", SEMANTIC_NOTE_FULL_REF, "expected-notes-ref")
    return prepared_oid


def read_semantic_result(
    repo,
    branch: str,
    *,
    client: Any | None = None,
    consumer: Any | None = None,
) -> SemanticReadResult:
    """Read and validate one frozen Semantic Status note.

    The optional consumer is invoked only after the codec has admitted the
    complete note and returned the established four-field projection.
    Historical JSON is read-only: this path performs no notes or ref update.
    """
    try:
        snapshot = freeze_semantic_snapshot(repo, branch)
    except (GitBodyFailure, RuntimeError) as exc:
        return SemanticReadResult(status="rejected", snapshot=None, failure=exc)
    if snapshot is None:
        return SemanticReadResult(
            status="missing",
            snapshot=None,
            failure=GitBodyFailure("GIT_READ", branch, "target-missing"),
        )
    if snapshot.blob is None:
        return SemanticReadResult(status="absent", snapshot=snapshot)

    codec = client or _default_body_codec_client()
    try:
        projected = codec.parse(
            SEMANTIC_CONTEXT_ID,
            snapshot.blob.data,
            expected=_semantic_contract_identity(),
        )
        if not isinstance(projected, Mapping) or set(projected) != set(SEMANTIC_FIELDS):
            raise GitBodyFailure("PROJECTION", SEMANTIC_CONTEXT_ID, "semantic-fields")
        body = {key: projected[key] for key in SEMANTIC_FIELDS}
        if any(not isinstance(value, str) for value in body.values()):
            raise GitBodyFailure("PROJECTION", SEMANTIC_CONTEXT_ID, "semantic-field-type")
    except Exception as exc:  # CodecFailure is data-only; preserve it verbatim.
        return SemanticReadResult(status="rejected", snapshot=snapshot, failure=exc)

    if consumer is not None:
        consumer(dict(body))
    return SemanticReadResult(status="validated", snapshot=snapshot, body=body)


def read_semantic(repo, branch: str, *, client: Any | None = None) -> dict[str, str]:
    """Read the established four-field semantic labels for a branch head."""
    result = read_semantic_result(repo, branch, client=client)
    return dict(result.body) if result.ok and result.body is not None else {}


def prepare_semantic_note(
    repo,
    branch: str,
    labels: Mapping[str, str],
    *,
    client: Any | None = None,
) -> PreparedSemanticNote:
    """Finish codec emission and validation before any Git mutation."""
    snapshot = freeze_semantic_snapshot(repo, branch)
    if snapshot is None:
        raise GitBodyFailure("GIT_READ", branch, "target-missing")
    semantic = {
        key: labels[key]
        for key in SEMANTIC_FIELDS
        if key in labels and isinstance(labels[key], str)
    }
    if set(semantic) != set(SEMANTIC_FIELDS):
        missing = next(key for key in SEMANTIC_FIELDS if key not in semantic)
        raise GitBodyFailure("SCHEMA_VALIDATION", missing, "required")
    codec = client or _default_body_codec_client()
    prepared = codec.prepare(
        SEMANTIC_CONTEXT_ID,
        semantic,
        expected=_semantic_contract_identity(),
    )
    canonical = bytes(prepared.canonical.data)
    if not _is_current_semantic_canonical(canonical, semantic):
        # This is both the storage-version gate and the supported-writer floor:
        # even a stale or injected client cannot replace a canonical note with
        # a legacy JSON representation.
        raise GitBodyFailure("WRITER_FLOOR", SEMANTIC_NOTE_FULL_REF, "canonical-cue-required")
    return PreparedSemanticNote(
        branch=branch,
        target_oid=snapshot.target_oid,
        expected_notes_ref_oid=snapshot.notes_ref_oid,
        codec_bodies=prepared,
    )


def publish_semantic_note(repo, prepared: PreparedSemanticNote) -> SemanticPublicationResult:
    """CAS-publish one already-prepared canonical Semantic Status note."""
    repo_path = Path(repo)
    try:
        notes_commit = _prepare_semantic_notes_commit(
            repo_path,
            target_oid=prepared.target_oid,
            expected_notes_ref_oid=prepared.expected_notes_ref_oid,
            canonical=prepared.canonical,
        )
        blob = _exact_note_blob(repo_path, notes_commit, prepared.target_oid)
        if blob is None or blob.data != prepared.canonical:
            raise GitBodyFailure("GIT_PREPARE", prepared.target_oid, "prepared-note-bytes")
        _publish_semantic_notes_commit(repo_path, notes_commit, prepared.expected_notes_ref_oid)
        return SemanticPublicationResult(
            status="applied",
            branch=prepared.branch,
            target_oid=prepared.target_oid,
            notes_ref_before=prepared.expected_notes_ref_oid,
            notes_ref_after=notes_commit,
            blob_oid=blob.oid,
            sha256=blob.sha256,
            byte_length=blob.byte_length,
        )
    except GitBodyFailure as exc:
        return SemanticPublicationResult(
            status="rejected",
            branch=prepared.branch,
            target_oid=prepared.target_oid,
            notes_ref_before=prepared.expected_notes_ref_oid,
            failure=exc,
        )
    except RuntimeError:
        failure = GitBodyFailure("GIT_PREPARE", SEMANTIC_NOTE_FULL_REF, "notes-candidate")
        return SemanticPublicationResult(
            status="rejected",
            branch=prepared.branch,
            target_oid=prepared.target_oid,
            notes_ref_before=prepared.expected_notes_ref_oid,
            failure=failure,
        )


def write_semantic_result(
    repo,
    branch: str,
    labels: Mapping[str, str],
    *,
    client: Any | None = None,
) -> SemanticPublicationResult:
    """Prepare and CAS-publish the existing Semantic Status API body."""
    try:
        prepared = prepare_semantic_note(repo, branch, labels, client=client)
    except Exception as exc:
        target = head_sha(repo, branch) or ""
        return SemanticPublicationResult(
            status="rejected",
            branch=branch,
            target_oid=target,
            notes_ref_before="",
            failure=exc,
        )
    return publish_semantic_note(repo, prepared)


def write_semantic(
    repo,
    branch: str,
    labels: Mapping[str, str],
    *,
    client: Any | None = None,
) -> dict[str, str]:
    """Write canonical CUE semantic labels for a frozen branch head."""
    result = write_semantic_result(repo, branch, labels, client=client)
    if not result.ok:
        raise RuntimeError(str(result.failure or "semantic note publication failed"))
    return {"branch": branch, "commit": result.target_oid}


def _default_body_codec_client() -> Any:
    # Lazy import preserves the phase-neutral dependency direction and prevents
    # importing ai_org from triggering executable discovery or a source build.
    from ai_org.body_codec import BodyCodecClient

    return BodyCodecClient()


def _semantic_contract_identity() -> dict[str, str]:
    return {
        "api_version": SEMANTIC_API_VERSION,
        "kind": SEMANTIC_KIND,
        "variant": SEMANTIC_VARIANT,
    }


def _is_current_semantic_canonical(
    data: bytes,
    expected_body: Mapping[str, str] | None = None,
) -> bool:
    if b"\r" in data or data.count(b"\n") != 1 or not data.endswith(b"\n"):
        return False
    from ai_org.body_codec import strict_json_loads
    try:
        envelope = strict_json_loads(data)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(envelope, dict) or list(envelope) != ["apiVersion", "body", "kind", "variant"]:
        return False
    if (
        envelope.get("apiVersion") != SEMANTIC_API_VERSION
        or envelope.get("kind") != SEMANTIC_KIND
        or envelope.get("variant") != SEMANTIC_VARIANT
    ):
        return False
    body = envelope.get("body")
    if not isinstance(body, dict) or list(body) != ["change_kind", "owner", "subsystem", "working_state"]:
        return False
    if any(not isinstance(value, str) for value in body.values()):
        return False
    return expected_body is None or body == dict(expected_body)


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


def update_ref(
    repo, ref: str, commit: str, *, expected: str | None = None
) -> dict[str, Any]:
    """Point a local ref at a commit; optionally compare-and-swap its old tip."""
    args = ["update-ref", ref, commit]
    if expected is not None:
        args.append(expected or "0" * len(commit))
    result = _git(Path(repo), *args)
    if result.returncode != 0:
        return {"ok": False, "error_type": "update_ref_failed", "detail": result.stderr.strip() or result.stdout.strip()}
    return {"ok": True, "ref": ref, "commit": commit}


def publish_prepared_integration_commit(
    repo,
    ref: str,
    commit_oid: str,
    expected_ref_oid: str,
    *,
    source_ref_oids: Mapping[str, str] | None = None,
    inject_failure: bool = False,
) -> GitPublicationResult:
    """Publish one prepared integration commit after all source-ref CAS checks."""

    repo_path = Path(repo)
    sources = tuple(
        sorted(
            (str(source_ref), str(source_oid))
            for source_ref, source_oid in (source_ref_oids or {}).items()
            if source_ref != ref
        )
    )
    expected_pairs = ((ref, expected_ref_oid), *sources)
    rejected = ((ref, "rejected"),)
    if (
        not ref.startswith("refs/heads/")
        or _git(repo_path, "check-ref-format", ref).returncode != 0
        or GIT_OBJECT_ID_RE.fullmatch(commit_oid) is None
        or (
            expected_ref_oid
            and (
                GIT_OBJECT_ID_RE.fullmatch(expected_ref_oid) is None
                or len(expected_ref_oid) != len(commit_oid)
            )
        )
        or _git(repo_path, "rev-parse", "--verify", f"{commit_oid}^{{commit}}").returncode
        != 0
        or any(
            _git(repo_path, "check-ref-format", source_ref).returncode != 0
            or GIT_OBJECT_ID_RE.fullmatch(source_oid) is None
            or len(source_oid) != len(commit_oid)
            for source_ref, source_oid in sources
        )
    ):
        return GitPublicationResult(
            "rejected",
            ref,
            expected_ref_oid,
            failure=GitBodyFailure("GIT_IDENTITY", ref, "prepared-integration-commit"),
            atomic=True,
            ref_outcomes=rejected,
            expected_ref_oids=expected_pairs,
        )
    if inject_failure:
        return GitPublicationResult(
            "rejected",
            ref,
            expected_ref_oid,
            failure=GitBodyFailure(
                "GIT_PUBLICATION", ref, "injected-publication-failure"
            ),
            atomic=True,
            ref_outcomes=rejected,
            expected_ref_oids=expected_pairs,
        )

    old = expected_ref_oid or ("0" * len(commit_oid))
    commands = ["start"]
    commands.extend(
        f"verify {source_ref} {source_oid}"
        for source_ref, source_oid in sources
    )
    commands.extend((f"update {ref} {commit_oid} {old}", "prepare", "commit", ""))
    result = subprocess.run(
        ["git", "-C", str(repo_path), "update-ref", "--stdin"],
        input="\n".join(commands),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return GitPublicationResult(
            "rejected",
            ref,
            expected_ref_oid,
            failure=GitBodyFailure("GIT_CAS", ref, "integration-ref-cas"),
            atomic=True,
            ref_outcomes=rejected,
            expected_ref_oids=expected_pairs,
        )
    status = "updated" if expected_ref_oid else "created"
    return GitPublicationResult(
        status,
        ref,
        expected_ref_oid,
        commit_oid=commit_oid,
        atomic=True,
        ref_outcomes=((ref, status),),
        expected_ref_oids=expected_pairs,
        resulting_ref_oids=((ref, commit_oid), *sources),
    )


def update_refs_atomic(
    repo,
    updates: Mapping[str, tuple[str, str]],
) -> GitPublicationResult:
    """CAS-update two or more local refs in one ref transaction.

    Each mapping value is ``(new_oid, expected_old_oid)``.  An empty expected
    OID means the ref must not exist.  Object creation is deliberately outside
    the transaction; no durable coordinate exposes those objects unless every
    compare-and-swap check reaches ``commit``.
    """

    ordered = tuple(
        sorted(
            (str(ref), str(new_oid), str(old_oid))
            for ref, (new_oid, old_oid) in updates.items()
        )
    )
    if len(ordered) < 2:
        raise ValueError("atomic local publication requires at least two refs")
    oid_pattern = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
    for ref, new_oid, old_oid in ordered:
        if _git(Path(repo), "check-ref-format", ref).returncode != 0:
            raise ValueError(f"invalid ref in atomic local publication: {ref}")
        if oid_pattern.fullmatch(new_oid) is None or (
            old_oid and oid_pattern.fullmatch(old_oid) is None
        ):
            raise ValueError("invalid object id in atomic local publication")
    commands = ["start"]
    commands.extend(
        f"update {ref} {new_oid} {old_oid or ('0' * len(new_oid))}"
        for ref, new_oid, old_oid in ordered
    )
    commands.extend(("prepare", "commit", ""))
    result = subprocess.run(
        ["git", "-C", str(Path(repo)), "update-ref", "--stdin"],
        input="\n".join(commands),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    primary = ordered[0][0]
    if result.returncode == 0:
        return GitPublicationResult(
            "updated",
            primary,
            ordered[0][2],
            commit_oid=ordered[0][1],
            atomic=True,
            ref_outcomes=tuple((ref, "updated") for ref, _new, _old in ordered),
            expected_ref_oids=tuple((ref, old) for ref, _new, old in ordered),
        )
    return GitPublicationResult(
        "rejected",
        primary,
        ordered[0][2],
        failure=GitBodyFailure("GIT_PUBLICATION", primary, "atomic-ref-cas"),
        atomic=True,
        ref_outcomes=tuple((ref, "rejected") for ref, _new, _old in ordered),
        expected_ref_oids=tuple((ref, old) for ref, _new, old in ordered),
    )


def list_refs(repo, prefix: str) -> list[str]:
    """Return full ref names under a prefix (for-each-ref), sorted by git."""
    result = _git(Path(repo), "for-each-ref", prefix, "--format=%(refname)")
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def committer_timestamp(repo, ref: str) -> int | None:
    """Return the committer epoch seconds for a ref tip, or None when unavailable."""
    result = _git(Path(repo), "log", "-1", "--format=%ct", ref)
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return int(text) if text.isdigit() else None


def commit_author(repo, ref: str) -> dict[str, str] | None:
    """Return {name, email} of a ref tip's author, or None when the ref is missing."""
    result = _git(Path(repo), "log", "-1", "--format=%an%x1f%ae", ref)
    if result.returncode != 0:
        return None
    parts = result.stdout.strip("\n").split("\x1f")
    if len(parts) != 2:
        return None
    return {"name": parts[0], "email": parts[1]}


def show_compatibility_file(repo, ref: str, path: str) -> str | None:
    """Read one explicitly named historical alias without creating a body API."""
    result = _git(Path(repo), "show", f"{ref}:{path}")
    return result.stdout if result.returncode == 0 else None


# --- Remote layer (patch-author pull model) -----------------------------------
# All remote operations return typed reports and never raise: a remote that is
# unreachable, or a ref that lost a race, is a normal outcome the caller must
# route on, not an exception (same totality invariant as the patchwork gate).


def clone_repository(source, dest) -> dict[str, Any]:
    """Clone source into dest; typed fail-closed result."""
    dest_path = Path(dest)
    try:
        result = subprocess.run(
            ["git", "clone", str(source), str(dest_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return {"ok": False, "error_type": "clone_failed", "detail": str(exc)}
    if result.returncode != 0:
        return {"ok": False, "error_type": "clone_failed", "detail": result.stderr.strip() or result.stdout.strip()}
    return {"ok": True, "path": str(dest_path)}


def ls_remote(repo, remote: str, *patterns: str) -> dict[str, Any]:
    """Return {ref: sha} for remote refs matching patterns; typed fail-closed result."""
    result = _git(Path(repo), "ls-remote", remote, *patterns)
    if result.returncode != 0:
        return {"ok": False, "error_type": "ls_remote_failed", "detail": result.stderr.strip() or result.stdout.strip()}
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if sha.strip() and ref.strip():
            refs[ref.strip()] = sha.strip()
    return {"ok": True, "refs": refs}


def remote_default_branch(repo, remote: str) -> str | None:
    """Return the remote's default branch name (its HEAD symref), or None."""
    result = _git(Path(repo), "ls-remote", "--symref", remote, "HEAD")
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("ref:"):
            target = line.split()[1] if len(line.split()) >= 2 else ""
            if target.startswith("refs/heads/"):
                return target.removeprefix("refs/heads/")
    return None


def fetch_refspecs(repo, remote: str, refspecs: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Fetch explicit refspecs from a remote; typed fail-closed result."""
    result = _git(Path(repo), "fetch", remote, *refspecs)
    if result.returncode != 0:
        return {"ok": False, "error_type": "fetch_failed", "detail": result.stderr.strip() or result.stdout.strip()}
    return {"ok": True}


def push_create(repo, remote: str, remote_ref: str, commit: str) -> dict[str, Any]:
    """Create remote_ref at commit only if it does not exist yet on the remote.

    Memento: create-only ref publish primitive — PROVEN with two racing clones
    against a bare remote. `--force-with-lease=<ref>:` with an EMPTY expected
    value means "create only if <ref> is absent on the remote". The
    expected-old-oid travels inside the push protocol and the server's
    receive-pack ref transaction enforces it, so exactly one racer sees
    "[new reference]" and every other racer sees "[rejected] ... (stale info)".
    Used for first-time announcement refs (each author's OWN ref — visibility,
    not a lock) and for publishing a contribution branch name exactly once.
    Do not replace with fetch+check+push: that reintroduces the check-then-act
    race the lease removes.
    """
    return _classified_push(repo, remote, remote_ref, commit, expected="")


def push_cas(repo, remote: str, remote_ref: str, commit: str, expected: str) -> dict[str, Any]:
    """Move remote_ref to commit only if the remote still points at expected.

    Same lease primitive as push_create with a non-empty expected old object id:
    used for updating an author's own announcement ref and for fast-forward
    re-publishes of a contribution branch. A concurrent-writer loss is a typed
    "rejected", never an exception.
    """
    return _classified_push(repo, remote, remote_ref, commit, expected=expected)


def push_delete(repo, remote: str, remote_ref: str, expected: str) -> dict[str, Any]:
    """Delete remote_ref only if the remote still points at expected.

    The withdraw-authoring-intent primitive: an announcement is withdrawn by
    REMOVING the ref (brief 23 addendum — no status ledger, absence IS the
    state). The lease guards against deleting a ref another process of the
    same author moved meanwhile.
    """
    result = _git(
        Path(repo),
        "push",
        f"--force-with-lease={remote_ref}:{expected}",
        remote,
        f":{remote_ref}",
    )
    if result.returncode == 0:
        return {"ok": True, "status": "deleted", "ref": remote_ref}
    detail = result.stderr.strip() or result.stdout.strip()
    if "[rejected]" in detail or "stale info" in detail:
        return {"ok": False, "status": "rejected", "error_type": "ref_cas_rejected", "ref": remote_ref, "detail": detail}
    return {"ok": False, "status": "error", "error_type": "push_failed", "ref": remote_ref, "detail": detail}


def push_atomic_ref_transfer(
    repo,
    remote: str,
    *,
    create_ref: str,
    commit: str,
    delete_ref: str,
    expected_delete_oid: str,
) -> GitPublicationResult:
    """Atomically create one ref at expected absence and exactly delete another.

    ``git push --atomic`` is also the capability probe: receive-pack rejects
    the operation before changing either ref when it did not advertise atomic
    receive.  Both leases travel in the same negotiated command set.
    """
    result = _git(
        Path(repo),
        "push",
        "--porcelain",
        "--atomic",
        f"--force-with-lease={create_ref}:",
        f"--force-with-lease={delete_ref}:{expected_delete_oid}",
        remote,
        f"{commit}:{create_ref}",
        f":{delete_ref}",
    )
    detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode == 0:
        return GitPublicationResult(
            "migrated",
            create_ref,
            "",
            commit_oid=commit,
            atomic=True,
            ref_outcomes=((create_ref, "created"), (delete_ref, "deleted")),
            expected_ref_oids=((create_ref, ""), (delete_ref, expected_delete_oid)),
        )
    lowered = detail.lower()
    rule = "atomic-receive-not-advertised" if "does not support --atomic" in lowered else "atomic-ref-cas"
    return GitPublicationResult(
        "rejected",
        create_ref,
        "",
        failure=GitBodyFailure("GIT_PUBLICATION", create_ref, rule),
        atomic=True,
        ref_outcomes=((create_ref, "rejected"), (delete_ref, "rejected")),
        expected_ref_oids=((create_ref, ""), (delete_ref, expected_delete_oid)),
    )


def push_atomic_create_refs(
    repo,
    remote: str,
    refs: Mapping[str, str],
) -> GitPublicationResult:
    """Create multiple absent refs in one receive-pack transaction."""
    ordered = tuple(sorted((str(ref), str(commit)) for ref, commit in refs.items()))
    if len(ordered) < 2:
        raise ValueError("atomic create requires at least two refs")
    result = _git(
        Path(repo),
        "push",
        "--porcelain",
        "--atomic",
        *(f"--force-with-lease={ref}:" for ref, _commit in ordered),
        remote,
        *(f"{commit}:{ref}" for ref, commit in ordered),
    )
    detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    primary = ordered[0][0]
    if result.returncode == 0:
        return GitPublicationResult(
            "created", primary, "", commit_oid=ordered[0][1], atomic=True,
            ref_outcomes=tuple((ref, "created") for ref, _commit in ordered),
            expected_ref_oids=tuple((ref, "") for ref, _commit in ordered),
            resulting_ref_oids=ordered,
        )
    lowered = detail.lower()
    rule = "atomic-receive-not-advertised" if "does not support --atomic" in lowered else "atomic-ref-create"
    return GitPublicationResult(
        "rejected", primary, "",
        failure=GitBodyFailure("GIT_PUBLICATION", primary, rule), atomic=True,
        ref_outcomes=tuple((ref, "rejected") for ref, _commit in ordered),
        expected_ref_oids=tuple((ref, "") for ref, _commit in ordered),
    )


def delete_local_ref(repo, ref: str) -> dict[str, Any]:
    """Delete a local ref; typed fail-closed result."""
    result = _git(Path(repo), "update-ref", "-d", ref)
    if result.returncode != 0:
        return {"ok": False, "error_type": "update_ref_failed", "detail": result.stderr.strip() or result.stdout.strip()}
    return {"ok": True, "ref": ref}


def _classified_push(repo, remote: str, remote_ref: str, commit: str, *, expected: str) -> dict[str, Any]:
    result = _git(
        Path(repo),
        "push",
        f"--force-with-lease={remote_ref}:{expected}",
        remote,
        f"{commit}:{remote_ref}",
    )
    if result.returncode == 0:
        return {"ok": True, "status": "pushed", "ref": remote_ref, "commit": commit}
    detail = result.stderr.strip() or result.stdout.strip()
    if "[rejected]" in detail or "stale info" in detail:
        return {"ok": False, "status": "rejected", "error_type": "ref_cas_rejected", "ref": remote_ref, "detail": detail}
    return {"ok": False, "status": "error", "error_type": "push_failed", "ref": remote_ref, "detail": detail}


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


def _git_bytes(repo: Path, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run Git without text decoding for exact durable-body reads."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_bytes,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            ["git", "-C", str(repo), *args],
            127,
            b"",
            str(exc).encode("utf-8", errors="replace"),
        )


def _git_required(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = _git(repo, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result


def _git_required_env(
    repo: Path,
    env: Mapping[str, str],
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_text,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **dict(env)},
        )
    except OSError as exc:
        result = subprocess.CompletedProcess(["git", "-C", str(repo), *args], 127, "", str(exc))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result


def _git_required_bytes_env(
    repo: Path,
    env: Mapping[str, str],
    *args: str,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a required Git command without locale or newline transcoding."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_bytes,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **dict(env)},
        )
    except OSError as exc:
        result = subprocess.CompletedProcess(
            ["git", "-C", str(repo), *args], 127, b"", str(exc).encode("utf-8", errors="replace")
        )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or result.stdout.decode(
            "utf-8", errors="replace"
        ).strip()
        raise RuntimeError(detail or "git command failed")
    return result


def _constitution_module_paths(repo: Path) -> list[str]:
    core = [rel_path for rel_path in CORE_CONSTITUTION_PATHS if (repo / rel_path).is_file()]
    if core:
        return core
    discovered: list[str] = []
    for path in sorted(repo.rglob("*.py")):
        rel_path = path.relative_to(repo).as_posix()
        if _skip_constitution_path(rel_path):
            continue
        if _top_module_docstring(path.read_text(encoding="utf-8", errors="replace")):
            discovered.append(rel_path)
        if len(discovered) >= 24:
            break
    return discovered


def _skip_constitution_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    return any(
        part.startswith(".") or part in {"__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
        for part in parts
    )


def _module_constitution_section(repo: Path, rel_path: str, max_chars: int) -> str:
    path = repo / rel_path
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    parts: list[str] = []
    leading_comments = _leading_comment_header(text)
    if leading_comments and (rel_path in CORE_CONSTITUTION_PATHS or "Memento" in leading_comments):
        parts.append(leading_comments)
    docstring = _top_module_docstring(text)
    if docstring:
        parts.append(docstring)
    if rel_path == "ai_org/engineering_precedent_store.py":
        preheld = _org_preheld_lessons_section(text)
        if preheld:
            parts.append(preheld)
    if not parts:
        return ""
    body, truncated = _truncate_at_paragraph(
        "\n\n".join(parts),
        max_chars,
        f"{rel_path} Memento/header projection exceeded {max_chars} characters",
    )
    suffix = " [module truncated]" if truncated else ""
    return f"## {rel_path}{suffix}\n{body}"


def _leading_comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                lines.append("")
            continue
        if stripped.startswith("#"):
            comment = stripped[1:]
            if comment.startswith(" "):
                comment = comment[1:]
            lines.append(comment.rstrip())
            continue
        break
    return _normalize_projection_text("\n".join(lines))


def _top_module_docstring(text: str) -> str:
    try:
        module = ast.parse(text)
    except SyntaxError:
        return ""
    docstring = ast.get_docstring(module, clean=False)
    if not isinstance(docstring, str):
        return ""
    return _normalize_projection_text(docstring)


def _org_preheld_lessons_section(text: str) -> str:
    try:
        module = ast.parse(text)
    except SyntaxError:
        return ""
    lessons: Any = None
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "ORG_PREHELD_LESSONS" for target in node.targets):
            try:
                lessons = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                lessons = None
            break
    if not isinstance(lessons, tuple):
        return ""
    lines = ["Org-preheld lessons (projected from ORG_PREHELD_LESSONS):"]
    for item in lessons:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        kind = str(item.get("kind") or "").strip()
        facet = item.get("facet")
        if not term or not isinstance(facet, dict):
            continue
        lines.append("")
        lines.append(f"- {term} ({kind})")
        for key in ("structure", "rationale", "when_to_use", "implementation_hooks", "evidence", "source_url"):
            value = str(facet.get(key) or "").strip()
            if value:
                lines.append(f"  {key}: {' '.join(value.split())}")
    return "\n".join(lines)


def _docs_patch_series_constitution_section(repo: Path, max_chars: int) -> str:
    docs_dir = repo / "docs" / "rfcs"
    if not docs_dir.is_dir():
        return ""
    lines: list[str] = ["## docs/rfcs titles and statuses"]
    for path in sorted(docs_dir.glob("*.md")):
        title = ""
        status = ""
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not title and stripped.startswith("# "):
                title = stripped
            elif not status and stripped.lower().startswith("status:"):
                status = stripped
            if title and status:
                break
        if title or status:
            rel_path = path.relative_to(repo).as_posix()
            lines.append(f"- {rel_path}: {title or '(untitled)'}; {status or 'Status: (missing)'}")
    if len(lines) == 1:
        return ""
    body, truncated = _truncate_at_paragraph(
        "\n".join(lines),
        max_chars,
        f"docs/rfcs title/status projection exceeded {max_chars} characters",
    )
    return body + (" [docs truncated]" if truncated else "")


def _truncate_at_paragraph(text: str, max_chars: int, reason: str) -> tuple[str, bool]:
    normalized = _normalize_projection_text(text)
    if max_chars <= 0:
        return f"[truncated: {reason}]", bool(normalized)
    if len(normalized) <= max_chars:
        return normalized, False
    marker = f"\n\n[truncated: {reason}]"
    budget = max_chars - len(marker)
    if budget <= 0:
        return marker.strip()[:max_chars], True
    paragraphs = re.split(r"\n\s*\n", normalized)
    kept: list[str] = []
    used = 0
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        projected = len(paragraph) if not kept else used + 2 + len(paragraph)
        if projected > budget:
            break
        kept.append(paragraph)
        used = projected
    prefix = "\n\n".join(kept).strip()
    if not prefix:
        return marker.strip()[:max_chars], True
    return f"{prefix}{marker}", True


def _normalize_projection_text(text: str) -> str:
    lines = [line.rstrip() for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _truncate_at_sentence(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        return "[truncated: body omitted by bound]", bool(text)
    if len(text) <= max_chars:
        return text, False
    boundary = max(text.rfind(marker, 0, max_chars + 1) for marker in (". ", "! ", "? ", ".\n", "!\n", "?\n"))
    if boundary >= max_chars // 3:
        clipped = text[: boundary + 1].strip()
    else:
        clipped = text[:max_chars].rstrip()
    return f"{clipped}\n[truncated: commit body exceeded {max_chars} characters]", True


def _history_item_size(item: Mapping[str, Any]) -> int:
    return len(str(item.get("subject") or "")) + len(str(item.get("body") or "")) + 80


def _dedupe_refs(repo: Path, refs: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        key = head_sha(repo, ref) or ref
        if key not in seen:
            seen.add(key)
            deduped.append(ref)
    return deduped
