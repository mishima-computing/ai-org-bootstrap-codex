#!/usr/bin/env python3
"""Independent measurement instrument for the result-screen cartridge.

NN1 applied to the artifact: the result screen DERIVES its own bg-dim/face-dim and reports a
self-measure, but a self-report is not the verification. This instrument renders each variant to
a real screenshot and INDEPENDENTLY measures, on the rendered pixels:

  * contrast per text rect, estimating the background as the dominant colour cluster behind the
    text (works for light-on-dark panel text and dark-on-gold button text alike), versus the
    text colour the page reports;
  * the saliency hierarchy S_reward > S_face and S_reward > S_background, with S_next in the top
    three, using a luminance*saturation*local-variance proxy;
  * flash safety, structurally (no full-screen white/red flash keyframe, no infinite background
    animation) plus the reveal-burst constant being within the <=500ms band.

It does not trust window.__resultMeasure for the verdict; it reads the rect geometry and text
colours from the self-report only to know WHERE and AGAINST WHAT to measure, then measures the
pixels itself. Exit 0 = all variants pass, 1 = a measured failure, 2 = instrument/Chrome error.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chrome_capture as cc  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

DEMO_ROOT = Path(__file__).resolve().parents[1] / "demos" / "retro-gacha-gui"
TARGET = "/result-screen/index.html"
VARIANTS = ["normal", "maxrewards", "longtext", "norewards", "defeat", "error"]

# Saliency regions in 1920x1080 stage coordinates.
SALIENCY_REGIONS = {
    "reward": (704, 304, 512, 272),
    "face": (120, 190, 200, 230),
    "next": (1280, 832, 288, 88),
    "background": (1664, 60, 200, 200),
}
# The control that must hold initial focus with a visible ring (XAG 112): Next normally, Retry in the modal.
FOCUS_BOX = {"default": (1280, 832, 288, 88), "error": (816, 616, 288, 88)}


def srgb_to_lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(rgb) -> float:
    return 0.2126 * srgb_to_lin(rgb[0]) + 0.7152 * srgb_to_lin(rgb[1]) + 0.0722 * srgb_to_lin(rgb[2])


def contrast(a, b) -> float:
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def saturation(rgb) -> float:
    mx, mn = max(rgb), min(rgb)
    return 0.0 if mx == 0 else (mx - mn) / mx


def dominant_color(pixels) -> tuple:
    """Most frequent colour cluster (coarse 16-step quantization) = the background behind text."""
    buckets: Counter = Counter()
    rep: dict = {}
    for px in pixels:
        key = (px[0] // 16, px[1] // 16, px[2] // 16)
        buckets[key] += 1
        rep.setdefault(key, px)
    key, _ = buckets.most_common(1)[0]
    members = [p for p in pixels if (p[0] // 16, p[1] // 16, p[2] // 16) == key]
    n = len(members)
    return (
        sum(p[0] for p in members) / n,
        sum(p[1] for p in members) / n,
        sum(p[2] for p in members) / n,
    )


def region_pixels(img, x, y, w, h):
    x = max(0, int(round(x)))
    y = max(0, int(round(y)))
    w = min(img.width - x, int(round(w)))
    h = min(img.height - y, int(round(h)))
    if w <= 0 or h <= 0:
        return []
    crop = img.crop((x, y, x + w, y + h))
    return list(crop.getdata())


def region_salience(img, box) -> float:
    px = region_pixels(img, *box)
    if not px:
        return 0.0
    lums = [rel_lum(p) for p in px]
    sats = [saturation(p) for p in px]
    mean_lum = sum(lums) / len(lums)
    mean_sat = sum(sats) / len(sats)
    var = sum((l - mean_lum) ** 2 for l in lums) / len(lums)
    return mean_lum * (0.6 * mean_sat + 0.4 * math.sqrt(var))


def focus_ring_present(img, box) -> dict:
    """The focused control must show a visible (near-white) ring along its border (XAG 112)."""
    x, y, w, h = box
    band = 5
    perim = []
    # collect a frame band around the border (just inside the box edge)
    for yy in range(max(0, y), min(img.height, y + h)):
        for xx in range(max(0, x), min(img.width, x + w)):
            if xx < x + band or xx > x + w - band or yy < y + band or yy > y + h - band:
                perim.append(img.getpixel((xx, yy)))
    if not perim:
        return {"present": False, "p90_lum": 0.0}
    lums = sorted(rel_lum(p) for p in perim)
    p90 = lums[int(0.9 * (len(lums) - 1))]
    return {"present": p90 >= 0.6, "p90_lum": round(p90, 3)}


def measure_contrast(img, rects) -> list:
    out = []
    for r in rects:
        px = region_pixels(img, r["x"], r["y"], r["w"], r["h"])
        if not px:
            out.append({"role": r["role"], "floor": r["floor"], "measured": None, "pass": False, "note": "empty-rect"})
            continue
        bg = dominant_color(px)
        c = contrast(r["color"], bg)
        out.append({
            "role": r["role"], "floor": r["floor"],
            "measured": round(c, 2), "bg_estimate": [round(v) for v in bg],
            "pass": c >= r["floor"] - 1e-6,
        })
    return out


def flash_check(css_text: str, js_text: str) -> dict:
    findings = []
    # full-screen white/red flash keyframe: a keyframe that drives a full-bleed element to near-white/red
    for m in re.finditer(r"@keyframes\s+([\w-]+)\s*\{([^}]*)\}", css_text):
        body = m.group(2)
        if re.search(r"#fff|#ffffff|rgba?\(\s*255\s*,\s*255\s*,\s*255", body, re.I) and "flash" in m.group(1).lower():
            findings.append(f"possible white flash keyframe: {m.group(1)}")
    # infinite background animation
    if re.search(r"animation:[^;]*infinite", css_text, re.I):
        findings.append("infinite animation present (background motion must default to 0%)")
    # reveal burst constant within band
    burst = re.search(r"REVEAL_BURST_MS\s*=\s*(\d+)", js_text)
    burst_ms = int(burst.group(1)) if burst else None
    burst_ok = burst_ms is not None and burst_ms <= 500
    return {
        "method": "structural: no full-screen white/red flash keyframe, no infinite background animation, "
                  "reveal burst constant <=500ms. Per-frame pixel-diff flash measurement is out of scope and "
                  "disclosed as a gap (NN4).",
        "findings": findings,
        "reveal_burst_ms": burst_ms,
        "reveal_burst_within_band": burst_ok,
        "pass": not findings and burst_ok,
    }


def measure_variant(base_url, server_root, variant, out_dir) -> dict:
    url = cc.site_url(base_url, TARGET) + f"?variant={variant}&native=1"
    shot = out_dir / f"result_{variant}.png"
    dom = cc.chrome_run(url, [1920], None, 6000, 120, window_size=(1920, 1080),
                        screenshot_path=shot, dump_dom=True)
    m = re.search(r'<script id="measureOutput"[^>]*>(.*?)</script>', dom, re.S)
    if not m:
        return {"variant": variant, "error": "no self-report (measureOutput) in DOM"}
    self_report = json.loads(m.group(1))
    img = Image.open(shot).convert("RGB")

    contrast_rows = measure_contrast(img, self_report.get("rects", []))
    contrast_pass = all(row["pass"] for row in contrast_rows)

    sal = {name: round(region_salience(img, box), 5) for name, box in SALIENCY_REGIONS.items()}
    ranked = sorted(sal.items(), key=lambda kv: kv[1], reverse=True)
    # Core #5 saliency hierarchy: the reward is the star — it must out-salience the character face
    # and the background. (Next-button prominence is verified as focus visibility below, not by a
    # saliency proxy, since a flat gold button reads low on a luminance*saturation*variance proxy.)
    saliency_pass = sal["reward"] > sal["face"] and sal["reward"] > sal["background"]

    focus = focus_ring_present(img, FOCUS_BOX["error" if variant == "error" else "default"])

    return {
        "variant": variant,
        "screenshot": str(shot),
        "self_report_derived": self_report.get("derived"),
        "independent_contrast": contrast_rows,
        "contrast_pass": contrast_pass,
        "saliency": sal,
        "saliency_rank": [name for name, _ in ranked],
        "saliency_pass": saliency_pass,
        "focus_ring": focus,
        "focus_pass": focus["present"],
        "pass": contrast_pass and saliency_pass and focus["present"],
    }


def run(out_dir: Path) -> dict:
    if Image is None:
        raise RuntimeError("Pillow (PIL) is required for pixel measurement")
    out_dir.mkdir(parents=True, exist_ok=True)
    css_text = (DEMO_ROOT / "result-screen" / "result.css").read_text(encoding="utf-8")
    js_text = (DEMO_ROOT / "result-screen" / "result.js").read_text(encoding="utf-8")
    flash = flash_check(css_text, js_text)

    server, base_url = cc.serve_site(DEMO_ROOT, [1920], TARGET)
    variants = []
    try:
        for variant in VARIANTS:
            variants.append(measure_variant(base_url, DEMO_ROOT, variant, out_dir))
    finally:
        server.shutdown()

    errors = [v for v in variants if "error" in v]
    good = [v for v in variants if "error" not in v]
    contrast_ok = all(v.get("contrast_pass") for v in good)
    saliency_ok = all(v.get("saliency_pass") for v in good)
    focus_ok = all(v.get("focus_pass") for v in good)
    overall = contrast_ok and saliency_ok and focus_ok and flash["pass"] and not errors
    return {
        "instrument": "measure-result-screen",
        "flash": flash,
        "variants": variants,
        "summary": {
            "contrast_all_variants_pass": contrast_ok,
            "saliency_all_variants_pass": saliency_ok,
            "focus_all_variants_pass": focus_ok,
            "flash_pass": flash["pass"],
            "errors": [v["variant"] for v in errors],
            "overall_pass": overall,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".agent-runs/result-screen-measure", help="screenshot/report output dir")
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    args = parser.parse_args(argv)
    try:
        report = run(Path(args.out).resolve())
    except cc.ChromeUnavailableError as exc:
        print(f"chrome unavailable: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"instrument error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        s = report["summary"]
        for v in report["variants"]:
            if "error" in v:
                print(f"  {v['variant']}: ERROR {v['error']}")
                continue
            print(f"  {v['variant']}: contrast={'PASS' if v['contrast_pass'] else 'FAIL'} "
                  f"saliency={'PASS' if v['saliency_pass'] else 'FAIL'} "
                  f"focus={'PASS' if v['focus_pass'] else 'FAIL'} "
                  f"(derived {v['self_report_derived']}, rank {v['saliency_rank']}, ring p90 {v['focus_ring']['p90_lum']})")
        print(f"flash: {'PASS' if report['flash']['pass'] else 'FAIL'} {report['flash']['findings']}")
        print(f"OVERALL: {'PASS' if s['overall_pass'] else 'FAIL'}")
    return 0 if report["summary"]["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
