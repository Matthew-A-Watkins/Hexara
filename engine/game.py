"""Authoritative game state and rules.

A single ``Game`` instance is the one source of truth for a match. The server
calls :meth:`Game.apply` with a player's action; the engine validates it
against the official base-game rules, mutates state, and the server then
broadcasts a fresh per-player view (with hidden information masked) plus the
set of currently legal actions for the player who must act.

Nothing in here knows about networking or rendering.
"""

import random

from . import constants as C
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

    def rules_view(self):
        """The active rule numbers, for serialization to clients."""
        return {
            "victoryPoints": self.vp_to_win,
            "discardThreshold": self.discard_threshold,
            "maxRoads": self.max_roads,
            "maxSettlements": self.max_settlements,
            "maxCities": self.max_cities,
            "bankPerResource": self.bank_per_resource,
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
            deck += [card] * n
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
        }.get(action.get("type"))
        self._require(handler is not None, "Unknown action: %r" % action.get("type"))
        handler(pid, action)
        self._check_win()

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
        self._log("%s rolled %d (%d+%d)." % (self._name(pid), total, d1, d2))
        if total == 7:
            self._begin_robber(steal_only=False)
        else:
            self._produce(total)

    def _produce(self, roll):
        gains = {pid: _empty_hand() for pid in self.order}
        for hid, hx in self.hexes.items():
            if hx["number"] != roll or hid == self.robber_hex or not hx["resource"]:
                continue
            res = hx["resource"]
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
            elif len(owed) == 1:
                p, n = owed[0]
                give = min(n, self.bank[res])
                self._gain(p, res, give)
                self.bank[res] -= give
            # else: shortage with multiple claimants -> nobody gets this resource
        for pid in self.order:
            got = {r: gains[pid][r] for r in C.RESOURCES if gains[pid][r] > 0}
            if got:
                produced_lines.append("%s got %s" % (
                    self._name(pid),
                    ", ".join("%d %s" % (n, r) for r, n in got.items())))
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
        self._require(give in C.RESOURCES and recv in C.RESOURCES, "Invalid resources.")
        self._require(give != recv, "Trade for a different resource.")
        ratio = self._port_ratios(pid)[give]
        self._require(self.players[pid]["resources"][give] >= ratio,
                      "You need %d %s for that trade." % (ratio, give))
        self._require(self.bank[recv] >= 1, "The bank is out of %s." % recv)
        self.players[pid]["resources"][give] -= ratio
        self.bank[give] += ratio
        self._gain(pid, recv, 1)
        self.bank[recv] -= 1
        self._log("%s traded %d %s for 1 %s with the bank." % (self._name(pid), ratio, give, recv))

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
        return vp

    def total_vp(self, pid):
        p = self.players[pid]
        vp_cards = p["dev"].get(C.DEV_VICTORY_POINT, 0) + p["dev_new"].get(C.DEV_VICTORY_POINT, 0)
        return self.public_vp(pid) + vp_cards

    def _check_win(self):
        if self.phase != "main" or self.winner:
            return
        actor = self.current_pid
        if actor and self.total_vp(actor) >= self.vp_to_win:
            self.winner = actor
            self.phase = "ended"
            self._log("%s wins with %d victory points!" % (self._name(actor), self.total_vp(actor)))
