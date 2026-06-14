(function () {
  "use strict";

  const ODDS = Object.freeze({
    common: 70,
    rare: 23,
    epic: 6,
    legendary: 1
  });

  const PITY_LIMIT = 10;
  const STARTING_TICKETS = 3;

  const CADENCE_BANDS = Object.freeze({
    normal: Object.freeze({ min: 80, max: 450 }),
    fast: Object.freeze({ min: 0, max: 50 })
  });

  const CADENCE_MS = Object.freeze({
    pre_draw_audit: 80,
    draw_commit: 120,
    anticipation: 420,
    rarity_signal: 260,
    item_identity: 300,
    inventory_commit: 140,
    recovery: 100,
    reduced_motion_reveal: 20,
    silent_no_audio: 20,
    skipped_anticipation: 0,
    ticket_guard: 50,
    missing_draw_commit: 0
  });

  const RENDER_HOOKS = Object.freeze({
    pre_draw_audit: "audit_panel",
    draw_commit: "capsule_lock",
    anticipation: "capsule_shake",
    rarity_signal: "rarity_flash",
    item_identity: "local_png_reveal",
    inventory_commit: "collection_commit",
    recovery: "control_recovery",
    reduced_motion_reveal: "fast_motion_reveal",
    silent_no_audio: "silent_audio_gate",
    skipped_anticipation: "skip_gate",
    ticket_guard: "ticket_guard_panel",
    missing_draw_commit: "inventory_guard_panel"
  });

  const ITEMS = Object.freeze([
    Object.freeze({ id: "common_slime", name: "Common Slime", rarity: "common", image: "assets/common_slime.png" }),
    Object.freeze({ id: "rare_hero_m", name: "Rare Hero M", rarity: "rare", image: "assets/rare_hero_m.png" }),
    Object.freeze({ id: "rare_hero_f", name: "Rare Hero F", rarity: "rare", image: "assets/rare_hero_f.png" }),
    Object.freeze({ id: "epic_treasure", name: "Epic Treasure", rarity: "epic", image: "assets/epic_treasure.png" }),
    Object.freeze({ id: "legendary_dragon", name: "Legendary Dragon", rarity: "legendary", image: "assets/legendary_dragon.png" })
  ]);

  const els = {
    app: document.getElementById("app"),
    machine: document.getElementById("machine"),
    capsule: document.getElementById("capsule"),
    itemArt: document.getElementById("itemArt"),
    rarityLabel: document.getElementById("rarityLabel"),
    statusText: document.getElementById("statusText"),
    pullButton: document.getElementById("pullButton"),
    resetButton: document.getElementById("resetButton"),
    ticketCount: document.getElementById("ticketCount"),
    pityStatus: document.getElementById("pityStatus"),
    latestResult: document.getElementById("latestResult"),
    collectionList: document.getElementById("collectionList"),
    seedInput: document.getElementById("seedInput"),
    reducedMotionToggle: document.getElementById("reducedMotionToggle"),
    silentToggle: document.getElementById("silentToggle"),
    skipToggle: document.getElementById("skipToggle")
  };

  const querySeed = new URLSearchParams(window.location.search).get("seed");
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const game = {
    tickets: STARTING_TICKETS,
    pity: 0,
    inventory: {},
    busy: false,
    seed: String(querySeed || "20260614"),
    rng: null
  };

  window.__lastTrace = [];

  function hashSeed(seedText) {
    let hash = 2166136261;
    const text = String(seedText || "0");
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0 || 1;
  }

  function makeRng(seedText) {
    let state = hashSeed(seedText);
    return function nextRandom() {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      return state / 4294967296;
    };
  }

  function getModes() {
    return {
      motion: els.reducedMotionToggle.checked || prefersReducedMotion.matches ? "reduced" : "normal",
      audio: els.silentToggle.checked ? "silent" : "enabled",
      skip: els.skipToggle.checked
    };
  }

  function setCadenceVariables() {
    els.app.style.setProperty("--capsule-ms", `${CADENCE_MS.anticipation}ms`);
    els.app.style.setProperty("--flash-ms", `${CADENCE_MS.rarity_signal}ms`);
    els.app.style.setProperty("--item-ms", `${CADENCE_MS.item_identity}ms`);
  }

  function updateDomState(stateName, rarity) {
    document.body.dataset.state = stateName;
    els.app.dataset.state = stateName;
    els.machine.dataset.state = stateName;
    if (rarity) {
      els.app.dataset.rarity = rarity;
      els.machine.dataset.rarity = rarity;
    } else if (stateName === "pre_draw_audit" || stateName === "ticket_guard") {
      delete els.app.dataset.rarity;
      delete els.machine.dataset.rarity;
    }
  }

  function snapshotInventory() {
    return Object.assign({}, game.inventory);
  }

  function renderEconomy() {
    els.ticketCount.textContent = String(game.tickets);
    els.pityStatus.textContent = `Legendary pity: ${game.pity} / ${PITY_LIMIT}`;
  }

  function renderCollection() {
    const owned = ITEMS.filter((item) => game.inventory[item.id]);
    els.collectionList.replaceChildren();
    if (owned.length === 0) {
      const empty = document.createElement("li");
      empty.textContent = "No inventory committed.";
      els.collectionList.append(empty);
      return;
    }
    for (const item of owned) {
      const entry = document.createElement("li");
      const label = document.createElement("span");
      const count = document.createElement("strong");
      label.textContent = `${item.name} (${item.rarity})`;
      count.textContent = `x${game.inventory[item.id]}`;
      entry.append(label, count);
      els.collectionList.append(entry);
    }
  }

  function setControlsEnabled(enabled) {
    els.pullButton.disabled = !enabled;
    els.resetButton.disabled = !enabled;
    els.seedInput.disabled = !enabled;
  }

  function renderBeat(record) {
    els.machine.dataset.renderHook = record.render_hook;
    els.app.style.setProperty("--current-cadence-ms", `${record.cadence_ms}ms`);

    if (record.state === "pre_draw_audit") {
      els.itemArt.hidden = true;
      els.itemArt.removeAttribute("src");
      els.itemArt.alt = "";
      els.rarityLabel.textContent = "Audit complete";
      els.statusText.textContent = record.affordability
        ? `Odds and pity checked before draw. Tickets: ${record.ticket_balance}.`
        : "No ticket available. Draw commit blocked.";
    }

    if (record.state === "ticket_guard") {
      els.rarityLabel.textContent = "No ticket";
      els.statusText.textContent = "Insufficient ticket guard recorded. No draw committed.";
    }

    if (record.state === "draw_commit") {
      renderEconomy();
      els.rarityLabel.textContent = "Draw committed";
      els.statusText.textContent = "Ticket consumed. Capsule locked.";
    }

    if (record.state === "silent_no_audio") {
      els.statusText.textContent = "Silent mode gate recorded. Reveal continues without audio.";
    }

    if (record.state === "reduced_motion_reveal") {
      els.statusText.textContent = "Reduced motion reveal state recorded.";
    }

    if (record.state === "skipped_anticipation") {
      els.statusText.textContent = "Anticipation skipped. Rarity signal remains next.";
    }

    if (record.state === "anticipation") {
      els.rarityLabel.textContent = "Shaking...";
      els.statusText.textContent = "Capsule shake in progress.";
    }

    if (record.state === "rarity_signal") {
      els.rarityLabel.textContent = record.rarity.toUpperCase();
      els.statusText.textContent = `${record.rarity.toUpperCase()} signal acquired. Identity still hidden.`;
    }

    if (record.state === "item_identity") {
      els.itemArt.src = record.item_image;
      els.itemArt.alt = record.item_name;
      els.itemArt.hidden = false;
      els.rarityLabel.textContent = record.item_name;
      els.statusText.textContent = `${record.item_name} revealed from local asset.`;
    }

    if (record.state === "inventory_commit") {
      els.latestResult.textContent = `${record.item_name} (${record.rarity}) committed to collection.`;
      renderCollection();
      renderEconomy();
      els.statusText.textContent = "Inventory commit complete.";
    }

    if (record.state === "missing_draw_commit") {
      els.statusText.textContent = "Inventory guard blocked a missing draw_commit.";
      els.rarityLabel.textContent = "Commit blocked";
    }

    if (record.state === "recovery") {
      els.statusText.textContent = "Ready for another pull.";
    }
  }

  function traceBeat(stateName, details) {
    const modes = getModes();
    const record = Object.assign({
      state: stateName,
      event: stateName,
      guard_result: "not_applicable",
      render_hook: RENDER_HOOKS[stateName],
      cadence_ms: CADENCE_MS[stateName],
      motion_mode: modes.motion,
      audio_mode: modes.audio,
      tickets: game.tickets,
      inventory_mutation: "none"
    }, details || {});
    window.__lastTrace.push(record);
    updateDomState(stateName, record.rarity);
    renderBeat(record);
    return record;
  }

  function waitForCadence(record) {
    return new Promise((resolve) => window.setTimeout(resolve, record.cadence_ms));
  }

  function chooseByRarity(rarity) {
    const candidates = ITEMS.filter((item) => item.rarity === rarity);
    const index = Math.floor(game.rng() * candidates.length);
    return candidates[index];
  }

  function drawItem() {
    const pityBefore = game.pity;
    const forcedLegendary = pityBefore >= PITY_LIMIT - 1;
    const roll = forcedLegendary ? 99.5 : game.rng() * 100;
    let rarity = "common";
    if (roll >= 99) {
      rarity = "legendary";
    } else if (roll >= 93) {
      rarity = "epic";
    } else if (roll >= 70) {
      rarity = "rare";
    }
    const item = chooseByRarity(rarity);
    return Object.assign({ pity_before: pityBefore, forced_legendary: forcedLegendary }, item);
  }

  function commitInventory(context) {
    const hasDrawCommit = window.__lastTrace.some((entry) => entry.state === "draw_commit" && entry.event === "draw_committed");
    if (!context || !context.committedDraw || !hasDrawCommit || !context.draw) {
      return traceBeat("missing_draw_commit", {
        event: "inventory_commit_blocked",
        guard_result: "missing_draw_commit",
        inventory_mutation: "blocked"
      });
    }

    const draw = context.draw;
    const inventoryAfter = snapshotInventory();
    inventoryAfter[draw.id] = (inventoryAfter[draw.id] || 0) + 1;
    const pityAfter = draw.rarity === "legendary" ? 0 : draw.pity_before + 1;
    const modes = getModes();
    const record = {
      state: "inventory_commit",
      event: "inventory_committed",
      guard_result: "draw_commit_present",
      render_hook: RENDER_HOOKS.inventory_commit,
      cadence_ms: CADENCE_MS.inventory_commit,
      motion_mode: modes.motion,
      audio_mode: modes.audio,
      tickets: game.tickets,
      inventory_mutation: "committed",
      item_id: draw.id,
      item_name: draw.name,
      item_image: draw.image,
      rarity: draw.rarity,
      inventory_after: inventoryAfter,
      pity_after: pityAfter
    };
    window.__lastTrace.push(record);
    game.inventory[draw.id] = inventoryAfter[draw.id];
    game.pity = pityAfter;
    updateDomState("inventory_commit", record.rarity);
    renderBeat(record);
    return record;
  }

  function resetRun(seedText) {
    game.seed = String(seedText || els.seedInput.value || "0");
    game.rng = makeRng(game.seed);
    game.tickets = STARTING_TICKETS;
    game.pity = 0;
    game.inventory = {};
    window.__lastTrace = [];
    els.seedInput.value = game.seed;
    els.latestResult.textContent = "No committed item yet.";
    renderEconomy();
    renderCollection();
    updateDomState("recovery");
    els.machine.dataset.renderHook = "control_recovery";
    els.itemArt.hidden = true;
    els.itemArt.removeAttribute("src");
    els.itemArt.alt = "";
    els.rarityLabel.textContent = "Awaiting pull";
    els.statusText.textContent = "Run reset. Odds and pity are visible before the next pull.";
  }

  async function runPull() {
    if (game.busy) {
      return window.__lastTrace;
    }

    game.busy = true;
    setControlsEnabled(false);
    window.__lastTrace = [];

    const context = {
      committedDraw: false,
      draw: null
    };

    const audit = traceBeat("pre_draw_audit", {
      event: "pull_requested",
      guard_result: game.tickets > 0 ? "affordable" : "insufficient_tickets",
      odds: ODDS,
      pity: { current: game.pity, limit: PITY_LIMIT },
      ticket_balance: game.tickets,
      affordability: game.tickets > 0,
      inventory_before: snapshotInventory()
    });
    await waitForCadence(audit);

    if (game.tickets <= 0) {
      const guard = traceBeat("ticket_guard", {
        event: "pull_blocked",
        guard_result: "insufficient_tickets",
        cadence_ms: CADENCE_MS.ticket_guard,
        inventory_mutation: "blocked"
      });
      await waitForCadence(guard);
      traceBeat("recovery", { event: "control_recovered" });
      setControlsEnabled(true);
      game.busy = false;
      return window.__lastTrace;
    }

    context.draw = drawItem();
    game.tickets -= 1;
    context.committedDraw = true;
    const commit = traceBeat("draw_commit", {
      event: "draw_committed",
      inventory_mutation: "ticket_consumed",
      seed: game.seed
    });
    await waitForCadence(commit);

    const modes = getModes();
    if (modes.audio === "silent") {
      const silent = traceBeat("silent_no_audio", {
        event: "silent_mode_selected",
        cadence_ms: CADENCE_MS.silent_no_audio
      });
      await waitForCadence(silent);
    }

    if (modes.motion === "reduced") {
      const reduced = traceBeat("reduced_motion_reveal", {
        event: "reduced_motion_selected",
        cadence_ms: CADENCE_MS.reduced_motion_reveal
      });
      await waitForCadence(reduced);
    }

    if (modes.skip) {
      const skipped = traceBeat("skipped_anticipation", {
        event: "skip_selected",
        cadence_ms: CADENCE_MS.skipped_anticipation
      });
      await waitForCadence(skipped);
    } else {
      const anticipation = traceBeat("anticipation", { event: "capsule_shake" });
      await waitForCadence(anticipation);
    }

    const rarity = traceBeat("rarity_signal", {
      event: "rarity_signaled",
      rarity: context.draw.rarity
    });
    await waitForCadence(rarity);

    const identity = traceBeat("item_identity", {
      event: "item_identified",
      rarity: context.draw.rarity,
      item_id: context.draw.id,
      item_name: context.draw.name,
      item_image: context.draw.image
    });
    await waitForCadence(identity);

    const inventory = commitInventory(context);
    await waitForCadence(inventory);

    const recovery = traceBeat("recovery", { event: "control_recovered" });
    await waitForCadence(recovery);

    setControlsEnabled(true);
    game.busy = false;
    return window.__lastTrace;
  }

  function wireControls() {
    els.pullButton.addEventListener("click", () => {
      runPull();
    });
    els.resetButton.addEventListener("click", () => {
      resetRun(els.seedInput.value);
    });
    els.seedInput.addEventListener("change", () => {
      resetRun(els.seedInput.value);
    });
    els.reducedMotionToggle.addEventListener("change", () => {
      if (!game.busy) {
        updateDomState(els.app.dataset.state || "recovery", els.app.dataset.rarity);
      }
    });
  }

  function initialize() {
    setCadenceVariables();
    els.reducedMotionToggle.checked = prefersReducedMotion.matches;
    wireControls();
    resetRun(game.seed);
  }

  window.__gachaTest = {
    pull: runPull,
    resetRun,
    setTickets(count) {
      game.tickets = Math.max(0, Number(count) || 0);
      renderEconomy();
    },
    attemptMissingDrawCommit() {
      return commitInventory({ committedDraw: false, draw: null });
    },
    getState() {
      return {
        tickets: game.tickets,
        pity: game.pity,
        inventory: snapshotInventory(),
        seed: game.seed,
        data_state: els.app.dataset.state,
        data_rarity: els.app.dataset.rarity || null
      };
    },
    cadence: { bands: CADENCE_BANDS, values: CADENCE_MS }
  };

  initialize();
}());
