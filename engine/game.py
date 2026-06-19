"""Authoritative game state and rules.

A single ``Game`` instance is the one source of truth for a match. The server
calls :meth:`Game.apply` with a player's action; the engine validates it
against the official base-game rules, mutates state, and the server then
broadcasts a fresh per-player view (with hidden information masked) plus the
set of currently legal actions for the player who must act.

Nothing in here knows about networking or rendering.
"""

import random

from . import casino
from . import constants as C
from . import dealer_chat
from . import maps


class GameError(Exception):
    """Raised when an action is illegal. The message is shown to the player."""


def _empty_hand():
    return {r: 0 for r in C.RESOURCES}


# Host-tunable numeric rules: key -> (default, min, max).
_RULE_SPECS = {
    "victoryPoints": (C.VICTORY_POINTS_TO_WIN, 3, 30),
    "discardThreshold": (C.ROBBER_DISCARD_LIMIT, 2, 40),
    "maxRoads": (C.MAX_ROADS, 1, 60),
    "maxSettlements": (C.MAX_SETTLEMENTS, 1, 40),
    "maxCities": (C.MAX_CITIES, 0, 40),
    "bankPerResource": (C.BANK_PER_RESOURCE, 1, 400),
    "beansPerResource": (C.BEANS_PER_RESOURCE, 1, 1000),
    "beansPerVictoryPoint": (C.BEANS_PER_VP, 1, 100000),
    "devDeckMultiplier": (1, 1, 20),   # 1 = the standard 25-card deck
    "gambleMode": (0, 0, 1),           # boolean: casino/main-game integration
    "gambleDevForBeans": (0, 0, 1),    # boolean sub-option: cash dev cards for beans
}


def normalize_rules(rules):
    """Validate and fill in rule overrides; returns a complete rules dict.

    Raises :class:`GameError` with a friendly message on a bad value."""
    rules = rules or {}
    out = {}
    for key, (default, lo, hi) in _RULE_SPECS.items():
        v = rules.get(key, default)
        if v is None or v == "":
            v = default
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise GameError("Setting '%s' must be a whole number." % key)
        if not (lo <= v <= hi):
            raise GameError("Setting '%s' must be between %d and %d." % (key, lo, hi))
        out[key] = v
    return out


def rule_bounds():
    """Defaults and ranges for each rule, for the lobby's settings editor."""
    return {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in _RULE_SPECS.items()}


def validate_config(config):
    """Validate a whole {rules, map} config for the lobby. Returns the
    normalized config; raises :class:`GameError` on any problem."""
    config = config or {}
    rules = normalize_rules(config.get("rules") or {})
    try:
        mp = maps.validate(config.get("map") or {})
    except maps.MapError as e:
        raise GameError(str(e))
    return {"rules": rules, "map": mp}


class Game:
    def __init__(self, players, config=None, seed=None):
        """players: list of {"id","name","color"} in seating (turn) order.

        config (optional): {"rules": {...}, "map": {...}}. Rules override the
        base-game numbers (victory points to win, the 7-discard threshold, the
        per-player piece limits and the bank size). The map spec selects/defines
        the board (see geometry.build_map). Both default to the standard game.
        """
        if not (C.MIN_PLAYERS <= len(players) <= C.MAX_PLAYERS):
            raise GameError("Need %d-%d players" % (C.MIN_PLAYERS, C.MAX_PLAYERS))
        self.rng = random.Random(seed)
        config = config or {}
        self._apply_rules(config.get("rules") or {})

        self.order = [p["id"] for p in players]
        self.players = {}
        for p in players:
            self.players[p["id"]] = {
                "id": p["id"], "name": p["name"], "color": p["color"],
                "resources": _empty_hand(),
                "dev": {k: 0 for k in C.DEV_CARD_COUNTS},      # playable
                "dev_new": {k: 0 for k in C.DEV_CARD_COUNTS},  # bought this turn
                "played_knights": 0,
                "gained": _empty_hand(),   # cumulative resources gained (for stats)
                "beans": 0,                # gambling currency (never negative)
                "bought_vp": 0,            # victory points purchased with beans
                "bj": None,                # blackjack table state (lazy)
                "bj_tipped": 0,            # total beans this player has tipped the dealer
                "bj_dealer_given": 0,      # beans the dealer has gifted back (capped by tipped)
            }

        self._make_board(config.get("map") or {})
        self._make_deck()
        self.bank = {r: self.bank_per_resource for r in C.RESOURCES}

        self.buildings = {}   # vid -> {"type","owner"}
        self.roads = {}       # eid -> owner pid

        # --- phase / turn state ---
        self.phase = "setup"
        self.current = 0  # index into self.order (used in main phase)
        # Snake-draft order: forward once, then back once.
        self.setup_queue = list(self.order) + list(reversed(self.order))
        self.setup_i = 0
        self.setup_sub = "settlement"  # then "road"
        self.last_setup_vertex = None

        self.dice = None
        self.dice_rolled = False
        self.robber_phase = None        # None | "discard" | "move"
        self.pending_discards = {}      # pid -> count still owed
        self.free_roads = 0             # from Road Building card
        self.dev_played_this_turn = False
        self.trade = None               # current open domestic trade offer

        self.longest_road_owner = None
        self.longest_road_len = 0
        self.largest_army_owner = None

        self.winner = None
        self.roll_counts = {n: 0 for n in range(2, 13)}  # dice-total histogram
        # Shared casino table: one shoe & seen-list for the whole room so card
        # counting is communal ("the table exists between all players").
        self.bj_shoe = None
        self.bj_seen = []
        self.bj_tips = 0          # total beans tipped to the dealer (a bean sink)
        self.bj_count_bias = 0.0  # gamble mode: tips nudge the running count
        self.bj_message = "Welcome to the table! Place a bet."
        self.bj_chat = []         # shared table talk: {"id","from","name","text","dealer"}
        self.bj_chat_next = 1     # chat message id sequence (for async LLM replies)
        # A SEPARATE RNG for chat flavour/grants: drawing from it never advances
        # self.rng, so chatter can't perturb the dice or the shoe. Derived from the
        # game seed (distinct stream) so seeded games stay reproducible.
        self.chat_rng = random.Random((seed ^ 0x5EED) if isinstance(seed, int) else seed)
        self.log = []
        self._log("Game started. Place your first settlement.")

    # ------------------------------------------------------------------ rules
    def _apply_rules(self, rules):
        """Validate and store the (optionally overridden) numeric rules.

        Everything the host can tune lives here; anything absent falls back to
        the canonical base-game value from ``constants``.
        """
        r = normalize_rules(rules)
        self.vp_to_win = r["victoryPoints"]
        self.discard_threshold = r["discardThreshold"]
        self.max_roads = r["maxRoads"]
        self.max_settlements = r["maxSettlements"]
        self.max_cities = r["maxCities"]
        self.bank_per_resource = r["bankPerResource"]
        self.beans_per_resource = r["beansPerResource"]
        self.beans_per_vp = r["beansPerVictoryPoint"]
        self.dev_deck_multiplier = r["devDeckMultiplier"]
        self.gamble_mode = bool(r["gambleMode"])
        self.gamble_dev_for_beans = bool(r["gambleDevForBeans"])

    def rules_view(self):
        """The active rule numbers, for serialization to clients."""
        return {
            "victoryPoints": self.vp_to_win,
            "discardThreshold": self.discard_threshold,
            "maxRoads": self.max_roads,
            "maxSettlements": self.max_settlements,
            "maxCities": self.max_cities,
            "bankPerResource": self.bank_per_resource,
            "beansPerResource": self.beans_per_resource,
            "beansPerVictoryPoint": self.beans_per_vp,
            "devDeckMultiplier": self.dev_deck_multiplier,
            "gambleMode": 1 if self.gamble_mode else 0,
            "gambleDevForBeans": 1 if self.gamble_dev_for_beans else 0,
        }

    # ------------------------------------------------------------------ setup
    def _make_board(self, map_spec):
        # Resolve the map spec (preset / size / custom design) into the board
        # graph plus the per-hex terrain & number assignment and robber start.
        self._hex_adj_cache = None
        try:
            board = maps.resolve(map_spec or {}, self.rng)
        except maps.MapError as e:
            raise GameError(str(e))
        self.geo = board["geo"]
        self.hexes = board["hexes"]
        self.robber_hex = board["robber_hex"]
        self.map_spec = board["spec"]

    def _red_numbers_ok(self, number_of):
        """No two red (6/8) tokens may sit on adjacent hexes."""
        adj = self._hex_adjacency()
        for hid, num in number_of.items():
            if num in C.RED_NUMBERS:
                for nb in adj[hid]:
                    if number_of.get(nb) in C.RED_NUMBERS:
                        return False
        return True

    def _hex_adjacency(self):
        if getattr(self, "_hex_adj_cache", None) is None:
            adj = {h["id"]: set() for h in self.geo["hexes"]}
            for e in self.geo["edges"]:
                if len(e["hexes"]) == 2:
                    a, b = e["hexes"]
                    adj[a].add(b)
                    adj[b].add(a)
            self._hex_adj_cache = adj
        return self._hex_adj_cache

    def _make_deck(self):
        deck = []
        for card, n in C.DEV_CARD_COUNTS.items():
            deck += [card] * (n * self.dev_deck_multiplier)
        self.rng.shuffle(deck)
        self.deck = deck

    # ----------------------------------------------------------- small helpers
    def _log(self, msg):
        self.log.append(msg)
        if len(self.log) > 200:
            self.log = self.log[-200:]

    def _name(self, pid):
        return self.players[pid]["name"]

    @property
    def current_pid(self):
        if self.phase == "setup":
            return self.setup_queue[self.setup_i]
        if self.phase == "main":
            return self.order[self.current]
        return None

    def _settlements(self, pid):
        return [v for v, b in self.buildings.items()
                if b["owner"] == pid and b["type"] == "settlement"]

    def _cities(self, pid):
        return [v for v, b in self.buildings.items()
                if b["owner"] == pid and b["type"] == "city"]

    def _roads_of(self, pid):
        return [e for e, o in self.roads.items() if o == pid]

    def _hand_size(self, pid):
        return sum(self.players[pid]["resources"].values())

    def _has(self, pid, cost):
        res = self.players[pid]["resources"]
        return all(res[r] >= n for r, n in cost.items())

    def _pay(self, pid, cost):
        res = self.players[pid]["resources"]
        for r, n in cost.items():
            res[r] -= n
            self.bank[r] += n

    def _gain(self, pid, resource, n=1):
        self.players[pid]["resources"][resource] += n
        if n > 0:
            self.players[pid]["gained"][resource] += n

    def _dev_count(self, pid):
        p = self.players[pid]
        return sum(p["dev"].values()) + sum(p["dev_new"].values())

    # ------------------------------------------------------------- validation
    def _require(self, cond, msg):
        if not cond:
            raise GameError(msg)

    def _require_turn(self, pid):
        self._require(pid == self.current_pid, "It is not your turn.")

    # -------------------------------------------------------- placement rules
    def _vertex_empty(self, vid):
        return vid not in self.buildings

    def _distance_ok(self, vid):
        """No adjacent vertex may carry a building (the distance rule)."""
        if not self._vertex_empty(vid):
            return False
        for nb in self.geo["vertices"][vid]["adjacent"]:
            if nb in self.buildings:
                return False
        return True

    def _road_connects(self, pid, eid):
        """A new road at eid is legal if it touches the player's own building,
        or their own road through a vertex not blocked by an opponent."""
        e = self.geo["edges"][eid]
        for v in (e["v1"], e["v2"]):
            b = self.buildings.get(v)
            if b and b["owner"] == pid:
                return True
            if b and b["owner"] != pid:
                continue  # opponent building blocks connection through here
            # own road incident to this open/own vertex?
            for ie in self.geo["vertices"][v]["edges"]:
                if ie != eid and self.roads.get(ie) == pid:
                    return True
        return False

    def _settlement_connected(self, pid, vid):
        """Main-phase settlements must touch one of the player's own roads."""
        for ie in self.geo["vertices"][vid]["edges"]:
            if self.roads.get(ie) == pid:
                return True
        return False

    def legal_settlement_spots(self, pid, setup=False):
        spots = []
        for v in self.geo["vertices"]:
            vid = v["id"]
            if not self._distance_ok(vid):
                continue
            if setup or self._settlement_connected(pid, vid):
                spots.append(vid)
        return spots

    def legal_road_spots(self, pid, setup=False):
        spots = []
        for e in self.geo["edges"]:
            eid = e["id"]
            if eid in self.roads:
                continue
            if setup:
                if self.last_setup_vertex in (e["v1"], e["v2"]):
                    spots.append(eid)
            elif self._road_connects(pid, eid):
                spots.append(eid)
        return spots

    # ============================================================= dispatch
    def apply(self, pid, action):
        if self.phase == "ended":
            raise GameError("The game is over.")
        handler = {
            "place_setup_settlement": self._h_setup_settlement,
            "place_setup_road": self._h_setup_road,
            "roll_dice": self._h_roll,
            "build_road": self._h_build_road,
            "build_settlement": self._h_build_settlement,
            "build_city": self._h_build_city,
            "buy_dev_card": self._h_buy_dev,
            "play_knight": self._h_play_knight,
            "play_road_building": self._h_play_road_building,
            "play_year_of_plenty": self._h_play_yop,
            "play_monopoly": self._h_play_monopoly,
            "move_robber": self._h_move_robber,
            "discard": self._h_discard,
            "bank_trade": self._h_bank_trade,
            "propose_trade": self._h_propose_trade,
            "accept_trade": self._h_accept_trade,
            "cancel_trade": self._h_cancel_trade,
            "end_turn": self._h_end_turn,
            # Casino / beans economy (allowed off-turn).
            "convert_to_beans": self._h_convert_to_beans,
            "convert_to_resources": self._h_convert_to_resources,
            "convert_dev_to_beans": self._h_convert_dev_to_beans,
            "buy_vp": self._h_buy_vp,
            "sell_vp": self._h_sell_vp,
            "bj_bet": self._h_bj_bet,
            "bj_hit": self._h_bj_hit,
            "bj_stand": self._h_bj_stand,
            "bj_double": self._h_bj_double,
            "bj_split": self._h_bj_split,
            "bj_surrender": self._h_bj_surrender,
            "bj_tip": self._h_bj_tip,
            "bj_chat": self._h_bj_chat,
        }.get(action.get("type"))
        self._require(handler is not None, "Unknown action: %r" % action.get("type"))
        result = handler(pid, action)
        self._check_win(pid)
        return result  # most handlers return None; bj_chat returns its reply info

    # ------------------------------------------------------------- setup phase
    def _h_setup_settlement(self, pid, action):
        self._require(self.phase == "setup", "Not in setup.")
        self._require_turn(pid)
        self._require(self.setup_sub == "settlement", "Place a road, not a settlement.")
        vid = action.get("vertex")
        self._require(vid in self.legal_settlement_spots(pid, setup=True),
                      "You can't place a settlement there.")
        self.buildings[vid] = {"type": "settlement", "owner": pid}
        self.last_setup_vertex = vid
        self._log("%s placed a settlement." % self._name(pid))

        # Second-round settlement yields one resource per adjacent tile.
        if self.setup_i >= len(self.order):
            for hid in self.geo["vertices"][vid]["hexes"]:
                res = self.hexes[hid]["resource"]
                if res and hid != self.robber_hex:
                    self._gain(pid, res, 1)
                    self.bank[res] -= 1
        self.setup_sub = "road"

    def _h_setup_road(self, pid, action):
        self._require(self.phase == "setup", "Not in setup.")
        self._require_turn(pid)
        self._require(self.setup_sub == "road", "Place a settlement first.")
        eid = action.get("edge")
        self._require(eid in self.legal_road_spots(pid, setup=True),
                      "Road must connect to the settlement you just placed.")
        self.roads[eid] = pid
        self._log("%s placed a road." % self._name(pid))
        self._advance_setup()

    def _advance_setup(self):
        self.setup_sub = "settlement"
        self.last_setup_vertex = None
        self.setup_i += 1
        if self.setup_i >= len(self.setup_queue):
            self.phase = "main"
            self.current = 0
            self.dice_rolled = False
            self._recompute_longest_road()
            self._log("Setup complete. %s rolls first." % self._name(self.current_pid))

    # -------------------------------------------------------------- main phase
    def _h_roll(self, pid, action):
        self._require(self.phase == "main", "Not in the main phase.")
        self._require_turn(pid)
        self._require(self.robber_phase is None, "Resolve the robber first.")
        self._require(not self.dice_rolled, "You already rolled this turn.")
        d1 = self.rng.randint(1, 6)
        d2 = self.rng.randint(1, 6)
        self.dice = (d1, d2)
        self.dice_rolled = True
        total = d1 + d2
        self.roll_counts[total] = self.roll_counts.get(total, 0) + 1
        self._log("%s rolled %d (%d+%d)." % (self._name(pid), total, d1, d2))
        if total == 7:
            self._begin_robber(steal_only=False)
        else:
            self._produce(total)

    def _produce(self, roll):
        gains = {pid: _empty_hand() for pid in self.order}
        granted = {pid: _empty_hand() for pid in self.order}  # what was actually paid out
        bean_gains = {pid: 0 for pid in self.order}
        for hid, hx in self.hexes.items():
            if hx["number"] != roll or hid == self.robber_hex:
                continue
            terrain = hx["terrain"]
            if terrain == C.TERRAIN_BEANS:
                if not self.gamble_mode:
                    continue  # bean tiles only pay out in Gamble mode
                for vid in self.geo["hex_vertices"][hid]:
                    b = self.buildings.get(vid)
                    if b:
                        bean_gains[b["owner"]] += C.BEAN_TILE_PAYOUT * (2 if b["type"] == "city" else 1)
                continue
            if terrain == C.TERRAIN_GOLD:
                # Gold field: each building draws a random resource (city = 2).
                for vid in self.geo["hex_vertices"][hid]:
                    b = self.buildings.get(vid)
                    if b:
                        for _ in range(2 if b["type"] == "city" else 1):
                            gains[b["owner"]][self.rng.choice(C.RESOURCES)] += 1
                continue
            res = hx["resource"]
            if not res:
                continue
            for vid in self.geo["hex_vertices"][hid]:
                b = self.buildings.get(vid)
                if b:
                    gains[b["owner"]][res] += 2 if b["type"] == "city" else 1

        produced_lines = []
        for res in C.RESOURCES:
            owed = [(pid, gains[pid][res]) for pid in self.order if gains[pid][res] > 0]
            total = sum(n for _, n in owed)
            if total == 0:
                continue
            if total <= self.bank[res]:
                for p, n in owed:
                    self._gain(p, res, n)
                    self.bank[res] -= n
                    granted[p][res] += n
            elif len(owed) == 1:
                p, n = owed[0]
                give = min(n, self.bank[res])
                self._gain(p, res, give)
                self.bank[res] -= give
                granted[p][res] += give
            # else: shortage with multiple claimants -> nobody gets this resource
        for pid, amt in bean_gains.items():
            if amt:
                self.players[pid]["beans"] += amt
        for pid in self.order:
            # Log what was ACTUALLY granted, not the gross intended amount (the
            # bank-shortage rules can grant less, or nothing).
            got = {r: granted[pid][r] for r in C.RESOURCES if granted[pid][r] > 0}
            parts = ["%d %s" % (n, r) for r, n in got.items()]
            if bean_gains[pid]:
                parts.append("%d beans" % bean_gains[pid])
            if parts:
                produced_lines.append("%s got %s" % (self._name(pid), ", ".join(parts)))
        if produced_lines:
            self._log("; ".join(produced_lines) + ".")
        else:
            self._log("No resources produced.")

    # ------------------------------------------------------------- robber / 7
    def _begin_robber(self, steal_only):
        self.pending_discards = {}
        if not steal_only:
            for pid in self.order:
                n = self._hand_size(pid)
                if n > self.discard_threshold:
                    self.pending_discards[pid] = n // 2
        if self.pending_discards:
            self.robber_phase = "discard"
            self._log("A 7 was rolled. Players over 7 cards must discard half.")
        else:
            self.robber_phase = "move"
            if not steal_only:
                self._log("A 7 was rolled. %s moves the robber." % self._name(self.current_pid))

    def _h_discard(self, pid, action):
        self._require(self.robber_phase == "discard", "No discards are required now.")
        self._require(pid in self.pending_discards, "You don't need to discard.")
        need = self.pending_discards[pid]
        cards = action.get("resources", {})
        cards = {r: int(n) for r, n in cards.items() if int(n) > 0}
        self._require(sum(cards.values()) == need,
                      "You must discard exactly %d cards." % need)
        res = self.players[pid]["resources"]
        self._require(all(res.get(r, 0) >= n for r, n in cards.items()),
                      "You don't have those cards.")
        for r, n in cards.items():
            res[r] -= n
            self.bank[r] += n
        del self.pending_discards[pid]
        self._log("%s discarded %d cards." % (self._name(pid), need))
        if not self.pending_discards:
            self.robber_phase = "move"
            self._log("%s moves the robber." % self._name(self.current_pid))

    def _h_move_robber(self, pid, action):
        self._require(self.robber_phase == "move", "You can't move the robber now.")
        self._require_turn(pid)
        hid = action.get("hex")
        self._require(hid in self.hexes, "No such tile.")
        self._require(hid != self.robber_hex, "The robber must move to a new tile.")
        self.robber_hex = hid
        self._log("%s moved the robber." % self._name(pid))
        targets = self._steal_targets(pid, hid)
        target = action.get("target")
        if targets:
            self._require(target in targets,
                          "Choose a player to steal from: %s" % targets)
            stolen = self._steal_from(pid, target)
            if stolen:
                self._log("%s stole a card from %s." % (self._name(pid), self._name(target)))
            else:
                self._log("%s had no cards to steal." % self._name(target))
        else:
            self._require(target in (None, "",) or target not in self.players,
                          "There is no one to steal from there.")
        self.robber_phase = None

    def _steal_targets(self, pid, hid):
        targets = set()
        for vid in self.geo["hex_vertices"][hid]:
            b = self.buildings.get(vid)
            if b and b["owner"] != pid and self._hand_size(b["owner"]) > 0:
                targets.add(b["owner"])
        return sorted(targets)

    def _steal_from(self, pid, target):
        res = self.players[target]["resources"]
        pool = []
        for r, n in res.items():
            pool += [r] * n
        if not pool:
            return None
        r = self.rng.choice(pool)
        res[r] -= 1
        self._gain(pid, r, 1)
        return r

    # --------------------------------------------------------------- building
    def _require_can_build(self, pid):
        self._require(self.phase == "main", "Not in the main phase.")
        self._require_turn(pid)
        self._require(self.robber_phase is None, "Resolve the robber first.")
        self._require(self.dice_rolled, "Roll the dice first.")

    def _h_build_road(self, pid, action):
        self._require(self.phase == "main", "Not in the main phase.")
        self._require_turn(pid)
        self._require(self.robber_phase is None, "Resolve the robber first.")
        free = self.free_roads > 0
        if not free:
            self._require(self.dice_rolled, "Roll the dice first.")
        eid = action.get("edge")
        self._require(len(self._roads_of(pid)) < self.max_roads, "No roads left in supply.")
        self._require(eid not in self.roads, "There is already a road there.")
        self._require(self._road_connects(pid, eid), "That road isn't connected to your network.")
        if free:
            self.free_roads -= 1
        else:
            self._require(self._has(pid, C.COST_ROAD), "You can't afford a road.")
            self._pay(pid, C.COST_ROAD)
        self.roads[eid] = pid
        self._log("%s built a road." % self._name(pid))
        self._recompute_longest_road()

    def _h_build_settlement(self, pid, action):
        self._require_can_build(pid)
        vid = action.get("vertex")
        self._require(len(self._settlements(pid)) < self.max_settlements,
                      "No settlements left in supply.")
        self._require(self._distance_ok(vid), "Too close to another building (distance rule).")
        self._require(self._settlement_connected(pid, vid), "Must connect to one of your roads.")
        self._require(self._has(pid, C.COST_SETTLEMENT), "You can't afford a settlement.")
        self._pay(pid, C.COST_SETTLEMENT)
        self.buildings[vid] = {"type": "settlement", "owner": pid}
        self._log("%s built a settlement." % self._name(pid))
        # A new settlement may cut an opponent's longest road.
        self._recompute_longest_road()

    def _h_build_city(self, pid, action):
        self._require_can_build(pid)
        vid = action.get("vertex")
        b = self.buildings.get(vid)
        self._require(b is not None and b["owner"] == pid and b["type"] == "settlement",
                      "You can only upgrade your own settlement.")
        self._require(len(self._cities(pid)) < self.max_cities, "No cities left in supply.")
        self._require(self._has(pid, C.COST_CITY), "You can't afford a city.")
        self._pay(pid, C.COST_CITY)
        b["type"] = "city"
        self._log("%s upgraded to a city." % self._name(pid))

    def _h_buy_dev(self, pid, action):
        self._require_can_build(pid)
        self._require(len(self.deck) > 0, "The development deck is empty.")
        self._require(self._has(pid, C.COST_DEV_CARD), "You can't afford a development card.")
        self._pay(pid, C.COST_DEV_CARD)
        card = self.deck.pop()
        self.players[pid]["dev_new"][card] += 1
        self._log("%s bought a development card." % self._name(pid))

    # ----------------------------------------------------------- dev cards
    def _require_can_play_dev(self, pid, card):
        self._require(self.phase == "main", "Not in the main phase.")
        self._require_turn(pid)
        self._require(self.robber_phase is None, "Resolve the robber first.")
        self._require(not self.dev_played_this_turn, "You may only play one development card per turn.")
        self._require(self.players[pid]["dev"].get(card, 0) > 0,
                      "You don't have that card to play (a card bought this turn can't be played yet).")

    def _h_play_knight(self, pid, action):
        self._require_can_play_dev(pid, C.DEV_KNIGHT)
        self.players[pid]["dev"][C.DEV_KNIGHT] -= 1
        self.players[pid]["played_knights"] += 1
        self.dev_played_this_turn = True
        self._log("%s played a Knight." % self._name(pid))
        self._recompute_largest_army()
        # Then move the robber (no discards on a Knight).
        self._begin_robber(steal_only=True)

    def _h_play_road_building(self, pid, action):
        self._require_can_play_dev(pid, C.DEV_ROAD_BUILDING)
        self.players[pid]["dev"][C.DEV_ROAD_BUILDING] -= 1
        self.dev_played_this_turn = True
        roads_left = max(0, self.max_roads - len(self._roads_of(pid)))
        self.free_roads = min(2, roads_left)
        self._log("%s played Road Building (2 free roads)." % self._name(pid))

    def _h_play_yop(self, pid, action):
        self._require_can_play_dev(pid, C.DEV_YEAR_OF_PLENTY)
        picks = action.get("resources", [])
        self._require(isinstance(picks, list) and len(picks) == 2,
                      "Choose exactly two resources.")
        for r in picks:
            self._require(r in C.RESOURCES, "Invalid resource.")
        from collections import Counter
        need = Counter(picks)
        for r, n in need.items():
            self._require(self.bank[r] >= n, "The bank doesn't have enough %s." % r)
        self.players[pid]["dev"][C.DEV_YEAR_OF_PLENTY] -= 1
        self.dev_played_this_turn = True
        for r in picks:
            self._gain(pid, r, 1)
            self.bank[r] -= 1
        self._log("%s played Year of Plenty (%s)." % (self._name(pid), ", ".join(picks)))

    def _h_play_monopoly(self, pid, action):
        self._require_can_play_dev(pid, C.DEV_MONOPOLY)
        res = action.get("resource")
        self._require(res in C.RESOURCES, "Invalid resource.")
        self.players[pid]["dev"][C.DEV_MONOPOLY] -= 1
        self.dev_played_this_turn = True
        taken = 0
        for other in self.order:
            if other == pid:
                continue
            n = self.players[other]["resources"][res]
            if n:
                self.players[other]["resources"][res] = 0
                taken += n
        self._gain(pid, res, taken)
        self._log("%s played Monopoly on %s and took %d." % (self._name(pid), res, taken))

    # ---------------------------------------------------------------- trading
    def _port_ratios(self, pid):
        ratios = {r: 4 for r in C.RESOURCES}
        owned_vertices = set(self._settlements(pid)) | set(self._cities(pid))
        for port in self.geo["ports"]:
            if owned_vertices & set(port["vertices"]):
                if port["type"] == C.PORT_GENERIC:
                    for r in C.RESOURCES:
                        ratios[r] = min(ratios[r], 3)
                else:
                    ratios[port["type"]] = min(ratios[port["type"]], 2)
        return ratios

    def _h_bank_trade(self, pid, action):
        self._require_can_build(pid)
        give = action.get("give")
        recv = action.get("receive")
        ratios = self._port_ratios(pid)
        # Backward-compatible single form: give/receive are resource strings,
        # meaning "ratio of give for 1 of receive".
        if isinstance(give, str) and isinstance(recv, str):
            self._require(give in C.RESOURCES and recv in C.RESOURCES, "Invalid resources.")
            give = {give: ratios[give]}
            recv = {recv: 1}
        # General form: give/receive are bundles. Each give amount must be a whole
        # multiple of that resource's best ratio, and the number of cards bought
        # must equal the number paid for — so you can do several 2:1s at once.
        give = self._norm_bundle(give)
        recv = self._norm_bundle(recv)
        self._require(give and recv, "Choose what to give and what to receive.")
        credits = 0
        for r, n in give.items():
            self._require(n % ratios[r] == 0,
                          "You must give %s in multiples of %d (your rate)." % (r, ratios[r]))
            credits += n // ratios[r]
        recv_total = sum(recv.values())
        self._require(credits == recv_total,
                      "Unbalanced trade: %d cards in pays for %d, not %d." %
                      (sum(give.values()), credits, recv_total))
        for r in recv:
            self._require(r not in give, "Trade for a different resource.")
        self._require(self._has(pid, give), "You don't have those cards to give.")
        for r, n in recv.items():
            self._require(self.bank[r] >= n, "The bank is out of %s." % r)
        for r, n in give.items():
            self.players[pid]["resources"][r] -= n
            self.bank[r] += n
        for r, n in recv.items():
            self._gain(pid, r, n)
            self.bank[r] -= n
        give_s = ", ".join("%d %s" % (n, r) for r, n in give.items())
        recv_s = ", ".join("%d %s" % (n, r) for r, n in recv.items())
        self._log("%s traded %s for %s with the bank." % (self._name(pid), give_s, recv_s))

    def _norm_bundle(self, bundle):
        out = {}
        for r, n in (bundle or {}).items():
            n = int(n)
            if r not in C.RESOURCES or n < 0:
                raise GameError("Invalid trade contents.")
            if n:
                out[r] = n
        return out

    def _h_propose_trade(self, pid, action):
        self._require_can_build(pid)
        give = self._norm_bundle(action.get("give"))
        recv = self._norm_bundle(action.get("receive"))
        self._require(give or recv, "An empty trade isn't allowed.")
        self._require(self._has(pid, give), "You don't have what you're offering.")
        to = action.get("to")
        if to is not None:
            self._require(to in self.players and to != pid, "Invalid trade partner.")
        self.trade = {"from": pid, "give": give, "receive": recv, "to": to}
        self._log("%s proposed a trade." % self._name(pid))

    def _h_accept_trade(self, pid, action):
        self._require(self.trade is not None, "There is no trade to accept.")
        offer = self.trade
        self._require(pid != offer["from"], "You can't accept your own trade.")
        if offer["to"] is not None:
            self._require(pid == offer["to"], "This trade is directed at someone else.")
        proposer = offer["from"]
        # proposer gives `give`, receives `receive`; acceptor mirrors.
        self._require(self._has(proposer, offer["give"]), "The proposer no longer has the goods.")
        self._require(self._has(pid, offer["receive"]), "You don't have what they want.")
        for r, n in offer["give"].items():
            self.players[proposer]["resources"][r] -= n
            self.players[pid]["resources"][r] += n
        for r, n in offer["receive"].items():
            self.players[pid]["resources"][r] -= n
            self.players[proposer]["resources"][r] += n
        self._log("%s and %s completed a trade." % (self._name(proposer), self._name(pid)))
        self.trade = None

    def _h_cancel_trade(self, pid, action):
        self._require(self.trade is not None, "There is no open trade.")
        self._require(pid == self.trade["from"], "Only the proposer can cancel.")
        self.trade = None
        self._log("%s withdrew their trade offer." % self._name(pid))

    # ===================================================== casino / beans
    # These run off-turn: gambling and currency swaps don't need it to be your
    # turn, but the game must be underway. Beans can never go negative.
    def _require_casino(self, pid):
        self._require(self.phase == "main", "The casino opens once the game is underway.")

    def _h_convert_to_beans(self, pid, action):
        self._require_casino(pid)
        give = self._norm_bundle(action.get("resources"))
        self._require(give, "Choose resources to cash in for beans.")
        self._require(self._has(pid, give), "You don't have those cards.")
        total = sum(give.values())
        for r, n in give.items():
            self.players[pid]["resources"][r] -= n
            self.bank[r] += n
        gained = total * self.beans_per_resource
        self.players[pid]["beans"] += gained
        self._log("%s cashed %d card(s) in for %d beans." % (self._name(pid), total, gained))

    def _h_convert_to_resources(self, pid, action):
        self._require_casino(pid)
        recv = self._norm_bundle(action.get("resources"))
        self._require(recv, "Choose resources to buy with beans.")
        total = sum(recv.values())
        cost = total * self.beans_per_resource
        self._require(self.players[pid]["beans"] >= cost,
                      "You need %d beans for that (you have %d)." % (cost, self.players[pid]["beans"]))
        for r, n in recv.items():
            self._require(self.bank[r] >= n, "The bank is out of %s." % r)
        self.players[pid]["beans"] -= cost
        for r, n in recv.items():
            self._gain(pid, r, n)
            self.bank[r] -= n
        self._log("%s spent %d beans on %d resource card(s)." % (self._name(pid), cost, total))

    def _h_buy_vp(self, pid, action):
        self._require_casino(pid)
        amount = self._pos_int(action.get("amount"), "victory points")
        cost = amount * self.beans_per_vp
        self._require(self.players[pid]["beans"] >= cost,
                      "You need %d beans for %d VP (you have %d)." %
                      (cost, amount, self.players[pid]["beans"]))
        self.players[pid]["beans"] -= cost
        self.players[pid]["bought_vp"] += amount
        self._log("%s bought %d victory point(s) for %d beans." % (self._name(pid), amount, cost))

    def _h_sell_vp(self, pid, action):
        self._require_casino(pid)
        amount = self._pos_int(action.get("amount"), "victory points")
        self._require(self.players[pid]["bought_vp"] >= amount,
                      "You can only sell victory points you bought with beans.")
        self.players[pid]["bought_vp"] -= amount
        gained = amount * self.beans_per_vp
        self.players[pid]["beans"] += gained
        self._log("%s sold %d victory point(s) for %d beans." % (self._name(pid), amount, gained))

    def _pos_int(self, v, what):
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise GameError("Choose a whole number of %s." % what)
        self._require(v > 0, "Choose at least one %s." % what.rstrip("s"))
        return v

    def _h_convert_dev_to_beans(self, pid, action):
        """Sell development cards for beans at half the resource rate
        (1 dev card = beansPerResource/2 = 10 beans by default). Only when the
        host has turned this on inside Gamble mode."""
        self._require_casino(pid)
        self._require(self.gamble_mode and self.gamble_dev_for_beans,
                      "Cashing dev cards for beans is off (enable it in Gamble mode).")
        cards = action.get("cards") or {}
        p = self.players[pid]
        want = {}
        total = 0
        for k, n in cards.items():
            self._require(k in C.DEV_CARD_COUNTS, "Unknown development card.")
            # Victory-point cards are worth a real VP — never cash those away.
            self._require(k != C.DEV_VICTORY_POINT, "Victory point cards can't be cashed in.")
            n = int(n)
            self._require(n >= 0, "Whole cards only.")
            if n:
                have = p["dev"].get(k, 0) + p["dev_new"].get(k, 0)
                self._require(have >= n, "You don't have %d %s card(s)." % (n, k))
                want[k] = n
                total += n
        self._require(total > 0, "Choose development cards to cash in.")
        for k, n in want.items():
            take = min(p["dev"].get(k, 0), n)
            p["dev"][k] -= take
            rest = n - take
            if rest:
                p["dev_new"][k] -= rest
        per_card = max(1, self.beans_per_resource // 2)  # always at least 1 bean
        gained = total * per_card
        p["beans"] += gained
        self._log("%s cashed %d development card(s) in for %d beans." % (self._name(pid), total, gained))

    # ---- blackjack (shared shoe, per-player hands) ----
    def _bj_shoe_obj(self):
        if self.bj_shoe is None:
            self.bj_shoe = casino.new_shoe(self.rng)
            self.bj_seen = []
        return self.bj_shoe

    def _bj(self, pid):
        """The player's seat at the shared table, created on demand."""
        p = self.players[pid]
        if p["bj"] is None:
            p["bj"] = {"state": "idle", "hands": [], "dealer": [], "active": 0,
                       "dealerHidden": True, "net": 0, "streak": 0, "mood": "happy"}
        return p["bj"]

    def _bj_draw(self):
        """Draw the next card from the shared shoe, reshuffling at the cut."""
        self._bj_shoe_obj()
        if casino.needs_shuffle(self.bj_shoe):
            self.bj_shoe = casino.new_shoe(self.rng)
            self.bj_seen = []
            self.bj_count_bias = 0.0  # the count resets with the shoe
            self.bj_message = "Fresh shoe! Six decks, shuffled up."
        card = self.bj_shoe.pop()
        self.bj_seen.append(card)
        return card

    def _h_bj_bet(self, pid, action):
        self._require_casino(pid)
        bj = self._bj(pid)
        self._require(bj["state"] in ("idle", "done"), "Finish the current hand first.")
        bet = self._pos_int(action.get("amount"), "beans")
        self._require(bet >= C.BLACKJACK_MIN_BET, "Minimum bet is %d bean(s)." % C.BLACKJACK_MIN_BET)
        self._require(self.players[pid]["beans"] >= bet,
                      "You only have %d beans." % self.players[pid]["beans"])
        self.players[pid]["beans"] -= bet
        bj["net"] = 0
        bj["mood"] = "dealing"
        bj["dealer"] = [self._bj_draw(), self._bj_draw()]
        bj["dealerHidden"] = True
        bj["hands"] = [{"cards": [self._bj_draw(), self._bj_draw()],
                        "bet": bet, "done": False, "result": None}]
        bj["active"] = 0
        bj["state"] = "player"
        self.bj_message = "Cards out for %s." % self._name(pid)
        # Naturals: peek on a ten/ace up, settle immediately on any blackjack.
        player_bj = casino.is_blackjack(bj["hands"][0]["cards"])
        dealer_bj = casino.is_blackjack(bj["dealer"])
        if player_bj or dealer_bj:
            bj["hands"][0]["done"] = True
            self._bj_finish(pid)
            return
        self._log("%s dealt a blackjack hand (bet %d)." % (self._name(pid), bet))

    def _bj_active(self, bj):
        return bj["hands"][bj["active"]]

    def _require_bj_turn(self, pid):
        bj = self._bj(pid)
        self._require(bj["state"] == "player", "No blackjack hand in progress.")
        return bj

    def _h_bj_hit(self, pid, action):
        bj = self._require_bj_turn(pid)
        hand = self._bj_active(bj)
        hand["cards"].append(self._bj_draw())
        if casino.is_bust(hand["cards"]):
            hand["done"] = True
            hand["result"] = "bust"
            self._bj_advance(pid)

    def _h_bj_stand(self, pid, action):
        bj = self._require_bj_turn(pid)
        self._bj_active(bj)["done"] = True
        self._bj_advance(pid)

    def _h_bj_double(self, pid, action):
        bj = self._require_bj_turn(pid)
        hand = self._bj_active(bj)
        self._require(len(hand["cards"]) == 2, "You can only double on your first two cards.")
        self._require(self.players[pid]["beans"] >= hand["bet"], "Not enough beans to double.")
        self.players[pid]["beans"] -= hand["bet"]
        hand["bet"] *= 2
        hand["cards"].append(self._bj_draw())
        hand["done"] = True
        if casino.is_bust(hand["cards"]):
            hand["result"] = "bust"
        self._bj_advance(pid)

    def _h_bj_split(self, pid, action):
        bj = self._require_bj_turn(pid)
        hand = self._bj_active(bj)
        self._require(casino.can_split(hand["cards"]), "Those cards can't be split.")
        self._require(len(bj["hands"]) < 4, "You can't split again.")
        self._require(self.players[pid]["beans"] >= hand["bet"], "Not enough beans to split.")
        self.players[pid]["beans"] -= hand["bet"]
        moved = hand["cards"].pop()
        new_hand = {"cards": [moved], "bet": hand["bet"], "done": False, "result": None}
        hand["cards"].append(self._bj_draw())
        new_hand["cards"].append(self._bj_draw())
        bj["hands"].insert(bj["active"] + 1, new_hand)
        # Split aces receive one card each and stand automatically.
        if casino.rank_of(moved) == "A":
            hand["done"] = True
            new_hand["done"] = True
            self._bj_advance(pid)

    def _h_bj_surrender(self, pid, action):
        bj = self._require_bj_turn(pid)
        hand = self._bj_active(bj)
        self._require(len(hand["cards"]) == 2 and len(bj["hands"]) == 1,
                      "You can only surrender your opening two cards.")
        hand["done"] = True
        hand["result"] = "surrender"   # half the bet comes back at settlement
        self._bj_advance(pid)

    def _bj_advance(self, pid):
        bj = self._bj(pid)
        while bj["active"] < len(bj["hands"]) and bj["hands"][bj["active"]]["done"]:
            bj["active"] += 1
        if bj["active"] < len(bj["hands"]):
            self.bj_message = "Play hand %d, %s." % (bj["active"] + 1, self._name(pid))
            return
        self._bj_finish(pid)

    def _bj_finish(self, pid):
        bj = self._bj(pid)
        bj["dealerHidden"] = False
        # Dealer draws to 17 (stands on all 17) only if some hand can still win
        # (i.e. is neither busted nor surrendered).
        if any(h["result"] != "surrender" and not casino.is_bust(h["cards"]) for h in bj["hands"]):
            while casino.best(bj["dealer"]) < 17:
                bj["dealer"].append(self._bj_draw())
        dealer_total = casino.best(bj["dealer"])
        dealer_bj = casino.is_blackjack(bj["dealer"])
        payout = 0
        for h in bj["hands"]:
            payout += self._bj_settle(h, dealer_total, dealer_bj)
        self.players[pid]["beans"] += payout
        bj["net"] = payout - sum(h["bet"] for h in bj["hands"])
        bj["state"] = "done"
        # Streak + the dealer's (very friendly) reaction.
        if bj["net"] > 0:
            bj["streak"] = bj["streak"] + 1 if bj["streak"] > 0 else 1
        elif bj["net"] < 0:
            bj["streak"] = bj["streak"] - 1 if bj["streak"] < 0 else -1
        has_bj = any(h["result"] == "blackjack" for h in bj["hands"])
        if has_bj or (bj["net"] > 0 and bj["streak"] >= 3):
            bj["mood"] = "excited"
        elif bj["net"] > 0:
            bj["mood"] = "happy"
        elif bj["net"] < 0:
            bj["mood"] = "sad"
        else:
            bj["mood"] = "neutral"
        self.bj_message = self._dealer_line(pid, bj)
        self._log("%s finished blackjack: %+d beans." % (self._name(pid), bj["net"]))

    def _dealer_line(self, pid, bj):
        """A friendly, reactive line from the 8-bit dealer."""
        name = self._name(pid)
        net, streak = bj["net"], bj["streak"]
        has_bj = any(h["result"] == "blackjack" for h in bj["hands"])
        busted = all(h["result"] == "bust" for h in bj["hands"])
        surrendered = any(h["result"] == "surrender" for h in bj["hands"])
        if surrendered and len(bj["hands"]) == 1:
            return self.rng.choice([
                "Surrendered — sometimes folding is the smart play, %s. Half back to you." % name,
                "A wise retreat, %s. Live to bet another hand." % name])
        if has_bj:
            return self.rng.choice([
                "Blackjack!! Pays three-to-two — beautifully played, %s." % name,
                "A natural! The cards adore you today, %s." % name])
        if net > 0:
            if streak >= 3:
                return self.rng.choice([
                    "%d wins in a row, %s — you're on fire! 🔥" % (streak, name),
                    "The whole table's watching you, %s. What a run!" % name])
            return self.rng.choice([
                "Winner, winner! +%d beans for %s." % (net, name),
                "Nicely done, %s — the house tips its visor to you." % name,
                "That's how it's done! +%d to you, friend." % net])
        if net == 0:
            return self.rng.choice([
                "A push — your beans stay right where they are.",
                "Even money that time. Care to run it again, %s?" % name])
        if busted:
            return self.rng.choice([
                "Busted! Tough one, %s — the cards giveth and taketh." % name,
                "Over twenty-one. Shake it off, %s, the next is yours." % name])
        if streak <= -3:
            return self.rng.choice([
                "Rough patch, %s. The shoe's bound to turn — chin up. 💛" % name,
                "The house has been lucky against you, %s. Don't let it rattle you." % name])
        return self.rng.choice([
            "Dealer takes it this time. Better luck next hand, %s." % name,
            "So close, %s. Want to run it back?" % name])

    def _h_bj_tip(self, pid, action):
        self._require_casino(pid)
        amount = self._pos_int(action.get("amount"), "beans")
        self._require(self.players[pid]["beans"] >= amount,
                      "You only have %d beans." % self.players[pid]["beans"])
        self.players[pid]["beans"] -= amount
        self.bj_tips += amount
        self.players[pid]["bj_tipped"] += amount  # the dealer remembers who tips
        self._bj(pid)["mood"] = "thankful"
        extra = ""
        if self.gamble_mode:
            # A grateful dealer reads the count more kindly for you: the displayed
            # running count climbs (a friendly read — the real shoe is unchanged).
            self.bj_count_bias += amount * 0.01
            extra = " The count's looking friendlier already (+%.2f)..." % (amount * 0.01)
        self.bj_message = self.rng.choice([
            "Oh, %s, you shouldn't have! Thank you kindly! 💛" % self._name(pid),
            "A tip?! You're too generous, %s — may the cards favor you!" % self._name(pid),
            "Bless you, %s! Tips keep this old dealer smiling. 🎩" % self._name(pid)]) + extra
        self._log("%s tipped the dealer %d beans." % (self._name(pid), amount))

    def _h_bj_chat(self, pid, action):
        """Table talk: the player says something, the dealer answers in kind.
        Pattern-matching (engine.dealer_chat) — instant, no network — and the
        dealer may slip the player a few beans back (framed as a tip). An
        optional external model can replace the dealer's text afterward (server)."""
        self._require_casino(pid)
        text = str(action.get("text") or "").strip()[:200]
        self._require(bool(text), "Say something first.")
        dealer = action.get("dealer") if action.get("dealer") in ("m", "f") else "m"
        reply_text, grant = dealer_chat.reply(self, pid, text, dealer)
        if grant > 0:
            self.players[pid]["beans"] += grant
            self.players[pid]["bj_dealer_given"] += grant
        self._add_chat(pid, self._name(pid), text, None)
        msg = self._add_chat("dealer", dealer_chat.DEALER_NAMES.get(dealer, "Dealer"),
                             reply_text, dealer)
        if grant > 0:
            self._log("The dealer tipped %s %d beans." % (self._name(pid), grant))
        # Hand the server what it needs to (optionally) upgrade this reply with an
        # external model: which message to rewrite, who/which dealer, the grant.
        return {"message": msg, "grant": grant, "dealer": dealer, "pid": pid}

    def _add_chat(self, frm, name, text, dealer):
        mid = self.bj_chat_next
        self.bj_chat_next += 1
        msg = {"id": mid, "from": frm, "name": name, "text": text, "dealer": dealer}
        self.bj_chat.append(msg)
        if len(self.bj_chat) > 40:
            self.bj_chat = self.bj_chat[-40:]
        return msg

    def set_chat_text(self, msg_id, text):
        """Replace a chat message's text by id (used by the optional async LLM
        reply). No-op if the message has scrolled off."""
        for m in self.bj_chat:
            if m.get("id") == msg_id:
                m["text"] = text
                return True
        return False

    def _bj_settle(self, hand, dealer_total, dealer_bj):
        """Return the beans paid back for this hand (0 = lost the wager)."""
        cards, bet = hand["cards"], hand["bet"]
        if hand["result"] == "surrender":
            return bet // 2  # forfeited the hand; half the wager comes back
        if casino.is_bust(cards):
            hand["result"] = "bust"
            return 0
        player_bj = casino.is_blackjack(cards)
        if player_bj and not dealer_bj:
            hand["result"] = "blackjack"
            return bet + bet * C.BLACKJACK_PAYOUT_NUM // C.BLACKJACK_PAYOUT_DEN
        total = casino.best(cards)
        if dealer_bj and not player_bj:
            hand["result"] = "lose"
            return 0
        if dealer_total > 21 or total > dealer_total:
            hand["result"] = "win"
            return bet * 2
        if total == dealer_total:
            hand["result"] = "push"
            return bet
        hand["result"] = "lose"
        return 0

    # ------------------------------------------------------------- end of turn
    def _h_end_turn(self, pid, action):
        self._require(self.phase == "main", "Not in the main phase.")
        self._require_turn(pid)
        self._require(self.dice_rolled, "You must roll before ending your turn.")
        self._require(self.robber_phase is None, "Resolve the robber first.")
        self._require(not self.pending_discards, "Discards are still pending.")
        # Newly bought dev cards become playable next turn.
        p = self.players[pid]
        for k in C.DEV_CARD_COUNTS:
            p["dev"][k] += p["dev_new"][k]
            p["dev_new"][k] = 0
        self.free_roads = 0
        self.dev_played_this_turn = False
        self.trade = None
        self.dice = None
        self.dice_rolled = False
        self.current = (self.current + 1) % len(self.order)
        self._log("%s ended their turn. %s to roll." % (self._name(pid), self._name(self.current_pid)))

    # ------------------------------------------------------------- awards / VP
    def _recompute_longest_road(self):
        lengths = {pid: self._longest_road_length(pid) for pid in self.order}
        maxlen = max(lengths.values()) if lengths else 0
        holder = self.longest_road_owner
        if maxlen < C.LONGEST_ROAD_MINIMUM:
            new_owner = None
        else:
            leaders = [p for p, l in lengths.items() if l == maxlen]
            if holder in leaders:
                new_owner = holder
            elif len(leaders) == 1:
                new_owner = leaders[0]
            else:
                new_owner = None  # tie among non-holders -> set aside
        self.longest_road_len = lengths.get(new_owner, 0) if new_owner else maxlen
        if new_owner != holder:
            self.longest_road_owner = new_owner
            if new_owner:
                self._log("%s now holds the Longest Road (%d)." % (self._name(new_owner), lengths[new_owner]))
            else:
                self._log("The Longest Road is now unclaimed.")

    def _longest_road_length(self, pid):
        my_roads = self._roads_of(pid)
        if not my_roads:
            return 0
        adj = {}
        for eid in my_roads:
            e = self.geo["edges"][eid]
            adj.setdefault(e["v1"], []).append((e["v2"], eid))
            adj.setdefault(e["v2"], []).append((e["v1"], eid))

        def blocked(v):
            b = self.buildings.get(v)
            return b is not None and b["owner"] != pid

        best = 0

        def dfs(v, used):
            nonlocal best
            if len(used) > best:
                best = len(used)
            if used and blocked(v):
                return  # may end at a blocked vertex but not pass through it
            for nv, eid in adj.get(v, ()):
                if eid not in used:
                    used.add(eid)
                    dfs(nv, used)
                    used.remove(eid)

        for start in adj:
            dfs(start, set())
        return best

    def _recompute_largest_army(self):
        counts = {pid: self.players[pid]["played_knights"] for pid in self.order}
        maxk = max(counts.values()) if counts else 0
        holder = self.largest_army_owner
        if maxk < C.LARGEST_ARMY_MINIMUM:
            new_owner = None
        else:
            leaders = [p for p, k in counts.items() if k == maxk]
            new_owner = holder if holder in leaders else leaders[0]
        if new_owner != holder:
            self.largest_army_owner = new_owner
            if new_owner:
                self._log("%s now holds the Largest Army (%d knights)." %
                          (self._name(new_owner), counts[new_owner]))

    def public_vp(self, pid):
        vp = len(self._settlements(pid)) * C.VP_SETTLEMENT + len(self._cities(pid)) * C.VP_CITY
        if self.longest_road_owner == pid:
            vp += C.VP_LONGEST_ROAD
        if self.largest_army_owner == pid:
            vp += C.VP_LARGEST_ARMY
        vp += self.players[pid].get("bought_vp", 0)  # gambled VP is openly known
        return vp

    def total_vp(self, pid):
        p = self.players[pid]
        vp_cards = p["dev"].get(C.DEV_VICTORY_POINT, 0) + p["dev_new"].get(C.DEV_VICTORY_POINT, 0)
        return self.public_vp(pid) + vp_cards

    def _check_win(self, actor=None):
        if self.phase != "main" or self.winner:
            return
        # Normally the active player; for off-turn gambling, the player who acted.
        if actor is None:
            actor = self.current_pid
        if actor and self.total_vp(actor) >= self.vp_to_win:
            self.winner = actor
            self.phase = "ended"
            self._log("%s wins with %d victory points!" % (self._name(actor), self.total_vp(actor)))
