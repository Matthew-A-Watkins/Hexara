"""Diagnostic: drive full games with a goal-directed bot across many seeds and
report turns-to-finish (or where players plateau). Not part of the suite."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import constants as C
from engine.game import Game

PLAYERS = [
    {"id": "A", "name": "Alice", "color": "red"},
    {"id": "B", "name": "Bob", "color": "blue"},
    {"id": "C", "name": "Cara", "color": "orange"},
]


def auto_setup(g):
    while g.phase == "setup":
        pid = g.current_pid
        if g.setup_sub == "settlement":
            g.apply(pid, {"type": "place_setup_settlement",
                          "vertex": g.legal_settlement_spots(pid, setup=True)[0]})
        else:
            g.apply(pid, {"type": "place_setup_road",
                          "edge": g.legal_road_spots(pid, setup=True)[0]})


def choose(g, pid):
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
    # trade toward what a city needs, then settlement needs
    wants = []
    for r, need in (("ore", 3), ("wheat", 2)):
        if res[r] < need:
            wants.append(r)
    for r in ("wood", "brick", "sheep", "wheat"):
        if res[r] < 1 and r not in wants:
            wants.append(r)
    ratios = g._port_ratios(pid)
    for want in wants:
        for give in C.RESOURCES:
            if give == want:
                continue
            if res[give] >= ratios[give] and g.bank[want] > 0:
                # keep enough of `give` if it's also wanted
                if give in wants and res[give] - ratios[give] < 1:
                    continue
                return {"type": "bank_trade", "give": give, "receive": want}
    return {"type": "end_turn"}


def play(seed, cap=30000):
    g = Game(PLAYERS, seed=seed)
    auto_setup(g)
    steps = 0
    while g.phase != "ended" and steps < cap:
        steps += 1
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
                g.apply(dp, {"type": "discard", "resources": pick})
            continue
        if g.robber_phase == "move":
            dest = next(h for h in g.hexes if h != g.robber_hex)
            tg = g._steal_targets(pid, dest)
            g.apply(pid, {"type": "move_robber", "hex": dest, "target": tg[0] if tg else None})
            continue
        if not g.dice_rolled:
            g.apply(pid, {"type": "roll_dice"})
            continue
        g.apply(pid, choose(g, pid))
    standings = {p: g.total_vp(p) for p in g.order}
    return g.phase == "ended", steps, g.winner, standings


if __name__ == "__main__":
    for seed in range(10):
        ended, steps, winner, standings = play(seed)
        print("seed %2d: ended=%s steps=%5d winner=%s vp=%s" %
              (seed, ended, steps, winner, standings))
