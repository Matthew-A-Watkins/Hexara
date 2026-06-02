"""Hit a PUBLIC base URL (e.g. a trycloudflare tunnel) and verify the game is
reachable over the internet AND that long-poll realtime works (no edge
buffering). Usage:
    py -3 tests/_net_check.py https://<something>.trycloudflare.com
"""
import json
import sys
import threading
import time
import urllib.request

base = sys.argv[1].rstrip("/")


def jget(url, timeout=35):
    return json.load(urllib.request.urlopen(url, timeout=timeout))


def jpost(path, obj, timeout=20):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


print("health:", jget(base + "/api/health", 20))
j = jpost("/api/join", {"name": "PyNet"})
print("join:", "room", j["room"], "player", j["playerId"][:8] + "…")


def purl(since):
    return "%s/api/poll?room=%s&player=%s&token=%s&since=%d" % (
        base, j["room"], j["playerId"], j["token"], since)


# 1) first poll must return the lobby payload quickly (proves NO edge buffering)
t0 = time.time()
d1 = jget(purl(0))
dt1 = time.time() - t0
ptype = (d1.get("payload") or {}).get("type")
print("first poll: %.2fs  version=%s  payloadType=%s" % (dt1, d1.get("version"), ptype))

# 2) push test: schedule an add-bot; a blocking poll should wake promptly
ver = d1.get("version", 1)


def add_bot_later():
    time.sleep(1.2)
    try:
        jpost("/api/action", {"room": j["room"], "player": j["playerId"],
                              "token": j["token"], "action": {"type": "lobby_add_bot"}})
    except Exception as e:  # noqa: BLE001
        print("add_bot error:", e)


threading.Thread(target=add_bot_later, daemon=True).start()
t0 = time.time()
d2 = jget(purl(ver))
dt2 = time.time() - t0
print("push poll: %.2fs  version=%s  (woke on the add-bot broadcast)" % (dt2, d2.get("version")))

ok = (ptype == "lobby" and dt1 < 5 and d2.get("version", ver) > ver and dt2 < 12)
print("RESULT: reachable + realtime long-poll through tunnel =", ok)
sys.exit(0 if ok else 2)
