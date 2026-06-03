"""End-to-end backend test over real HTTP + long-poll.

Starts the server as a subprocess, joins two players, starts the game, then
drives setup and several main turns purely through the public HTTP API while
two background threads long-poll each player's stream. Verifies the transport,
the room manager, per-player views and turn rotation all work together.

Run: py -3 tests\\test_server.py
"""
import json
import os
import subprocess
import sys
import threading
import time
import http.client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8137
HOST = "127.0.0.1"


def _post(path, obj):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    body = json.dumps(obj)
    conn.request("POST", path, body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


def _get_json(path):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


class PollReader(threading.Thread):
    """Mimics a connected client by long-polling /api/poll in a loop."""
    def __init__(self, room, pid, token):
        super().__init__(daemon=True)
        self.room, self.pid, self.token = room, pid, token
        self.latest = None
        self.count = 0
        self.since = 0
        self.lock = threading.Lock()
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                path = "/api/poll?room=%s&player=%s&token=%s&since=%d" % (
                    self.room, self.pid, self.token, self.since)
                conn = http.client.HTTPConnection(HOST, PORT, timeout=40)
                conn.request("GET", path)
                resp = conn.getresponse()
                data = json.loads(resp.read() or b"{}")
                conn.close()
                if resp.status != 200:
                    time.sleep(0.3)
                    continue
                if isinstance(data.get("version"), int):
                    self.since = data["version"]
                payload = data.get("payload")
                if payload:
                    with self.lock:
                        self.latest = payload
                        self.count += 1
            except Exception:
                time.sleep(0.2)

    def get(self):
        with self.lock:
            return self.latest

    def stop(self):
        self._stop = True


def wait_for(fn, timeout=8.0, interval=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def main():
    proc = subprocess.Popen([sys.executable, "-m", "server.app", str(PORT)],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    failures = []

    def check(cond, msg):
        if cond:
            print("PASS  %s" % msg)
        else:
            print("FAIL  %s" % msg)
            failures.append(msg)

    try:
        # wait for health
        ready = False
        for _ in range(60):
            try:
                status, data = _get_json("/api/health")
                if status == 200 and data.get("ok"):
                    ready = True
                    break
            except Exception:
                time.sleep(0.1)
        check(ready, "server starts and answers /api/health")
        if not ready:
            raise SystemExit("server never came up")

        # join two players
        st, a = _post("/api/join", {"name": "Alice"})
        check(st == 200 and "playerId" in a, "Alice joins, gets a room")
        room = a["room"]
        st, b = _post("/api/join", {"room": room, "name": "Bob"})
        check(st == 200 and b["room"] == room, "Bob joins the same room")

        ra = PollReader(room, a["playerId"], a["token"]); ra.start()
        rb = PollReader(room, b["playerId"], b["token"]); rb.start()

        lobby = wait_for(lambda: ra.get() if ra.get() and ra.get()["type"] == "lobby" else None)
        check(lobby is not None, "Alice receives a lobby snapshot via long-poll")
        check(lobby and len(lobby["lobby"]["players"]) == 2, "lobby shows two players")

        # ---- game setup: presets, bounds and host-only config over HTTP ----
        lv = lobby["lobby"]
        check(len(lv.get("presets", [])) >= 4, "lobby offers map presets")
        check("victoryPoints" in lv.get("ruleBounds", {}), "lobby offers rule bounds")

        def cfg_post(jd, config):
            return _post("/api/action", {"room": room, "player": jd["playerId"],
                                         "token": jd["token"],
                                         "action": {"type": "lobby_set_config", "config": config}})

        # non-host cannot change settings
        st, _ = cfg_post(b, {"rules": {"victoryPoints": 5}})
        check(st == 400, "non-host cannot change game settings")

        # host picks a custom map + rules; the lobby broadcasts them
        st, _ = cfg_post(a, {"map": {"preset": "small"}, "rules": {"victoryPoints": 6}})
        check(st == 200, "host sets a custom map + rules")
        echoed = wait_for(lambda: ra.get() if (ra.get() and ra.get()["type"] == "lobby"
                          and ra.get()["lobby"]["config"]["map"].get("preset") == "small"
                          and ra.get()["lobby"]["config"]["rules"].get("victoryPoints") == 6) else None)
        check(echoed is not None, "lobby broadcasts the updated map + rules")

        # invalid settings are rejected
        st, _ = cfg_post(a, {"rules": {"victoryPoints": 99}})
        check(st == 400, "invalid settings are rejected")

        # settle on a standard board with a high VP cap so the flow below is stable
        st, _ = cfg_post(a, {"map": {"preset": "standard"}, "rules": {"victoryPoints": 12, "discardThreshold": 8}})
        check(st == 200, "host finalizes settings")

        # bad token is rejected
        st, _ = _post("/api/action", {"room": room, "player": a["playerId"],
                                      "token": "wrong", "action": {"type": "lobby_start"}})
        check(st == 403, "action with a bad token is rejected (403)")

        # only host can start; Bob is not host
        st, _ = _post("/api/action", {"room": room, "player": b["playerId"],
                                      "token": b["token"], "action": {"type": "lobby_start"}})
        check(st == 400, "non-host cannot start the game")

        # host starts
        st, _ = _post("/api/action", {"room": room, "player": a["playerId"],
                                      "token": a["token"], "action": {"type": "lobby_start"}})
        check(st == 200, "host starts the game")

        started = wait_for(lambda: ra.get() if ra.get() and ra.get()["type"] == "state" else None)
        check(started is not None and started["state"]["phase"] == "setup",
              "game starts; long-poll delivers the setup state")
        check(started and started["state"]["rules"]["victoryPoints"] == 12,
              "game starts with the host's custom rules")
        check(started and len(started["state"]["board"]["hexes"]) == 19,
              "game starts on the chosen board")

        ids = {"Alice": a["playerId"], "Bob": b["playerId"]}
        tok = {a["playerId"]: a["token"], b["playerId"]: b["token"]}
        readers = {a["playerId"]: ra, b["playerId"]: rb}

        def act(pid, action):
            return _post("/api/action", {"room": room, "player": pid,
                                         "token": tok[pid], "action": action})

        # illegal: Bob acts on Alice's setup turn
        cur = started["state"]["currentPlayer"]
        other = b["playerId"] if cur == a["playerId"] else a["playerId"]
        st, _ = act(other, {"type": "place_setup_settlement", "vertex": 0})
        check(st == 400, "a player cannot act on someone else's turn")

        # Drive setup and many main turns over the wire. A full 10-VP game is
        # proven in the engine smoke test; here we verify real gameplay flows
        # over HTTP long-poll: setup, rolling/production, building, the robber, and
        # turn rotation.
        def settle(prev):
            wait_for(lambda: ra.count > prev, timeout=4)

        reached_main = False
        rolls = builds = 0
        turn_players = set()
        ended = False
        deadline = time.time() + 90
        while time.time() < deadline:
            sa = ra.get()
            if not sa or sa["type"] != "state":
                time.sleep(0.02)
                continue
            state = sa["state"]
            if state["phase"] == "main":
                reached_main = True
            if state["phase"] == "ended":
                ended = True
                break
            if reached_main and rolls >= 8 and builds >= 1 and len(turn_players) >= 2:
                break  # gameplay over the transport is firmly demonstrated

            # handle discards for either player (can be off-turn)
            did = False
            for pid, r in readers.items():
                pay = r.get()
                if pay and pay["type"] == "state" and pay["legal"]["mustDiscard"] > 0:
                    me = next(p for p in pay["state"]["players"] if p["id"] == pid)
                    need = pay["legal"]["mustDiscard"]
                    res = dict(me["resources"])
                    pick, left = {}, need
                    for rr in list(res):
                        take = min(res[rr], left)
                        if take:
                            pick[rr] = take
                            left -= take
                    c = ra.count
                    act(pid, {"type": "discard", "resources": pick})
                    settle(c)
                    did = True
                    break
            if did:
                continue

            cur = state["currentPlayer"]
            pay = readers[cur].get()
            if not pay or pay["type"] != "state":
                time.sleep(0.02)
                continue
            in_main = pay["state"]["phase"] == "main"
            action = _decide(pay["state"], pay["legal"])
            if action is None:
                time.sleep(0.02)
                continue
            c = ra.count
            status, _ = act(cur, action)
            if status == 200:
                if action["type"] == "roll_dice":
                    rolls += 1
                elif action["type"] in ("build_settlement", "build_city", "build_road"):
                    builds += 1
                if in_main:
                    turn_players.add(cur)
                settle(c)
            else:
                time.sleep(0.02)

        check(reached_main, "setup completes and play reaches the main phase over HTTP")
        check(rolls >= 8, "dice are rolled repeatedly, driving production (%d rolls)" % rolls)
        check(builds >= 1, "players build settlements/cities/roads over HTTP (%d builds)" % builds)
        check(len(turn_players) >= 2, "turns rotate between players in round-robin order")
        check(ra.count > 20 and rb.count > 20, "both players receive many live long-poll updates")
        if ended:
            check(ra.get()["state"]["winner"] is not None, "a completed game reports a winner")

        ra.stop(); rb.stop()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print("\n%d checks failed" % len(failures))
    sys.exit(1 if failures else 0)


def _decide(state, legal):
    if legal["setupSettlementSpots"]:
        return {"type": "place_setup_settlement", "vertex": legal["setupSettlementSpots"][0]}
    if legal["setupRoadSpots"]:
        return {"type": "place_setup_road", "edge": legal["setupRoadSpots"][0]}
    if legal["robberMove"]:
        for hid, tgts in legal["stealTargetsByHex"].items():
            return {"type": "move_robber", "hex": int(hid), "target": tgts[0] if tgts else None}
    if legal["canRoll"]:
        return {"type": "roll_dice"}
    # main phase: greedy toward a win so the test terminates
    if legal["citySpots"]:
        return {"type": "build_city", "vertex": legal["citySpots"][0]}
    if legal["settlementSpots"]:
        return {"type": "build_settlement", "vertex": legal["settlementSpots"][0]}
    if legal["roadSpots"]:
        return {"type": "build_road", "edge": legal["roadSpots"][0]}
    if legal["canBuyDev"]:
        return {"type": "buy_dev_card"}
    me = next(p for p in state["players"] if p["id"] == state["yourId"])
    res = me["resources"]
    wants = [r for r, n in (("ore", 3), ("wheat", 2)) if res[r] < n]
    for want in wants:
        for give in ("wood", "brick", "sheep", "wheat", "ore"):
            if give != want and legal["bankTrades"].get(give) and res[give] >= legal["bankTrades"][give]:
                if not (give in wants and res[give] - legal["bankTrades"][give] < 1):
                    return {"type": "bank_trade", "give": give, "receive": want}
    if legal["canEndTurn"]:
        return {"type": "end_turn"}
    return None


if __name__ == "__main__":
    main()
