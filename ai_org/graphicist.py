# codex is the pipeline ENGINEER, not a blind illustrator. Reliable form comes from STRUCTURE, not freehand coordinates. Sources: (a) constructive/parametric SVG for flat/structural assets (form guaranteed by construction), (b) web image fetch (CC-licensed found images), (c) a raster image-model slot for painterly art (provision later). Naive full-regenerate visual feedback loops are DEPRECATED: proven to fail (round-1 produced no SVG); use structured QA + targeted edits instead.
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]
SVG_TECHNIQUES = REPO_ROOT / "docs" / "svg-asset-techniques.md"
DEFAULT_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def constructive_svg(spec, out_path, model=None):
    """Generate a flat, structural SVG asset through a Codex parametric construction prompt."""
    out_path = Path(out_path)
    prompt = _constructive_svg_prompt(spec)

    with tempfile.TemporaryDirectory(prefix="graphicist-codex-") as tmp:
        codex_out = Path(tmp) / "asset.txt"
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-C",
            str(REPO_ROOT),
            "-o",
            str(codex_out),
        ]
        if model:
            cmd.extend(["-m", str(model)])
        cmd.append(prompt)

        try:
            result = subprocess.run(
                cmd,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            return None

        output = ""
        if codex_out.exists():
            output = codex_out.read_text(encoding="utf-8", errors="replace")
        if not output:
            output = result.stdout or ""

    svg = _extract_svg(output)
    if svg is None:
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    return out_path


def fetch_web_image(query, out_dir, n=5):
    """Fetch up to n openly licensed images using Openverse first, then Wikimedia Commons."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = _openverse_results(query, n)
    if not results:
        results = _wikimedia_results(query, n)

    downloaded = []
    for index, item in enumerate(results[:n], start=1):
        image_url = item.get("image_url")
        if not image_url:
            continue

        try:
            image_bytes = _read_url(image_url)
        except (OSError, urllib.error.URLError, TimeoutError):
            continue

        extension = _image_extension(image_url, item.get("content_type"))
        image_path = out_dir / f"image_{index}{extension}"
        image_path.write_bytes(image_bytes)

        metadata = {
            "source_url": item.get("source_url") or image_url,
            "license": item.get("license") or "",
            "creator": item.get("creator") or "",
            "attribution": item.get("attribution") or item.get("creator") or "",
        }
        image_path.with_suffix(".json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        downloaded.append(
            {
                "path": image_path,
                "source_url": metadata["source_url"],
                "license": metadata["license"],
                "attribution": metadata["attribution"],
            }
        )

    return downloaded


def render_svg(input_path, png_path, size=512):
    """Render an SVG or HTML file to PNG through headless Chrome."""
    chrome = Path(os.environ.get("ASSET_CHROME") or DEFAULT_CHROME)
    if not chrome.exists():
        return False

    input_path = Path(input_path).resolve()
    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if png_path.exists():
        png_path.unlink()

    cmd = [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        f"--screenshot={png_path}",
        f"--window-size={int(size)},{int(size)}",
        "--default-background-color=00000000",
        input_path.as_uri(),
    ]
    try:
        subprocess.run(
            cmd,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return png_path.exists()


def qa(png_path):
    """Run structural, model-free PNG checks and return ok/checks/reasons."""
    png_path = Path(png_path)
    checks = {
        "exists": png_path.exists(),
        "nonempty": False,
        "valid_png": False,
        "positive_dimensions": False,
        "idat_decompresses": False,
        "not_blank": False,
    }
    reasons = []

    if not checks["exists"]:
        reasons.append("file missing")
        return {"ok": False, "checks": checks, "reasons": reasons}

    data = png_path.read_bytes()
    checks["nonempty"] = len(data) > 0
    if not checks["nonempty"]:
        reasons.append("file empty")
        return {"ok": False, "checks": checks, "reasons": reasons}

    try:
        parsed = _parse_png(data)
    except ValueError as exc:
        reasons.append(str(exc))
        return {"ok": False, "checks": checks, "reasons": reasons}

    checks["valid_png"] = True
    checks["positive_dimensions"] = parsed["width"] > 0 and parsed["height"] > 0
    checks["idat_decompresses"] = parsed["idat_decompresses"]
    checks["not_blank"] = parsed["idat_length"] > 16

    if not checks["positive_dimensions"]:
        reasons.append("PNG dimensions are not positive")
    if not checks["idat_decompresses"]:
        reasons.append("PNG IDAT data does not decompress")
    if not checks["not_blank"]:
        reasons.append("PNG appears blank or single-color")

    return {"ok": all(checks.values()), "checks": checks, "reasons": reasons}


def image_model(spec, out_path):
    """Generate painterly raster art for spec at out_path once ASSET_IMAGE_MODEL and credentials exist."""
    raise NotImplementedError(
        "raster image model not provisioned: set ASSET_IMAGE_MODEL + API key to enable painterly generation"
    )


def _constructive_svg_prompt(spec) -> str:
    techniques = SVG_TECHNIQUES.read_text(encoding="utf-8")
    spec_text = spec if isinstance(spec, str) else json.dumps(spec, indent=2, sort_keys=True)
    return f"""Build a standalone flat/structural SVG asset from this spec:

{spec_text}

You are codex acting as a pipeline engineer, not a blind illustrator. Build the asset parametrically and procedurally.

Hard requirements:
- Output ONLY one standalone <svg viewBox="0 0 512 512">...</svg>. No markdown, no prose, no code fence.
- Define proportions as explicit ratios and derived measurements.
- Place symmetric parts such as leg pairs, eyes, handles, panels, or ornaments by computed coordinates.
- Mirror repeated symmetric parts with transforms and/or <use> elements so symmetry and proportion are guaranteed by construction.
- Do not eyeball anatomy with unrelated freehand coordinates.
- Use constructive SVG primitives, reusable <defs>, clipping, gradients, filters, and layered paths where appropriate.
- Keep the asset flat/structural; do not call external images or remote resources.

Technique reference to incorporate:

{techniques}
"""


def _extract_svg(output: str) -> str | None:
    match = re.search(r"<svg\b[^>]*>.*?</svg>", output, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return match.group(0).strip()


def _openverse_results(query: str, n: int) -> list[dict]:
    url = "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(
        {"q": query, "page_size": max(1, int(n))}
    )
    try:
        payload = json.loads(_read_url(url).decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        return []

    items = []
    for result in payload.get("results", []):
        items.append(
            {
                "image_url": result.get("url"),
                "source_url": result.get("foreign_landing_url") or result.get("source_url") or result.get("url"),
                "license": result.get("license") or result.get("license_url") or "",
                "creator": result.get("creator") or "",
                "attribution": result.get("attribution") or result.get("creator") or "",
            }
        )
    return items


def _wikimedia_results(query: str, n: int) -> list[dict]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": max(1, int(n)),
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "format": "json",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    try:
        payload = json.loads(_read_url(url).decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        return []

    pages = payload.get("query", {}).get("pages", {})
    items = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        metadata = info.get("extmetadata") or {}
        license_value = _metadata_value(metadata, "LicenseShortName") or _metadata_value(metadata, "License")
        creator = _metadata_value(metadata, "Artist") or _metadata_value(metadata, "Attribution")
        source_url = info.get("descriptionurl") or page.get("fullurl") or info.get("url")
        items.append(
            {
                "image_url": info.get("url"),
                "source_url": source_url,
                "license": license_value or "",
                "creator": creator or "",
                "attribution": creator or "",
                "content_type": info.get("mime"),
            }
        )
    return items


def _metadata_value(metadata: dict, key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, dict):
        return value.get("value")
    if isinstance(value, str):
        return value
    return None


def _read_url(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ai-org-graphicist/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _image_extension(url: str, content_type: str | None = None) -> str:
    content_extensions = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    if content_type in content_extensions:
        return content_extensions[content_type]

    path = urllib.parse.urlparse(url).path.lower()
    suffix = Path(path).suffix
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".img"


def _parse_png(data: bytes) -> dict:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file")

    offset = len(PNG_SIGNATURE)
    width = 0
    height = 0
    idat_parts = []
    saw_ihdr = False
    saw_iend = False

    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        crc_end = chunk_end + 4
        if crc_end > len(data):
            raise ValueError("truncated PNG chunk")

        chunk_data = data[chunk_start:chunk_end]
        if chunk_type == b"IHDR":
            if length < 8:
                raise ValueError("invalid PNG IHDR chunk")
            width, height = struct.unpack(">II", chunk_data[:8])
            saw_ihdr = True
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            saw_iend = True
            break
        offset = crc_end

    if not saw_ihdr:
        raise ValueError("PNG missing IHDR chunk")
    if not saw_iend:
        raise ValueError("PNG missing IEND chunk")
    if not idat_parts:
        raise ValueError("PNG missing IDAT data")

    idat_data = b"".join(idat_parts)
    try:
        zlib.decompress(idat_data)
        idat_decompresses = True
    except zlib.error:
        idat_decompresses = False

    return {
        "width": width,
        "height": height,
        "idat_length": len(idat_data),
        "idat_decompresses": idat_decompresses,
    }
