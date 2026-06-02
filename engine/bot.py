"""A reasonable computer opponent.

The bot returns a single action for whatever decision the engine is waiting on
(setup placement, rolling, the robber, discards, or a main-phase move). The
server drives it: after each state change it asks the bot for the next action
for any bot whose turn/decision it is, applies it, and broadcasts.

The strategy is deliberately simple but coherent: value city upgrades, expand,
buy and use development cards, and bank-trade toward whatever the next build
needs. It plays a legal, finite turn and always ends.
"""

from . import constants as C
from .geometry import GEOMETRY


def next_action(game, pid):
    """Return one action dict for ``pid``, who must currently act."""
    g = game
    if g.robber_phase == "discard" and pid in g.pending_discards:
        return _discard(g, pid)
    if g.robber_phase == "move" and g.current_pid == pid:
        return _robber(g, pid)
    if g.phase == "setup" and g.current_pid == pid:
        return _setup(g, pid)
    if g.phase == "main" and g.current_pid == pid:
        if not g.dice_rolled:
            return {"type": "roll_dice"}
        return _main(g, pid)
    return None


def _setup(g, pid):
    if g.setup_sub == "settlement":
        spots = g.legal_settlement_spots(pid, setup=True)
        best = max(spots, key=lambda v: _vertex_value(g, v))
        return {"type": "place_setup_settlement", "vertex": best}
    spots = g.legal_road_spots(pid, setup=True)
    return {"type": "place_setup_road", "edge": spots[0]}


def _vertex_value(g, vid):
    score = 0.0
    seen = set()
    for hid in GEOMETRY["vertices"][vid]["hexes"]:
        num = g.hexes[hid]["number"]
        if num:
            score += C.NUMBER_PIPS.get(num, 0)
        res = g.hexes[hid]["resource"]
        if res and res not in seen:
            score += 0.5  # reward resource diversity
            seen.add(res)
    return score


def _main(g, pid):
    p = g.players[pid]
    res = p["resources"]

    # Use up free roads from a Road Building card first.
    if g.free_roads > 0:
        spots = g.legal_road_spots(pid)
        if spots:
            return {"type": "build_road", "edge": spots[0]}

    if not g.dev_played_this_turn:
        if p["dev"].get(C.DEV_KNIGHT, 0) > 0:
            return {"type": "play_knight"}
        if p["dev"].get(C.DEV_YEAR_OF_PLENTY, 0) > 0:
            need = []
            need += ["ore"] * max(0, 3 - res["ore"])
            need += ["wheat"] * max(0, 2 - res["wheat"])
            need = [r for r in need if g.bank[r] > 0][:2]
            if len(need) == 2:
                return {"type": "play_year_of_plenty", "resources": need}
        if p["dev"].get(C.DEV_MONOPOLY, 0) > 0:
            totals = {r: 0 for r in C.RESOURCES}
            for o in g.order:
                if o != pid:
                    for r in C.RESOURCES:
                        totals[r] += g.players[o]["resources"][r]
            r = max(totals, key=lambda k: totals[k])
            if totals[r] >= 3:
                return {"type": "play_monopoly", "resource": r}
        if p["dev"].get(C.DEV_ROAD_BUILDING, 0) > 0 and len(g._roads_of(pid)) < C.MAX_ROADS:
            if g.legal_road_spots(pid):
                return {"type": "play_road_building"}

    if g._has(pid, C.COST_CITY) and g._settlements(pid):
        return {"type": "build_city", "vertex": g._settlements(pid)[0]}
    if g._has(pid, C.COST_SETTLEMENT):
        spots = g.legal_settlement_spots(pid)
        if spots:
            best = max(spots, key=lambda v: _vertex_value(g, v))
            return {"type": "build_settlement", "vertex": best}
    if g._has(pid, C.COST_ROAD) and len(g._roads_of(pid)) < C.MAX_ROADS:
        spots = g.legal_road_spots(pid)
        if spots:
            return {"type": "build_road", "edge": spots[0]}
    if g._has(pid, C.COST_DEV_CARD) and g.deck:
        return {"type": "buy_dev_card"}

    trade = _bank_trade_toward_goal(g, pid)
    if trade:
        return trade
    return {"type": "end_turn"}


def _bank_trade_toward_goal(g, pid):
    res = g.players[pid]["resources"]
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
    return None


def _robber(g, pid):
    best_hex, best_score = None, -1e9
    for h in g.hexes:
        if h == g.robber_hex:
            continue
        owners = {}
        on_self = False
        for v in GEOMETRY["hex_vertices"][h]:
            b = g.buildings.get(v)
            if not b:
                continue
            if b["owner"] == pid:
                on_self = True
            else:
                owners[b["owner"]] = owners.get(b["owner"], 0) + (2 if b["type"] == "city" else 1)
        score = 0.0
        for o in owners:
            score = max(score, g.public_vp(o) * 10 + g._hand_size(o))
        if on_self:
            score -= 100
        if score > best_score:
            best_score, best_hex = score, h
    if best_hex is None:
        best_hex = next(h for h in g.hexes if h != g.robber_hex)
    targets = g._steal_targets(pid, best_hex)
    target = max(targets, key=lambda o: g._hand_size(o)) if targets else None
    return {"type": "move_robber", "hex": best_hex, "target": target}


def _discard(g, pid):
    res = dict(g.players[pid]["resources"])
    need = g.pending_discards[pid]
    pick = {}
    for _ in range(need):
        r = max(res, key=lambda k: res[k])  # shave the largest stack each time
        if res[r] <= 0:
            break
        res[r] -= 1
        pick[r] = pick.get(r, 0) + 1
    return {"type": "discard", "resources": pick}
