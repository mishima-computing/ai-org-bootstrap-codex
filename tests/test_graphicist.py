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
