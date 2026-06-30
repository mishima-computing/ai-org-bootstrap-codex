from __future__ import annotations

import ast
import binascii
import json
from pathlib import Path
import struct
import subprocess
import zlib

import pytest

from ai_org import graphicist


def test_constructive_svg_extracts_svg_from_codex_output(monkeypatch, tmp_path):
    captured = {}
    known_svg = '<svg viewBox="0 0 512 512"><rect width="512" height="512"/></svg>'

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(f"ignore before\n{known_svg}\nignore after", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(graphicist.subprocess, "run", fake_run)

    out_path = tmp_path / "asset.svg"
    result = graphicist.constructive_svg(
        {"subject": "symmetric robot"},
        out_path,
        model="test-model",
        view="three-quarter",
    )

    assert result == out_path
    assert out_path.read_text(encoding="utf-8") == known_svg
    assert captured["cmd"][:7] == [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-C",
        str(graphicist.REPO_ROOT),
    ]
    assert "-m" in captured["cmd"]
    assert "test-model" in captured["cmd"]
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    prompt = captured["cmd"][-1]
    assert "parametrically and procedurally" in prompt
    assert "High-fidelity SVG asset techniques" in prompt
    assert "COMPOSE in this requested view: three-quarter" in prompt
    assert "not a flat top-down diagram" in prompt


def test_constructive_svg_prompt_includes_view_and_face_canon():
    prompt = graphicist._constructive_svg_prompt(
        {"subject": "spider creature head with chelicerae"},
        view="three-quarter",
    )

    assert "COMPOSE in this requested view: three-quarter" in prompt
    assert "3/4 projection with mild foreshortening" in prompt
    assert "Faces (dedicated canon)" in prompt
    assert "eye-line at the vertical MIDLINE" in prompt
    assert "chelicerae/mandibles" in prompt
    assert "FOCAL detail zone" in prompt


def test_constructive_svg_prompt_includes_side_view_and_rigging_contract():
    prompt = graphicist._constructive_svg_prompt(
        {"subject": "realistic walking spider"},
        view="side",
        style="painterly",
    )

    assert "COMPOSE in this requested view: side" in prompt
    assert "orthographic side silhouette" in prompt
    assert "PROFILE" in prompt
    assert "legs visible from the side so the gait reads" in prompt
    assert "proper anatomical segmentation" in prompt
    assert "EACH of the 8 legs as separate segmented parts" in prompt
    assert "coxa/femur/patella/tibia/tarsus" in prompt
    assert 'own <g id="...">' in prompt
    assert '"leg-L1-upper"' in prompt
    assert "rig manifest XML comment" in prompt
    assert "pivot [x,y] in viewBox coordinates" in prompt


def test_constructive_svg_accepts_cute_style(monkeypatch, tmp_path):
    captured = {}
    known_svg = '<svg viewBox="0 0 512 512"><circle cx="256" cy="256" r="128"/></svg>'

    def fake_run(cmd, **kwargs):
        captured["prompt"] = cmd[-1]
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(known_svg, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(graphicist.subprocess, "run", fake_run)

    out_path = tmp_path / "cute.svg"
    result = graphicist.constructive_svg(
        {"subject": "cute friendly robot head"},
        out_path,
        style="cute",
    )

    assert result == out_path
    assert out_path.read_text(encoding="utf-8") == known_svg
    assert "CUTE / APPEAL CANON (Flash-era clean vector)" in captured["prompt"]
    assert "closed vector shapes with flat fills" in captured["prompt"]
    assert "SUBJECT FIDELITY" in captured["prompt"]
    assert "defining / identifying features" in captured["prompt"]
    assert "EXACTLY 8 legs" in captured["prompt"]
    assert "Procedural texture (feTurbulence)" not in captured["prompt"]
    assert "Cut-in shadow SHAPE" not in captured["prompt"]


def test_constructive_svg_prompt_switches_between_cute_and_painterly_style():
    cute_prompt = graphicist._constructive_svg_prompt(
        {"subject": "cute friendly robot head"},
        style="cute",
    )
    painterly_prompt = graphicist._constructive_svg_prompt(
        {"subject": "armored robot head"},
        style="painterly",
    )

    assert "CUTE / APPEAL CANON (Flash-era clean vector)" in cute_prompt
    assert "Procedural texture (feTurbulence)" not in cute_prompt
    assert "Cut-in shadow SHAPE" not in cute_prompt
    assert "CUTE / APPEAL CANON (Flash-era clean vector)" not in painterly_prompt
    assert "Procedural texture (feTurbulence)" in painterly_prompt
    assert "Cut-in shadow SHAPE" in painterly_prompt


def test_constructive_svg_fails_closed_without_svg(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text("no svg here", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(graphicist.subprocess, "run", fake_run)

    out_path = tmp_path / "missing.svg"
    assert graphicist.constructive_svg("asset", out_path) is None
    assert not out_path.exists()


def test_autonomous_create_researches_brief_drives_generation_and_returns_result(monkeypatch, tmp_path):
    brief = {
        "subject": "grounded clockwork fox",
        "defining_features": ["fox ears", "brass gears", "bushy tail"],
        "realism_cute": 0.25,
        "style": "painterly",
        "view": "side",
        "palette_hint": "copper, cream, dark teal",
        "canon_notes": "Readable fox silhouette with mechanical joints.",
        "animation": {"needed": False, "states": []},
    }
    captured = {"codex_cmds": []}
    known_svg = '<svg viewBox="0 0 512 512"><rect width="256" height="256" fill="red"/></svg>'

    def fake_run(cmd, **kwargs):
        captured["codex_cmds"].append(cmd)
        out_file = Path(cmd[cmd.index("-o") + 1])
        if "-i" in cmd:
            out_file.write_text(json.dumps({"matches": True, "svg": None}), encoding="utf-8")
        else:
            out_file.write_text(f"research notes\n{json.dumps(brief)}\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_constructive(spec, out_path, model=None, view="three-quarter", style="painterly"):
        captured["spec"] = spec
        captured["view"] = view
        captured["style"] = style
        out_path.write_text(known_svg, encoding="utf-8")
        return out_path

    def fake_render(_svg, png, size=512):
        _write_rgba_png(
            png,
            2,
            2,
            [
                (255, 0, 0, 255),
                (0, 255, 0, 255),
                (0, 0, 255, 255),
                (255, 255, 255, 255),
            ],
        )
        return True

    monkeypatch.setattr(graphicist.subprocess, "run", fake_run)
    monkeypatch.setattr(graphicist, "constructive_svg", fake_constructive)
    monkeypatch.setattr(graphicist, "render_svg", fake_render)

    result = graphicist.autonomous_create("make me that fox automaton thing", tmp_path)

    assert result["brief"] == brief
    assert result["svg"] == tmp_path / "asset.svg"
    assert result["png"] == tmp_path / "asset.png"
    assert result["qa"]["ok"] is True
    assert result["preview"] is None
    assert captured["view"] == "side"
    assert captured["style"] == "painterly"
    assert captured["spec"]["raw_request"] == "make me that fox automaton thing"
    assert captured["spec"]["defining_features"] == ["fox ears", "brass gears", "bushy tail"]
    assert captured["spec"]["canon_notes"] == "Readable fox silhouette with mechanical joints."
    assert "--enable" in captured["codex_cmds"][0]
    assert "web_search" in captured["codex_cmds"][0]
    assert "-i" in captured["codex_cmds"][1]


def test_autonomous_create_fails_closed_when_brief_json_missing(monkeypatch, tmp_path):
    calls = {"constructive": 0}

    def fake_run(cmd, **kwargs):
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text("no structured brief", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_constructive(*args, **kwargs):
        calls["constructive"] += 1
        raise AssertionError("generation should not run without a parsed brief")

    monkeypatch.setattr(graphicist.subprocess, "run", fake_run)
    monkeypatch.setattr(graphicist, "constructive_svg", fake_constructive)

    assert graphicist.autonomous_create("an unknown asset", tmp_path) is None
    assert calls["constructive"] == 0


def test_animate_generates_valid_rig_runtime_and_preview(monkeypatch, tmp_path):
    captured = {}
    svg = tmp_path / "spider.svg"
    svg.write_text(
        '<svg viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg">'
        '<!-- rig-manifest: part cephalothorax parent null pivot [240,250]; '
        'part abdomen parent cephalothorax pivot [310,250]; '
        'part leg-L1-upper parent cephalothorax pivot [218,276] -->'
        '<g id="cephalothorax"><ellipse cx="240" cy="250" rx="58" ry="44"/></g>'
        '<g id="abdomen"><ellipse cx="320" cy="254" rx="72" ry="50"/></g>'
        '<g id="leg-L1-upper"><path d="M218 276 L170 320"/></g>'
        "</svg>",
        encoding="utf-8",
    )
    known_rig = {
        "parts": {
            "cephalothorax": {"selector": "#cephalothorax", "pivot": [240, 250], "parent": None},
            "abdomen": {"selector": "#abdomen", "pivot": [310, 250], "parent": "cephalothorax"},
            "leg-L1-upper": {"selector": "#leg-L1-upper", "pivot": [218, 276], "parent": "cephalothorax"},
        },
        "states": {
            "idle": {
                "duration": 1.6,
                "parts": {
                    "cephalothorax": [{"t": 0, "y": 0}, {"t": 0.8, "y": -2}],
                    "abdomen": [{"t": 0, "rot": -1}, {"t": 0.8, "rot": 1}],
                    "leg-L1-upper": [{"t": 0, "rot": -2}, {"t": 0.8, "rot": 2}],
                },
            },
            "walk": {
                "duration": 1.0,
                "parts": {
                    "cephalothorax": [{"t": 0, "y": 0}, {"t": 0.5, "y": -4}],
                    "abdomen": [{"t": 0, "rot": 2}, {"t": 0.5, "rot": -2}],
                    "leg-L1-upper": [{"t": 0, "rot": -18}, {"t": 0.5, "rot": 18}],
                },
            },
        },
    }

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps(known_rig), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(graphicist.subprocess, "run", fake_run)

    preview = graphicist.animate(svg, {"subject": "spider"}, tmp_path / "anim", state="walk")

    assert preview == tmp_path / "anim" / "preview.html"
    rig = json.loads((tmp_path / "anim" / "rig.json").read_text(encoding="utf-8"))
    assert graphicist._valid_rig(rig)
    assert set(rig["parts"]) == {"cephalothorax", "abdomen", "leg-L1-upper"}
    for part in rig["parts"].values():
        assert isinstance(part["selector"], str) and part["selector"].startswith("#")
        assert isinstance(part["pivot"], list) and len(part["pivot"]) == 2
        assert part["parent"] is None or part["parent"] in rig["parts"]
    for state in rig["states"].values():
        assert state["duration"] > 0
        for keyframes in state["parts"].values():
            assert keyframes
            assert all("t" in frame for frame in keyframes)

    runtime = (tmp_path / "anim" / "animate-runtime.js").read_text(encoding="utf-8")
    preview_html = preview.read_text(encoding="utf-8")
    assert "DOMMatrix" in runtime
    assert "function partMatrix" in runtime
    assert "function renderRig" in runtime
    assert "requestAnimationFrame" in runtime
    assert "./rig.json" in preview_html
    assert "./animate-runtime.js" in preview_html
    assert "<svg" in preview_html
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert "Animate only by JSON keyframes" in captured["cmd"][-1]


def test_animate_fails_closed_without_valid_rig(monkeypatch, tmp_path):
    svg = tmp_path / "asset.svg"
    svg.write_text('<svg viewBox="0 0 512 512"><g id="body"/></svg>', encoding="utf-8")

    def fake_run(cmd, **kwargs):
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text('{"parts": {}, "states": {}}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(graphicist.subprocess, "run", fake_run)

    out_dir = tmp_path / "anim"
    assert graphicist.animate(svg, "asset", out_dir) is None
    assert not (out_dir / "rig.json").exists()
    assert not (out_dir / "preview.html").exists()


def test_animate_preview_renders_with_chrome_or_skip(monkeypatch, tmp_path):
    chrome = Path(graphicist.os.environ.get("ASSET_CHROME") or graphicist.DEFAULT_CHROME)
    if not chrome.exists():
        pytest.skip("Chrome path absent")

    svg = tmp_path / "asset.svg"
    svg.write_text(
        '<svg viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg">'
        '<g id="body"><rect x="156" y="156" width="200" height="200" fill="red"/></g>'
        "</svg>",
        encoding="utf-8",
    )
    rig = {
        "parts": {"body": {"selector": "#body", "pivot": [256, 256], "parent": None}},
        "states": {
            "idle": {"duration": 1, "parts": {"body": [{"t": 0, "rot": 0}]}},
            "walk": {"duration": 1, "parts": {"body": [{"t": 0, "rot": 0}, {"t": 0.5, "rot": 4}]}},
        },
    }

    def fake_run(cmd, **kwargs):
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps(rig), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with monkeypatch.context() as scoped:
        scoped.setattr(graphicist.subprocess, "run", fake_run)
        preview = graphicist.animate(svg, "red square body", tmp_path / "anim")

    png = tmp_path / "preview.png"
    if not graphicist.render_svg(preview, png, size=128):
        pytest.skip("Chrome headless screenshot unavailable")
    assert png.exists()


def test_fetch_web_image_downloads_openverse_result_and_sidecar(monkeypatch, tmp_path):
    image_bytes = b"\x89PNG\r\n\x1a\nfake image bytes"
    openverse_payload = {
        "results": [
            {
                "url": "https://images.example.test/asset.png",
                "foreign_landing_url": "https://source.example.test/page",
                "license": "cc-by",
                "creator": "Asset Maker",
                "attribution": "Asset Maker / Example",
            }
        ]
    }

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body

    def fake_urlopen(request, timeout=20):
        url = request.full_url
        if "api.openverse.org" in url:
            return FakeResponse(json.dumps(openverse_payload).encode("utf-8"))
        if url == "https://images.example.test/asset.png":
            return FakeResponse(image_bytes)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(graphicist.urllib.request, "urlopen", fake_urlopen)

    results = graphicist.fetch_web_image("test query", tmp_path, n=1)

    assert len(results) == 1
    downloaded = results[0]["path"]
    assert downloaded.read_bytes() == image_bytes
    assert results[0]["source_url"] == "https://source.example.test/page"
    assert results[0]["license"] == "cc-by"
    assert results[0]["attribution"] == "Asset Maker / Example"
    sidecar = json.loads(downloaded.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["source_url"] == "https://source.example.test/page"
    assert sidecar["creator"] == "Asset Maker"


def test_render_svg_with_chrome_or_skip(tmp_path):
    chrome = Path(graphicist.os.environ.get("ASSET_CHROME") or graphicist.DEFAULT_CHROME)
    if not chrome.exists():
        pytest.skip("Chrome path absent")

    svg = tmp_path / "asset.svg"
    png = tmp_path / "asset.png"
    svg.write_text(
        '<svg viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="512" height="512" fill="red"/></svg>',
        encoding="utf-8",
    )

    if not graphicist.render_svg(svg, png, size=128):
        pytest.skip("Chrome headless screenshot unavailable")
    assert png.exists()


def test_qa_accepts_real_tiny_png_and_rejects_blank(tmp_path):
    varied = tmp_path / "varied.png"
    blank = tmp_path / "blank.png"

    _write_rgba_png(
        varied,
        2,
        2,
        [
            (255, 0, 0, 255),
            (0, 255, 0, 255),
            (0, 0, 255, 255),
            (255, 255, 255, 255),
        ],
    )
    _write_rgba_png(blank, 1, 1, [(0, 0, 0, 0)])

    varied_result = graphicist.qa(varied)
    blank_result = graphicist.qa(blank)

    assert varied_result["ok"] is True
    assert varied_result["checks"]["valid_png"] is True
    assert blank_result["ok"] is False
    assert "PNG appears blank or single-color" in blank_result["reasons"]


def test_image_model_is_unprovisioned_stub(tmp_path):
    with pytest.raises(NotImplementedError, match="raster image model not provisioned"):
        graphicist.image_model({"subject": "painterly"}, tmp_path / "asset.png")


def test_graphicist_imports_no_pipeline_or_archive_modules():
    tree = ast.parse(Path(graphicist.__file__).read_text(encoding="utf-8"))
    forbidden = {"ai_org.rfc", "ai_org.patch", "ai_org.merge", "archive"}
    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not {name for name in imports if name in forbidden or any(name.startswith(f"{item}.") for item in forbidden)}


def _write_rgba_png(path, width, height, pixels):
    raw_rows = []
    for y in range(height):
        row = bytearray([0])
        for pixel in pixels[y * width : (y + 1) * width]:
            row.extend(pixel)
        raw_rows.append(bytes(row))

    png = bytearray(graphicist.PNG_SIGNATURE)
    png.extend(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
    png.extend(_png_chunk(b"IDAT", zlib.compress(b"".join(raw_rows))))
    png.extend(_png_chunk(b"IEND", b""))
    path.write_bytes(bytes(png))


def _png_chunk(chunk_type, data):
    body = chunk_type + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
