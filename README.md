# HEXARA — Island Settlers

A complete, multiplayer **hex-tile settlement & trading** board game in the browser. Original art and
code; the rules of the base game are implemented faithfully (1:1). Built to run
with **zero installs** — pure Python standard library on the server, vanilla
JavaScript + Canvas in the browser.

> Note on IP: "CATAN" and its artwork/branding are trademarks/copyright of their
> owner. This project uses **original art and an original name** and only
> implements the (non-copyrightable) game mechanics.

## Requirements
- Python 3.10+ (developed on 3.12). Nothing else — no Node, no npm, no pip packages.

## Run it
```
cd C:\Users\mwatk\Documents\Hexara
py -3 -m server.app            # serves on http://localhost:8000
py -3 -m server.app 9000       # custom port
```
Open **http://localhost:8000** in a browser. To play with friends on the same
network, share `http://<your-LAN-ip>:8000`.

## Play with friends over the internet
One command, free, no accounts:
```
py -3 share.py
```
This serves the game and opens a free **Cloudflare Quick Tunnel**, printing a
public HTTPS link (`https://…trycloudflare.com`) to share. Friends just open the
link — no install. Press Ctrl+C to stop. See **`docs/PLAY_ONLINE.md`** for all
the options (and why this is the cheapest/safest), plus an optional access code
for invite-only games. (First run downloads the official `cloudflared` helper
into `./bin`.)

## How to play (quick start)
1. Enter a name and click **Join / Create Game** (leave the room code blank to
   create a new game). You'll get a 4-letter **room code**.
2. Friends open the same URL and enter that code to join. Or the host clicks
   **+ Add Bot** to fill seats with computer players.
3. **Game Setup (host):** in the lobby, choose a **map** (a built-in preset, a
   random board of any size, or your **own custom design**) and tune the rules —
   victory points to win, the 7-discard threshold, per-player piece limits and
   the bank size. See **Game setup & custom maps** below.
4. Host clicks **Start Game** (2–6 players).
5. **Setup:** in snake-draft order, each player clicks the board to place 2
   settlements and 2 roads (your second settlement gives starting resources).
6. **Your turn:** Roll the dice, then build, trade, buy/play development cards,
   and End Turn. First to the **victory-point target** (10 by default) on their
   turn wins.

The UI only lets you make legal moves — valid spots are highlighted and buttons
enable/disable based on what the rules allow right now. When a **7** is rolled,
everyone holding too many cards gets a discard window (with an always-available
prompt over the board and in the action bar), and the roller then moves the
robber. **Trading** is click-to-select: tap the cards you'll give and the cards
you want, then **Trade with Bank** (uses your best port ratio) or **Trade with
Players**.

## Game setup & custom maps
Everything below is chosen by the host in the lobby and shared live with the
table; non-hosts see a read-only summary.

- **Presets (Catan-style scenarios):** a broad catalog played with the base
  rules — sizes from Small Cove (7) up to Colossus (91), the Greater Catan
  (5–6) board, multi-island archipelagos (Heading for New Shores, The Four
  Islands, The Pirate Islands, Twin Continents), gold-rich boards (Golden
  Rivers, Treasure Isles), Through the Desert, and the bean-studded High
  Roller's Isle. New terrains: **gold fields** (yield a random resource) and
  **bean tiles** (pay beans in Gamble mode).
- **Any size:** pick *Random — choose size* and set a board radius (1–5).
- **Custom maps:** open the **Map Editor** to draw any board. A visual canvas
  (click hexes to paint terrain, stamp number tokens, place the robber, carve the
  island shape) stays in sync with a live **JSON** definition you can edit or
  paste directly. Ports auto-place **thematically** — each 2:1 resource port lands
  on the coast of a tile that produces it, generic 3:1 ports fill the rest, and the
  count scales with the number of land tiles so small islands aren't over-ported.
  They can also be turned off, or — with the **Port brushes** — pinned to exact
  coastal edges (3:1 or any 2:1; click the same edge again to remove).
- **Rule tuning:** victory points to win, the discard threshold, max
  roads/settlements/cities, the bank size, the casino bean rates, and the
  **development-deck size** (×25 cards).
- **Gamble mode:** a toggle that ties the casino into the main game — **bean
  tiles** pay out when their number rolls (5 per settlement, 10 per city),
  **tipping** the dealer warms the running count (+tip×0.01), and an optional
  sub-toggle lets you **cash development cards for beans** so you can alternate
  building and gambling.

Maps and rules are validated server-side, so an illegal board or value is
refused with a clear message before the game can start. The full format is in
**`docs/PROTOCOL.md`** (`MapSpec` and `Rules`).

## Table extras
- **Sound effects** — procedural cues (dice, building, the robber, your turn,
  victory, blackjack chips/cards) synthesized in the browser via Web Audio, so
  there are no audio files to ship. Toggle with the 🔊 button.
- **Auto-roll** — a checkbox that rolls the dice for you at the start of each of
  your turns. A red border **glows** when your turn begins.
- **Stats** — a 📊 panel with a live dice histogram (observed vs. theoretical
  odds per total) and, once the game ends, who accumulated the most of each
  resource.
- **Casino** — a 🎰 shared table you can play any time, even on others' turns,
  hosted by an animated **8-bit dealer** (switch between **Marv** and **Bella**)
  who deals the cards, reacts to your run (congratulates wins, sympathizes with
  losses), and **chats back**: ask about rates, your hand ("should I hit?" gets
  real basic-strategy advice), or just talk. The dealer has a **personality that
  warms as you tip** — Marv gets funnier, Bella gets more flirtatious — and a
  generous tipper may find the dealer **slipping a few beans back** into their
  pouch (a friendly rebate, never more than you've tipped). Replies come instantly
  from a rule-based brain in the engine (CPU-only, no GPU, no network); if the
  server is pointed at an external chat model (`HEXARA_CHAT_URL`), that line is
  quietly **upgraded in place** a moment later — and it still works fully with no
  model configured. The counting aid shows the live **Hi-Lo running & true count** with
  a per-rank tally, and rolling a 7 boots you from the table to go place the
  robber.
  Everyone shares one **6-deck shoe** with ~5-deck penetration, so card counting
  is real and communal — every dealt card is shown, and you can see the whole
  table's hands. 1-bean minimum (type any bet or hit **Max**), naturals pay 3:2,
  hit/stand/double/split/**surrender**. The **cashier** spends **beans** on
  resource cards (20 each) or **victory points** (which count toward winning),
  and cashes **development cards** in for beans (10 each = ½ a resource) — you can
  never go negative. End your turn or flip on **Auto-buy dev** (turns your
  resources into development cards) without leaving the table. All wagers and
  conversions are server-authoritative.
- **Surrender** — leave a game in progress and a bot takes over your seat, so the
  rest of the table plays on.
- **Leaderboard** — all-time wins by player name, persisted on the server and
  shown on the join screen and the victory screen.

## Rules implemented (base game, 1:1)
- 19-hex island, 9 harbors (4× generic 3:1, one 2:1 per resource), random board
  with the "no adjacent red 6/8" placement rule.
- Snake-draft setup; second settlement yields starting resources.
- Dice production with the official **bank-shortage rule** (if the bank can't pay
  everyone owed a resource, and more than one is owed, no one gets it).
- Robber on a 7: players over 7 cards discard half; mover steals 1 random card.
- Building: roads, settlements (distance rule + road connectivity), city upgrades;
  per-player piece limits (15 roads / 5 settlements / 4 cities).
- Trading: 4:1 bank, 3:1/2:1 ports, and player-to-player trades (only the active
  player trades, with anyone who agrees).
- Development cards (25-card deck): Knight, Victory Point, Road Building,
  Year of Plenty, Monopoly — one play per turn, none the turn it's bought.
- **Longest Road** (≥5, recomputed and broken by opposing settlements) and
  **Largest Army** (≥3 knights), each worth 2 VP, with correct "keep on tie" rules.
- Win at 10 VP (hidden VP cards count and are revealed on the winning turn).

All of the numbers above (victory target, the 7-discard threshold, piece limits
and the bank size) are **host-configurable per game**, and the 19-hex island can
be swapped for a preset, a random board of any size, or a fully custom design —
see **Game setup & custom maps**.

## Architecture
- `engine/` — the authoritative rules engine (pure Python, no I/O):
  `constants.py`, `geometry.py` (board graph + pixel layout for **any** hex
  field), `maps.py` (board presets, validation and map-spec resolution),
  `game.py` (state + rules, with host-configurable numbers), `views.py`
  (per-player serialization + legal-move computation), `bot.py` (computer
  opponent — board-agnostic).
- `server/` — `app.py` (stdlib HTTP server: static files, long-poll, action
  endpoint) and `manager.py` (rooms, lobby + game-setup config, the bot driver).
- `client/` — `index.html`, `styles.css`, `js/{net,board,ui,editor,app}.js`
  (`editor.js` is the in-lobby custom map editor), and original SVG `assets/`.
  The browser renders state and sends actions; it never decides rules — the
  **server is authoritative** and even sends the legal-move set, so clients
  can't desync.
- `docs/` — `PROTOCOL.md` (HTTP/SSE/action contract) and `ASSETS.md` (art manifest).

The server pushes a fresh, per-player view (opponents' hands/cards are hidden) on
every change via Server-Sent Events; clients POST actions back. Reconnect is
automatic (your session is stored in `localStorage`).

## Tests
```
py -3 tests\test_engine.py     # 25 rules tests (unit + full-game smoke)
py -3 tests\test_server.py     # end-to-end HTTP/SSE backend test
```
