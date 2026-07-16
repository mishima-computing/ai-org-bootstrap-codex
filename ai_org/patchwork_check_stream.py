"""Exact-byte authority for typed Patchwork Check event streams.

The enclosing body is durable.  Parsed events, physical-line positions, and
current values are replay products and are intentionally never serialized
back into that body.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import json
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, Mapping


CONTEXT_ID = "patchwork-check-event-stream-v1"
CONTRACT = {
    "apiVersion": "ai-org-cue-body-v1",
    "kind": "PatchworkCheckEventStream",
    "variant": "network-node",
}
CANONICAL_PATH = "patchwork-check-event-stream.cue"
LEGACY_PATH = "patchwork-check-events.jsonl"
MAX_RAW_STREAM_BYTES = 4 << 20
MAX_RECORD_DEPTH = 64
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_CHECK_NAME = re.compile(r"[a-z0-9_]+\Z")
_CHILD_NODE_PATH = re.compile(r"sub/[a-z0-9_]+\Z")
_SOURCE_PATH = re.compile(
    r"(?:sub/[a-z0-9_]+/)?(?:patchwork-check-event-stream\.cue|patchwork-check-events\.jsonl)\Z"
)
_BASE64 = re.compile(
    r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?\Z"
)


@dataclass(frozen=True, slots=True)
class PhysicalLineDiagnostic:
    code: str
    path: str
    line: int
    rule: str
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": self.code,
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
        }
        if self.message:
            value["message"] = self.message
        return value


@dataclass(frozen=True, slots=True)
class PatchworkCheckProjection:
    state: Mapping[str, int | str]
    last_events: Mapping[str, Mapping[str, Any]]
    diagnostics: tuple[PhysicalLineDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class PreparedAppend:
    body: Mapping[str, str]
    raw_stream: bytes
    prior_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedGitPublication:
    ref: str
    expected_oid: str
    commit_oid: str
    path: str
    canonical: bytes


class StreamFailure(ValueError):
    """Stable payload-free stream boundary failure."""

    def __init__(self, code: str, location: str, rule: str) -> None:
        self.code = code
        self.location = location
        self.rule = rule
        super().__init__(f"{code} location={location!r} rule={rule!r}")


def body_from_raw_stream(raw: bytes, *, source_oid: str, source_path: str) -> dict[str, str]:
    """Create the sole durable body representation for opaque stream bytes."""

    _validate_anchor(source_oid, source_path)
    data = bytes(raw)
    if len(data) > MAX_RAW_STREAM_BYTES:
        raise StreamFailure("INPUT_LIMIT", "/body/raw_stream_base64", "decoded-stream-4-mib")
    return {
        "raw_stream_base64": base64.b64encode(data).decode("ascii"),
        "source_oid": source_oid,
        "source_path": source_path,
    }


def import_legacy_jsonl(raw: bytes, *, source_oid: str, source_path: str = LEGACY_PATH) -> dict[str, str]:
    """Import legacy JSONL without parsing, normalizing, or repairing it."""

    return body_from_raw_stream(raw, source_oid=source_oid, source_path=source_path)


def parse_canonical_body(client: Any, raw: bytes) -> Mapping[str, Any]:
    """Parse a canonical coordinate and reject the historical JSON carrier."""

    body = client.parse(CONTEXT_ID, raw, expected=CONTRACT)
    prepared = client.prepare(CONTEXT_ID, body, expected=CONTRACT)
    if bytes(prepared.canonical.data) != bytes(raw):
        raise StreamFailure("NON_CANONICAL", "/", "canonical-body-coordinate")
    return body


def decode_raw_stream(body: Mapping[str, Any]) -> bytes:
    """Strictly decode the exact bytes and reject competing durable fields."""

    if not isinstance(body, Mapping) or set(body) != {"raw_stream_base64", "source_oid", "source_path"}:
        raise StreamFailure("SCHEMA_VALIDATION", "/body", "closed-stream-body")
    source_oid = body.get("source_oid")
    source_path = body.get("source_path")
    _validate_anchor(source_oid, source_path)
    encoded = body.get("raw_stream_base64")
    if (
        not isinstance(encoded, str)
        or not encoded.isascii()
        or _BASE64.fullmatch(encoded) is None
    ):
        raise StreamFailure("ENCODING", "/body/raw_stream_base64", "rfc4648-base64")
    padding = len(encoded) - len(encoded.rstrip("="))
    decoded_size = len(encoded) // 4 * 3 - padding
    if decoded_size > MAX_RAW_STREAM_BYTES:
        raise StreamFailure("INPUT_LIMIT", "/body/raw_stream_base64", "decoded-stream-4-mib")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise StreamFailure("ENCODING", "/body/raw_stream_base64", "rfc4648-base64") from exc
    if base64.b64encode(decoded).decode("ascii") != encoded:
        raise StreamFailure("NON_CANONICAL", "/body/raw_stream_base64", "rfc4648-base64")
    if len(decoded) != decoded_size:
        raise StreamFailure("ENCODING", "/body/raw_stream_base64", "rfc4648-base64")
    return decoded


def replay(
    body: Mapping[str, Any],
    registry: Mapping[str, str],
    *,
    initial_state: Mapping[str, int | str] | None = None,
) -> PatchworkCheckProjection:
    """Replay independent physical records, continuing after every rejection."""

    raw = decode_raw_stream(body)
    declared_checks = _validated_registry(registry)
    path = str(body["source_path"])
    state = _validated_initial_state(declared_checks, initial_state)
    last_events: dict[str, Mapping[str, Any]] = {}
    diagnostics: list[PhysicalLineDiagnostic] = []
    for line_number, record in enumerate(_physical_records(raw), start=1):
        if _record_is_blank(record):
            continue
        diagnostic = _decode_record(record, path, line_number)
        if isinstance(diagnostic, PhysicalLineDiagnostic):
            diagnostics.append(diagnostic)
            continue
        error = _apply_event(state, diagnostic, declared_checks, path, line_number)
        if error is not None:
            diagnostics.append(error)
            continue
        last_events[str(diagnostic["var"])] = {"path": path, "line": line_number}
    return PatchworkCheckProjection(state, last_events, tuple(diagnostics))


def prepare_append(body: Mapping[str, Any], record: bytes, registry: Mapping[str, str]) -> PreparedAppend:
    """Validate exactly one nonblank record before extending the byte stream."""

    prior = decode_raw_stream(body)
    declared_checks = _validated_registry(registry)
    candidate_record = bytes(record)
    separator = b"" if not prior or prior.endswith((b"\n", b"\r")) else b"\n"
    # A record can shed at most one CRLF pair during framing.  Reject a
    # candidate that cannot possibly fit before _physical_records creates a
    # slice for it; the exact check below still decides the CR/LF boundary
    # cases after the contract framing has been identified.
    maximum_candidate_size = (
        MAX_RAW_STREAM_BYTES - len(prior) - len(separator) - 1 + len(b"\r\n")
    )
    if len(candidate_record) > maximum_candidate_size:
        raise StreamFailure("INPUT_LIMIT", "/body/raw_stream_base64", "decoded-stream-4-mib")
    clean: bytes | None = None
    for framed_record in _physical_records(candidate_record):
        # Append accepts one physical record, not a mini-stream. Fail as soon
        # as a blank or second record proves the contract false so validation
        # never materializes a candidate-sized list of record slices.
        if _record_is_blank(framed_record) or clean is not None:
            raise StreamFailure("SCHEMA_VALIDATION", "/record", "one-nonblank-record")
        clean = framed_record
    if clean is None:
        raise StreamFailure("SCHEMA_VALIDATION", "/record", "one-nonblank-record")
    appended_size = len(prior) + len(separator) + len(clean) + 1
    # Bound the complete decoded sequence before json.loads recursively
    # allocates the candidate record's object graph.
    if appended_size > MAX_RAW_STREAM_BYTES:
        raise StreamFailure("INPUT_LIMIT", "/body/raw_stream_base64", "decoded-stream-4-mib")
    decoded = _decode_record(clean, str(body["source_path"]), 1)
    if isinstance(decoded, PhysicalLineDiagnostic):
        raise StreamFailure(decoded.code.upper(), "/record", decoded.rule)
    scratch = {name: 0 if declared == "counter" else "" for name, declared in declared_checks.items()}
    error = _apply_event(scratch, decoded, declared_checks, str(body["source_path"]), 1)
    if error is not None:
        raise StreamFailure("SCHEMA_VALIDATION", "/record", error.rule)
    appended = prior + separator + clean + b"\n"
    updated = body_from_raw_stream(
        appended,
        source_oid=str(body["source_oid"]),
        source_path=str(body["source_path"]),
    )
    return PreparedAppend(updated, appended, hashlib.sha256(prior).hexdigest())


def append_and_publish(
    body: Mapping[str, Any],
    record: bytes,
    registry: Mapping[str, str],
    *,
    expected_oid: str,
    publish: Callable[[Mapping[str, str], str], Any],
) -> Any:
    """Prepare completely, then delegate exactly one enclosing-body CAS."""

    if _OID.fullmatch(expected_oid) is None:
        raise StreamFailure("GIT_IDENTITY", "expected_oid", "source-oid")
    prepared = prepare_append(body, record, registry)
    return publish(prepared.body, expected_oid)


def append_to_git(
    repo: Any,
    branch: str,
    node_path: str,
    record: bytes,
    registry: Mapping[str, str],
    *,
    expected_oid: str,
    client: Any = None,
    inject_failure: bool = False,
) -> Any:
    """Read a frozen authority, append once, and CAS-publish its enclosing body."""

    from ai_org import git_wrapper
    from ai_org.body_codec import BodyCodecClient

    repo_path = Path(repo)
    ref = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
    relative_dir = _node_relative_dir(node_path)
    if git_wrapper.head_sha(repo_path, ref) != expected_oid:
        return git_wrapper.GitPublicationResult(
            "rejected",
            ref,
            expected_oid,
            failure=git_wrapper.GitBodyFailure("GIT_CAS", ref, "expected-branch-oid"),
        )
    canonical_path = relative_dir + CANONICAL_PATH
    legacy_path = relative_dir + LEGACY_PATH
    canonical = git_wrapper.show_file_bytes(repo, expected_oid, canonical_path)
    codec = client or BodyCodecClient()
    legacy = git_wrapper.show_file_bytes(repo, expected_oid, legacy_path)
    if canonical is not None and legacy is not None:
        raise git_wrapper.GitBodyFailure("BODY_VALIDATION", canonical_path, "ambiguous-stream-coordinate")
    if canonical is not None:
        try:
            # Diagnose a coordinate mismatch before canonical-byte checking so
            # callers receive the stable node-binding failure even when a
            # validating test double cannot emit canonical bytes.
            parsed = codec.parse(CONTEXT_ID, canonical, expected=CONTRACT)
            _validate_node_source_path(parsed.get("source_path"), relative_dir)
            body = parse_canonical_body(codec, canonical)
        except Exception as exc:
            if isinstance(exc, StreamFailure) and exc.rule == "stream-node-coordinate":
                raise git_wrapper.GitBodyFailure(
                    "BODY_VALIDATION", canonical_path, "stream-source-path-mismatch"
                ) from exc
            raise git_wrapper.GitBodyFailure(
                "BODY_VALIDATION", canonical_path, "patchwork-check-stream"
            ) from exc
    else:
        if legacy is None:
            legacy = b""
        body = import_legacy_jsonl(legacy, source_oid=expected_oid, source_path=legacy_path)
    try:
        _validate_node_source_path(body.get("source_path"), relative_dir)
    except StreamFailure as exc:
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", canonical_path, "stream-source-path-mismatch"
        ) from exc
    manifest_registry = _registry_from_git_manifest(
        repo_path, expected_oid, client=codec
    )
    supplied_registry = _validated_registry(registry)
    if supplied_registry != manifest_registry:
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", "patch-series-manifest.json", "patchwork-check-registry-mismatch"
        )
    appended = prepare_append(body, record, manifest_registry)
    try:
        prepared = _prepare_git_publication(
            repo,
            branch,
            node_path,
            appended.body,
            expected_oid=expected_oid,
            client=codec,
        )
    except git_wrapper.GitBodyFailure as exc:
        if exc.code != "GIT_CAS":
            raise
        return git_wrapper.GitPublicationResult(
            "rejected", ref, expected_oid, failure=exc
        )
    return _publish_git(repo, prepared, inject_failure=inject_failure)


def _prepare_git_publication(
    repo: Any,
    branch: str,
    node_path: str,
    body: Mapping[str, Any],
    *,
    expected_oid: str,
    client: Any,
) -> PreparedGitPublication:
    from ai_org import git_wrapper

    repo_path = Path(repo)
    ref = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
    relative_dir = _node_relative_dir(node_path)
    if git_wrapper.head_sha(repo_path, ref) != expected_oid:
        raise git_wrapper.GitBodyFailure("GIT_CAS", ref, "expected-branch-oid")
    path = relative_dir + CANONICAL_PATH
    legacy_path = relative_dir + LEGACY_PATH
    try:
        decode_raw_stream(body)
        _validate_node_source_path(body.get("source_path"), relative_dir)
        prepared = client.prepare(CONTEXT_ID, body, expected=CONTRACT)
        canonical = bytes(prepared.canonical.data)
        reparsed = parse_canonical_body(client, canonical)
    except Exception as exc:
        raise git_wrapper.GitBodyFailure("BODY_VALIDATION", path, "patchwork-check-stream") from exc
    if dict(reparsed) != dict(body):
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", path, "prepared-body-round-trip"
        )
    with tempfile.TemporaryDirectory(prefix="ai-org-patchwork-stream-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git_wrapper._git_required_env(repo_path, env, "read-tree", expected_oid)
        blob = git_wrapper._git_required_bytes_env(
            repo_path, env, "hash-object", "-w", "--stdin", input_bytes=canonical
        ).stdout.decode("ascii").strip()
        git_wrapper._git_required_env(repo_path, env, "update-index", "--add", "--cacheinfo", "100644", blob, path)
        if git_wrapper.file_exists(repo_path, expected_oid, legacy_path):
            git_wrapper._git_required_env(repo_path, env, "update-index", "--force-remove", "--", legacy_path)
        tree = git_wrapper._git_required_env(repo_path, env, "write-tree").stdout.strip()
    commit = git_wrapper._git_required(
        repo_path,
        *git_wrapper.identity_config_args(),
        "commit-tree", tree, "-p", expected_oid,
        "-m", "patchwork checks: append event",
    ).stdout.strip()
    return PreparedGitPublication(ref, expected_oid, commit, path, canonical)


def _publish_git(repo: Any, prepared: PreparedGitPublication, *, inject_failure: bool) -> Any:
    from ai_org import git_wrapper

    if inject_failure:
        return git_wrapper.GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_oid,
            failure=git_wrapper.GitBodyFailure("GIT_PUBLICATION", prepared.ref, "injected-publication-failure"),
        )
    result = git_wrapper._git(Path(repo), "update-ref", prepared.ref, prepared.commit_oid, prepared.expected_oid)
    if result.returncode != 0:
        return git_wrapper.GitPublicationResult(
            "rejected", prepared.ref, prepared.expected_oid,
            failure=git_wrapper.GitBodyFailure("GIT_CAS", prepared.ref, "compare-and-swap"),
        )
    return git_wrapper.GitPublicationResult("updated", prepared.ref, prepared.expected_oid, commit_oid=prepared.commit_oid)


def _decode_record(record: bytes, path: str, line: int) -> Mapping[str, Any] | PhysicalLineDiagnostic:
    if _json_depth_exceeds(record, MAX_RECORD_DEPTH):
        return PhysicalLineDiagnostic("patchwork_check_event_invalid", path, line, "record-depth-64")
    try:
        text = record.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return PhysicalLineDiagnostic("ingestion_invalid", path, line, "utf8")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        return PhysicalLineDiagnostic(
            "ingestion_invalid",
            path,
            line,
            "json-record",
            f"invalid JSON on line {line}: {exc}",
        )
    if not isinstance(value, Mapping):
        return PhysicalLineDiagnostic(
            "patchwork_check_event_invalid",
            path,
            line,
            "record-object",
            "state event must be an object",
        )
    return value


def _apply_event(
    state: dict[str, int | str], event: Mapping[str, Any], registry: Mapping[str, str], path: str, line: int
) -> PhysicalLineDiagnostic | None:
    var, op = event.get("var"), event.get("op")
    if not isinstance(var, str) or not var:
        return PhysicalLineDiagnostic(
            "patchwork_check_event_invalid",
            path,
            line,
            "declared-variable",
            f"state event {line} has invalid var",
        )
    if var not in registry:
        return PhysicalLineDiagnostic(
            "patchwork_check_event_invalid",
            path,
            line,
            "declared-variable",
            f"state event {line} writes undeclared variable {var}",
        )
    declared = registry[var]
    if op == "set":
        value = event.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return PhysicalLineDiagnostic(
                "patchwork_check_event_invalid",
                path,
                line,
                "event-value",
                f"state event {line} set value must be integer or string",
            )
        if declared == "counter" and (isinstance(value, bool) or not isinstance(value, int)):
            return PhysicalLineDiagnostic(
                "patchwork_check_event_invalid",
                path,
                line,
                "counter-integer",
                f"state event {line} set value for counter {var} must be integer",
            )
        if declared == "text" and not isinstance(value, str):
            return PhysicalLineDiagnostic(
                "patchwork_check_event_invalid",
                path,
                line,
                "text-string",
                f"state event {line} set value for text {var} must be string",
            )
        state[var] = value
        return None
    if op in {"inc", "dec"}:
        delta = event.get("value", 1)
        if declared != "counter":
            return PhysicalLineDiagnostic(
                "patchwork_check_event_invalid",
                path,
                line,
                "counter-delta",
                f"state event {line} cannot {op} non-counter var {var}",
            )
        if isinstance(delta, bool) or not isinstance(delta, int):
            return PhysicalLineDiagnostic(
                "patchwork_check_event_invalid",
                path,
                line,
                "counter-delta",
                f"state event {line} {op} value must be integer",
            )
        state[var] = int(state[var]) + (delta if op == "inc" else -delta)
        return None
    return PhysicalLineDiagnostic(
        "patchwork_check_event_invalid",
        path,
        line,
        "event-operation",
        f"state event {line} op must be set, inc, or dec",
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    """Reject Python's non-standard NaN and Infinity JSON extensions."""

    raise ValueError(f"non-standard JSON constant {value!r}")


def _json_depth_exceeds(record: bytes, limit: int) -> bool:
    """Bound container depth from bytes before the recursive JSON decoder runs."""

    depth = 0
    in_string = False
    escaped = False
    for byte in record:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > limit:
                return True
        elif byte in (0x7D, 0x5D):
            depth = max(0, depth - 1)
    return False


def _physical_records(raw: bytes) -> Iterator[bytes]:
    """Yield records split only by the stream contract's physical line ends.

    ``bytes.splitlines`` also splits at vertical tab, form feed, NEL, and the
    ASCII file/group/record separators.  Those bytes belong to the record and
    must remain visible to JSON validation; treating them as framing could
    turn the tail of one malformed record into an accepted later event.
    """

    start = 0
    cursor = 0
    while cursor < len(raw):
        byte = raw[cursor]
        if byte == 0x0A:  # LF
            yield raw[start:cursor]
            cursor += 1
            start = cursor
            continue
        if byte == 0x0D:  # CR or CRLF
            yield raw[start:cursor]
            cursor += 2 if cursor + 1 < len(raw) and raw[cursor + 1] == 0x0A else 1
            start = cursor
            continue
        cursor += 1
    if start < len(raw):
        yield raw[start:]


def _record_is_blank(record: bytes) -> bool:
    """Return true only for JSON whitespace left after physical framing."""

    return all(byte in (0x09, 0x20) for byte in record)


def is_permitted_source_path(source_path: Any) -> bool:
    """Return whether a stream anchor names the root or one direct child."""

    return isinstance(source_path, str) and _SOURCE_PATH.fullmatch(source_path) is not None


def _validate_anchor(source_oid: Any, source_path: Any) -> None:
    if not isinstance(source_oid, str) or _OID.fullmatch(source_oid) is None:
        raise StreamFailure("GIT_IDENTITY", "/body/source_oid", "source-oid")
    if not is_permitted_source_path(source_path):
        raise StreamFailure("GIT_IDENTITY", "/body/source_path", "stream-source-path")


def _validate_node_source_path(source_path: Any, relative_dir: str) -> None:
    """Bind replay anchors to the root or child coordinate being published."""

    permitted = {relative_dir + CANONICAL_PATH, relative_dir + LEGACY_PATH}
    if source_path not in permitted:
        raise StreamFailure("GIT_IDENTITY", "/body/source_path", "stream-node-coordinate")


def _node_relative_dir(node_path: Any) -> str:
    """Keep stream publication inside the established network-node scope."""

    if isinstance(node_path, str) and node_path in {"", "."}:
        return ""
    if not isinstance(node_path, str) or _CHILD_NODE_PATH.fullmatch(node_path) is None:
        raise StreamFailure("GIT_IDENTITY", "node_path", "network-node-path")
    return node_path + "/"


def _validated_registry(registry: Mapping[str, str]) -> dict[str, str]:
    """Freeze the manifest-typed Patchwork Check vocabulary for one operation."""

    if not isinstance(registry, Mapping):
        raise StreamFailure("SCHEMA_VALIDATION", "/manifest/declared_patchwork_checks", "typed-check-registry")
    result: dict[str, str] = {}
    for name, declared_type in registry.items():
        if not isinstance(name, str) or _CHECK_NAME.fullmatch(name) is None:
            raise StreamFailure("SCHEMA_VALIDATION", "/manifest/declared_patchwork_checks", "check-name")
        if declared_type not in {"counter", "text"}:
            raise StreamFailure("SCHEMA_VALIDATION", "/manifest/declared_patchwork_checks", "check-type")
        result[name] = declared_type
    return result


def _validated_initial_state(
    registry: Mapping[str, str], initial_state: Mapping[str, int | str] | None
) -> dict[str, int | str]:
    state: dict[str, int | str] = {
        name: 0 if declared == "counter" else "" for name, declared in registry.items()
    }
    if initial_state is None:
        return state
    if not isinstance(initial_state, Mapping):
        raise StreamFailure("SCHEMA_VALIDATION", "/initial_state", "typed-check-state")
    for name, value in initial_state.items():
        declared = registry.get(name)
        if declared is None:
            raise StreamFailure("SCHEMA_VALIDATION", "/initial_state", "declared-variable")
        if declared == "counter" and (isinstance(value, bool) or not isinstance(value, int)):
            raise StreamFailure("SCHEMA_VALIDATION", "/initial_state", "counter-integer")
        if declared == "text" and not isinstance(value, str):
            raise StreamFailure("SCHEMA_VALIDATION", "/initial_state", "text-string")
        state[name] = value
    return state


def _registry_from_git_manifest(repo: Path, oid: str, *, client: Any) -> dict[str, str]:
    """Bind append validation to the root manifest at the frozen source OID."""

    from ai_org import git_wrapper, network_bodies

    canonical = git_wrapper.show_file_bytes(repo, oid, "patch-series-manifest.cue")
    historical = git_wrapper.show_file_bytes(repo, oid, "patch-series-manifest.json")
    if canonical is not None and historical is not None:
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", "patch-series-manifest.json", "manifest-ambiguous-coordinate"
        )
    if canonical is None and historical is None:
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", "patch-series-manifest.json", "declared-patchwork-checks-missing"
        )
    try:
        if canonical is not None:
            manifest = network_bodies.read_network_body(
                repo, oid, "patch-series-manifest.json", client=client
            )
        else:
            assert historical is not None
            manifest = json.loads(
                historical.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_object,
            )
    except Exception as exc:
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", "patch-series-manifest.json", "manifest-json"
        ) from exc
    declarations = manifest.get("declared_patchwork_checks") if isinstance(manifest, Mapping) else None
    if not isinstance(declarations, list):
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", "patch-series-manifest.json", "declared-patchwork-checks-missing"
        )
    registry: dict[str, str] = {}
    for declaration in declarations:
        if not isinstance(declaration, Mapping) or set(declaration) != {"name", "type"}:
            raise git_wrapper.GitBodyFailure(
                "BODY_VALIDATION", "patch-series-manifest.json", "declared-patchwork-checks-invalid"
            )
        name = declaration.get("name")
        declared_type = declaration.get("type")
        if not isinstance(name, str) or name in registry or not isinstance(declared_type, str):
            raise git_wrapper.GitBodyFailure(
                "BODY_VALIDATION", "patch-series-manifest.json", "declared-patchwork-checks-invalid"
            )
        registry[name] = declared_type
    try:
        return _validated_registry(registry)
    except StreamFailure as exc:
        raise git_wrapper.GitBodyFailure(
            "BODY_VALIDATION", "patch-series-manifest.json", "declared-patchwork-checks-invalid"
        ) from exc
