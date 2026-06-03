"""HTTP + SSE front door.

Routes:
  GET  /                      -> client/index.html
  GET  /<path>                -> static files under client/
  GET  /api/health            -> {"ok": true}
  GET  /api/events?room&player&token -> Server-Sent-Events state stream
  POST /api/join   {room?, name}              -> {room, playerId, token}
  POST /api/action {room, player, token, action} -> {ok} | {error}

Run:  py -3 -m server.app           (serves on http://localhost:8000)
      py -3 -m server.app 9000      (custom port)
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.game import GameError  # noqa: E402
from server import manager  # noqa: E402
from server import leaderboard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_DIR = os.path.join(ROOT, "client")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HexaraServer/1.0"

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; errors are surfaced in responses

    # --------------------------------------------------------------- helpers
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 256 * 1024:  # reject oversized bodies (basic DoS guard)
            raise ValueError("request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    # ------------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            return self._send_json({"ok": True})
        if path == "/api/config":
            return self._send_json({"requirePassword": manager.requires_password()})
        if path == "/api/leaderboard":
            return self._send_json({"leaders": leaderboard.top(20)})
        if path == "/api/poll":
            return self._handle_poll(parse_qs(parsed.query))
        return self._serve_static(path)

    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(CLIENT_DIR, rel))
        if not full.startswith(CLIENT_DIR):
            return self._send_json({"error": "not found"}, 404)
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            return self._send_json({"error": "not found", "path": path}, 404)
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            return self._send_json({"error": "cannot read"}, 500)
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _handle_poll(self, qs):
        """Long-poll for the next state. Returns a normal finite JSON body
        ({version, payload}) so it works through any proxy/CDN/tunnel."""
        code = (qs.get("room") or [""])[0]
        pid = (qs.get("player") or [""])[0]
        token = (qs.get("token") or [""])[0]
        try:
            since = int((qs.get("since") or ["0"])[0])
        except ValueError:
            since = 0
        room = manager.get_room(code)
        if room is None or not manager.authed(room, pid, token):
            return self._send_json({"error": "Session not found.", "fatal": True}, 404)
        try:
            result = manager.poll(room, pid, since)
        except Exception as e:  # noqa: BLE001  defensive
            return self._send_json({"error": "Server error: %s" % e}, 500)
        return self._send_json(result)

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            return self._send_json({"error": "Malformed request body."}, 400)

        if parsed.path == "/api/join":
            result, err = manager.join(body.get("room", ""), body.get("name", ""),
                                       body.get("password", ""))
            if err:
                return self._send_json({"error": err}, 400)
            return self._send_json(result)

        if parsed.path == "/api/action":
            code = body.get("room", "")
            pid = body.get("player", "")
            token = body.get("token", "")
            action = body.get("action", {})
            room = manager.get_room(code)
            if room is None:
                return self._send_json({"error": "Room not found."}, 404)
            if not manager.authed(room, pid, token):
                return self._send_json({"error": "Not authorised."}, 403)
            try:
                manager.handle_action(room, pid, action)
            except GameError as e:
                return self._send_json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001  defensive
                return self._send_json({"error": "Server error: %s" % e}, 500)
            return self._send_json({"ok": True})

        return self._send_json({"error": "not found"}, 404)


def make_server(host="0.0.0.0", port=8000):
    """Build a ready-to-serve HTTP server (used by app.main and share.py)."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    # Port: first CLI arg, else $HEXARA_PORT, else 8000.
    port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    elif os.environ.get("HEXARA_PORT"):
        try:
            port = int(os.environ["HEXARA_PORT"])
        except ValueError:
            pass
    host = os.environ.get("HEXARA_HOST", "0.0.0.0")
    server = make_server(host, port)
    print("Hexara server running:  http://localhost:%d" % port)
    if manager.requires_password():
        print("Access code required to join (HEXARA_PASSWORD is set).")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
