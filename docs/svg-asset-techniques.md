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

## Sources
MDN (feTurbulence / feDisplacementMap / feSpecularLighting / feBlend / mix-blend-mode / path / stroke-linecap);
Sara Soueidan SVG filters series (codrops/sarasoueidan.com); Smashing Magazine "Art of SVG Filters" &
displacement filtering (Dirk Weber); SVG Wow! (Dahlström/Hardy); Ghostscript Tiger (showcase); CodePen
lbebber Gooey Menu (blur+color-matrix+blend pipeline). Game art-direction refs: Jet Set Radio, Okami, Ghost Trick.
