"""Git-native event medium for the AI Org shared mailing list."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from ai_org import git_wrapper


LIST_BRANCH = "ai-org/list"
LIST_REF = f"refs/heads/{LIST_BRANCH}"
CURSOR_REF_PREFIX = "refs/ai-org/list-cursors/"
_APPEND_ATTEMPTS = 4
_KIND_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")

# Memento: correspondence, not containment. The list carries WORDS and POINTS
# to commits; it never carries artifact objects. Message-Id is stable across
# re-rooting, and every post stays an empty-tree commit with only its list parent.


def ensure_list(repo, *, ctx=None) -> dict[str, Any]:
    """Create the orphan list genesis when absent and return its current head."""
    del ctx
    repo_path = Path(repo)
    head = git_wrapper.head_sha(repo_path, LIST_BRANCH)
    if head is not None:
        return {"ok": True, "created": False, "head": head}

    try:
        seed, seed_tree = _capture_seed(repo_path)
        empty_tree = git_wrapper._git_required_env(  # noqa: SLF001 - git_wrapper is the engine Git gateway.
            repo_path, {}, "mktree", input_text=""
        ).stdout.strip()
        genesis = _commit_tree(
            repo_path,
            empty_tree,
            f"list: genesis\n\nSeed: {seed}\nSeed-Tree: {seed_tree}\n",
        )
    except RuntimeError as exc:
        return {"ok": False, "created": False, "head": "", "error": str(exc)}

    if _update_ref_cas(repo_path, LIST_REF, genesis, "0" * len(genesis)):
        return {"ok": True, "created": True, "head": genesis}

    head = git_wrapper.head_sha(repo_path, LIST_BRANCH)
    if head is not None:
        return {"ok": True, "created": False, "head": head}
    return {
        "ok": False,
        "created": False,
        "head": "",
        "error": "could not create mailing-list ref",
    }


def post(
    repo,
    *,
    kind: str,
    subject: str,
    body: str = "",
    refs: Mapping[str, Any] | None = None,
    coordinate: str | None = None,
    body_text: str | None = None,
    corresponds_to: Mapping[str, Any] | None = None,
    in_reply_to: str | None = None,
    ctx=None,
) -> dict[str, Any]:
    """Append one empty mail commit using compare-and-swap publication."""
    resolved_body = _resolve_body_text(body, body_text)
    normalized_refs = _validate_post(kind, subject, resolved_body, refs)
    resolved_coordinate = _validate_coordinate(coordinate, subject)
    normalized_links = _normalize_mapping(corresponds_to, field="corresponds_to")
    resolved_reply = _validate_in_reply_to(in_reply_to)
    ensured = ensure_list(repo, ctx=ctx)
    if not ensured["ok"]:
        return {
            "ok": False,
            "commit": "",
            "kind": kind,
            "subject": subject,
            "error": ensured.get("error", "could not ensure mailing list"),
        }
    repo_path = Path(repo)
    for _attempt in range(_APPEND_ATTEMPTS):
        expected = git_wrapper.head_sha(repo_path, LIST_BRANCH)
        if expected is None:
            ensured = ensure_list(repo_path, ctx=ctx)
            if not ensured["ok"]:
                break
            expected = str(ensured["head"])
        unique_item = _qa_item_ref(kind, normalized_refs)
        if unique_item is not None:
            existing = _qa_post_for_item(repo_path, unique_item)
            if existing is not None:
                result = {
                    "ok": True,
                    "commit": str(existing["commit"]),
                    "kind": kind,
                    "subject": subject,
                    "existing": True,
                }
                if "message_id" in existing:
                    result["message_id"] = existing["message_id"]
                return result
        tree = git_wrapper.tree_sha(repo_path, expected)
        if tree is None:
            break
        try:
            sequence = _next_sequence(repo_path, expected)
            message_id = _message_id(kind, resolved_coordinate, sequence)
            message = _post_message(
                kind,
                subject,
                resolved_body,
                normalized_refs,
                message_id=message_id,
                corresponds_to=normalized_links,
                in_reply_to=resolved_reply,
            )
            commit = _commit_tree(repo_path, tree, message, parent=expected)
        except RuntimeError as exc:
            return {
                "ok": False,
                "commit": "",
                "kind": kind,
                "subject": subject,
                "error": str(exc),
            }
        if _update_ref_cas(repo_path, LIST_REF, commit, expected):
            return {
                "ok": True,
                "commit": commit,
                "kind": kind,
                "subject": subject,
                "message_id": message_id,
            }

    return {
        "ok": False,
        "commit": "",
        "kind": kind,
        "subject": subject,
        "error": "mailing-list append contention exceeded retry limit",
    }


def read(repo, *, since=None, kinds=None) -> list[dict[str, Any]]:
    """Read list posts oldest-first, excluding genesis and ``since`` itself."""
    repo_path = Path(repo)
    head = git_wrapper.head_sha(repo_path, LIST_BRANCH)
    if head is None:
        return []

    revision = LIST_BRANCH
    if since is not None:
        since_oid = git_wrapper.head_sha(repo_path, str(since))
        if since_oid is None or not git_wrapper.is_ancestor(repo_path, since_oid, head):
            raise ValueError("since must identify a commit on the mailing list")
        revision = f"{since_oid}..{LIST_BRANCH}"

    selected_kinds = _normalize_kinds(kinds)
    result = git_wrapper._git_bytes(  # noqa: SLF001 - delimiter-safe batch history read.
        repo_path,
        "log",
        "--reverse",
        "-z",
        "--format=%H%x00%aI%x00%B",
        revision,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip() or "could not read mailing list")
    fields = result.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3:
        raise RuntimeError("could not parse mailing-list history")

    posts: list[dict[str, Any]] = []
    for index in range(0, len(fields), 3):
        commit = fields[index].decode("ascii")
        author_date = fields[index + 1].decode("utf-8", errors="strict")
        message = fields[index + 2].decode("utf-8", errors="strict")
        parsed = _parse_post_message(message)
        if parsed is None:
            continue
        if selected_kinds is not None and parsed["kind"] not in selected_kinds:
            continue
        posts.append({"commit": commit, **parsed, "author_date": author_date})
    return posts


def genesis_seed(repo) -> dict[str, str] | None:
    """Return the immutable work-repository seed recorded by list genesis."""
    repo_path = Path(repo)
    head = git_wrapper.head_sha(repo_path, LIST_BRANCH)
    if head is None:
        return None
    roots = git_wrapper._git(  # noqa: SLF001 - root discovery through the Git gateway.
        repo_path, "rev-list", "--max-parents=0", head
    )
    if roots.returncode != 0:
        detail = roots.stderr.strip() or roots.stdout.strip()
        raise RuntimeError(detail or "could not read mailing-list genesis")
    root_oids = roots.stdout.splitlines()
    if len(root_oids) != 1:
        raise RuntimeError("mailing-list history must have exactly one genesis")
    message = git_wrapper._git(  # noqa: SLF001 - message read through the Git gateway.
        repo_path, "show", "-s", "--format=%B", root_oids[0]
    )
    if message.returncode != 0:
        detail = message.stderr.strip() or message.stdout.strip()
        raise RuntimeError(detail or "could not read mailing-list genesis")
    return _parse_genesis_seed(message.stdout)


def format_patch_text(repo, base, tip) -> str:
    """Return deterministic, applyable mail patch text for ``base..tip``."""
    repo_path = Path(repo)
    base_oid = git_wrapper.head_sha(repo_path, str(base))
    if base_oid is None:
        raise ValueError(f"base must identify a commit: {base}")
    tip_oid = git_wrapper.head_sha(repo_path, str(tip))
    if tip_oid is None:
        raise ValueError(f"tip must identify a commit: {tip}")
    result = git_wrapper._git_required_env(  # noqa: SLF001 - read-only Git gateway operation.
        repo_path,
        {"LC_ALL": "C"},
        "-c",
        "format.signature=",
        "-c",
        "format.mboxrd=false",
        "-c",
        "log.mailmap=false",
        "format-patch",
        "--stdout",
        "--numbered",
        "--subject-prefix=PATCH",
        "--no-signature",
        "--no-signoff",
        "--no-cover-letter",
        "--no-thread",
        "--no-add-header",
        "--no-to",
        "--no-cc",
        "--no-in-reply-to",
        "--no-attach",
        "--no-base",
        "--no-from",
        "--no-notes",
        "--no-rfc",
        "--no-force-in-body-from",
        "--binary",
        "--full-index",
        "--no-stat",
        "--no-renames",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--unified=3",
        "--diff-algorithm=myers",
        "--no-indent-heuristic",
        "--inter-hunk-context=0",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--no-relative",
        "--submodule=short",
        "--ignore-submodules=none",
        f"{base_oid}..{tip_oid}",
    )
    return result.stdout


def cursor(repo, reader: str) -> str | None:
    """Return a reader's last-read list commit, or ``None`` when unset."""
    ref = _cursor_ref(repo, reader)
    return git_wrapper.head_sha(repo, ref)


def advance_cursor(repo, reader: str, commit: str) -> dict[str, Any]:
    """Move a reader cursor monotonically forward to a list commit."""
    repo_path = Path(repo)
    ref = _cursor_ref(repo_path, reader)
    list_head = git_wrapper.head_sha(repo_path, LIST_BRANCH)
    target = git_wrapper.head_sha(repo_path, commit)
    if list_head is None or target is None or not git_wrapper.is_ancestor(repo_path, target, list_head):
        return {"ok": False, "reader": reader, "commit": "", "error": "commit is not on the mailing list"}

    for _attempt in range(_APPEND_ATTEMPTS):
        current = git_wrapper.head_sha(repo_path, ref)
        if current == target:
            return {"ok": True, "reader": reader, "commit": target, "advanced": False}
        if current is not None and not git_wrapper.is_ancestor(repo_path, current, target):
            return {
                "ok": False,
                "reader": reader,
                "commit": current,
                "error": "cursor cannot move backward or leave list history",
            }
        expected = current or ("0" * len(target))
        if _update_ref_cas(repo_path, ref, target, expected):
            return {"ok": True, "reader": reader, "commit": target, "advanced": True}
    return {"ok": False, "reader": reader, "commit": "", "error": "cursor update contention exceeded retry limit"}


def _commit_tree(repo: Path, tree: str, message: str, *, parent: str | None = None) -> str:
    args = [*git_wrapper.identity_config_args(), "commit-tree", tree]
    if parent is not None:
        args.extend(["-p", parent])
    return git_wrapper._git_required_env(  # noqa: SLF001 - isolated commit construction through the Git gateway.
        repo, {}, *args, input_text=message
    ).stdout.strip()


def _capture_seed(repo: Path) -> tuple[str, str]:
    try:
        default_branch = git_wrapper.default_branch(repo)
    except RuntimeError as exc:
        raise RuntimeError("could not determine mailing-list seed branch") from exc
    seed = git_wrapper.head_sha(repo, default_branch)
    if seed is None:
        raise RuntimeError("could not resolve mailing-list seed commit")
    seed_tree = git_wrapper.tree_sha(repo, seed)
    if seed_tree is None:
        raise RuntimeError("could not resolve mailing-list seed tree")
    return seed, seed_tree


def _parse_genesis_seed(message: str) -> dict[str, str] | None:
    stripped = message.rstrip("\n")
    if not stripped.startswith("list: genesis"):
        return None
    seed_values = [
        line.removeprefix("Seed: ")
        for line in stripped.splitlines()
        if line.startswith("Seed: ")
    ]
    tree_values = [
        line.removeprefix("Seed-Tree: ")
        for line in stripped.splitlines()
        if line.startswith("Seed-Tree: ")
    ]
    if not seed_values and not tree_values:
        return None
    if len(seed_values) != 1 or len(tree_values) != 1 or not seed_values[0] or not tree_values[0]:
        raise RuntimeError("malformed mailing-list genesis seed")
    return {"commit": seed_values[0], "tree": tree_values[0]}


def _update_ref_cas(repo: Path, ref: str, commit: str, expected: str) -> bool:
    result = git_wrapper._git(repo, "update-ref", ref, commit, expected)  # noqa: SLF001 - CAS has no public wrapper.
    return result.returncode == 0


def _validate_post(
    kind: str,
    subject: str,
    body: str,
    refs: Mapping[str, Any] | None,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(kind, str) or _KIND_RE.fullmatch(kind) is None:
        raise ValueError("kind must be a short lowercase token")
    if not isinstance(subject, str) or not subject or "\n" in subject or "\r" in subject:
        raise ValueError("subject must be a non-empty single line")
    if not isinstance(body, str):
        raise TypeError("body must be a string")
    if refs is not None and not isinstance(refs, Mapping):
        raise TypeError("refs must be a mapping")

    return _normalize_mapping(refs, field="refs")


def _resolve_body_text(body: str, body_text: str | None) -> str:
    if not isinstance(body, str):
        raise TypeError("body must be a string")
    if body_text is None:
        return body
    if not isinstance(body_text, str):
        raise TypeError("body_text must be a string")
    if body and body != body_text:
        raise ValueError("body and body_text must not conflict")
    return body_text


def _validate_coordinate(coordinate: str | None, subject: str) -> str:
    value = subject if coordinate is None else coordinate
    if not isinstance(value, str):
        raise TypeError("coordinate must be a string")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("coordinate must be a non-empty single line")
    return value


def _validate_in_reply_to(in_reply_to: str | None) -> str | None:
    if in_reply_to is None:
        return None
    if not isinstance(in_reply_to, str):
        raise TypeError("in_reply_to must be a string")
    if not in_reply_to or "\n" in in_reply_to or "\r" in in_reply_to:
        raise ValueError("in_reply_to must be a non-empty single line")
    return in_reply_to


def _normalize_mapping(
    values: Mapping[str, Any] | None,
    *,
    field: str,
) -> tuple[tuple[str, str], ...]:
    if values is not None and not isinstance(values, Mapping):
        raise TypeError(f"{field} must be a mapping")

    normalized: list[tuple[str, str]] = []
    for raw_name, raw_value in sorted((values or {}).items(), key=lambda item: str(item[0])):
        name = str(raw_name)
        if not name or "=" in name or "\n" in name or "\r" in name:
            raise ValueError(f"{field} names must be non-empty single-line tokens without '='")
        raw_items = raw_value if isinstance(raw_value, (list, tuple)) else (raw_value,)
        for raw_item in raw_items:
            value = str(raw_item)
            if "\n" in value or "\r" in value:
                raise ValueError(f"{field} values must be single-line strings")
            normalized.append((name, value))
    return tuple(normalized)


def _next_sequence(repo: Path, expected: str) -> int:
    result = git_wrapper._git(  # noqa: SLF001 - first-parent ordinal through the Git gateway.
        repo, "rev-list", "--first-parent", "--count", expected
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "could not number mailing-list post")
    try:
        sequence = int(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("could not number mailing-list post") from exc
    if sequence < 1:
        raise RuntimeError("could not number mailing-list post")
    return sequence


def _message_id(kind: str, coordinate: str, sequence: int) -> str:
    encoded_coordinate = quote(coordinate, safe="-._~")
    return f"{kind}.{encoded_coordinate}.{sequence}@ai-org-list"


def _post_message(
    kind: str,
    subject: str,
    body: str,
    refs: Mapping[str, str] | Iterable[tuple[str, str]],
    *,
    message_id: str | None = None,
    corresponds_to: Mapping[str, str] | Iterable[tuple[str, str]] = (),
    in_reply_to: str | None = None,
) -> str:
    ref_items = refs.items() if isinstance(refs, Mapping) else refs
    link_items = corresponds_to.items() if isinstance(corresponds_to, Mapping) else corresponds_to
    trailers = [f"Kind: {kind}"]
    if message_id is not None:
        trailers.append(f"Message-Id: {message_id}")
    if in_reply_to is not None:
        trailers.append(f"In-Reply-To: {in_reply_to}")
    trailers.extend(f"Link: {name}={value}" for name, value in link_items)
    trailers.extend(f"Ref: {name}={value}" for name, value in ref_items)
    return f"{kind.upper()}: {subject}\n\n{body}\n\n" + "\n".join(trailers) + "\n"


def _qa_item_ref(kind: str, refs: Iterable[tuple[str, str]]) -> str | None:
    if kind != "qa":
        return None
    values = [value for name, value in refs if name == "item"]
    return values[0] if len(values) == 1 else None


def _qa_post_for_item(repo: Path, item_ref: str) -> dict[str, Any] | None:
    for entry in read(repo, kinds={"qa"}):
        if entry.get("refs", {}).get("item") == item_ref:
            return entry
    return None


def _parse_post_message(message: str) -> dict[str, Any] | None:
    message = message.rstrip("\n")
    if message.startswith("list: genesis"):
        return None
    subject_line, separator, remainder = message.partition("\n\n")
    trailer_start = remainder.rfind("\n\nKind: ")
    if not separator or trailer_start < 0:
        raise RuntimeError("malformed mailing-list post message")
    body = remainder[:trailer_start]
    trailer_lines = remainder[trailer_start + 2 :].splitlines()
    kind = trailer_lines[0].removeprefix("Kind: ") if trailer_lines else ""
    prefix, colon, subject = subject_line.partition(": ")
    if not colon or _KIND_RE.fullmatch(kind) is None or prefix != kind.upper():
        raise RuntimeError("malformed mailing-list post headers")
    refs: dict[str, Any] = {}
    corresponds_to: dict[str, Any] = {}
    message_id: str | None = None
    in_reply_to: str | None = None
    for line in trailer_lines[1:]:
        if line.startswith("Message-Id: "):
            if message_id is not None:
                raise RuntimeError("malformed mailing-list post trailers")
            message_id = line.removeprefix("Message-Id: ")
            if not message_id:
                raise RuntimeError("malformed mailing-list post trailers")
            continue
        if line.startswith("In-Reply-To: "):
            if in_reply_to is not None:
                raise RuntimeError("malformed mailing-list post trailers")
            in_reply_to = line.removeprefix("In-Reply-To: ")
            if not in_reply_to:
                raise RuntimeError("malformed mailing-list post trailers")
            continue
        if line.startswith("Link: ") and "=" in line:
            name, value = line.removeprefix("Link: ").split("=", 1)
            _append_parsed_value(corresponds_to, name, value)
            continue
        if line.startswith("Ref: ") and "=" in line:
            name, value = line.removeprefix("Ref: ").split("=", 1)
            _append_parsed_value(refs, name, value)
            continue
        raise RuntimeError("malformed mailing-list post trailers")
    parsed: dict[str, Any] = {"kind": kind, "subject": subject, "body": body, "refs": refs}
    if message_id is not None:
        parsed["body_text"] = body
        parsed["message_id"] = message_id
    if corresponds_to:
        parsed["corresponds_to"] = corresponds_to
    if in_reply_to is not None:
        parsed["in_reply_to"] = in_reply_to
    return parsed


def _append_parsed_value(values: dict[str, Any], name: str, value: str) -> None:
    if not name:
        raise RuntimeError("malformed mailing-list post trailers")
    previous = values.get(name)
    if previous is None:
        values[name] = value
    elif isinstance(previous, list):
        previous.append(value)
    else:
        values[name] = [previous, value]


def _normalize_kinds(kinds: Iterable[str] | str | None) -> set[str] | None:
    if kinds is None:
        return None
    values = {kinds} if isinstance(kinds, str) else set(kinds)
    if any(not isinstance(kind, str) or _KIND_RE.fullmatch(kind) is None for kind in values):
        raise ValueError("kinds must contain short lowercase tokens")
    return values


def _cursor_ref(repo, reader: str) -> str:
    if not isinstance(reader, str) or not reader:
        raise ValueError("reader must be a non-empty Git ref name")
    ref = f"{CURSOR_REF_PREFIX}{reader}"
    result = git_wrapper._git(Path(repo), "check-ref-format", ref)  # noqa: SLF001 - validation through Git.
    if result.returncode != 0:
        raise ValueError("reader must form a valid Git ref name")
    return ref


def harvest_and_post_qa(repo, contrib_branch, *, ctx=None) -> dict:
    """Harvest recorded per-item QA evidence and append it to the list."""
    from ai_org.mailing_list_harvest import _harvest_and_post_qa

    return _harvest_and_post_qa(
        repo,
        contrib_branch,
        ctx=ctx,
        read_posts=read,
        post_message=post,
    )
