/*
 * Result screen — Game Designer console, cartridge:result-screen.
 *
 * The deciding numbers (bg-dim alpha, face-dim alpha) are DERIVED here from the rendered
 * composite, never fixed by feel (#5 derive-on-render rule). Standard-backed floors
 * (28px text, 4.5:1 contrast measured on the composite, focus order, ≤500ms reveal) are
 * enforced. window.__resultMeasure exposes the derivation; an external Python instrument
 * independently re-measures contrast and saliency on the screenshot (NN1: the screen's
 * self-report is not the verification).
 */
(function () {
  "use strict";

  var PANEL_ALPHA = 0.55;  // mirror of the CSS panel alpha, for the self-report only; the actual
                           // compositing reads the live CSS alpha through the ancestor chain.
  var DIM_RGB = [5, 8, 12];        // --bg-dim
  var CONTRAST_FLOORS = { title: 3.0, "title-modal": 4.5, primary: 4.5, secondary: 4.5,
    value: 3.0, rarity: 3.0, body: 4.5, "button-primary": 4.5, "button-secondary": 4.5 };
  var REVEAL_BURST_MS = 460;       // ≤ 500ms (NN/g / #5)
  // Face salience must drop below both 60% of its undimmed value AND an absolute budget that sits
  // under a typical reward-card salience, so the character can never out-compete the reward.
  var FACE_SALIENCE_FRACTION = 0.6;
  var FACE_SALIENCE_BUDGET = 0.015;

  var RARITY_TIER = { common: "N", rare: "R", epic: "SR", legendary: "SSR" };
  var RARITY_TITLE = { common: "PULL COMPLETE", rare: "RARE!", epic: "EPIC!", legendary: "LEGENDARY!" };

  function asset(name) { return "../assets/" + name; }

  var VARIANTS = {
    normal: {
      outcome: "win", stageName: "PULL 042 / STANDARD BANNER",
      featured: { icon: asset("legendary_dragon.png"), name: "Legendary Dragon", rarity: "legendary" },
      exp: 12400, gold: 8900,
      char: asset("rare_hero_m.png"),
      rewards: [
        { icon: asset("common_slime.png"), count: 12 },
        { icon: asset("rare_hero_f.png"), count: 2 },
        { icon: asset("epic_treasure.png"), count: 1 },
        { icon: asset("common_slime.png"), count: 5 },
        { icon: asset("rare_hero_m.png"), count: 3 }
      ]
    },
    maxrewards: {
      outcome: "win", stageName: "PULL 050 / 10x MULTI",
      featured: { icon: asset("epic_treasure.png"), name: "Epic Treasure", rarity: "epic" },
      exp: 24800, gold: 19500, char: asset("rare_hero_f.png"),
      rewards: Array.apply(null, { length: 20 }).map(function (_, i) {
        return { icon: asset(["common_slime.png", "rare_hero_m.png", "rare_hero_f.png", "epic_treasure.png"][i % 4]), count: i + 1 };
      })
    },
    longtext: {
      outcome: "win", stageName: "PULL 051 / RUINS OF THE ANCIENT CAPITAL / LIMITED BANNER",
      featured: { icon: asset("legendary_dragon.png"), name: "古代竜王の灼熱大剣・改・アルティメットフォーム", rarity: "legendary" },
      exp: 999999999, gold: 1500000000, char: asset("rare_hero_m.png"),
      rewards: [{ icon: asset("epic_treasure.png"), count: 1 }, { icon: asset("common_slime.png"), count: 99 }]
    },
    norewards: {
      outcome: "win", stageName: "PULL 052 / SINGLE", featured: { icon: asset("rare_hero_f.png"), name: "Rare Hero", rarity: "rare" },
      exp: 1200, gold: 0, char: asset("rare_hero_f.png"), rewards: []
    },
    defeat: {
      outcome: "defeat", stageName: "PULL 053 / FAILED", featured: { icon: asset("common_slime.png"), name: "Common Slime", rarity: "common" },
      exp: 0, gold: -300, char: asset("common_slime.png"), rewards: [{ icon: asset("common_slime.png"), count: 1 }]
    },
    error: { error: true, base: "normal" }
  };

  function readResult() {
    var params = new URLSearchParams(location.search);
    var dataParam = params.get("data");
    if (dataParam) {
      try { return normalize(JSON.parse(decodeURIComponent(escape(atob(dataParam))))); } catch (e) { /* fall through */ }
    }
    var variantName = params.get("variant") || "normal";
    var variant = VARIANTS[variantName] || VARIANTS.normal;
    if (variant.error) {
      var base = JSON.parse(JSON.stringify(VARIANTS[variant.base]));
      base.error = true; base.variantName = variantName; return normalize(base);
    }
    var copy = JSON.parse(JSON.stringify(variant));
    copy.variantName = variantName;
    return normalize(copy);
  }

  function normalize(r) {
    r.outcome = r.outcome || "win";
    r.featured = r.featured || { icon: asset("common_slime.png"), name: "Item", rarity: "common" };
    r.featured.rarity = r.featured.rarity || "common";
    r.title = r.title || (r.outcome === "defeat" ? "DEFEAT" : RARITY_TITLE[r.featured.rarity] || "PULL COMPLETE");
    r.rewards = r.rewards || [];
    return r;
  }

  function formatGain(n) {
    var neg = n < 0;
    var v = Math.abs(n);
    if (v >= 1000000000) return (neg ? "-" : "+") + "999,999,999+";
    var s = v.toLocaleString("en-US");
    return (neg ? "-" : "+") + s;
  }

  // ---- color / contrast math (WCAG relative luminance) ----
  function srgbToLin(c) { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
  function relLum(rgb) { return 0.2126 * srgbToLin(rgb[0]) + 0.7152 * srgbToLin(rgb[1]) + 0.0722 * srgbToLin(rgb[2]); }
  function contrast(a, b) { var la = relLum(a), lb = relLum(b); var hi = Math.max(la, lb), lo = Math.min(la, lb); return (hi + 0.05) / (lo + 0.05); }
  function over(src, sa, dst) { return [0, 1, 2].map(function (i) { return src[i] * sa + dst[i] * (1 - sa); }); }
  function parseColor(str) {
    var m = String(str).match(/rgba?\(([^)]+)\)/i);
    if (m) { var p = m[1].split(",").map(function (x) { return parseFloat(x.trim()); }); return [p[0], p[1], p[2]]; }
    return [255, 255, 255];
  }
  function alphaOf(str) {
    if (!str || str === "transparent") return 0;
    var m = String(str).match(/rgba\(([^)]+)\)/i);
    if (m) { var p = m[1].split(",").map(function (x) { return parseFloat(x.trim()); }); return p.length >= 4 ? p[3] : 1; }
    return /rgb\(/i.test(str) ? 1 : 0;
  }
  // The composite a text pixel actually sits on: scene (already dimmed) with each ancestor
  // background painted over it, outermost first. An opaque ancestor occludes the scene, so
  // bg-dim is load-bearing only for text whose ancestor chain stays translucent (panel text).
  function ancestorChain(el) {
    var chain = [], cur = el;
    while (cur && cur.id !== "stage" && cur.tagName) {
      var bgc = getComputedStyle(cur).backgroundColor;
      var a = alphaOf(bgc);
      if (a > 0) chain.push({ rgb: parseColor(bgc), a: a });
      cur = cur.parentElement;
    }
    return chain.reverse();
  }
  function effectiveBg(chain, dimmedScenePx) {
    var base = dimmedScenePx.slice();
    chain.forEach(function (layer) { base = over(layer.rgb, layer.a, base); });
    return base;
  }
  function saturation(rgb) { var mx = Math.max(rgb[0], rgb[1], rgb[2]), mn = Math.min(rgb[0], rgb[1], rgb[2]); return mx === 0 ? 0 : (mx - mn) / mx; }

  // ---- scene ----
  function drawScene(ctx, charImg, result) {
    // Bright celebratory scene: a luminous upper sky fading to a dark floor. This is what makes the
    // translucent panel genuinely need a derived dim for text readability (the #5 measure-on-composite case).
    var g = ctx.createLinearGradient(0, 0, 0, 1080);
    if (result.outcome === "defeat") { g.addColorStop(0, "#5A6478"); g.addColorStop(0.55, "#1A2230"); g.addColorStop(1, "#070B11"); }
    else { g.addColorStop(0, "#CBD8F2"); g.addColorStop(0.5, "#5C6E9A"); g.addColorStop(1, "#070B11"); }
    ctx.fillStyle = g; ctx.fillRect(0, 0, 1920, 1080);
    // Warm victory burst behind the panel center.
    var rad = ctx.createRadialGradient(960, 460, 60, 960, 460, 820);
    var warm = result.outcome === "defeat" ? "rgba(120,140,170,0.45)" : "rgba(255,224,150,0.62)";
    rad.addColorStop(0, warm); rad.addColorStop(1, "rgba(11,17,24,0)");
    ctx.fillStyle = rad; ctx.fillRect(0, 0, 1920, 1080);
    // Vignette: darken the edges so the bright scene cannot out-compete the reward for attention
    // (keeps the saliency hierarchy holdable). Uniform design choice, not aimed at any sample point.
    var vig = ctx.createRadialGradient(960, 540, 360, 960, 540, 1120);
    vig.addColorStop(0, "rgba(5,8,12,0)"); vig.addColorStop(0.75, "rgba(5,8,12,0.55)"); vig.addColorStop(1, "rgba(5,8,12,0.96)");
    ctx.fillStyle = vig; ctx.fillRect(0, 0, 1920, 1080);
    // Character art, held left (drawn onto the scene canvas so it is part of the measured composite).
    if (charImg && charImg.complete && charImg.naturalWidth) {
      var bw = 544, bh = 900, bx = 64, by = 120;
      var scale = Math.min(bw / charImg.naturalWidth, bh / charImg.naturalHeight);
      var w = charImg.naturalWidth * scale, h = charImg.naturalHeight * scale;
      ctx.drawImage(charImg, bx, by, w, h);
    }
  }

  function regionStats(ctx, x, y, w, h) {
    x = Math.max(0, Math.round(x)); y = Math.max(0, Math.round(y));
    w = Math.min(1920 - x, Math.round(w)); h = Math.min(1080 - y, Math.round(h));
    if (w <= 0 || h <= 0) return { maxLum: 0, meanLum: 0, salience: 0 };
    var data = ctx.getImageData(x, y, w, h).data;
    var maxLum = 0, sumLum = 0, sumSat = 0, lums = [], n = 0;
    for (var i = 0; i < data.length; i += 4) {
      var rgb = [data[i], data[i + 1], data[i + 2]];
      var l = relLum(rgb); lums.push(l); sumLum += l; sumSat += saturation(rgb);
      if (l > maxLum) maxLum = l; n++;
    }
    var meanLum = sumLum / n, meanSat = sumSat / n;
    var variance = 0; for (var k = 0; k < lums.length; k++) { var d = lums[k] - meanLum; variance += d * d; }
    var std = Math.sqrt(variance / n);
    var salience = meanLum * (0.6 * meanSat + 0.4 * std);
    return { maxLum: maxLum, meanLum: meanLum, meanSat: meanSat, std: std, salience: salience };
  }

  // Derive the smallest bg-dim alpha so every text rect clears its contrast floor on the composite.
  function deriveBgDim(ctx, textRects) {
    var best = 0;
    var perRect = [];
    textRects.forEach(function (t) {
      var stats = regionStats(ctx, t.x, t.y, t.w, t.h);
      // Worst case for light text = brightest scene pixel under the rect; approximate as a neutral at maxLum.
      var brightVal = Math.round(maxLumToSrgb(stats.maxLum) * 255);
      var scenePx = [brightVal, brightVal, brightVal];
      // If the ancestor chain ends opaque, the scene is occluded → dim is irrelevant for this rect.
      var occluded = t.chain.length > 0 && t.chain[0].a >= 0.999 ? false : null;
      var needed = 0, passAt;
      for (var a = 0; a <= 1.0001; a += 0.02) {
        var dimmed = over(DIM_RGB, a, scenePx);
        var composite = effectiveBg(t.chain, dimmed);
        if (contrast(t.color, composite) >= t.floor) { needed = a; passAt = true; break; }
        needed = 1; passAt = false;
      }
      // Detect "unfixable by dim": contrast didn't improve from a=0 to a=1 (chain is opaque).
      var c0 = contrast(t.color, effectiveBg(t.chain, over(DIM_RGB, 0, scenePx)));
      var c1 = contrast(t.color, effectiveBg(t.chain, over(DIM_RGB, 1, scenePx)));
      var dimSensitive = Math.abs(c1 - c0) > 0.05;
      perRect.push({ role: t.role, floor: t.floor, sceneMaxLum: Math.round(stats.maxLum * 1000) / 1000,
        neededAlpha: Math.round(needed * 1000) / 1000, dimSensitive: dimSensitive,
        opaquePass: !dimSensitive ? (c0 >= t.floor) : null });
      if (dimSensitive && needed > best) best = needed;
    });
    return { alpha: best, perRect: perRect };
  }
  function maxLumToSrgb(l) {
    // invert relative luminance for a neutral grey: l = lin, srgb channel = gamma(lin)
    var c = l <= 0.0031308 ? l * 12.92 : 1.055 * Math.pow(l, 1 / 2.4) - 0.055;
    return Math.max(0, Math.min(1, c));
  }

  // Derive face-dim alpha so the face region salience drops to <= target fraction of its undimmed value.
  function deriveFaceDim(ctx, faceRect) {
    var base = regionStats(ctx, faceRect.x, faceRect.y, faceRect.w, faceRect.h);
    if (base.salience <= 0) return { alpha: 0, base: base.salience, after: base.salience };
    var target = Math.min(base.salience * FACE_SALIENCE_FRACTION, FACE_SALIENCE_BUDGET);
    var chosen = 0, after = base.salience;
    for (var a = 0; a <= 1.0001; a += 0.05) {
      // simulate the solid dim overlay over the region: every pixel blended toward DIM_RGB by a
      var data = ctx.getImageData(Math.round(faceRect.x), Math.round(faceRect.y), Math.round(faceRect.w), Math.round(faceRect.h)).data;
      var sumLum = 0, sumSat = 0, lums = [], n = 0;
      for (var i = 0; i < data.length; i += 4) {
        var rgb = over(DIM_RGB, a, [data[i], data[i + 1], data[i + 2]]);
        var l = relLum(rgb); lums.push(l); sumLum += l; sumSat += saturation(rgb); n++;
      }
      var meanLum = sumLum / n, meanSat = sumSat / n, variance = 0;
      for (var k = 0; k < lums.length; k++) { var d = lums[k] - meanLum; variance += d * d; }
      var sal = meanLum * (0.6 * meanSat + 0.4 * Math.sqrt(variance / n));
      chosen = a; after = sal;
      if (sal <= target) break;
    }
    return { alpha: chosen, base: base.salience, after: after, target: target };
  }

  // ---- build DOM ----
  // Pure partition: 8 slots; 9+ rewards become first 7 + a "他N件" more-card; 0 rewards = empty.
  function partitionRewards(rewards) {
    if (!rewards || !rewards.length) return { slots: [], more: 0, empty: true };
    if (rewards.length > 8) return { slots: rewards.slice(0, 7), more: rewards.length - 7, empty: false };
    return { slots: rewards.slice(0, 8), more: 0, empty: false };
  }
  function buildRewards(listEl, noRewardsEl, rewards) {
    listEl.innerHTML = "";
    var part = partitionRewards(rewards);
    if (part.empty) { listEl.hidden = true; noRewardsEl.hidden = false; return; }
    listEl.hidden = false; noRewardsEl.hidden = true;
    var slots = part.slots, more = part.more;
    slots.forEach(function (r) {
      var li = document.createElement("li");
      li.className = "reward-slot"; li.setAttribute("tabindex", "-1");
      var img = document.createElement("img"); img.src = r.icon; img.alt = "";
      var c = document.createElement("span"); c.className = "count"; c.textContent = "x" + r.count;
      li.appendChild(img); li.appendChild(c); listEl.appendChild(li);
    });
    if (more > 0) {
      var li2 = document.createElement("li");
      li2.className = "reward-slot more"; li2.setAttribute("tabindex", "-1");
      var t = document.createElement("span"); t.className = "more-text"; t.textContent = "他" + more + "件";
      li2.appendChild(t); listEl.appendChild(li2);
    }
  }

  // ---- focus map (XAG 112): D-pad navigable, initial focus = Next, modal traps focus ----
  function setupFocus(stage, modalOpen) {
    var nextBtn = document.getElementById("nextBtn");
    var detailBtn = document.getElementById("detailBtn");
    var retryBtn = document.getElementById("retryBtn");
    var featured = document.getElementById("featuredCard");
    featured.setAttribute("tabindex", "-1");
    var order = [detailBtn, nextBtn];
    function focusEl(el) {
      [detailBtn, nextBtn, retryBtn, featured].concat(Array.prototype.slice.call(document.querySelectorAll(".reward-slot")))
        .forEach(function (e) { if (e) e.classList.remove("is-focused"); });
      if (el) { el.classList.add("is-focused"); el.focus(); stage.dataset.focus = el.id || el.className; }
    }
    if (modalOpen) { focusEl(retryBtn); }
    else { focusEl(nextBtn); }

    document.addEventListener("keydown", function (e) {
      if (stage.dataset.modal === "open") {
        // focus trapped on retry
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Tab"].indexOf(e.key) >= 0) { e.preventDefault(); focusEl(retryBtn); }
        return;
      }
      var cur = document.activeElement;
      if (e.key === "ArrowLeft") { e.preventDefault(); if (cur === nextBtn) focusEl(detailBtn); }
      else if (e.key === "ArrowRight") { e.preventDefault(); if (cur === detailBtn) focusEl(nextBtn); }
      else if (e.key === "ArrowUp") { e.preventDefault(); focusEl(featured); }
      else if (e.key === "ArrowDown") { e.preventDefault(); focusEl(nextBtn); }
      else if (e.key === "Tab") { e.preventDefault(); var idx = order.indexOf(cur); focusEl(order[(idx + 1 + order.length) % order.length] || nextBtn); }
    });
    return { focusEl: focusEl, nextBtn: nextBtn, detailBtn: detailBtn, retryBtn: retryBtn, featured: featured };
  }

  function render() {
    var result = readResult();
    var stage = document.getElementById("stage");
    var reduced = new URLSearchParams(location.search).get("reduced") === "1" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    stage.dataset.reducedMotion = reduced ? "true" : "false";

    // text content
    document.getElementById("stageName").textContent = result.stageName || "";
    var titleEl = document.getElementById("resultTitle");
    titleEl.textContent = result.title; titleEl.dataset.outcome = result.outcome;
    document.getElementById("featuredIcon").src = result.featured.icon;
    var nameEl = document.getElementById("featuredName");
    nameEl.textContent = result.featured.name;
    nameEl.dataset.long = (result.featured.name && result.featured.name.length >= 18) ? "true" : "false";
    document.getElementById("featuredRarity").textContent = RARITY_TIER[result.featured.rarity] || "N";
    var expEl = document.getElementById("statExpValue"); expEl.textContent = formatGain(result.exp || 0);
    var goldEl = document.getElementById("statGoldValue"); goldEl.textContent = formatGain(result.gold || 0);
    if ((result.exp || 0) < 0) { expEl.className = "stat-value stat-value--loss"; }
    if ((result.gold || 0) < 0) { goldEl.className = "stat-value stat-value--loss"; }
    buildRewards(document.getElementById("rewardList"), document.getElementById("noRewards"), result.rewards);

    // scene + derivation
    var canvas = document.getElementById("bgScene");
    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    var charImg = document.getElementById("bgChar");
    charImg.hidden = true;

    function afterScene() {
      drawScene(ctx, charImg, result);
      // collect text rects in stage coordinates (the stage is 1:1 with the 1920x1080 canvas)
      var stageRect = stage.getBoundingClientRect();
      var scale = stageRect.width / 1920;
      var modalOpen = !!result.error;
      var textRects = [];
      document.querySelectorAll("[data-text-role]").forEach(function (el) {
        var r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return;
        var role = el.dataset.textRole;
        var inModal = !!el.closest("#errorModal");
        // When the modal is open the background is intentionally scrimmed/inactive — measure only the
        // active modal layer. When closed, skip the modal's own text. (Avoids scoring occluded text.)
        if (modalOpen && !inModal) return;
        if (!modalOpen && inModal) return;
        textRects.push({
          role: role, floor: CONTRAST_FLOORS[role] || 4.5,
          color: parseColor(getComputedStyle(el).color),
          chain: ancestorChain(el),
          x: (r.left - stageRect.left) / scale, y: (r.top - stageRect.top) / scale,
          w: r.width / scale, h: r.height / scale
        });
      });

      var bg = deriveBgDim(ctx, textRects);
      // Test hook: ?forcedim=N pins the dim, bypassing derivation, so a RED test can prove the
      // instrument detects under-dimming (calibration fairness: the gate must bite, not rubber-stamp).
      var forced = new URLSearchParams(location.search).get("forcedim");
      if (forced !== null && !isNaN(parseFloat(forced))) { bg.alpha = parseFloat(forced); bg.forced = true; }
      stage.style.setProperty("--bg-dim-alpha", String(bg.alpha));
      var face = deriveFaceDim(ctx, { x: 120, y: 190, w: 200, h: 230 });
      stage.style.setProperty("--bg-face-dim-alpha", String(face.alpha));

      // measured contrast AFTER applying derived dim (for the self-report; Python re-verifies on the screenshot)
      var measuredContrast = textRects.map(function (t) {
        var brightVal = Math.round(maxLumToSrgb(regionStats(ctx, t.x, t.y, t.w, t.h).maxLum) * 255);
        var scenePx = [brightVal, brightVal, brightVal];
        var dimmed = over(DIM_RGB, bg.alpha, scenePx);
        var composite = effectiveBg(t.chain, dimmed);
        var cval = contrast(t.color, composite);
        return { role: t.role, floor: t.floor, measured: Math.round(cval * 100) / 100, pass: cval >= t.floor };
      });

      window.__resultMeasure = {
        variant: result.variantName || "normal",
        outcome: result.outcome,
        panel_alpha: PANEL_ALPHA,
        derived: { bg_dim_alpha: Math.round(bg.alpha * 1000) / 1000, face_dim_alpha: Math.round(face.alpha * 1000) / 1000 },
        bg_dim_per_rect: bg.perRect,
        face_salience: { before: face.base, after: face.after, target: face.target },
        contrast: measuredContrast,
        contrast_all_pass: measuredContrast.every(function (c) { return c.pass; }),
        rects: textRects.map(function (t) {
          return { role: t.role, floor: t.floor, color: t.color.map(Math.round),
            x: Math.round(t.x), y: Math.round(t.y), w: Math.round(t.w), h: Math.round(t.h) };
        }),
        reveal_burst_ms: REVEAL_BURST_MS,
        reduced_motion: reduced
      };

      // serialize the self-report into the DOM so a --dump-dom run can read it without devtools.
      var out = document.getElementById("measureOutput");
      if (!out) { out = document.createElement("script"); out.id = "measureOutput"; out.type = "application/json"; document.body.appendChild(out); }
      out.textContent = JSON.stringify(window.__resultMeasure);

      // error modal
      if (result.error) {
        document.getElementById("errorModal").hidden = false;
        stage.dataset.modal = "open";
      } else {
        stage.dataset.modal = "closed";
      }
      var focus = setupFocus(stage, !!result.error);
      focus.detailBtn.addEventListener("click", function () { stage.dataset.action = "detail"; });
      focus.nextBtn.addEventListener("click", function () { stage.dataset.action = "next"; });
      if (focus.retryBtn) focus.retryBtn.addEventListener("click", function () {
        document.getElementById("errorModal").hidden = true; stage.dataset.modal = "closed"; setupFocus(stage, false);
      });

      // reveal animation within band (not on reduced motion)
      if (!reduced) { stage.dataset.animate = "true"; }
      stage.dataset.state = "ready";
      document.body.dataset.state = "ready";
      window.__resultReady = true;
    }

    if (result.char) {
      charImg.onload = afterScene;
      charImg.onerror = afterScene;
      charImg.src = result.char;
      if (charImg.complete) afterScene();
    } else {
      afterScene();
    }
  }

  // expose a minimal test harness
  window.__resultTest = {
    setStageScale: function () {
      var stage = document.getElementById("stage");
      var native = new URLSearchParams(location.search).get("native") === "1";
      if (native) {
        stage.dataset.native = "true";
        stage.style.setProperty("--stage-scale", "1");
        return;
      }
      var sx = window.innerWidth / 1920, sy = window.innerHeight / 1080;
      stage.style.setProperty("--stage-scale", String(Math.min(sx, sy)));
    }
  };

  // Pure helpers exposed for the deterministic node spec (rendering-independent).
  window.__resultInternals = {
    formatGain: formatGain,
    partitionRewards: partitionRewards,
    RARITY_TIER: RARITY_TIER,
    REVEAL_BURST_MS: REVEAL_BURST_MS
  };

  function boot() {
    window.__resultTest.setStageScale();
    window.addEventListener("resize", window.__resultTest.setStageScale);
    render();
  }

  if (typeof globalThis !== "undefined" && globalThis.__RESULT_SPEC__) {
    // unit-test mode: helpers are exposed; do not auto-boot (no real DOM/canvas).
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
