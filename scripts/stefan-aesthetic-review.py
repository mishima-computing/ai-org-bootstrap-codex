#!/usr/bin/env python3
"""Stefan — the aesthetic reviewer (design counterpart to Linon).

Linon judges CODE (NN1-4 + RED tests). Stefan judges DESIGN, on the rendered pixels, using
established, paper-grounded aesthetic measures computed by MIT-licensed libraries:

  * AIM (Aalto Interface Metrics, MIT) — UI clutter / contour / figure-ground contrast.
  * Aesthetics-Toolbox (MIT, Bartho et al.) — balance, symmetry, RMS contrast, colour/luminance
    entropy, edge-orientation entropy, PHOG self-similarity/complexity/anisotropy.
  * visual-clutter (MIT, Aalto / Rosenholtz et al.) — feature congestion / subband entropy (optional).

Stefan is NOT taste. A computed metric is a CORRELATE of beauty, validated against human ratings;
the owner taste-gate remains final (issue #66: raw clutter/complexity did not reproduce the owner's
A/B preference). Stefan therefore reports per-axis, directional reads where research gives a
direction, flags concerning bands as evidence-based critique, and defers the verdict to the owner.

License posture (owner ruling 2026-06-14): use the MIT libraries directly, with attribution
(THIRD_PARTY_NOTICES). No AVA-trained weights, no LGPL (OCTA), no unlicensed code. Own
re-implementation is a later option ("独自実装はそのうち").

Usage:
  stefan-aesthetic-review.py score  <image.png>
  stefan-aesthetic-review.py compare <a.png> <b.png> [--labels A,B]
Env: STEFAN_AESTHETIC_REPOS = path to the aesthetic-repos checkout (AIM + Aesthetics-Toolbox).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

DEFAULT_REPOS = "/Users/terum/Documents/Codex/2026-06-14/new-chat/work/aesthetic-repos"


def _setup_paths():
    repos = Path(os.environ.get("STEFAN_AESTHETIC_REPOS", DEFAULT_REPOS))
    sys.path.insert(0, str(repos / "aim" / "aim2_metrics"))
    sys.path.insert(0, str(repos / "Aesthetics-Toolbox"))
    return repos


# Research-directional axes: where the literature gives a "more aesthetic" direction.
# (Non-directional measures — clutter, complexity, anisotropy, edge density — are reported as
#  context, not scored, because their good direction depends on genre. See #66.)
DIRECTIONAL = {
    "balance":            ("higher", "compositional balance (Wilson & Chatterjee)"),
    "mirror_symmetry":    ("higher", "reflective symmetry (a robust beauty correlate)"),
    "rms_contrast":       ("higher", "luminance contrast / striking-ness (to a point)"),
    "figure_ground":      ("higher", "clarity of UI hierarchy (AIM m5)"),
    "palette_cohesion":   ("higher", "fewer competing colours = more cohesive"),
    "phog_self_sim":      ("higher", "PHOG self-similarity (fractal aesthetics in art)"),
}


def _metrics(image: str) -> dict:
    import numpy as np
    from PIL import Image
    from skimage import color

    out: dict = {}

    # ---- AIM (MIT) ----
    img_b64 = base64.b64encode(Path(image).read_bytes()).decode()
    for key, mod in [
        ("aim_distinct_rgb", "aim.metrics.m3_distinct_rgb_values"),
        ("aim_contour_density", "aim.metrics.m4_contour_density"),
        ("figure_ground", "aim.metrics.m5_figure_ground_contrast"),
        ("aim_contour_congestion", "aim.metrics.m6_contour_congestion"),
    ]:
        try:
            metric = __import__(mod, fromlist=["Metric"]).Metric
            res = metric.execute_metric(img_b64, 0)
            out[key] = float(res[0]) if res else None
        except Exception as exc:  # noqa: BLE001
            out[key] = None
            out.setdefault("_errors", []).append(f"{key}: {type(exc).__name__}: {str(exc)[:70]}")

    # ---- Aesthetics-Toolbox (MIT) ----
    try:
        from AT import (color_and_simple_qips as C, edge_entropy_qips as E,
                        balance_qips as B, PHOG_qips as P)
        pil = Image.open(image)
        rgb = np.asarray(pil.convert("RGB"))
        gray = np.asarray(pil.convert("L"))
        lab = color.rgb2lab(rgb)
        hsv = color.rgb2hsv(rgb)
        out["rms_contrast"] = float(C.std_channels(lab)[0])
        out["color_entropy"] = float(C.shannonentropy_channels(hsv[:, :, 0]))
        out["luminance_entropy"] = float(C.shannonentropy_channels(lab[:, :, 0]))
        try:
            a, b, d = E.do_first_and_second_order_entropy_and_edge_density(gray)
            out["edge_density"] = float(d)
            out["edge_orientation_entropy"] = float(a)
        except Exception:  # noqa: BLE001
            pass
        out["balance"] = float(B.Balance(gray))
        out["mirror_symmetry"] = float(B.Mirror_symmetry(gray))
        out["homogeneity"] = float(B.Homogeneity(gray))
        try:
            dcm_dist, dcm_x, dcm_y = B.DCM(gray)
            out["dcm_distance"] = float(dcm_dist)
            out["dcm_x"] = float(dcm_x)  # +/- = visual weight right/left of centre
            out["dcm_y"] = float(dcm_y)  # +/- = visual weight below/above centre
        except Exception:  # noqa: BLE001
            pass
        try:
            s, c, an = P.PHOGfromImage(rgb)
            out["phog_self_sim"] = float(s)
            out["phog_complexity"] = float(c)
            out["phog_anisotropy"] = float(an)
        except Exception:  # noqa: BLE001
            pass
        # derived: palette cohesion = inverse of distinct colours (normalised, higher = cohesive)
        if out.get("aim_distinct_rgb"):
            out["palette_cohesion"] = round(1.0 / (1.0 + out["aim_distinct_rgb"] / 10000.0), 4)
    except Exception as exc:  # noqa: BLE001
        out.setdefault("_errors", []).append(f"toolbox: {type(exc).__name__}: {str(exc)[:70]}")

    # ---- visual-clutter (MIT, optional) ----
    try:
        from visual_clutter import Vlc
        v = Vlc(image)
        out["clutter_feature_congestion"] = float(v.getClutter_FC())
    except Exception:  # noqa: BLE001
        pass  # optional layer; absence disclosed in the report

    return out


def _flags(m: dict) -> list:
    """Evidence-based aesthetic critique: concerning bands on known axes (advisory)."""
    flags = []
    if m.get("figure_ground") is not None and m["figure_ground"] < 0.3:
        flags.append(f"weak figure-ground hierarchy (AIM m5={m['figure_ground']:.2f} < 0.30): UI may not pop from background")
    if m.get("aim_distinct_rgb") is not None and m["aim_distinct_rgb"] > 60000:
        flags.append(f"chaotic palette (distinct RGB {int(m['aim_distinct_rgb'])} > 60000): colours competing")
    if m.get("aim_contour_congestion") is not None and m["aim_contour_congestion"] > 0.55:
        flags.append(f"high contour congestion ({m['aim_contour_congestion']:.2f} > 0.55): cramped edges")
    if m.get("rms_contrast") is not None and m["rms_contrast"] < 8:
        flags.append(f"low overall contrast (RMS {m['rms_contrast']:.1f} < 8): flat / washed out")
    if m.get("balance") is not None and m["balance"] < 20:
        flags.append(f"low compositional balance ({m['balance']:.1f} < 20): visually lopsided")
    return flags


def cmd_score(args) -> int:
    _setup_paths()
    m = _metrics(args.image)
    print(f"=== Stefan aesthetic read: {args.image} ===")
    for k in sorted(m):
        if k.startswith("_"):
            continue
        print(f"  {k:26} {m[k]}")
    flags = _flags(m)
    print("--- flags (advisory; owner taste-gate is final) ---")
    print("\n".join("  ⚑ " + f for f in flags) if flags else "  (none)")
    if m.get("_errors"):
        print("--- instrument notes ---")
        for e in m["_errors"]:
            print("  ! " + e)
    if args.json:
        print(json.dumps({"image": args.image, "metrics": m, "flags": flags}, indent=2, ensure_ascii=False))
    return 0


def cmd_compare(args) -> int:
    _setup_paths()
    labels = (args.labels or "A,B").split(",")
    la, lb = labels[0], labels[1] if len(labels) > 1 else "B"
    ma, mb = _metrics(args.a), _metrics(args.b)

    print(f"=== Stefan A/B aesthetic comparison ===\n  {la}: {args.a}\n  {lb}: {args.b}\n")
    print(f"{'directional axis':22} {la:>12} {lb:>12}  winner   (meaning)")
    wins = {la: 0, lb: 0}
    for axis, (direction, meaning) in DIRECTIONAL.items():
        va, vb = ma.get(axis), mb.get(axis)
        if va is None or vb is None:
            print(f"{axis:22} {'n/a':>12} {'n/a':>12}  -")
            continue
        better = la if (va > vb) == (direction == "higher") else lb
        if abs(va - vb) / (abs(va) + abs(vb) + 1e-9) < 0.01:
            better = "tie"
        else:
            wins[better] += 1
        print(f"{axis:22} {va:>12.3f} {vb:>12.3f}  {better:8} ({meaning})")

    lean = max(wins, key=wins.get) if wins[la] != wins[lb] else "tie"
    print(f"\n  directional tally: {la} {wins[la]} — {lb} {wins[lb]}  →  aesthetic lean: {lean}")
    print("\n  non-directional (context, not scored — genre decides; see #66):")
    for axis in ["aim_contour_congestion", "phog_complexity", "edge_density", "clutter_feature_congestion"]:
        va, vb = ma.get(axis), mb.get(axis)
        if va is not None and vb is not None:
            print(f"    {axis:26} {la}={va:.3f}  {lb}={vb:.3f}")
    print("\n  flags:")
    for lab, mm in [(la, ma), (lb, mb)]:
        fl = _flags(mm)
        print(f"    {lab}: " + ("; ".join(fl) if fl else "none"))
    print("\n  VERDICT: directional lean only. The owner taste-gate is final (Stefan is a calibrated")
    print("  correlate of beauty, not taste itself). #66: raw clutter/complexity do not decide.")
    if args.json:
        print(json.dumps({la: ma, lb: mb, "wins": wins, "lean": lean}, indent=2, ensure_ascii=False))
    return 0


# Per-axis fix direction — what the implementer should change to close the shortfall.
FIX_DIRECTION = {
    "balance": "rebalance the composition; the visual weight is off-centre {where}. Move/glow/dim mass toward the empty side, or recentre the focal element.",
    "mirror_symmetry": "tighten symmetry around the focal axis: align framing/halo/rays so left and right read as mirrored.",
    "rms_contrast": "raise overall contrast: deepen the darks (vignette/bg-dim) and brighten the focal bloom so the image is not flat.",
    "figure_ground": "strengthen figure-ground separation: darken directly behind text/UI and/or add a rim/halo so the subject pops from the background.",
    "palette_cohesion": "cut competing colours: reduce the hue count toward a dominant accent + neutrals; the palette is fragmented.",
    "phog_self_sim": "add structured, self-similar detail (consistent ornament/particles/framing) so the image is not visually barren.",
}


def cmd_review(args) -> int:
    """Linon-style adversarial aesthetic review: located, measured findings vs an exemplar,
    with severity and a fix direction, and a verdict that drives re-implementation."""
    _setup_paths()
    cand = _metrics(args.candidate)
    exe = _metrics(args.exemplar)

    def where(m):
        x, y = m.get("dcm_x", 0.0), m.get("dcm_y", 0.0)
        h = "right" if x > 1 else "left" if x < -1 else ""
        v = "low" if y > 1 else "high" if y < -1 else ""
        return (" ".join(p for p in [v, h] if p) + " of centre") if (h or v) else "near centre"

    findings = []
    for axis, (direction, meaning) in DIRECTIONAL.items():
        c, e = cand.get(axis), exe.get(axis)
        if c is None or e is None or e == 0:
            continue
        ratio = c / e if direction == "higher" else e / c
        if ratio >= 0.95:
            continue  # meets the exemplar bar on this axis
        sev = "critical" if ratio < 0.70 else "major" if ratio < 0.85 else "minor"
        fix = FIX_DIRECTION.get(axis, "").format(where=where(cand))
        findings.append({
            "axis": axis, "severity": sev, "meaning": meaning,
            "candidate": round(c, 3), "exemplar": round(e, 3), "ratio": round(ratio, 3),
            "claim": f"{axis} {c:.2f} is {(1-ratio)*100:.0f}% below the exemplar {e:.2f}",
            "fix": fix,
        })
    # absolute-band findings (catch flat/washed/chaotic regardless of exemplar)
    for f in _flags(cand):
        findings.append({"axis": "band", "severity": "major", "claim": f, "fix": "see claim"})

    order = {"critical": 0, "major": 1, "minor": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    n_crit = sum(1 for f in findings if f["severity"] == "critical")
    n_major = sum(1 for f in findings if f["severity"] == "major")
    verdict = "REWORK" if (n_crit >= 1 or n_major >= 2) else "PASS-subject-to-owner"

    print(f"=== Stefan aesthetic review (vs exemplar) ===")
    print(f"  candidate: {args.candidate}")
    print(f"  exemplar:  {args.exemplar}\n")
    if not findings:
        print("  no shortfalls vs exemplar. (Owner taste-gate is still final.)")
    for f in findings:
        print(f"  [{f['severity'].upper()}] {f['claim']}")
        if f.get("fix"):
            print(f"        → fix: {f['fix']}")
    print(f"\n  VERDICT: {verdict}  ({n_crit} critical, {n_major} major)")
    print("  Stefan returns design feedback to drive re-implementation; the owner taste-gate is final.")
    if args.json:
        print(json.dumps({"candidate": args.candidate, "exemplar": args.exemplar,
                          "findings": findings, "verdict": verdict}, indent=2, ensure_ascii=False))
    return 0 if verdict.startswith("PASS") else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score"); s.add_argument("image"); s.add_argument("--json", action="store_true")
    c = sub.add_parser("compare"); c.add_argument("a"); c.add_argument("b")
    c.add_argument("--labels"); c.add_argument("--json", action="store_true")
    r = sub.add_parser("review"); r.add_argument("candidate"); r.add_argument("--exemplar", required=True)
    r.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "score":
        return cmd_score(args)
    if args.cmd == "compare":
        return cmd_compare(args)
    return cmd_review(args)


if __name__ == "__main__":
    raise SystemExit(main())
