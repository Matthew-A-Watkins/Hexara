"""Rooms, lobby, and the bot driver.

Holds all live matches in memory. A room is a lobby that becomes a game when
the host starts it. Every connected client has a Subscriber with a queue; when
anything changes we push a fresh, per-player payload to each queue and the HTTP
layer writes it to that client's SSE stream.
"""

import hmac
import json
import os
import secrets
import threading
import time
import urllib.request

from engine import bot, views, constants as C, maps, dealer_chat
from engine.game import Game, GameError, validate_config, normalize_rules, rule_bounds
from server import leaderboard

PALETTE = [{"name": name, "hex": hexv} for name, hexv in C.PLAYER_COLORS]
_BOT_NAMES = ["Robo-Rurik", "Auto-Astrid", "Bot Bjorn", "C.P.-Una",
              "Mecha-Magnus", "Silicon-Sven"]

# --- safety limits for internet exposure ---
ACCESS_PASSWORD = (os.environ.get("HEXARA_PASSWORD") or "").strip()
MAX_ROOMS = 300                  # cap live rooms to bound memory
ROOM_IDLE_SECONDS = 1800         # reap rooms with no connected humans after 30 min


class _ChatLLM:
    """Optional external chat model for the dealer. Entirely opt-in: with no
    HEXARA_CHAT_URL set it stays disabled and the instant, rule-based reply from
    engine.dealer_chat is the only thing players ever see. When configured, it
    calls an OpenAI-compatible /chat/completions endpoint over the stdlib (no pip
    deps) on a daemon thread and *upgrades* the already-sent reply in place."""

    def __init__(self):
        self.url = (os.environ.get("HEXARA_CHAT_URL") or "").strip()
        self.key = (os.environ.get("HEXARA_CHAT_KEY") or "").strip()
        self.model = (os.environ.get("HEXARA_CHAT_MODEL") or "gpt-4o-mini").strip()
        try:
            self.timeout = max(1.0, float(os.environ.get("HEXARA_CHAT_TIMEOUT") or "8"))
        except ValueError:
            self.timeout = 8.0
        self.enabled = bool(self.url)

    def complete(self, system, history):
        """Return the model's reply text, or None on any failure (so the caller
        simply keeps the rule-based line). Never raises."""
        if not self.enabled:
            return None
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(history),
            "max_tokens": 80,
            "temperature": 0.9,
        }).encode("utf-8")
        req = urllib.request.Request(self.url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("Authorization", "Bearer " + self.key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
            return text[:240] or None
        except Exception:
            return None


_CHAT_LLM = _ChatLLM()

_rooms = {}
_rooms_lock = threading.Lock()
_reaper_started = False


def requires_password():
    return bool(ACCESS_PASSWORD)


def _fixed_seed():
    """A fixed RNG seed for new games when HEXARA_SEED is set (deterministic
    tests); otherwise None for a fresh random board each game."""
    raw = os.environ.get("HEXARA_SEED")
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _check_password(supplied):
    if not ACCESS_PASSWORD:
        return True
    return hmac.compare_digest((supplied or "").strip(), ACCESS_PASSWORD)


class Room:
    def __init__(self, code):
        self.code = code
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)  # wakes long-polls on change
        self.version = 1            # >0 so a first poll (since=0) returns at once
        self.players = []           # {id,name,color,token,is_bot,last_seen}
        self.host = None
        self.game = None
        self.config = {"rules": {}, "map": {}}   # host-chosen rules + map spec
        self.bot_event = threading.Event()
        self.bot_thread = None
        self.closed = False
        self.touched = time.monotonic()
        self.win_recorded = False   # leaderboard credited once per game


# ------------------------------------------------------------------- rooms
def _new_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no easily-confused chars
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        if code not in _rooms:
            return code


def get_room(code):
    with _rooms_lock:
        return _rooms.get(code)


def create_room():
    with _rooms_lock:
        _ensure_reaper()
        room = Room(_new_code())
        _rooms[room.code] = room
        return room


# ----------------------------------------------------- room reaping (memory)
def _ensure_reaper():
    global _reaper_started
    if not _reaper_started:
        _reaper_started = True
        threading.Thread(target=_reaper_loop, daemon=True).start()


def _prune_locked():
    """Drop dead/abandoned rooms. Caller must hold _rooms_lock."""
    now = time.monotonic()
    for code, room in list(_rooms.items()):
        with room.lock:
            age = now - room.touched
            no_humans = not any((not p["is_bot"]) and (now - p.get("last_seen", 0)) < 30
                                for p in room.players)
            empty = not room.players
            if (empty and age > 60) or (no_humans and age > ROOM_IDLE_SECONDS):
                room.closed = True
                room.bot_event.set()  # let the bot thread exit
                _rooms.pop(code, None)


def _reaper_loop():
    while True:
        time.sleep(60)
        with _rooms_lock:
            _prune_locked()


def _free_color(room):
    used = {p["color"] for p in room.players}
    for c in PALETTE:
        if c["hex"] not in used:
            return c["hex"]
    return "#888888"


def join(code, name, password=""):
    """Join an existing room, or create one if code is falsy. Pre-game only."""
    if not _check_password(password):
        return None, "Wrong access code."
    if code:
        room = get_room(code)
        if room is None:
            return None, "Room %s not found." % code
    else:
        with _rooms_lock:
            _prune_locked()
            if len(_rooms) >= MAX_ROOMS:
                return None, "The server is at capacity. Please try again shortly."
        room = create_room()
    with room.lock:
        if room.game is not None:
            return None, "That game has already started."
        if len(room.players) >= C.MAX_PLAYERS:
            return None, "That room is full."
        room.touched = time.monotonic()
        pid = "p_" + secrets.token_hex(6)
        token = secrets.token_hex(16)
        player = {
            "id": pid,
            "name": (name or "").strip()[:16] or ("Player %d" % (len(room.players) + 1)),
            "color": _free_color(room),
            "token": token,
            "is_bot": False,
            "last_seen": time.monotonic(),
        }
        room.players.append(player)
        if room.host is None:
            room.host = pid
        broadcast(room)
        return {"room": room.code, "playerId": pid, "token": token}, None


def _find(room, pid):
    for p in room.players:
        if p["id"] == pid:
            return p
    return None


def authed(room, pid, token):
    p = _find(room, pid)
    return p is not None and not p["is_bot"] and p["token"] == token


# --------------------------------------------------------------- lobby ops
def handle_lobby(room, pid, action):
    t = action.get("type")
    with room.lock:
        room.touched = time.monotonic()
        if room.game is not None:
            raise GameError("The game has already started.")
        me = _find(room, pid)
        if me is None:
            raise GameError("You are not in this room.")
        if t == "lobby_set_name":
            me["name"] = (action.get("name") or "").strip()[:16] or me["name"]
        elif t == "lobby_set_color":
            hexv = action.get("color")
            if hexv not in {c["hex"] for c in PALETTE}:
                raise GameError("Unknown colour.")
            if any(p["color"] == hexv and p["id"] != pid for p in room.players):
                raise GameError("That colour is taken.")
            me["color"] = hexv
        elif t == "lobby_add_bot":
            if pid != room.host:
                raise GameError("Only the host can add bots.")
            if len(room.players) >= C.MAX_PLAYERS:
                raise GameError("The room is full.")
            used = {p["name"] for p in room.players}
            name = next((n for n in _BOT_NAMES if n not in used), "Bot")
            bot_player = {
                "id": "bot_" + secrets.token_hex(5),
                "name": name, "color": _free_color(room),
                "token": "", "is_bot": True, "last_seen": time.monotonic(),
            }
            room.players.append(bot_player)
        elif t == "lobby_set_config":
            if pid != room.host:
                raise GameError("Only the host can change game settings.")
            incoming = action.get("config") or {}
            # Validate up front so the host gets immediate feedback. Keep the
            # raw map (so the UI can still tell which preset/size was chosen) but
            # store fully-normalized rules; Game() re-validates both at start.
            validate_config(incoming)
            room.config = {
                "rules": normalize_rules(incoming.get("rules") or {}),
                "map": incoming.get("map") or {},
            }
        elif t == "lobby_remove":
            if pid != room.host:
                raise GameError("Only the host can remove players.")
            target = action.get("target")
            room.players = [p for p in room.players if p["id"] != target or p["id"] == room.host]
        elif t == "lobby_leave":
            room.players = [p for p in room.players if p["id"] != pid]
            if room.host == pid:
                room.host = room.players[0]["id"] if room.players else None
        elif t == "lobby_start":
            if pid != room.host:
                raise GameError("Only the host can start the game.")
            humans = [p for p in room.players if not p["is_bot"]]
            if len(room.players) < C.MIN_PLAYERS:
                raise GameError("Need at least %d players." % C.MIN_PLAYERS)
            if not humans:
                raise GameError("Need at least one human player.")
            plist = [{"id": p["id"], "name": p["name"], "color": p["color"]}
                     for p in room.players]
            room.game = Game(plist, config=room.config, seed=_fixed_seed())
            _start_bot_thread(room)
        else:
            raise GameError("Unknown lobby action: %r" % t)
        broadcast(room)
    room.bot_event.set()


# ----------------------------------------------------------------- game ops
def handle_action(room, pid, action):
    t = action.get("type", "")
    # Leaving (or surrendering) a game in progress hands the seat to a bot so
    # play continues for everyone else.
    if t in ("lobby_leave", "surrender") and room.game is not None:
        _surrender(room, pid)
        return
    if t.startswith("lobby_"):
        handle_lobby(room, pid, action)
        return
    llm_job = None
    with room.lock:
        room.touched = time.monotonic()
        if room.game is None:
            raise GameError("The game hasn't started yet.")
        result = room.game.apply(pid, action)
        _maybe_record_win(room)
        # If an external chat model is configured, prepare to upgrade the dealer's
        # instant reply. We build the prompt here (under the lock, reading a
        # consistent snapshot) but make the network call off-lock so the room
        # never blocks on it.
        if t == "bj_chat" and _CHAT_LLM.enabled and isinstance(result, dict):
            msg = result.get("message") or {}
            mid = msg.get("id")
            if mid is not None:
                dealer = msg.get("dealer") or "m"
                llm_job = {
                    "msg_id": mid,
                    "system": dealer_chat.system_prompt(room.game, pid, dealer,
                                                        result.get("grant", 0)),
                    "history": dealer_chat.history_for(room.game, exclude_id=mid),
                }
        broadcast(room)
    room.bot_event.set()
    if llm_job is not None:
        threading.Thread(target=_llm_reply_worker, args=(room, llm_job),
                         daemon=True).start()


def _llm_reply_worker(room, job):
    """Off-lock: call the external model, then (if it answered and the message is
    still on the board) rewrite that dealer line in place and rebroadcast. Any
    failure is silently ignored — the instant rule-based reply already stands."""
    text = _CHAT_LLM.complete(job["system"], job["history"])
    if not text:
        return
    with room.lock:
        g = room.game
        changed = bool(g and g.set_chat_text(job["msg_id"], text))
    if changed:
        broadcast(room)


def _maybe_record_win(room):
    """Credit the winner's all-time wins exactly once when a game ends."""
    g = room.game
    if g is None or room.win_recorded or g.winner is None:
        return
    room.win_recorded = True
    winner = _find(room, g.winner)
    if winner:
        leaderboard.record_win(winner["name"])


def _surrender(room, pid):
    """A human leaves a game in progress: their seat becomes a bot (keeping all
    pieces, resources and turn position) and their session is invalidated."""
    with room.lock:
        room.touched = time.monotonic()
        p = _find(room, pid)
        if p is None or p["is_bot"]:
            return
        base = p["name"]
        p["is_bot"] = True
        p["token"] = secrets.token_hex(16)  # kill their session so they bounce to join
        p["name"] = (base + " (bot)")[:20]
        if room.game and pid in room.game.players:
            room.game.players[pid]["name"] = p["name"]
            room.game._log("%s left the game — a bot took over their seat." % base)
        if room.host == pid:  # hand the (now-cosmetic) host flag to a remaining human
            human = next((q for q in room.players if not q["is_bot"]), None)
            room.host = human["id"] if human else room.host
        broadcast(room)
    room.bot_event.set()  # let the bot driver pick up the seat immediately


# ---------------------------------------------------------------- bot driver
def _start_bot_thread(room):
    if room.bot_thread is None:
        room.bot_thread = threading.Thread(target=_bot_loop, args=(room,), daemon=True)
        room.bot_thread.start()


def _next_bot_actor(room, g):
    bots = {p["id"] for p in room.players if p["is_bot"]}
    if g.robber_phase == "discard":
        for pid in g.pending_discards:
            if pid in bots:
                return pid
        return None
    if g.robber_phase == "move":
        return g.current_pid if g.current_pid in bots else None
    if g.phase in ("setup", "main"):
        return g.current_pid if g.current_pid in bots else None
    return None


def _bot_loop(room):
    while True:
        room.bot_event.wait()
        room.bot_event.clear()
        if room.closed:
            return
        while True:
            time.sleep(0.7)  # pacing so humans can follow the action
            with room.lock:
                g = room.game
                if room.closed or g is None or g.phase == "ended":
                    break
                actor = _next_bot_actor(room, g)
                if actor is None:
                    break
                action = bot.next_action(g, actor)
                if action is None:
                    break
                try:
                    g.apply(actor, action)
                except GameError:
                    break  # defensive: never spin on a rejected bot move
                _maybe_record_win(room)
            broadcast(room)


# --------------------------------------------------------- realtime (long-poll)
def broadcast(room):
    """Signal that room state changed; wakes any waiting long-polls."""
    with room.lock:
        room.version += 1
        room.cond.notify_all()


POLL_TIMEOUT = 25.0  # seconds a long-poll waits before returning a heartbeat


def poll(room, pid, since):
    """Long-poll: return this player's latest payload as soon as the room is
    newer than `since`, or after POLL_TIMEOUT (a heartbeat). Always returns the
    current version so the client can request the next change. Each response is
    a complete, finite HTTP body, so it streams cleanly through any proxy/CDN."""
    with room.cond:
        now = time.monotonic()
        room.touched = now
        p = _find(room, pid)
        if p:
            p["last_seen"] = now
        if room.version <= since:
            room.cond.wait(POLL_TIMEOUT)
            now = time.monotonic()
            room.touched = now
            p = _find(room, pid)
            if p:
                p["last_seen"] = now
        return {"version": room.version, "payload": payload(room, pid)}


def payload(room, pid):
    with room.lock:
        if room.game is None:
            return {"type": "lobby", "youId": pid, "lobby": _lobby_view(room)}
        g = room.game
        return {"type": "state",
                "state": views.serialize(g, pid),
                "legal": views.legal_actions(g, pid)}


def _is_connected(p):
    if p["is_bot"]:
        return True
    return (time.monotonic() - p.get("last_seen", 0)) < 25


def _lobby_view(room):
    return {
        "code": room.code,
        "host": room.host,
        "minPlayers": C.MIN_PLAYERS,
        "maxPlayers": C.MAX_PLAYERS,
        "palette": PALETTE,
        "config": room.config,
        "presets": maps.list_presets(),
        "ruleBounds": rule_bounds(),
        "players": [
            {"id": p["id"], "name": p["name"], "color": p["color"],
             "isBot": p["is_bot"], "connected": _is_connected(p),
             "isHost": p["id"] == room.host}
            for p in room.players
        ],
    }
