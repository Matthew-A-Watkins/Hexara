/* ============================================================
   net.js  ·  window.Net
   Session management (localStorage), HTTP join/action, SSE stream.
   The server is authoritative: we send action requests and render
   whatever state arrives over SSE. We never read state from the
   action POST response.
   ============================================================ */
(function () {
  "use strict";

  var LS_KEY = "hexara.session";

  var Net = {
    session: null, // { room, playerId, token }
    _polling: false, // long-poll loop active?
    _opened: false, // have we received the first poll response?
    _since: 0, // last room version we've seen
    _handlers: {
      onLobby: null, // (msg) => void   msg.lobby, msg.youId
      onState: null, // (msg) => void   msg.state, msg.legal
      onError: null, // (message) => void  (session invalid)
      onActionError: null, // (message) => void
      onOpen: null, // () => void
      onDrop: null, // () => void  (transport dropped; reconnecting)
    },

    /* ---- handler registration ---- */
    on: function (name, fn) {
      if (name in this._handlers) this._handlers[name] = fn;
      return this;
    },

    /* ---- session persistence ---- */
    loadSession: function () {
      try {
        var raw = localStorage.getItem(LS_KEY);
        this.session = raw ? JSON.parse(raw) : null;
      } catch (e) {
        this.session = null;
      }
      return this.session;
    },
    _saveSession: function () {
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(this.session));
      } catch (e) {
        /* storage may be unavailable; keep in-memory session */
      }
    },
    hasSession: function () {
      return !!(this.session && this.session.room && this.session.playerId && this.session.token);
    },
    clearSession: function () {
      this.session = null;
      try {
        localStorage.removeItem(LS_KEY);
      } catch (e) {}
    },

    /* ---- POST /api/join ----
       Resolves with the session, rejects with an error string. */
    join: function (name, roomCode, password) {
      var self = this;
      var body = { room: (roomCode || "").trim().toUpperCase(), name: (name || "").trim(),
                   password: (password || "") };
      return fetch("/api/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (resp) {
          return resp
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              return { resp: resp, data: data };
            });
        })
        .then(function (r) {
          if (!r.resp.ok || r.data.error) {
            throw new Error(r.data.error || "Could not join (HTTP " + r.resp.status + ")");
          }
          if (!r.data.room || !r.data.playerId || !r.data.token) {
            throw new Error("Malformed join response from server");
          }
          self.session = { room: r.data.room, playerId: r.data.playerId, token: r.data.token };
          self._saveSession();
          return self.session;
        })
        .catch(function (err) {
          // Normalise network failures to a readable message.
          throw new Error(err && err.message ? err.message : "Network error while joining");
        });
    },

    /* ---- realtime via HTTP long-poll (GET /api/poll) ----
       Each poll returns {version, payload}; we hand the payload to _dispatch
       then immediately poll again for the next change. Every response is a
       complete, finite HTTP body, so it streams cleanly through any proxy,
       CDN or tunnel (unlike SSE, which some edges buffer). */
    connect: function () {
      if (!this.hasSession()) return false;
      this.disconnect();
      this._polling = true;
      this._opened = false;
      this._since = 0;
      this._pollLoop();
      return true;
    },

    _pollLoop: function () {
      var self = this;
      if (!this._polling || !this.hasSession()) return;
      var s = this.session;
      var url =
        "/api/poll?room=" + encodeURIComponent(s.room) +
        "&player=" + encodeURIComponent(s.playerId) +
        "&token=" + encodeURIComponent(s.token) +
        "&since=" + (this._since || 0);
      fetch(url, { cache: "no-store" })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; })
            .then(function (data) { return { resp: resp, data: data }; });
        })
        .then(function (r) {
          if (!self._polling) return;
          if (r.resp.status === 404 || r.resp.status === 403 || (r.data && r.data.fatal)) {
            self._dispatch({ type: "error", message: (r.data && r.data.error) || "Session ended." });
            return; // do not keep polling a dead session
          }
          if (!r.resp.ok) { self._retry(); return; } // transient → back off
          if (typeof r.data.version === "number") self._since = r.data.version;
          if (!self._opened) { self._opened = true; if (self._handlers.onOpen) self._handlers.onOpen(); }
          if (r.data.payload) self._dispatch(r.data.payload);
          self._pollLoop(); // wait for the next change immediately
        })
        .catch(function () {
          if (self._handlers.onDrop) self._handlers.onDrop();
          self._retry(); // network hiccup / tunnel blip → retry shortly
        });
    },

    _retry: function () {
      var self = this;
      if (!this._polling) return;
      setTimeout(function () { self._pollLoop(); }, 1500);
    },

    _dispatch: function (msg) {
      if (!msg || typeof msg.type !== "string") return;
      switch (msg.type) {
        case "lobby":
          if (this._handlers.onLobby) this._handlers.onLobby(msg);
          break;
        case "state":
          if (this._handlers.onState) this._handlers.onState(msg);
          break;
        case "error":
          // Session invalid → drop everything and bounce to join screen.
          this.disconnect();
          this.clearSession();
          if (this._handlers.onError)
            this._handlers.onError(msg.message || "Session ended by server");
          break;
        case "ping":
        case "heartbeat":
          break; // ignore
        default:
          break; // unknown frame types are ignored, not fatal
      }
    },

    disconnect: function () {
      this._polling = false;
      this._opened = false;
    },

    /* ---- POST /api/action ----
       Returns a promise resolving to {ok:true} or {error}. Also fires
       onActionError for any error so callers can just await or ignore. */
    sendAction: function (action) {
      var self = this;
      if (!this.hasSession()) {
        var noSess = "No active session";
        if (this._handlers.onActionError) this._handlers.onActionError(noSess);
        return Promise.resolve({ error: noSess });
      }
      var s = this.session;
      var payload = { room: s.room, player: s.playerId, token: s.token, action: action };
      return fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (resp) {
          return resp
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              return { resp: resp, data: data };
            });
        })
        .then(function (r) {
          if (r.resp.status === 403 || r.resp.status === 404) {
            // Auth/room gone — treat like an invalid session.
            self.disconnect();
            self.clearSession();
            var m = r.data.error || "Session expired";
            if (self._handlers.onError) self._handlers.onError(m);
            return { error: m };
          }
          if (!r.resp.ok || r.data.error) {
            var msg = r.data.error || "Illegal move";
            if (self._handlers.onActionError) self._handlers.onActionError(msg);
            return { error: msg };
          }
          return { ok: true };
        })
        .catch(function () {
          var msg = "Network error — action not sent";
          if (self._handlers.onActionError) self._handlers.onActionError(msg);
          return { error: msg };
        });
    },

    /* ---- leave the game/lobby ---- */
    leave: function () {
      var self = this;
      var p = Promise.resolve();
      if (this.hasSession()) {
        p = this.sendAction({ type: "lobby_leave" }).catch(function () {});
      }
      return p.then(function () {
        self.disconnect();
        self.clearSession();
      });
    },
  };

  window.Net = Net;
})();
