# 出来上がった RFC を読む — 「ドラクエをつくって」(run14 promoted)

**Working Title**: ドラゴンクエスト本編再現ゲーム

**依頼原文**: ドラクエをつくって

## 問題・動機

Requester wants a complete user-facing game that faithfully reproduces the current ドラクエ / ドラゴンクエスト (Dragon Quest) mainline RPG identity, including its exploration, command battles, party growth, story cadence, and presentation signatures. 

## 成功の定義

Success means a complete playable Dragon Quest recreation from title and adventure-log flow through final boss and ending, with Dragon Quest-specific towns, NPC clue progression, command and status windows, party LV/HP/MP/gold visibility, turn-based battles, inventory, equipment, spells, skills, side activities, and persistent world-state changes. 

## ゴール（10本・検証法つき）

- **goal:1** [player] start or resume an adventure from the title flow
  - 検証:  — create a save, return to title, reload it, and confirm the restored world and party state match the saved state
- **goal:2** [player] complete an early town-to-dungeon progression loop using in-game guidance
  - 検証:  — run a fresh playthrough through the first town, route, dungeon, boss, and newly opened route using only on-screen information
- **goal:3** [player] inspect party condition and resources during exploration and battle
  - 検証:  — exercise exploration, menu, and battle states and assert required status and resource labels are present and update after damage, healing, s
- **goal:4** [player] resolve turn-based battles through commands
  - 検証:  — simulate battles covering each command category and assert party and enemy state changes match visible battle log and status-window updates
- **goal:5** [player] interact with world objects and services
  - 検証:  — invoke each interaction type twice where relevant and verify both first-use outcome and repeated-use state are visible and persisted
- **goal:6** [player] unlock a blocked route after obtaining the required key item or story flag
  - 検証:  — attempt the blocker before and after acquiring the required condition and compare feedback, visual state, log or inventory evidence, and tra
- **goal:7** [player] recover from danger, KO, and party defeat
  - 検証:  — force poison, KO, and full-party defeat scenarios, then perform recovery flows and assert visible state, costs, and restored values are corr
- **goal:8** [player] complete the full heroic adventure from opening through final boss and ending
  - 検証:  — perform a full playthrough from new game to ending and document that every required progression gate, boss, and ending state is reachable wi
- **goal:9** [reviewer] distinguish the delivered product from an unrelated or partial RPG
  - 検証:  — review required screenshots against the identity, scene, UI, readability, and no-placeholder expectations
- **goal:10** [reviewer] verify readable and accessible play across target viewports
  - 検証:  — capture representative states at target viewports and assert no blank screens, clipped critical labels, overlapping controls, or color-only 

## ハード制約（29本から抜粋10本）

- The deliverable must be a complete user-facing Dragon Quest mainline-inspired command RPG, not merely infrastructure or a generic game shell.
- The request must not be narrowed or reinterpreted as a prototype, unrelated fantasy RPG, MMO, roguelike, idle/action game, or spin-off-focused title.
- No specific numbered Dragon Quest installment is the sole target unless the requester later specifies one.
- The repository currently has no application architecture, modules, product APIs, schemas, assets, tests, or save data to preserve.
- AI Org metadata is not product source or runtime game data.
- Adventure-log save slots must support new-game and continue paths and restore full gameplay state for resumed play.
- The first town-to-overworld-to-dungeon-to-boss loop must be completable using only in-game clues and UI.
- Party condition and resources must be visible and synchronized across exploration, menus, and battle.
- Battle resolution must be a readable turn-based command system tied to canonical combat state.
- Common RPG interactions must provide immediate feedback and persistent consequences.

## 14 システム（それぞれの「ゲーム内での振る舞い」）

### App Shell, Scene Routing, and Input
Runs the React browser app as the single user-facing surface for title, adventure-log slots, exploration, menus, battle, credits, and post-clear scenes. Supports goal:1 and goal:10 with keyboard/gamepad-friendly confirm, cancel, menu, and directional input.

*固有名詞*: {"entities": ["Hero", "Party", "Adventure Log Keeper"], "content_items": ["Title Screen", "Travel Record Slots", "New Adventure", "Continue Adventure", "Credits"]}

### Original Content Registry and Localization
Defines original, text-authored locations, actors, monsters, items, spells, dialog, services, encounters, and ending content without relying on Square Enix assets or proprietary text. Supports goal:2, goal:8, goal:9, and goal:10.

*固有名詞*: {"entities": ["Mosslet", "Candle Imp", "Copper Drake", "Aster Bell Elder", "Suncrest Gatekeeper"], "content_items": ["Aster Bell Hamlet", "Sunvale Road", "Moonlit Well", "Old Observatory Door", "Suncrest Sigil", "Traveler Blade", "Medicinal Leaf", "C

### Logical Grid World and DOM/SVG Exploration
Uses a logical grid for maps, occupancy, collision, facing, transitions, encounter zones, hazards, and route unlocks, rendered through retained HTML/SVG layers. Supports goal:2, goal:5, and goal:9.

*固有名詞*: {"entities": ["Hero", "Town NPC", "Chest", "Locked Door", "Stairway", "Well Entrance"], "content_items": ["Aster Bell Hamlet Map", "Sunvale Road Map", "Moonlit Well Map", "Old Observatory Door Tile", "Poison Marsh Tile"]}

### NPC Dialogue, Clues, and Objective Log
Keeps the hero player-inhabited while NPCs, party remarks, and objective entries carry exposition, clue progression, and post-flag dialog changes. Supports goal:2 and goal:6.

*固有名詞*: {"entities": ["Aster Bell Elder", "Well Watcher", "Gatekeeper", "Rescued Miner", "Quiet Hero"], "content_items": ["Copper Drake Rumor", "Moonlit Well Clue", "Suncrest Sigil Hint", "Next Objective Log", "Post-Boss Thanks Dialog"]}

### World Interactions and Services
Implements Talk, Search, Open, Shop, Inn, Save, cure/revive service, doors, stairs, chests, and searchable props as state-changing actions with repeat feedback. Supports goal:5 and goal:7.

*固有名詞*: {"entities": ["Shopkeeper", "Innkeeper", "Shrine Scribe", "Healer", "Chest", "Bookshelf", "Barrel"], "content_items": ["Traveler Shop", "Aster Bell Inn", "Shrine Save Desk", "Moonlit Well Chest", "Old Observatory Door"]}

### Party Status and Progression
Owns party members, levels, XP, HP, MP, stats, vocations, ailments, KO state, gold, next-level thresholds, and post-battle growth. Supports goal:3 and goal:7.

*固有名詞*: {"entities": ["Hero", "Companion Healer", "Companion Guard", "Companion Sage"], "content_items": ["Level Up Notice", "Next Level XP", "HP Danger State", "Poison State", "KO State", "Gold Purse"]}

### Inventory, Equipment, Spells, Skills, and Tactics
Provides item use, equipment changes, spell and skill casting, learned abilities, MP costs, unusable-state checks, and tactics settings for routine battle acceleration. Supports goal:3, goal:4, and goal:5.

*固有名詞*: {"entities": ["Hero", "Companion Healer", "Mosslet", "Candle Imp"], "content_items": ["Traveler Blade", "Medicinal Leaf", "Clearwater Tonic", "Mending Light", "Defend", "Tactics: Conserve MP"]}

### Turn-Based Command Battle FSM
Runs deterministic command battles through phases for command choice, target selection, action resolution, animation/log feedback, end-of-turn effects, victory, defeat, rewards, and level-ups. Supports goal:4.

*固有名詞*: {"entities": ["Hero", "Mosslet", "Candle Imp", "Copper Drake"], "content_items": ["Attack", "Spell", "Skill", "Defend", "Item", "Flee", "Tactics", "Battle Log", "Victory Rewards"]}

### Progression Graph, Gates, and World State
Binds quest flags, key items, boss defeats, NPC clues, route locks, unlocked transitions, world art changes, and full-adventure gate order through final boss and epilogue. Supports goal:2, goal:6, and goal:8.

*固有名詞*: {"entities": ["Gatekeeper", "Copper Drake", "Final Wyrm Sovereign"], "content_items": ["Suncrest Sigil", "Old Observatory Door", "Opened Observatory Route", "Restored Aster Bell", "Final Citadel Seal", "Clear Star"]}

### Failure, Recovery, and Safe Return
Handles low HP warnings, poison ticks, KO, full-party defeat, gold consequences, safe-point return, inn rest, cure, and revival. Supports goal:7.

*固有名詞*: {"entities": ["Healer", "Innkeeper", "Shrine Scribe", "KO Party Member"], "content_items": ["Low HP Warning", "Poison Cure", "Revival Rite", "Safe Return Point", "Wipeout Message"]}

### Adventure Log Save and Load
Persists complete playthrough snapshots in versioned browser save slots with slot metadata, import/export, validation, migration hooks, and complete replacement load semantics. Supports goal:1, goal:5, goal:6, and goal:8.

*固有名詞*: {"entities": ["Hero", "Party", "Shrine Scribe"], "content_items": ["Travel Record Slot", "Manual Save", "Continue", "Clear Save", "Exported Save"]}

### Framed DOM/SVG UI, Feedback, and Accessibility
Renders command, status, dialog, inventory, shop, objective, target, and notification windows as semantic DOM/SVG components with stable layout, readable Unicode text, visual equivalents for audio, and no overlap with action-critical game state. Supports goal:3, goal:4, goal:9, and goal:10.

*固有名詞*: {"entities": ["Hero", "Party", "Enemy Target", "NPC Speaker"], "content_items": ["Command Window", "Party Status Window", "Dialog Window", "Objective Window", "Target Highlight", "Status Icon Labels"]}

### Side Content and Review Ledgers
Tracks optional but persistent Dragon Quest-style completion content such as mini medals, arena ranks, rescued monsters, bestiary entries, side rewards, and related NPC acknowledgements. Supports goal:5, goal:8, and goal:9.

*固有名詞*: {"entities": ["Arena Clerk", "Medal Collector", "Rescued Mosslet", "Copper Drake"], "content_items": ["Mini Medal Ledger", "Arena Rank C", "Monster Rescue List", "Bestiary", "Side Reward Chest"]}

### Final Arc, Ending, and Post-Clear Continuity
Completes the main adventure with all required regions, late gates, final dungeon, final boss, epilogue, credits, clear-save state, and post-clear world updates. Supports goal:8 and goal:9.

*固有名詞*: {"entities": ["Hero", "Party", "Final Wyrm Sovereign", "Restored Townsfolk"], "content_items": ["Final Citadel", "Final Boss Battle", "Epilogue", "Credits", "Clear Save", "Post-Clear Aster Bell"]}

## First Playable（最初に遊べる瞬間）

{
 "player_actions": [
  "start a new adventure from an adventure-log title screen",
  "move the hero across a grid-based town, road, and dungeon rendered as SVG/HTML layers",
  "talk to NPCs for clues and objective updates",
  "open a chest, inspect party status, and use inventory items",
  "resolve command battles with attack, spell, item, defend, flee, and tactics choices",
  "buy supplies, rest, cure poison, and save at town services",
  "attempt a sealed observatory door before and after earning its required story flag",
  "return to title and continue from the restored save slot"
 ],
 "named_content": {
  "locations": [
   "Aster Bell Hamlet",
   "Sunvale Road",
   "Moonlit Well",
   "Old Observatory Door"
  ],
  "enemies": [
   "Mosslet",
   "Candle Imp",
   "Copper Drake"
  ],
  "items_or_spells": [
   "Traveler Blade",
   "Medicinal Leaf",
   "Clearwater Tonic",
   "Suncrest Sigil",
   "Mending Light"
  ]
 },
 "win_or_progress_condition": "Defeat the Copper Drake in Moonlit Well, gain the Suncrest Sigil, unlock the Old Observatory Door, and verify the opened route plus party state survive save and reload."
} 

## Patch Plan

### first_playable
{
 "player_can": [
  "launch a React/TypeScript browser app from an empty repo and see a nonblank title/adventure-log screen",
  "start New Adventure into Aster Bell Hamlet",
  "move a silent hero on a small DOM/SVG grid, with blocking tiles, facing, and a location banner",
  "talk to the Aster Bell Elder and receive a visible objective prompt",
  "open one chest, gain a Medicinal Leaf, and inspect party status, gold, inventory, gear, and next XP in framed windows",
  "enter a scripted Mosslet battle, choose Attack or Mending Light, see target focus, battle log text, HP/MP changes, victory rewards, and a level-up-ready XP total",
  "save to a versioned Travel Record Slot, return to title, and continue with location, party HP/MP, gold, inventory, objective, and opened chest restored"
 ],
 "named_content": {
  "locations": [
   "Title Screen",
   "Travel Record Slots",
   "Aster Bell Hamle

### follow_ups
- {'adds': 'Complete the first town-to-road-to-dungeon progression loop: Sunvale Road traversal, Moonlit Well map, encounter zones, multi-step NPC clues, objectiv
- {'adds': 'Expand command combat from the first battle into the required command set: Attack, Spell, Skill, Defend, Item, Flee, and Tactics, with target selectio
- {'adds': 'Add common RPG interactions and services with persistent consequences: shop purchases, inn rest, shrine save service, healer cure, searchable props, d
- {'adds': 'Fill out the versioned adventure-log save envelope, storage adapter, validation, migration hook, and import/export path so the full early-loop state r
- {'adds': 'Implement danger, failure, and recovery: low HP warnings, poison ticks, KO, full-party defeat, safe return to last shrine, gold consequence, inn rest,
- {'adds': 'Extend the progression graph beyond the first gate with additional regions, route locks, party growth milestones, companion joins, boss flags, map mar
- {'adds': 'Add optional but persistent completion ledgers: mini medals, arena ranks, rescued monsters, bestiary defeated counts, reward claims, and NPC acknowled
- {'adds': 'Complete the final arc: late gates, final dungeon, final boss phases, epilogue, credits, clear-save marker, post-clear continue, restored-world NPC di

## リスク（7本）

- **risk:full_adventure_scope_underestimated**: The selected implementation spans 14 systems from an empty repo, and the full final-boss-to-post-clear arc is still content-heavy. This threatens goal:8 and the decision to deliver a complete mainline
- **risk:save_schema_content_drift**: Versioned saves must preserve many fields while authored content evolves. Renamed map IDs, item IDs, quest flags, or enemy IDs could make continue paths lossy or invalid, threatening goal:1 and goal:8
- **risk:viewport_matrix_not_concrete**: The approach requires desktop/mobile readability, but the exact target viewport sizes are not yet concretely named in the patch plan. Without that, goal:10 tests can pass too narrowly or miss real lay
- **risk:unicode_text_fit**: Japanese-origin naming and English-capable menus can produce longer or taller strings than early English fixtures reveal. Clipped labels or broken wrapping would threaten goal:10 and status/dialog rea
- **risk:focus_and_input_state_drift**: React scene routing, modal windows, keyboard/gamepad input, and battle target selection can drift if focus ownership is not centralized. This threatens goal:1, goal:4, and goal:10 through trapped focu
- **risk:progression_graph_authoring_errors**: NPC clues, key items, boss flags, route locks, and post-boss world state can become inconsistent as content grows, creating invisible gates or dead ends. This threatens goal:2, goal:6, and goal:8.
- **risk:browser_storage_reliability**: Browser storage can fail due to quota, private browsing, origin changes, or corrupted slot data. That threatens goal:1 if continue paths rely only on optimistic local storage behavior.

## UX 要件の実文（抜粋）

### experience_identity
- *named_reference*: ドラクエ / ドラゴンクエスト (Dragon Quest), Square Enix mainline RPG identity; grounding uses the official series definition, Dragon Quest VII Reimagined as the latest released mainline remake, the HD-2D Roto remakes, and Dragon Que
- *genre_conventions*: Japanese command-menu RPG with player-named hero, party adventure, towns, castles, overworld, dungeons, NPC clue navigation, treasure chests, visible doors, stairs, wells and locks, turn-based command battles, levels, HP
- *must_resemble*: Dragon Quest: bright heroic fantasy, comic NPC message cadence, Toriyama-derived character and monster silhouettes including Slime readability, orchestral adventure tone, framed command and status windows, readable menus

### presentation_model
- *camera_and_view*: Use modern Dragon Quest exploration presentation for fields, towns and dungeons, with readable character navigation and destination framing; battle scenes shift to a dedicated combat presentation with enemy lineup and pa
- *world_readability*: Towns, castles, shops, inns, churches, overworld routes, dungeons, stairs, doors, bridges, ships or other transport, hidden spots, chests, NPCs, monster encounters and objective landmarks must be visually identifiable be
- *ui_taxonomy_notes*: Use framed Dragon Quest command/status windows: title, load, adventure log, field commands such as Talk, Search, Items, Equipment, Spells and Status, battle commands such as Fight, Attack, Spells, Skills, Defend, Items, 

### progression_legibility
- *current_goal_visibility*: The current main objective is recoverable through NPC hints, party chat or objective log, map markers for known destinations, and visible world changes; progression must not depend on invisible flags alone.
- *locked_state_feedback*: Attempting a locked door, blocked route or sealed object yields text feedback naming the missing key, condition or clue source when narratively known.
- *unlocked_state_feedback*: Unlocked routes show changed art, animation, sound-independent confirmation or dialog acknowledgement and allow immediate use or transition.

