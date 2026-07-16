"""Lossless Python boundary for the engine-owned CUE body codec.

The target repository is data, never codec authority.  This module therefore
discovers an engine-owned ``ai-org-cuecodec`` bundle, verifies its identity with
the ``cuecodec-process-v1`` handshake, and launches a fresh process for every
operation.  Artifact bytes cross the outer JSON protocol only as base64.

JSON projections deliberately do not use Python ``float`` (or eagerly coerce
numbers to ``int``).  :class:`ExactJSONNumber` retains the JSON token verbatim;
callers opt in to arithmetic through its explicit conversion helpers.
"""
from __future__ import annotations

from base64 import b64decode, b64encode
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Any, Final
import uuid


PROCESS_PROTOCOL: Final = "cuecodec-process-v1"
ENGINE_PUBLIC_CONTRACT: Final = "engine-codec-public-contract-v1"
BODY_API_VERSION: Final = "ai-org-cue-body-v1"
PRODUCER_LIFECYCLE_MANIFEST: Final = "producer-lifecycle-contract-matrix-v1"
# The dormant producer lifecycle is one codec identity cohort. Keeping its
# exact context/kind/variant tuples beside the authority digest pins prevents a
# reader from pairing current engine bytes with a stale or invented identity.
PINNED_PRODUCER_LIFECYCLE_IDENTITIES: Final = (
    (
        "producer-promise-v1",
        BODY_API_VERSION,
        "ProducerPromise",
        "producer-commitment",
    ),
    (
        "producer-task-binding-v1",
        BODY_API_VERSION,
        "ProducerTaskBinding",
        "contribution-task",
    ),
    (
        "producer-completion-assertion-v1",
        BODY_API_VERSION,
        "ProducerCompletionAssertion",
        "producer-claim",
    ),
    (
        "claim-admission-v1",
        BODY_API_VERSION,
        "ClaimAdmission",
        "functional-acceptance",
    ),
    (
        "functional-acceptance-v1",
        BODY_API_VERSION,
        "FunctionalAcceptanceVerdict",
        "contribution",
    ),
    (
        "acceptance-authority-seal-v1",
        BODY_API_VERSION,
        "AcceptanceAuthoritySeal",
        "verifier-authority",
    ),
)
CODEC_MODULE: Final = "github.com/mishima-computing/cuecodec"
PINNED_GO_RELEASE: Final = "1.26.5"
PINNED_CUE_LANGUAGE: Final = "v0.17.0"
PINNED_CUELANG_GO: Final = "v0.17.0"
EXPECTED_AUTHORITY_SHA256: Final = "9918f17f8d51e79646a0e3948ed1f6197e1f6f5c1039084f304b9bd3a8dd668e"
EXPECTED_CATALOG_SHA256: Final = "c440e12519d6d6ea578dc1b2c6457b2b5a56acf5aa79ac6802fdc34348b14a43"
EXPECTED_MANIFEST_SHA256: Final = "a0d2e3416dd13a5c5fbeedab7d736c9b14f66863f0e79a82dcd636cae239cde5"
EXPECTED_PUBLIC_CONTRACT_SHA256: Final = "db0ef6a3de2bebf44714de86aa697e23cdf9a06d0731565dce2dbe4511df519d"
CODEC_EXECUTABLE_ENV: Final = "AI_ORG_CUECODEC"
CODEC_EXECUTABLE_NAME: Final = "ai-org-cuecodec"

DEFAULT_TIMEOUT_SECONDS: Final = 15.0
MAX_ARTIFACT_BYTES: Final = 8 << 20
# The base64 member is bounded mechanically from the decoded codec limit.  The
# fixed allowance covers the versioned response object and handshake metadata.
MAX_PROCESS_RESPONSE_BYTES: Final = 4 * ((MAX_ARTIFACT_BYTES + 2) // 3) + (64 << 10)
MAX_PROCESS_REQUEST_BYTES: Final = MAX_PROCESS_RESPONSE_BYTES
MAX_PROCESS_STDERR_BYTES: Final = 64 << 10
MAX_JSON_DEPTH: Final = 64

_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_OPERATIONS = frozenset({"handshake", "inspect", "emit", "parse", "vet", "project", "schema"})
_INPUT_OPERATIONS = frozenset({"emit", "parse", "vet", "project"})


@dataclass(frozen=True, slots=True)
class ExactJSONNumber:
    """An immutable, grammar-checked JSON number token.

    Equality is lexical on purpose: ``1``, ``1.0``, and ``1e0`` remain distinct
    values at this boundary even though their arithmetic values are equal.
    """

    lexeme: str

    def __post_init__(self) -> None:
        if not isinstance(self.lexeme, str):
            raise TypeError("ExactJSONNumber lexeme must be a string")
        if _JSON_NUMBER_RE.fullmatch(self.lexeme) is None:
            raise ValueError(f"invalid JSON number lexeme: {self.lexeme!r}")

    def __str__(self) -> str:
        return self.lexeme

    def as_decimal(self) -> Decimal:
        """Return an exact decimal view without changing the retained lexeme."""

        # Construction from a string is exact and does not use the active
        # context precision.
        return Decimal(self.lexeme)

    def as_int_exact(self) -> int:
        """Return the mathematical integer, rejecting a fractional value."""

        try:
            value = self.as_decimal()
            integral = value.to_integral_value()
        except InvalidOperation as exc:  # defensive; the JSON grammar excludes NaN/Inf
            raise ValueError(f"JSON number is not an exact integer: {self.lexeme}") from exc
        if value != integral:
            raise ValueError(f"JSON number is not an exact integer: {self.lexeme}")
        return int(integral)


class DuplicateJSONKey(ValueError):
    """Raised when strict JSON decoding encounters an object member twice."""


def strict_json_loads(
    data: str | bytes | bytearray | memoryview,
    *,
    max_depth: int = MAX_JSON_DEPTH,
) -> Any:
    """Decode one strict JSON value, retaining every numeric token exactly."""

    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")

    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("JSON input is not UTF-8") from exc
    elif isinstance(data, str):
        text = data
    else:
        raise TypeError("strict_json_loads expects str or bytes-like input")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJSONKey(f"duplicate JSON object key: {key!r}")
            result[key] = value
        return result

    def invalid_constant(token: str) -> Any:
        raise ValueError(f"invalid JSON numeric constant: {token}")

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_int=ExactJSONNumber,
            parse_float=ExactJSONNumber,
            parse_constant=invalid_constant,
        )
    except RecursionError as exc:
        raise ValueError(f"JSON structure exceeds max depth {max_depth}") from exc
    stack: list[tuple[Any, int]] = [(decoded, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, Mapping):
            if depth >= max_depth:
                raise ValueError(f"JSON structure exceeds max depth {max_depth}")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray, memoryview)
        ):
            if depth >= max_depth:
                raise ValueError(f"JSON structure exceeds max depth {max_depth}")
            stack.extend((child, depth + 1) for child in item)
    return decoded


def strict_json_dumps(value: Any, *, sort_keys: bool = True, max_depth: int = MAX_JSON_DEPTH) -> bytes:
    """Encode JSON bytes while emitting :class:`ExactJSONNumber` verbatim.

    Native ``float`` and :class:`~decimal.Decimal` values are refused: accepting
    them would make it unclear whether their spelling or binary approximation
    was authoritative.  Use ``ExactJSONNumber`` at this boundary instead.
    """

    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    active: set[int] = set()

    def encode(item: Any, depth: int) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, ExactJSONNumber):
            return item.lexeme
        if isinstance(item, int) and not isinstance(item, bool):
            return str(item)
        if isinstance(item, (float, Decimal)):
            raise TypeError("lossy numeric values are forbidden; use ExactJSONNumber")
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False, allow_nan=False)
        if isinstance(item, (bytes, bytearray, memoryview)):
            raise TypeError("bytes are not a JSON value; frame artifacts with base64")

        if isinstance(item, Mapping):
            if depth >= max_depth:
                raise ValueError(f"JSON structure exceeds max depth {max_depth}")
            marker = id(item)
            if marker in active:
                raise ValueError("circular JSON object")
            active.add(marker)
            try:
                entries: list[tuple[str, Any]] = []
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise TypeError("JSON object keys must be strings")
                    entries.append((key, child))
                if sort_keys:
                    entries.sort(key=lambda pair: pair[0].encode("utf-8"))
                return "{" + ",".join(
                    f"{json.dumps(key, ensure_ascii=False)}:{encode(child, depth + 1)}"
                    for key, child in entries
                ) + "}"
            finally:
                active.remove(marker)

        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray, memoryview)):
            if depth >= max_depth:
                raise ValueError(f"JSON structure exceeds max depth {max_depth}")
            marker = id(item)
            if marker in active:
                raise ValueError("circular JSON array")
            active.add(marker)
            try:
                return "[" + ",".join(encode(child, depth + 1) for child in item) + "]"
            finally:
                active.remove(marker)
        raise TypeError(f"unsupported JSON value type: {type(item).__name__}")

    try:
        return encode(value, 0).encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("JSON strings must contain valid Unicode scalar values") from exc


# Explicit aliases make the lossless nature discoverable without forcing users
# to remember whether the project calls the helper "strict" or "exact".
loads_exact_json = strict_json_loads
dumps_exact_json = strict_json_dumps


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    api_version: str
    kind: str
    variant: str

    def __post_init__(self) -> None:
        for name, value in (
            ("api_version", self.api_version),
            ("kind", self.kind),
            ("variant", self.variant),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"contract {name} must be a non-empty string")

    def as_dict(self) -> dict[str, str]:
        return {"api_version": self.api_version, "kind": self.kind, "variant": self.variant}


@dataclass(frozen=True, slots=True)
class CodecArtifact:
    media_type: str
    data: bytes
    byte_length: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ValueError("artifact media_type must be a non-empty string")
        if not isinstance(self.data, bytes):
            raise TypeError("artifact data must be bytes")
        if self.byte_length != len(self.data):
            raise ValueError("artifact byte_length does not match data")
        if _SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError("artifact sha256 is not a lowercase SHA-256 digest")
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise ValueError("artifact sha256 does not match data")


@dataclass(frozen=True, slots=True)
class CodecResult:
    protocol: str
    operation: str
    context_id: str
    contract: ContractIdentity | None
    authority_sha256: str
    artifact: CodecArtifact | None


@dataclass(frozen=True, slots=True)
class PreparedBodySet:
    """Validated canonical bytes ready for a caller-owned publication CAS."""

    context_id: str
    contract: ContractIdentity
    canonical: CodecArtifact


class CodecFailure(RuntimeError):
    """Stable codec/process diagnostic which can never carry body payload."""

    __slots__ = (
        "code",
        "operation",
        "context_id",
        "cue_path",
        "json_pointer",
        "rule_id",
        "detail",
        "exit_status",
    )

    def __init__(
        self,
        code: str,
        operation: str,
        context_id: str = "",
        *,
        cue_path: str = "",
        json_pointer: str = "",
        rule_id: str = "",
        detail: str = "",
        exit_status: int | None = None,
    ) -> None:
        self.code = str(code or "PROCESS_FRAMING")
        self.operation = str(operation or "")
        self.context_id = str(context_id or "")
        self.cue_path = str(cue_path or "")
        self.json_pointer = str(json_pointer or "")
        self.rule_id = str(rule_id or "")
        self.detail = str(detail or "")
        self.exit_status = exit_status
        location = self.json_pointer or self.cue_path or "<process>"
        message = f"{self.code} operation={self.operation or '<unknown>'} location={location}"
        if self.rule_id:
            message += f" rule={self.rule_id}"
        if self.detail:
            message += f": {self.detail}"
        super().__init__(message)

    @property
    def location(self) -> str:
        return self.json_pointer or self.cue_path

    def as_dict(self) -> dict[str, Any]:
        """Return only diagnostic metadata; artifact/body fields do not exist."""

        result: dict[str, Any] = {
            "code": self.code,
            "operation": self.operation,
            "context_id": self.context_id,
            "cue_path": self.cue_path,
            "json_pointer": self.json_pointer,
            "rule_id": self.rule_id,
        }
        if self.exit_status is not None:
            result["exit_status"] = self.exit_status
        return result


class BodyCodecClient:
    """Supervise the versioned single-shot Go codec executable."""

    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
        source_root: str | os.PathLike[str] | None = None,
    ) -> None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self._explicit_executable = os.fspath(executable) if executable is not None else None
        self._timeout = float(timeout)
        self._env = dict(env) if env is not None else None
        self._source_root = Path(source_root).expanduser().resolve() if source_root is not None else None
        self._command: tuple[str, ...] | None = None
        self._discovery_source = ""
        self._handshake: CodecResult | None = None
        self._handshake_metadata: Mapping[str, Any] | None = None

    @property
    def executable(self) -> str:
        return self._discover_command()[0]

    @property
    def discovery_source(self) -> str:
        self._discover_command()
        return self._discovery_source

    @property
    def handshake_metadata(self) -> Mapping[str, Any]:
        self.handshake()
        assert self._handshake_metadata is not None
        return self._handshake_metadata

    def handshake(self, *, force: bool = False, timeout: float | None = None) -> CodecResult:
        if self._handshake is not None and not force:
            return self._handshake
        try:
            result = self._invoke(
                {"protocol": PROCESS_PROTOCOL, "operation": "handshake"}, timeout=timeout
            )
            metadata = self._validate_handshake(result)
        except CodecFailure:
            # PATH discovery is advisory: an older installed bundle must not
            # shadow the engine source pinned by this checkout, whether it
            # rejects the protocol, uses stale framing, or reports stale
            # metadata. Explicit and environment-selected commands are
            # operator choices, so their incompatibility remains fail-closed.
            if self._discovery_source != "installed":
                raise
            built = self._build_from_source()
            self._command = (str(built),)
            self._discovery_source = "source-build"
            result = self._invoke(
                {"protocol": PROCESS_PROTOCOL, "operation": "handshake"}, timeout=timeout
            )
            metadata = self._validate_handshake(result)
        self._handshake = result
        self._handshake_metadata = MappingProxyType(metadata)
        return result

    def request(
        self,
        operation: str,
        context_id: str | None = None,
        data: Any | None = None,
        *,
        expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None = None,
        profile: str | None = None,
        timeout: float | None = None,
    ) -> CodecResult:
        """Perform one protocol operation and return its validated response."""

        if operation not in _OPERATIONS:
            raise ValueError(f"unsupported codec operation: {operation!r}")
        if operation == "handshake":
            if context_id is not None or data is not None or expected is not None or profile is not None:
                raise ValueError("handshake does not accept body request fields")
            return self.handshake(timeout=timeout)

        self.handshake()
        request: dict[str, Any] = {"protocol": PROCESS_PROTOCOL, "operation": operation}
        if context_id is not None:
            if not isinstance(context_id, str) or not context_id:
                raise ValueError("context_id must be a non-empty string")
            request["context_id"] = context_id
        elif operation in _INPUT_OPERATIONS or operation == "schema":
            raise ValueError(f"{operation} requires context_id")

        expected_fields = _expected_fields(expected)
        request.update(expected_fields)
        if profile is not None:
            if not isinstance(profile, str) or not profile:
                raise ValueError("profile must be a non-empty string")
            request["profile"] = profile

        if data is not None:
            artifact = _coerce_input_bytes(data)
            if len(artifact) > MAX_ARTIFACT_BYTES:
                raise CodecFailure(
                    "INPUT_LIMIT",
                    operation,
                    context_id or "",
                    rule_id="max-artifact-bytes",
                )
            request["input_base64"] = b64encode(artifact).decode("ascii")
        elif operation in _INPUT_OPERATIONS:
            raise ValueError(f"{operation} requires input data")

        result = self._invoke(request, timeout=timeout)
        if self._handshake is not None and result.authority_sha256 != self._handshake.authority_sha256:
            raise CodecFailure(
                "AUTHORITY_DRIFT",
                operation,
                context_id or "",
                rule_id="handshake-authority-sha256",
            )
        _require_expected_contract(result, expected_fields)
        return result

    def emit(
        self,
        context_id: str,
        value: Mapping[str, Any] | bytes | bytearray | memoryview,
        *,
        expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> CodecArtifact:
        """Validate a projection and return its canonical CUE artifact."""

        result = self.request("emit", context_id, value, expected=expected, timeout=timeout)
        return _require_artifact(result)

    def parse(
        self,
        context_id: str,
        data: bytes | bytearray | memoryview,
        *,
        expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Admit current/legacy bytes and return the validated body projection."""

        result = self.request("parse", context_id, data, expected=expected, timeout=timeout)
        artifact = _require_artifact(result)
        decoded = strict_json_loads(artifact.data)
        if not isinstance(decoded, dict):
            raise CodecFailure(
                "PROCESS_FRAMING",
                "parse",
                context_id,
                rule_id="projection-object",
            )
        return decoded

    def vet(
        self,
        context_id: str,
        data: bytes | bytearray | memoryview,
        *,
        expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> CodecResult:
        return self.request("vet", context_id, data, expected=expected, timeout=timeout)

    def project(
        self,
        context_id: str,
        data: bytes | bytearray | memoryview,
        *,
        profile: str,
        expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> CodecArtifact:
        result = self.request(
            "project", context_id, data, expected=expected, profile=profile, timeout=timeout
        )
        return _require_artifact(result)

    def schema(
        self,
        context_id: str,
        *,
        profile: str,
        expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> CodecArtifact:
        result = self.request("schema", context_id, expected=expected, profile=profile, timeout=timeout)
        return _require_artifact(result)

    def prepare(
        self,
        context_id: str,
        value: Mapping[str, Any] | bytes | bytearray | memoryview,
        *,
        expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> PreparedBodySet:
        result = self.request("emit", context_id, value, expected=expected, timeout=timeout)
        artifact = _require_artifact(result)
        if result.contract is None:
            raise CodecFailure(
                "PROCESS_FRAMING",
                "emit",
                context_id,
                rule_id="prepared-contract-required",
            )
        return PreparedBodySet(context_id=context_id, contract=result.contract, canonical=artifact)

    def _discover_command(self) -> tuple[str, ...]:
        if self._command is not None:
            return self._command

        if self._explicit_executable is not None:
            resolved = _resolve_executable(self._explicit_executable)
            if resolved is None:
                raise CodecFailure(
                    "PROCESS_LAUNCH", "handshake", rule_id="explicit-executable-not-found"
                )
            self._command = (resolved,)
            self._discovery_source = "explicit"
            return self._command

        environment = self._process_environment()
        configured = environment.get(CODEC_EXECUTABLE_ENV, "").strip()
        if configured:
            resolved = _resolve_executable(configured, path=environment.get("PATH"))
            if resolved is None:
                raise CodecFailure(
                    "PROCESS_LAUNCH", "handshake", rule_id="environment-executable-not-found"
                )
            self._command = (resolved,)
            self._discovery_source = "environment"
            return self._command

        installed = shutil.which(CODEC_EXECUTABLE_NAME, path=environment.get("PATH"))
        if installed:
            self._command = (str(Path(installed).resolve()),)
            self._discovery_source = "installed"
            return self._command

        built = self._build_from_source()
        self._command = (str(built),)
        self._discovery_source = "source-build"
        return self._command

    def _build_from_source(self) -> Path:
        source = self._source_root or Path(__file__).resolve().parents[1] / "cuecodec"
        source = source.resolve()
        go_mod = source / "go.mod"
        go_version_file = source / ".go-version"
        command_dir = source / "cmd" / CODEC_EXECUTABLE_NAME
        if not go_mod.is_file() or not go_version_file.is_file() or not command_dir.is_dir():
            raise CodecFailure(
                "PROCESS_LAUNCH",
                "handshake",
                rule_id="pinned-source-unavailable",
            )
        go_version = go_version_file.read_text(encoding="utf-8").strip()
        go_mod_text = go_mod.read_text(encoding="utf-8")
        if go_version != PINNED_GO_RELEASE:
            raise CodecFailure(
                "PROCESS_LAUNCH", "handshake", rule_id="pinned-go-release-mismatch"
            )
        if not re.search(rf"(?m)^module\s+{re.escape(CODEC_MODULE)}\s*$", go_mod_text):
            raise CodecFailure("PROCESS_LAUNCH", "handshake", rule_id="pinned-module-mismatch")
        if not re.search(rf"(?m)^go\s+{re.escape(PINNED_GO_RELEASE)}\s*$", go_mod_text):
            raise CodecFailure("PROCESS_LAUNCH", "handshake", rule_id="pinned-go-mod-mismatch")
        if not re.search(
            rf"(?m)^\s*(?:require\s+)?cuelang\.org/go\s+{re.escape(PINNED_CUELANG_GO)}\s*$",
            go_mod_text,
        ):
            raise CodecFailure("PROCESS_LAUNCH", "handshake", rule_id="pinned-cue-module-mismatch")

        digest = _source_digest(source)
        cache_root = Path(
            self._process_environment().get(
                "AI_ORG_CUECODEC_CACHE",
                str(Path(tempfile.gettempdir()) / f"ai-org-cuecodec-{_safe_uid()}"),
            )
        ).expanduser().resolve()
        destination = cache_root / digest / CODEC_EXECUTABLE_NAME
        if destination.is_file() and os.access(destination, os.X_OK):
            return destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A PID alone is not unique across simultaneous client threads.  Every
        # builder gets a private output and publishes it with an atomic link;
        # all losers discard their complete equivalent build.
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        environment = self._process_environment()
        environment["GOTOOLCHAIN"] = "local"
        if not environment.get("GOCACHE"):
            go_cache = (cache_root / "go-build-cache").resolve()
            go_cache.mkdir(parents=True, exist_ok=True)
            environment["GOCACHE"] = str(go_cache)
        try:
            completed = subprocess.run(
                ["go", "build", "-trimpath", "-o", str(temporary), f"./cmd/{CODEC_EXECUTABLE_NAME}"],
                cwd=source,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(self._timeout, 60.0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            temporary.unlink(missing_ok=True)
            raise CodecFailure(
                "PROCESS_LAUNCH",
                "handshake",
                rule_id="source-build-launch",
                detail=type(exc).__name__,
            ) from exc
        if completed.returncode != 0:
            temporary.unlink(missing_ok=True)
            detail = _bounded_stderr(completed.stderr)
            raise CodecFailure(
                "PROCESS_LAUNCH",
                "handshake",
                rule_id="source-build-exit",
                detail=detail,
                exit_status=completed.returncode,
            )
        try:
            temporary.chmod(0o755)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                # Another builder with the same source digest won the race.
                if not destination.is_file() or not os.access(destination, os.X_OK):
                    raise CodecFailure(
                        "PROCESS_LAUNCH",
                        "handshake",
                        rule_id="source-build-cache-collision",
                    )
        finally:
            temporary.unlink(missing_ok=True)
        return destination.resolve()

    def _invoke(self, request: Mapping[str, Any], *, timeout: float | None) -> CodecResult:
        operation = str(request.get("operation") or "")
        context_id = str(request.get("context_id") or "")
        deadline = self._timeout if timeout is None else float(timeout)
        if deadline <= 0:
            raise ValueError("timeout must be positive")
        payload = strict_json_dumps(request)
        if len(payload) > MAX_PROCESS_REQUEST_BYTES:
            raise CodecFailure(
                "PROCESS_FRAMING", operation, context_id, rule_id="request-max-bytes"
            )
        command = self._discover_command()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._process_environment(),
                start_new_session=True,
            )
        except OSError as exc:
            raise CodecFailure(
                "PROCESS_LAUNCH",
                operation,
                context_id,
                rule_id="process-launch",
                detail=type(exc).__name__,
            ) from exc

        stdout_capture = _PipeCapture(MAX_PROCESS_RESPONSE_BYTES, keep_tail=False)
        stderr_capture = _PipeCapture(MAX_PROCESS_STDERR_BYTES, keep_tail=True)
        stdin_writer = _PipeWriter()
        assert process.stdout is not None and process.stderr is not None and process.stdin is not None
        stdin_thread = threading.Thread(
            target=stdin_writer.write,
            args=(process.stdin, payload),
            name="ai-org-cuecodec-stdin",
            daemon=True,
        )
        stdout_thread = threading.Thread(
            target=stdout_capture.drain,
            args=(process.stdout,),
            name="ai-org-cuecodec-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_capture.drain,
            args=(process.stderr,),
            name="ai-org-cuecodec-stderr",
            daemon=True,
        )
        drain_deadline = time.monotonic() + deadline
        stdout_thread.start()
        stderr_thread.start()
        stdin_thread.start()
        try:
            process.wait(timeout=deadline)
        except subprocess.TimeoutExpired as exc:
            _terminate_and_reap(process)
            _terminate_process_group(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            for capture_thread in (stdin_thread, stdout_thread, stderr_thread):
                capture_thread.join(timeout=1.0)
            raise CodecFailure(
                "PROCESS_TIMEOUT",
                operation,
                context_id,
                rule_id="process-deadline",
            ) from exc

        for capture_thread in (stdin_thread, stdout_thread, stderr_thread):
            capture_thread.join(timeout=max(0.0, drain_deadline - time.monotonic()))
        if stdin_thread.is_alive() or stdout_thread.is_alive() or stderr_thread.is_alive():
            _terminate_process_group(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            for capture_thread in (stdin_thread, stdout_thread, stderr_thread):
                capture_thread.join(timeout=0.25)
            if process.returncode != 0:
                raise CodecFailure(
                    "PROCESS_EXIT",
                    operation,
                    context_id,
                    rule_id="process-exit",
                    exit_status=process.returncode,
                )
            raise CodecFailure(
                "PROCESS_TIMEOUT", operation, context_id, rule_id="pipe-drain-deadline"
            )
        stdout = stdout_capture.data
        stderr = stderr_capture.data

        if process.returncode != 0:
            raise CodecFailure(
                "PROCESS_EXIT",
                operation,
                context_id,
                rule_id="process-exit",
                detail=_bounded_stderr(stderr),
                exit_status=process.returncode,
            )
        if stdin_writer.error is not None:
            raise CodecFailure(
                "PROCESS_FRAMING", operation, context_id, rule_id="stdin-write"
            )
        if stdout_capture.error is not None:
            raise CodecFailure(
                "PROCESS_FRAMING", operation, context_id, rule_id="stdout-read"
            )
        if stderr_capture.error is not None:
            raise CodecFailure(
                "PROCESS_FRAMING", operation, context_id, rule_id="stderr-read"
            )
        if stdout_capture.overflow:
            raise CodecFailure(
                "PROCESS_FRAMING", operation, context_id, rule_id="stdout-max-bytes"
            )
        if stderr_capture.overflow:
            raise CodecFailure(
                "PROCESS_FRAMING", operation, context_id, rule_id="stderr-max-bytes"
            )
        return _decode_response(stdout, operation=operation, requested_context=context_id)

    def _process_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        if self._env is not None:
            environment.update(self._env)
        return environment

    def _validate_handshake(self, result: CodecResult) -> dict[str, Any]:
        if result.operation != "handshake" or result.artifact is None:
            raise CodecFailure(
                "HANDSHAKE", "handshake", rule_id="handshake-artifact-required"
            )
        try:
            metadata = strict_json_loads(result.artifact.data)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CodecFailure(
                "HANDSHAKE", "handshake", rule_id="handshake-metadata-json"
            ) from exc
        if not isinstance(metadata, dict):
            raise CodecFailure("HANDSHAKE", "handshake", rule_id="handshake-metadata-object")

        required_values = {
            "protocol": (PROCESS_PROTOCOL, ("protocol",)),
            "public_contract": (ENGINE_PUBLIC_CONTRACT, ("public_contract",)),
            "module": (CODEC_MODULE, ("module", "module_path")),
            "go_release": (PINNED_GO_RELEASE, ("go_release", "go_version")),
            "cue_language": (
                PINNED_CUE_LANGUAGE,
                ("cue_language", "cue_language_version"),
            ),
            "cuelang_go": (PINNED_CUELANG_GO, ("cuelang_go", "cuelang_go_version")),
        }
        normalized = dict(metadata)
        for canonical, (expected, aliases) in required_values.items():
            value = _first_string(metadata, aliases)
            if value != expected:
                raise CodecFailure(
                    "HANDSHAKE", "handshake", rule_id=f"handshake-{canonical}"
                )
            normalized[canonical] = value

        authority = _first_string(metadata, ("authority_sha256", "authority_digest"))
        catalog = _first_string(metadata, ("catalog_sha256", "catalog_digest"))
        manifest = _first_string(metadata, ("manifest_sha256", "manifest_digest"))
        public_contract = _first_string(metadata, ("public_contract_sha256",))
        for name, value in (
            ("authority_sha256", authority),
            ("catalog_sha256", catalog),
            ("manifest_sha256", manifest),
            ("public_contract_sha256", public_contract),
        ):
            if _SHA256_RE.fullmatch(value) is None:
                raise CodecFailure("HANDSHAKE", "handshake", rule_id=f"handshake-{name}")
            normalized[name] = value
        expected_digests = {
            "authority_sha256": EXPECTED_AUTHORITY_SHA256,
            "catalog_sha256": EXPECTED_CATALOG_SHA256,
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "public_contract_sha256": EXPECTED_PUBLIC_CONTRACT_SHA256,
        }
        for name, expected in expected_digests.items():
            if normalized[name] != expected:
                raise CodecFailure("HANDSHAKE", "handshake", rule_id=f"handshake-{name}")
        if authority != result.authority_sha256:
            raise CodecFailure(
                "HANDSHAKE", "handshake", rule_id="handshake-authority-binding"
            )
        return normalized


def _expected_fields(
    expected: ContractIdentity | Mapping[str, str] | Sequence[str] | None,
) -> dict[str, str]:
    if expected is None:
        return {}
    if isinstance(expected, ContractIdentity):
        return expected.as_dict()
    if isinstance(expected, Mapping):
        result: dict[str, str] = {}
        aliases = {
            "api_version": ("api_version", "apiVersion"),
            "kind": ("kind",),
            "variant": ("variant",),
        }
        for canonical, names in aliases.items():
            value = next((expected[name] for name in names if name in expected), None)
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError(f"expected {canonical} must be a non-empty string")
                result[canonical] = value
        return result
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        if len(expected) != 3 or not all(isinstance(value, str) and value for value in expected):
            raise ValueError("expected sequence must contain api_version, kind, and variant")
        return {"api_version": expected[0], "kind": expected[1], "variant": expected[2]}
    raise TypeError("expected must be ContractIdentity, mapping, three-item sequence, or None")


def _coerce_input_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    return strict_json_dumps(data)


def _resolve_executable(command: str, *, path: str | None = None) -> str | None:
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
        return None
    resolved = shutil.which(command, path=path)
    return str(Path(resolved).resolve()) if resolved else None


def _decode_response(stdout: bytes, *, operation: str, requested_context: str) -> CodecResult:
    if not stdout:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="stdout-empty"
        )
    if b"\r" in stdout or stdout.count(b"\n") != 1 or not stdout.endswith(b"\n"):
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="response-terminal-lf"
        )
    try:
        response = strict_json_loads(stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="stdout-json"
        ) from exc
    if not isinstance(response, dict):
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="response-object"
        )
    if response.get("protocol") != PROCESS_PROTOCOL:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="response-protocol"
        )
    if response.get("operation") != operation:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="response-operation"
        )
    if not isinstance(response.get("ok"), bool):
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="response-ok"
        )
    response_context = response.get("context_id", "")
    if not isinstance(response_context, str):
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="response-context"
        )
    if requested_context and response_context != requested_context:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="response-context"
        )

    if response["ok"] is False:
        if "artifact" in response:
            raise CodecFailure(
                "PROCESS_FRAMING", operation, requested_context, rule_id="failure-has-artifact"
            )
        failure = response.get("failure")
        if not isinstance(failure, dict):
            raise CodecFailure(
                "PROCESS_FRAMING", operation, requested_context, rule_id="failure-object"
            )
        raise CodecFailure(
            _string_field(failure, "code", default="CODEC_FAILURE"),
            _string_field(failure, "operation", default=operation),
            _string_field(failure, "context_id", default=response_context),
            cue_path=_string_field(failure, "cue_path"),
            json_pointer=_string_field(failure, "json_pointer"),
            rule_id=_string_field(failure, "rule_id"),
        )
    if "failure" in response:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="success-has-failure"
        )

    authority = response.get("authority_sha256")
    if not isinstance(authority, str) or _SHA256_RE.fullmatch(authority) is None:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, requested_context, rule_id="authority-sha256"
        )
    contract = _decode_contract(response.get("contract"), operation, requested_context)
    artifact_value = response.get("artifact")
    artifact = None
    if artifact_value is not None:
        artifact = _decode_artifact(artifact_value, operation, requested_context)
    return CodecResult(
        protocol=PROCESS_PROTOCOL,
        operation=operation,
        context_id=response_context,
        contract=contract,
        authority_sha256=authority,
        artifact=artifact,
    )


def _decode_contract(value: Any, operation: str, context_id: str) -> ContractIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CodecFailure("PROCESS_FRAMING", operation, context_id, rule_id="contract-object")
    if not value:
        return None
    try:
        return ContractIdentity(
            _string_field(value, "api_version"),
            _string_field(value, "kind"),
            _string_field(value, "variant"),
        )
    except ValueError as exc:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, context_id, rule_id="contract-identity"
        ) from exc


def _decode_artifact(value: Any, operation: str, context_id: str) -> CodecArtifact:
    if not isinstance(value, dict):
        raise CodecFailure("PROCESS_FRAMING", operation, context_id, rule_id="artifact-object")
    media_type = _string_field(value, "media_type")
    encoded = _string_field(value, "data_base64")
    digest = _string_field(value, "sha256")
    try:
        data = b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, context_id, rule_id="artifact-base64"
        ) from exc
    length_value = value.get("byte_length")
    try:
        length = _integer_value(length_value)
    except (TypeError, ValueError) as exc:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, context_id, rule_id="artifact-byte-length"
        ) from exc
    if length != len(data):
        raise CodecFailure(
            "PROCESS_FRAMING", operation, context_id, rule_id="artifact-byte-length"
        )
    if len(data) > MAX_ARTIFACT_BYTES:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, context_id, rule_id="artifact-max-bytes"
        )
    if _SHA256_RE.fullmatch(digest) is None or hashlib.sha256(data).hexdigest() != digest:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, context_id, rule_id="artifact-sha256"
        )
    try:
        return CodecArtifact(media_type, data, length, digest)
    except (TypeError, ValueError) as exc:
        raise CodecFailure(
            "PROCESS_FRAMING", operation, context_id, rule_id="artifact-contract"
        ) from exc


def _integer_value(value: Any) -> int:
    if isinstance(value, ExactJSONNumber):
        return value.as_int_exact()
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError("value is not an integer")


def _string_field(mapping: Mapping[str, Any], key: str, *, default: str = "") -> str:
    value = mapping.get(key, default)
    if not isinstance(value, str):
        raise CodecFailure("PROCESS_FRAMING", "", rule_id=f"{key}-string")
    return value


def _require_artifact(result: CodecResult) -> CodecArtifact:
    if result.artifact is None:
        raise CodecFailure(
            "PROCESS_FRAMING",
            result.operation,
            result.context_id,
            rule_id="success-artifact-required",
        )
    return result.artifact


def _require_expected_contract(result: CodecResult, expected: Mapping[str, str]) -> None:
    if not expected:
        return
    if result.contract is None:
        raise CodecFailure(
            "PROCESS_FRAMING",
            result.operation,
            result.context_id,
            rule_id="response-contract-required",
        )
    actual = result.contract.as_dict()
    for key, value in expected.items():
        if actual[key] != value:
            raise CodecFailure(
                "IDENTITY",
                result.operation,
                result.context_id,
                cue_path={"api_version": "apiVersion", "kind": "kind", "variant": "variant"}[key],
                rule_id="response-contract-mismatch",
            )


def _first_string(mapping: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            return value
    return ""


class _PipeCapture:
    """Drain one child pipe while retaining at most its declared byte limit."""

    __slots__ = ("limit", "keep_tail", "data", "overflow", "error")

    def __init__(self, limit: int, *, keep_tail: bool) -> None:
        self.limit = limit
        self.keep_tail = keep_tail
        self.data = b""
        self.overflow = False
        self.error: OSError | None = None

    def drain(self, stream: Any) -> None:
        retained = bytearray()
        total = 0
        try:
            while True:
                chunk = stream.read(64 << 10)
                if not chunk:
                    break
                total += len(chunk)
                if self.keep_tail:
                    retained.extend(chunk)
                    if len(retained) > self.limit:
                        del retained[: len(retained) - self.limit]
                elif len(retained) < self.limit:
                    retained.extend(chunk[: self.limit - len(retained)])
        except (OSError, ValueError) as exc:
            self.error = exc
        finally:
            self.overflow = total > self.limit
            self.data = bytes(retained)
            try:
                stream.close()
            except (OSError, ValueError):
                pass


class _PipeWriter:
    """Write and close child stdin without allowing pipe backpressure to stall the deadline."""

    __slots__ = ("error",)

    def __init__(self) -> None:
        self.error: OSError | ValueError | None = None

    def write(self, stream: Any, data: bytes) -> None:
        try:
            stream.write(data)
        except BrokenPipeError:
            # The process exit result owns this normal early-close condition.
            pass
        except (OSError, ValueError) as exc:
            self.error = exc
        finally:
            try:
                stream.close()
            except BrokenPipeError:
                pass
            except (OSError, ValueError) as exc:
                self.error = self.error or exc


def _terminate_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:  # pragma: no cover - exercised by platform CI when applicable
            process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=0.25)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover
            process.kill()
    except OSError:
        pass
    process.wait()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best-effort cleanup for descendants that retain inherited pipes."""

    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:  # pragma: no cover - platform CI
            process.kill()
    except OSError:
        pass


def _bounded_stderr(stderr: bytes) -> str:
    bounded = stderr[-2000:]
    return bounded.decode("utf-8", errors="replace").strip()


def _source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.name in {"go.mod", "go.sum", ".go-version"}
            or path.suffix in {".go", ".cue", ".json"}
        )
        and ".git" not in path.parts
    )
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _safe_uid() -> int:
    try:
        return os.getuid()
    except AttributeError:  # pragma: no cover
        return os.getpid()


__all__ = [
    "BODY_API_VERSION",
    "BodyCodecClient",
    "CODEC_EXECUTABLE_ENV",
    "CodecArtifact",
    "CodecFailure",
    "CodecResult",
    "ContractIdentity",
    "DuplicateJSONKey",
    "ExactJSONNumber",
    "EXPECTED_AUTHORITY_SHA256",
    "EXPECTED_CATALOG_SHA256",
    "EXPECTED_MANIFEST_SHA256",
    "EXPECTED_PUBLIC_CONTRACT_SHA256",
    "ENGINE_PUBLIC_CONTRACT",
    "PRODUCER_LIFECYCLE_MANIFEST",
    "PINNED_PRODUCER_LIFECYCLE_IDENTITIES",
    "PreparedBodySet",
    "PROCESS_PROTOCOL",
    "dumps_exact_json",
    "loads_exact_json",
    "strict_json_dumps",
    "strict_json_loads",
]
