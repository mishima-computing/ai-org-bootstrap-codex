from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from ai_org import patchwork_check_stream as stream
from ai_org import git_wrapper
from ai_org.body_codec import BodyCodecClient
from ai_org.patchwork_queue import patch_series_gate as network


OID = "1" * 40
ROOT = Path(__file__).resolve().parents[1]


def test_raw_stream_round_trip_and_replay_preserve_physical_records():
    raw = (
        b' {"var":"counter","op":"inc","value":2}\r\n'
        b'\r\n'
        b'{bad json}\n'
        b'{"var":"label","op":"set","value":"done"}'
    )
    body = stream.import_legacy_jsonl(raw, source_oid=OID, source_path="sub/a/patchwork-check-events.jsonl")

    assert stream.decode_raw_stream(body) == raw
    assert set(body) == {"raw_stream_base64", "source_oid", "source_path"}
    projection = stream.replay(body, {"counter": "counter", "label": "text"})

    assert projection.state == {"counter": 2, "label": "done"}
    assert projection.last_events == {
        "counter": {"path": "sub/a/patchwork-check-events.jsonl", "line": 1},
        "label": {"path": "sub/a/patchwork-check-events.jsonl", "line": 4},
    }
    assert [(item.code, item.line) for item in projection.diagnostics] == [("ingestion_invalid", 3)]


@pytest.mark.parametrize(
    "source_path",
    ["events.jsonl", "docs/patchwork-check-events.jsonl", "sub/a/other.jsonl", "sub/a/nested/patchwork-check-events.jsonl"],
)
def test_stream_body_rejects_source_paths_outside_registered_coordinates(source_path):
    with pytest.raises(stream.StreamFailure, match="stream-source-path"):
        stream.body_from_raw_stream(b"", source_oid=OID, source_path=source_path)


def test_stream_publication_binds_source_path_to_selected_node(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    expected = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            "patch-series-manifest.json": {
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
            },
            "sub/a/" + stream.CANONICAL_PATH: "opaque canonical body",
        },
        subject="fixture: mismatched stream anchor",
    )["commit"]

    class MismatchedCodec:
        def parse(self, *_args, **_kwargs):
            return stream.body_from_raw_stream(
                b"",
                source_oid=expected,
                source_path="sub/b/patchwork-check-events.jsonl",
            )

    with pytest.raises(git_wrapper.GitBodyFailure, match="stream-source-path-mismatch"):
        stream.append_to_git(
            repo,
            "main",
            "sub/a",
            b'{"var":"counter","op":"inc"}',
            {"counter": "counter"},
            expected_oid=expected,
            client=MismatchedCodec(),
        )

    assert git_wrapper.head_sha(repo, "main") == expected


def test_replay_does_not_treat_json_sequence_control_bytes_as_physical_lines():
    raw = (
        b'{"var":"counter","op":"set","value":1}\n'
        b'{bad\x1e{"var":"counter","op":"set","value":99}\n'
        b'{"var":"counter","op":"inc","value":2}'
    )
    body = stream.body_from_raw_stream(raw, source_oid=OID, source_path=stream.LEGACY_PATH)

    projection = stream.replay(body, {"counter": "counter"})

    assert projection.state == {"counter": 3}
    assert projection.last_events == {
        "counter": {"path": stream.LEGACY_PATH, "line": 3},
    }
    assert [(item.code, item.line) for item in projection.diagnostics] == [("ingestion_invalid", 2)]


def test_replay_treats_vertical_tab_and_form_feed_as_malformed_not_blank():
    raw = (
        b"\x0b\n"
        b"\x0c\r\n"
        b'{"var":"counter","op":"inc","value":2}'
    )
    body = stream.body_from_raw_stream(raw, source_oid=OID, source_path=stream.LEGACY_PATH)

    projection = stream.replay(body, {"counter": "counter"})

    assert projection.state == {"counter": 2}
    assert [(item.code, item.line) for item in projection.diagnostics] == [
        ("ingestion_invalid", 1),
        ("ingestion_invalid", 2),
    ]
    assert projection.last_events == {
        "counter": {"path": stream.LEGACY_PATH, "line": 3}
    }


def test_replay_preserves_bounded_legacy_diagnostics_and_continues():
    raw = b"\n".join([
        b"[]",
        b'{"var":[],"op":"set","value":1}',
        b'{"var":"counter","op":"set","value":true}',
        b'{"var":"counter","op":"inc","value":2}',
    ])
    body = stream.body_from_raw_stream(
        raw, source_oid=OID, source_path=stream.LEGACY_PATH
    )

    projection = stream.replay(body, {"counter": "counter"})

    assert projection.state == {"counter": 2}
    assert [item.message for item in projection.diagnostics] == [
        "state event must be an object",
        "state event 2 has invalid var",
        "state event 3 set value must be integer or string",
    ]
    assert projection.last_events == {
        "counter": {"path": stream.LEGACY_PATH, "line": 4}
    }


def test_replay_rejects_nonstandard_json_constant_and_continues_at_next_physical_line():
    raw = (
        b'{"var":"label","op":"set","value":"bad","metadata":NaN}\n'
        b'{"var":"label","op":"set","value":"good"}'
    )
    body = stream.body_from_raw_stream(raw, source_oid=OID, source_path=stream.LEGACY_PATH)

    projection = stream.replay(body, {"label": "text"})

    assert projection.state == {"label": "good"}
    assert projection.last_events == {"label": {"path": stream.LEGACY_PATH, "line": 2}}
    assert [(item.code, item.line, item.rule) for item in projection.diagnostics] == [
        ("ingestion_invalid", 1, "json-record")
    ]


def test_replay_accepts_depth_64_rejects_depth_65_and_continues():
    def event_with_depth(depth, value):
        arrays = depth - 1  # the event object is the first container
        return (
            b'{"var":"counter","op":"inc","value":'
            + str(value).encode("ascii")
            + b',"metadata":'
            + b"[" * arrays
            + b"0"
            + b"]" * arrays
            + b"}"
        )

    raw = b"\n".join(
        [
            event_with_depth(64, 1),
            event_with_depth(65, 100),
            b'{"var":"counter","op":"inc","value":2}',
        ]
    )
    body = stream.body_from_raw_stream(raw, source_oid=OID, source_path=stream.LEGACY_PATH)

    projection = stream.replay(body, {"counter": "counter"})

    assert projection.state == {"counter": 3}
    assert projection.last_events == {
        "counter": {"path": stream.LEGACY_PATH, "line": 3}
    }
    assert [(item.line, item.rule) for item in projection.diagnostics] == [
        (2, "record-depth-64")
    ]


def test_replay_can_continue_the_gate_state_across_enclosing_streams():
    first = stream.body_from_raw_stream(
        b'{"var":"counter","op":"inc","value":2}',
        source_oid=OID,
        source_path=stream.LEGACY_PATH,
    )
    second = stream.body_from_raw_stream(
        b'{"var":"counter","op":"inc","value":3}',
        source_oid=OID,
        source_path="sub/a/patchwork-check-events.jsonl",
    )

    first_projection = stream.replay(first, {"counter": "counter"})
    second_projection = stream.replay(
        second,
        {"counter": "counter"},
        initial_state=first_projection.state,
    )

    assert second_projection.state == {"counter": 5}


def test_append_rejects_nonstandard_json_constant_before_publication():
    body = stream.body_from_raw_stream(b"", source_oid=OID, source_path=stream.LEGACY_PATH)
    calls = []

    with pytest.raises(stream.StreamFailure, match="json-record"):
        stream.append_and_publish(
            body,
            b'{"var":"label","op":"set","value":"bad","metadata":Infinity}',
            {"label": "text"},
            expected_oid="2" * 40,
            publish=lambda candidate, expected: calls.append((candidate, expected)),
        )

    assert calls == []


def test_append_preserves_prefix_adds_only_separator_and_terminal_lf():
    prior = b'{"var":"counter","op":"set","value":1}'
    body = stream.body_from_raw_stream(prior, source_oid=OID, source_path=stream.LEGACY_PATH)

    prepared = stream.prepare_append(
        body,
        b'{"var":"counter","op":"inc","value":2}\r\n',
        {"counter": "counter"},
    )

    assert prepared.raw_stream == prior + b'\n{"var":"counter","op":"inc","value":2}\n'
    assert stream.decode_raw_stream(prepared.body) == prepared.raw_stream
    assert stream.replay(prepared.body, {"counter": "counter"}).state["counter"] == 3


def test_append_rejects_blank_extra_record_depth_and_complete_size():
    body = stream.body_from_raw_stream(b"", source_oid=OID, source_path=stream.LEGACY_PATH)
    with pytest.raises(stream.StreamFailure, match="one-nonblank-record"):
        stream.prepare_append(body, b"\n{}\n", {})
    nested = b'{"var":"counter","op":"set","value":' + b"[" * 65 + b"0" + b"]" * 65 + b"}"
    with pytest.raises(stream.StreamFailure, match="record-depth-64"):
        stream.prepare_append(body, nested, {"counter": "counter"})
    oversized = {
        "raw_stream_base64": base64.b64encode(b"x" * (stream.MAX_RAW_STREAM_BYTES + 1)).decode("ascii"),
        "source_oid": OID,
        "source_path": stream.LEGACY_PATH,
    }
    with pytest.raises(stream.StreamFailure, match="decoded-stream-4-mib"):
        stream.decode_raw_stream(oversized)


def test_append_checks_complete_size_before_recursive_json_allocation(monkeypatch):
    body = stream.body_from_raw_stream(
        b" " * stream.MAX_RAW_STREAM_BYTES,
        source_oid=OID,
        source_path=stream.LEGACY_PATH,
    )
    decoder_called = False

    def unexpected_decode(*_args, **_kwargs):
        nonlocal decoder_called
        decoder_called = True
        raise AssertionError("json decoder must not run for an oversized complete stream")

    monkeypatch.setattr(stream.json, "loads", unexpected_decode)
    with pytest.raises(stream.StreamFailure, match="decoded-stream-4-mib"):
        stream.prepare_append(body, b'{"var":"counter","op":"inc"}', {"counter": "counter"})
    assert decoder_called is False


def test_append_rejects_impossible_size_before_physical_record_allocation(monkeypatch):
    monkeypatch.setattr(stream, "MAX_RAW_STREAM_BYTES", 16)
    body = stream.body_from_raw_stream(
        b"", source_oid=OID, source_path=stream.LEGACY_PATH
    )
    framing_called = False

    def unexpected_framing(_record):
        nonlocal framing_called
        framing_called = True
        raise AssertionError("an impossible candidate must not be split into records")

    monkeypatch.setattr(stream, "_physical_records", unexpected_framing)
    with pytest.raises(stream.StreamFailure, match="decoded-stream-4-mib"):
        stream.prepare_append(body, b"x" * 18, {})
    assert framing_called is False


def test_append_record_scan_fails_without_materializing_later_records(monkeypatch):
    body = stream.body_from_raw_stream(
        b"", source_oid=OID, source_path=stream.LEGACY_PATH
    )

    def records(_record):
        yield b""
        raise AssertionError("append must stop after the first decisive invalid record")

    monkeypatch.setattr(stream, "_physical_records", records)

    with pytest.raises(stream.StreamFailure, match="one-nonblank-record"):
        stream.prepare_append(body, b"\nignored", {})


def test_append_checks_record_depth_before_recursive_json_allocation(monkeypatch):
    body = stream.body_from_raw_stream(
        b"", source_oid=OID, source_path=stream.LEGACY_PATH
    )
    nested = (
        b'{"var":"counter","op":"set","value":0,"metadata":'
        + b"[" * 64
        + b"0"
        + b"]" * 64
        + b"}"
    )
    decoder_called = False

    def unexpected_decode(*_args, **_kwargs):
        nonlocal decoder_called
        decoder_called = True
        raise AssertionError("json decoder must not run for an over-depth record")

    monkeypatch.setattr(stream.json, "loads", unexpected_decode)
    with pytest.raises(stream.StreamFailure, match="record-depth-64"):
        stream.prepare_append(body, nested, {"counter": "counter"})
    assert decoder_called is False


def test_append_accepts_exact_complete_stream_limit_and_rejects_one_over():
    record = b'{"var":"counter","op":"inc"}'
    exact_prior = b" " * (stream.MAX_RAW_STREAM_BYTES - len(record) - 2)
    exact_body = stream.body_from_raw_stream(
        exact_prior, source_oid=OID, source_path=stream.LEGACY_PATH
    )

    prepared = stream.prepare_append(exact_body, record, {"counter": "counter"})

    assert len(prepared.raw_stream) == stream.MAX_RAW_STREAM_BYTES
    assert prepared.raw_stream[: len(exact_prior)] == exact_prior
    assert prepared.raw_stream[len(exact_prior) :] == b"\n" + record + b"\n"

    one_over_body = stream.body_from_raw_stream(
        exact_prior + b" ", source_oid=OID, source_path=stream.LEGACY_PATH
    )
    with pytest.raises(stream.StreamFailure, match="decoded-stream-4-mib"):
        stream.prepare_append(one_over_body, record, {"counter": "counter"})


@pytest.mark.parametrize(
    "source_oid, source_path, rule",
    [
        ("1" * 41, stream.LEGACY_PATH, "source-oid"),
        ("1" * 63, stream.LEGACY_PATH, "source-oid"),
        (OID, "docs/patchwork-check-events.jsonl", "source-path"),
        (OID, "sub/a/../patchwork-check-events.jsonl", "source-path"),
    ],
)
def test_stream_body_rejects_unpermitted_source_anchors(source_oid, source_path, rule):
    with pytest.raises(stream.StreamFailure, match=rule):
        stream.body_from_raw_stream(
            b"", source_oid=source_oid, source_path=source_path
        )


@pytest.mark.parametrize(
    "registry, rule",
    [({"Upper": "counter"}, "check-name"), ({"counter": "number"}, "check-type")],
)
def test_replay_and_append_require_manifest_typed_registry(registry, rule):
    body = stream.body_from_raw_stream(b"", source_oid=OID, source_path=stream.LEGACY_PATH)
    with pytest.raises(stream.StreamFailure, match=rule):
        stream.replay(body, registry)
    with pytest.raises(stream.StreamFailure, match=rule):
        stream.prepare_append(body, b'{"var":"counter","op":"inc"}', registry)


def test_append_publishes_only_after_validation_and_passes_expected_oid():
    body = stream.body_from_raw_stream(b"", source_oid=OID, source_path=stream.LEGACY_PATH)
    calls = []

    result = stream.append_and_publish(
        body,
        b'{"var":"label","op":"set","value":"ok"}',
        {"label": "text"},
        expected_oid="2" * 40,
        publish=lambda candidate, expected: calls.append((candidate, expected)) or "published",
    )

    assert result == "published"
    assert len(calls) == 1 and calls[0][1] == "2" * 40
    assert stream.decode_raw_stream(calls[0][0]).endswith(b"\n")


def test_rejected_append_leaves_stream_and_computed_projection_unchanged():
    raw = b'{"var":"counter","op":"set","value":7}'
    body = stream.body_from_raw_stream(raw, source_oid=OID, source_path=stream.LEGACY_PATH)
    before_body = dict(body)
    before_projection = stream.replay(body, {"counter": "counter"})
    calls = []

    with pytest.raises(stream.StreamFailure, match="counter-integer"):
        stream.append_and_publish(
            body,
            b'{"var":"counter","op":"set","value":"wrong"}',
            {"counter": "counter"},
            expected_oid="2" * 40,
            publish=lambda candidate, expected: calls.append((candidate, expected)),
        )

    assert calls == []
    assert body == before_body
    assert stream.decode_raw_stream(body) == raw
    assert stream.replay(body, {"counter": "counter"}) == before_projection


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_gate_replays_mixed_streams_in_logical_path_order_and_rejects_ambiguity(
    tmp_path, monkeypatch
):
    root = tmp_path / "network"
    (root / "sub" / "a").mkdir(parents=True)
    (root / "sub" / "b").mkdir(parents=True)
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    monkeypatch.setattr("ai_org.body_codec.BodyCodecClient", lambda: client)

    (root / stream.LEGACY_PATH).write_bytes(
        b'{"var":"counter","op":"set","value":1}\n'
    )
    (root / "sub" / "a" / stream.LEGACY_PATH).write_bytes(
        b'{"var":"counter","op":"set","value":2}\n'
    )
    canonical_body = stream.body_from_raw_stream(
        b'{"var":"counter","op":"set","value":4}',
        source_oid=OID,
        source_path="sub/b/patchwork-check-event-stream.cue",
    )
    canonical = client.prepare(
        stream.CONTEXT_ID, canonical_body, expected=stream.CONTRACT
    ).canonical.data
    (root / "sub" / "b" / stream.CANONICAL_PATH).write_bytes(canonical)

    projection = network._project_tree_patchwork_check_events(
        root,
        registry={"counter": "counter", "untouched": "text"},
        registry_present=True,
    )

    assert projection["errors"] == []
    assert projection["state"] == {"counter": 4}
    assert projection["last_events"] == {
        "counter": {"path": "sub/b/patchwork-check-event-stream.cue", "line": 1}
    }

    (root / "sub" / "b" / stream.LEGACY_PATH).write_bytes(b"{}\n")
    ambiguous = network._project_tree_patchwork_check_events(
        root,
        registry={"counter": "counter", "untouched": "text"},
        registry_present=True,
    )
    assert any(
        error["type"] == "patchwork_check_stream_invalid"
        and error["message"] == "ambiguous-stream-coordinate"
        for error in ambiguous["errors"]
    )
    assert ambiguous["state"] == {"counter": 2}
    assert ambiguous["last_events"] == {
        "counter": {"path": "sub/a/patchwork-check-events.jsonl", "line": 1}
    }


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_git_gate_replays_mixed_streams_in_logical_path_order(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    monkeypatch.setattr("ai_org.body_codec.BodyCodecClient", lambda: client)
    canonical_body = stream.body_from_raw_stream(
        b'{"var":"counter","op":"set","value":4}',
        source_oid=OID,
        source_path="sub/b/patchwork-check-event-stream.cue",
    )
    canonical = client.prepare(
        stream.CONTEXT_ID, canonical_body, expected=stream.CONTRACT
    ).canonical.data
    git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            stream.LEGACY_PATH: '{"var":"counter","op":"set","value":1}\n',
            "sub/a/" + stream.LEGACY_PATH: '{"var":"counter","op":"set","value":2}\n',
            "sub/b/" + stream.CANONICAL_PATH: canonical.decode("utf-8"),
        },
        subject="fixture: mixed exact-byte stream order",
    )

    projection = network._project_tree_patchwork_check_events_from_git(
        repo,
        "main",
        registry={"counter": "counter"},
        registry_present=True,
    )

    assert projection["errors"] == []
    assert projection["state"] == {"counter": 4}
    assert projection["last_events"] == {
        "counter": {"path": "sub/b/patchwork-check-event-stream.cue", "line": 1}
    }


def test_git_gate_replay_reads_one_frozen_commit_when_ref_advances(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    initial = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {stream.LEGACY_PATH: '{"var":"counter","op":"set","value":1}\n'},
        subject="fixture: frozen stream source",
    )["commit"]
    winner = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/unused",
        {stream.LEGACY_PATH: '{"var":"counter","op":"set","value":9}\n'},
        subject="fixture: concurrent stream source",
        parent=initial,
        update_ref=False,
    )["commit"]
    real_tree_files = git_wrapper.tree_files

    def advance_after_inventory(repo_path, ref, pathspec=""):
        assert ref == initial
        paths = real_tree_files(repo_path, ref, pathspec)
        subprocess.run(
            ["git", "-C", str(repo), "update-ref", "refs/heads/main", winner, initial],
            check=True,
            capture_output=True,
        )
        return paths

    monkeypatch.setattr(git_wrapper, "tree_files", advance_after_inventory)
    projection = network._project_tree_patchwork_check_events_from_git(
        repo,
        "main",
        registry={"counter": "counter"},
        registry_present=True,
    )

    assert git_wrapper.head_sha(repo, "main") == winner
    assert projection["errors"] == []
    assert projection["state"] == {"counter": 1}
    assert projection["last_events"] == {
        "counter": {"path": stream.LEGACY_PATH, "line": 1}
    }


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_computed_git_facts_use_one_frozen_commit_when_ref_advances(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    series_branch = "ai-org/patch-series/frozen-facts"
    manifest = {
        "schema": "patch_series-network-node-v1",
        "identity_stage": "nested",
        "node_path": ".",
        "branch": series_branch,
        "relation_from_parent": "root",
        "lifecycle_status": "ready_for_patch_authoring",
        "ownership": {"request_owner": "root", "interior_owner": "root"},
        "write_scope": {"allowed_subtree": "."},
        "scope_item_ids": [],
        "children": [],
        "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
    }
    initial = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/" + series_branch,
        {
            "patch-series-manifest.json": manifest,
            stream.LEGACY_PATH: '{"var":"counter","op":"set","value":1}\n',
        },
        subject="fixture: frozen computed facts",
    )["commit"]
    winner = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/unused",
        {
            "patch-series-manifest.json": manifest,
            stream.LEGACY_PATH: '{"var":"counter","op":"set","value":9}\n',
        },
        subject="fixture: concurrent computed facts",
        parent=initial,
        update_ref=False,
    )["commit"]
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    monkeypatch.setattr("ai_org.network_bodies.BodyCodecClient", lambda: client)
    real_tree_files = git_wrapper.tree_files
    advanced = False

    def advance_after_first_inventory(repo_path, ref, pathspec=""):
        nonlocal advanced
        paths = real_tree_files(repo_path, ref, pathspec)
        if not advanced:
            assert ref == initial
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "update-ref",
                    "refs/heads/" + series_branch,
                    winner,
                    initial,
                ],
                check=True,
                capture_output=True,
            )
            advanced = True
        return paths

    monkeypatch.setattr(git_wrapper, "tree_files", advance_after_first_inventory)
    facts = network.computed_patchwork_check_facts(repo, series_branch, "root")

    assert git_wrapper.head_sha(repo, series_branch) == winner
    assert facts["checks"]["counter"] == {
        "declared_type": "counter",
        "current_value": 1,
        "last_event": {"path": stream.LEGACY_PATH, "line": 1},
    }


def test_gate_rejects_out_of_scope_legacy_source_paths_on_filesystem_and_git(tmp_path):
    root = tmp_path / "network"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / stream.LEGACY_PATH).write_bytes(
        b'{"var":"counter","op":"set","value":9}\n'
    )

    filesystem = network._project_tree_patchwork_check_events(
        root,
        registry={"counter": "counter"},
        registry_present=True,
    )

    assert filesystem["state"] == {}
    assert filesystem["errors"] == [{
        "type": "patchwork_check_stream_invalid",
        "node": "docs",
        "path": "docs/patchwork-check-events.jsonl",
        "message": "source-path-anchor",
    }]

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {"docs/" + stream.LEGACY_PATH: '{"var":"counter","op":"set","value":9}\n'},
        subject="fixture: invalid legacy stream anchor",
    )
    git_projection = network._project_tree_patchwork_check_events_from_git(
        repo,
        "main",
        registry={"counter": "counter"},
        registry_present=True,
    )

    assert git_projection["state"] == {}
    assert git_projection["errors"] == filesystem["errors"]


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_gate_legacy_and_canonical_carriers_share_exact_record_semantics(
    tmp_path, monkeypatch
):
    root = tmp_path / "network"
    root.mkdir()
    raw = (
        b"\x0b\n"
        b'{"var":"counter","var":"counter","op":"set","value":99}\r\n'
        b'{"var":"counter","op":"set","value":4}'
    )
    (root / stream.LEGACY_PATH).write_bytes(raw)
    legacy = network._project_tree_patchwork_check_events(
        root,
        registry={"counter": "counter"},
        registry_present=True,
    )

    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    monkeypatch.setattr("ai_org.body_codec.BodyCodecClient", lambda: client)
    body = stream.body_from_raw_stream(
        raw, source_oid=OID, source_path=stream.LEGACY_PATH
    )
    canonical = client.prepare(
        stream.CONTEXT_ID, body, expected=stream.CONTRACT
    ).canonical.data
    (root / stream.LEGACY_PATH).unlink()
    (root / stream.CANONICAL_PATH).write_bytes(canonical)
    projected = network._project_tree_patchwork_check_events(
        root,
        registry={"counter": "counter"},
        registry_present=True,
    )

    assert projected == legacy
    assert projected["state"] == {"counter": 4}
    assert [(error["line"], error["rule"]) for error in projected["errors"]] == [
        (1, "json-record"),
        (2, "json-record"),
    ]


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_gate_rejects_historical_json_body_at_canonical_coordinate(
    tmp_path, monkeypatch
):
    root = tmp_path / "network"
    root.mkdir()
    body = stream.body_from_raw_stream(
        b'{"var":"counter","op":"set","value":9}',
        source_oid=OID,
        source_path=stream.LEGACY_PATH,
    )
    (root / stream.CANONICAL_PATH).write_text(json.dumps(body), encoding="utf-8")
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    monkeypatch.setattr("ai_org.body_codec.BodyCodecClient", lambda: client)

    projection = network._project_tree_patchwork_check_events(
        root,
        registry={"counter": "counter"},
        registry_present=True,
    )

    assert projection["state"] == {}
    assert len(projection["errors"]) == 1
    assert projection["errors"][0]["type"] == "patchwork_check_stream_invalid"
    assert "canonical-body-coordinate" in projection["errors"][0]["message"]


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_git_append_is_single_cas_and_removes_legacy_alias(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    legacy = b'{bad}\r\n{"var":"counter","op":"set","value":1}'
    git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            "patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
            },
            stream.LEGACY_PATH: legacy.decode("ascii"),
        },
        subject="fixture: legacy check stream",
    )
    expected = git_wrapper.head_sha(repo, "main")
    assert expected is not None
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    record = b'{"var":"counter","op":"inc","value":2}'

    rejected = stream.append_to_git(
        repo, "main", ".", record, {"counter": "counter"},
        expected_oid=expected, client=client, inject_failure=True,
    )
    assert rejected.status == "rejected"
    assert git_wrapper.head_sha(repo, "main") == expected
    assert git_wrapper.show_file_bytes(repo, expected, stream.LEGACY_PATH) == legacy

    published = stream.append_to_git(
        repo, "main", ".", record, {"counter": "counter"},
        expected_oid=expected, client=client,
    )
    assert published.ok and published.status == "updated"
    assert git_wrapper.head_sha(repo, "main") == published.commit_oid
    assert git_wrapper.show_file_bytes(repo, published.commit_oid, stream.LEGACY_PATH) is None
    canonical = git_wrapper.show_file_bytes(repo, published.commit_oid, stream.CANONICAL_PATH)
    assert canonical is not None
    body = client.parse(stream.CONTEXT_ID, canonical, expected=stream.CONTRACT)
    assert stream.decode_raw_stream(body) == legacy + b"\n" + record + b"\n"


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_git_append_uses_canonical_manifest_registry_and_appends_canonical_stream_twice(
    tmp_path,
):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    manifest = {
        "schema": "patch_series-network-node-v1",
        "identity_stage": "nested",
        "node_path": ".",
        "branch": "main",
        "relation_from_parent": "root",
        "lifecycle_status": "ready_for_patch_authoring",
        "ownership": {"request_owner": "root", "interior_owner": "root"},
        "write_scope": {"allowed_subtree": "."},
        "scope_item_ids": [],
        "children": [],
        "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
    }
    manifest_contract = {
        "apiVersion": "ai-org-cue-body-v1",
        "kind": "NetworkNodeManifest",
        "variant": "network-root",
    }
    manifest_bytes = client.prepare(
        "root-network-node-manifest-v1", manifest, expected=manifest_contract
    ).canonical.data
    initial = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            "patch-series-manifest.cue": manifest_bytes.decode("utf-8"),
            stream.LEGACY_PATH: '{"var":"counter","op":"inc","value":1}',
        },
        subject="fixture: canonical typed registry",
    )["commit"]

    first = stream.append_to_git(
        repo,
        "main",
        ".",
        b'{"var":"counter","op":"inc","value":2}',
        {"counter": "counter"},
        expected_oid=initial,
        client=client,
    )
    assert first.ok
    second = stream.append_to_git(
        repo,
        "main",
        ".",
        b'{"var":"counter","op":"inc","value":3}',
        {"counter": "counter"},
        expected_oid=first.commit_oid,
        client=client,
    )

    assert second.ok
    canonical = git_wrapper.show_file_bytes(repo, second.commit_oid, stream.CANONICAL_PATH)
    assert canonical is not None
    body = client.parse(stream.CONTEXT_ID, canonical, expected=stream.CONTRACT)
    projection = stream.replay(body, {"counter": "counter"})
    assert projection.state == {"counter": 6}
    assert body["source_oid"] == initial
    assert body["source_path"] == stream.LEGACY_PATH
    assert set(body) == {"raw_stream_base64", "source_oid", "source_path"}
    assert not any(
        name.endswith(".schema.json") or name.endswith("events.json")
        for name in git_wrapper.tree_files(repo, second.commit_oid)
    )


def test_git_append_rejects_ambiguous_canonical_and_legacy_coordinates(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            stream.CANONICAL_PATH: "not dispatched",
            stream.LEGACY_PATH: '{"var":"counter","op":"inc"}\n',
        },
        subject="fixture: ambiguous check stream",
    )
    expected = git_wrapper.head_sha(repo, "main")

    with pytest.raises(git_wrapper.GitBodyFailure, match="ambiguous-stream-coordinate"):
        stream.append_to_git(
            repo,
            "main",
            ".",
            b'{"var":"counter","op":"inc"}',
            {"counter": "counter"},
            expected_oid=expected,
            client=object(),
        )
    assert git_wrapper.head_sha(repo, "main") == expected


def test_git_append_binds_registry_to_frozen_manifest(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            "patch-series-manifest.json": {
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
            },
            stream.LEGACY_PATH: "",
        },
        subject="fixture: typed check registry",
    )
    expected = git_wrapper.head_sha(repo, "main")

    with pytest.raises(git_wrapper.GitBodyFailure, match="patchwork-check-registry-mismatch"):
        stream.append_to_git(
            repo,
            "main",
            ".",
            b'{"var":"counter","op":"inc"}',
            {"counter": "text"},
            expected_oid=expected,
            client=object(),
        )
    assert git_wrapper.head_sha(repo, "main") == expected


def test_git_append_returns_typed_rejection_for_stale_cas(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    first = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {"patch-series-manifest.json": {"declared_patchwork_checks": []}},
        subject="fixture: first head",
    )["commit"]
    current = git_wrapper.commit_files(repo, "main", {"marker": "new head"}, subject="fixture: advance head")["commit"]

    result = stream.append_to_git(
        repo,
        "main",
        ".",
        b'{"var":"counter","op":"inc"}',
        {"counter": "counter"},
        expected_oid=first,
        client=object(),
    )

    assert result.status == "rejected" and not result.ok
    assert result.failure is not None and result.failure.code == "GIT_CAS"
    assert git_wrapper.head_sha(repo, "main") == current


def test_git_append_returns_typed_rejection_for_race_after_preparation(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    expected = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            "patch-series-manifest.json": {
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}]
            },
            stream.LEGACY_PATH: '{"var":"counter","op":"set","value":7}\n',
        },
        subject="fixture: prepared race",
    )["commit"]
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    client = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)
    real_prepare = stream._prepare_git_publication
    winner = ""

    def lose_race(*args, **kwargs):
        nonlocal winner
        prepared = real_prepare(*args, **kwargs)
        winner = git_wrapper.create_ref_with_files(
            repo,
            "refs/heads/main",
            {
                "patch-series-manifest.json": {
                    "declared_patchwork_checks": [
                        {"name": "counter", "type": "counter"}
                    ]
                },
                stream.LEGACY_PATH: '{"var":"counter","op":"set","value":7}\n',
                "winner": "concurrent publication\n",
            },
            subject="fixture: win append race",
            parent=expected,
        )["commit"]
        return prepared

    monkeypatch.setattr(stream, "_prepare_git_publication", lose_race)
    result = stream.append_to_git(
        repo,
        "main",
        ".",
        b'{"var":"counter","op":"inc"}',
        {"counter": "counter"},
        expected_oid=expected,
        client=client,
    )

    assert result.status == "rejected"
    assert result.failure is not None and result.failure.code == "GIT_CAS"
    assert git_wrapper.head_sha(repo, "main") == winner
    assert git_wrapper.show_file_bytes(repo, winner, stream.LEGACY_PATH) == (
        b'{"var":"counter","op":"set","value":7}\n'
    )
    assert git_wrapper.show_file_bytes(repo, winner, stream.CANONICAL_PATH) is None


@pytest.mark.skipif(shutil.which("go") is None, reason="pinned Go toolchain is unavailable")
def test_git_append_rejects_codec_output_for_a_different_enclosing_body(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    expected = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {
            "patch-series-manifest.json": {
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}]
            },
            stream.LEGACY_PATH: '{"var":"counter","op":"set","value":7}\n',
        },
        subject="fixture: codec round-trip guard",
    )["commit"]
    environment = dict(os.environ)
    environment.update({
        "AI_ORG_CUECODEC": "",
        "AI_ORG_CUECODEC_CACHE": str(tmp_path / "codec-cache"),
        "GOCACHE": str(tmp_path / "go-cache"),
    })
    real = BodyCodecClient(source_root=ROOT / "cuecodec", env=environment)

    class InconsistentCodec:
        def __init__(self):
            self.prepare_calls = 0

        def prepare(self, context_id, body, *, expected):
            self.prepare_calls += 1
            if self.prepare_calls == 1:
                body = stream.body_from_raw_stream(
                    b"", source_oid=body["source_oid"], source_path=body["source_path"]
                )
            return real.prepare(context_id, body, expected=expected)

        def parse(self, context_id, raw, *, expected):
            return real.parse(context_id, raw, expected=expected)

    before = stream.replay(
        stream.import_legacy_jsonl(
            git_wrapper.show_file_bytes(repo, expected, stream.LEGACY_PATH),
            source_oid=expected,
        ),
        {"counter": "counter"},
    )

    with pytest.raises(git_wrapper.GitBodyFailure, match="prepared-body-round-trip"):
        stream.append_to_git(
            repo,
            "main",
            ".",
            b'{"var":"counter","op":"inc","value":2}',
            {"counter": "counter"},
            expected_oid=expected,
            client=InconsistentCodec(),
        )

    assert git_wrapper.head_sha(repo, "main") == expected
    raw_after = git_wrapper.show_file_bytes(repo, expected, stream.LEGACY_PATH)
    assert raw_after == b'{"var":"counter","op":"set","value":7}\n'
    after = stream.replay(
        stream.import_legacy_jsonl(raw_after, source_oid=expected),
        {"counter": "counter"},
    )
    assert after == before


@pytest.mark.parametrize(
    "node_path",
    ["docs", "sub/a/nested", "sub/../outside", "/sub/a", "sub/a/", "sub/a-b", None, []],
)
def test_git_append_rejects_node_paths_outside_existing_write_scope(tmp_path, node_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    expected = git_wrapper.create_ref_with_files(
        repo,
        "refs/heads/main",
        {"patch-series-manifest.json": {"declared_patchwork_checks": []}},
        subject="fixture: bounded stream scope",
    )["commit"]

    with pytest.raises(stream.StreamFailure, match="network-node-path"):
        stream.append_to_git(
            repo,
            "main",
            node_path,
            b'{}',
            {},
            expected_oid=expected,
            client=object(),
        )

    assert git_wrapper.head_sha(repo, "main") == expected
