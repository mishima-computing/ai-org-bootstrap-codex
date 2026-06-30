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
ANIMATE_RUNTIME_FILENAME = "animate-runtime.js"
RIG_FILENAME = "rig.json"
PREVIEW_FILENAME = "preview.html"


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

    for _attempt in range(2):
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

    output = _codex_read_only(
        _critique_prompt(brief, svg_text, qa_result),
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
  "palette_hint": "short palette direction",
  "canon_notes": "grounded notes and constraints",
  "animation": {{"needed": false, "states": []}}
}}

Rules:
- realism_cute is a number from 0.0 to 1.0, where 0.0 means realistic/painterly and 1.0 means cute/appeal.
- style must be exactly "painterly" or "cute".
- view must be the best asset view, such as "three-quarter", "side", "front", or "top-down".
- defining_features must include exact counts, silhouette markers, material cues, and canon details when they matter.
- canon_notes must summarize what your research found without URLs unless a URL is essential.
- animation.needed is true when the subject naturally needs motion or the request implies animation.
- Output JSON only. No markdown, prose, or code fence."""


def _critique_prompt(brief, svg_text, qa_result) -> str:
    return f"""You are QAing a rendered SVG asset against its autonomous art brief.

Brief:
{json.dumps(brief, indent=2, sort_keys=True)}

Model-free PNG QA result:
{json.dumps(qa_result, indent=2, sort_keys=True)}

Current SVG:
{svg_text}

The rendered PNG is attached as an image. Judge whether the asset visibly matches the brief's subject,
defining_features, canon_notes, view, palette_hint, and realism_cute level.

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
    brief["palette_hint"] = palette_hint.strip()
    brief["canon_notes"] = canon_notes.strip()
    brief["animation"] = {"needed": needed, "states": [item.strip() for item in states if item.strip()]}
    return brief


def _spec_from_brief(request, brief):
    return {
        "raw_request": str(request or ""),
        "subject": brief["subject"],
        "defining_features": brief["defining_features"],
        "canon_notes": brief["canon_notes"],
        "realism_cute": brief["realism_cute"],
        "palette_hint": brief["palette_hint"],
        "animation": brief.get("animation", {}),
        "instruction": (
            "Generate from this autonomous brief. Preserve the defining features and canon notes exactly; "
            "use the realism_cute value to balance grounded detail against appeal simplification."
        ),
    }


def _brief_requests_animation(brief) -> bool:
    animation = brief.get("animation")
    return isinstance(animation, dict) and animation.get("needed") is True


def _constructive_svg_prompt(spec, view="three-quarter", style="painterly") -> str:
    style_text = _normalized_style(style)
    style_reference = _style_reference_prompt(style_text)
    style_requirement = _style_hard_requirement(style_text)
    segmentation_requirement = _segmentation_requirement(style_text)
    spec_text = spec if isinstance(spec, str) else json.dumps(spec, indent=2, sort_keys=True)
    face_canon = f"\n{_face_canon_prompt()}\n" if _asset_has_face(spec) else ""
    return f"""Build a standalone flat/structural SVG asset from this spec:

{spec_text}

You are codex acting as a pipeline engineer, not a blind illustrator. Build the asset parametrically and procedurally.

{_view_prompt(view)}
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
            "Use proper anatomical segmentation for realism. For example, a spider must have EACH of the 8 legs as "
            "separate segmented parts with visible joints, including coxa/femur/patella/tibia/tarsus where readable, "
            "plus distinct cephalothorax and abdomen body segments."
        )
    return (
        "Keep defining body parts segmented enough to rig later, while preserving the requested cute simplified style."
    )


def _style_reference_prompt(style: str) -> str:
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
- SUBJECT FIDELITY: FIRST identify the subject's defining / identifying features, including exact counts where they matter, before simplifying. For example: spider = EXACTLY 8 legs plus cephalothorax/abdomen segmentation, an eye cluster, and chelicerae; cat = pointed ears, whiskers, and tail. These defining features are mandatory and must remain clearly readable in the silhouette. Apply cuteness by stylizing them: round them, enlarge the eyes, soften joints, and thicken limbs. Do not remove or genericize defining features. Spend the economical shape budget on the defining features first; do not drop a defining feature to save shapes. The result must be unmistakably the subject and cute; if forced to choose, keep identity readable.
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
    spider_guidance = ""
    if re.search(r"\bspider|arachnid\b", spec_text, flags=re.IGNORECASE):
        spider_guidance = (
            "\nSpider animation guidance:\n"
            "- idle: subtle body bob plus slow leg sway.\n"
            "- walk: alternating-tetrapod gait. Put legs L1/R2/L3/R4 in one phase and R1/L2/R3/L4 in the opposite phase, with body bob.\n"
        )
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
{spider_guidance}"""


def _svg_group_ids(svg_text: str) -> list[str]:
    ids = re.findall(r"<g\b[^>]*\bid=[\"']([^\"']+)[\"']", svg_text, flags=re.IGNORECASE)
    return sorted(dict.fromkeys(ids))


def _svg_manifest_comments(svg_text: str) -> str:
    comments = re.findall(r"<!--(.*?)-->", svg_text, flags=re.DOTALL)
    relevant = [comment.strip() for comment in comments if re.search(r"\brig|pivot|part\b", comment, re.I)]
    return "\n\n".join(relevant)


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
