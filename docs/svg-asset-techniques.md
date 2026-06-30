# High-fidelity SVG asset techniques (for codex-generated game assets)

Purpose: when the AI Org generates SVG assets, feed this reference to codex so output reads as
crafted, shaded, "PS2-era 2D" game art — NOT flat childish clip-art. SVG can do rich 2D (gradients,
lighting, procedural texture, soft/contact shadows); it is NOT a 3D renderer (true 3D = WebGL/canvas).

## 0. THE GATE — require a PLAN before drawing (most important)
Before emitting any SVG, codex must first write this plan; if any item is missing the result reads as flat:
`silhouette` · `view/camera` · `palette (with an explicit light→dark VALUE LADDER)` · `light direction`
· `2–3 shading bands (cel) OR soft gradient shading` · `AO locations (crevices)` · `cast + contact shadows`
· `texture overlay` · `focal detail zones (where to spend detail)` · `final color-grade`.

## 1. Art direction (separates pro from amateur)
- **Value ladder**: pick a palette ordered light→dark; use VALUE (not just hue) to build form/volume.
- **Cohesive limited palette** + 1 accent; consistent **light direction** across all elements.
- **Shading**: cel = 2–3 hard value bands; or soft = radial/linear gradients. Add **rim/specular highlights**.
- **AO** in crevices (darken where forms meet); **cast shadow** (on ground) + **contact shadow** (tight, dark).
- **Silhouette readability** first; spend detail only in focal zones (face, hands), keep the rest simple.
- Benchmarks for stylized-2D fidelity: **Jet Set Radio** (cel, thick lines, controlled bright color),
  **Okami** (sumi-e/cel, painterly texture), **Ghost Trick** (bold color, hard shadows, strong silhouettes).

## 1a. View / composition (default three-quarter)
- Default to **three-quarter** (3/4, angled side) composition unless a caller explicitly requests another view.
- For three-quarter view, construct parts in a 3/4 projection with mild foreshortening, consistent ground plane,
  and light from upper-left.
- Show side faces, overlaps, cast/contact shadows, and depth ordering so the asset reads as an object in space.
- Do **not** flatten into a top-down map unless the requested view explicitly says top-down, overhead, map, or plan view.

## 1b. Faces (dedicated canon)
- Faces are special: humans are hypersensitive to small facial errors, so do not freehand them.
- Treat the face/head as the **focal detail zone** and spend precision there.
- Build the head on a construction frame, Loomis-style: cranial sphere plus face plane, with a clear vertical center line.
- Mirror all facial features across that center line using computed ratios, transforms, and/or `<use>` elements.
- Put the eye-line at the vertical midline of the head. Space eyes about one eye-width apart.
- Place brow, nose, and mouth by facial thirds. For creatures, place the eye cluster and chelicerae/mandibles by
  the same symmetric, ratio-driven rules.
- Size and place every facial feature by explicit ratios relative to head width/height; small asymmetry or
  misplacement makes a face look wrong.

## 2. Rendering techniques (the levers) — name → effect → snippet
**Procedural texture (feTurbulence)** — skin/rust/fabric/cloud/paper. Tint via feColorMatrix/feComponentTransfer.
```svg
<filter id="rough"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" result="n"/>
  <feColorMatrix in="n" type="matrix" values="0 0 0 0 .4  0 0 0 0 .3  0 0 0 0 .2  0 0 0 .25 0"/></filter>
<rect ... filter="url(#rough)" style="mix-blend-mode:multiply"/>
```
**3D-like shading (feDiffuse/feSpecularLighting + light source)** — volume, highlights, emboss.
```svg
<filter id="lit"><feGaussianBlur stdDeviation="2" result="b"/>
  <feSpecularLighting in="b" surfaceScale="4" specularConstant=".9" specularExponent="18" lighting-color="#fff">
    <fePointLight x="60" y="40" z="80"/></feSpecularLighting></filter>
```
**Organic distortion (feDisplacementMap + turbulence)** — warp stiff shapes (flames, water, fur edges).
**Soft / contact / inner shadows** — feDropShadow (cheap), or feOffset+feFlood+feComposite (controlled). For
**ambient occlusion**, prefer a hand-drawn dark shape over random blur (see "cut-in shadow").
**Gradients done well** — multi-stop linear/radial; fake a **gradient mesh** by stacking several radial
gradients; place a small bright **specular hotspot** radial near the light.
**Patterns** (`<pattern>`) — tiled cloth/stone/scales, low opacity + `mix-blend-mode:overlay`.
**Blend layers** — `mix-blend-mode`/feBlend like Photoshop: **multiply** for dirt/shadow, **screen** for glow,
**overlay/soft-light** for texture. Stack 2–3 thin layers for material richness.
**Path craft** — prefer cubic-bezier `C` curves over many straight `L` segments for organic bodies/hair/rocks.
**Layered-stroke variable width** — draw a thick dark stroke, then a thinner light stroke on top (inked feel);
`stroke-linecap="round"` for painted strokes.
**Cut-in shadow SHAPE** — draw form shadows as REAL shapes clipped to the body (`clip-path` + `multiply`),
not as random blur. This is the single biggest "looks crafted" upgrade.

## 3. Per-asset recipes
- **Character**: silhouette path → flat base colors → 2–3 cel shadow shapes (cut-in) → rim highlight →
  small specular hotspots → subtle turbulence overlay (multiply) → contact shadow under feet.
- **Background**: gradient sky/backdrop → layered parallax shapes (lighter+hazier toward back) → texture
  overlay (turbulence/pattern, low opacity) → vignette (radial dark, multiply) → color-grade.
- **Prop**: base shape (cubic) → material gradient → AO where it meets ground → specular edge → texture.
- **UI**: crisp shapes, 1–2px strokes, soft drop shadow for depth, restrained gradients, consistent corner radius.

## 4. Cute / appeal style (Flash-era vector)
Use this instead of the painterly stack when `constructive_svg(..., style="cute")` is requested. It should read as
original, clean Flash/Animate-era vector-cartoon craft: flat, deliberate, appealing, and not filter-heavy.

- **Construction**: closed vector shapes, flat fills, and consistent strokes with rounded joins/caps. Use `<defs>` +
  `<use>` for repeated parts such as eyes, highlights, limbs, and paired details. Group semantic parts (`head`,
  `face`, `body`, `arms`, `legs`). Keep the shape count economical: roughly 12-40 deliberate shapes.
- **Shape language**: pick one dominant family. Round/bean shapes are the default for cute/friendly appeal;
  rounded-square shapes feel sturdy; teardrops feel energetic or magical. Use sharp angles only as tiny rounded
  accents. The silhouette must stay readable at about 64px.
- **Cute proportions**: normalize height to 100. Head is 42-55, body is 32-42, neck is minimal or hidden, and legs
  are short, thick, and rounded. Eyes are large: 18-28% of head height. Nose and mouth stay small and low.
- **Eye system**: each eye is layered: outer shape, iris, pupil, large white highlight, tiny secondary highlight,
  and optional lid. Keep iris and pupil large. Drive expression through brows, lids, and mouth, not by changing head
  anatomy. Default expression is warm: open eyes and a small smile.
- **Palette**: one dominant color, one accent or hair color, one small complementary pop, and a dark hue-shifted
  outline color, never pure black. Keep colors saturated but harmonious. Use flat cel shadows only: 0-2 shapes,
  10-20% darker than the base. Avoid gradients except for a subtle iris gradient when useful. Do not use painterly
  texture, noisy overlays, or heavy filters.
- **Face focus**: the face is the focal zone. Keep it balanced and symmetric, and use the face canon for ratio-driven
  placement whenever the asset has facial features.

## Sources
MDN (feTurbulence / feDisplacementMap / feSpecularLighting / feBlend / mix-blend-mode / path / stroke-linecap);
Sara Soueidan SVG filters series (codrops/sarasoueidan.com); Smashing Magazine "Art of SVG Filters" &
displacement filtering (Dirk Weber); SVG Wow! (Dahlström/Hardy); Ghostscript Tiger (showcase); CodePen
lbebber Gooey Menu (blur+color-matrix+blend pipeline). Game art-direction refs: Jet Set Radio, Okami, Ghost Trick.
