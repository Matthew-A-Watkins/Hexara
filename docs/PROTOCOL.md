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
  ]
}
```

## `GameState` object (per-viewer; opponents' hidden info is masked)
```jsonc
{
  "phase": "setup" | "main" | "ended",
  "winner": "<playerId>" | null,
  "currentPlayer": "<playerId>" | null,
  "yourId": "<playerId>",
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
  // The following are non-null ONLY for yourId (your own private info):
  "resources": { "wood":1, "brick":0, "sheep":2, "wheat":1, "ore":0 } | null,
  "dev":    { "knight":1, "victory_point":0, "road_building":0, "year_of_plenty":0, "monopoly":0 } | null,  // playable
  "devNew": { ... } | null       // bought this turn, not yet playable
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
  "bankTrades": { "wood": 4, "wheat": 2 },   // resource -> best ratio you can give
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
- `{ "type": "lobby_leave" }`
- `{ "type": "lobby_start" }`              (host only)

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
- `{ "type": "bank_trade", "give": "wood", "receive": "ore" }`   // uses your best ratio
- `{ "type": "propose_trade", "give": {"wood":1}, "receive": {"ore":1}, "to": "<id>"|null }`
- `{ "type": "accept_trade" }`
- `{ "type": "cancel_trade" }`                             // proposer only
- `{ "type": "end_turn" }`

Robber / 7:
- `{ "type": "discard", "resources": { "wood": 2, "ore": 1 } }`   // sum == mustDiscard
- `{ "type": "move_robber", "hex": <hexId>, "target": "<playerId>"|null }`
```
```
