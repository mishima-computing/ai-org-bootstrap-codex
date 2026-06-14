# Retro Game Experience Reference

This reference supports downstream implementation and review when an objective explicitly declares `ui-retro-gamer`. It is a handoff document, not a profile card: use it to form contract obligations and evidence requests, then enforce those obligations through `profile_applications`, `implementation_evidence`, and `scripts/profile-evidence-check.py`.

## Reading Rule

Retro game experience claims only survive when they become code-observable behavior. A term such as punch, anticipation, ceremony, charm, RPG-like, readable, or satisfying must map to a trigger, state/proof, guard/failure path, render hook, cadence, fallback, and verification artifact. If one of those cannot be named, mark the term as a gap instead of preserving it as prose.

## Reference Patterns

| Pattern | Source-readable pointer | What to extract | Contract obligation shape |
| --- | --- | --- | --- |
| Button has state, cost, and refusal | `anchor:genre-retro-game-experience#adarkroom-button-state` | Controls expose affordability/availability and refuse invalid action visibly. | Name the input event, enabled/disabled rule, refused path, and evidence file proving the guard. |
| Input cadence is a first-class state | `anchor:genre-retro-game-experience#celeste2-input-cadence` | Held/pressed distinctions shape when actions fire. | Name the input edge, repeat/hold behavior, and a replayable or manual proof. |
| Events execute through a runner | `anchor:genre-retro-game-experience#easyrpg-game-interpreter` | Game-feel changes are event/state transitions, not decorative labels. | Name the event command or state transition and its observable consequence. |
| Message boxes have progression cadence | `anchor:genre-retro-game-experience#easyrpg-window-message` | Text, pause, and advance states create readable rhythm. | Name message state, advance trigger, timing band, and fallback. |
| Event commands use declared vocabulary | `anchor:genre-retro-game-experience#easyrpg-event-command` | Scene behavior is contractible when events are named. | Require declared event/state terms in contract, not only aesthetic copy. |
| GUI behavior has tests or specs | `anchor:genre-retro-game-experience#rpgjs-gui-spec` | Menus and UI scenes need replayable expectations. | Require GUI proof, screenshot proof, or an explicit unavailable-test gap. |
| Input can be recorded or replayed | `anchor:genre-retro-game-experience#tuxemon-input-recorder` | Interaction traces keep feel review from becoming opinion-only. | Require trace, replay, deterministic fixture, or documented manual proof. |

## Code Contract Checklist

- Trigger: the exact user or system event that starts the feel moment.
- State/proof: the state that the visible/audio/text response proves.
- Guard: what prevents invalid action and how refusal is surfaced.
- Render hook: where the visual, message, scene, or UI state changes.
- Cadence: bounded timing, step order, or event sequence; avoid fixed magic constants without product justification.
- Fallback: no-audio, reduced-motion, and unavailable-render behavior.
- Verification: automated test, input trace, screenshot/manual proof object, or explicit gap.

## Example Obligation Forms

- `ui-retro-gamer`: pointerdown on a draw button changes visible control state before the draw result, and the implementation evidence cites the handler and CSS/state hook.
- `ui-retro-gamer`: a refused action uses a named guard path and visible refusal state, and the implementation evidence cites the guard branch plus a test or manual proof.
- `ui-gacha-genre`: reveal ceremony separates anticipation, rarity signal, item identity, and recovery, and the evidence names reduced-motion and skip/fallback behavior.

## Boundary

Use the pointers to ask better implementation questions. Do not copy source code, assets, dialogue, audio, maps, or commercial RPG canon. If a product needs exact feel calibration, create a target-specific contract and capture evidence rather than expanding this pack-level reference.
