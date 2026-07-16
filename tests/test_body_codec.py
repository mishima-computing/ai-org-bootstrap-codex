from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import textwrap
import threading
import time

import pytest

from ai_org import body_codec
from ai_org.body_codec import (
    BODY_API_VERSION,
    BodyCodecClient,
    CodecFailure,
    ContractIdentity,
    DuplicateJSONKey,
    ExactJSONNumber,
    PROCESS_PROTOCOL,
    strict_json_dumps,
    strict_json_loads,
)


SEMANTIC_CONTEXT = "semantic-status-note-v1"
SEMANTIC_CONTRACT = ContractIdentity(BODY_API_VERSION, "SemanticStatus", "git-note")
ROOT = Path(__file__).resolve().parents[1]


def test_exact_json_number_is_immutable_and_converts_only_explicitly():
    integer = ExactJSONNumber("900719925474099312345678901234567890")
    decimal = ExactJSONNumber("1.230000000000000000000e+2")
    fractional = ExactJSONNumber("0.0000000000000000001")

    assert integer.as_int_exact() == 900719925474099312345678901234567890
    assert decimal.as_decimal() == Decimal("123.0000000000000000000")
    assert decimal.as_int_exact() == 123
    assert str(ExactJSONNumber("-0")) == "-0"
    with pytest.raises(ValueError, match="not an exact integer"):
        fractional.as_int_exact()
    with pytest.raises(FrozenInstanceError):
        integer.lexeme = "1"  # type: ignore[misc]

    for invalid in ("", "+1", "01", "1.", ".1", "1e", "NaN", "Infinity", " 1"):
        with pytest.raises(ValueError, match="invalid JSON number"):
            ExactJSONNumber(invalid)


def test_strict_json_helpers_reject_duplicates_and_round_trip_number_lexemes():
    source = (
        b'{"integer":900719925474099312345678901234567890,'
        b'"decimal":0.1000000000000000000000000001,'
        b'"exponent":1.234567890123456789e+42,"negative_zero":-0}'
    )

    decoded = strict_json_loads(source)

    assert all(isinstance(value, ExactJSONNumber) for value in decoded.values())
    assert strict_json_dumps(decoded) == (
        b'{"decimal":0.1000000000000000000000000001,'
        b'"exponent":1.234567890123456789e+42,'
        b'"integer":900719925474099312345678901234567890,"negative_zero":-0}'
    )
    with pytest.raises(DuplicateJSONKey, match="duplicate JSON object key"):
        strict_json_loads('{"same":1,"same":2}')
    with pytest.raises(ValueError, match="numeric constant"):
        strict_json_loads('{"value":NaN}')
    with pytest.raises(TypeError, match="ExactJSONNumber"):
        strict_json_dumps({"rounded": 0.1})


def test_strict_json_dumps_bounds_depth_and_rejects_cycles():
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="circular JSON array"):
        strict_json_dumps(cyclic)

    value: object = "leaf"
    for _ in range(4):
        value = [value]
    with pytest.raises(ValueError, match="max depth 3"):
        strict_json_dumps(value, max_depth=3)

    below = "[" * 63 + "0" + "]" * 63
    exact = "[" * 64 + "0" + "]" * 64
    above = "[" * 65 + "0" + "]" * 65
    assert isinstance(strict_json_loads(below), list)
    assert isinstance(strict_json_loads(exact), list)
    with pytest.raises(ValueError, match="max depth 64"):
        strict_json_loads(above)


def test_client_handshakes_once_then_uses_one_process_per_operation(tmp_path):
    executable = _fake_codec(tmp_path)
    log = tmp_path / "requests.jsonl"
    client = BodyCodecClient(executable, env={"FAKE_CODEC_LOG": str(log)})
    projection = {
        "change_kind": "behavior",
        "subsystem": "patchwork",
        "owner": "maintainer",
        "working_state": "green",
        "large": ExactJSONNumber("900719925474099312345678901234567890"),
    }

    canonical = client.emit(SEMANTIC_CONTEXT, projection, expected=SEMANTIC_CONTRACT)
    parsed = client.parse(
        SEMANTIC_CONTEXT,
        b'{"change_kind":"behavior","subsystem":"patchwork","owner":"maintainer",'
        b'"working_state":"green","large":900719925474099312345678901234567890}',
        expected={"apiVersion": BODY_API_VERSION, "kind": "SemanticStatus", "variant": "git-note"},
    )

    assert client.discovery_source == "explicit"
    assert client.handshake_metadata["module"] == "github.com/mishima-computing/cuecodec"
    with pytest.raises(TypeError):
        client.handshake_metadata["module"] = "changed"  # type: ignore[index]
    assert canonical.media_type == "application/cue"
    assert canonical.data.endswith(b"\n")
    assert isinstance(parsed["large"], ExactJSONNumber)
    assert parsed["large"].lexeme == "900719925474099312345678901234567890"
    operations = [json.loads(line)["operation"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert operations == ["handshake", "emit", "parse"]


def test_prepare_returns_frozen_server_resolved_body_set(tmp_path):
    client = BodyCodecClient(_fake_codec(tmp_path))

    prepared = client.prepare(
        SEMANTIC_CONTEXT,
        {
            "change_kind": "behavior",
            "subsystem": "patchwork",
            "owner": "maintainer",
            "working_state": "green",
        },
    )

    assert prepared.context_id == SEMANTIC_CONTEXT
    assert prepared.contract == SEMANTIC_CONTRACT
    assert prepared.canonical.data.endswith(b"\n")
    with pytest.raises(FrozenInstanceError):
        prepared.context_id = "changed"  # type: ignore[misc]


def test_remote_failure_has_stable_location_and_cannot_carry_payload(tmp_path):
    client = BodyCodecClient(_fake_codec(tmp_path))

    with pytest.raises(CodecFailure) as caught:
        client.parse("remote-failure", b"{}")

    failure = caught.value
    assert failure.code == "NOT_ADMITTED"
    assert failure.operation == "parse"
    assert failure.context_id == "remote-failure"
    assert failure.location == "context_id"
    assert failure.rule_id == "context-not-admitted"
    assert not hasattr(failure, "payload")
    assert "artifact" not in failure.as_dict()
    assert "body" not in failure.as_dict()


@pytest.mark.parametrize(
    ("context_id", "code", "rule_id"),
    [
        ("malformed-frame", "PROCESS_FRAMING", "stdout-json"),
        ("no-terminal-lf", "PROCESS_FRAMING", "response-terminal-lf"),
        ("bad-artifact", "PROCESS_FRAMING", "artifact-sha256"),
        ("exit", "PROCESS_EXIT", "process-exit"),
        ("oversized-stdout", "PROCESS_FRAMING", "stdout-max-bytes"),
        ("oversized-stderr", "PROCESS_FRAMING", "stderr-max-bytes"),
    ],
)
def test_process_and_framing_failures_are_deterministic(tmp_path, context_id, code, rule_id):
    client = BodyCodecClient(_fake_codec(tmp_path))

    with pytest.raises(CodecFailure) as caught:
        client.parse(context_id, b"{}")

    assert caught.value.code == code
    assert caught.value.rule_id == rule_id
    assert caught.value.context_id == context_id


def test_timeout_terminates_and_reaps_process_group(tmp_path):
    executable = _fake_codec(tmp_path)
    log = tmp_path / "requests.jsonl"
    client = BodyCodecClient(executable, timeout=0.5, env={"FAKE_CODEC_LOG": str(log)})

    with pytest.raises(CodecFailure) as caught:
        client.parse("timeout", b"{}")

    assert caught.value.code == "PROCESS_TIMEOUT"
    assert caught.value.rule_id == "process-deadline"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    timed_out_pid = records[-1]["pid"]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _pid_exists(timed_out_pid):
        time.sleep(0.01)
    assert _pid_exists(timed_out_pid) is False


def test_handshake_rejects_a_self_consistent_but_unpinned_bundle(tmp_path):
    executable = _fake_codec(tmp_path)
    source = executable.read_text(encoding="utf-8")
    executable.write_text(
        source.replace(body_codec.EXPECTED_AUTHORITY_SHA256, "d" * 64),
        encoding="utf-8",
    )

    with pytest.raises(CodecFailure) as caught:
        BodyCodecClient(executable).handshake()

    assert caught.value.code == "HANDSHAKE"
    assert caught.value.rule_id == "handshake-authority_sha256"


def test_producer_lifecycle_codec_identity_cohort_is_exact_and_closed():
    assert (
        body_codec.PRODUCER_LIFECYCLE_MANIFEST
        == "producer-lifecycle-contract-matrix-v1"
    )
    assert body_codec.PINNED_PRODUCER_LIFECYCLE_IDENTITIES == (
        (
            "producer-promise-v1",
            "ai-org-cue-body-v1",
            "ProducerPromise",
            "producer-commitment",
        ),
        (
            "producer-task-binding-v1",
            "ai-org-cue-body-v1",
            "ProducerTaskBinding",
            "contribution-task",
        ),
        (
            "producer-completion-assertion-v1",
            "ai-org-cue-body-v1",
            "ProducerCompletionAssertion",
            "producer-claim",
        ),
        (
            "claim-admission-v1",
            "ai-org-cue-body-v1",
            "ClaimAdmission",
            "functional-acceptance",
        ),
        (
            "functional-acceptance-v1",
            "ai-org-cue-body-v1",
            "FunctionalAcceptanceVerdict",
            "contribution",
        ),
        (
            "acceptance-authority-seal-v1",
            "ai-org-cue-body-v1",
            "AcceptanceAuthoritySeal",
            "verifier-authority",
        ),
    )


def test_discovery_precedence_is_explicit_then_environment_then_path(tmp_path):
    explicit = _fake_codec(tmp_path / "explicit")
    configured = _fake_codec(tmp_path / "configured")
    installed_dir = tmp_path / "installed"
    installed = _fake_codec(installed_dir, name="ai-org-cuecodec")
    path = str(installed_dir)

    explicit_client = BodyCodecClient(
        explicit,
        env={"AI_ORG_CUECODEC": str(configured), "PATH": path},
    )
    configured_client = BodyCodecClient(env={"AI_ORG_CUECODEC": str(configured), "PATH": path})
    installed_client = BodyCodecClient(env={"AI_ORG_CUECODEC": "", "PATH": path})

    assert Path(explicit_client.executable) == explicit.resolve()
    assert explicit_client.discovery_source == "explicit"
    assert Path(configured_client.executable) == configured.resolve()
    assert configured_client.discovery_source == "environment"
    assert Path(installed_client.executable) == installed.resolve()
    assert installed_client.discovery_source == "installed"


def test_incompatible_installed_codec_yields_to_pinned_source(tmp_path, monkeypatch):
    installed_dir = tmp_path / "installed"
    installed = _make_codec_stale(_fake_codec(installed_dir, name="ai-org-cuecodec"))
    pinned = _fake_codec(tmp_path / "pinned")
    log = tmp_path / "requests.jsonl"
    client = BodyCodecClient(
        env={
            "AI_ORG_CUECODEC": "",
            "PATH": str(installed_dir),
            "FAKE_CODEC_LOG": str(log),
        }
    )
    monkeypatch.setattr(client, "_build_from_source", lambda: pinned.resolve())

    assert Path(client.executable) == installed.resolve()
    assert client.discovery_source == "installed"

    client.handshake()

    assert Path(client.executable) == pinned.resolve()
    assert client.discovery_source == "source-build"
    assert [
        json.loads(line)["operation"]
        for line in log.read_text(encoding="utf-8").splitlines()
    ] == ["handshake", "handshake"]


def test_installed_codec_with_stale_protocol_exit_yields_to_pinned_source(
    tmp_path, monkeypatch
):
    installed_dir = tmp_path / "installed"
    installed_dir.mkdir()
    installed = installed_dir / "ai-org-cuecodec"
    installed.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    installed.chmod(0o755)
    pinned = _fake_codec(tmp_path / "pinned")
    client = BodyCodecClient(
        env={"AI_ORG_CUECODEC": "", "PATH": str(installed_dir)}
    )
    monkeypatch.setattr(client, "_build_from_source", lambda: pinned.resolve())

    client.handshake()

    assert Path(client.executable) == pinned.resolve()
    assert client.discovery_source == "source-build"


def test_compatible_installed_codec_keeps_path_precedence(tmp_path, monkeypatch):
    installed_dir = tmp_path / "installed"
    installed = _fake_codec(installed_dir, name="ai-org-cuecodec")
    client = BodyCodecClient(env={"AI_ORG_CUECODEC": "", "PATH": str(installed_dir)})

    def unexpected_source_build():
        pytest.fail("compatible installed codec must not build from source")

    monkeypatch.setattr(client, "_build_from_source", unexpected_source_build)

    client.handshake()

    assert Path(client.executable) == installed.resolve()
    assert client.discovery_source == "installed"


@pytest.mark.parametrize("selection", ["explicit", "environment"])
def test_incompatible_configured_codec_fails_closed_without_source_fallback(
    tmp_path, monkeypatch, selection
):
    configured = _make_codec_stale(_fake_codec(tmp_path / "configured"))
    pinned = _fake_codec(tmp_path / "pinned")
    if selection == "explicit":
        client = BodyCodecClient(configured, env={"AI_ORG_CUECODEC": "", "PATH": ""})
    else:
        client = BodyCodecClient(
            env={"AI_ORG_CUECODEC": str(configured), "PATH": ""}
        )
    monkeypatch.setattr(client, "_build_from_source", lambda: pinned.resolve())

    with pytest.raises(CodecFailure) as caught:
        client.handshake()

    assert caught.value.code == "HANDSHAKE"
    assert caught.value.rule_id == "handshake-authority_sha256"
    assert Path(client.executable) == configured.resolve()
    assert client.discovery_source == selection


def test_missing_explicit_executable_fails_without_falling_back(tmp_path):
    client = BodyCodecClient(tmp_path / "missing")

    with pytest.raises(CodecFailure) as caught:
        client.handshake()

    assert caught.value.code == "PROCESS_LAUNCH"
    assert caught.value.rule_id == "explicit-executable-not-found"


def test_pinned_source_build_is_cwd_independent_and_uses_local_toolchain(tmp_path, monkeypatch):
    source = _fake_source_tree(tmp_path / "engine" / "cuecodec")
    cache = tmp_path / "cache"
    calls: list[tuple[list[str], Path, str, str]] = []

    def fake_run(command, *, cwd, env, **_kwargs):
        destination = Path(command[command.index("-o") + 1])
        destination.write_bytes(b"fake executable")
        calls.append((command, Path(cwd), env["GOTOOLCHAIN"], env["GOCACHE"]))
        return body_codec.subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(body_codec.subprocess, "run", fake_run)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    client = BodyCodecClient(
        source_root=source,
        env={"PATH": "", "AI_ORG_CUECODEC": "", "AI_ORG_CUECODEC_CACHE": str(cache)},
    )

    executable = Path(client.executable)

    assert client.discovery_source == "source-build"
    assert executable.is_file()
    assert executable.parent.parent == cache
    assert len(calls) == 1
    command, cwd, toolchain, go_cache = calls[0]
    assert command[:4] == ["go", "build", "-trimpath", "-o"]
    assert command[-1] == "./cmd/ai-org-cuecodec"
    assert Path(command[4]).parent == executable.parent
    assert Path(command[4]).name.startswith(f".{executable.name}.{os.getpid()}.")
    assert cwd == source.resolve()
    assert toolchain == "local"
    assert Path(go_cache) == (cache / "go-build-cache").resolve()
    assert Path(go_cache).is_dir()


def test_source_digest_covers_embedded_registry_json(tmp_path):
    source = _fake_source_tree(tmp_path / "cuecodec")
    authority = source / "cue" / "engine" / "registry.json"
    authority.parent.mkdir(parents=True)
    authority.write_text('{"revision":1}\n', encoding="utf-8")
    before = body_codec._source_digest(source)

    authority.write_text('{"revision":2}\n', encoding="utf-8")

    assert body_codec._source_digest(source) != before


def test_concurrent_source_builds_use_unique_outputs_and_one_atomic_winner(tmp_path, monkeypatch):
    source = _fake_source_tree(tmp_path / "cuecodec")
    cache = tmp_path / "cache"
    barrier = threading.Barrier(2)
    temporary_paths: list[Path] = []
    lock = threading.Lock()

    def fake_run(command, **_kwargs):
        temporary = Path(command[command.index("-o") + 1])
        with lock:
            temporary_paths.append(temporary)
        barrier.wait(timeout=2)
        temporary.write_bytes(b"same complete executable")
        return body_codec.subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(body_codec.subprocess, "run", fake_run)
    environment = {
        "PATH": "",
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(cache),
    }
    clients = [
        BodyCodecClient(source_root=source, env=environment),
        BodyCodecClient(source_root=source, env=environment),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        executables = list(pool.map(lambda client: client.executable, clients))

    assert executables[0] == executables[1]
    assert Path(executables[0]).read_bytes() == b"same complete executable"
    assert len(set(temporary_paths)) == 2
    assert all(not path.exists() for path in temporary_paths)


def test_response_contract_is_checked_against_expected_identity(tmp_path):
    client = BodyCodecClient(_fake_codec(tmp_path))

    with pytest.raises(CodecFailure) as caught:
        client.parse(
            SEMANTIC_CONTEXT,
            b"{}",
            expected=(BODY_API_VERSION, "WrongKind", "git-note"),
        )

    assert caught.value.code == "IDENTITY"
    assert caught.value.cue_path == "kind"
    assert caught.value.rule_id == "response-contract-mismatch"


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_real_bundle_is_deterministic_and_schema_is_definition_based(tmp_path, monkeypatch):
    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir()
    os.symlink(shutil.which("go"), tool_bin / "go")
    environment = {
        "AI_ORG_CUECODEC": "",
        "PATH": str(tool_bin),
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "bundle-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    }
    first = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    second = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    body = {
        "change_kind": "behavior",
        "subsystem": "codec",
        "owner": "維持担当",
        "working_state": "green",
    }

    canonical_a = first.emit(SEMANTIC_CONTEXT, body, expected=SEMANTIC_CONTRACT)
    canonical_b = second.emit(SEMANTIC_CONTEXT, body, expected=SEMANTIC_CONTRACT)
    projection = first.project(
        SEMANTIC_CONTEXT,
        canonical_a.data,
        expected=SEMANTIC_CONTRACT,
        profile="consumer-json-v1",
    )
    schema_a = first.schema(
        SEMANTIC_CONTEXT,
        expected=SEMANTIC_CONTRACT,
        profile="codex-structured-output-v1",
    )
    schema_b = second.schema(
        SEMANTIC_CONTEXT,
        expected=SEMANTIC_CONTRACT,
        profile="codex-structured-output-v1",
    )

    assert canonical_a.data == canonical_b.data
    assert first.discovery_source == second.discovery_source == "source-build"
    assert canonical_a.sha256 == canonical_b.sha256
    assert schema_a.data == schema_b.data and schema_a.sha256 == schema_b.sha256
    assert canonical_a.data.count(b"\n") == 1 and canonical_a.data.endswith(b"\n")
    assert "維持担当".encode() in canonical_a.data
    assert strict_json_loads(projection.data) == body
    schema = strict_json_loads(schema_a.data)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["required"]) == {"change_kind", "owner", "subsystem", "working_state"}

    inspection = strict_json_loads(first.request("inspect").artifact.data)  # type: ignore[union-attr]
    assert inspection["coverage"] == {
        "registered_sites": ExactJSONNumber("138"),
        "mapped_sites": ExactJSONNumber("138"),
        "admitted_sites": ExactJSONNumber("138"),
        "pending_sites": ExactJSONNumber("0"),
        "missing_rows": ExactJSONNumber("0"),
        "ambiguous_rows": ExactJSONNumber("0"),
        "orphaned_sites": ExactJSONNumber("0"),
        "admitted_scope_bypasses": ExactJSONNumber("0"),
    }
    assert inspection["catalog_sha256"] == first.handshake_metadata["catalog_sha256"]
    root_contract = next(
        contract
        for contract in inspection["contracts"]
        if contract["context_id"] == "root-technical-approach-tree-v1"
    )
    assert root_contract["contract"] == {
        "api_version": "ai-org-cue-body-v1",
        "kind": "TechnicalApproachTree",
        "variant": "patch-series-root",
    }
    assert root_contract["projection_profiles"] == ["consumer-json-v1"]
    assert root_contract["schema_profiles"] == []
    assert root_contract["manifest"] == "patch-series-root-cohort-v2"
    contracts_by_context = {
        contract["context_id"]: contract for contract in inspection["contracts"]
    }
    for context_id, api_version, kind, variant in (
        body_codec.PINNED_PRODUCER_LIFECYCLE_IDENTITIES
    ):
        contract = contracts_by_context[context_id]
        assert contract["contract"] == {
            "api_version": api_version,
            "kind": kind,
            "variant": variant,
        }
        assert contract["manifest"] == body_codec.PRODUCER_LIFECYCLE_MANIFEST
        assert contract["definition_path"] == f"schema/semantic_status.cue:#{kind}"
        assert len(contract["definition_sha256"]) == 64
        assert contract["projection_profiles"] == ["consumer-json-v1"]
        assert contract["schema_profiles"] == []
        assert contract["historical_aliases"] == []

    with pytest.raises(CodecFailure) as negative:
        first.emit(
            SEMANTIC_CONTEXT,
            {"change_kind": "behavior", "subsystem": "codec", "owner": "maintainer"},
            expected=SEMANTIC_CONTRACT,
        )
    assert negative.value.code == "SCHEMA_VALIDATION"
    assert negative.value.location in {"/working_state", "working_state"}

    with pytest.raises(CodecFailure) as unknown:
        first.parse("unregistered-context-v1", b"{}")
    assert unknown.value.code == "INVALID_ARGUMENT"
    assert unknown.value.rule_id == "context-unknown"
    assert not hasattr(unknown.value, "payload")

    # A target clone has no codec sources or schema assets. The exact built
    # bundle still works through both explicit and installed discovery from an
    # unrelated working directory.
    installed_bin = tmp_path / "installed-bin"
    installed_bin.mkdir()
    installed_executable = installed_bin / "ai-org-cuecodec"
    shutil.copy2(first.executable, installed_executable)
    target_clone = tmp_path / "fresh-target-clone"
    target_clone.mkdir()
    assert not (target_clone / "cuecodec").exists()
    monkeypatch.chdir(target_clone)
    explicit = BodyCodecClient(installed_executable)
    installed = BodyCodecClient(
        source_root=target_clone / "cuecodec",
        env={"AI_ORG_CUECODEC": "", "PATH": str(installed_bin)},
    )
    assert explicit.emit(SEMANTIC_CONTEXT, body, expected=SEMANTIC_CONTRACT).data == canonical_a.data
    assert installed.emit(SEMANTIC_CONTEXT, body, expected=SEMANTIC_CONTRACT).data == canonical_a.data
    assert explicit.discovery_source == "explicit"
    assert installed.discovery_source == "installed"


def _fake_source_tree(source: Path) -> Path:
    command_dir = source / "cmd" / "ai-org-cuecodec"
    command_dir.mkdir(parents=True)
    (source / ".go-version").write_text("1.26.5\n", encoding="utf-8")
    (source / "go.mod").write_text(
        "module github.com/mishima-computing/cuecodec\n\ngo 1.26.5\n\nrequire cuelang.org/go v0.17.0\n",
        encoding="utf-8",
    )
    (source / "go.sum").write_text("", encoding="utf-8")
    (command_dir / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    return source


def _fake_codec(directory: Path, *, name: str = "fake-codec") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / name
    source = f"""#!{sys.executable}
import base64
import hashlib
import json
import os
import sys
import time

PROTOCOL = {PROCESS_PROTOCOL!r}
AUTHORITY = {body_codec.EXPECTED_AUTHORITY_SHA256!r}
CONTRACT = {{"api_version": {BODY_API_VERSION!r}, "kind": "SemanticStatus", "variant": "git-note"}}

request = json.loads(sys.stdin.buffer.read())
log = os.environ.get("FAKE_CODEC_LOG")
if log:
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({{"operation": request.get("operation"), "pid": os.getpid()}}) + "\\n")

operation = request.get("operation")
context_id = request.get("context_id", "")
if context_id == "timeout":
    time.sleep(10)
if context_id == "exit":
    print("injected exit", file=sys.stderr)
    raise SystemExit(7)
if context_id == "oversized-stdout":
    sys.stdout.write("x" * (12 << 20))
    raise SystemExit(0)
if context_id == "oversized-stderr":
    sys.stderr.write("x" * ((64 << 10) + 1))
if context_id == "malformed-frame":
    sys.stdout.write("not-json\\n")
    raise SystemExit(0)

def artifact(data, media_type):
    return {{
        "media_type": media_type,
        "byte_length": len(data),
        "data_base64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
    }}

if operation == "handshake":
    metadata = json.dumps({{
        "protocol": PROTOCOL,
        "public_contract": {body_codec.ENGINE_PUBLIC_CONTRACT!r},
        "module": "github.com/mishima-computing/cuecodec",
        "go_release": "1.26.5",
        "cue_language": "v0.17.0",
        "cuelang_go": "v0.17.0",
        "authority_sha256": AUTHORITY,
        "catalog_sha256": {body_codec.EXPECTED_CATALOG_SHA256!r},
        "manifest_sha256": {body_codec.EXPECTED_MANIFEST_SHA256!r},
        "public_contract_sha256": {body_codec.EXPECTED_PUBLIC_CONTRACT_SHA256!r},
    }}, sort_keys=True, separators=(",", ":")).encode()
    response = {{
        "protocol": PROTOCOL,
        "operation": operation,
        "ok": True,
        "context_id": "",
        "contract": {{}},
        "authority_sha256": AUTHORITY,
        "artifact": artifact(metadata, "application/json"),
    }}
elif context_id == "remote-failure":
    response = {{
        "protocol": PROTOCOL,
        "operation": operation,
        "ok": False,
        "context_id": context_id,
        "failure": {{
            "code": "NOT_ADMITTED",
            "operation": operation,
            "context_id": context_id,
            "cue_path": "context_id",
            "json_pointer": "",
            "rule_id": "context-not-admitted",
        }},
    }}
else:
    incoming = base64.b64decode(request.get("input_base64", ""))
    if operation == "emit":
        output = incoming + b"\\n"
        media_type = "application/cue"
    elif operation == "parse":
        output = incoming
        media_type = "application/json"
    elif operation == "schema":
        output = b'{{"type":"object"}}'
        media_type = "application/schema+json"
    else:
        output = b""
        media_type = "application/octet-stream"
    item = artifact(output, media_type) if operation not in {{"vet", "inspect"}} else None
    if context_id == "bad-artifact" and item is not None:
        item["sha256"] = "0" * 64
    response = {{
        "protocol": PROTOCOL,
        "operation": operation,
        "ok": True,
        "context_id": context_id,
        "contract": CONTRACT,
        "authority_sha256": AUTHORITY,
    }}
    if item is not None:
        response["artifact"] = item

framed = json.dumps(response, sort_keys=True, separators=(",", ":"))
sys.stdout.write(framed if context_id == "no-terminal-lf" else framed + "\\n")
"""
    executable.write_text(textwrap.dedent(source), encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _make_codec_stale(executable: Path) -> Path:
    source = executable.read_text(encoding="utf-8")
    executable.write_text(
        source.replace(body_codec.EXPECTED_AUTHORITY_SHA256, "d" * 64),
        encoding="utf-8",
    )
    return executable


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
