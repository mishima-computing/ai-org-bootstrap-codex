---
profile_id: ui-retro-gamer
scope: Human-facing objectives that explicitly declare `ui-retro-gamer`.
covers: retro game-feel claims; event/state contracts; guards; render hooks; timing cadence; sound-or-silence fallback; replay proof.
freshness: Short horizon; re-check anchors before material product use or after six months.
supersede_trigger: Supersede on product test contradiction, platform accessibility conflict, or updated source-anchor review.
evidence_refs: anchor:genre-retro-game-experience#adarkroom-button-state; anchor:genre-retro-game-experience#celeste2-input-cadence; anchor:genre-retro-game-experience#easyrpg-game-interpreter; anchor:genre-retro-game-experience#easyrpg-window-message; anchor:genre-retro-game-experience#rpgjs-gui-spec; anchor:genre-retro-game-experience#tuxemon-input-recorder
---

Use: Apply only when the objective explicitly declares `ui-retro-gamer`; never infer it from nostalgia, pixels, RPG labels, or selector taste.
Admit: Retro/game-feel terms are admissible only when mapped to event, state/proof, guard, render or scene-command hook, timing, sound-or-silence, fallback, verification, gap, or unresolved status.
Require: Each kept term names the observable event trigger, state it proves, guard/failure path, render hook, cadence range, and silent or no-audio fallback.
Reject: If the objective cannot name observable events, states/proofs, renderer hooks, fallback, and verification evidence, reject or downgrade the RetroGamer claim.
Matrix: `punch/cozy/retro/RPG-like` -> event + state + render + timing; `game feel` -> guard + fallback + verification; unmapped terms -> gap/unresolved.
Timing: Input/message cadence must be bounded, replayable, or explicitly marked as a gap.
Verification: Prefer runner/replay/testability evidence: input trace, message cadence proof, GUI spec, manual proof object, or explicit gap.
Reference: Use `docs/uiux-knowledge/retro-game-experience-reference.md` as the longer handoff map from source-readable game experience patterns to contract obligations.
Boundary: Do not copy source, assets, dialogue, audio, maps, or commercial RPG canon; use anchors as evidence pointers only.
