# Retro Game Experience Genre Anchors

Scope: thin anchor index for source-readable retro game-experience evidence: input cadence, message cadence, event commands, render hooks, GUI proof, replay proof, and unresolved gaps; references are evidence pointers only.

## adarkroom-button-state
Pointer: https://github.com/doublespeakgames/adarkroom/blob/1fada4620b6c66bd07bf15a3f1eb8223df8bc1d7/script/Button.js | Date/version: commit 1fada4620b6c66bd07bf15a3f1eb8223df8bc1d7 checked 2026-06-13. | Scope note: pointer for button affordance, disabled/available state, resource guard, and feedback timing questions. | Local use boundary: cite only to require observable control state and guard proof; no source reuse. | Stable ID: #adarkroom-button-state

## celeste2-input-cadence
Pointer: https://github.com/ExOK/Celeste2/blob/f3820ba29c04e63813815c898dac60b18a3a8bd5/input.lua | Date/version: commit f3820ba29c04e63813815c898dac60b18a3a8bd5 checked 2026-06-13. | Scope note: pointer for input cadence, held/pressed distinction, and action-trigger vocabulary. | Local use boundary: cite only to require event timing and input-state evidence; no source reuse. | Stable ID: #celeste2-input-cadence

## easyrpg-game-interpreter
Pointer: https://github.com/EasyRPG/Player/blob/9a8e2ff633527412fdc7d6f29606e59d39488c32/src/game_interpreter.cpp | Date/version: commit 9a8e2ff633527412fdc7d6f29606e59d39488c32 checked 2026-06-13. | Scope note: pointer for event interpreter sequencing, guard progression, and scene-command execution evidence. | Local use boundary: cite only to require command/state traceability; no source reuse. | Stable ID: #easyrpg-game-interpreter

## easyrpg-window-message
Pointer: https://github.com/EasyRPG/Player/blob/9a8e2ff633527412fdc7d6f29606e59d39488c32/src/window_message.cpp | Date/version: commit 9a8e2ff633527412fdc7d6f29606e59d39488c32 checked 2026-06-13. | Scope note: pointer for message-window cadence, text progression, and render-state evidence. | Local use boundary: cite only to require visible message state and fallback proof; no source reuse. | Stable ID: #easyrpg-window-message

## easyrpg-event-command
Pointer: https://github.com/EasyRPG/liblcf/blob/666e6c023696d4a45a67dd9ba879dbff7b0f69f3/src/generated/lcf/rpg/eventcommand.h | Date/version: commit 666e6c023696d4a45a67dd9ba879dbff7b0f69f3 checked 2026-06-13. | Scope note: pointer for event-command naming, scene triggers, and state-machine proof vocabulary. | Local use boundary: cite only to require declared event/state terms in contracts; no source reuse. | Stable ID: #easyrpg-event-command

## rpgjs-gui-spec
Pointer: https://github.com/RSamaium/RPG-JS/blob/cb673804e22a18002b805c12fa85485ea7bb4b12/packages/server/tests/gui.spec.ts | Date/version: commit cb673804e22a18002b805c12fa85485ea7bb4b12 checked 2026-06-13. | Scope note: pointer for GUI test expectations around menus, scene state, and user-visible proof. | Local use boundary: cite only to require replayable GUI evidence or a declared gap; no source reuse. | Stable ID: #rpgjs-gui-spec

## tuxemon-input-recorder
Pointer: https://github.com/Tuxemon/Tuxemon/blob/c34a9c727129999671e4206ade7425cbb45745b4/tests/tuxemon/test_input_recorder.py | Date/version: commit c34a9c727129999671e4206ade7425cbb45745b4 checked 2026-06-13. | Scope note: pointer for input recording, replay expectations, and testable interaction traces. | Local use boundary: cite only to require runner or replay evidence, or an explicit unresolved gap; no source reuse. | Stable ID: #tuxemon-input-recorder
