# Client/Server Protocol

The server is **authoritative**. The client renders state and sends action
requests; it never decides legality itself — the server sends the exact set of
legal moves for the player who must act. Transport: plain HTTP for requests,
**HTTP long-polling** for state push (each response is a finite JSON body, so it
works through any proxy/CDN/tunnel — SSE is buffered by some edges). All bodies are JSON.

## HTTP endpoints

### `GET /api/config`
Response `200`: `{ "requirePassword": false }` — whether `/api/join` needs an
access code (set server-side via the `HEXARA_PASSWORD` env var). Fetch this on
the join screen to decide whether to show the access-code field.

### `GET /api/leaderboard`
Response `200`: `{ "leaders": [ { "name": "Ada", "wins": 7 }, ... ] }` — all-time
wins by player name (case-insensitive), highest first, persisted server-side.

### `POST /api/join`
Request: `{ "room": "<CODE>" | "", "name": "<string>", "password": "<string>"? }`
- Empty/missing `room` creates a new room and makes you the host.
- `password` is only required when the server has an access code configured.
Response `200`: `{ "room": "<CODE>", "playerId": "<id>", "token": "<secret>" }`
Response `400`: `{ "error": "<message>" }` (room full, already started, not found, wrong access code)

Persist `room`, `playerId`, `token` in `localStorage` so a refresh reconnects.

### `GET /api/poll?room=<CODE>&player=<id>&token=<secret>&since=<version>`
Long-poll for the next state. The server returns **immediately** if the room is
newer than `since`, otherwise it holds the request open until something changes
or ~25s elapses (a heartbeat). Response `200`:
```jsonc
{ "version": <int>, "payload": <Payload> }
```
- `<version>` is a per-room counter; pass it back as `since` on your next poll to
  get only newer states. Start with `since=0`.
- `<Payload>` is one of:
  - `{ "type": "lobby", "youId": "<id>", "lobby": <Lobby> }`
  - `{ "type": "state", "state": <GameState>, "legal": <Legal> }`
- Render the payload, then **immediately poll again** with the new `version`.
Response `404`: `{ "error": "...", "fatal": true }` — session/room gone; clear
storage and return to the join screen. On a network error, retry after a short delay.

### `POST /api/action`
Request: `{ "room", "player", "token", "action": <Action> }`
Response `200`: `{ "ok": true }`  ·  `400`: `{ "error": "<message>" }` (illegal move — show it)  ·  `403`/`404`: auth/room errors.

You do **not** read game state from the action response — wait for the next poll.

## `Lobby` object
```jsonc
{
  "code": "ABCD",
  "host": "<playerId>",
  "minPlayers": 2, "maxPlayers": 6,
  "palette": [ { "name": "red", "hex": "#c0392b" }, ... ],   // selectable colours
  "players": [
    { "id", "name", "color": "#hex", "isBot": false, "connected": true, "isHost": true }
  ],
  "config": { "rules": <Rules>, "map": <MapSpec> },          // the host's chosen settings
  "presets": [ { "id", "name", "description", "tiles": 19 }, ... ],  // selectable board presets
  "ruleBounds": { "victoryPoints": { "default": 10, "min": 3, "max": 30 }, ... }  // per-rule defaults & ranges
}
```

### `Rules` object (all optional; omitted keys use the default)
```jsonc
{
  "victoryPoints": 10,      // points to win (3–30)
  "discardThreshold": 7,    // a 7 forces discarding half above this hand size (2–40)
  "maxRoads": 15,           // per-player piece limits
  "maxSettlements": 5,
  "maxCities": 4,
  "bankPerResource": 19,    // cards of each resource in the bank (1–400)
  "beansPerResource": 20,   // casino: beans -> 1 resource card / dev card = half this (1–1000)
  "beansPerVictoryPoint": 200, // casino: beans <-> 1 victory point (1–100000)
  "devDeckMultiplier": 1    // scales the development deck (1 = the standard 25 cards; 1–20)
}
```

### `MapSpec` object — the board to play on
One of three content modes, plus optional ports/robber. Omit entirely for the
standard 19-hex island.
```jsonc
{
  "name": "My Map",
  "preset": "standard|small|large|huge|frontier",  // pick a built-in (overrides the rest)
  // -- content mode A: a regular hexagon of the given size --
  "radius": 2,                       // 1–5 (radius 2 = 19 hexes); randomly filled
  // -- content mode B: an arbitrary island shape, randomly filled --
  "axials": [ [q, r], ... ],
  // -- content mode C: a fully explicit layout --
  "tiles": [ { "q": 0, "r": 0, "terrain": "forest", "number": 8 }, ... ],
  // -- ports (optional): omit to auto-spread; [] for none; or a type list --
  "ports": [ "3:1", "wheat", "ore", ... ],
  "robber": { "q": 0, "r": 0 }       // optional robber start (default: a desert)
}
```
Terrains: `forest|hills|pasture|fields|mountains|desert`. Number tokens are 2–12
(never 7); deserts have no number. Bad specs are rejected with a `400`/error.

## `GameState` object (per-viewer; opponents' hidden info is masked)
```jsonc
{
  "phase": "setup" | "main" | "ended",
  "winner": "<playerId>" | null,
  "currentPlayer": "<playerId>" | null,
  "yourId": "<playerId>",
  "rules": <Rules>,                        // active rule numbers (see Lobby)
  "mapName": "Standard Island" | null,     // the board's display name
  "order": ["<playerId>", ...],            // seating / turn order
  "setup": { "sub": "settlement" | "road" } | null,
  "dice": [d1, d2] | null,
  "diceRolled": true,
  "robberPhase": null | "discard" | "move",
  "robberHex": <hexId>,
  "pendingDiscards": { "<playerId>": <count> },   // who still owes discards
  "freeRoads": 0,                                  // remaining Road-Building roads
  "trade": { "from", "give": {res:n}, "receive": {res:n}, "to": <id>|null } | null,
  "bank": { "wood":19, "brick":19, "sheep":19, "wheat":19, "ore":19 },
  "devDeckCount": 25,
  "longestRoadOwner": "<id>"|null, "longestRoadLen": 0,
  "largestArmyOwner": "<id>"|null,
  "rollStats": { "2": 1, "3": 4, ... "12": 2 },   // dice-total histogram (for the Stats menu)
  "players": [ <PlayerView> ... ],
  "board": <Board>,
  "log": [ "<string>", ... ]               // newest last, ~60 lines
}
```

### `PlayerView`
```jsonc
{
  "id", "name", "color": "#hex",
  "vp": 3,                       // PUBLIC victory points (hidden VP cards excluded)
  "resourceCount": 7,            // total cards in hand (always visible)
  "devCount": 2,                 // total development cards (always visible)
  "playedKnights": 1,
  "builtSettlements": 3, "builtCities": 1, "builtRoads": 5,
  "roadsLeft": 10, "settlementsLeft": 2, "citiesLeft": 3,
  "hasLongestRoad": false, "hasLargestArmy": true,
  "ports": ["3:1", "wheat"],     // port types this player can use
  "beans": 40,                   // casino balance (public)
  "boughtVp": 1,                 // victory points bought with beans (public; counts toward the win)
  "gained": { "wood": 12, ... } | null,  // total resources accumulated — revealed only when the game ends
  // The following are non-null ONLY for yourId (your own private info):
  "resources": { "wood":1, "brick":0, "sheep":2, "wheat":1, "ore":0 } | null,
  "dev":    { "knight":1, "victory_point":0, "road_building":0, "year_of_plenty":0, "monopoly":0 } | null,  // playable
  "devNew": { ... } | null,      // bought this turn, not yet playable
  "casino": <Casino> | null      // your private casino state (beans, rates, blackjack table)
}
```

### `Casino` (self-only)
The blackjack table is **shared**: one shoe and `seen` list for the whole room
(communal card counting), with `seats` showing every player's hands. You still
play your own hand heads-up against the dealer.
```jsonc
{
  "beans": 40, "boughtVp": 1, "tips": 5,
  "minBet": 1, "beansPerResource": 20, "beansPerVp": 200, "beansPerDev": 10,
  "dev": { "knight": 2, ... }, "devNew": { ... },   // dev cards you can cash in
  // shared table:
  "shoeLeft": 300, "decks": 6, "seen": ["KS","7S",...],   // every dealt card, so counting works
  "mood": "happy|sad|excited|thankful|neutral|dealing",   // the 8-bit dealer's mood toward you
  "message": "Nicely done!",                              // the dealer's latest line
  "canBet": true,
  "seats": [ { "id","name","color","you","state","bet","net",
               "hands": [ { "cards":["AH","KD"], "value":21, "result":"blackjack", "bust":false } ] } ],
  // your own hand (null until you place a bet):
  "table": {
    "state": "idle|player|done",
    "dealer": ["9D","back"], "dealerValue": null,   // hole card is "back" until the hand resolves
    "hands": [ { "cards": ["KS","7S"], "bet": 5, "value": 17, "soft": false,
                 "bust": false, "blackjack": false, "result": null, "active": true } ],
    "active": 0, "net": 0,
    "canHit": true, "canStand": true, "canDouble": true, "canSplit": false, "canSurrender": true
  }
}
```

### `Board`
Coordinates are in an arbitrary layout space centred on the origin; **rescale
to your canvas** using `bounds`. IDs are stable.
```jsonc
{
  "hexes": [ { "id", "q", "r", "cx", "cy",
               "terrain": "forest|hills|pasture|fields|mountains|desert",
               "resource": "wood|brick|sheep|wheat|ore"|null,
               "number": 8|null, "hasRobber": false } ],   // 19 hexes
  "vertices": [ { "id", "x", "y",
                  "building": { "type": "settlement|city", "owner": "<id>" } | null,
                  "port": "3:1|wood|brick|sheep|wheat|ore" | null } ],  // 54
  "edges": [ { "id", "v1", "v2", "road": "<ownerId>" | null } ],         // 72
  "ports": [ { "type", "vertices": [vId, vId], "x", "y" } ],             // 9
  "bounds": { "minx", "miny", "maxx", "maxy" }
}
```
To draw a hex, build its polygon from the 6 nearest vertices, or from `(cx,cy)`
with the known pointy-top corner offsets (corner i at angle `60*i-30` degrees,
radius = distance to any of its vertices). Settlements/cities sit on vertices;
roads on edges (line from `v1` to `v2`).

## `Legal` object (what `yourId` may do right now)
```jsonc
{
  "yourTurn": true,
  "canRoll": false, "canEndTurn": true, "canBuyDev": true, "canTrade": true,
  "settlementSpots": [vId...],     // build a settlement here (already affordable + legal)
  "citySpots": [vId...],           // upgrade your settlement here
  "roadSpots": [eId...],           // build a road here (also used for free roads)
  "setupSettlementSpots": [vId...],// during setup
  "setupRoadSpots": [eId...],
  "playableDev": ["knight","road_building","year_of_plenty","monopoly"],
  "bankTrades": { "wood": 4, "wheat": 2 },   // resource -> best ratio you can give (only ones you can afford)
  "portRatios": { "wood": 4, "brick": 4, "sheep": 4, "wheat": 2, "ore": 4 },  // best ratio per resource (for multi-unit trades)
  "mustDiscard": 0,                // if > 0, you must POST a discard of this many
  "robberMove": false,             // you must move the robber
  "stealTargetsByHex": { "<hexId>": ["<playerId>", ...] },  // valid steal victims per hex
  "tradeRespond": false            // there is an open trade you may accept
}
```
Only non-empty spot lists should be highlighted/clickable. If a list is empty
the action isn't currently available (unaffordable, no legal spot, or not your turn).

## `Action` objects (the `action` field of `POST /api/action`)

Lobby (before the game starts):
- `{ "type": "lobby_set_name", "name": "<string>" }`
- `{ "type": "lobby_set_color", "color": "#hex" }`
- `{ "type": "lobby_add_bot" }`            (host only)
- `{ "type": "lobby_remove", "target": "<playerId>" }`  (host only)
- `{ "type": "lobby_set_config", "config": { "rules": <Rules>, "map": <MapSpec> } }`  (host only)
- `{ "type": "lobby_leave" }`
- `{ "type": "lobby_start" }`              (host only)

Leaving a game **in progress** (`lobby_leave` or `surrender`) hands your seat to a
bot — your pieces, resources and turn position are kept, the bot plays on, and
your session is invalidated (your next poll/action returns `404`/`403`, so the
client returns to the join screen).

Setup:
- `{ "type": "place_setup_settlement", "vertex": <vId> }`
- `{ "type": "place_setup_road", "edge": <eId> }`

Main turn:
- `{ "type": "roll_dice" }`
- `{ "type": "build_road", "edge": <eId> }`
- `{ "type": "build_settlement", "vertex": <vId> }`
- `{ "type": "build_city", "vertex": <vId> }`
- `{ "type": "buy_dev_card" }`
- `{ "type": "play_knight" }`                              // then a move_robber follows
- `{ "type": "play_road_building" }`                       // grants 2 free roads
- `{ "type": "play_year_of_plenty", "resources": ["ore","wheat"] }`
- `{ "type": "play_monopoly", "resource": "ore" }`
- `{ "type": "bank_trade", "give": "wood", "receive": "ore" }`   // single trade at your best ratio
- `{ "type": "bank_trade", "give": {"wheat":4}, "receive": {"ore":2} }`  // multi-unit: each give amount is a multiple of its rate; cards out == cards paid for
- `{ "type": "propose_trade", "give": {"wood":1}, "receive": {"ore":1}, "to": "<id>"|null }`
- `{ "type": "accept_trade" }`
- `{ "type": "cancel_trade" }`                             // proposer only
- `{ "type": "end_turn" }`

Casino / beans (allowed off-turn, once the game is underway):
- `{ "type": "convert_to_resources", "resources": {"ore":1} }`    // buy resource cards with beans
- `{ "type": "convert_dev_to_beans", "cards": {"knight":1} }`     // sell dev cards (beansPerResource/2 = 10 each)
- `{ "type": "buy_vp", "amount": 1 }`                             // beans -> victory points (counts toward the win)
- `{ "type": "sell_vp", "amount": 1 }`                            // sell beans-bought VP back for beans
- `{ "type": "bj_bet", "amount": 5 }`                             // deal a blackjack hand (>= minBet beans)
- `{ "type": "bj_hit" }` · `{ "type": "bj_stand" }` · `{ "type": "bj_double" }` · `{ "type": "bj_split" }`
- `{ "type": "bj_surrender" }`                                    // forfeit opening two cards; half the bet back
- `{ "type": "bj_tip", "amount": 2 }`                             // tip the dealer (a beans sink; the dealer cheers)

Robber / 7:
- `{ "type": "discard", "resources": { "wood": 2, "ore": 1 } }`   // sum == mustDiscard
- `{ "type": "move_robber", "hex": <hexId>, "target": "<playerId>"|null }`
```
```
