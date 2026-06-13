# Ecosystem Craft Cards

## Purpose

Ecosystem craft cards are pack-level implementation knowledge pointers selected from mechanical repository evidence only. They never infer design intent.

## Profile Card Format

Each card has exactly these frontmatter keys: `profile_id`, `scope`, `covers`, `freshness`, `supersede_trigger`, and `evidence_refs`.

Caps: evidence_refs cap: 6 pointers, separated by semicolons; body cap: 12 nonblank lines; no embedded research excerpts.

Basename contract: card filename stem must equal `profile_id`; renames are selection-breaking.

Selection: `detect-ecosystem-profiles.py` may select these cards from manifest, config, runtime, or dependency-pin facts only. The initial HTML/CSS route is `pin:tailwindcss`; plain-CSS or CDN Tailwind repos without a package pin are a declared knowledge gap, not a selector guess.

#32 pack/repo boundary: cards carry conservative implementation-craft constraints, not tutorials, product worldview, or lint implementation.
