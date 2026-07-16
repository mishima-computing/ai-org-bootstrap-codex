"""Isolated asset tool, not wired into the patch_series, patch, or merge pipeline.

Codex is the pipeline engineer, not a blind illustrator: reliable asset form
comes from construction, proportions, segmentation, and targeted QA rather than
freehand coordinates or full-regenerate visual loops. Public entries:
autonomous_create self-drives request-only asset creation through web research,
brief, generation, QA, self-critique, and optional animation; constructive_svg
builds parametric form-by-construction SVG with painterly/cute styles, side and
other views, face canon, and segmentation; animate creates rig.json keyframes,
an FK runtime, and preview.html; fetch_web_image downloads Openverse/Wikimedia
CC images without a key; render_svg renders through headless Chrome; qa runs
model-free PNG checks; image_model is an unprovisioned raster-model slot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import urllib.error
import urllib.parse
import urllib.request
import zlib

import ai_org.log as org_log


REPO_ROOT = Path(__file__).resolve().parents[1]
SVG_TECHNIQUES = REPO_ROOT / "docs" / "svg-asset-techniques.md"
DEFAULT_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ANIMATE_RUNTIME_FILENAME = "animate-runtime.js"
RIG_FILENAME = "rig.json"
PREVIEW_FILENAME = "preview.html"
CONSTRUCTION_CANON_TERMS = (
    "vector construction grid doctrine",
    "limited shape grammar",
    "optical correction doctrine",
    "small size mastering and tests",
    "identity color economy",
    "mascot identity system",
    "vector figure base construction",
    "vector costume layering",
    "vector character value staging",
    "vector silhouette first construction",
    # Memento: deformation canons enable multi-head-count projection of one character identity
    # (research: deformation-research-2026-07-04).
    "deformation information redistribution",
    "identity invariants under deformation",
    "chibi template grammar and slots",
)
CRITIQUE_CANON_TERMS = (
    "doctrine limits judgment override",
)
HEAD_UNIT_TOLERANCE = 0.25
_REFERENCE_CANON_CACHE: dict[str, str | None] = {}


def autonomous_create(request, out_dir, animate=False):
    """Create an asset from only a plain request by researching, briefing, generating, QAing, and optionally animating."""
    brief = _research_art_brief(request)
    if brief is None:
        return None

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / "asset.svg"
    png_path = out_dir / "asset.png"

    spec = _spec_from_brief(request, brief)
    generated = constructive_svg(
        spec,
        svg_path,
        view=brief["view"],
        style=brief["style"],
    )
    if generated is None:
        return None

    rendered = render_svg(svg_path, png_path)
    qa_result = qa(png_path)
    if rendered:
        log_lines = []
        head_unit_qa = {}
        corrected_for_head_count = _correct_head_count_after_render(
            spec,
            svg_path,
            png_path,
            view=brief["view"],
            style=brief["style"],
            log_lines=log_lines,
            report=head_unit_qa,
        )
        if corrected_for_head_count:
            rendered = render_svg(svg_path, png_path)
            qa_result = qa(png_path)
    else:
        log_lines = ["head-unit QA: skipped because render failed before measurement"]
        head_unit_qa = {"ok": False, "decision": "skipped_render_failed"}

    for _attempt in range(1):
        if not rendered or not png_path.exists():
            break

        critique = _critique_rendered_asset(brief, svg_path, png_path, qa_result)
        if critique is None:
            break

        if critique.get("matches") is True and qa_result.get("ok") is True:
            break

        corrected_svg = critique.get("svg")
        if not corrected_svg:
            break

        svg_path.write_text(corrected_svg, encoding="utf-8")
        rendered = render_svg(svg_path, png_path)
        qa_result = qa(png_path)

    preview_path = None
    if bool(animate) or _brief_requests_animation(brief):
        states = brief.get("animation", {}).get("states") or ["walk"]
        state = states[0] if isinstance(states[0], str) and states[0] else "walk"
        preview_path = globals()["animate"](svg_path, spec, out_dir / "anim", state=state)

    return {
        "brief": brief,
        "svg": svg_path,
        "png": png_path,
        "qa": qa_result,
        "head_unit_qa": head_unit_qa,
        "log_lines": log_lines,
        "preview": preview_path,
    }


def constructive_svg(spec, out_path, model=None, view="three-quarter", style="painterly"):
    """Generate a flat, structural SVG asset through a Codex parametric construction prompt."""
    out_path = Path(out_path)
    prompt = _constructive_svg_prompt(spec, view=view, style=style)

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


def animate(svg_path, spec, out_dir, state="walk"):
    """Generate a JSON rig and fixed FK preview runtime for a segmented SVG asset."""
    svg_path = Path(svg_path)
    out_dir = Path(out_dir)
    try:
        svg_text = svg_path.read_text(encoding="utf-8")
    except OSError:
        return None

    prompt = _animate_prompt(svg_text, spec, state=state)
    with tempfile.TemporaryDirectory(prefix="graphicist-rig-codex-") as tmp:
        codex_out = Path(tmp) / "rig-output.txt"
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
            prompt,
        ]
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

    rig = _extract_json_object(output)
    if not _valid_rig(rig):
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    rig_path = out_dir / RIG_FILENAME
    runtime_path = out_dir / ANIMATE_RUNTIME_FILENAME
    preview_path = out_dir / PREVIEW_FILENAME
    rig_path.write_text(json.dumps(rig, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime_path.write_text(_animation_runtime_js(), encoding="utf-8")
    preview_path.write_text(_preview_html(svg_text, state=state), encoding="utf-8")
    return preview_path


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


def _research_art_brief(request):
    try:
        techniques = SVG_TECHNIQUES.read_text(encoding="utf-8")
    except OSError:
        return None

    output = _codex_read_only(
        _research_brief_prompt(request, techniques),
        output_name="brief.json",
        web_search=True,
    )
    brief = _extract_json_object(output or "")
    return _normalize_art_brief(brief)


def _critique_rendered_asset(brief, svg_path, png_path, qa_result):
    try:
        svg_text = Path(svg_path).read_text(encoding="utf-8")
    except OSError:
        return None

    canon_block = _critique_canon_block(brief, style=brief.get("style", "painterly"))
    output = _codex_read_only(
        _critique_prompt(brief, svg_text, qa_result, canon_block=canon_block),
        output_name="critique.json",
        image_path=png_path,
    )
    if not output:
        return None

    parsed = _extract_json_object(output)
    if isinstance(parsed, dict):
        matches = parsed.get("matches")
        corrected = parsed.get("svg")
        if matches is True:
            return {"matches": True, "svg": None}
        if isinstance(corrected, str):
            svg = _extract_svg(corrected)
            if svg is not None:
                return {"matches": False, "svg": svg}

    svg = _extract_svg(output)
    if svg is not None:
        return {"matches": False, "svg": svg}
    return None


def _codex_read_only(prompt, output_name="codex-output.txt", image_path=None, web_search=False):
    with tempfile.TemporaryDirectory(prefix="graphicist-autonomous-codex-") as tmp:
        codex_out = Path(tmp) / output_name
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
        if web_search:
            cmd.extend(["--enable", "web_search"])
        if image_path is not None:
            cmd.extend(["-i", str(Path(image_path))])
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
        return output


def _research_brief_prompt(request, techniques) -> str:
    request_text = str(request or "")
    return f"""You are the autonomous asset artist for ai_org.graphicist.

The human provided ONLY this raw request:
{request_text}

Research what is actually being asked. If the request names a game, product, character type, genre, object,
animal, place, historical style, or visual trope, use web search to ground the subject and identify the real
references. Also research the relevant art principles needed to make a recognizable SVG asset.

Seed SVG technique library:
{techniques}

Derive the art direction yourself. Do not ask the caller for style, view, canon, or features.

Return ONLY one JSON object with at least this schema:
{{
  "subject": "specific grounded subject",
  "defining_features": ["feature required for recognizability"],
  "realism_cute": 0.0,
  "style": "painterly",
  "view": "three-quarter",
  "requested_head_units": null,
  "palette_hint": "short palette direction",
  "canon_notes": "grounded notes and constraints",
  "animation": {{"needed": false, "states": []}}
}}

Rules:
- realism_cute is a number from 0.0 to 1.0, where 0.0 means realistic/painterly and 1.0 means cute/appeal.
- style must be exactly "painterly" or "cute".
- view must be the best asset view, such as "three-quarter", "side", "front", or "top-down".
- requested_head_units is the numeric head-count proportion from the raw request when the request states one
  (for example, "exactly 2.0 heads" -> 2.0); use null or omit it when absent. Never invent this number.
- defining_features must include exact counts, silhouette markers, material cues, and canon details when they matter.
- canon_notes must summarize what your research found without URLs unless a URL is essential.
- animation.needed is true when the subject naturally needs motion or the request implies animation.
- Output JSON only. No markdown, prose, or code fence."""


def _critique_prompt(brief, svg_text, qa_result, canon_block="") -> str:
    canon_text = canon_block or "(no external construction canons available)"
    return f"""You are QAing a rendered SVG asset against its autonomous art brief.

Brief:
{json.dumps(brief, indent=2, sort_keys=True)}

Construction canons to judge against:
{canon_text}

Model-free PNG QA result:
{json.dumps(qa_result, indent=2, sort_keys=True)}

Current SVG:
{svg_text}

The rendered PNG is attached as an image. Judge whether the asset visibly matches the brief's subject,
defining_features, canon_notes, view, palette_hint, and realism_cute level.

Also judge character appeal against the construction canons:
- line of action and pose energy, not mannequin-stiff stacking;
- neck, shoulder, collar, belt, hand, and foot junction quality;
- value staging with base local color, darker accents, contact/occlusion marks, and focal highlights;
- 64px silhouette readability, including whether limbs, props, cape, hair, or accessories expand the outer contour.

Return ONLY one JSON object:
{{
  "matches": true,
  "svg": null
}}

If it does not match, set "matches" to false and put a corrected COMPLETE standalone
<svg viewBox="0 0 512 512">...</svg> string in "svg". Correct the existing SVG directly. Do not redesign from
scratch unless the current structure cannot satisfy the brief. Keep the SVG riggable with stable grouped parts."""


def _normalize_art_brief(value):
    if not isinstance(value, dict):
        return None

    subject = value.get("subject")
    features = value.get("defining_features")
    realism_cute = value.get("realism_cute")
    style = value.get("style")
    view = value.get("view")
    requested_head_units = value.get("requested_head_units")
    palette_hint = value.get("palette_hint")
    canon_notes = value.get("canon_notes")
    animation = value.get("animation")

    if not isinstance(subject, str) or not subject.strip():
        return None
    if not isinstance(features, list) or not features or not all(isinstance(item, str) and item.strip() for item in features):
        return None
    if isinstance(realism_cute, bool) or not isinstance(realism_cute, (int, float)) or not 0 <= realism_cute <= 1:
        return None
    style_text = str(style or "").strip().lower()
    if style_text not in {"painterly", "cute"}:
        return None
    if not isinstance(view, str) or not view.strip():
        return None
    if requested_head_units is not None:
        if isinstance(requested_head_units, bool) or not isinstance(requested_head_units, (int, float)):
            return None
        if requested_head_units <= 0:
            return None
    if not isinstance(palette_hint, str) or not palette_hint.strip():
        return None
    if not isinstance(canon_notes, str) or not canon_notes.strip():
        return None
    if not isinstance(animation, dict):
        return None
    needed = animation.get("needed")
    states = animation.get("states")
    if not isinstance(needed, bool):
        return None
    if not isinstance(states, list) or not all(isinstance(item, str) for item in states):
        return None

    brief = dict(value)
    brief["subject"] = subject.strip()
    brief["defining_features"] = [item.strip() for item in features]
    brief["realism_cute"] = float(realism_cute)
    brief["style"] = style_text
    brief["view"] = view.strip()
    if requested_head_units is not None:
        brief["requested_head_units"] = float(requested_head_units)
    brief["palette_hint"] = palette_hint.strip()
    brief["canon_notes"] = canon_notes.strip()
    brief["animation"] = {"needed": needed, "states": [item.strip() for item in states if item.strip()]}
    return brief


def _spec_from_brief(request, brief):
    requested_head_units = _requested_head_units_from_brief(brief)
    # Memento: requester-specified proportions are the contract; style defaults are only fallbacks.
    # This prevents the 2.0-vs-2.6 chibi incident where cute-style machinery overrode the commission.
    target_head_units = (
        requested_head_units
        if requested_head_units is not None
        else _default_target_head_units(brief.get("realism_cute"))
    )
    return {
        "raw_request": str(request or ""),
        "subject": brief["subject"],
        "defining_features": brief["defining_features"],
        "canon_notes": brief["canon_notes"],
        "realism_cute": brief["realism_cute"],
        "requested_head_units": requested_head_units,
        "palette_hint": brief["palette_hint"],
        "animation": brief.get("animation", {}),
        "yaw_degrees": _default_yaw_degrees(brief.get("view")),
        "target_head_units": target_head_units,
        "instruction": (
            "Generate from this autonomous brief. Preserve the defining features and canon notes exactly; "
            "use the realism_cute value to balance grounded detail against appeal simplification."
        ),
    }


def _requested_head_units_from_brief(brief) -> float | None:
    if not isinstance(brief, dict):
        return None
    value = brief.get("requested_head_units")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return float(value)


def _brief_requests_animation(brief) -> bool:
    animation = brief.get("animation")
    return isinstance(animation, dict) and animation.get("needed") is True


def _correct_head_count_after_render(
    spec,
    svg_path,
    png_path,
    view="three-quarter",
    style="painterly",
    log_lines=None,
    report=None,
) -> bool:
    def record(message, **fields):
        if log_lines is not None:
            log_lines.append(f"head-unit QA: {message}")
        if report is not None:
            report.update(fields)

    if not Path(png_path).exists():
        record("skipped because rendered PNG is missing", ok=False, decision="skipped_png_missing")
        return False

    target = _target_head_units(spec, style=style)
    if target is None:
        record("skipped because no target head-unit contract applies", ok=False, decision="skipped_no_target")
        return False

    try:
        svg_text = Path(svg_path).read_text(encoding="utf-8")
    except OSError:
        record(
            f"skipped because SVG could not be read; target={float(target):g}",
            ok=False,
            decision="skipped_svg_unreadable",
            target_head_units=float(target),
        )
        return False

    measurement = _measure_svg_head_unit_count(svg_text)
    measured = measurement.get("head_units") if measurement.get("ok") else None
    if not isinstance(measured, (int, float)):
        record(
            f"skipped because measurement failed; target={float(target):g}; reason={measurement.get('reason', 'unknown')}",
            ok=False,
            decision="skipped_measurement_failed",
            target_head_units=float(target),
            measurement=measurement,
        )
        return False
    if abs(float(measured) - float(target)) <= HEAD_UNIT_TOLERANCE:
        record(
            f"measured={float(measured):.3f}; target={float(target):.3f}; within tolerance, no correction",
            ok=True,
            decision="accepted",
            measured_head_units=float(measured),
            target_head_units=float(target),
            tolerance=HEAD_UNIT_TOLERANCE,
            measurement=measurement,
            corrected=False,
        )
        return False

    feedback_spec = _spec_with_head_unit_feedback(spec, target=float(target), measured=float(measured))
    regenerated = constructive_svg(feedback_spec, svg_path, view=view, style=style)
    corrected = regenerated is not None
    record(
        (
            f"measured={float(measured):.3f}; target={float(target):.3f}; "
            f"outside tolerance, correction {'applied' if corrected else 'failed'}"
        ),
        ok=corrected,
        decision="corrected" if corrected else "correction_failed",
        measured_head_units=float(measured),
        target_head_units=float(target),
        tolerance=HEAD_UNIT_TOLERANCE,
        measurement=measurement,
        corrected=corrected,
    )
    return corrected


def _spec_with_head_unit_feedback(spec, target: float, measured: float):
    if isinstance(spec, dict):
        feedback_spec = dict(spec)
    else:
        feedback_spec = {"raw_spec": str(spec or "")}
    feedback_spec["target_head_units"] = target
    feedback_spec["head_unit_feedback"] = {
        "measured_head_units": round(measured, 3),
        "target_head_units": round(target, 3),
        "tolerance": HEAD_UNIT_TOLERANCE,
        "instruction": (
            "Correct the construction scale mechanically: full figure height divided by the head group height "
            "must land within tolerance of target_head_units. Change head/body proportions, not just prose."
        ),
    }
    return feedback_spec


def _constructive_svg_prompt(spec, view="three-quarter", style="painterly") -> str:
    style_text = _normalized_style(style)
    style_reference = _style_precedent_prompt(style_text)
    style_requirement = _style_hard_requirement(style_text)
    segmentation_requirement = _segmentation_requirement(style_text)
    spec_text = spec if isinstance(spec, str) else json.dumps(spec, indent=2, sort_keys=True)
    face_canon = f"\n{_face_canon_prompt()}\n" if _asset_has_face(spec) else ""
    canon_block = _construction_canon_block(spec, style=style_text)
    yaw_prompt = _body_yaw_prompt(spec, view=view)
    head_unit_prompt = _head_unit_prompt(spec, style=style_text)
    return f"""Build a standalone flat/structural SVG asset from this spec:

{spec_text}

You are codex acting as a pipeline engineer, not a blind illustrator. Build the asset parametrically and procedurally.

{canon_block}

{_view_prompt(view)}
{yaw_prompt}
{head_unit_prompt}
{face_canon}
Hard requirements:
- Output ONLY one standalone <svg viewBox="0 0 512 512">...</svg>. No markdown, no prose, no code fence.
- Define proportions as explicit ratios and derived measurements.
- Place symmetric parts such as leg pairs, eyes, handles, panels, or ornaments by computed coordinates.
- Mirror repeated symmetric parts with transforms and/or <use> elements so symmetry and proportion are guaranteed by construction.
- Do not eyeball anatomy with unrelated freehand coordinates.
- {style_requirement}
- {segmentation_requirement}
- Make the asset riggable: every animatable body part MUST be its own <g id="..."> with a stable, predictable id.
- Use this id convention for creatures and characters: core body ids such as "cephalothorax", "abdomen", "head", and limb ids such as "leg-L1-upper", "leg-L1-lower", "leg-L1-foot", "leg-R1-upper", "leg-R1-lower", "leg-R1-foot". Number legs from front to back in side/profile view; use L/R for the visible side pair or mirrored side when both sides are present.
- Put a rig manifest XML comment inside the SVG listing each animatable part id, parent id or null, and a suggested pivot [x,y] in viewBox coordinates at the joint location. Example line: part leg-L1-upper parent cephalothorax pivot [198,276]. These pivots are consumed by a JSON FK rig and must be stable.
- Keep the asset flat/structural; do not call external images or remote resources.

Style reference to incorporate:

{style_reference}
"""


def _construction_canon_block(spec, style="painterly") -> str:
    terms = list(CONSTRUCTION_CANON_TERMS)
    terms.extend(_style_relevant_precedent_terms(spec, style=style))
    rows = _precedent_canon_rows(terms)
    if not rows:
        return ""

    # Memento: amateur-exemplar canons poisoned output (bullseye incident 2026-07-03)
    # and were replaced by axiom facets. Figure canons returned only after being
    # re-derived from professional sources (Open Peeps/Humaaans; pro-corpus-2026-07-03).
    return (
        "construction axioms (source-backed logo/mascot doctrine - apply as geometry):\n"
        + "\n\n".join(rows)
    )


def _critique_canon_block(spec, style="painterly") -> str:
    blocks = []
    construction_block = _construction_canon_block(spec, style=style)
    if construction_block:
        blocks.append(construction_block)

    critique_rows = _precedent_canon_rows(CRITIQUE_CANON_TERMS)
    if critique_rows:
        blocks.append("critique doctrine (source-backed judgment limits):\n" + "\n\n".join(critique_rows))
    return "\n\n".join(blocks)


def _precedent_canon_rows(terms) -> list[str]:
    rows = []
    for term in dict.fromkeys(terms):
        text = _cached_precedent_canon(term)
        if text:
            rows.append(f"[{term}]\n{text}")
    return rows


def _style_relevant_precedent_terms(spec, style="painterly") -> list[str]:
    spec_text = spec if isinstance(spec, str) else json.dumps(spec, sort_keys=True)
    terms = []
    if re.search(r"\b(lpc|liberated pixel cup|pixel art|sprite|tile|tileset)\b", spec_text, flags=re.IGNORECASE):
        terms.append("LPC style guide existence proof")
    if style == "cute" or re.search(r"\b(style guide|art bible|exemplar|appeal|character)\b", spec_text, flags=re.IGNORECASE):
        terms.append("art bible two layers plus exemplar anchor")
    return terms


def _cached_precedent_canon(term: str) -> str | None:
    key = _precedent_cache_key(term)
    if key not in _REFERENCE_CANON_CACHE:
        _REFERENCE_CANON_CACHE[key] = _lookup_precedent_canon(term)
    return _REFERENCE_CANON_CACHE[key]


def _precedent_cache_key(term: str) -> str:
    return re.sub(r"\s+", " ", str(term or "").strip().lower())


def _lookup_precedent_canon(term: str) -> str | None:
    try:
        from ai_org import engineering_precedent_store

        entry = engineering_precedent_store.lookup(term, kind="design")
    except engineering_precedent_store.StoreUnavailable as exc:
        org_log.emit(
            "engineering_precedent_store.store_unavailable",
            {"reason": exc.reason, "caller": "graphicist._lookup_precedent_canon"},
            ctx=org_log.RunContext(repo=REPO_ROOT, stage="graphicist.precedent_store"),
        )
        return None

    candidates = entry.get("candidates") if isinstance(entry, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return None

    pieces = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        text = _precedent_candidate_prompt_text(candidate)
        if text:
            pieces.append(text)
    return "\n".join(pieces) or None


def _precedent_candidate_prompt_text(candidate: dict) -> str:
    fields = (
        "structure",
        "rationale",
        "implementation_hooks",
        "quality_attributes",
        "evidence",
        "delta_claim",
        "tradeoffs",
        "when_to_use",
        "when_not_to_use",
        "summary",
        "pitfalls",
        "snippet",
    )
    lines = []
    for field in fields:
        value = str(candidate.get(field) or "").strip()
        if value:
            lines.append(f"- {field}: {_trim_prompt_text(value)}")
    return "\n".join(lines)


def _trim_prompt_text(value: str, limit: int = 1800) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _body_yaw_prompt(spec, view="three-quarter") -> str:
    yaw = _yaw_degrees(spec, view=view)
    near_side = "left" if yaw >= 0 else "right"
    far_side = "right" if yaw >= 0 else "left"
    return f"""Body-yaw construction:
- Target body yaw: {yaw:g} degrees from camera; build it with geometry, overlap, and asymmetry.
- Near side is the {near_side} side and far side is the {far_side} side for this yaw. Make the near shoulder/hip/foot slightly larger and lower; tuck the far shoulder/hip/foot partly behind the torso.
- Rotate the ribcage, pelvis, belt line, collar, and feet consistently. Foot direction must agree with the torso yaw rather than pointing straight forward by default.
- Use a centerline, shoulder line, pelvis line, and offset limb anchors before drawing costume or surface detail. Do not rely on numeric yaw prose alone."""


def _head_unit_prompt(spec, style="painterly") -> str:
    target = _target_head_units(spec, style=style)
    if target is None:
        return ""
    return f"""Head-count construction:
- Target full figure height: {target:g} head units, measured as full visible figure bbox height divided by the <g id="head"> bbox height.
- Create a real <g id="head"> around the complete head mass so deterministic QA can measure it.
- Keep the final ratio within +/- {HEAD_UNIT_TOLERANCE:g} head. If simplifying for cuteness, reduce detail without drifting from this head-unit target."""


def _yaw_degrees(spec, view="three-quarter") -> float:
    explicit = _first_number_from_spec(spec, ("yaw_degrees", "body_yaw_degrees", "yaw"))
    if explicit is not None:
        return max(-90.0, min(90.0, explicit))
    return _default_yaw_degrees(view)


def _default_yaw_degrees(view="three-quarter") -> float:
    view_text = str(view or "").strip().lower()
    if view_text in {"side", "profile", "orthographic side", "orthographic-side", "side view", "side-view"}:
        return 90.0
    if view_text in {"front", "frontal", "front view", "front-view"}:
        return 0.0
    return 30.0


def _target_head_units(spec, style="painterly") -> float | None:
    explicit = _first_number_from_spec(
        spec,
        ("requested_head_units", "target_head_units", "head_units", "head_count", "target_head_count"),
    )
    if explicit is not None and explicit > 0:
        return explicit
    realism = _first_number_from_spec(spec, ("realism_cute",))
    if realism is not None:
        return _default_target_head_units(realism)
    if _asset_has_face(spec):
        return 3.5 if _normalized_style(style) == "cute" else 5.5
    return None


def _default_target_head_units(realism_cute) -> float:
    try:
        cute = float(realism_cute)
    except (TypeError, ValueError):
        cute = 0.0
    cute = max(0.0, min(1.0, cute))
    return round(5.5 - (2.0 * cute), 2)


def _first_number_from_spec(spec, names: tuple[str, ...]) -> float | None:
    if not isinstance(spec, dict):
        return None
    pending = [spec]
    seen = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        for name in names:
            value = current.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
        for value in current.values():
            if isinstance(value, dict):
                pending.append(value)
    return None


def _normalized_style(style) -> str:
    style_text = str(style or "painterly").strip().lower()
    if style_text not in {"painterly", "cute"}:
        return "painterly"
    return style_text


def _style_hard_requirement(style: str) -> str:
    if style == "cute":
        return (
            "Use constructive SVG primitives, closed vector shapes, reusable <defs>/<use>, grouped semantic parts, "
            "flat fills, consistent rounded strokes, and clean cel-shadow shapes where appropriate."
        )
    return "Use constructive SVG primitives, reusable <defs>, clipping, gradients, filters, and layered paths where appropriate."


def _segmentation_requirement(style: str) -> str:
    if style == "painterly":
        return (
            "Use proper anatomical segmentation for realism. Preserve each count-sensitive defining appendage as "
            "separate segmented parts with visible joints, and keep major body masses distinct where readable."
        )
    return (
        "Keep defining body parts segmented enough to rig later, while preserving the requested cute simplified style."
    )


def _style_precedent_prompt(style: str) -> str:
    if style == "cute":
        return _cute_canon_prompt()
    return _painterly_techniques_prompt()


def _painterly_techniques_prompt() -> str:
    techniques = SVG_TECHNIQUES.read_text(encoding="utf-8")
    cute_heading = "\n## 4. Cute / appeal style (Flash-era vector)"
    sources_heading = "\n## Sources"
    if cute_heading in techniques and sources_heading in techniques:
        before_cute, after_cute = techniques.split(cute_heading, 1)
        _, sources = after_cute.split(sources_heading, 1)
        return before_cute.rstrip() + sources_heading + sources
    return techniques


def _cute_canon_prompt() -> str:
    return """CUTE / APPEAL CANON (Flash-era clean vector):
- Style priority: ORIGINAL appeal character art with a flat, clean vector-cartoon look. This style overrides painterly texture, noisy overlays, realistic lighting, heavy filters, and filter-heavy shadow stacks.
- Construction: build closed vector shapes with flat fills and consistent strokes using rounded joins/caps. Put reusable repeated parts in <defs> and place them with <use>, especially eyes, highlights, limbs, and paired details. Group semantic parts such as head, face, body, arms, and legs. Keep the shape count economical, about 12-40 deliberate shapes.
- SUBJECT FIDELITY: FIRST identify the subject's defining / identifying features, including exact counts where they matter, before simplifying. These defining features are mandatory and must remain clearly readable in the silhouette. Apply cuteness by stylizing them: round them, enlarge the eyes, soften joints, and thicken limbs. Do not remove or genericize defining features. Spend the economical shape budget on the defining features first; do not drop a defining feature to save shapes. The result must be unmistakably the subject and cute; if forced to choose, keep identity readable.
- Shape language: choose one dominant family. Use round/bean shapes for the default cute/friendly read, rounded-square shapes for sturdy appeal, or teardrops for energetic/magical appeal. Sharp angles are allowed only as tiny rounded accents. The silhouette must read at about 64px.
- Cute proportions: normalize the character height to 100 units. Head is 42-55 units tall, body is 32-42, neck is minimal or hidden, and legs are short, thick, and rounded. Eyes are large, 18-28% of head height. Nose and mouth stay small and low on the face.
- Eye system: layer each eye from outer shape, iris, pupil, large white highlight, tiny secondary highlight, and optional lid. Keep iris and pupil large. Show expression through brows, lids, and mouth, not by changing head anatomy. Default expression is warm, with open eyes and a small smile.
- Palette: use one dominant color, one accent or hair color, one small complementary pop, and a dark hue-shifted outline color that is never pure black. Keep colors saturated but harmonious. Use flat cel shadows only, 0-2 shapes at 10-20% darker than the base. Avoid gradients except for a subtle iris gradient if useful.
- Face focus: make the face the focal zone, structurally balanced and symmetric. Reuse the face canon for feature placement when the asset has a face."""


def _view_prompt(view="three-quarter") -> str:
    view_text = str(view or "three-quarter")
    prompt = (
        f"View / composition:\n"
        f"- COMPOSE in this requested view: {view_text}.\n"
        "- Do not default to a flat top-down map; use top-down only when the requested view explicitly says top-down, "
        "overhead, map, or plan view.\n"
    )
    if view_text.strip().lower() in {"three-quarter", "3/4", "three quarter", "angled side", "angled-side"}:
        prompt += (
            "- For three-quarter view, construct the parts in a 3/4 projection with mild foreshortening, a consistent "
            "ground plane, and light from upper-left.\n"
            "- Show angled side faces and overlapping depth cues so the result reads as an object in space, not a flat "
            "top-down diagram.\n"
        )
    if view_text.strip().lower() in {"side", "profile", "orthographic side", "orthographic-side", "side view", "side-view"}:
        prompt += (
            "- For side/profile view, construct an orthographic side silhouette with the creature in PROFILE.\n"
            "- For a walking creature, keep legs visible from the side so the gait reads, with front-to-back leg "
            "numbering and clear body segments readable in side view.\n"
        )
    return prompt.rstrip()


def _animate_prompt(svg_text, spec, state="walk") -> str:
    spec_text = spec if isinstance(spec, str) else json.dumps(spec, indent=2, sort_keys=True)
    part_ids = _svg_group_ids(svg_text)
    manifest = _svg_manifest_comments(svg_text)
    return f"""Create an animation rig JSON for this segmented SVG.

Spec:
{spec_text}

Requested default state: {state}

SVG group ids detected:
{json.dumps(part_ids, indent=2)}

Rig manifest comments detected:
{manifest or "(none)"}

Rig schema:
{{
  "parts": {{
    "<id>": {{"selector": "#<id>", "pivot": [x, y], "parent": "<id-or-null>"}}
  }},
  "states": {{
    "idle": {{"duration": 1.6, "parts": {{"<id>": [{{"t": 0, "rot": 0}}, {{"t": 0.8, "rot": 2}}]}}}},
    "walk": {{"duration": 1.0, "parts": {{"<id>": [{{"t": 0, "rot": -12, "x": 0, "y": 0}}, {{"t": 0.5, "rot": 12}}]}}}}
  }}
}}

Rules:
- Output ONLY valid JSON. No markdown, prose, comments, or trailing commas.
- Do not edit or regenerate SVG path geometry. Animate only by JSON keyframes.
- Include every riggable SVG part id from the manifest when possible.
- Use selectors matching the SVG group ids, normally "#<id>".
- Use pivots in the SVG viewBox coordinate system at the anatomical joint location.
- Parent core body parts sensibly, with null for the root body part.
- Include both "idle" and "walk" states even if the requested default state differs.
- Keyframes use seconds in t, degrees in rot, and optional x/y/sx/sy local pose values.
"""


def _svg_group_ids(svg_text: str) -> list[str]:
    ids = re.findall(r"<g\b[^>]*\bid=[\"']([^\"']+)[\"']", svg_text, flags=re.IGNORECASE)
    return sorted(dict.fromkeys(ids))


def _svg_manifest_comments(svg_text: str) -> str:
    comments = re.findall(r"<!--(.*?)-->", svg_text, flags=re.DOTALL)
    relevant = [comment.strip() for comment in comments if re.search(r"\brig|pivot|part\b", comment, re.I)]
    return "\n\n".join(relevant)


def _measure_svg_head_unit_count(svg_text: str) -> dict:
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return {"ok": False, "reason": f"invalid svg xml: {exc}"}

    id_bboxes: dict[str, tuple[float, float, float, float]] = {}
    figure_bbox = _svg_element_bbox(root, _identity_matrix(), id_bboxes)
    head_bbox = _head_bbox(id_bboxes)
    if figure_bbox is None:
        return {"ok": False, "reason": "figure bbox unavailable"}
    if head_bbox is None:
        return {"ok": False, "reason": "head group bbox unavailable"}

    figure_height = figure_bbox[3] - figure_bbox[1]
    head_height = head_bbox[3] - head_bbox[1]
    if figure_height <= 0 or head_height <= 0:
        return {"ok": False, "reason": "non-positive figure or head height"}

    return {
        "ok": True,
        "head_units": figure_height / head_height,
        "figure_bbox": list(figure_bbox),
        "head_bbox": list(head_bbox),
    }


def _head_bbox(id_bboxes: dict[str, tuple[float, float, float, float]]) -> tuple[float, float, float, float] | None:
    if "head" in id_bboxes:
        return id_bboxes["head"]
    for element_id, bbox in sorted(id_bboxes.items()):
        if re.search(r"(^|[-_])head($|[-_])", element_id, flags=re.IGNORECASE):
            return bbox
    return None


def _svg_element_bbox(
    element,
    inherited_matrix: tuple[float, float, float, float, float, float],
    id_bboxes: dict[str, tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    tag = _svg_local_name(element.tag)
    if tag in {"defs", "clipPath", "mask", "marker", "pattern", "linearGradient", "radialGradient", "filter", "style"}:
        return None

    matrix = _multiply_matrix(inherited_matrix, _parse_svg_transform(element.attrib.get("transform", "")))
    bboxes = []
    own_bbox = _svg_shape_bbox(tag, element.attrib)
    if own_bbox is not None:
        bboxes.append(_transform_bbox(own_bbox, matrix))

    for child in list(element):
        child_bbox = _svg_element_bbox(child, matrix, id_bboxes)
        if child_bbox is not None:
            bboxes.append(child_bbox)

    bbox = _union_bboxes(bboxes)
    element_id = element.attrib.get("id")
    if bbox is not None and isinstance(element_id, str) and element_id:
        id_bboxes[element_id] = bbox
    return bbox


def _svg_shape_bbox(tag: str, attrib: dict) -> tuple[float, float, float, float] | None:
    if tag == "rect":
        x = _svg_float(attrib.get("x"), 0.0)
        y = _svg_float(attrib.get("y"), 0.0)
        width = _svg_float(attrib.get("width"), 0.0)
        height = _svg_float(attrib.get("height"), 0.0)
        if width > 0 and height > 0:
            return (x, y, x + width, y + height)
    if tag == "circle":
        cx = _svg_float(attrib.get("cx"), 0.0)
        cy = _svg_float(attrib.get("cy"), 0.0)
        r = _svg_float(attrib.get("r"), 0.0)
        if r > 0:
            return (cx - r, cy - r, cx + r, cy + r)
    if tag == "ellipse":
        cx = _svg_float(attrib.get("cx"), 0.0)
        cy = _svg_float(attrib.get("cy"), 0.0)
        rx = _svg_float(attrib.get("rx"), 0.0)
        ry = _svg_float(attrib.get("ry"), 0.0)
        if rx > 0 and ry > 0:
            return (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag == "line":
        x1 = _svg_float(attrib.get("x1"), 0.0)
        y1 = _svg_float(attrib.get("y1"), 0.0)
        x2 = _svg_float(attrib.get("x2"), 0.0)
        y2 = _svg_float(attrib.get("y2"), 0.0)
        return _points_bbox([(x1, y1), (x2, y2)])
    if tag in {"polyline", "polygon"}:
        return _points_bbox(_svg_points(attrib.get("points", "")))
    if tag == "path":
        return _path_coordinate_bbox(attrib.get("d", ""))
    return None


def _svg_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", str(value))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _svg_points(value: str) -> list[tuple[float, float]]:
    numbers = _svg_numbers(value)
    return [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, 2)]


def _path_coordinate_bbox(value: str) -> tuple[float, float, float, float] | None:
    numbers = _svg_numbers(value)
    if len(numbers) < 2:
        return None
    return _points_bbox([(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, 2)])


def _svg_numbers(value: str) -> list[float]:
    return [
        float(match.group(0))
        for match in re.finditer(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", str(value or ""))
    ]


def _points_bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _transform_bbox(
    bbox: tuple[float, float, float, float],
    matrix: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    points = [
        _transform_point(x1, y1, matrix),
        _transform_point(x2, y1, matrix),
        _transform_point(x1, y2, matrix),
        _transform_point(x2, y2, matrix),
    ]
    return _points_bbox(points) or bbox


def _transform_point(
    x: float,
    y: float,
    matrix: tuple[float, float, float, float, float, float],
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _parse_svg_transform(value: str) -> tuple[float, float, float, float, float, float]:
    matrix = _identity_matrix()
    for match in re.finditer(r"(matrix|translate|scale)\s*\(([^)]*)\)", str(value or ""), flags=re.IGNORECASE):
        name = match.group(1).lower()
        numbers = _svg_numbers(match.group(2))
        if name == "matrix" and len(numbers) >= 6:
            operation = tuple(numbers[:6])
        elif name == "translate" and numbers:
            operation = (1.0, 0.0, 0.0, 1.0, numbers[0], numbers[1] if len(numbers) > 1 else 0.0)
        elif name == "scale" and numbers:
            sx = numbers[0]
            sy = numbers[1] if len(numbers) > 1 else sx
            operation = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        else:
            continue
        matrix = _multiply_matrix(matrix, operation)
    return matrix


def _identity_matrix() -> tuple[float, float, float, float, float, float]:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiply_matrix(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    la, lb, lc, ld, le, lf = left
    ra, rb, rc, rd, re, rf = right
    return (
        la * ra + lc * rb,
        lb * ra + ld * rb,
        la * rc + lc * rd,
        lb * rc + ld * rd,
        la * re + lc * rf + le,
        lb * re + ld * rf + lf,
    )


def _union_bboxes(bboxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float] | None:
    real = [bbox for bbox in bboxes if bbox is not None]
    if not real:
        return None
    return (
        min(bbox[0] for bbox in real),
        min(bbox[1] for bbox in real),
        max(bbox[2] for bbox in real),
        max(bbox[3] for bbox in real),
    )


def _svg_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _extract_json_object(output: str):
    text = (output or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _valid_rig(rig) -> bool:
    if not isinstance(rig, dict):
        return False
    parts = rig.get("parts")
    states = rig.get("states")
    if not isinstance(parts, dict) or not parts:
        return False
    if not isinstance(states, dict) or not states:
        return False

    for part_id, part in parts.items():
        if not isinstance(part_id, str) or not part_id:
            return False
        if not isinstance(part, dict):
            return False
        selector = part.get("selector")
        pivot = part.get("pivot")
        parent = part.get("parent")
        if not isinstance(selector, str) or not selector:
            return False
        if not _valid_number_pair(pivot):
            return False
        if parent is not None and not isinstance(parent, str):
            return False
        if isinstance(parent, str) and parent not in parts:
            return False

    for state in states.values():
        if not isinstance(state, dict):
            return False
        duration = state.get("duration")
        state_parts = state.get("parts")
        if not isinstance(duration, (int, float)) or duration <= 0:
            return False
        if not isinstance(state_parts, dict) or not state_parts:
            return False
        for part_id, keyframes in state_parts.items():
            if part_id not in parts:
                return False
            if not isinstance(keyframes, list) or not keyframes:
                return False
            for frame in keyframes:
                if not isinstance(frame, dict):
                    return False
                if not isinstance(frame.get("t"), (int, float)):
                    return False
                for key in ("rot", "x", "y", "sx", "sy"):
                    if key in frame and not isinstance(frame[key], (int, float)):
                        return False
    return True


def _valid_number_pair(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, (int, float)) for item in value)
    )


def _preview_html(svg_text: str, state="walk") -> str:
    inline_svg = re.sub(r"<\?xml[^>]*\?>", "", svg_text, flags=re.IGNORECASE).strip()
    inline_svg = re.sub(r"<!DOCTYPE[^>]*>", "", inline_svg, flags=re.IGNORECASE).strip()
    state_json = json.dumps(str(state))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Animated SVG Preview</title>
  <style>
    html, body {{
      margin: 0;
      min-height: 100%;
      background: #f5f7fa;
    }}
    body {{
      display: grid;
      place-items: center;
    }}
    main {{
      width: min(92vmin, 760px);
      aspect-ratio: 1;
      display: grid;
      place-items: center;
    }}
    svg {{
      width: 100%;
      height: auto;
      overflow: visible;
    }}
  </style>
</head>
<body>
  <main id="stage">
{inline_svg}
  </main>
  <script src="./animate-runtime.js"></script>
  <script>
    const requestedState = {state_json};
    fetch("./rig.json")
      .then((response) => response.json())
      .then((rig) => {{
        const svg = document.querySelector("#stage svg");
        window.GraphicistAnimation.play(svg, rig, requestedState);
      }})
      .catch((error) => {{
        console.error("Animation rig failed to load", error);
      }});
  </script>
</body>
</html>
"""


def _animation_runtime_js() -> str:
    return """(function () {
  "use strict";

  function numberOr(value, fallback) {
    return Number.isFinite(Number(value)) ? Number(value) : fallback;
  }

  function matrixAttribute(matrix) {
    return "matrix(" + [
      matrix.a,
      matrix.b,
      matrix.c,
      matrix.d,
      matrix.e,
      matrix.f
    ].map(function (value) {
      return Number(value.toFixed(6));
    }).join(" ") + ")";
  }

  function partMatrix(pivot, offset, pose) {
    var p = Array.isArray(pivot) ? pivot : [0, 0];
    var o = Array.isArray(offset) ? offset : [0, 0];
    var current = pose || {};
    var x = numberOr(current.x, 0) + numberOr(o[0], 0);
    var y = numberOr(current.y, 0) + numberOr(o[1], 0);
    var rot = numberOr(current.rot, 0);
    var sx = numberOr(current.sx, 1);
    var sy = numberOr(current.sy, 1);
    var px = numberOr(p[0], 0);
    var py = numberOr(p[1], 0);
    var matrix = new DOMMatrix();
    matrix.translateSelf(px + x, py + y);
    matrix.rotateSelf(rot);
    matrix.scaleSelf(sx, sy);
    matrix.translateSelf(-px, -py);
    return matrix;
  }

  function sortedPartIds(rig) {
    var parts = rig.parts || {};
    var order = [];
    var visiting = {};
    var visited = {};

    function visit(id) {
      if (visited[id] || visiting[id]) {
        return;
      }
      visiting[id] = true;
      var parent = parts[id] && parts[id].parent;
      if (parent && parts[parent]) {
        visit(parent);
      }
      visiting[id] = false;
      visited[id] = true;
      order.push(id);
    }

    Object.keys(parts).forEach(visit);
    return order;
  }

  function escapeSelectorId(id) {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return "#" + window.CSS.escape(id);
    }
    return "#" + String(id).replace(/([ #;?%&,.+*~':"!^$[\\]()=>|/@])/g, "\\\\$1");
  }

  function renderRig(svg, rig, poseByPart) {
    if (!svg || !rig || !rig.parts) {
      return;
    }
    var parts = rig.parts;
    var world = {};
    sortedPartIds(rig).forEach(function (id) {
      var part = parts[id];
      var local = partMatrix(part.pivot || [0, 0], [0, 0], poseByPart[id] || {});
      var parent = part.parent;
      var matrix = parent && world[parent] ? world[parent].multiply(local) : local;
      world[id] = matrix;
      var selector = part.selector || escapeSelectorId(id);
      var node = svg.querySelector(selector);
      if (node) {
        node.setAttribute("transform", matrixAttribute(matrix));
      }
    });
  }

  function interpolateFrames(frames, seconds, duration) {
    if (!Array.isArray(frames) || frames.length === 0) {
      return {};
    }
    var sorted = frames.slice().sort(function (a, b) {
      return numberOr(a.t, 0) - numberOr(b.t, 0);
    });
    if (sorted.length === 1) {
      return Object.assign({}, sorted[0]);
    }
    var localTime = ((seconds % duration) + duration) % duration;
    var previous = sorted[0];
    var next = sorted[0];

    for (var index = 0; index < sorted.length; index += 1) {
      var current = sorted[index];
      var candidate = sorted[(index + 1) % sorted.length];
      var currentT = numberOr(current.t, 0);
      var candidateT = numberOr(candidate.t, 0);
      var wrappedCandidateT = index === sorted.length - 1 ? candidateT + duration : candidateT;
      var wrappedLocalTime = localTime < currentT ? localTime + duration : localTime;
      if (wrappedLocalTime >= currentT && wrappedLocalTime <= wrappedCandidateT) {
        previous = current;
        next = candidate;
        localTime = wrappedLocalTime;
        break;
      }
    }

    var prevT = numberOr(previous.t, 0);
    var nextT = numberOr(next.t, 0);
    if (nextT <= prevT) {
      nextT += duration;
    }
    var span = Math.max(0.0001, nextT - prevT);
    var alpha = Math.max(0, Math.min(1, (localTime - prevT) / span));
    var pose = {};
    ["rot", "x", "y", "sx", "sy"].forEach(function (key) {
      var startDefault = key === "sx" || key === "sy" ? 1 : 0;
      var start = numberOr(previous[key], startDefault);
      var end = numberOr(next[key], start);
      pose[key] = start + (end - start) * alpha;
    });
    return pose;
  }

  function play(svg, rig, stateName) {
    var state = rig && rig.states && rig.states[stateName];
    if (!state) {
      state = rig && rig.states && rig.states.idle;
    }
    if (!svg || !state) {
      return null;
    }
    var duration = Math.max(0.0001, numberOr(state.duration, 1));
    var start = performance.now();
    var frameId = null;

    function frame(now) {
      var seconds = (now - start) / 1000;
      var poses = {};
      Object.keys(state.parts || {}).forEach(function (id) {
        poses[id] = interpolateFrames(state.parts[id], seconds, duration);
      });
      renderRig(svg, rig, poses);
      frameId = requestAnimationFrame(frame);
    }

    frameId = requestAnimationFrame(frame);
    return {
      stop: function () {
        if (frameId !== null) {
          cancelAnimationFrame(frameId);
        }
      }
    };
  }

  window.GraphicistAnimation = {
    partMatrix: partMatrix,
    renderRig: renderRig,
    interpolateFrames: interpolateFrames,
    play: play
  };
}());
"""


def _asset_has_face(spec) -> bool:
    spec_text = spec if isinstance(spec, str) else json.dumps(spec, sort_keys=True)
    return bool(
        re.search(
            r"\b(face|head|portrait|character|creature|human|person|people|animal|monster|robot|eye|eyes|brow|nose|"
            r"mouth|mandible|mandibles|chelicera|chelicerae|skull|mask)\b",
            spec_text,
            flags=re.IGNORECASE,
        )
    )


def _face_canon_prompt() -> str:
    return """Faces (dedicated canon):
- Treat the face/head region as the FOCAL detail zone; spend the highest precision and detail there.
- Build the head on a construction frame, Loomis-style: cranial sphere plus face plane, with a clear vertical center line.
- Make all facial features mirror across the center line; use computed ratios, transforms, and/or <use> mirroring instead of freehand paired coordinates.
- Put the eye-line at the vertical MIDLINE of the head. Space eyes about one eye-width apart, with eye width, gaps, brow, nose, and mouth positions derived from head width/height ratios.
- Place brow, nose, and mouth by facial thirds. For creatures, place the eye CLUSTER and chelicerae/mandibles by the same symmetric, ratio-driven rules.
- Size and place features by explicit ratios relative to head size so the face reads correctly. Even small asymmetry or misplacement makes a face look wrong."""


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
