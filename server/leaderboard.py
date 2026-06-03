"""All-time wins leaderboard, persisted to a small JSON file.

Wins are keyed by the player's display name (case-insensitively; there are no
accounts in this game). Reads/writes are serialized with a lock and written
atomically so a crash mid-write can't corrupt the file.
"""

import json
import os
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "data")
_PATH = os.path.join(_DIR, "leaderboard.json")
_lock = threading.Lock()


def _load():
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    try:
        os.makedirs(_DIR, exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _PATH)
    except OSError:
        pass  # leaderboard is best-effort; never break the game over it


def record_win(name):
    """Credit one win to ``name`` (its latest spelling is kept for display)."""
    name = (name or "").strip()[:24]
    if not name:
        return
    key = name.lower()
    with _lock:
        data = _load()
        entry = data.get(key) or {"name": name, "wins": 0}
        entry["name"] = name
        entry["wins"] = int(entry.get("wins", 0)) + 1
        data[key] = entry
        _save(data)


def top(n=20):
    """Return the highest-win players: ``[{"name", "wins"}, ...]``."""
    with _lock:
        data = _load()
    rows = sorted(data.values(), key=lambda e: (-int(e.get("wins", 0)), e.get("name", "")))
    return [{"name": e.get("name", "?"), "wins": int(e.get("wins", 0))} for e in rows[:n]]
