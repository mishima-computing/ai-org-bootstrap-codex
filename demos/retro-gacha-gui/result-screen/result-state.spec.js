"use strict";

// Deterministic unit spec for the rendering-independent logic of the result-screen cartridge.
// Pixel-dependent behaviour (contrast/saliency/focus/flash on the composite) is verified separately
// by scripts/measure-result-screen.py against real screenshots; this spec covers the pure helpers.

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "result.js"), "utf8");
const context = { window: {}, console: console };
context.globalThis = context;
context.__RESULT_SPEC__ = true;
vm.createContext(context);
vm.runInContext(src, context, { filename: "result.js" });

const R = context.window.__resultInternals;
assert.ok(R, "result internals should be exposed in spec mode");

const tests = {
  testFormatGainBasic() {
    assert.strictEqual(R.formatGain(0), "+0");
    assert.strictEqual(R.formatGain(12400), "+12,400");
    assert.strictEqual(R.formatGain(8900), "+8,900");
  },
  testFormatGainClamp() {
    assert.strictEqual(R.formatGain(999999999), "+999,999,999");
    assert.strictEqual(R.formatGain(1000000000), "+999,999,999+");
    assert.strictEqual(R.formatGain(1500000000), "+999,999,999+");
  },
  testFormatGainNegative() {
    assert.strictEqual(R.formatGain(-300), "-300");
    assert.strictEqual(R.formatGain(-12400), "-12,400");
  },
  testRewardPartitionSmall() {
    const p = R.partitionRewards([1, 2, 3]);
    assert.strictEqual(p.slots.length, 3);
    assert.strictEqual(p.more, 0);
    assert.strictEqual(p.empty, false);
  },
  testRewardPartitionExactlyEight() {
    const p = R.partitionRewards([1, 2, 3, 4, 5, 6, 7, 8]);
    assert.strictEqual(p.slots.length, 8);
    assert.strictEqual(p.more, 0);
  },
  testRewardPartitionOverflow() {
    const p9 = R.partitionRewards(new Array(9).fill(1));
    assert.strictEqual(p9.slots.length, 7, "9 rewards => 7 slots");
    assert.strictEqual(p9.more, 2, "9 rewards => 他2件");
    const p20 = R.partitionRewards(new Array(20).fill(1));
    assert.strictEqual(p20.slots.length, 7, "20 rewards => 7 slots");
    assert.strictEqual(p20.more, 13, "20 rewards => 他13件");
  },
  testRewardPartitionEmpty() {
    const p = R.partitionRewards([]);
    assert.strictEqual(p.empty, true);
    assert.strictEqual(p.slots.length, 0);
  },
  testRarityTierMap() {
    assert.strictEqual(R.RARITY_TIER.legendary, "SSR");
    assert.strictEqual(R.RARITY_TIER.epic, "SR");
    assert.strictEqual(R.RARITY_TIER.rare, "R");
    assert.strictEqual(R.RARITY_TIER.common, "N");
  },
  testRevealBurstWithinBand() {
    assert.ok(R.REVEAL_BURST_MS <= 500, "reveal burst must stay within the <=500ms band");
    assert.ok(R.REVEAL_BURST_MS >= 100, "reveal burst must stay within the 100ms floor");
  }
};

let failures = 0;
for (const name of Object.keys(tests)) {
  try {
    tests[name]();
    console.log("PASS " + name);
  } catch (err) {
    failures += 1;
    console.error("FAIL " + name + ": " + err.message);
  }
}
if (failures > 0) {
  console.error(failures + " test(s) failed");
  process.exit(1);
}
