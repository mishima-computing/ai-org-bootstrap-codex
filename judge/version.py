"""Chain link 1: field -> version.

Given the resolved target (node id / path / scalar) find the commit on the
series branch that introduced or last changed it, and corroborate the
version/round identity from two committed sources. In origin mode
(at_commit) the walk is anchored at a historical commit instead of the
tip, so text later deleted from the body can still be traced to the
commit that introduced it. Corroborating sources:
  * the commit subject ("patch_series v2: ..." / "patch_series: receive ...")
  * patch-series-review-rounds/*.json records, which list added/changed
    node ids per author version.

Everything is read from git; nothing is written.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import body as body_mod
from . import gitread
from .model import RoundRecordHit, Target, VersionLink

_VERSION_SUBJECT_RE = re.compile(r"patch_series v(\d+)\b")
_RECEIVE_SUBJECT_RE = re.compile(r"patch_series: receive\b")

ROUND_RECORDS_DIR = "patch-series-review-rounds"


def resolve_target(tip_text: str, body_path: str, term: Optional[str],
                   node_arg: Optional[str]) -> Target:
    """Resolve --term / --node against the tip blob of the body."""
    target = Target(body_path=body_path, term=term, node_arg=node_arg)
    try:
        data = json.loads(tip_text)
    except json.JSONDecodeError as exc:
        target.reason = f"body at branch tip is not valid JSON: {exc}"
        return target

    if node_arg:
        path = body_mod.parse_node_arg(node_arg)
        try:
            node = body_mod.navigate(data, path)
        except (KeyError, IndexError, TypeError):
            target.reason = f"json-path {node_arg!r} does not exist in the body"
            return target
        target.json_path = list(path)
        node_id, node_path = body_mod.enclosing_id_node(data, path)
        target.node_id = node_id
        target.node_path = list(node_path) if node_path is not None else None
        if isinstance(node, str):
            target.scalar_text = node
        else:
            target.scalar_text = body_mod.canonical(node)
        target.ok = True
        return target

    if term:
        scalar = body_mod.locate_term(tip_text, term)
        if scalar is None:
            target.reason = f"term {term!r} not found anywhere in the body"
            return target
        target.json_path = list(scalar.path)
        node_id, node_path = body_mod.enclosing_id_node(data, scalar.path)
        target.node_id = node_id
        target.node_path = list(node_path) if node_path is not None else None
        target.scalar_text = scalar.value if isinstance(scalar.value, str) else (
            body_mod.canonical(scalar.value))
        target.ok = True
        return target

    # Neither term nor node: the whole body is the target.
    target.json_path = []
    target.node_path = []
    target.node_id = data.get("id") if isinstance(data, dict) else None
    target.scalar_text = None
    target.ok = True
    return target


def _value_at_commit(repo: str, sha: str, body_path: str,
                     target: Target) -> Optional[str]:
    """Canonical value of the target in the body at a given commit.

    None means: file missing, unparseable, or target absent at that commit.
    """
    text = gitread.show_blob(repo, sha, body_path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    node: Any = None
    if target.node_id:
        node = body_mod.find_node_by_id(data, target.node_id)
    if node is None and target.node_path is not None:
        try:
            node = body_mod.navigate(data, tuple(target.node_path))
        except (KeyError, IndexError, TypeError):
            node = None
    if node is None:
        if target.node_id or target.node_path:
            return None
        node = data  # whole-body target
    return body_mod.canonical(node)


def _round_record_hits(repo: str, sha: str, target: Target) -> list:
    """Committed review-round records at `sha` that name the target node."""
    hits: list[RoundRecordHit] = []
    if not target.node_id:
        return hits
    for path in gitread.ls_tree(repo, sha, ROUND_RECORDS_DIR):
        if not path.endswith(".json"):
            continue
        text = gitread.show_blob(repo, sha, path)
        if text is None:
            continue
        try:
            rec = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        listed_as = ""
        if target.node_id in (rec.get("added_node_ids") or []):
            listed_as = "added"
        elif target.node_id in (rec.get("changed_node_ids") or []):
            listed_as = "changed"
        if not listed_as:
            continue
        hits.append(RoundRecordHit(
            record_path=path,
            review_round=rec.get("review_round"),
            author_version=rec.get("author_version"),
            listed_as=listed_as,
        ))
    return hits


def _version_label(subject: str) -> str:
    m = _VERSION_SUBJECT_RE.search(subject)
    if m:
        return f"v{m.group(1)}"
    if _RECEIVE_SUBJECT_RE.search(subject):
        return "v1"
    return ""


def resolve_version(repo: str, branch: str, body_path: str,
                    target: Target, at_commit: str = "") -> VersionLink:
    """Walk the body history to the change commit.

    Default mode anchors at the branch tip: the change commit is the one
    that introduced/last changed the target's CURRENT value.

    Origin mode (Memento tattoo): when `at_commit` is given, the walk is
    anchored there instead -- only commits reachable from `at_commit` are
    considered, and the change commit is the one that INTRODUCED the value
    the target had AS OF that commit (even if later commits rewrote or
    deleted it). The caller must have resolved `target` against the body
    blob at `at_commit`, not at the tip.
    """
    link = VersionLink()
    if not target.ok:
        link.reason = "target unresolved; cannot walk versions"
        return link
    anchor_ref = at_commit or branch
    commits = gitread.log_for_path(repo, anchor_ref, body_path)
    if not commits:
        link.reason = (
            f"no commits touch {body_path!r} on branch {branch!r}"
            + (f" at or before {at_commit[:12]}" if at_commit else ""))
        return link

    anchor_label = (f"commit {at_commit[:12]}" if at_commit
                    else "the branch tip")
    anchor_value = _value_at_commit(repo, commits[0].sha, body_path, target)
    if anchor_value is None:
        link.reason = (
            f"target node not extractable from the body at {anchor_label}")
        return link

    # Newest -> oldest: the change commit is the oldest commit whose value
    # still equals the anchor value, walking back contiguously from the
    # anchor (branch tip, or at_commit in origin mode).
    change_idx = 0
    for i in range(1, len(commits)):
        value = _value_at_commit(repo, commits[i].sha, body_path, target)
        if value == anchor_value:
            change_idx = i
            continue
        break
    change = commits[change_idx]
    parent = commits[change_idx + 1] if change_idx + 1 < len(commits) else None
    parent_value = (
        _value_at_commit(repo, parent.sha, body_path, target)
        if parent else None
    )

    link.ok = True
    link.commit = change.sha
    link.commit_subject = change.subject
    link.committed_at = change.committer_date
    link.parent_commit = parent.sha if parent else ""
    link.window_start = parent.committer_date if parent else ""
    link.window_end = change.committer_date
    link.change_kind = "changed" if parent_value is not None else "introduced"
    link.version_label = _version_label(change.subject)
    link.round_records = _round_record_hits(repo, change.sha, target)
    return link

