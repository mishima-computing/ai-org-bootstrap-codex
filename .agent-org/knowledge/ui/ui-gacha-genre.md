---
profile_id: ui-gacha-genre
scope: Human-facing gacha, collection, draw, reward, or reveal flows.
covers: reveal ceremony; rarity language; anticipation beats; pity/odds disclosure pointers.
freshness: Short horizon; re-check before material product use or after six months.
supersede_trigger: Supersede on yatai Stage-A pilot contradiction or current market/research contradiction.
evidence_refs: anchor:genre-gacha#cesa-guideline-20160427; anchor:genre-gacha#joga-guideline-index; anchor:genre-gacha#yin-xiao-chi2022-rrm; anchor:motion#wcag22-motion-criteria; anchor:motion#apple-hig-motion; anchor:motion#nng-animation-duration
---

Cartridge: this card is `cartridge:gacha` under the `game-designer` console (`game-designer.md`); it specializes the console for gacha/reveal and never relaxes a console value class or the measured-on-render rule.

Fact: Reveal ceremony separates anticipation, rarity signal, and item identity; cite anchor:genre-gacha#yin-xiao-chi2022-rrm.

Visual register (the light grammar): a high-rarity reveal is a DARK THEATRE where light is the protagonist, not a bright lit room. The genre register is the difference between "correct" and "premium" and is emergent — the sum of per-proposition-correct parts does not guarantee it, so the owner taste-gate is the final judge (yatai post-mortem, issue #66: a bright correct office failed where a dark theatre passed, same assets). Observable obligations for the peak reveal frame:
- Stage is dark: corners crushed by a heavy vignette; scene colour is concentrated as a glow behind the prize, not a full-screen wash.
- Light radiates from the prize: a hot focal bloom plus god-rays emanating from behind it; the prize out-saliences the stage (S_prize > S_background, measured per the console derive-on-render rule and `cartridge:result-screen`).
- Rarity is unmistakable and legible over the bloom: rarity stars/signal and a holographic rarity frame read clearly against the bright focal area (do not let the bloom wash the rarity signal).
- One supporting cinematic accent is allowed (e.g. an anamorphic light streak); restraint elsewhere — chrome is quiet, light is loud.
- Art is staged, not just placed: rim-light/backlight and contrast make even modest art read as emerging from light; never present the prize flatly under even lighting.
Exemplar (static peak frame): `demos/retro-gacha-gui/gacha-reveal/` — produced by a Codex carrier reading this grammar alone (independent of the controller's hand version, 0/154 shared CSS declarations), controller-verified (prize salience 0.113 ≫ background 0.0001; all grammar obligations present in code), owner taste-gate preferred it over the controller's hand build. Evidence that the light grammar transfers to a carrier, not just the controller.
Rule: Rarity signaled before item; pre-draw audit record shows odds and material constraints visible before ceremony.
Language: Use rarity language consistently across copy, color, audio, motion, and inventory state.
Beats: Entrance, suspense, rarity confirmation, item reveal, and recovery carry skip and reduced-motion paths, citing anchor:motion#wcag22-motion-criteria and anchor:motion#apple-hig-motion.
Risk: Genre conventions stale quickly and vary by market; cite anchor:genre-gacha#joga-guideline-index.
Freshness: Treat as short-horizon guidance until a product Stage-A spec ratifies it.
Pointers: JP informative self-regulation, Yin & Xiao CHI 2022 RRM research, and federated motion anchors.
