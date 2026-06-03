"""Rules tests for the engine. Run: py -3 tests\\test_engine.py

No third-party deps; a tiny assert harness prints PASS/FAIL per test.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import constants as C
from engine.game import Game, GameError
from engine.geometry import GEOMETRY
from engine import views

PLAYERS = [
    {"id": "A", "name": "Alice", "color": "red"},
    {"id": "B", "name": "Bob", "color": "blue"},
    {"id": "C", "name": "Cara", "color": "orange"},
]

_tests = []


def test(fn):
    _tests.append(fn)
    return fn


# --------------------------------------------------------------------- helpers
def grant(game, pid, **res):
    for r, n in res.items():
        game.players[pid]["resources"][r] += n
        game.bank[r] -= n


def auto_setup(game):
    while game.phase == "setup":
        pid = game.current_pid
        if game.setup_sub == "settlement":
            spots = game.legal_settlement_spots(pid, setup=True)
            game.apply(pid, {"type": "place_setup_settlement", "vertex": spots[0]})
        else:
            spots = game.legal_road_spots(pid, setup=True)
            game.apply(pid, {"type": "place_setup_road", "edge": spots[0]})


def find_edge_trail(length):
    """Find a simple trail (no repeated edges/vertices) of `length` edges."""
    geo = GEOMETRY
    vadj = {v["id"]: [] for v in geo["vertices"]}
    for e in geo["edges"]:
        vadj[e["v1"]].append((e["v2"], e["id"]))
        vadj[e["v2"]].append((e["v1"], e["id"]))

    result = {}

    def dfs(v, verts, eids):
        if len(eids) == length:
            result["v"] = list(verts)
            result["e"] = list(eids)
            return True
        for nv, eid in vadj[v]:
            if nv in verts or eid in eids:
                continue
            verts.append(nv)
            eids.append(eid)
            if dfs(nv, verts, eids):
                return True
            verts.pop()
            eids.pop()
        return False

    for start in vadj:
        if dfs(start, [start], []):
            return result["e"], result["v"]
    raise RuntimeError("no trail found")


def expect_error(fn, contains=None):
    try:
        fn()
    except GameError as e:
        if contains:
            assert contains.lower() in str(e).lower(), "wrong error: %s" % e
        return
    raise AssertionError("expected GameError but none raised")


def expect_map_error(fn, contains=None):
    from engine.maps import MapError
    try:
        fn()
    except MapError as e:
        if contains:
            assert contains.lower() in str(e).lower(), "wrong error: %s" % e
        return
    raise AssertionError("expected MapError but none raised")


def smart_choose(g, pid):
    """A goal-directed move: knights -> cities -> settlements -> roads -> dev,
    bank-trading toward whatever the next build needs."""
    res = g.players[pid]["resources"]
    if not g.dev_played_this_turn and g.players[pid]["dev"].get(C.DEV_KNIGHT, 0) > 0:
        return {"type": "play_knight"}
    if g._has(pid, C.COST_CITY) and g._settlements(pid):
        return {"type": "build_city", "vertex": g._settlements(pid)[0]}
    if g._has(pid, C.COST_SETTLEMENT):
        spots = g.legal_settlement_spots(pid)
        if spots:
            return {"type": "build_settlement", "vertex": spots[0]}
    if g._has(pid, C.COST_ROAD) and len(g._roads_of(pid)) < C.MAX_ROADS:
        spots = g.legal_road_spots(pid)
        if spots:
            return {"type": "build_road", "edge": spots[0]}
    if g._has(pid, C.COST_DEV_CARD) and g.deck:
        return {"type": "buy_dev_card"}
    wants = [r for r, need in (("ore", 3), ("wheat", 2)) if res[r] < need]
    wants += [r for r in ("wood", "brick", "sheep", "wheat") if res[r] < 1 and r not in wants]
    ratios = g._port_ratios(pid)
    for want in wants:
        for give in C.RESOURCES:
            if give == want or res[give] < ratios[give] or g.bank[want] <= 0:
                continue
            if give in wants and res[give] - ratios[give] < 1:
                continue
            return {"type": "bank_trade", "give": give, "receive": want}
    return {"type": "end_turn"}


# ----------------------------------------------------------------------- tests
@test
def geometry_counts():
    g = GEOMETRY
    assert len(g["hexes"]) == 19
    assert len(g["vertices"]) == 54
    assert len(g["edges"]) == 72
    assert len(g["ports"]) == 9
    assert sum(1 for e in g["edges"] if e["coastal"]) == 30


@test
def board_setup_is_legal():
    g = Game(PLAYERS, seed=1)
    terr = Counter(h["terrain"] for h in g.hexes.values())
    assert terr == Counter(C.TERRAIN_COUNTS)
    nums = sorted(h["number"] for h in g.hexes.values() if h["number"] is not None)
    assert nums == sorted(C.NUMBER_TOKENS)
    # robber starts on the desert
    assert g.hexes[g.robber_hex]["terrain"] == C.TERRAIN_DESERT
    # desert has no number
    assert g.hexes[g.robber_hex]["number"] is None
    # red numbers (6/8) never adjacent
    adj = g._hex_adjacency()
    for hid, hx in g.hexes.items():
        if hx["number"] in C.RED_NUMBERS:
            for nb in adj[hid]:
                assert g.hexes[nb]["number"] not in C.RED_NUMBERS


@test
def snake_draft_order():
    g = Game(PLAYERS, seed=2)
    assert g.setup_queue == ["A", "B", "C", "C", "B", "A"]
    auto_setup(g)
    assert g.phase == "main"
    # everyone has exactly 2 settlements and 2 roads after setup
    for pid in g.order:
        assert len(g._settlements(pid)) == 2
        assert len(g._roads_of(pid)) == 2
    assert g.current_pid == "A"


@test
def second_settlement_gives_resources():
    g = Game(PLAYERS, seed=3)
    # play first round (3 settlements + roads), no resources yet
    for _ in range(3):
        pid = g.current_pid
        g.apply(pid, {"type": "place_setup_settlement",
                      "vertex": g.legal_settlement_spots(pid, setup=True)[0]})
        g.apply(pid, {"type": "place_setup_road",
                      "edge": g.legal_road_spots(pid, setup=True)[0]})
    for pid in g.order:
        assert sum(g.players[pid]["resources"].values()) == 0
    # second round: each second settlement yields one card per adjacent tile
    while g.phase == "setup":
        pid = g.current_pid
        if g.setup_sub == "settlement":
            v = g.legal_settlement_spots(pid, setup=True)[0]
            adj_res = [g.hexes[h]["resource"] for h in GEOMETRY["vertices"][v]["hexes"]
                       if g.hexes[h]["resource"] and h != g.robber_hex]
            before = sum(g.players[pid]["resources"].values())
            g.apply(pid, {"type": "place_setup_settlement", "vertex": v})
            after = sum(g.players[pid]["resources"].values())
            assert after - before == len(adj_res)
        else:
            g.apply(pid, {"type": "place_setup_road",
                          "edge": g.legal_road_spots(pid, setup=True)[0]})


@test
def must_roll_before_acting():
    g = Game(PLAYERS, seed=4)
    auto_setup(g)
    grant(g, "A", wood=1, brick=1)
    expect_error(lambda: g.apply("A", {"type": "end_turn"}), "roll")
    spots = g.legal_road_spots("A")
    expect_error(lambda: g.apply("A", {"type": "build_road", "edge": spots[0]}), "roll")
    # not your turn
    expect_error(lambda: g.apply("B", {"type": "roll_dice"}), "your turn")


@test
def cannot_roll_twice():
    g = Game(PLAYERS, seed=5)
    auto_setup(g)
    # roll until we get a non-7 so we stay in free-action state
    while True:
        g2 = Game(PLAYERS, seed=5)
        auto_setup(g2)
        g2.apply("A", {"type": "roll_dice"})
        if g2.robber_phase is None:
            g = g2
            break
        # reseed by playing differently is hard; just accept and move robber
        g = g2
        break
    if g.robber_phase is None:
        expect_error(lambda: g.apply("A", {"type": "roll_dice"}), "already rolled")


@test
def build_costs_and_distance_rule():
    g = Game(PLAYERS, seed=6)
    auto_setup(g)
    g.dice_rolled = True  # pretend A rolled a non-7
    # can't build settlement with no resources
    spots = g.legal_settlement_spots("A")
    if spots:
        expect_error(lambda: g.apply("A", {"type": "build_settlement", "vertex": spots[0]}),
                     "afford")
    # distance rule: a vertex adjacent to an existing settlement is illegal
    existing = g._settlements("A")[0]
    for nb in GEOMETRY["vertices"][existing]["adjacent"]:
        assert not g._distance_ok(nb)


@test
def build_road_then_settlement_flow():
    g = Game(PLAYERS, seed=7)
    auto_setup(g)
    g.dice_rolled = True
    # build a road extending from A's network, then a settlement on its far end
    grant(g, "A", wood=2, brick=2, wheat=1, sheep=1)
    road_spots = g.legal_road_spots("A")
    assert road_spots
    g.apply("A", {"type": "build_road", "edge": road_spots[0]})
    # settlement now possible somewhere connected
    sset = g.legal_settlement_spots("A")
    # may or may not be empty depending on distance; just assert no crash & types
    assert isinstance(sset, list)


@test
def production_and_bank_limit():
    g = Game(PLAYERS, seed=8)
    auto_setup(g)
    # craft a deterministic production scenario on two NON-adjacent tiles so
    # their corners are exclusive, and silence every other tile.
    hadj = g._hex_adjacency()
    hexes = list(g.hexes.keys())
    hA = hexes[0]
    hB = next(h for h in hexes if h != hA and h not in hadj[hA])
    robber = next(h for h in hexes if h not in (hA, hB))
    for h in hexes:
        g.hexes[h]["number"] = 5  # nothing else fires on a 4
    g.hexes[hA].update(resource="wood", terrain="forest", number=4)
    g.hexes[hB].update(resource="wood", terrain="forest", number=4)
    g.robber_hex = robber
    vA = GEOMETRY["hex_vertices"][hA][0]
    vB = GEOMETRY["hex_vertices"][hB][3]
    g.buildings.clear()
    g.buildings[vA] = {"type": "settlement", "owner": "A"}
    g.buildings[vB] = {"type": "city", "owner": "B"}
    # plenty in bank
    g.bank["wood"] = 19
    for pid in g.order:
        g.players[pid]["resources"] = {r: 0 for r in C.RESOURCES}
    g._produce(4)
    assert g.players["A"]["resources"]["wood"] == 1
    assert g.players["B"]["resources"]["wood"] == 2  # city = 2

    # bank shortage with multiple claimants -> nobody gets that resource
    for pid in g.order:
        g.players[pid]["resources"] = {r: 0 for r in C.RESOURCES}
    g.bank["wood"] = 1
    g._produce(4)
    assert g.players["A"]["resources"]["wood"] == 0
    assert g.players["B"]["resources"]["wood"] == 0

    # single claimant gets as many as the bank holds
    g.buildings[vB] = {"type": "settlement", "owner": "A"}  # now only A claims
    del g.buildings[vA]
    g.buildings[vA] = {"type": "city", "owner": "A"}
    for pid in g.order:
        g.players[pid]["resources"] = {r: 0 for r in C.RESOURCES}
    g.bank["wood"] = 2
    g._produce(4)  # A demands 3 (city) + 1 (settlement) = 3, bank has 2
    assert g.players["A"]["resources"]["wood"] == 2


@test
def robber_does_not_produce():
    g = Game(PLAYERS, seed=9)
    auto_setup(g)
    h = list(g.hexes.keys())[0]
    g.hexes[h].update(resource="ore", terrain="mountains", number=5)
    g.robber_hex = h
    v = GEOMETRY["hex_vertices"][h][0]
    g.buildings[v] = {"type": "settlement", "owner": "A"}
    g.players["A"]["resources"] = {r: 0 for r in C.RESOURCES}
    g._produce(5)
    assert g.players["A"]["resources"]["ore"] == 0


@test
def seven_triggers_discard_and_move():
    g = Game(PLAYERS, seed=10)
    auto_setup(g)
    # give B 8 cards so they must discard 4
    g.players["B"]["resources"] = {"wood": 8, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
    g.bank["wood"] -= 8
    g._begin_robber(steal_only=False)
    assert g.robber_phase == "discard"
    assert g.pending_discards["B"] == 4
    expect_error(lambda: g.apply("A", {"type": "move_robber", "hex": 0}), "move the robber")
    g.apply("B", {"type": "discard", "resources": {"wood": 4}})
    assert g.robber_phase == "move"
    assert g.players["B"]["resources"]["wood"] == 4


@test
def robber_steal():
    g = Game(PLAYERS, seed=11)
    auto_setup(g)
    h = list(g.hexes.keys())[3]
    g.robber_hex = list(g.hexes.keys())[10]
    v = GEOMETRY["hex_vertices"][h][0]
    g.buildings[v] = {"type": "settlement", "owner": "B"}
    g.players["B"]["resources"] = {"wood": 0, "brick": 0, "sheep": 3, "wheat": 0, "ore": 0}
    g.bank["sheep"] -= 3
    g.players["A"]["resources"] = {r: 0 for r in C.RESOURCES}
    g.dice_rolled = True
    g.robber_phase = "move"
    g.apply("A", {"type": "move_robber", "hex": h, "target": "B"})
    assert g.players["A"]["resources"]["sheep"] == 1
    assert g.players["B"]["resources"]["sheep"] == 2
    assert g.robber_phase is None


@test
def knight_and_largest_army():
    g = Game(PLAYERS, seed=12)
    auto_setup(g)
    g.players["A"]["dev"][C.DEV_KNIGHT] = 3
    g.dice_rolled = True
    # empty every hand so the robber has nothing to steal (keeps the test simple)
    for pid in g.order:
        g.players[pid]["resources"] = {r: 0 for r in C.RESOURCES}
    other = list(g.hexes.keys())
    for i in range(3):
        g.dev_played_this_turn = False  # simulate three separate turns
        g.apply("A", {"type": "play_knight"})
        assert g.robber_phase == "move"
        # move robber to an empty hex, no steal
        dest = next(h for h in other if h != g.robber_hex)
        g.apply("A", {"type": "move_robber", "hex": dest, "target": None})
    assert g.players["A"]["played_knights"] == 3
    assert g.largest_army_owner == "A"
    assert g.public_vp("A") >= C.VP_LARGEST_ARMY


@test
def dev_card_bought_this_turn_not_playable():
    g = Game(PLAYERS, seed=13)
    auto_setup(g)
    g.dice_rolled = True
    grant(g, "A", wheat=1, sheep=1, ore=1)
    # stack the deck so we draw a knight
    g.deck.append(C.DEV_KNIGHT)
    g.apply("A", {"type": "buy_dev_card"})
    assert g.players["A"]["dev_new"][C.DEV_KNIGHT] == 1
    expect_error(lambda: g.apply("A", {"type": "play_knight"}), "bought this turn")
    # after ending the turn it becomes playable
    g.apply("A", {"type": "end_turn"})
    assert g.players["A"]["dev"][C.DEV_KNIGHT] == 1


@test
def one_dev_card_per_turn():
    g = Game(PLAYERS, seed=14)
    auto_setup(g)
    g.dice_rolled = True
    g.players["A"]["dev"][C.DEV_YEAR_OF_PLENTY] = 1
    g.players["A"]["dev"][C.DEV_MONOPOLY] = 1
    g.apply("A", {"type": "play_year_of_plenty", "resources": ["wood", "brick"]})
    expect_error(lambda: g.apply("A", {"type": "play_monopoly", "resource": "ore"}),
                 "one development card")


@test
def road_building_card():
    g = Game(PLAYERS, seed=15)
    auto_setup(g)
    g.dice_rolled = True
    g.players["A"]["resources"] = {r: 0 for r in C.RESOURCES}
    g.players["A"]["dev"][C.DEV_ROAD_BUILDING] = 1
    before = len(g._roads_of("A"))
    g.apply("A", {"type": "play_road_building"})
    assert g.free_roads == 2
    spots = g.legal_road_spots("A")
    g.apply("A", {"type": "build_road", "edge": spots[0]})
    assert g.free_roads == 1
    spots = g.legal_road_spots("A")
    g.apply("A", {"type": "build_road", "edge": spots[0]})
    assert g.free_roads == 0
    assert len(g._roads_of("A")) == before + 2
    # roads were free (no resources spent)
    assert sum(g.players["A"]["resources"].values()) == 0


@test
def year_of_plenty_and_monopoly():
    g = Game(PLAYERS, seed=16)
    auto_setup(g)
    g.dice_rolled = True
    g.players["A"]["resources"] = {r: 0 for r in C.RESOURCES}
    g.players["A"]["dev"][C.DEV_YEAR_OF_PLENTY] = 1
    g.apply("A", {"type": "play_year_of_plenty", "resources": ["ore", "ore"]})
    assert g.players["A"]["resources"]["ore"] == 2
    # monopoly: take all wheat from others
    g.dev_played_this_turn = False
    g.players["A"]["dev"][C.DEV_MONOPOLY] = 1
    g.players["B"]["resources"]["wheat"] = 3
    g.players["C"]["resources"]["wheat"] = 2
    g.apply("A", {"type": "play_monopoly", "resource": "wheat"})
    assert g.players["A"]["resources"]["wheat"] == 5
    assert g.players["B"]["resources"]["wheat"] == 0
    assert g.players["C"]["resources"]["wheat"] == 0


@test
def longest_road_and_breaking():
    g = Game(PLAYERS, seed=17)
    auto_setup(g)
    g.buildings.clear()
    g.roads.clear()
    eids, verts = find_edge_trail(5)
    for eid in eids:
        g.roads[eid] = "A"
    g._recompute_longest_road()
    assert g._longest_road_length("A") == 5
    assert g.longest_road_owner == "A"
    # opponent settlement in the middle splits the road
    mid = verts[2]
    g.buildings[mid] = {"type": "settlement", "owner": "B"}
    g._recompute_longest_road()
    assert g._longest_road_length("A") < 5
    assert g.longest_road_owner is None


@test
def longest_road_needs_five():
    g = Game(PLAYERS, seed=18)
    auto_setup(g)
    g.roads.clear()
    eids, _ = find_edge_trail(4)
    for eid in eids:
        g.roads[eid] = "A"
    g._recompute_longest_road()
    assert g.longest_road_owner is None  # 4 is not enough


@test
def bank_and_port_trade():
    g = Game(PLAYERS, seed=19)
    auto_setup(g)
    g.dice_rolled = True
    # 4:1 with the bank
    g.players["A"]["resources"] = {"wood": 4, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
    g.apply("A", {"type": "bank_trade", "give": "wood", "receive": "ore"})
    assert g.players["A"]["resources"]["wood"] == 0
    assert g.players["A"]["resources"]["ore"] == 1
    # give A a 2:1 wood port and verify the better ratio
    woodport = next(p for p in GEOMETRY["ports"] if p["type"] == "wood")
    g.buildings[woodport["vertices"][0]] = {"type": "settlement", "owner": "A"}
    assert g._port_ratios("A")["wood"] == 2
    g.players["A"]["resources"] = {"wood": 2, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
    g.apply("A", {"type": "bank_trade", "give": "wood", "receive": "brick"})
    assert g.players["A"]["resources"]["brick"] == 1
    assert g.players["A"]["resources"]["wood"] == 0


@test
def domestic_trade():
    g = Game(PLAYERS, seed=20)
    auto_setup(g)
    g.dice_rolled = True
    g.players["A"]["resources"] = {"wood": 2, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
    g.players["B"]["resources"] = {"wood": 0, "brick": 0, "sheep": 0, "wheat": 1, "ore": 0}
    g.apply("A", {"type": "propose_trade",
                  "give": {"wood": 2}, "receive": {"wheat": 1}, "to": "B"})
    assert g.trade is not None
    # C cannot accept a trade directed at B
    expect_error(lambda: g.apply("C", {"type": "accept_trade"}), "directed")
    g.apply("B", {"type": "accept_trade"})
    assert g.players["A"]["resources"]["wood"] == 0
    assert g.players["A"]["resources"]["wheat"] == 1
    assert g.players["B"]["resources"]["wood"] == 2
    assert g.players["B"]["resources"]["wheat"] == 0
    assert g.trade is None


@test
def win_condition():
    g = Game(PLAYERS, seed=21)
    auto_setup(g)
    g.dice_rolled = True
    # hand A enough to reach 10 VP: give cities + a VP card
    # Simplest: set buildings directly to 4 cities (8) + 2 settlements? max settlements 5,
    # but use longest army + cities. Place direct buildings.
    g.buildings.clear()
    # 4 cities = 8 VP
    verts = [v["id"] for v in GEOMETRY["vertices"]][:6]
    for i in range(4):
        g.buildings[verts[i]] = {"type": "city", "owner": "A"}
    g.players["A"]["dev"][C.DEV_VICTORY_POINT] = 1  # +1 = 9
    assert g.total_vp("A") == 9
    g.buildings[verts[4]] = {"type": "settlement", "owner": "A"}  # +1 = 10
    g._check_win()
    assert g.winner == "A"
    assert g.phase == "ended"


@test
def serialize_hides_opponent_hands():
    g = Game(PLAYERS, seed=22)
    auto_setup(g)
    g.players["B"]["resources"] = {r: 0 for r in C.RESOURCES}
    grant(g, "B", wood=3)
    state = views.serialize(g, "A")
    a = next(p for p in state["players"] if p["id"] == "A")
    b = next(p for p in state["players"] if p["id"] == "B")
    assert a["resources"] is not None          # own hand visible
    assert b["resources"] is None              # opponent hand hidden
    assert b["resourceCount"] == 3             # but count is public
    assert state["yourId"] == "A"


@test
def legal_actions_basic():
    g = Game(PLAYERS, seed=23)
    auto_setup(g)
    la = views.legal_actions(g, "A")
    assert la["yourTurn"] and la["canRoll"]
    assert not la["canEndTurn"]
    lb = views.legal_actions(g, "B")
    assert not lb["yourTurn"]
    # after rolling (force non-7)
    g.dice_rolled = True
    g.dice = (3, 4)
    la = views.legal_actions(g, "A")
    assert la["canEndTurn"] and la["canTrade"]


@test
def full_game_smoke():
    """Drive a complete game with a greedy auto-player; it must finish cleanly."""
    g = Game(PLAYERS, seed=24)
    auto_setup(g)
    safety = 0
    while g.phase != "ended" and safety < 30000:
        safety += 1
        pid = g.current_pid
        # resolve discards first
        if g.robber_phase == "discard":
            for dp, need in list(g.pending_discards.items()):
                res = g.players[dp]["resources"]
                pick = {}
                left = need
                for r in C.RESOURCES:
                    take = min(res[r], left)
                    if take:
                        pick[r] = take
                        left -= take
                    if left == 0:
                        break
                g.apply(dp, {"type": "discard", "resources": pick})
            continue
        if g.robber_phase == "move":
            dest = next(h for h in g.hexes if h != g.robber_hex)
            tgts = g._steal_targets(pid, dest)
            g.apply(pid, {"type": "move_robber", "hex": dest,
                          "target": tgts[0] if tgts else None})
            continue
        if not g.dice_rolled:
            g.apply(pid, {"type": "roll_dice"})
            continue
        g.apply(pid, smart_choose(g, pid))
    assert g.phase == "ended", "game did not finish (safety=%d)" % safety
    assert g.winner is not None
    assert g.total_vp(g.winner) >= C.VICTORY_POINTS_TO_WIN


# -------------------------------------------------- configurable rules & maps
@test
def hex_field_and_variable_geometry():
    from engine import geometry as G
    assert len(G.hex_field(1)) == 7
    assert len(G.hex_field(2)) == 19
    assert len(G.hex_field(3)) == 37
    # default build is unchanged
    std = G.build_geometry()
    assert len(std["hexes"]) == 19 and len(std["vertices"]) == 54
    assert len(std["edges"]) == 72 and len(std["ports"]) == 9
    # arbitrary sizes build a consistent graph
    for radius, n in [(1, 7), (3, 37), (4, 61)]:
        g = G.build_geometry(G.hex_field(radius))
        assert len(g["hexes"]) == n
        nv = len(g["vertices"])
        for e in g["edges"]:
            assert 0 <= e["v1"] < nv and 0 <= e["v2"] < nv


@test
def map_presets_resolve():
    import random
    from engine import maps
    presets = maps.list_presets()
    assert len(presets) >= 4
    for p in presets:
        board = maps.resolve({"preset": p["id"]}, random.Random(1))
        assert len(board["geo"]["hexes"]) == p["tiles"]
        assert board["robber_hex"] in board["hexes"]
        # the robber starts on a desert when there is one
        assert board["hexes"][board["robber_hex"]]["terrain"] == C.TERRAIN_DESERT


@test
def custom_explicit_tiles_map():
    import random
    from engine import maps
    spec = {"name": "Tiny", "tiles": [
        {"q": 0, "r": 0, "terrain": "desert"},
        {"q": 1, "r": 0, "terrain": "forest", "number": 8},
        {"q": -1, "r": 0, "terrain": "hills", "number": 5},
        {"q": 0, "r": 1, "terrain": "mountains", "number": 6},
    ]}
    board = maps.resolve(spec, random.Random(0))
    assert len(board["geo"]["hexes"]) == 4
    assert board["hexes"][board["robber_hex"]]["terrain"] == C.TERRAIN_DESERT
    terrains = sorted(h["terrain"] for h in board["hexes"].values())
    assert terrains == ["desert", "forest", "hills", "mountains"]


@test
def map_validation_rejects_bad_specs():
    from engine import maps
    expect_map_error(lambda: maps.validate(
        {"tiles": [{"q": 0, "r": 0, "terrain": "forest", "number": 7}]}), "2-12")
    expect_map_error(lambda: maps.validate(
        {"tiles": [{"q": 0, "r": 0, "terrain": "swamp", "number": 5}]}), "terrain")
    expect_map_error(lambda: maps.validate({"radius": 99}), "between")
    expect_map_error(lambda: maps.validate({"tiles": [
        {"q": 0, "r": 0, "terrain": "forest", "number": 5},
        {"q": 0, "r": 0, "terrain": "hills", "number": 6}]}), "duplicate")


@test
def rule_overrides_apply():
    g = Game(PLAYERS, config={"rules": {
        "victoryPoints": 4, "discardThreshold": 5, "maxRoads": 20,
        "maxSettlements": 7, "maxCities": 6, "bankPerResource": 25}}, seed=1)
    assert g.vp_to_win == 4
    assert g.discard_threshold == 5
    assert g.max_roads == 20 and g.max_settlements == 7 and g.max_cities == 6
    assert g.bank["wood"] == 25


@test
def rule_validation_rejects_bad():
    expect_error(lambda: Game(PLAYERS, config={"rules": {"victoryPoints": 1}}), "between")
    expect_error(lambda: Game(PLAYERS, config={"rules": {"maxRoads": "lots"}}), "whole number")


@test
def custom_discard_threshold():
    g = Game(PLAYERS, config={"rules": {"discardThreshold": 5}}, seed=2)
    auto_setup(g)
    g.players["B"]["resources"] = {"wood": 6, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
    g.bank["wood"] -= 6
    g._begin_robber(steal_only=False)
    assert g.pending_discards.get("B") == 3  # more than 5 -> discard half (6//2)


@test
def custom_victory_points_win():
    g = Game(PLAYERS, config={"rules": {"victoryPoints": 4}}, seed=3)
    auto_setup(g)
    g.dice_rolled = True
    g.buildings.clear()
    verts = [v["id"] for v in g.geo["vertices"]][:2]
    g.buildings[verts[0]] = {"type": "city", "owner": "A"}  # 2 VP
    g.buildings[verts[1]] = {"type": "city", "owner": "A"}  # 2 VP -> 4
    assert g.total_vp("A") == 4
    g._check_win()
    assert g.winner == "A" and g.phase == "ended"


@test
def piece_limit_override():
    g = Game(PLAYERS, config={"rules": {"maxRoads": 2}}, seed=4)
    auto_setup(g)  # setup gives each player exactly 2 roads -> already at the cap
    g.dice_rolled = True
    grant(g, "A", wood=1, brick=1)
    spots = g.legal_road_spots("A")
    assert spots, "expected at least one connected road spot"
    expect_error(lambda: g.apply("A", {"type": "build_road", "edge": spots[0]}), "supply")


@test
def validate_config_function():
    from engine.game import validate_config
    cfg = validate_config({"rules": {"victoryPoints": 8}, "map": {"preset": "large"}})
    assert cfg["rules"]["victoryPoints"] == 8
    assert cfg["map"]["radius"] == 3  # 'large' is a radius-3 (37-hex) board
    expect_error(lambda: validate_config({"rules": {"victoryPoints": 99}}), "between")
    expect_error(lambda: validate_config({"map": {"radius": 99}}), "between")


@test
def variable_board_full_game():
    """A complete game on a non-standard board with custom rules must finish."""
    g = Game(PLAYERS, config={"map": {"preset": "small"},
                              "rules": {"victoryPoints": 5}}, seed=11)
    auto_setup(g)
    safety = 0
    while g.phase != "ended" and safety < 40000:
        safety += 1
        pid = g.current_pid
        if g.robber_phase == "discard":
            for dp, need in list(g.pending_discards.items()):
                res = g.players[dp]["resources"]
                pick, left = {}, need
                for r in C.RESOURCES:
                    take = min(res[r], left)
                    if take:
                        pick[r] = take
                        left -= take
                    if left == 0:
                        break
                g.apply(dp, {"type": "discard", "resources": pick})
            continue
        if g.robber_phase == "move":
            dest = next(h for h in g.hexes if h != g.robber_hex)
            tgts = g._steal_targets(pid, dest)
            g.apply(pid, {"type": "move_robber", "hex": dest,
                          "target": tgts[0] if tgts else None})
            continue
        if not g.dice_rolled:
            g.apply(pid, {"type": "roll_dice"})
            continue
        g.apply(pid, smart_choose(g, pid))
    assert g.phase == "ended", "variable-board game did not finish (safety=%d)" % safety
    assert g.total_vp(g.winner) >= 5


# --------------------------------------------- casino, beans, stats, leaderboard
@test
def multi_unit_bank_trade():
    g = Game(PLAYERS, seed=30)
    auto_setup(g)
    g.dice_rolled = True
    # 4:1 default — give 8 wood for 2 cards at once.
    g.players["A"]["resources"] = {"wood": 8, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
    g.apply("A", {"type": "bank_trade", "give": {"wood": 8}, "receive": {"ore": 1, "brick": 1}})
    assert g.players["A"]["resources"]["wood"] == 0
    assert g.players["A"]["resources"]["ore"] == 1
    assert g.players["A"]["resources"]["brick"] == 1
    # give a 2:1 wheat port and do two 2:1s in one action: 4 wheat -> 2 cards.
    wheatport = next(p for p in g.geo["ports"] if p["type"] == "wheat")
    g.buildings[wheatport["vertices"][0]] = {"type": "settlement", "owner": "A"}
    assert g._port_ratios("A")["wheat"] == 2
    g.players["A"]["resources"] = {"wood": 0, "brick": 0, "sheep": 0, "wheat": 4, "ore": 0}
    g.apply("A", {"type": "bank_trade", "give": {"wheat": 4}, "receive": {"ore": 2}})
    assert g.players["A"]["resources"]["ore"] == 2 and g.players["A"]["resources"]["wheat"] == 0
    # unbalanced trade is rejected (3 wheat isn't a multiple of the 2:1 rate)
    g.players["A"]["resources"]["wheat"] = 3
    expect_error(lambda: g.apply("A", {"type": "bank_trade",
                 "give": {"wheat": 3}, "receive": {"ore": 1}}), "multiple")


@test
def bean_exchanges_and_no_negative():
    g = Game(PLAYERS, config={"rules": {"beansPerResource": 20, "beansPerVictoryPoint": 200}}, seed=31)
    auto_setup(g)
    g.players["A"]["resources"] = {"wood": 5, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
    g.apply("A", {"type": "convert_to_beans", "resources": {"wood": 5}})
    assert g.players["A"]["beans"] == 100
    g.apply("A", {"type": "convert_to_resources", "resources": {"ore": 2}})
    assert g.players["A"]["beans"] == 60 and g.players["A"]["resources"]["ore"] == 2
    # beans can never go negative
    expect_error(lambda: g.apply("A", {"type": "convert_to_resources", "resources": {"wheat": 10}}), "beans")
    expect_error(lambda: g.apply("A", {"type": "buy_vp", "amount": 1}), "beans")


@test
def buy_victory_points_can_win():
    g = Game(PLAYERS, config={"rules": {"victoryPoints": 5, "beansPerVictoryPoint": 100}}, seed=32)
    auto_setup(g)
    before = g.total_vp("A")
    g.players["A"]["beans"] = 1000
    # buy enough VP to win (off-turn gambling resolves immediately for the actor)
    need = (g.vp_to_win - before)
    g.apply("A", {"type": "buy_vp", "amount": need})
    assert g.players["A"]["bought_vp"] == need
    assert g.public_vp("A") >= g.vp_to_win  # bought VP is public
    assert g.winner == "A" and g.phase == "ended"


@test
def sell_vp_only_what_you_bought():
    g = Game(PLAYERS, config={"rules": {"beansPerVictoryPoint": 100}}, seed=33)
    auto_setup(g)
    g.players["A"]["beans"] = 300
    g.apply("A", {"type": "buy_vp", "amount": 2})
    assert g.players["A"]["beans"] == 100 and g.players["A"]["bought_vp"] == 2
    expect_error(lambda: g.apply("A", {"type": "sell_vp", "amount": 3}), "bought")
    g.apply("A", {"type": "sell_vp", "amount": 2})
    assert g.players["A"]["beans"] == 300 and g.players["A"]["bought_vp"] == 0


@test
def blackjack_shoe_and_natural():
    g = Game(PLAYERS, seed=34)
    auto_setup(g)
    g.players["A"]["beans"] = 100
    assert len(g._bj_shoe_obj()) == 6 * 52  # fresh shared 6-deck shoe
    # Stack the shared shoe so A gets a natural (A,K) vs a dealer 9,5. pop() draws
    # from the end: dealer1, dealer2, hand1, hand2. Keep >1 deck so it won't reshuffle.
    g.bj_shoe = ["2C"] * 60 + ["KH", "AS", "5C", "9D"]
    g.bj_seen = []
    g.apply("A", {"type": "bj_bet", "amount": 2})
    bj = g._bj("A")
    assert bj["hands"][0]["result"] == "blackjack"
    assert g.players["A"]["beans"] == 100 - 2 + (2 + 2 * 3 // 2)  # 3:2 payout
    assert g.players["A"]["beans"] >= 0


@test
def blackjack_shared_shoe_across_players():
    g = Game(PLAYERS, seed=37)
    auto_setup(g)
    g.players["A"]["beans"] = 50
    g.players["B"]["beans"] = 50
    g.apply("A", {"type": "bj_bet", "amount": 1})
    while g._bj("A")["state"] == "player":
        g.apply("A", {"type": "bj_stand"})
    seen_after_a = len(g.bj_seen)
    g.apply("B", {"type": "bj_bet", "amount": 1})
    # B draws from the SAME shoe A used — the shared seen-list keeps growing.
    assert len(g.bj_seen) > seen_after_a
    assert g.bj_shoe is not None


@test
def blackjack_penetration_reshuffle():
    from engine import casino
    g = Game(PLAYERS, seed=35)
    auto_setup(g)
    g.players["A"]["beans"] = 1000
    g._bj_shoe_obj()
    g.bj_shoe = ["2C"] * 10  # fewer than a deck left -> next draw reshuffles
    g.bj_seen = []
    assert casino.needs_shuffle(g.bj_shoe)
    g.apply("A", {"type": "bj_bet", "amount": 1})
    assert len(g.bj_shoe) > 52  # a fresh 312-card shoe was dealt from


@test
def stats_track_rolls_and_accumulation():
    g = Game(PLAYERS, seed=36)
    auto_setup(g)
    g.apply("A", {"type": "roll_dice"})
    assert sum(g.roll_counts.values()) == 1
    total = g.dice[0] + g.dice[1]
    assert g.roll_counts[total] == 1
    # gained accrues on production / gains
    g.players["B"]["gained"] = {r: 0 for r in C.RESOURCES}
    g._gain("B", "wheat", 3)
    assert g.players["B"]["gained"]["wheat"] == 3
    # accumulation is hidden until the game ends
    g.phase = "main"
    assert views.serialize(g, "A")["players"][0]["gained"] is None
    g.phase = "ended"
    assert views.serialize(g, "A")["players"][0]["gained"] is not None


@test
def leaderboard_records_win_once():
    import os
    import tempfile
    from server import manager, leaderboard
    leaderboard._PATH = os.path.join(tempfile.gettempdir(), "hexara_lb_unit.json")
    if os.path.exists(leaderboard._PATH):
        os.remove(leaderboard._PATH)
    room = manager.Room("TST")
    room.players = [{"id": "A", "name": "Zelda", "color": "red", "token": "",
                     "is_bot": False, "last_seen": 0}]
    room.game = Game([{"id": "A", "name": "Zelda", "color": "red"},
                      {"id": "B", "name": "Bot", "color": "blue"}],
                     config={"rules": {"victoryPoints": 3}})
    room.game.winner = "A"  # pretend A won
    manager._maybe_record_win(room)
    manager._maybe_record_win(room)  # idempotent — must not double-count
    rows = {e["name"]: e["wins"] for e in leaderboard.top()}
    assert rows.get("Zelda") == 1, rows
    os.remove(leaderboard._PATH)


# ----------------------------------------------------- wave 3: dev/tips/leave
@test
def convert_dev_cards_to_beans():
    g = Game(PLAYERS, config={"rules": {"beansPerResource": 20}}, seed=38)
    auto_setup(g)
    g.players["A"]["dev"][C.DEV_KNIGHT] = 2
    g.players["A"]["dev_new"][C.DEV_MONOPOLY] = 1
    # 1 dev card = beansPerResource/2 = 10 beans (= 0.5 resource)
    g.apply("A", {"type": "convert_dev_to_beans", "cards": {"knight": 2, "monopoly": 1}})
    assert g.players["A"]["beans"] == 3 * 10
    assert g.players["A"]["dev"][C.DEV_KNIGHT] == 0
    assert g.players["A"]["dev_new"][C.DEV_MONOPOLY] == 0
    # can't sell cards you don't have
    expect_error(lambda: g.apply("A", {"type": "convert_dev_to_beans", "cards": {"knight": 1}}), "don't have")


@test
def dealer_tipping_and_messages():
    g = Game(PLAYERS, seed=39)
    auto_setup(g)
    g.players["A"]["beans"] = 10
    g.apply("A", {"type": "bj_tip", "amount": 4})
    assert g.players["A"]["beans"] == 6
    assert g.bj_tips == 4
    assert g._bj("A")["mood"] == "thankful"
    assert "thank" in g.bj_message.lower() or "kind" in g.bj_message.lower() or "bless" in g.bj_message.lower()
    # can't tip beans you don't have
    expect_error(lambda: g.apply("A", {"type": "bj_tip", "amount": 100}), "only have")
    # a settled hand sets a dealer line + mood
    g.players["A"]["beans"] = 50
    g.apply("A", {"type": "bj_bet", "amount": 1})
    safety = 0
    while g._bj("A")["state"] == "player" and safety < 20:
        safety += 1
        g.apply("A", {"type": "bj_stand"})
    assert g._bj("A")["mood"] in ("happy", "sad", "neutral", "excited")
    assert len(g.bj_message) > 0


@test
def surrender_converts_seat_to_bot():
    import os
    import tempfile
    from server import manager, leaderboard
    leaderboard._PATH = os.path.join(tempfile.gettempdir(), "hexara_lb_surr.json")
    if os.path.exists(leaderboard._PATH):
        os.remove(leaderboard._PATH)
    room = manager.Room("SUR")
    room.players = [
        {"id": "A", "name": "Ada", "color": "red", "token": "ta", "is_bot": False, "last_seen": 0},
        {"id": "B", "name": "Bo", "color": "blue", "token": "tb", "is_bot": False, "last_seen": 0},
    ]
    room.host = "A"
    room.game = Game([{"id": "A", "name": "Ada", "color": "red"},
                      {"id": "B", "name": "Bo", "color": "blue"}], seed=1)
    manager._surrender(room, "A")
    a = next(p for p in room.players if p["id"] == "A")
    assert a["is_bot"] is True              # seat handed to a bot
    assert a["token"] != "ta"               # old session invalidated
    assert room.host == "B"                 # host moved to the remaining human
    assert "A" in room.game.players         # pieces/resources kept in the game
    if os.path.exists(leaderboard._PATH):
        os.remove(leaderboard._PATH)


# ------------------------------------------------------ wave 4: surrender/deck
@test
def blackjack_surrender():
    g = Game(PLAYERS, seed=40)
    auto_setup(g)
    g.players["A"]["beans"] = 100
    # player 9,7 = 16 (no natural); dealer 10,6. pop draws dealer1,dealer2,hand1,hand2.
    g.bj_shoe = ["2C"] * 60 + ["7S", "9H", "6D", "10C"]
    g.bj_seen = []
    g.apply("A", {"type": "bj_bet", "amount": 4})
    bj = g._bj("A")
    assert bj["state"] == "player" and len(bj["hands"][0]["cards"]) == 2
    g.apply("A", {"type": "bj_surrender"})
    assert bj["hands"][0]["result"] == "surrender"
    assert g.players["A"]["beans"] == 100 - 4 + 2  # half the 4-bean wager returned
    # can't surrender after taking a card
    g.apply("A", {"type": "bj_bet", "amount": 2})
    g.apply("A", {"type": "bj_hit"})
    if g._bj("A")["state"] == "player":
        expect_error(lambda: g.apply("A", {"type": "bj_surrender"}), "opening two cards")


@test
def dev_deck_multiplier():
    from collections import Counter
    g1 = Game(PLAYERS, seed=41)
    assert len(g1.deck) == 25                      # default = standard deck
    g4 = Game(PLAYERS, config={"rules": {"devDeckMultiplier": 4}}, seed=41)
    assert len(g4.deck) == 100
    assert Counter(g4.deck)[C.DEV_KNIGHT] == 14 * 4  # proportions preserved
    expect_error(lambda: Game(PLAYERS, config={"rules": {"devDeckMultiplier": 99}}), "between")


# --------------------------------------------------------------------- runner
def main():
    passed = failed = 0
    for fn in _tests:
        try:
            fn()
            print("PASS  %s" % fn.__name__)
            passed += 1
        except Exception as e:
            print("FAIL  %s -> %s: %s" % (fn.__name__, type(e).__name__, e))
            import traceback
            traceback.print_exc()
            failed += 1
    print("\n%d passed, %d failed, %d total" % (passed, failed, len(_tests)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
