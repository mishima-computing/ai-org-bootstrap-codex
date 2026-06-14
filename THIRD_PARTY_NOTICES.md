# Third-Party Notices

Stefan's aesthetic-review instrument (`scripts/stefan-aesthetic-review.py`) computes its visual
features using the following third-party, MIT-licensed projects. They are invoked as external
libraries (configured via `STEFAN_AESTHETIC_REPOS` / pip); their source is not vendored into this
pack. Their copyright and MIT permission notices are reproduced by reference below.

Owner ruling (2026-06-14): use MIT-licensed libraries directly, with attribution. No AVA-trained
weights (research-encumbered), no LGPL (OCTA toolbox), no unlicensed code (computational-aesthetics)
are used — keeping the commercial posture clean for downstream (Shagiri).

## AIM — Aalto Interface Metrics
- License: MIT. Copyright (c) User Interfaces group, Aalto University, Finland.
- Use: UI clutter / contour density / figure-ground contrast metrics (m3–m6).
- Source: https://github.com/aalto-ui/aim

## Aesthetics-Toolbox
- License: MIT.
- Use: Quantitative Image Properties — balance, mirror symmetry, RMS contrast, colour/luminance
  entropy, edge-orientation entropy, PHOG self-similarity/complexity/anisotropy. (Training-free QIPs
  only; the optional AlexNet-conv1 CNN QIPs are not required and are not used.)
- Source: https://github.com/RBartho/Aesthetics-Toolbox

## visual-clutter
- License: MIT. Copyright (c) User Interfaces group, Aalto University, Finland.
- Use: optional feature-congestion / subband-entropy clutter measures (Rosenholtz et al.).
- Source: https://github.com/aalto-ui (visual-clutter)

The full MIT license text applies to each: permission is granted, free of charge, to use, copy,
modify, merge, publish, distribute, sublicense, and/or sell copies, provided the copyright notice
and this permission notice are included. Software is provided "as is", without warranty.
