import unittest

from retro_gacha import (
    FAST_REVEAL_MAX_MS,
    NORMAL_REVEAL_MAX_MS,
    NORMAL_REVEAL_MIN_MS,
    TOTAL_AUTOMATED_REVEAL_MAX_MS,
    RetroGachaDemo,
    run_replay,
    trace_relevant_fields,
)


def states(trace):
    return [event["state"] for event in trace]


class RetroGachaTests(unittest.TestCase):
    def test_audit_precedes_draw_commit_and_records_odds_materials(self):
        trace = run_replay(seed=7, commands=["draw"])["trace"]
        self.assertLess(states(trace).index("pre_draw_audit"), states(trace).index("draw_commit"))
        audit = next(event for event in trace if event["event"] == "pre_draw_audit")
        self.assertTrue(audit["odds_visible"])
        self.assertTrue(audit["material_constraints_visible"])
        self.assertIn("pity_state", audit)
        self.assertEqual(audit["ticket_balance"], 1)
        self.assertEqual(audit["material_balance"], 100)
        self.assertTrue(audit["affordable"])

    def test_guard_failure_does_not_mutate_inventory_or_commit_draw(self):
        trace = run_replay(seed=7, commands=["draw"], tickets=0)["trace"]
        guard = next(event for event in trace if event["event"] == "guard_failure")
        self.assertEqual(guard["failed_constraint"], "insufficient_ticket")
        self.assertFalse(guard["inventory_mutated"])
        self.assertNotIn("draw_commit", states(trace))
        self.assertNotIn("inventory_commit", states(trace))

    def test_invalid_odds_guard_fires_before_commit(self):
        trace = run_replay(seed=7, commands=["draw"], invalid_odds=True)["trace"]
        guard = next(event for event in trace if event["event"] == "guard_failure")
        self.assertEqual(guard["failed_constraint"], "invalid_odds")
        self.assertNotIn("draw_commit", states(trace))

    def test_ordered_normal_reveal_transitions(self):
        trace = run_replay(seed=7, commands=["draw"])["trace"]
        ordered = ["pre_draw_audit", "anticipation", "rarity_signal", "item_identity", "recovery"]
        indexes = [states(trace).index(state) for state in ordered]
        self.assertEqual(indexes, sorted(indexes))

    def test_skip_path_keeps_audit_and_reveal(self):
        trace = run_replay(seed=7, commands=["draw"], skip=True)["trace"]
        observed = states(trace)
        self.assertLess(observed.index("pre_draw_audit"), observed.index("skipped_anticipation"))
        self.assertLess(observed.index("skipped_anticipation"), observed.index("rarity_signal"))
        self.assertLess(observed.index("rarity_signal"), observed.index("item_identity"))
        skip_event = next(event for event in trace if event["state"] == "skipped_anticipation")
        self.assertLessEqual(skip_event["cadence_ms"], FAST_REVEAL_MAX_MS)

    def test_reduced_motion_path_emits_replacement_states(self):
        trace = run_replay(seed=7, commands=["draw"], reduced_motion=True)["trace"]
        reduced = [event for event in trace if event["state"] == "reduced_motion_reveal"]
        self.assertGreaterEqual(len(reduced), 4)
        self.assertTrue(all(0 <= event["cadence_ms"] <= FAST_REVEAL_MAX_MS for event in reduced))
        self.assertLess(states(trace).index("pre_draw_audit"), states(trace).index("rarity_signal"))

    def test_silent_fallback_path_emits_no_audio_states(self):
        trace = run_replay(seed=7, commands=["draw"], silent=True)["trace"]
        silent_events = [event for event in trace if event["state"] == "silent_no_audio"]
        self.assertGreaterEqual(len(silent_events), 5)
        self.assertTrue(all(event["audio_mode"] == "silent" for event in silent_events))
        audit = next(event for event in trace if event["state"] == "pre_draw_audit")
        self.assertEqual(audit["fallback_state"], "no_audio_audit")

    def test_rarity_token_consistency(self):
        trace = run_replay(seed=7, commands=["draw"], silent=True)["trace"]
        rarity = next(event for event in trace if event["state"] == "rarity_signal")
        identity = next(event for event in trace if event["state"] == "item_identity")
        inventory = next(event for event in trace if event["state"] == "inventory_commit")
        self.assertEqual(rarity["rarity_token"], rarity["copy_token"])
        self.assertIn(rarity["rarity_token"], rarity["scene_command"])
        self.assertEqual(identity["rarity_token"], rarity["rarity_token"])
        self.assertEqual(inventory["rarity_token"], rarity["rarity_token"])
        self.assertIn(identity["item_name"], inventory["inventory_after"])

    def test_cadence_bounds(self):
        trace = run_replay(seed=7, commands=["draw"], skip=True, reduced_motion=True, silent=True)["trace"]
        total = 0
        for event in trace:
            cadence = event["cadence_ms"]
            if event["state"] in {"anticipation", "rarity_signal", "item_identity", "inventory_commit", "recovery"}:
                self.assertGreaterEqual(cadence, NORMAL_REVEAL_MIN_MS)
                self.assertLessEqual(cadence, NORMAL_REVEAL_MAX_MS)
                total += cadence
            if event["state"] in {"skipped_anticipation", "reduced_motion_reveal", "silent_no_audio"}:
                self.assertGreaterEqual(cadence, 0)
                self.assertLessEqual(cadence, FAST_REVEAL_MAX_MS)
                total += cadence
        self.assertLessEqual(total, TOTAL_AUTOMATED_REVEAL_MAX_MS)

    def test_seeded_replay_is_reproducible(self):
        first = run_replay(seed=20260614, commands=["draw"], skip=True, reduced_motion=True, silent=True)
        second = run_replay(seed=20260614, commands=["draw"], skip=True, reduced_motion=True, silent=True)
        self.assertEqual(first["trace_relevant_fields"], second["trace_relevant_fields"])

    def test_item_identity_and_inventory_guards(self):
        demo = RetroGachaDemo(seed=7)
        demo._emit_reveal_state("item_identity")
        self.assertEqual(demo.trace[-1]["failed_constraint"], "missing_rarity_signal")
        self.assertEqual(trace_relevant_fields(demo.trace)[-1]["inventory_mutated"], False)

    def test_inventory_commit_requires_successful_draw_commit(self):
        demo = RetroGachaDemo(seed=7)
        demo.rarity_token = "rare"
        demo.item_payload = {"rarity": "rare", "name": "CRT Blaster", "roll": 1.0}
        demo._record(event="rarity_signal", state="rarity_signal")
        demo._record(event="item_identity", state="item_identity")
        demo._emit_inventory_commit()
        self.assertEqual(demo.state.inventory, [])
        self.assertEqual(demo.trace[-1]["failed_constraint"], "missing_draw_commit")
        self.assertFalse(demo.trace[-1]["inventory_mutated"])


if __name__ == "__main__":
    unittest.main()
