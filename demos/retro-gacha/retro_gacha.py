"""Deterministic RetroGamer gacha demo with machine-checkable traces."""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from typing import Any, Iterable


NORMAL_CADENCE_MS = {
    "pre_draw_audit": 0,
    "draw_commit": 0,
    "anticipation": 220,
    "rarity_signal": 180,
    "item_identity": 160,
    "inventory_commit": 90,
    "recovery": 120,
}
FAST_CADENCE_MS = {
    "skipped_anticipation": 20,
    "reduced_motion_reveal": 20,
    "silent_no_audio": 0,
}
NORMAL_REVEAL_MIN_MS = 80
NORMAL_REVEAL_MAX_MS = 450
FAST_REVEAL_MAX_MS = 50
TOTAL_AUTOMATED_REVEAL_MAX_MS = 1200
DRAW_MATERIAL_COST = 100

DEFAULT_ODDS = (
    {"rarity": "common", "weight": 70, "item": "Pixel Cap"},
    {"rarity": "rare", "weight": 25, "item": "CRT Blaster"},
    {"rarity": "epic", "weight": 4, "item": "Neon Cartridge"},
    {"rarity": "legendary", "weight": 1, "item": "Golden Joystick"},
)


@dataclass
class PlayerState:
    tickets: int = 1
    materials: int = 100
    pity: int = 0
    inventory: list[str] = field(default_factory=list)
    ticket_spent: bool = False
    material_spent: bool = False


class RetroGachaDemo:
    def __init__(
        self,
        *,
        seed: int,
        tickets: int = 1,
        materials: int = 100,
        odds: Iterable[dict[str, Any]] = DEFAULT_ODDS,
        reduced_motion: bool = False,
        silent: bool = False,
    ) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.state = PlayerState(tickets=tickets, materials=materials)
        self.odds = tuple(dict(row) for row in odds)
        self.reduced_motion = reduced_motion
        self.silent = silent
        self.trace: list[dict[str, Any]] = []
        self.audit_complete = False
        self.draw_committed = False
        self.rarity_token: str | None = None
        self.item_payload: dict[str, Any] | None = None

    def draw(self, *, skip: bool = False) -> list[dict[str, Any]]:
        self.pre_draw_audit()
        if not self._guard_draw_is_allowed():
            return self.trace
        self._commit_draw()
        self._resolve_rng()
        for state_name in self._reveal_plan(skip):
            self._emit_reveal_state(state_name)
        return self.trace

    def pre_draw_audit(self) -> None:
        odds_valid = self._odds_are_valid()
        affordable = self.state.tickets > 0 and self.state.materials >= DRAW_MATERIAL_COST
        retro_odds_record = self.odds
        gacha_odds_record = tuple(dict(row) for row in self.odds)
        pity_state_record = {"draws_since_rare": self.state.pity}
        ticket_balance_record = self.state.tickets
        material_balance_record = self.state.materials
        audit_payload = {
            "event": "pre_draw_audit",
            "state": "pre_draw_audit",
            "scene_command": "render_audit_panel",
            "cadence_ms": NORMAL_CADENCE_MS["pre_draw_audit"],
            "audio_mode": "silent" if self.silent else "audit_chime",
            "fallback_state": "no_audio_audit" if self.silent else "audio_audit",
            "odds_visible": True,
            "material_constraints_visible": True,
            "odds_table": retro_odds_record,
            "gacha_odds_record": gacha_odds_record,
            "odds_valid": odds_valid,
            "pity_state": pity_state_record,
            "ticket_balance": ticket_balance_record,
            "material_balance": material_balance_record,
            "material_cost": DRAW_MATERIAL_COST,
            "affordable": affordable,
            "inventory_before": tuple(self.state.inventory),
        }
        self._record(**audit_payload)
        self.audit_complete = True

    def _guard_draw_is_allowed(self) -> bool:
        if not self.audit_complete:
            self._guard_failure("missing_pre_draw_audit")
            return False
        if not self._odds_are_valid():
            self._guard_failure("invalid_odds")
            return False
        if self.state.ticket_spent or self.state.tickets < 1:
            self._guard_failure("insufficient_ticket")
            return False
        if self.state.materials < DRAW_MATERIAL_COST:
            self._guard_failure("insufficient_materials")
            return False
        return True

    def _commit_draw(self) -> None:
        if not self.audit_complete or self.draw_committed:
            self._guard_failure("draw_commit_guard")
            return
        self.state.tickets -= 1
        self.state.materials -= DRAW_MATERIAL_COST
        self.state.ticket_spent = True
        self.state.material_spent = True
        self.draw_committed = True
        self._record(
            event="draw_commit",
            state="draw_commit",
            scene_command="commit_seeded_draw",
            cadence_ms=NORMAL_CADENCE_MS["draw_commit"],
            audio_mode="silent" if self.silent else "commit_click",
            fallback_state="silent_draw_commit" if self.silent else "audio_draw_commit",
            seed=self.seed,
            ticket="ticket-1",
            ticket_consumed=True,
            material_consumed=DRAW_MATERIAL_COST,
            inventory_mutated=False,
        )

    def _resolve_rng(self) -> None:
        roll = self.rng.uniform(0, 100)
        cursor = 0.0
        selected = self.odds[-1]
        for row in self.odds:
            cursor += row["weight"]
            if roll < cursor:
                selected = row
                break
        self.rarity_token = selected["rarity"]
        self.item_payload = {
            "rarity": selected["rarity"],
            "name": selected["item"],
            "roll": round(roll, 6),
        }

    def _reveal_plan(self, skip: bool) -> list[str]:
        first_state = "skipped_anticipation" if skip else "anticipation"
        return [first_state, "rarity_signal", "item_identity", "inventory_commit", "recovery"]

    def _emit_reveal_state(self, state_name: str) -> None:
        if self.item_payload is None or self.rarity_token is None:
            self._guard_failure("missing_rarity_signal")
            return
        if state_name == "item_identity" and not self._seen_state("rarity_signal"):
            self._guard_failure("item_identity_without_rarity_signal")
            return
        if state_name == "inventory_commit":
            self._emit_inventory_commit()
            return
        self._emit_replacement_fallbacks(state_name, rarity=self.rarity_token)
        cadence_ms = FAST_CADENCE_MS["skipped_anticipation"] if state_name == "skipped_anticipation" else NORMAL_CADENCE_MS[state_name]
        self._record(
            event="skip_request" if state_name == "skipped_anticipation" else state_name,
            state=state_name,
            scene_command=f"render_{state_name}_{self.rarity_token}",
            cadence_ms=cadence_ms,
            audio_mode="silent" if self.silent else f"audio_{state_name}_{self.rarity_token}",
            fallback_state="silent_no_audio" if self.silent else "audio_enabled",
            motion_mode="reduced" if self.reduced_motion else "animated",
            rarity_token=self.rarity_token,
            copy_token=self.rarity_token,
            item_name=self.item_payload["name"] if state_name == "item_identity" else None,
            inventory_mutated=False,
        )

    def _emit_replacement_fallbacks(self, replaced_state: str, *, rarity: str) -> None:
        if self.reduced_motion and replaced_state in {"anticipation", "rarity_signal", "item_identity", "recovery"}:
            self._record(
                event="reduced_motion_request",
                state="reduced_motion_reveal",
                scene_command=f"render_reduced_{replaced_state}_{rarity}",
                cadence_ms=FAST_CADENCE_MS["reduced_motion_reveal"],
                audio_mode="silent" if self.silent else "reduced_motion_tick",
                fallback_state="reduced_motion_reveal",
                replaced_state=replaced_state,
                rarity_token=rarity,
                inventory_mutated=False,
            )
        if self.silent and replaced_state in {"anticipation", "rarity_signal", "item_identity", "inventory_commit", "recovery", "skipped_anticipation"}:
            self._record(
                event="silent_fallback",
                state="silent_no_audio",
                scene_command=f"render_silent_{replaced_state}_{rarity}",
                cadence_ms=FAST_CADENCE_MS["silent_no_audio"],
                audio_mode="silent",
                fallback_state="silent_no_audio",
                replaced_state=replaced_state,
                rarity_token=rarity,
                inventory_mutated=False,
            )

    def _emit_inventory_commit(self) -> None:
        if not self.draw_committed or not self._seen_state("draw_commit"):
            self._guard_failure("missing_draw_commit")
            return
        if self.item_payload is None or self.rarity_token is None or not self._seen_state("rarity_signal") or not self._seen_state("item_identity"):
            self._guard_failure("inventory_commit_without_item_identity")
            return
        self._emit_replacement_fallbacks("inventory_commit", rarity=self.rarity_token)
        self.state.inventory.append(self.item_payload["name"])
        self._record(
            event="inventory_commit",
            state="inventory_commit",
            scene_command=f"render_inventory_{self.rarity_token}",
            cadence_ms=NORMAL_CADENCE_MS["inventory_commit"],
            audio_mode="silent" if self.silent else f"audio_inventory_{self.rarity_token}",
            fallback_state="silent_inventory" if self.silent else "audio_inventory",
            rarity_token=self.rarity_token,
            item_name=self.item_payload["name"],
            inventory_after=tuple(self.state.inventory),
            inventory_mutated=True,
        )

    def _guard_failure(self, failed_constraint: str) -> None:
        self._record(
            event="guard_failure",
            state="guard_failure",
            scene_command=f"render_guard_failure_{failed_constraint}",
            cadence_ms=0,
            audio_mode="silent" if self.silent else "guard_buzz",
            fallback_state="silent_no_audio" if self.silent else "audio_guard",
            failed_constraint=failed_constraint,
            inventory_after=tuple(self.state.inventory),
            inventory_mutated=False,
        )

    def _odds_are_valid(self) -> bool:
        return bool(self.odds) and sum(row.get("weight", 0) for row in self.odds) == 100 and all(row.get("weight", 0) > 0 for row in self.odds)

    def _seen_state(self, state_name: str) -> bool:
        return any(event["state"] == state_name for event in self.trace)

    def _record(self, **event: Any) -> None:
        event.setdefault("scene_command", "render_noop")
        event.setdefault("cadence_ms", 0)
        event.setdefault("audio_mode", "silent" if self.silent else "audio_enabled")
        self.trace.append(event)


def trace_relevant_fields(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "event",
        "state",
        "scene_command",
        "cadence_ms",
        "audio_mode",
        "fallback_state",
        "failed_constraint",
        "rarity_token",
        "copy_token",
        "item_name",
        "inventory_mutated",
        "inventory_after",
        "affordable",
        "odds_valid",
    )
    return [{key: event[key] for key in keys if key in event} for event in trace]


def assert_cadence_bounds(trace: list[dict[str, Any]]) -> None:
    total = 0
    for event in trace:
        cadence = event["cadence_ms"]
        if event["state"] in {"anticipation", "rarity_signal", "item_identity", "inventory_commit", "recovery"}:
            assert NORMAL_REVEAL_MIN_MS <= cadence <= NORMAL_REVEAL_MAX_MS
            total += cadence
        if event["state"] in {"skipped_anticipation", "reduced_motion_reveal", "silent_no_audio"}:
            assert 0 <= cadence <= FAST_REVEAL_MAX_MS
            total += cadence
    assert total <= TOTAL_AUTOMATED_REVEAL_MAX_MS


def run_replay(
    *,
    seed: int | None,
    commands: Iterable[str],
    tickets: int = 1,
    materials: int = 100,
    invalid_odds: bool = False,
    skip: bool = False,
    reduced_motion: bool = False,
    silent: bool = False,
) -> dict[str, Any]:
    if seed is None:
        raise ValueError("seed is required for replay proof")
    command_list = tuple(commands)
    odds = ({"rarity": "common", "weight": 120, "item": "Broken Odds"},) if invalid_odds else DEFAULT_ODDS
    trace: list[dict[str, Any]] = [{"event": "replay_input", "state": "replay_input", "seed": seed, "commands": command_list, "scene_command": "stdout_json_trace", "cadence_ms": 0, "audio_mode": "silent" if silent else "audio_enabled", "fallback_flags": {"silent": silent, "reduced_motion": reduced_motion, "skip": skip}}]
    demo = RetroGachaDemo(seed=seed, tickets=tickets, materials=materials, odds=odds, reduced_motion=reduced_motion, silent=silent)
    if "draw" in command_list:
        trace.extend(demo.draw(skip=skip))
    assert_cadence_bounds(trace)
    relevant = trace_relevant_fields(trace)
    trace.append(
        {
            "event": "replay_proof",
            "state": "replay_proof",
            "scene_command": "stdout_json_trace",
            "cadence_ms": 0,
            "audio_mode": "silent" if silent else "audio_enabled",
            "cadence_sequence": [event["cadence_ms"] for event in trace],
            "trace_relevant_fields": relevant,
            "inventory_after": tuple(demo.state.inventory),
        }
    )
    return {"trace": trace, "trace_relevant_fields": trace_relevant_fields(trace)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic RetroGamer gacha replay.")
    parser.add_argument("mode", choices=("replay",))
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--commands", default="draw")
    parser.add_argument("--tickets", type=int, default=1)
    parser.add_argument("--materials", type=int, default=100)
    parser.add_argument("--invalid-odds", action="store_true")
    parser.add_argument("--skip", action="store_true")
    parser.add_argument("--reduced-motion", action="store_true")
    parser.add_argument("--silent", action="store_true")
    args = parser.parse_args()
    result = run_replay(
        seed=args.seed,
        commands=[command for command in args.commands.split(",") if command],
        tickets=args.tickets,
        materials=args.materials,
        invalid_odds=args.invalid_odds,
        skip=args.skip,
        reduced_motion=args.reduced_motion,
        silent=args.silent,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
