"""Canonical codec boundary for the patch-series network publication tree.

The network is prepared as one temporary aggregate.  Historical JSON paths
remain strict read inputs, while every newly prepared member is addressed by
its canonical ``.cue`` path.  The aggregate itself is never written.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Mapping

from ai_org.body_codec import BodyCodecClient, ContractIdentity, PreparedBodySet


@dataclass(frozen=True, slots=True)
class NetworkMember:
    context: str
    contract: ContractIdentity
    canonical_path: str
    historical_path: str
    prepared: PreparedBodySet


@dataclass(frozen=True, slots=True)
class PreparedNetworkPublication:
    """A fully preflighted, immutable-tree-shaped publication value."""

    files: Mapping[str, str]
    members: tuple[NetworkMember, ...]
    carrier_recipes: tuple[PreparedBodySet, ...]
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ManifestInput:
    """One root or child manifest selected from a frozen publication tree."""

    state: str
    frozen_oid: str | None
    node_path: str
    manifest: Mapping[str, Any] | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state in {"legacy", "registered"}


_API = "ai-org-cue-body-v1"
_SPECS: dict[str, tuple[str, ContractIdentity]] = {
    "root_manifest": ("root-network-node-manifest-v1", ContractIdentity(_API, "NetworkNodeManifest", "network-root")),
    "child_manifest": ("child-network-node-manifest-v1", ContractIdentity(_API, "NetworkNodeManifest", "network-child")),
    "ledger": ("series-coverage-ledger-v1", ContractIdentity(_API, "SeriesCoverageLedger", "network-root")),
    "scope_decomposition": ("series-scope-decomposition-v1", ContractIdentity(_API, "SeriesScopeDecomposition", "patch-series-root-preview")),
    "rollup": ("patch-queue-status-rollup-v1", ContractIdentity(_API, "PatchQueueStatusRollup", "network-control-read")),
    "request": ("maintainer-series-request-v1", ContractIdentity(_API, "MaintainerSeriesRequest", "network-child")),
    "metadata": ("patch-series-metadata-v1", ContractIdentity(_API, "PatchSeriesMetadata", "network-root-and-child")),
    "child_cover": ("child-patch-series-cover-letter-v1", ContractIdentity(_API, "PatchSeriesCoverLetter", "patch-series-child")),
    "child_approach": ("child-technical-approach-tree-v1", ContractIdentity(_API, "TechnicalApproachTree", "patch-series-child")),
    "recipe": ("lineage-carrier-recipe-v1", ContractIdentity(_API, "LineageCarrierRecipe", "network-publication")),
}

_BASENAMES = {
    "series-coverage-ledger.json": "ledger",
    "series-scope-decomposition.json": "scope_decomposition",
    "patch-queue-status-rollup.json": "rollup",
    "maintainer-series-request.json": "request",
    "patch-series-metadata.json": "metadata",
    "lineage-carrier-recipe.json": "recipe",
}

# Source order is contract data for the lineage carrier, not an accident of a
# caller's mapping implementation. Repeated child roles are ordered by their
# canonical tree coordinate after this root-to-leaf role order is applied.
_ROLE_ORDER = {
    role: index
    for index, role in enumerate(
        (
            "root_manifest",
            "child_manifest",
            "ledger",
            "scope_decomposition",
            "rollup",
            "request",
            "metadata",
            "child_cover",
            "child_approach",
            "recipe",
        )
    )
}


def classify_path(path: str) -> tuple[str, str, str] | None:
    """Return ``(role, canonical, historical)`` for one network coordinate."""

    normalized = str(PurePosixPath(path))
    item = PurePosixPath(normalized)
    name = item.name
    historical_name = item.with_suffix(".json").name if name.endswith(".cue") else name
    historical = str(item.with_name(historical_name))
    parent_is_root = str(item.parent) == "."
    role = _BASENAMES.get(historical_name)
    if historical_name == "patch-series-manifest.json":
        role = "root_manifest" if parent_is_root else "child_manifest"
    elif historical_name == "patch-series-cover-letter.json" and not parent_is_root:
        role = "child_cover"
    elif historical_name == "technical-approach-plan.json" and not parent_is_root:
        role = "child_approach"
    if role is None:
        return None
    canonical = str(PurePosixPath(historical).with_suffix(".cue"))
    return role, canonical, historical


def canonical_update_contract(path: str) -> tuple[str, ContractIdentity] | None:
    """Return the typed contract for an admitted child cover/approach update.

    Review and reform publication may receive canonical bytes prepared by the
    network cohort.  Exposing only these two update roles keeps that preflight
    from becoming a second network publication authority.
    """

    classified = classify_path(path)
    if classified is None:
        return None
    role, canonical, _historical = classified
    if path != canonical or role not in {"child_cover", "child_approach"}:
        return None
    return _SPECS[role]


def prepare_network_publication(
    files: Mapping[str, Any], *, previous_ledger_revision: int | None = None,
    client: BodyCodecClient | None = None,
) -> PreparedNetworkPublication:
    """Preflight every changed network member before returning any tree data."""

    codec = client or BodyCodecClient()
    output: dict[str, str] = {}
    members: list[NetworkMember] = []
    aliases: list[str] = []
    classified_inputs: list[tuple[int, str, str, str, Any]] = []
    for path, value in files.items():
        classified = classify_path(path)
        if classified is None:
            raise ValueError(f"network-publication:unregistered-path:{path}")
        role, canonical, historical = classified
        classified_inputs.append((_ROLE_ORDER[role], canonical, role, historical, value))

    for _order, canonical, role, historical, value in sorted(classified_inputs):
        if canonical in output:
            raise ValueError(f"network-publication:duplicate-target:{canonical}")
        if role == "recipe":
            raise ValueError("network-publication:temporary-recipe-cannot-be-published")
        context, contract = _SPECS[role]
        if isinstance(value, PreparedBodySet):
            if value.context_id != context or value.contract != contract:
                raise ValueError(
                    f"network-publication:prepared-contract-mismatch:{canonical}"
                )
            codec.parse(context, value.canonical.data, expected=contract)
            prepared = value
        else:
            if not isinstance(value, Mapping):
                raise TypeError(f"{historical}: network body must be a mapping")
            prepared = codec.prepare(context, value, expected=contract)
        output[canonical] = prepared.canonical.data.decode("utf-8", errors="strict")
        members.append(NetworkMember(context, contract, canonical, historical, prepared))
        if historical != canonical:
            aliases.append(historical)

    _validate_cohort_shape(members)
    _validate_pairwise(codec, members)
    _validate_ledger_revision(codec, members, previous_ledger_revision)
    # Recipes are independently typed evidence over the already emitted body
    # bytes. They remain temporary: persisting them would create a second tree.
    recipes = tuple(_prepare_recipe(codec, member) for member in members if member.context != _SPECS["recipe"][0])
    return PreparedNetworkPublication(
        files=MappingProxyType(dict(output)),
        members=tuple(members),
        carrier_recipes=recipes,
        aliases=tuple(sorted(set(aliases))),
    )


def validate_carrier_recipes(
    members: tuple[NetworkMember, ...] | list[NetworkMember],
    recipes: tuple[PreparedBodySet, ...] | list[PreparedBodySet],
    *,
    client: BodyCodecClient | None = None,
) -> None:
    """Require one exact temporary lineage carrier for every network member.

    Carrier order is meaningful because it binds each recipe to one canonical
    tree coordinate. Re-deriving the recipes also verifies source/target
    contracts, member bytes, projection profiles, and codec-authority digest;
    a same-length substituted or stale tuple therefore cannot reach Git CAS.
    """

    if len(recipes) != len(members):
        raise ValueError("network-publication:carrier-count-mismatch")
    codec = client or BodyCodecClient()
    for index, (member, actual) in enumerate(zip(members, recipes, strict=True)):
        if not isinstance(actual, PreparedBodySet):
            raise TypeError(
                f"network-publication:carrier-type-mismatch:{index}:"
                f"{member.canonical_path}"
            )
        expected = _prepare_recipe(codec, member)
        if (
            actual.context_id != expected.context_id
            or actual.contract != expected.contract
            or actual.canonical.data != expected.canonical.data
        ):
            raise ValueError(
                f"network-publication:carrier-mismatch:{index}:"
                f"{member.canonical_path}"
            )


def _validate_cohort_shape(members: list[NetworkMember]) -> None:
    """Require the operational tree, while keeping recipes non-durable."""

    roles: dict[str, list[NetworkMember]] = {}
    for member in members:
        classified = classify_path(member.historical_path)
        assert classified is not None
        roles.setdefault(classified[0], []).append(member)
    for role in ("root_manifest", "ledger", "rollup"):
        if len(roles.get(role, ())) != 1:
            raise ValueError(f"network-publication:incomplete-root-cohort:{role}")

    child_dirs = {
        str(PurePosixPath(member.historical_path).parent)
        for member in roles.get("child_manifest", ())
    }
    for role in ("request", "child_cover", "child_approach"):
        role_dirs = {
            str(PurePosixPath(member.historical_path).parent)
            for member in roles.get(role, ())
        }
        if role_dirs != child_dirs or len(roles.get(role, ())) != len(child_dirs):
            raise ValueError(f"network-publication:incomplete-child-cohort:{role}")
    metadata_dirs = [
        str(PurePosixPath(member.historical_path).parent)
        for member in roles.get("metadata", ())
    ]
    if "." not in metadata_dirs:
        raise ValueError("network-publication:incomplete-root-cohort:metadata")
    if set(metadata_dirs) != child_dirs | {"."} or len(metadata_dirs) != len(set(metadata_dirs)):
        raise ValueError("network-publication:incomplete-child-cohort:metadata")


def _validate_ledger_revision(
    codec: BodyCodecClient,
    members: list[NetworkMember],
    previous_ledger_revision: int | None,
) -> None:
    """Treat ledger_revision as monotonic mutable state, never a selector."""

    if previous_ledger_revision is None:
        return
    ledger = next(member for member in members if member.context == _SPECS["ledger"][0])
    body = codec.parse(ledger.context, ledger.prepared.canonical.data, expected=ledger.contract)
    revision = body["ledger_revision"]
    current = revision.as_int_exact() if hasattr(revision, "as_int_exact") else int(revision)
    if current < previous_ledger_revision:
        raise ValueError(
            "network-publication:ledger-revision-rollback:"
            f"{previous_ledger_revision}:{current}"
        )


def _validate_pairwise(codec: BodyCodecClient, members: list[NetworkMember]) -> None:
    """Reject mixed root/ledger/rollup or mismatched child carrier views."""

    values: dict[str, list[tuple[NetworkMember, Mapping[str, Any]]]] = {}
    for member in members:
        role = classify_path(member.historical_path)[0]  # type: ignore[index]
        projection = codec.parse(
            member.context, member.prepared.canonical.data, expected=member.contract
        )
        values.setdefault(role, []).append((member, projection))

    root = _single(values, "root_manifest")
    ledger = _single(values, "ledger")
    rollup = _single(values, "rollup")
    root_children = _keyed_children(root.get("children", [])) if root else {}
    ledger_children = _keyed_children(ledger.get("children", [])) if ledger else {}
    if root and ledger and set(root_children) != set(ledger_children):
        raise ValueError("network-publication:pair-mismatch:root-ledger-children")
    if root and ledger:
        for key in sorted(root_children):
            _same_fields(
                root_children[key], ledger_children[key],
                ("child_key", "node_path", "lifecycle_status", "edges"),
                f"root-ledger-child:{key}",
            )
    if rollup and root:
        if _keyed_children(rollup.get("children", [])) != root_children:
            raise ValueError("network-publication:pair-mismatch:root-rollup-children")
    if rollup and ledger and rollup.get("coverage") != ledger.get("coverage"):
        raise ValueError("network-publication:pair-mismatch:ledger-rollup-coverage")

    child_by_dir: dict[str, Mapping[str, Any]] = {}
    for member, child in values.get("child_manifest", []):
        directory = str(PurePosixPath(member.historical_path).parent)
        if child.get("node_path") != directory:
            raise ValueError(
                "network-publication:pair-mismatch:"
                f"child-coordinate:{directory}:{child.get('node_path')}"
            )
        child_by_dir[directory] = child
        key = str(child.get("child_key") or "")
        if root and key not in root_children:
            raise ValueError(f"network-publication:pair-mismatch:child-root:{key}")
        if ledger and key not in ledger_children:
            raise ValueError(f"network-publication:pair-mismatch:child-ledger:{key}")
        for view, label in ((root_children.get(key), "root"), (ledger_children.get(key), "ledger")):
            if view is not None:
                fields = (
                    ("child_key", "node_path")
                    if child.get("lifecycle_status") == "stale"
                    else ("child_key", "node_path", "lifecycle_status", "edges")
                )
                _same_fields(child, view, fields, f"child-{label}:{key}")
        ledger_view = ledger_children.get(key)
        if ledger_view is not None:
            _same_fields(
                child,
                ledger_view,
                ("id", "child_key", "node_path", "contrib_branch"),
                f"child-ledger-authority:{key}",
            )
            # A stale child intentionally retains the ledger's previous scope
            # assignment while revalidation compares it with the child's new
            # proposal. Stable identity, branch, and write authority still bind.
            if child.get("lifecycle_status") != "stale":
                _same_fields(
                    child,
                    ledger_view,
                    ("scope_item_ids",),
                    f"child-ledger-authority:{key}",
                )
            write_scope = child.get("write_scope")
            if not isinstance(write_scope, Mapping) or (
                write_scope.get("allowed_subtree") != ledger_view.get("allowed_subtree")
            ):
                raise ValueError(
                    f"network-publication:pair-mismatch:child-ledger-write-scope:{key}"
                )
    request_by_dir = {
        str(PurePosixPath(member.historical_path).parent): body
        for member, body in values.get("request", [])
    }
    cover_by_dir = {
        str(PurePosixPath(member.historical_path).parent): body
        for member, body in values.get("child_cover", [])
    }
    for directory, request in request_by_dir.items():
        child = child_by_dir.get(directory)
        provenance = request.get("provenance")
        if child is not None and (
            not isinstance(provenance, Mapping)
            or provenance.get("child_key") != child.get("child_key")
            or provenance.get("parent_branch") != child.get("parent_branch")
        ):
            raise ValueError(f"network-publication:pair-mismatch:request-child:{directory}")
        if directory in cover_by_dir and request.get("request") != cover_by_dir[directory]:
            raise ValueError(f"network-publication:pair-mismatch:request-cover:{directory}")
    for member, metadata in values.get("metadata", []):
        directory = str(PurePosixPath(member.historical_path).parent)
        child = child_by_dir.get(directory)
        if child is not None:
            _same_fields(metadata, child, ("lifecycle_status",), f"metadata-child:{directory}")
            if metadata.get("id") is not None and metadata.get("id") != child.get("id"):
                raise ValueError(f"network-publication:pair-mismatch:metadata-child-id:{directory}")
        # Root metadata records the patch-series review lifecycle while the root
        # manifest records network activity. They share a tree, not a status
        # authority, so their existing decisions are intentionally not equated.
    for member, approach in values.get("child_approach", []):
        directory = str(PurePosixPath(member.historical_path).parent)
        child = child_by_dir.get(directory)
        lineage = approach.get("lineage_child")
        if child is not None and isinstance(lineage, Mapping):
            _same_fields(lineage, child, ("id", "lifecycle_status"), f"approach-child:{directory}")


def _single(values, role: str) -> Mapping[str, Any] | None:
    entries = values.get(role, [])
    if len(entries) > 1:
        raise ValueError(f"network-publication:duplicate-role:{role}")
    return entries[0][1] if entries else None


def _keyed_children(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for child in value:
        if not isinstance(child, Mapping) or not isinstance(child.get("child_key"), str):
            raise ValueError("network-publication:pair-mismatch:child-shape")
        key = str(child["child_key"])
        if key in result:
            raise ValueError(f"network-publication:pair-mismatch:duplicate-child:{key}")
        result[key] = child
    return result


def _same_fields(left, right, fields, label: str) -> None:
    if any(left.get(field) != right.get(field) for field in fields):
        raise ValueError(f"network-publication:pair-mismatch:{label}")


def read_network_body(
    repo: str | Path,
    ref: str,
    path: str,
    *,
    client: BodyCodecClient | None = None,
) -> dict[str, Any] | None:
    """Read canonical or one strict historical body from a frozen tree."""

    # Resolve the outer Git transport lazily. The body boundary is also used by
    # git_wrapper's prepared publication API, so a package import here would
    # create an architectural dependency cycle.
    git_wrapper = importlib.import_module("ai_org.git_wrapper")

    classified = classify_path(path)
    if classified is None:
        raise ValueError(f"network-publication:unregistered-path:{path}")
    role, canonical, historical = classified
    frozen = git_wrapper.head_sha(repo, ref)
    if frozen is None:
        return None
    candidates = [
        (candidate, git_wrapper.show_file(repo, frozen, candidate))
        for candidate in (canonical, historical)
    ]
    present = [(candidate, raw) for candidate, raw in candidates if raw is not None]
    if not present:
        return None
    if len(present) != 1:
        raise ValueError(f"network-publication:ambiguous-coordinate:{historical}")
    context, contract = _SPECS[role]
    return dict((client or BodyCodecClient()).parse(context, present[0][1].encode("utf-8"), expected=contract))


def resolve_manifest_input(
    repo: str | Path,
    ref: str,
    node_path: str,
    *,
    client: BodyCodecClient | None = None,
) -> ManifestInput:
    """Select a registered root/child variant at one immutable Git OID.

    A tree without the canonical root coordinate is a historical input and is
    deliberately left to its compatibility consumer. The canonical root is
    the cutover marker: after it exists, a missing, ambiguous, malformed, or
    incorrectly addressed child fails closed instead of falling back to a
    path-derived route.
    """

    git_wrapper = importlib.import_module("ai_org.git_wrapper")
    frozen = git_wrapper.head_sha(repo, ref)
    if frozen is None:
        return ManifestInput("missing", None, node_path, detail="publication ref is missing")
    if node_path != "." and re.fullmatch(r"sub/[a-z0-9_]+", node_path) is None:
        return ManifestInput("invalid", frozen, node_path, detail="invalid manifest node_path")

    root_classification = classify_path("patch-series-manifest.json")
    assert root_classification is not None
    _role, canonical_root, _historical_root = root_classification
    if git_wrapper.show_file(repo, frozen, canonical_root) is None:
        return ManifestInput("legacy", frozen, node_path)

    selected_path = (
        "patch-series-manifest.json"
        if node_path == "."
        else f"{node_path}/patch-series-manifest.json"
    )
    try:
        # Always validate the root first: a child does not form an independent
        # authority tree merely because its coordinate can be decoded.
        root = read_network_body(repo, frozen, "patch-series-manifest.json", client=client)
        selected = root if node_path == "." else read_network_body(
            repo, frozen, selected_path, client=client
        )
        if root is None or selected is None:
            return ManifestInput(
                "missing", frozen, node_path,
                detail=f"registered network has no manifest for {node_path}",
            )
        _validate_grounded_manifest_input(
            repo, frozen, node_path, root, selected, client=client
        )
    except Exception as exc:
        return ManifestInput("invalid", frozen, node_path, detail=str(exc))
    if root.get("node_path") != "." or selected.get("node_path") != node_path:
        return ManifestInput(
            "invalid", frozen, node_path,
            detail=f"manifest node_path does not bind {node_path}",
        )
    return ManifestInput("registered", frozen, node_path, dict(selected))


def _validate_grounded_manifest_input(
    repo: str | Path,
    frozen: str,
    node_path: str,
    root: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    client: BodyCodecClient | None,
) -> None:
    """Ground discovery in the complete immutable publication unit.

    The rollup is deliberately only a control read: it must agree with the
    root and ledger, but it cannot make an orphan child authoritative.  A
    canonical root is the cohort cutover marker, so missing or mixed cohort
    members fail closed instead of reviving a historical path fallback.
    """

    ledger = read_network_body(
        repo, frozen, "series-coverage-ledger.json", client=client
    )
    rollup = read_network_body(
        repo, frozen, "patch-queue-status-rollup.json", client=client
    )
    root_metadata = read_network_body(
        repo, frozen, "patch-series-metadata.json", client=client
    )
    if ledger is None or rollup is None or root_metadata is None:
        raise ValueError("network-publication:incomplete-grounded-root-cohort")

    root_children = _keyed_children(root.get("children", []))
    ledger_children = _keyed_children(ledger.get("children", []))
    rollup_children = _keyed_children(rollup.get("children", []))
    if set(root_children) != set(ledger_children):
        raise ValueError("network-publication:grounding-mismatch:root-ledger-children")
    if root_children != rollup_children:
        raise ValueError("network-publication:grounding-mismatch:root-rollup-children")
    if ledger.get("coverage") != rollup.get("coverage"):
        raise ValueError("network-publication:grounding-mismatch:ledger-rollup-coverage")
    for key, projection in root_children.items():
        _same_fields(
            projection,
            ledger_children[key],
            ("child_key", "node_path", "lifecycle_status", "edges"),
            f"grounded-root-ledger:{key}",
        )

    grounded_children: dict[str, Mapping[str, Any]] = {}
    for key, root_view in root_children.items():
        child_path = root_view.get("node_path")
        if not isinstance(child_path, str) or not child_path:
            raise ValueError(f"network-publication:grounding-coordinate:{key}")
        child = read_network_body(
            repo, frozen, f"{child_path}/patch-series-manifest.json", client=client
        )
        if child is None:
            raise ValueError(
                f"network-publication:incomplete-grounded-child-cohort:{child_path}"
            )
        _validate_grounded_child_cohort(
            repo,
            frozen,
            key,
            child_path,
            child,
            root_view,
            ledger_children[key],
            client=client,
        )
        grounded_children[key] = child

    if node_path == ".":
        return
    key = selected.get("child_key")
    if not isinstance(key, str) or key not in grounded_children:
        raise ValueError(f"network-publication:grounding-orphan-child:{node_path}")
    if grounded_children[key] != selected:
        raise ValueError(f"network-publication:grounding-coordinate:{node_path}")


def _validate_grounded_child_cohort(
    repo: str | Path,
    frozen: str,
    key: str,
    node_path: str,
    child: Mapping[str, Any],
    root_view: Mapping[str, Any],
    ledger_view: Mapping[str, Any],
    *,
    client: BodyCodecClient | None,
) -> None:
    """Validate one declared child and every child-owned network carrier."""

    if root_view.get("node_path") != node_path:
        raise ValueError(f"network-publication:grounding-coordinate:{node_path}")
    _same_fields(
        child,
        root_view,
        ("child_key", "node_path", "lifecycle_status", "edges"),
        f"grounded-child-root:{key}",
    )
    _same_fields(
        child,
        ledger_view,
        ("id", "child_key", "node_path", "contrib_branch"),
        f"grounded-child-ledger:{key}",
    )
    if child.get("lifecycle_status") != "stale":
        _same_fields(
            child, ledger_view, ("scope_item_ids",),
            f"grounded-child-ledger:{key}",
        )
    write_scope = child.get("write_scope")
    if not isinstance(write_scope, Mapping) or (
        write_scope.get("allowed_subtree") != ledger_view.get("allowed_subtree")
    ):
        raise ValueError(
            f"network-publication:grounding-write-scope:{key}"
        )

    request = read_network_body(
        repo, frozen, f"{node_path}/maintainer-series-request.json", client=client
    )
    metadata = read_network_body(
        repo, frozen, f"{node_path}/patch-series-metadata.json", client=client
    )
    cover = read_network_body(
        repo, frozen, f"{node_path}/patch-series-cover-letter.json", client=client
    )
    approach = read_network_body(
        repo, frozen, f"{node_path}/technical-approach-plan.json", client=client
    )
    if any(value is None for value in (request, metadata, cover, approach)):
        raise ValueError(
            f"network-publication:incomplete-grounded-child-cohort:{node_path}"
        )
    assert request is not None and metadata is not None
    assert cover is not None and approach is not None
    provenance = request.get("provenance")
    if not isinstance(provenance, Mapping) or (
        provenance.get("child_key") != key
        or provenance.get("parent_branch") != child.get("parent_branch")
    ):
        raise ValueError(f"network-publication:grounding-request-child:{key}")
    if request.get("request") != cover:
        raise ValueError(f"network-publication:grounding-request-cover:{key}")
    _same_fields(
        metadata, child, ("lifecycle_status",),
        f"grounded-metadata-child:{key}",
    )
    if metadata.get("id") is not None and metadata.get("id") != child.get("id"):
        raise ValueError(f"network-publication:grounding-metadata-child-id:{key}")
    lineage = approach.get("lineage_child")
    if not isinstance(lineage, Mapping):
        raise ValueError(f"network-publication:grounding-approach-child:{key}")
    _same_fields(
        lineage, child, ("id", "lifecycle_status"),
        f"grounded-approach-child:{key}",
    )


def _prepare_recipe(codec: BodyCodecClient, member: NetworkMember) -> PreparedBodySet:
    target_context, target_contract = _recipe_target(member)
    # Some recursive network definitions intentionally exceed the structured-
    # output schema guard's shallow nesting limit.  The immutable codec
    # authority digest is the exact schema-source snapshot identity for those
    # contexts and avoids weakening that guard merely to stamp a carrier.
    schema_sha256 = str(codec.handshake_metadata["authority_sha256"])
    body = {
        "source_context": member.context,
        "source_kind": member.contract.kind,
        "target_context": target_context,
        "target_kind": target_contract.kind,
        "projection_profile": "consumer-json-v1",
        "schema_profile": "codex-structured-output-v1",
        "blob_sha256": hashlib.sha256(member.prepared.canonical.data).hexdigest(),
        "schema_sha256": schema_sha256,
    }
    context, contract = _SPECS["recipe"]
    return codec.prepare(context, body, expected=contract)


def _recipe_target(member: NetworkMember) -> tuple[str, ContractIdentity]:
    source_context = member.context
    if source_context in {_SPECS["root_manifest"][0], _SPECS["child_manifest"][0]}:
        return _SPECS["ledger"]
    if source_context == _SPECS["ledger"][0]:
        return _SPECS["rollup"]
    if source_context in {_SPECS["request"][0], _SPECS["child_approach"][0]}:
        return _SPECS["child_manifest"]
    if source_context == _SPECS["child_cover"][0]:
        return _SPECS["request"]
    if source_context == _SPECS["metadata"][0]:
        return (
            _SPECS["root_manifest"]
            if str(PurePosixPath(member.historical_path).parent) == "."
            else _SPECS["child_manifest"]
        )
    return _SPECS["root_manifest"]


__all__ = [
    "ManifestInput", "NetworkMember", "PreparedNetworkPublication", "classify_path",
    "canonical_update_contract", "prepare_network_publication", "read_network_body",
    "resolve_manifest_input", "validate_carrier_recipes",
]
