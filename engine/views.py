"""Per-player serialization and legal-move computation.

``serialize`` turns a :class:`~engine.game.Game` into JSON-ready state from one
player's perspective, masking information that player shouldn't see (opponents'
exact hands and development cards). ``legal_actions`` tells the client exactly
which moves the player may make right now, so the UI can highlight valid spots
and enable the right buttons. The client never needs the rules itself.
"""

from . import casino
from . import constants as C


def serialize(game, viewer):
    g = game
    players = []
    for pid in g.order:
        p = g.players[pid]
        is_self = (pid == viewer)
        entry = {
            "id": pid,
            "name": p["name"],
            "color": p["color"],
            "vp": g.public_vp(pid),
            "resourceCount": sum(p["resources"].values()),
            "devCount": g._dev_count(pid),
            "playedKnights": p["played_knights"],
            "builtSettlements": len(g._settlements(pid)),
            "builtCities": len(g._cities(pid)),
            "builtRoads": len(g._roads_of(pid)),
            "roadsLeft": g.max_roads - len(g._roads_of(pid)),
            "settlementsLeft": g.max_settlements - len(g._settlements(pid)),
            "citiesLeft": g.max_cities - len(g._cities(pid)),
            "hasLongestRoad": g.longest_road_owner == pid,
            "hasLargestArmy": g.largest_army_owner == pid,
            "ports": _owned_ports(g, pid),
            "beans": p.get("beans", 0),          # public gambling balance
            "boughtVp": p.get("bought_vp", 0),   # VP bought with beans (public)
            # Resource accumulation is only revealed once the game is over.
            "gained": dict(p["gained"]) if g.phase == "ended" else None,
            # Self-only private information:
            "resources": dict(p["resources"]) if is_self else None,
            "dev": dict(p["dev"]) if is_self else None,
            "devNew": dict(p["dev_new"]) if is_self else None,
            "casino": _casino_view(g, pid) if is_self else None,
        }
        if pid == g.winner:
            entry["vp"] = g.total_vp(pid)  # reveal winner's true total
        players.append(entry)

    return {
        "phase": g.phase,
        "winner": g.winner,
        "currentPlayer": g.current_pid,
        "yourId": viewer,
        "rules": g.rules_view(),
        "mapName": g.map_spec.get("name") if getattr(g, "map_spec", None) else None,
        "order": list(g.order),
        "setup": {"sub": g.setup_sub} if g.phase == "setup" else None,
        "dice": list(g.dice) if g.dice else None,
        "diceRolled": g.dice_rolled,
        "robberPhase": g.robber_phase,
        "robberHex": g.robber_hex,
        "pendingDiscards": dict(g.pending_discards),
        "freeRoads": g.free_roads,
        "trade": _trade_view(g),
        "bank": dict(g.bank),
        "devDeckCount": len(g.deck),
        "longestRoadOwner": g.longest_road_owner,
        "longestRoadLen": g.longest_road_len,
        "largestArmyOwner": g.largest_army_owner,
        "rollStats": dict(g.roll_counts),
        "players": players,
        "board": _board_view(g),
        "log": g.log[-60:],
    }


def _casino_view(g, pid):
    """The viewer's private casino state: beans, exchange rates, the shared
    table (one shoe + every player's hands so counting & the table are communal)
    and the viewer's own hand."""
    p = g.players[pid]
    beans = p.get("beans", 0)
    bj = p.get("bj")
    out = {
        "beans": beans,
        "boughtVp": p.get("bought_vp", 0),
        "minBet": C.BLACKJACK_MIN_BET,
        "beansPerResource": g.beans_per_resource,
        "beansPerVp": g.beans_per_vp,
        "beansPerDev": g.beans_per_resource // 2,
        "dev": dict(p["dev"]),            # development cards you can cash in
        "devNew": dict(p["dev_new"]),
        # shared table
        "shoeLeft": len(g.bj_shoe) if g.bj_shoe else C.BLACKJACK_DECKS * 52,
        "decks": C.BLACKJACK_DECKS,
        "seen": _public_seen(g),  # excludes face-down dealer hole cards
        "tips": g.bj_tips,
        "countBias": round(g.bj_count_bias, 2),
        "canCashDev": g.gamble_mode and g.gamble_dev_for_beans,
        "message": g.bj_message,
        "chat": list(g.bj_chat),
        "mood": bj["mood"] if bj else "happy",
        "seats": _bj_seats(g, pid),
        "canBet": (not bj or bj["state"] in ("idle", "done")) and beans >= C.BLACKJACK_MIN_BET,
        "table": None,
    }
    if not bj or (not bj["hands"] and bj["state"] == "idle"):
        return out

    hidden = bj["dealerHidden"]
    dealer = bj["dealer"]
    dealer_cards = ([dealer[0], "back"] + ["back"] * (len(dealer) - 2)) if hidden and dealer else list(dealer)
    active = bj.get("active", 0)
    hands = []
    for i, h in enumerate(bj["hands"]):
        total, soft = casino.hand_value(h["cards"])
        hands.append({
            "cards": list(h["cards"]), "bet": h["bet"], "done": h["done"],
            "result": h["result"], "value": total, "soft": soft,
            "bust": casino.is_bust(h["cards"]), "blackjack": casino.is_blackjack(h["cards"]),
            "active": (bj["state"] == "player" and i == active),
        })
    in_play = bj["state"] == "player"
    cur = bj["hands"][active] if (in_play and active < len(bj["hands"])) else None
    out["table"] = {
        "state": bj["state"],
        "dealer": dealer_cards,
        "dealerValue": None if hidden else casino.best(dealer),
        "dealerHidden": hidden,
        "hands": hands,
        "active": active,
        "net": bj.get("net", 0),
        "canHit": in_play,
        "canStand": in_play,
        "canDouble": bool(cur and len(cur["cards"]) == 2 and beans >= cur["bet"]),
        "canSplit": bool(cur and casino.can_split(cur["cards"]) and len(bj["hands"]) < 4 and beans >= cur["bet"]),
        "canSurrender": bool(cur and len(cur["cards"]) == 2 and len(bj["hands"]) == 1),
    }
    return out


def _public_seen(g):
    """The shared seen-list for card counting, MINUS any dealer hole card that
    is still face-down — otherwise a hidden hole card would leak through the
    counting aid (everyone could deduce it)."""
    seen = list(g.bj_seen)
    for opid in g.order:
        bj = g.players[opid].get("bj")
        if bj and bj.get("dealerHidden") and len(bj.get("dealer", [])) >= 2:
            hole = bj["dealer"][1]
            if hole in seen:
                seen.remove(hole)  # drop one instance (count is rank-based)
    return seen


def _bj_seats(g, viewer):
    """Every player currently sitting at the shared table (hands are face-up)."""
    seats = []
    for opid in g.order:
        bj = g.players[opid].get("bj")
        if not bj or bj["state"] == "idle" or not bj["hands"]:
            continue
        seats.append({
            "id": opid, "name": g._name(opid), "color": g.players[opid]["color"],
            "you": opid == viewer, "state": bj["state"],
            "bet": sum(h["bet"] for h in bj["hands"]),
            "net": bj["net"] if bj["state"] == "done" else None,
            "hands": [{"cards": list(h["cards"]), "value": casino.best(h["cards"]),
                       "result": h["result"], "bust": casino.is_bust(h["cards"])}
                      for h in bj["hands"]],
        })
    return seats


def _trade_view(g):
    if not g.trade:
        return None
    return {
        "from": g.trade["from"],
        "give": dict(g.trade["give"]),
        "receive": dict(g.trade["receive"]),
        "to": g.trade["to"],
    }


def _owned_ports(g, pid):
    owned = set(g._settlements(pid)) | set(g._cities(pid))
    out = []
    for port in g.geo["ports"]:
        if owned & set(port["vertices"]):
            out.append(port["type"])
    return out


def _board_view(g):
    geo = g.geo
    hexes = []
    for h in geo["hexes"]:
        hx = g.hexes[h["id"]]
        hexes.append({
            "id": h["id"], "q": h["q"], "r": h["r"],
            "cx": h["cx"], "cy": h["cy"],
            "terrain": hx["terrain"], "resource": hx["resource"],
            "number": hx["number"], "hasRobber": h["id"] == g.robber_hex,
        })
    port_by_vertex = {}
    for port in geo["ports"]:
        for v in port["vertices"]:
            port_by_vertex[v] = port["type"]
    vertices = []
    for v in geo["vertices"]:
        b = g.buildings.get(v["id"])
        vertices.append({
            "id": v["id"], "x": v["x"], "y": v["y"],
            "building": {"type": b["type"], "owner": b["owner"]} if b else None,
            "port": port_by_vertex.get(v["id"]),
        })
    edges = []
    for e in geo["edges"]:
        edges.append({
            "id": e["id"], "v1": e["v1"], "v2": e["v2"],
            "road": g.roads.get(e["id"]),
        })
    return {
        "hexes": hexes,
        "vertices": vertices,
        "edges": edges,
        "ports": [{"type": p["type"], "vertices": p["vertices"], "x": p["x"], "y": p["y"]}
                  for p in geo["ports"]],
        "bounds": geo["bounds"],
    }


def legal_actions(game, viewer):
    g = game
    out = {
        "yourTurn": viewer == g.current_pid,
        "canRoll": False,
        "canEndTurn": False,
        "canBuyDev": False,
        "settlementSpots": [],
        "citySpots": [],
        "roadSpots": [],
        "setupSettlementSpots": [],
        "setupRoadSpots": [],
        "playableDev": [],
        "bankTrades": {},
        "portRatios": {},
        "mustDiscard": g.pending_discards.get(viewer, 0),
        "robberMove": False,
        "stealTargetsByHex": {},
        "canTrade": False,
        "tradeRespond": False,
    }
    if g.phase == "ended":
        return out

    # A trade can be accepted by its intended audience regardless of turn.
    if g.trade and viewer != g.trade["from"]:
        if g.trade["to"] is None or g.trade["to"] == viewer:
            out["tradeRespond"] = g._has(viewer, g.trade["receive"])

    # Discarding can be required of any player, on or off turn.
    if g.robber_phase == "discard" and out["mustDiscard"]:
        return out

    if viewer != g.current_pid:
        return out

    # ---- it's the viewer's turn ----
    if g.phase == "setup":
        if g.setup_sub == "settlement":
            out["setupSettlementSpots"] = g.legal_settlement_spots(viewer, setup=True)
        else:
            out["setupRoadSpots"] = g.legal_road_spots(viewer, setup=True)
        return out

    if g.robber_phase == "move":
        out["robberMove"] = True
        for h in g.geo["hexes"]:
            hid = h["id"]
            if hid == g.robber_hex:
                continue
            targets = g._steal_targets(viewer, hid)
            out["stealTargetsByHex"][hid] = targets
        return out

    if g.robber_phase == "discard":
        return out  # waiting on others to discard

    # main phase, robber resolved
    p = g.players[viewer]

    # Dev cards may be played before or after the roll (one per turn).
    if not g.dev_played_this_turn:
        for card in (C.DEV_KNIGHT, C.DEV_ROAD_BUILDING, C.DEV_YEAR_OF_PLENTY, C.DEV_MONOPOLY):
            if p["dev"].get(card, 0) > 0:
                out["playableDev"].append(card)

    if not g.dice_rolled:
        out["canRoll"] = True
        # Before rolling you may only play a dev card or roll.
        if g.free_roads > 0:
            out["roadSpots"] = g.legal_road_spots(viewer)
        return out

    # after rolling: building, trading, buying, ending
    out["canEndTurn"] = True
    out["canTrade"] = True

    if g.free_roads > 0:
        out["roadSpots"] = g.legal_road_spots(viewer)
    elif g._has(viewer, C.COST_ROAD) and len(g._roads_of(viewer)) < g.max_roads:
        out["roadSpots"] = g.legal_road_spots(viewer)

    if g._has(viewer, C.COST_SETTLEMENT) and len(g._settlements(viewer)) < g.max_settlements:
        out["settlementSpots"] = g.legal_settlement_spots(viewer)

    if g._has(viewer, C.COST_CITY) and len(g._cities(viewer)) < g.max_cities:
        out["citySpots"] = g._settlements(viewer)

    if g._has(viewer, C.COST_DEV_CARD) and len(g.deck) > 0:
        out["canBuyDev"] = True

    ratios = g._port_ratios(viewer)
    out["portRatios"] = dict(ratios)  # best ratio per resource (for multi-unit trades)
    for r in C.RESOURCES:
        if p["resources"][r] >= ratios[r]:
            out["bankTrades"][r] = ratios[r]

    return out
