# Art Asset Manifest & Style Guide

All art is **original** (the commercial game's artwork/branding is copyrighted —
do not copy it). The goal is a warm, classic European-board-game look that feels
premium and cohesive. Everything is **hand-authored SVG** (no external images,
no fonts beyond system/web-safe, no CDNs). Keep each file self-contained.

The game's brand name is **“HEXARA — Island Settlers”** (original name).

## Shared visual language
- Palette: parchment/cream backgrounds (`#f3e6c8`), deep sea blue (`#2b6c8f`/`#1d4f६b`→use `#1d4f6b`),
  warm wood (`#7a4a23`), ink outlines (`#3a2a18`). Resource accent colours:
  lumber `#2e7d32`, brick `#c1572e`, wool `#8bc34a`/sheep cream, grain `#e3b23c`, ore `#6b7785`.
- Style: soft gradients, gentle drop shadows, rounded corners, subtle texture via
  layered shapes/patterns. Clean and readable at small sizes. Thick friendly outlines.
- Consistent light source (top-left).

## Files to produce (exact paths & sizes)

### Terrain hex tiles — `client/assets/tiles/<terrain>.svg`  (viewBox 0 0 200 200)
Full-bleed square textures; the client clips them into the hexagon, so fill the
whole 200×200 area with the terrain scene (no transparent margins, no hex border).
- `forest.svg` — lumber: dense evergreen forest, layered pines, green tones.
- `hills.svg` — brick: rolling red-clay hills, a small clay pit.
- `pasture.svg` — wool: green rolling pasture with a sheep and a fence.
- `fields.svg` — grain: rows of golden wheat under a warm sky strip.
- `mountains.svg` — ore: grey rocky peaks with exposed ore veins.
- `desert.svg` — sandy dunes with a cactus / sun-bleached look.
- `gold.svg` — gold field (scenario): golden hills, a coin stack, sparkles.
- `beans.svg` — bean tile (Gamble mode): green felt, casino chips, a pile of beans.

### Resource cards — `client/assets/cards/res_<resource>.svg`  (viewBox 0 0 120 168)
Playing-card look: rounded rect, coloured border keyed to the resource, a central
emblem, and a small label at the bottom. `<resource>` ∈ wood, brick, sheep, wheat, ore.
(“wood”=lumber log, “brick”=clay brick, “sheep”=wool/sheep, “wheat”=grain sheaf, “ore”=ore chunk.)

### Development cards — `client/assets/cards/dev_<type>.svg`  (viewBox 0 0 120 168)
Same card frame, distinct from resource cards (e.g., a royal/indigo frame).
- `dev_knight.svg` — a knight/soldier helmet or figure.
- `dev_victory_point.svg` — a chalice/crown/monument (e.g., “Great Hall”, “Library”).
- `dev_road_building.svg` — crossed roads / paving.
- `dev_year_of_plenty.svg` — a bountiful harvest / cornucopia.
- `dev_monopoly.svg` — a grasping hand / market scales.
- `dev_back.svg` — a generic ornate card back (face-down dev cards).

### Dice — `client/assets/ui/die_<n>.svg`  (viewBox 0 0 64 64), n = 1..6
Rounded white die face with dark pips, soft shadow. Standard pip layouts.

### Port badges — `client/assets/ui/port_<type>.svg`  (viewBox 0 0 56 56)
A small round/wood badge a ship-sail motif. `<type>` ∈ generic, wood, brick, sheep, wheat, ore.
- `port_generic.svg` shows “3:1” and a “?”.
- the five resource ports show “2:1” plus the resource emblem.

### Pieces / icons — `client/assets/ui/`
- `robber.svg` (viewBox 0 0 48 64) — a hooded bandit pawn, single dark colour (not player-tinted).
- `icon_settlement.svg`, `icon_city.svg`, `icon_road.svg`, `icon_card.svg` (viewBox 0 0 40 40) —
  flat neutral glyphs for buttons (the board pieces themselves are drawn in code,
  tinted per player, so these are just button icons).

### Branding & backgrounds — `client/assets/ui/`
- `logo.svg` (viewBox 0 0 420 130) — the “HEXARA — Island Settlers” title lockup, ornate but legible.
- `sea.svg` (viewBox 0 0 256 256) — a seamless, tileable stylised ocean texture (gentle waves).
- `wood_panel.svg` (viewBox 0 0 256 256) — a tileable warm wood texture for side panels.

## Notes for the frontend
- Reference assets by these exact paths. The board pieces (settlement, city, road,
  number tokens) are **drawn procedurally** in canvas so they can be tinted per
  player and stay crisp; the SVGs above are for tiles, cards, dice, ports, the
  robber, button icons, branding and textures.
- **Always provide a code fallback** (flat colour + simple shape or text) if an
  asset fails to load, so a missing file never breaks the game.
