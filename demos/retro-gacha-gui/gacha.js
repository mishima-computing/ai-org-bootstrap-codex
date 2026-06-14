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
  const PULL_COST = 1;

  // Empirical/tuned cadence bands: these are stable feel constants, not derived timings.
  const CADENCE_BANDS = Object.freeze({
    normal: Object.freeze({ min: 80, max: 450 }),
    fast: Object.freeze({ min: 0, max: 50 }),
    input_hold: Object.freeze({ min: 220, max: 420 }),
    message_char: Object.freeze({ min: 18, max: 42 }),
    message_pause: Object.freeze({ min: 90, max: 180 })
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
    missing_draw_commit: 0,
    pull_refused: 50,
    input_press_started: 0,
    input_hold_ready: 260,
    input_released: 0,
    input_cancelled: 0,
    message_typing: 28,
    message_pause: 120,
    message_advance: 0,
    message_instant_skip: 0,
    input_record_exported: 0,
    input_replay_started: 0,
    input_replay_finished: 0,
    input_replay_refused: 40
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
    missing_draw_commit: "inventory_guard_panel",
    pull_refused: "pull_refusal_flash",
    input_press_started: "charge_meter",
    input_hold_ready: "charge_meter",
    input_released: "charge_meter",
    input_cancelled: "charge_meter",
    message_typing: "message_window",
    message_pause: "message_window",
    message_advance: "message_window",
    message_instant_skip: "message_window",
    input_record_exported: "replay_panel",
    input_replay_started: "replay_panel",
    input_replay_finished: "replay_panel",
    input_replay_refused: "replay_panel"
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
    pullCostLabel: document.getElementById("pullCostLabel"),
    pullAffordableState: document.getElementById("pullAffordableState"),
    pullRefusalFlash: document.getElementById("pullRefusalFlash"),
    chargeLabel: document.getElementById("chargeLabel"),
    chargeMeter: document.getElementById("chargeMeter"),
    resetButton: document.getElementById("resetButton"),
    ticketCount: document.getElementById("ticketCount"),
    pityStatus: document.getElementById("pityStatus"),
    latestResult: document.getElementById("latestResult"),
    messageWindow: document.getElementById("messageWindow"),
    collectionList: document.getElementById("collectionList"),
    seedInput: document.getElementById("seedInput"),
    exportRecordButton: document.getElementById("exportRecordButton"),
    replayRecordButton: document.getElementById("replayRecordButton"),
    replayStatus: document.getElementById("replayStatus"),
    inputRecordOutput: document.getElementById("inputRecordOutput"),
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
    rng: null,
    input: null,
    inputRecord: null,
    replaySnapshot: null,
    replaying: false,
    instantCadence: false
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

  function hasTicketsForPull() {
    return game.tickets >= PULL_COST;
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

  function updateInputDom(stateName, details) {
    const state = stateName || "idle";
    els.app.dataset.inputState = state;
    els.pullButton.dataset.inputState = state;
    els.chargeLabel.textContent = details && details.label ? details.label : "Press or hold to pull";
    if (state === "idle" || state === "cancelled") {
      els.chargeMeter.style.width = "0%";
    }
    if (state === "charging") {
      els.chargeMeter.style.width = "55%";
    }
    if (state === "held_ready" || state === "held") {
      els.chargeMeter.style.width = "100%";
    }
  }

  function renderAffordability() {
    const affordable = hasTicketsForPull();
    els.pullButton.textContent = affordable
      ? `Pull - ${PULL_COST} ticket`
      : `Pull - ${PULL_COST} ticket (refuse)`;
    els.pullCostLabel.textContent = `Cost: ${PULL_COST} ticket`;
    els.pullAffordableState.textContent = affordable ? "Affordable" : "Not enough tickets";
    els.pullAffordableState.dataset.affordable = affordable ? "true" : "false";
    els.pullButton.dataset.affordable = affordable ? "true" : "false";
  }

  function clearRefusalFlash() {
    els.pullRefusalFlash.textContent = "";
    delete els.pullRefusalFlash.dataset.refused;
  }

  function renderReplayRecord(record) {
    if (!record) {
      els.inputRecordOutput.textContent = "";
      els.replayStatus.textContent = "No input record exported.";
      els.replayStatus.dataset.replayState = "idle";
      return;
    }
    els.inputRecordOutput.textContent = JSON.stringify(record, null, 2);
    els.replayStatus.textContent = `Record exported: ${record.events.length} input edges for seed ${record.seed}.`;
    els.replayStatus.dataset.replayState = "exported";
  }

  function snapshotInventory() {
    return Object.assign({}, game.inventory);
  }

  function renderEconomy() {
    els.ticketCount.textContent = String(game.tickets);
    els.pityStatus.textContent = `Legendary pity: ${game.pity} / ${PITY_LIMIT}`;
    renderAffordability();
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
    els.exportRecordButton.disabled = !enabled;
    els.replayRecordButton.disabled = !enabled;
  }

  function renderBeat(record) {
    els.machine.dataset.renderHook = record.render_hook;
    els.app.style.setProperty("--current-cadence-ms", `${record.cadence_ms}ms`);

    if (record.state === "input_press_started") {
      updateInputDom("charging", { label: `${record.input_source} press started` });
      clearRefusalFlash();
    }

    if (record.state === "input_hold_ready") {
      updateInputDom("held_ready", { label: `Hold ready at ${record.held_duration_ms}ms` });
    }

    if (record.state === "input_released") {
      updateInputDom(record.commit_classification, {
        label: `${record.commit_classification} release: ${record.held_duration_ms}ms`
      });
    }

    if (record.state === "input_cancelled") {
      updateInputDom("cancelled", { label: `Input cancelled at ${record.held_duration_ms}ms` });
    }

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

    if (record.state === "pull_refused") {
      els.rarityLabel.textContent = "Pull refused";
      els.statusText.textContent = `Pull refused: cost ${record.cost}, tickets ${record.ticket_balance}.`;
      els.pullRefusalFlash.textContent = "Pull refused. Not enough demo tickets; no draw was committed.";
      els.pullRefusalFlash.dataset.refused = "true";
    }

    if (record.state === "draw_commit") {
      renderEconomy();
      els.rarityLabel.textContent = "Draw committed";
      els.statusText.textContent = "Ticket consumed. Capsule locked.";
      clearRefusalFlash();
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

    if (record.state === "message_typing") {
      els.messageWindow.dataset.messageState = "message_typing";
      els.messageWindow.textContent = record.message_text;
    }

    if (record.state === "message_pause") {
      els.messageWindow.dataset.messageState = "message_pause";
    }

    if (record.state === "message_advance") {
      els.messageWindow.dataset.messageState = "message_advance";
      els.messageWindow.textContent = record.message_text;
    }

    if (record.state === "message_instant_skip") {
      els.messageWindow.dataset.messageState = "message_instant_skip";
      els.messageWindow.textContent = record.message_text;
    }

    if (record.state === "input_record_exported") {
      renderReplayRecord(record.record);
    }

    if (record.state === "input_replay_started") {
      els.replayStatus.textContent = `Replay started for seed ${record.seed}.`;
      els.replayStatus.dataset.replayState = "started";
    }

    if (record.state === "input_replay_finished") {
      els.replayStatus.textContent = `Replay finished: ${record.outcome.item_id || "no item"}.`;
      els.replayStatus.dataset.replayState = "finished";
    }

    if (record.state === "input_replay_refused") {
      els.replayStatus.textContent = record.reason;
      els.replayStatus.dataset.replayState = "refused";
    }

    if (record.state === "recovery") {
      els.statusText.textContent = "Ready for another pull.";
      updateInputDom("idle");
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
    if (game.instantCadence) {
      return Promise.resolve();
    }
    return new Promise((resolve) => window.setTimeout(resolve, record.cadence_ms));
  }

  async function narrateMessage(messageText, phaseName) {
    const modes = getModes();
    if (modes.skip) {
      const skipped = traceBeat("message_instant_skip", {
        event: "message_skip_selected",
        message_phase: phaseName,
        message_text: messageText
      });
      await waitForCadence(skipped);
      return skipped;
    }

    const typed = traceBeat("message_typing", {
      event: "message_typed",
      message_phase: phaseName,
      message_text: messageText,
      cadence_band: "message_char"
    });
    await waitForCadence(typed);

    const pause = traceBeat("message_pause", {
      event: "message_paused",
      message_phase: phaseName,
      message_text: messageText,
      cadence_band: "message_pause"
    });
    await waitForCadence(pause);

    const advance = traceBeat("message_advance", {
      event: "message_advanced",
      message_phase: phaseName,
      message_text: messageText
    });
    await waitForCadence(advance);
    return advance;
  }

  function makeInputRecord(seed) {
    return {
      format: "retro-gacha-input-record-v1",
      seed: String(seed),
      started_at_ms: 0,
      events: []
    };
  }

  function ensureInputRecord() {
    if (!game.inputRecord || game.inputRecord.seed !== game.seed) {
      game.inputRecord = makeInputRecord(game.seed);
    }
    return game.inputRecord;
  }

  function normalizeRelativeMs(value) {
    return Math.max(0, Math.round(Number(value) || 0));
  }

  function recordInputEdge(edge) {
    if (game.replaying) {
      return;
    }
    const record = ensureInputRecord();
    record.events.push({
      type: edge.type,
      target_id: edge.target_id || "pullButton",
      input_source: edge.input_source || "pointer",
      relative_ms: normalizeRelativeMs(edge.relative_ms),
      semantic: edge.semantic || "pull_control"
    });
  }

  function exportInputRecord() {
    const source = game.inputRecord || makeInputRecord(game.seed);
    const exported = {
      format: source.format,
      seed: source.seed,
      started_at_ms: 0,
      events: source.events.map((event) => Object.assign({}, event))
    };
    const traceRecord = traceBeat("input_record_exported", {
      event: "input_record_exported",
      record: exported,
      event_count: exported.events.length,
      seed: exported.seed
    });
    renderReplayRecord(exported);
    game.replaySnapshot = exported;
    return traceRecord.record;
  }

  function latestOutcomeFromTrace(trace) {
    const item = trace.filter((entry) => entry.state === "item_identity").pop();
    const inventory = trace.filter((entry) => entry.state === "inventory_commit").pop();
    return {
      item_id: item ? item.item_id : null,
      item_name: item ? item.item_name : null,
      rarity: item ? item.rarity : null,
      inventory_after: inventory ? inventory.inventory_after : snapshotInventory(),
      tickets: game.tickets,
      pity: game.pity
    };
  }

  function validateRecord(record) {
    return record
      && record.format === "retro-gacha-input-record-v1"
      && typeof record.seed === "string"
      && Array.isArray(record.events)
      && record.events.every((event) => event
        && (event.type === "input_press_started" || event.type === "input_released" || event.type === "input_cancelled")
        && event.target_id === "pullButton"
        && Number.isFinite(Number(event.relative_ms)));
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

  function nowMs() {
    if (window.performance && typeof window.performance.now === "function") {
      return window.performance.now();
    }
    return Date.now();
  }

  function startPullInput(options) {
    const opts = options || {};
    if (game.busy || game.input) {
      return null;
    }

    if (!game.replaying) {
      window.__lastTrace = [];
      game.inputRecord = makeInputRecord(game.seed);
    }

    const startAt = Number.isFinite(Number(opts.now)) ? Number(opts.now) : nowMs();
    const inputSource = opts.inputSource || "pointer";
    game.input = {
      startAt,
      inputSource,
      holdReady: false,
      timer: null
    };
    recordInputEdge({
      type: "input_press_started",
      input_source: inputSource,
      relative_ms: 0
    });
    const press = traceBeat("input_press_started", {
      event: "input_press_started",
      input_source: inputSource,
      target_id: "pullButton",
      cadence_band: "input_hold",
      hold_threshold_ms: CADENCE_MS.input_hold_ready
    });
    if (!game.instantCadence) {
      game.input.timer = window.setTimeout(() => {
        if (!game.input || game.input.holdReady) {
          return;
        }
        game.input.holdReady = true;
        traceBeat("input_hold_ready", {
          event: "input_hold_ready",
          input_source: inputSource,
          target_id: "pullButton",
          held_duration_ms: CADENCE_MS.input_hold_ready,
          cadence_band: "input_hold",
          hold_threshold_ms: CADENCE_MS.input_hold_ready
        });
      }, CADENCE_MS.input_hold_ready);
    }
    return press;
  }

  function markHoldReadyIfNeeded(input, heldDuration) {
    if (input.holdReady || heldDuration < CADENCE_MS.input_hold_ready) {
      return;
    }
    input.holdReady = true;
    traceBeat("input_hold_ready", {
      event: "input_hold_ready",
      input_source: input.inputSource,
      target_id: "pullButton",
      held_duration_ms: heldDuration,
      cadence_band: "input_hold",
      hold_threshold_ms: CADENCE_MS.input_hold_ready
    });
  }

  async function releasePullInput(options) {
    const opts = options || {};
    const input = game.input;
    if (!input) {
      return window.__lastTrace;
    }
    const releaseAt = Number.isFinite(Number(opts.now)) ? Number(opts.now) : nowMs();
    const heldDuration = normalizeRelativeMs(releaseAt - input.startAt);
    if (input.timer) {
      window.clearTimeout(input.timer);
    }
    markHoldReadyIfNeeded(input, heldDuration);
    const classification = input.holdReady ? "held" : "pressed";
    recordInputEdge({
      type: "input_released",
      input_source: input.inputSource,
      relative_ms: heldDuration,
      semantic: classification
    });
    traceBeat("input_released", {
      event: "input_released",
      input_source: input.inputSource,
      target_id: "pullButton",
      held_duration_ms: heldDuration,
      commit_classification: classification,
      cadence_band: "input_hold",
      hold_threshold_ms: CADENCE_MS.input_hold_ready
    });
    game.input = null;
    return runPull({
      preserveTrace: true,
      input_source: input.inputSource,
      input_classification: classification,
      held_duration_ms: heldDuration
    });
  }

  function cancelPullInput(options) {
    const opts = options || {};
    const input = game.input;
    if (!input) {
      return null;
    }
    const cancelAt = Number.isFinite(Number(opts.now)) ? Number(opts.now) : nowMs();
    const heldDuration = normalizeRelativeMs(cancelAt - input.startAt);
    if (input.timer) {
      window.clearTimeout(input.timer);
    }
    recordInputEdge({
      type: "input_cancelled",
      input_source: input.inputSource,
      relative_ms: heldDuration,
      semantic: "cancelled"
    });
    const cancel = traceBeat("input_cancelled", {
      event: "input_cancelled",
      input_source: input.inputSource,
      target_id: "pullButton",
      held_duration_ms: heldDuration,
      commit_classification: "cancelled",
      cadence_band: "input_hold"
    });
    game.input = null;
    return cancel;
  }

  function resetRun(seedText) {
    game.seed = String(seedText || els.seedInput.value || "0");
    game.rng = makeRng(game.seed);
    game.tickets = STARTING_TICKETS;
    game.pity = 0;
    game.inventory = {};
    game.input = null;
    game.inputRecord = makeInputRecord(game.seed);
    window.__lastTrace = [];
    els.seedInput.value = game.seed;
    els.latestResult.textContent = "No committed item yet.";
    els.messageWindow.textContent = "Ready.";
    els.messageWindow.dataset.messageState = "idle";
    clearRefusalFlash();
    updateInputDom("idle");
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

  async function runPull(options) {
    const opts = options || {};
    if (game.busy) {
      return window.__lastTrace;
    }

    game.busy = true;
    setControlsEnabled(false);
    if (!opts.preserveTrace) {
      window.__lastTrace = [];
    }

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
      cost: PULL_COST,
      affordability: hasTicketsForPull(),
      input_source: opts.input_source || "programmatic",
      input_classification: opts.input_classification || "programmatic",
      held_duration_ms: opts.held_duration_ms || 0,
      inventory_before: snapshotInventory()
    });
    await waitForCadence(audit);

    if (!hasTicketsForPull()) {
      const refused = traceBeat("pull_refused", {
        event: "pull_refused",
        guard_result: "insufficient_tickets",
        cost: PULL_COST,
        ticket_balance: game.tickets,
        draw_commit: "blocked",
        inventory_mutation: "blocked",
        input_source: opts.input_source || "programmatic",
        input_classification: opts.input_classification || "programmatic",
        held_duration_ms: opts.held_duration_ms || 0
      });
      await waitForCadence(refused);

      const guard = traceBeat("ticket_guard", {
        event: "pull_blocked",
        guard_result: "insufficient_tickets",
        cost: PULL_COST,
        ticket_balance: game.tickets,
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
    game.tickets -= PULL_COST;
    context.committedDraw = true;
    const commit = traceBeat("draw_commit", {
      event: "draw_committed",
      inventory_mutation: "ticket_consumed",
      seed: game.seed,
      cost: PULL_COST,
      input_source: opts.input_source || "programmatic",
      input_classification: opts.input_classification || "programmatic",
      held_duration_ms: opts.held_duration_ms || 0
    });
    await waitForCadence(commit);

    await narrateMessage("Ticket accepted. Capsule lock engaged.", "draw_commit");

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

    await narrateMessage("The capsule rattles. Rarity signal incoming.", "anticipation");

    const rarity = traceBeat("rarity_signal", {
      event: "rarity_signaled",
      rarity: context.draw.rarity
    });
    await waitForCadence(rarity);

    await narrateMessage(`${context.draw.rarity.toUpperCase()} signal locked.`, "rarity_signal");

    const identity = traceBeat("item_identity", {
      event: "item_identified",
      rarity: context.draw.rarity,
      item_id: context.draw.id,
      item_name: context.draw.name,
      item_image: context.draw.image
    });
    await waitForCadence(identity);

    await narrateMessage(`${context.draw.name} leaves the capsule.`, "item_identity");

    const inventory = commitInventory(context);
    await waitForCadence(inventory);

    const recovery = traceBeat("recovery", { event: "control_recovered" });
    await waitForCadence(recovery);

    setControlsEnabled(true);
    game.busy = false;
    return window.__lastTrace;
  }

  async function replayInputRecord(record) {
    const replayRecord = record || game.replaySnapshot;
    if (game.busy) {
      return traceBeat("input_replay_refused", {
        event: "input_replay_refused",
        guard_result: "busy",
        reason: "Replay refused while the runner is busy."
      });
    }
    if (!validateRecord(replayRecord)) {
      return traceBeat("input_replay_refused", {
        event: "input_replay_refused",
        guard_result: "malformed_record",
        reason: "Replay refused because the input record is malformed."
      });
    }

    resetRun(replayRecord.seed);
    window.__lastTrace = [];
    game.replaying = true;
    const started = traceBeat("input_replay_started", {
      event: "input_replay_started",
      guard_result: "accepted",
      seed: replayRecord.seed,
      event_count: replayRecord.events.length
    });
    await waitForCadence(started);

    const events = replayRecord.events
      .map((event) => Object.assign({}, event, { relative_ms: normalizeRelativeMs(event.relative_ms) }))
      .sort((left, right) => left.relative_ms - right.relative_ms);

    let lastRelative = 0;
    for (const event of events) {
      const delta = Math.max(0, event.relative_ms - lastRelative);
      if (!game.instantCadence && delta > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, delta));
      }
      lastRelative = event.relative_ms;

      if (event.type === "input_press_started") {
        startPullInput({
          inputSource: "replay",
          now: event.relative_ms
        });
      }
      if (event.type === "input_cancelled") {
        cancelPullInput({
          inputSource: "replay",
          now: event.relative_ms
        });
      }
      if (event.type === "input_released") {
        await releasePullInput({
          inputSource: "replay",
          now: event.relative_ms
        });
      }
    }

    const outcome = latestOutcomeFromTrace(window.__lastTrace);
    const finished = traceBeat("input_replay_finished", {
      event: "input_replay_finished",
      guard_result: "completed",
      seed: replayRecord.seed,
      outcome
    });
    await waitForCadence(finished);
    game.replaying = false;
    return window.__lastTrace;
  }

  async function simulatePullInput(options) {
    const opts = options || {};
    const holdMs = normalizeRelativeMs(opts.holdMs);
    const source = opts.inputSource || "test";
    startPullInput({ inputSource: source, now: 0 });
    return releasePullInput({ inputSource: source, now: holdMs });
  }

  function wireControls() {
    els.pullButton.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) {
        return;
      }
      event.preventDefault();
      startPullInput({ inputSource: "pointer" });
    });
    els.pullButton.addEventListener("pointerup", (event) => {
      event.preventDefault();
      releasePullInput({ inputSource: "pointer" });
    });
    els.pullButton.addEventListener("pointercancel", (event) => {
      event.preventDefault();
      cancelPullInput({ inputSource: "pointer" });
    });
    els.pullButton.addEventListener("keydown", (event) => {
      if (event.key !== " " && event.key !== "Enter") {
        return;
      }
      event.preventDefault();
      if (!game.input) {
        startPullInput({ inputSource: "keyboard" });
      }
    });
    els.pullButton.addEventListener("keyup", (event) => {
      if (event.key !== " " && event.key !== "Enter") {
        return;
      }
      event.preventDefault();
      releasePullInput({ inputSource: "keyboard" });
    });
    els.resetButton.addEventListener("click", () => {
      resetRun(els.seedInput.value);
    });
    els.exportRecordButton.addEventListener("click", () => {
      exportInputRecord();
    });
    els.replayRecordButton.addEventListener("click", () => {
      replayInputRecord(game.replaySnapshot);
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
    renderReplayRecord(null);
  }

  window.__gachaTest = {
    pull: runPull,
    simulatePull: simulatePullInput,
    pressPull(options) {
      return startPullInput(options || { inputSource: "test" });
    },
    releasePull(options) {
      return releasePullInput(options || { inputSource: "test" });
    },
    cancelPull(options) {
      return cancelPullInput(options || { inputSource: "test" });
    },
    resetRun,
    setTickets(count) {
      game.tickets = Math.max(0, Number(count) || 0);
      renderEconomy();
    },
    setModes(modes) {
      const next = modes || {};
      if (Object.prototype.hasOwnProperty.call(next, "reducedMotion")) {
        els.reducedMotionToggle.checked = Boolean(next.reducedMotion);
      }
      if (Object.prototype.hasOwnProperty.call(next, "silent")) {
        els.silentToggle.checked = Boolean(next.silent);
      }
      if (Object.prototype.hasOwnProperty.call(next, "skip")) {
        els.skipToggle.checked = Boolean(next.skip);
      }
    },
    exportInputRecord,
    replayInputRecord,
    getInputRecord() {
      return game.inputRecord
        ? {
          format: game.inputRecord.format,
          seed: game.inputRecord.seed,
          started_at_ms: 0,
          events: game.inputRecord.events.map((event) => Object.assign({}, event))
        }
        : makeInputRecord(game.seed);
    },
    setInstantCadence(enabled) {
      game.instantCadence = Boolean(enabled);
    },
    getOutcome() {
      return latestOutcomeFromTrace(window.__lastTrace);
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
        data_rarity: els.app.dataset.rarity || null,
        input_state: els.app.dataset.inputState || null,
        message_state: els.messageWindow.dataset.messageState || null,
        pull_affordable: els.pullAffordableState.dataset.affordable,
        replay_state: els.replayStatus.dataset.replayState || null
      };
    },
    cadence: {
      bands: CADENCE_BANDS,
      values: CADENCE_MS,
      pull_cost: PULL_COST
    }
  };

  initialize();
}());
