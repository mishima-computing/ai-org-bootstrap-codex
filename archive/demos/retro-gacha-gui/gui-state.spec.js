"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = __dirname;

class FakeElement {
  constructor(id, tagName) {
    this.id = id || "";
    this.tagName = tagName || "div";
    this.dataset = {};
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.textContent = "";
    this.value = "";
    this.style = {
      values: {},
      setProperty: (name, value) => {
        this.style.values[name] = value;
      }
    };
  }

  addEventListener(type, handler) {
    this.listeners[type] = this.listeners[type] || [];
    this.listeners[type].push(handler);
  }

  dispatchEvent(event) {
    const handlers = this.listeners[event.type] || [];
    const nextEvent = Object.assign({
      preventDefault() {},
      target: this
    }, event);
    for (const handler of handlers) {
      handler(nextEvent);
    }
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = nodes;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "src") {
      this.src = String(value);
    }
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "src") {
      delete this.src;
    }
  }
}

function makeHarness() {
  const ids = [
    "app",
    "machine",
    "capsule",
    "itemArt",
    "rarityLabel",
    "statusText",
    "pullButton",
    "pullCostLabel",
    "pullAffordableState",
    "pullRefusalFlash",
    "chargeLabel",
    "chargeMeter",
    "resetButton",
    "ticketCount",
    "pityStatus",
    "latestResult",
    "messageWindow",
    "collectionList",
    "seedInput",
    "exportRecordButton",
    "replayRecordButton",
    "replayStatus",
    "inputRecordOutput",
    "reducedMotionToggle",
    "silentToggle",
    "skipToggle"
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
  const document = {
    body: new FakeElement("body", "body"),
    getElementById(id) {
      return elements[id] || null;
    },
    createElement(tagName) {
      return new FakeElement("", tagName);
    }
  };
  const window = {
    location: { search: "" },
    matchMedia() {
      return { matches: false, addEventListener() {}, removeEventListener() {} };
    },
    setTimeout,
    clearTimeout,
    performance: { now: () => 0 }
  };
  const context = {
    console,
    document,
    window,
    URLSearchParams,
    Date,
    setTimeout,
    clearTimeout
  };
  vm.createContext(context);
  const code = fs.readFileSync(path.join(root, "gacha.js"), "utf8");
  vm.runInContext(code, context, { filename: "gacha.js" });
  context.window.__gachaTest.setInstantCadence(true);
  return { window: context.window, document, elements, test: context.window.__gachaTest };
}

function states(trace) {
  return trace.map((entry) => entry.state);
}

function assertSubsequence(actualStates, expectedStates) {
  let cursor = 0;
  for (const state of actualStates) {
    if (state === expectedStates[cursor]) {
      cursor += 1;
    }
    if (cursor === expectedStates.length) {
      return;
    }
  }
  assert.fail(`Missing ordered states: ${expectedStates.join(" -> ")} in ${actualStates.join(" -> ")}`);
}

function latest(trace, state) {
  return trace.filter((entry) => entry.state === state).pop();
}

async function testDomHooksAndConstants() {
  const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "style.css"), "utf8");
  for (const hook of [
    "pull_cost_label",
    "pull_refusal_flash",
    "charge_meter",
    "message_window",
    "replay_panel"
  ]) {
    assert(html.includes(`data-render-hook="${hook}"`), `missing render hook ${hook}`);
  }
  assert(html.includes("irasutoya.com"), "Irasutoya credit must remain visible");
  assert(css.includes("[data-input-state=\"held_ready\"]"), "held charge state must be styled");
  assert(css.includes("[data-message-state=\"message_typing\"]"), "message typing state must be styled");

  const { test } = makeHarness();
  const cadence = test.cadence;
  assert.equal(cadence.pull_cost, 1);
  assert.equal(cadence.values.anticipation, 420);
  assert.equal(cadence.values.rarity_signal, 260);
  assert.equal(cadence.values.item_identity, 300);
  assert(cadence.values.input_hold_ready >= cadence.bands.input_hold.min);
  assert(cadence.values.input_hold_ready <= cadence.bands.input_hold.max);
  assert(cadence.values.message_typing >= cadence.bands.message_char.min);
  assert(cadence.values.message_typing <= cadence.bands.message_char.max);
}

async function testNormalPullAndMessageCadence() {
  const { window, test } = makeHarness();
  test.resetRun("normal-seed");
  const trace = await test.simulatePull({ holdMs: 40, inputSource: "test" });
  assertSubsequence(states(trace), [
    "input_press_started",
    "input_released",
    "pre_draw_audit",
    "draw_commit",
    "message_typing",
    "message_pause",
    "message_advance",
    "anticipation",
    "rarity_signal",
    "item_identity",
    "inventory_commit",
    "recovery"
  ]);
  assert.equal(latest(trace, "input_released").commit_classification, "pressed");
  assert.equal(latest(trace, "pre_draw_audit").odds.legendary, 1);
  assert.equal(latest(trace, "pre_draw_audit").pity.limit, 10);
  assert(latest(trace, "item_identity").item_id, "normal pull should reveal an item");
  assert.equal(window.__lastTrace, trace);
}

async function testHeldChargePull() {
  const { test } = makeHarness();
  test.resetRun("held-seed");
  const holdMs = test.cadence.values.input_hold_ready + 40;
  const trace = await test.simulatePull({ holdMs, inputSource: "test" });
  assertSubsequence(states(trace), ["input_press_started", "input_hold_ready", "input_released", "draw_commit"]);
  assert.equal(latest(trace, "input_released").commit_classification, "held");
  assert.equal(latest(trace, "input_released").held_duration_ms, holdMs);
  assert.equal(latest(trace, "draw_commit").input_classification, "held");
}

async function testZeroTicketRefusal() {
  const { test, elements } = makeHarness();
  test.resetRun("refusal-seed");
  test.setTickets(0);
  const before = test.getState();
  const trace = await test.simulatePull({ holdMs: 20, inputSource: "test" });
  assertSubsequence(states(trace), ["pre_draw_audit", "pull_refused", "ticket_guard", "recovery"]);
  assert(!states(trace).includes("draw_commit"), "refusal must not draw_commit");
  assert.deepEqual(test.getState().inventory, before.inventory);
  assert.equal(test.getState().tickets, 0);
  assert.equal(latest(trace, "pull_refused").cost, 1);
  assert.equal(latest(trace, "pull_refused").ticket_balance, 0);
  assert.equal(elements.pullAffordableState.dataset.affordable, "false");
  assert.equal(elements.pullRefusalFlash.dataset.refused, "true");
}

async function testFallbackStatesAndMissingCommitGuard() {
  const { test } = makeHarness();
  test.resetRun("fallback-seed");
  test.setModes({ reducedMotion: true, silent: true, skip: true });
  const trace = await test.simulatePull({ holdMs: 10, inputSource: "test" });
  for (const state of ["silent_no_audio", "reduced_motion_reveal", "skipped_anticipation", "message_instant_skip"]) {
    assert(states(trace).includes(state), `missing fallback state ${state}`);
  }
  const guard = test.attemptMissingDrawCommit();
  assert.equal(guard.state, "missing_draw_commit");
  assert.equal(guard.guard_result, "missing_draw_commit");
}

async function testInputRecordReplayReproducibility() {
  const { test } = makeHarness();
  test.resetRun("replay-seed");
  const firstTrace = await test.simulatePull({
    holdMs: test.cadence.values.input_hold_ready + 25,
    inputSource: "test"
  });
  const firstOutcome = test.getOutcome();
  const record = test.exportInputRecord();
  assert.equal(record.seed, "replay-seed");
  assert.deepEqual(record.events.map((event) => event.type), ["input_press_started", "input_released"]);
  assert.equal(record.events[1].semantic, "held");
  assert(latest(firstTrace, "draw_commit").seed, "first run must remain seed-backed");

  const replayTrace = await test.replayInputRecord(record);
  const replayOutcome = test.getOutcome();
  assertSubsequence(states(replayTrace), [
    "input_replay_started",
    "input_press_started",
    "input_hold_ready",
    "input_released",
    "draw_commit",
    "item_identity",
    "inventory_commit",
    "input_replay_finished"
  ]);
  assert.equal(replayOutcome.item_id, firstOutcome.item_id);
  assert.equal(replayOutcome.rarity, firstOutcome.rarity);
  assert.equal(latest(replayTrace, "input_replay_finished").outcome.item_id, firstOutcome.item_id);
}

async function run() {
  const tests = [
    testDomHooksAndConstants,
    testNormalPullAndMessageCadence,
    testHeldChargePull,
    testZeroTicketRefusal,
    testFallbackStatesAndMissingCommitGuard,
    testInputRecordReplayReproducibility
  ];
  for (const testCase of tests) {
    await testCase();
    process.stdout.write(`PASS ${testCase.name}\n`);
  }
}

run().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
