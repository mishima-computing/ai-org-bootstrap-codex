"""Authoring announcements: visibility-only refs, NEVER a gate on inputs.

[RATIFIED requester, brief 23 ADDENDUM] NO LOCK anywhere. "Open" is the TASK's
status; taking a task does not change it. A canonical announcement on the
collision-resistant refs/ai-org/authoring-announcements/v2/<coordinate-sha256>
makes an author's intent VISIBLE to other families and to projections — it is
never consulted by any gate that admits or refuses work. The former readable
coordinate remains a read-only migration alias.

Memento (requester verbatim, the no-lock rationale — do not re-derive a lock):
家族間重複=競争、家族内重複=無駄 — duplication BETWEEN families (parent A and
parent B taking the same task) is pure competition, resolved downstream at
review (accept/superseded, kernel canon); duplication WITHIN a family (child 1
and child 2 of the same parent fighting over an item) is waste, prevented only
by the parent's partition of work among its own children. Therefore: no CAS
gate on taking, no lease, no renewal, no staleness daemon — nothing can go
stale when nothing is held. Withdrawal is announcement REMOVAL.

Each author writes only its OWN ref (the ref path ends in the author slug), so
two authors announcing on one series both succeed by construction. A rejected
push on your own ref means another process is running under your identity — a
typed report, never arbitration between families.

Totality invariant (same as the patchwork gate): a pushed announcement ref can
only produce a typed report, never an exception. Malformed records evaluate to
state "invalid" and are simply ignored by every projection — an invalid
announcement can never block anything, because announcements block nothing.
Coexisting canonical and legacy coordinates likewise release no body and stay
non-blocking until a writer resolves the observation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ai_org import contributor_handoff, git_wrapper, network_bodies
from ai_org.body_codec import BodyCodecClient, ContractIdentity
from ai_org.patch_author import producer_lifecycle


ANNOUNCEMENT_SCHEMA = "ai-org-authoring-announcement-v2"
LEGACY_ANNOUNCEMENT_SCHEMA = "ai-org-authoring-announcement-v1"
ANNOUNCEMENT_RECORD_PATH = "announcement.cue"
LEGACY_ANNOUNCEMENT_RECORD_PATH = "announcement.json"
ANNOUNCEMENT_REF_PREFIX = "refs/ai-org/authoring-announcements/"
CANONICAL_REF_PREFIX = f"{ANNOUNCEMENT_REF_PREFIX}v2/"
SERIES_BRANCH_PREFIX = "ai-org/patch-series/"
CONTRIB_BRANCH_PREFIX = "ai-org/contrib/"
ROOT_CONTEXT = "authoring-announcement-root-v1"
CHILD_CONTEXT = "authoring-announcement-child-v1"
ROOT_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "AuthoringAnnouncement", "root-announcement")
CHILD_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "AuthoringAnnouncement", "child-announcement")

_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_NODE_PATH_RE = re.compile(r"^sub/[a-z0-9_]+$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_NOREPLY_EMAIL_RE = re.compile(
    r"[^@\s\x00-\x1f\x7f]+@users\.noreply\.github\.com"
)
_IDENTITY_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_REQUIRED_FIELDS = (
    "schema",
    "series_branch",
    "node_path",
    "node_address",
    "series_head_at_announcement",
    "parent",
    "contrib_branch",
    "canonical_ref",
    "author",
    "status",
)
_LEGACY_REQUIRED_FIELDS = (
    "schema", "series_branch", "node_path", "series_head_at_announcement",
    "contrib_branch", "author", "status",
)


@dataclass(frozen=True)
class AnnouncementCoordinateState:
    """One observation of the canonical coordinate and its legacy alias.

    Announcement consumers share this state classifier so publication,
    evaluation, and withdrawal cannot disagree about which coordinate may
    release a body.  Coexistence is deliberately observable but never
    authoritative.
    """

    state: str
    canonical_ref: str
    legacy_ref: str
    canonical_oid: str = ""
    legacy_oid: str = ""

    @property
    def releases_body(self) -> bool:
        return self.state in {"canonical_only", "legacy_only"}

    @property
    def sole_ref(self) -> str:
        if self.state == "canonical_only":
            return self.canonical_ref
        if self.state == "legacy_only":
            return self.legacy_ref
        return ""

    @property
    def sole_oid(self) -> str:
        if self.state == "canonical_only":
            return self.canonical_oid
        if self.state == "legacy_only":
            return self.legacy_oid
        return ""


def announcement_coordinate_state(
    refs: Mapping[str, str], *, canonical_ref: str, legacy_ref: str
) -> AnnouncementCoordinateState:
    """Classify an exact canonical/legacy ref observation."""

    canonical_oid = refs.get(canonical_ref, "")
    legacy_oid = refs.get(legacy_ref, "")
    if canonical_oid and legacy_oid:
        state = "coexisting"
    elif canonical_oid:
        state = "canonical_only"
    elif legacy_oid:
        state = "legacy_only"
    else:
        state = "absent"
    return AnnouncementCoordinateState(
        state, canonical_ref, legacy_ref, canonical_oid, legacy_oid
    )


def normalize_noreply_identity(name: str, email: str) -> dict[str, str] | None:
    if not isinstance(name, str) or not isinstance(email, str):
        return None
    normalized = {"name": name.strip(), "email": email.strip().lower()}
    if (
        not normalized["name"]
        or _IDENTITY_CONTROL_RE.search(normalized["name"])
        or _NOREPLY_EMAIL_RE.fullmatch(normalized["email"]) is None
    ):
        return None
    return normalized


def author_slug(email: str) -> str:
    """Deterministic path segment retained only for the historical alias.

    [PROVISIONAL-N1] slug = the email's local part lowered with every
    non-alphanumeric run collapsed to "-". Because this projection may collide,
    writers use ``canonical_announcement_ref``; an empty legacy slug remains a
    typed error at announce time.
    """
    local = email.split("@", 1)[0].lower()
    return _SLUG_RE.sub("-", local).strip("-")


def announcement_ref(series_id: str, child_key: str, slug: str) -> str:
    """Return the read-compatible legacy announcement alias.

    [PROVISIONAL-N2] layout: <prefix><series-id>/<author-slug> for a root
    series and <prefix><series-id>/<child_key>/<author-slug> for a nested
    leaf (the addendum names the root form; the child segment mirrors the
    node address exactly as the retired reservation refs did). Address
    listing filters on the number of trailing segments, so root and child
    namespaces never mix even though they share the series-id directory.
    """
    if child_key:
        return f"{ANNOUNCEMENT_REF_PREFIX}{series_id}/{child_key}/{slug}"
    return f"{ANNOUNCEMENT_REF_PREFIX}{series_id}/{slug}"


def canonical_announcement_ref(series_branch: str, node_path: str, author_email: str) -> str:
    """Collision-resistant coordinate over the full normalized identity and address."""
    coordinate = "\0".join((series_branch, node_path, author_email.strip().lower())).encode("utf-8")
    return f"{CANONICAL_REF_PREFIX}{hashlib.sha256(coordinate).hexdigest()}"


def address_ref_prefix(series_id: str, child_key: str = "") -> str:
    if child_key:
        return f"{ANNOUNCEMENT_REF_PREFIX}{series_id}/{child_key}/"
    return f"{ANNOUNCEMENT_REF_PREFIX}{series_id}/"


def refs_for_address(all_refs, series_id: str, child_key: str = "") -> list[str]:
    """Filter announcement refs belonging to exactly this address.

    A root address owns refs with exactly ONE segment after the series id
    (the author slug); a leaf owns refs with exactly one segment after
    series-id/child_key. This keeps root and child announcements apart
    without a second namespace.
    """
    prefix = address_ref_prefix(series_id, child_key)
    return sorted(ref for ref in all_refs if ref.startswith(prefix) and "/" not in ref.removeprefix(prefix))


def series_id_for_branch(series_branch: str) -> str:
    return series_branch.removeprefix(SERIES_BRANCH_PREFIX)


def child_key_for_node_path(node_path: str) -> str:
    if node_path in {"", "."}:
        return ""
    return node_path.rsplit("/", 1)[-1]


def build_record(
    *,
    series_branch: str,
    node_path: str,
    series_head_at_announcement: str,
    contrib_branch: str,
    author_name: str,
    author_email: str,
) -> dict[str, Any]:
    identity = normalize_noreply_identity(author_name, author_email) or {
        "name": author_name.strip(), "email": author_email.strip().lower()
    }
    canonical_ref = canonical_announcement_ref(series_branch, node_path, identity["email"])
    return {
        "schema": ANNOUNCEMENT_SCHEMA,
        "series_branch": series_branch,
        "node_path": node_path,
        "node_address": series_branch if node_path == "." else f"{series_branch}:{node_path}",
        "series_head_at_announcement": series_head_at_announcement,
        "parent": series_head_at_announcement,
        "contrib_branch": contrib_branch,
        "canonical_ref": canonical_ref,
        "author": identity,
        "status": "authoring",
    }


def parse_announcement_record(text: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Parse a historical JSON announcement fail-closed.

    Canonical CUE is handled by ``_parse_body`` through the registered codec;
    this function remains the explicit read-compatibility input.
    """
    errors: list[dict[str, Any]] = []
    if not isinstance(text, str):
        return None, [{"type": "announcement_record_missing"}]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [{"type": "announcement_record_unparseable", "detail": str(exc)}]
    if not isinstance(parsed, Mapping):
        return None, [{"type": "announcement_record_not_object"}]

    required = _LEGACY_REQUIRED_FIELDS if parsed.get("schema") == LEGACY_ANNOUNCEMENT_SCHEMA else _REQUIRED_FIELDS
    for field in required:
        if field not in parsed:
            errors.append({"type": "announcement_record_field_missing", "field": field})
    for field in parsed:
        if field not in required:
            errors.append({"type": "announcement_record_unknown_field", "field": str(field)})
    if errors:
        return None, errors

    if parsed["schema"] not in {ANNOUNCEMENT_SCHEMA, LEGACY_ANNOUNCEMENT_SCHEMA}:
        errors.append(
            {"type": "announcement_record_schema_mismatch", "expected": ANNOUNCEMENT_SCHEMA, "actual": parsed["schema"]}
        )
    series_branch = parsed["series_branch"]
    if not isinstance(series_branch, str) or not series_branch.startswith(SERIES_BRANCH_PREFIX):
        errors.append({"type": "announcement_record_field_invalid", "field": "series_branch"})
    node_path = parsed["node_path"]
    if not (node_path == "." or (isinstance(node_path, str) and _NODE_PATH_RE.match(node_path))):
        errors.append({"type": "announcement_record_field_invalid", "field": "node_path"})
    anchor = parsed["series_head_at_announcement"]
    if not isinstance(anchor, str) or not _SHA_RE.match(anchor):
        errors.append({"type": "announcement_record_field_invalid", "field": "series_head_at_announcement"})
    contrib_branch = parsed["contrib_branch"]
    if not isinstance(contrib_branch, str) or not contrib_branch.startswith(CONTRIB_BRANCH_PREFIX):
        errors.append({"type": "announcement_record_field_invalid", "field": "contrib_branch"})
    author = parsed["author"]
    normalized_author = (
        normalize_noreply_identity(author.get("name", ""), author.get("email", ""))
        if isinstance(author, Mapping)
        and isinstance(author.get("name"), str)
        and isinstance(author.get("email"), str)
        else None
    )
    if (
        not isinstance(author, Mapping)
        or set(author) != {"name", "email"}
        or not isinstance(author.get("name"), str)
        or not author["name"].strip()
        or not isinstance(author.get("email"), str)
        or normalized_author is None
    ):
        # Real emails are forbidden org-wide (crawl exposure); an announcement
        # naming a non-noreply author is invalid, not tolerated.
        errors.append({"type": "announcement_record_field_invalid", "field": "author"})
    if parsed["status"] != "authoring":
        errors.append({"type": "announcement_record_field_invalid", "field": "status"})
    if (
        parsed["schema"] == ANNOUNCEMENT_SCHEMA
        and isinstance(series_branch, str)
        and isinstance(node_path, str)
        and normalized_author is not None
    ):
        expected_ref = canonical_announcement_ref(series_branch, node_path, normalized_author["email"])
        expected_address = series_branch if node_path == "." else f"{series_branch}:{node_path}"
        if parsed["canonical_ref"] != expected_ref:
            errors.append({"type": "announcement_record_field_invalid", "field": "canonical_ref"})
        if parsed["node_address"] != expected_address:
            errors.append({"type": "announcement_record_field_invalid", "field": "node_address"})
        if parsed["parent"] != anchor:
            errors.append({"type": "announcement_record_field_invalid", "field": "parent"})
    if errors:
        return None, errors
    record = {field: parsed[field] for field in required}
    record["author"] = normalized_author
    return record, []


def _context_contract(node_path: str):
    return (ROOT_CONTEXT, ROOT_CONTRACT) if node_path == "." else (CHILD_CONTEXT, CHILD_CONTRACT)


def _parse_body(text: Any, *, client: BodyCodecClient | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(text, str):
        return None, [{"type": "announcement_record_missing"}]
    if text.lstrip().startswith("{") and '"apiVersion"' not in text:
        record, errors = parse_announcement_record(text)
        if record is None or errors:
            return None, errors
        if record["schema"] == LEGACY_ANNOUNCEMENT_SCHEMA:
            record = build_record(
                series_branch=record["series_branch"], node_path=record["node_path"],
                series_head_at_announcement=record["series_head_at_announcement"],
                contrib_branch=record["contrib_branch"], author_name=record["author"]["name"],
                author_email=record["author"]["email"],
            )
        return record, []
    codec = client or BodyCodecClient()
    failures = []
    for context, contract in ((ROOT_CONTEXT, ROOT_CONTRACT), (CHILD_CONTEXT, CHILD_CONTRACT)):
        try:
            projected = codec.parse(context, text.encode("utf-8"), expected=contract)
            record, errors = parse_announcement_record(json.JSONEncoder().encode(projected))
            if record is not None and not errors:
                return record, []
            failures.extend(errors)
        except Exception as exc:
            failures.append({"type": "announcement_record_unparseable", "context": context, "detail": str(exc)})
    return None, failures


def evaluate(
    repo,
    ref: str,
    *,
    coordinate_refs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate one announcement ref. Never raises.

    Result states: none | authoring | invalid. There is deliberately NO
    stale/lease/abandoned state: nothing is held, so nothing can go stale.
    A withdrawn announcement is a DELETED ref, i.e. state "none".
    """
    report: dict[str, Any] = {"ok": True, "ref": ref, "state": "none", "tip": None, "record": None, "errors": []}
    tip = git_wrapper.head_sha(repo, ref)
    if tip is None:
        return report
    report["tip"] = tip
    canonical_text = git_wrapper.show_file(repo, tip, ANNOUNCEMENT_RECORD_PATH)
    legacy_text = git_wrapper.show_compatibility_file(repo, tip, LEGACY_ANNOUNCEMENT_RECORD_PATH)
    if canonical_text is not None and legacy_text is not None:
        report["state"] = "invalid"
        report["errors"] = [{"type": "announcement_body_alias_conflict"}]
        return report
    record, errors = _parse_body(canonical_text if canonical_text is not None else legacy_text)
    if record is None:
        report["state"] = "invalid"
        report["errors"] = errors
        return report
    canonical_ref = record["canonical_ref"]
    legacy_ref = announcement_ref(
        series_id_for_branch(record["series_branch"]),
        child_key_for_node_path(record["node_path"]),
        author_slug(record["author"]["email"]),
    )
    if ref not in {canonical_ref, legacy_ref}:
        report["state"] = "invalid"
        report["errors"] = [{"type": "announcement_ref_binding_invalid"}]
        return report
    observed_refs = coordinate_refs if coordinate_refs is not None else {
        canonical_ref: git_wrapper.head_sha(repo, canonical_ref) or "",
        legacy_ref: git_wrapper.head_sha(repo, legacy_ref) or "",
    }
    coordinates = announcement_coordinate_state(
        observed_refs,
        canonical_ref=canonical_ref,
        legacy_ref=legacy_ref,
    )
    if coordinates.state == "coexisting":
        # A reader may observe both sides of a writer-atomic transfer.  This is
        # visibility-only and non-blocking, but no body escapes the ambiguity.
        report["state"] = "coexistence"
        report["errors"] = [{"type": "announcement_coordinate_coexistence"}]
        return report
    binding_errors = _record_binding_errors(repo, tip, record)
    if binding_errors:
        report["state"] = "invalid"
        report["errors"] = binding_errors
        return report
    report["record"] = record
    report["state"] = "authoring"
    return report


def evaluate_remote(repo, remote: str, ref: str) -> dict[str, Any]:
    """Evaluate an announcement against one authoritative remote observation.

    Submission is a publication boundary and cannot rely on a clone's possibly
    stale view of the compatibility alias.  Fetch the requested coordinate for
    body and commit-binding validation, then classify canonical/legacy
    coexistence from one remote listing.  Network failures remain typed and no
    announcement body is released.
    """
    listed = git_wrapper.ls_remote(repo, remote, ref)
    if not listed.get("ok"):
        return {
            "ok": False,
            "ref": ref,
            "state": "unavailable",
            "tip": None,
            "record": None,
            "errors": [{
                "type": "announcement_remote_unavailable",
                "detail": listed.get("detail", ""),
            }],
        }
    remote_oid = listed["refs"].get(ref, "")
    if not remote_oid:
        return {
            "ok": True, "ref": ref, "state": "none", "tip": None,
            "record": None, "errors": [],
        }
    fetched = git_wrapper.fetch_refspecs(repo, remote, [f"+{ref}:{ref}"])
    if not fetched.get("ok") or git_wrapper.head_sha(repo, ref) != remote_oid:
        return {
            "ok": False,
            "ref": ref,
            "state": "unavailable",
            "tip": None,
            "record": None,
            "errors": [{
                "type": "announcement_remote_unavailable",
                "detail": fetched.get("detail", "announcement ref changed while fetching"),
            }],
        }

    # Evaluate against the requested coordinate alone to derive its validated,
    # bound counterpart without letting a stale local alias affect the result.
    preliminary = evaluate(repo, ref, coordinate_refs={ref: remote_oid})
    record = preliminary.get("record")
    if not isinstance(record, Mapping):
        return preliminary
    canonical_ref = record["canonical_ref"]
    legacy_ref = announcement_ref(
        series_id_for_branch(record["series_branch"]),
        child_key_for_node_path(record["node_path"]),
        author_slug(record["author"]["email"]),
    )
    coordinates = git_wrapper.ls_remote(repo, remote, canonical_ref, legacy_ref)
    if not coordinates.get("ok"):
        return {
            "ok": False,
            "ref": ref,
            "state": "unavailable",
            "tip": remote_oid,
            "record": None,
            "errors": [{
                "type": "announcement_remote_unavailable",
                "detail": coordinates.get("detail", ""),
            }],
        }
    if coordinates["refs"].get(ref, "") != remote_oid:
        return {
            "ok": False,
            "ref": ref,
            "state": "unavailable",
            "tip": remote_oid,
            "record": None,
            "errors": [{
                "type": "announcement_remote_changed",
                "detail": "announcement coordinate changed during evaluation",
            }],
        }
    return evaluate(repo, ref, coordinate_refs=coordinates["refs"])


def announce(
    repo,
    remote: str,
    series_branch: str,
    *,
    node_path: str = ".",
    author_name: str,
    author_email: str,
) -> dict[str, Any]:
    """Announce authoring intent on the author's OWN ref. Pure visibility.

    Never checks whether anyone else announced or published: another family on
    the same series is intended competition (家族間重複=競争). The only reads
    are for picking a contribution branch NAME that is not already taken —
    output namespace deconfliction, not admission control: nobody is refused
    work by this function, ever.
    """
    identity = normalize_noreply_identity(author_name, author_email)
    if identity is None:
        return {"ok": False, "status": "author_identity_invalid", "email": author_email}
    slug = author_slug(identity["email"])
    if not slug:
        return {"ok": False, "status": "author_slug_empty", "email": author_email}
    series_id = series_id_for_branch(series_branch)
    child_key = child_key_for_node_path(node_path)
    legacy_ref = announcement_ref(series_id, child_key, slug)
    ref = canonical_announcement_ref(series_branch, node_path, identity["email"])
    series_ref = _series_ref(repo, remote, series_branch)
    series_head = git_wrapper.head_sha(repo, series_ref)
    if series_head is None:
        return {"ok": False, "status": "series_missing", "series_branch": series_branch, "ref": ref}

    # Freeze and apply producer readiness before branch-name discovery. That
    # discovery may fetch visibility refs into the local repository, so a
    # blocked canonical root must return before it can mutate even local refs.
    readiness_decision = producer_lifecycle.promise_readiness_decision(
        repo, series_head, series_branch
    )
    if readiness_decision is not None and readiness_decision.blocked:
        return {
            "ok": False,
            "status": "producer_lifecycle_not_ready",
            "ref": ref,
            "record": None,
            "detail": (
                readiness_decision.diagnostic.detail
                if readiness_decision.diagnostic is not None
                else "producer lifecycle readiness is closed"
            ),
            "authorability_decision": readiness_decision.as_dict(),
        }

    manifest_input = _registered_manifest_input(repo, series_head, node_path)
    if not manifest_input.get("ok"):
        return {
            "ok": False,
            "status": manifest_input["status"],
            "series_branch": series_branch,
            "node_path": node_path,
            "ref": ref,
            "detail": manifest_input.get("detail", ""),
        }

    contrib_branch = _contrib_branch_for(
        repo, remote, series_ref, series_id, node_path, child_key,
        manifest=manifest_input.get("manifest"),
    )
    if not contrib_branch.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "ref": ref, "detail": contrib_branch.get("detail", "")}
    record = build_record(
        series_branch=series_branch,
        node_path=node_path,
        series_head_at_announcement=series_head,
        contrib_branch=contrib_branch["branch"],
        author_name=identity["name"],
        author_email=identity["email"],
    )
    validated, errors = parse_announcement_record(json.dumps(record))
    if validated is None:
        return {"ok": False, "status": "announcement_record_invalid", "ref": ref, "errors": errors}

    initialization_options = (
        {"readiness_decision": readiness_decision}
        if readiness_decision is not None
        else {}
    )
    promise_initialization = producer_lifecycle.prepare_initialization(
        repo, series_head, record, **initialization_options
    )
    if promise_initialization.status == "blocked":
        decision = promise_initialization.decision
        return {
            "ok": False,
            "status": "producer_lifecycle_not_ready",
            "ref": ref,
            "record": None,
            "detail": promise_initialization.detail,
            "authorability_decision": decision.as_dict() if decision is not None else None,
        }
    if promise_initialization.status == "invalid":
        return {
            "ok": False,
            "status": "producer_promise_invalid",
            "ref": ref,
            "record": None,
            "detail": promise_initialization.detail,
        }
    if promise_initialization.active:
        try:
            producer_lifecycle.validate_initialization_cohort(
                promise_initialization
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "producer_promise_invalid",
                "ref": ref,
                "record": None,
                "detail": str(exc),
            }

    listed = git_wrapper.ls_remote(repo, remote, ref, legacy_ref)
    if not listed.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "ref": ref, "detail": listed.get("detail", "")}
    coordinates = announcement_coordinate_state(
        listed["refs"], canonical_ref=ref, legacy_ref=legacy_ref
    )
    expected = coordinates.canonical_oid
    legacy_expected = coordinates.legacy_oid
    if coordinates.state == "coexisting":
        return {
            "ok": False, "status": "announcement_coordinate_coexistence", "ref": ref,
            "legacy_ref": legacy_ref, "record": None,
        }
    if legacy_expected:
        legacy_preflight = _preflight_legacy_transfer(
            repo,
            remote,
            legacy_ref,
            expected_oid=legacy_expected,
            series_branch=series_branch,
            node_path=node_path,
            author_email=identity["email"],
        )
        if not legacy_preflight.get("ok"):
            return {
                **legacy_preflight,
                "ok": False,
                "ref": ref,
                "legacy_ref": legacy_ref,
                "record": None,
            }

    context, contract = _context_contract(node_path)
    try:
        prepared = BodyCodecClient().prepare(context, record, expected=contract)
        canonical = prepared.canonical.data.decode("utf-8", errors="strict")
    except Exception as exc:
        return {"ok": False, "status": "announcement_record_invalid", "ref": ref, "errors": [{"type": "codec_rejected", "detail": str(exc)}]}

    try:
        written = git_wrapper.create_ref_with_files(
            repo,
            ref,
            {ANNOUNCEMENT_RECORD_PATH: canonical},
            subject=f"authoring-intent: {series_id}" + (f" {child_key}" if child_key else ""),
            parent=series_head,
            identity=record["author"],
            # Preparing the body must not publish it at the local coordinate.
            # The ref becomes reachable only after the guarded remote create,
            # CAS, or atomic legacy transfer succeeds.
            update_ref=False,
        )
    except RuntimeError as exc:
        return {"ok": False, "status": "commit_failed", "ref": ref, "detail": str(exc)}
    if promise_initialization.active:
        if expected or legacy_expected:
            return {
                "ok": False,
                "status": "producer_promise_transition_required",
                "ref": ref,
                "record": None,
            }
        contribution_ref = f"refs/heads/{record['contrib_branch']}"
        contribution_base = _contribution_base(repo, remote)
        if contribution_base is None:
            return {
                "ok": False,
                "status": "contribution_base_missing",
                "ref": ref,
                "record": None,
            }
        try:
            promise_written = git_wrapper.create_ref_with_files(
                repo,
                contribution_ref,
                promise_initialization.files,
                subject=f"{producer_lifecycle.INITIAL_SUBJECT_PREFIX} {series_id}" + (f" {child_key}" if child_key else ""),
                parent=contribution_base,
                identity=record["author"],
                update_ref=False,
                inherit_parent_tree=True,
            )
        except RuntimeError as exc:
            return {"ok": False, "status": "commit_failed", "ref": ref, "detail": str(exc)}
        publication = git_wrapper.push_atomic_create_refs(
            repo,
            remote,
            {ref: written["commit"], contribution_ref: promise_written["commit"]},
        )
        if not publication.ok:
            return {
                "ok": False,
                "status": "atomic_promise_publication_rejected",
                "ref": ref,
                "contribution_ref": contribution_ref,
                "record": None,
                "publication": publication,
            }
        refreshed = _refresh_local_coordinate(repo, ref, written["commit"])
        if refreshed.get("ok"):
            refreshed = git_wrapper.update_ref(
                repo, contribution_ref, promise_written["commit"]
            )
        if not refreshed.get("ok"):
            return {
                "ok": False,
                "status": "local_refresh_failed",
                "ref": ref,
                "contribution_ref": contribution_ref,
                "record": None,
                "detail": refreshed.get("detail", ""),
                "publication": publication,
            }
        return {
            "ok": True,
            "status": "authoring_promise_initialized",
            "ref": ref,
            "contribution_ref": contribution_ref,
            "commit": written["commit"],
            "promise_commit": promise_written["commit"],
            "obligation_coordinates": dict(promise_initialization.obligation_coordinates),
            "record": record,
            "publication": publication,
        }
    if expected:
        # Re-announce over our own previous announcement (e.g. after the
        # series moved): CAS on our own observed tip. Same-identity processes
        # racing each other is the only way this rejects.
        pushed = git_wrapper.push_cas(repo, remote, ref, written["commit"], expected)
    elif legacy_expected:
        publication = git_wrapper.push_atomic_ref_transfer(
            repo, remote, create_ref=ref, commit=written["commit"],
            delete_ref=legacy_ref, expected_delete_oid=legacy_expected,
        )
        if publication.ok:
            refreshed = _refresh_local_coordinate(
                repo, ref, written["commit"], delete_ref=legacy_ref
            )
            if not refreshed.get("ok"):
                return {
                    "ok": False, "status": "local_refresh_failed", "ref": ref,
                    "legacy_ref": legacy_ref, "record": None,
                    "detail": refreshed.get("detail", ""), "publication": publication,
                }
            return {
                "ok": True, "status": "authoring_announcement_migrated", "ref": ref,
                "legacy_ref": legacy_ref, "commit": written["commit"], "record": record,
                "publication": publication,
            }
        return {
            "ok": False, "status": "atomic_transfer_rejected", "ref": ref,
            "legacy_ref": legacy_ref, "record": None, "publication": publication,
        }
    else:
        pushed = git_wrapper.push_create(repo, remote, ref, written["commit"])
    if pushed.get("ok"):
        publication = git_wrapper.GitPublicationResult(
            "updated" if expected else "created", ref, expected,
            commit_oid=written["commit"], ref_outcomes=((ref, "updated" if expected else "created"),),
            expected_ref_oids=((ref, expected),),
        )
        refreshed = _refresh_local_coordinate(repo, ref, written["commit"])
        if not refreshed.get("ok"):
            return {
                "ok": False, "status": "local_refresh_failed", "ref": ref,
                "record": None, "detail": refreshed.get("detail", ""),
                "publication": publication,
            }
        return {
            "ok": True, "status": "authoring_announced", "ref": ref,
            "commit": written["commit"], "record": record, "publication": publication,
        }
    status = "same_author_conflict" if pushed.get("status") == "rejected" else "push_failed"
    publication = git_wrapper.GitPublicationResult(
        "rejected", ref, expected,
        failure=git_wrapper.GitBodyFailure("GIT_PUBLICATION", ref, "create-or-compare-and-swap"),
        ref_outcomes=((ref, "rejected"),),
        expected_ref_oids=((ref, expected),),
    )
    return {
        "ok": False, "status": status, "ref": ref,
        "detail": pushed.get("detail", ""), "publication": publication,
    }


def withdraw(repo, remote: str, ref: str, *, author_email: str) -> dict[str, Any]:
    """Withdraw authoring intent = REMOVE the announcement ref (addendum law).

    Only the announcing author withdraws its own ref; there is no third-party
    release because there is nothing to release.
    """
    listed = git_wrapper.ls_remote(repo, remote, ref)
    if not listed.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "ref": ref, "detail": listed.get("detail", "")}
    remote_sha = listed["refs"].get(ref, "")
    if not remote_sha:
        return {"ok": False, "status": "announcement_missing", "ref": ref}
    fetched = git_wrapper.fetch_refspecs(repo, remote, [f"+{ref}:{ref}"])
    if not fetched.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "ref": ref, "detail": fetched.get("detail", "")}
    canonical_text = git_wrapper.show_file(repo, ref, ANNOUNCEMENT_RECORD_PATH)
    legacy_text = git_wrapper.show_compatibility_file(repo, ref, LEGACY_ANNOUNCEMENT_RECORD_PATH)
    record, errors = _parse_body(canonical_text if canonical_text is not None else legacy_text)
    if record is None:
        return {"ok": False, "status": "announcement_record_invalid", "ref": ref, "errors": errors}
    canonical_ref = record["canonical_ref"]
    legacy_ref = announcement_ref(
        series_id_for_branch(record["series_branch"]), child_key_for_node_path(record["node_path"]),
        author_slug(record["author"]["email"]),
    )
    counterpart = legacy_ref if ref == canonical_ref else canonical_ref
    if ref not in {canonical_ref, legacy_ref}:
        return {
            "ok": False, "status": "announcement_ref_binding_invalid",
            "ref": ref, "record": None,
        }
    listed_coordinates = git_wrapper.ls_remote(repo, remote, ref, counterpart)
    if not listed_coordinates.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "ref": ref, "detail": listed_coordinates.get("detail", "")}
    coordinates = announcement_coordinate_state(
        listed_coordinates["refs"], canonical_ref=canonical_ref, legacy_ref=legacy_ref
    )
    if coordinates.state == "coexisting":
        return {"ok": False, "status": "announcement_coordinate_coexistence", "ref": ref, "record": None}
    if not coordinates.releases_body or coordinates.sole_ref != ref:
        return {"ok": False, "status": "announcement_missing", "ref": ref}
    if coordinates.sole_oid != remote_sha:
        return {
            "ok": False, "status": "withdraw_lost", "ref": ref,
            "detail": "announcement coordinate changed during withdrawal",
        }
    binding_errors = _record_binding_errors(repo, remote_sha, record)
    if binding_errors:
        return {
            "ok": False, "status": "announcement_record_invalid", "ref": ref,
            "record": None, "errors": binding_errors,
        }
    normalized = normalize_noreply_identity(record["author"]["name"], author_email)
    if normalized is None or record["author"]["email"] != normalized["email"]:
        return {"ok": False, "status": "author_mismatch", "ref": ref, "holder": record["author"]}
    deleted = git_wrapper.push_delete(repo, remote, ref, remote_sha)
    if not deleted.get("ok"):
        status = "withdraw_lost" if deleted.get("status") == "rejected" else "push_failed"
        publication = git_wrapper.GitPublicationResult(
            "rejected", ref, remote_sha,
            failure=git_wrapper.GitBodyFailure("GIT_PUBLICATION", ref, "exact-coordinate-delete"),
            ref_outcomes=((ref, "rejected"),),
            expected_ref_oids=((ref, remote_sha),),
        )
        return {
            "ok": False, "status": status, "ref": ref,
            "detail": deleted.get("detail", ""), "publication": publication,
        }
    publication = git_wrapper.GitPublicationResult(
        "deleted", ref, remote_sha, atomic=False, ref_outcomes=((ref, "deleted"),),
        expected_ref_oids=((ref, remote_sha),),
    )
    refreshed = git_wrapper.delete_local_ref(repo, ref)
    if not refreshed.get("ok"):
        return {
            "ok": False, "status": "local_refresh_failed", "ref": ref,
            "record": None, "detail": refreshed.get("detail", ""),
            "publication": publication,
        }
    return {
        "ok": True, "status": "authoring_intent_withdrawn",
        "ref": ref, "publication": publication,
    }


def list_for_address(repo, series_id: str, child_key: str = "") -> list[dict[str, Any]]:
    """Evaluate every announcement on one series address (visibility only)."""
    legacy_refs = refs_for_address(
        git_wrapper.list_refs(repo, address_ref_prefix(series_id, child_key).rstrip("/")),
        series_id,
        child_key,
    )
    refs = sorted({*legacy_refs, *git_wrapper.list_refs(repo, CANONICAL_REF_PREFIX.rstrip("/"))})
    wanted_node = "." if not child_key else f"sub/{child_key}"
    evaluations = [evaluate(repo, ref) for ref in refs]
    return [
        value for value in evaluations
        if isinstance(value.get("record"), Mapping)
        and series_id_for_branch(value["record"]["series_branch"]) == series_id
        and value["record"]["node_path"] == wanted_node
    ]


def find_for_contrib(repo, contrib_branch: str) -> dict[str, Any] | None:
    """Return the authoring announcement naming contrib_branch, or None.

    This is a PROVENANCE lookup (which series/node does this published branch
    implement, under which author identity) — not a gate: it admits published
    work into acceptance, it never refuses an author the right to work.
    """
    for ref in git_wrapper.list_refs(repo, ANNOUNCEMENT_REF_PREFIX.rstrip("/")):
        evaluation = evaluate(repo, ref)
        record = evaluation.get("record")
        if (
            evaluation["state"] == "authoring"
            and isinstance(record, Mapping)
            and record["contrib_branch"] == contrib_branch
            and contribution_author_matches(repo, contrib_branch, record)
        ):
            return evaluation
    return None


def contribution_author_matches(
    repo, contrib_branch: str, record: Mapping[str, Any]
) -> bool:
    """Bind a contribution tip to the announcement's full normalized identity.

    Email-only matching is insufficient: the registered announcement variant
    binds both name and noreply address, and every consumer must apply the same
    identity contract before routing, projecting, submitting, or accepting the
    contribution branch.
    """
    identity_ref = contrib_branch
    subjects = git_wrapper.log_subjects(repo, contrib_branch)
    subject = subjects[0] if subjects else ""
    if subject in contributor_handoff.ACCEPTANCE_SUBJECTS:
        # The independent judge appends the verdict marker.  The contribution
        # identity remains the author of the implementation tip immediately
        # below that canonical-side marker.  Only the two registered verdict
        # subjects receive this treatment; an arbitrary acceptance-like subject
        # is still an ordinary contributor commit.
        identity_ref = f"{contrib_branch}^"
        subject = git_wrapper.commit_subject(repo, identity_ref)
    if subject.startswith("patch: implementation result for "):
        # ImplementationResult is an engine/harness-authored observation.
        # Submission identity remains the producer immediately below it (a
        # completion assertion for migrated contributions, otherwise the last
        # producer-authored implementation commit).
        identity_ref = f"{identity_ref}^"
    author = git_wrapper.commit_author(repo, identity_ref)
    if not isinstance(author, Mapping):
        return False
    normalized = normalize_noreply_identity(
        str(author.get("name", "")), str(author.get("email", ""))
    )
    announced = record.get("author")
    return (
        normalized is not None
        and isinstance(announced, Mapping)
        and normalized == dict(announced)
    )


def _series_ref(repo, remote: str, series_branch: str) -> str:
    remote_tracking = f"refs/remotes/{remote}/{series_branch}"
    if git_wrapper.head_sha(repo, remote_tracking) is not None:
        return remote_tracking
    return series_branch


def _contribution_base(repo, remote: str) -> str | None:
    default_name = git_wrapper.remote_default_branch(repo, remote)
    if default_name:
        remote_tracking = f"refs/remotes/{remote}/{default_name}"
        if git_wrapper.head_sha(repo, remote_tracking) is not None:
            return remote_tracking
        if git_wrapper.head_sha(repo, default_name) is not None:
            return default_name
    try:
        fallback = git_wrapper.default_branch(repo)
    except RuntimeError:
        return None
    return fallback if git_wrapper.head_sha(repo, fallback) is not None else None


def _record_binding_errors(repo, tip: str, record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Bind a decoded announcement to its Git author, lineage, and route."""
    if (
        git_wrapper.commit_author(repo, tip) != record["author"]
        or git_wrapper.parent_commits(repo, tip) != [record["parent"]]
    ):
        return [{"type": "announcement_commit_binding_invalid"}]
    manifest_input = _registered_manifest_input(
        repo, record["parent"], record["node_path"]
    )
    if not manifest_input.get("ok"):
        return [{
            "type": "announcement_manifest_input_invalid",
            "status": manifest_input["status"],
            "detail": manifest_input.get("detail", ""),
        }]
    if not _contrib_branch_is_bound(record, manifest_input.get("manifest")):
        return [{"type": "announcement_contrib_branch_binding_invalid"}]
    return []


def _preflight_legacy_transfer(
    repo,
    remote: str,
    legacy_ref: str,
    *,
    expected_oid: str,
    series_branch: str,
    node_path: str,
    author_email: str,
) -> dict[str, Any]:
    """Prove the exact observed legacy ref belongs to this full identity."""
    fetched = git_wrapper.fetch_refspecs(repo, remote, [f"+{legacy_ref}:{legacy_ref}"])
    if not fetched.get("ok"):
        return {
            "status": "remote_unavailable",
            "detail": fetched.get("detail", ""),
        }
    if git_wrapper.head_sha(repo, legacy_ref) != expected_oid:
        return {
            "status": "legacy_observation_changed",
            "detail": "legacy ref changed during transfer preflight",
        }
    evaluation = evaluate(repo, legacy_ref)
    record = evaluation.get("record")
    if evaluation.get("state") != "authoring" or not isinstance(record, Mapping):
        return {
            "status": "legacy_announcement_invalid",
            "errors": evaluation.get("errors", []),
        }
    if (
        record["series_branch"] != series_branch
        or record["node_path"] != node_path
        or record["author"]["email"] != author_email
    ):
        return {
            "status": "announcement_identity_collision",
            "holder": dict(record["author"]),
        }
    return {"ok": True}


def _contrib_branch_is_bound(
    record: Mapping[str, Any], manifest: Mapping[str, Any] | None
) -> bool:
    declared = manifest.get("contrib_branch") if isinstance(manifest, Mapping) else None
    if isinstance(declared, str) and declared.startswith(CONTRIB_BRANCH_PREFIX):
        base = declared
    else:
        series_id = series_id_for_branch(str(record["series_branch"]))
        child_key = child_key_for_node_path(str(record["node_path"]))
        base = f"{CONTRIB_BRANCH_PREFIX}{series_id}" + (f"-{child_key}" if child_key else "")
    branch = str(record["contrib_branch"])
    return branch == base or re.fullmatch(re.escape(base) + r"-r(?:[2-9]|[1-9][0-9]+)", branch) is not None


def _refresh_local_coordinate(
    repo, ref: str, commit: str, *, delete_ref: str = ""
) -> dict[str, Any]:
    """Refresh non-authoritative local coordinates after remote publication."""
    updated = git_wrapper.update_ref(repo, ref, commit)
    if not updated.get("ok"):
        return updated
    if delete_ref:
        deleted = git_wrapper.delete_local_ref(repo, delete_ref)
        if not deleted.get("ok"):
            return deleted
    return {"ok": True}


def _registered_manifest_input(repo, series_head: str, node_path: str) -> dict[str, Any]:
    """Resolve announcement routing from registered manifests at one frozen OID.

    A branch without the canonical root coordinate is a historical pre-cutover
    input and keeps its existing announcement behavior.  Once the canonical
    root exists, the selected root/child manifest must be admitted by the
    registered codec; a malformed or missing child cannot silently fall back
    to path-derived work routing.
    """
    selected = network_bodies.resolve_manifest_input(repo, series_head, node_path)
    if selected.state == "legacy":
        return {"ok": True, "manifest": None, "legacy": True}
    if selected.state == "registered":
        return {"ok": True, "manifest": selected.manifest, "legacy": False}
    status = (
        "announcement_manifest_missing"
        if selected.state == "missing"
        else "announcement_manifest_invalid"
    )
    return {"ok": False, "status": status, "detail": selected.detail}


def _contrib_branch_for(
    repo,
    remote: str,
    series_ref: str,
    series_id: str,
    node_path: str,
    child_key: str,
    *,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if node_path == ".":
        base = f"{CONTRIB_BRANCH_PREFIX}{series_id}"
    else:
        base = f"{CONTRIB_BRANCH_PREFIX}{series_id}-{child_key}"
        declared = manifest.get("contrib_branch") if isinstance(manifest, Mapping) else None
        if manifest is None:
            # Strictly historical compatibility: canonical network trees have
            # already supplied a validated projection above.
            manifest_text = git_wrapper.show_file(
                repo, series_ref, f"{node_path}/patch-series-manifest.json"
            )
            if manifest_text is not None:
                try:
                    historical = json.loads(manifest_text)
                except json.JSONDecodeError:
                    historical = None
                declared = historical.get("contrib_branch") if isinstance(historical, Mapping) else None
        if isinstance(declared, str) and declared.startswith(CONTRIB_BRANCH_PREFIX):
            base = declared
    listed = git_wrapper.ls_remote(repo, remote, f"refs/heads/{base}*", f"{ANNOUNCEMENT_REF_PREFIX.rstrip('/')}/*")
    if not listed.get("ok"):
        return {"ok": False, "detail": listed.get("detail", "")}
    taken = {ref for ref in listed["refs"] if ref.startswith("refs/heads/")}
    # Visibility-informed naming: other families' ANNOUNCED branch names are
    # also treated as taken so concurrent competitors pick distinct output
    # names up front. This consults announcements for NAMING only — never to
    # refuse or defer anyone's work (no-lock law). A name race that slips
    # through is still resolved fail-closed at submission (create-only push).
    remote_announcements = sorted(
        ref for ref in listed["refs"] if ref.startswith(ANNOUNCEMENT_REF_PREFIX)
    )
    # Synchronize the complete coordinate set before evaluating any body.
    # Evaluating while fetching one ref at a time can briefly release the
    # canonical half of a canonical/legacy coexistence before its counterpart
    # is present locally. Routing is one of the announcement consumers, so it
    # must share evaluate()'s sole-coordinate, commit-binding, and registered-
    # manifest decision rather than parsing bodies through a side door.
    announcement_snapshot_complete = True
    for ref in remote_announcements:
        fetched = git_wrapper.fetch_refspecs(repo, remote, [f"+{ref}:{ref}"])
        if not fetched.get("ok"):
            announcement_snapshot_complete = False
    local_announcements = set(
        git_wrapper.list_refs(repo, ANNOUNCEMENT_REF_PREFIX.rstrip("/"))
    )
    for ref in sorted(local_announcements - set(remote_announcements)):
        git_wrapper.delete_local_ref(repo, ref)
    if announcement_snapshot_complete:
        for ref in remote_announcements:
            evaluation = evaluate(repo, ref)
            record = evaluation.get("record")
            if evaluation.get("state") == "authoring" and isinstance(record, Mapping):
                taken.add(f"refs/heads/{record['contrib_branch']}")
    if f"refs/heads/{base}" not in taken:
        return {"ok": True, "branch": base}
    # Memento: a competitor's published or announced contribution branch is
    # NEVER force-taken — rewriting another author's published history violates
    # the public-history mutability boundary. Competing families publish
    # sibling suffixed branches; review resolves accept/superseded downstream.
    suffix = 2
    while f"refs/heads/{base}-r{suffix}" in taken:
        suffix += 1
    return {"ok": True, "branch": f"{base}-r{suffix}"}
