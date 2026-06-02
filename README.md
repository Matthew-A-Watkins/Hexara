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
3. Host clicks **Start Game** (2–6 players).
4. **Setup:** in snake-draft order, each player clicks the board to place 2
   settlements and 2 roads (your second settlement gives starting resources).
5. **Your turn:** Roll the dice, then build, trade, buy/play development cards,
   and End Turn. First to **10 victory points** on their turn wins.

The UI only lets you make legal moves — valid spots are highlighted and buttons
enable/disable based on what the rules allow right now.

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

## Architecture
- `engine/` — the authoritative rules engine (pure Python, no I/O):
  `constants.py`, `geometry.py` (board graph + pixel layout), `game.py` (state +
  rules), `views.py` (per-player serialization + legal-move computation),
  `bot.py` (computer opponent).
- `server/` — `app.py` (stdlib HTTP server: static files, SSE stream, action
  endpoint) and `manager.py` (rooms, lobby, the bot driver).
- `client/` — `index.html`, `styles.css`, `js/{net,board,ui,app}.js`, and
  original SVG `assets/`. The browser renders state and sends actions; it never
  decides rules — the **server is authoritative** and even sends the legal-move
  set, so clients can't desync.
- `docs/` — `PROTOCOL.md` (HTTP/SSE/action contract) and `ASSETS.md` (art manifest).

The server pushes a fresh, per-player view (opponents' hands/cards are hidden) on
every change via Server-Sent Events; clients POST actions back. Reconnect is
automatic (your session is stored in `localStorage`).

## Tests
```
py -3 tests\test_engine.py     # 25 rules tests (unit + full-game smoke)
py -3 tests\test_server.py     # end-to-end HTTP/SSE backend test
```
