/* ============================================================
   app.js  ·  window.App  (entry point)
   Wires Net <-> BoardView <-> UI, owns the interaction state machine
   (placement modes, robber), and drives everything from the server's
   pushed {state, legal}. The client never computes rules.
   ============================================================ */
(function () {
  "use strict";

  var App = {
    state: null,
    legal: null,
    colorMap: {},
    mode: null, // null | "road" | "settlement" | "city" | "robber" | "setup_settlement" | "setup_road"
    _modeManual: false, // true if the user explicitly entered a build mode (so we don't auto-cancel)
    _stealHex: null,

    autoRoll: false,
    autoSell: false,

    boot: function () {
      Sound.init();
      try {
        App.autoRoll = localStorage.getItem("hexara.autoroll") === "1";
        App.autoSell = localStorage.getItem("hexara.autosell") === "1";
      } catch (e) {}

      // Wire UI callbacks.
      UI.init({
        onJoin: function (name, room, password) {
          App._doJoin(name, room, password);
        },
        onSetName: function (name) {
          Net.sendAction({ type: "lobby_set_name", name: name });
        },
        onSetColor: function (color) {
          Net.sendAction({ type: "lobby_set_color", color: color });
        },
        onAddBot: function () {
          Net.sendAction({ type: "lobby_add_bot" });
        },
        onSetConfig: function (config) {
          Net.sendAction({ type: "lobby_set_config", config: config });
        },
        onRemove: function (target) {
          Net.sendAction({ type: "lobby_remove", target: target });
        },
        onStart: function () {
          Net.sendAction({ type: "lobby_start" });
        },
        onLeave: function () {
          App._leave();
        },
        onRoll: function () {
          Net.sendAction({ type: "roll_dice" });
        },
        onEndTurn: function () {
          App._cancelMode();
          Net.sendAction({ type: "end_turn" });
        },
        onBuyDev: function () {
          Net.sendAction({ type: "buy_dev_card" });
        },
        onEnterMode: function (mode) {
          App._enterMode(mode, true);
        },
        onPlayDev: function (kind) {
          App._playDev(kind);
        },
        onBankTrade: function (give, receive) {
          Net.sendAction({ type: "bank_trade", give: give, receive: receive });
        },
        onProposeTrade: function (give, receive, to) {
          Net.sendAction({ type: "propose_trade", give: give, receive: receive, to: to });
        },
        onAcceptTrade: function () {
          Net.sendAction({ type: "accept_trade" });
        },
        onCancelTrade: function () {
          Net.sendAction({ type: "cancel_trade" });
        },
        onDiscard: function (resources) {
          Net.sendAction({ type: "discard", resources: resources });
        },
        onMoveRobber: function (hex, target) {
          App._sendMoveRobber(hex, target);
        },
        onPlayYearOfPlenty: function (resources) {
          Net.sendAction({ type: "play_year_of_plenty", resources: resources });
        },
        onPlayMonopoly: function (resource) {
          Net.sendAction({ type: "play_monopoly", resource: resource });
        },
      });

      // Wire Net handlers.
      Net.on("onLobby", function (msg) {
        App._onLobby(msg);
      })
        .on("onState", function (msg) {
          App._onState(msg);
        })
        .on("onError", function (m) {
          App._onSessionError(m);
        })
        .on("onActionError", function (m) {
          UI.toast(m || "Illegal move");
        })
        .on("onOpen", function () {
          /* connected */
        })
        .on("onDrop", function () {
          /* EventSource auto-reconnects; no UI churn needed */
        });

      // Board.
      var canvas = document.getElementById("board-canvas");
      BoardView.init(canvas, App.colorMap);
      BoardView.onClick(function (hit, ev) {
        App._onBoardClick(hit, ev);
      });

      // Global cancel: Esc and right-click cancel an active manual mode.
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          if (UI._openModalKind && UI._openModalKind !== "discard" && UI._openModalKind !== "win") {
            UI.closeModal();
          } else if (App._modeManual) {
            App._cancelMode();
          }
        }
      });
      canvas.addEventListener("contextmenu", function () {
        if (App._modeManual) App._cancelMode();
      });

      // Ask the server whether an access code is required (off by default).
      fetch("/api/config")
        .then(function (r) { return r.json(); })
        .then(function (cfg) { UI.showPasswordField(!!(cfg && cfg.requirePassword)); })
        .catch(function () {});

      // Show the all-time wins leaderboard on the join screen.
      UI.loadLeaderboard();

      // Initial route: resume session or show join.
      if (Net.loadSession() && Net.hasSession()) {
        UI.show("game"); // optimistic; first SSE frame replaces it
        UI.toast("Reconnecting…", "info");
        Net.connect();
      } else {
        UI.show("join");
      }
    },

    /* ===================== session/join ===================== */
    _doJoin: function (name, room, password) {
      Net.join(name, room, password).then(
        function () {
          UI.setJoinBusy(false);
          UI.joinError("");
          Net.connect();
        },
        function (err) {
          UI.joinError(err.message || "Could not join.");
        }
      );
    },

    _leave: function () {
      Net.leave().then(function () {
        App.state = null;
        App.legal = null;
        App._cancelMode();
        UI.closeModal();
        UI.show("join");
        UI.setJoinBusy(false);
        UI.joinError("");
        UI.loadLeaderboard();
      });
    },

    _onSessionError: function (msg) {
      App.state = null;
      App.legal = null;
      App._cancelMode();
      UI.closeModal();
      UI.show("join");
      UI.setJoinBusy(false);
      if (msg) UI.joinError(msg);
    },

    _onLobby: function (msg) {
      // Leaving the game state behind if we drop back to a lobby.
      App.state = null;
      App.legal = null;
      UI.renderLobby(msg);
    },

    /* ===================== state push ===================== */
    _onState: function (msg) {
      var state = msg.state || {};
      var legal = msg.legal || {};
      var prev = App.state;
      App.state = state;
      App.legal = legal;

      // colour map from players[].color
      var cm = {};
      (state.players || []).forEach(function (p) {
        if (p && p.id) cm[p.id] = p.color || "#cccccc";
      });
      App.colorMap = cm;
      BoardView.setColorMap(cm);

      // Re-evaluate interaction mode against the new legal set.
      App._reconcileMode(prev, state, legal);

      // Make the game screen visible BEFORE drawing so the canvas has real
      // dimensions (otherwise the first paint sizes against a 0px hidden canvas).
      UI.show("game");
      BoardView.resize();

      // Render board with current highlight, then the rest of the DOM.
      BoardView.draw(state, App._highlightSpec());
      UI.renderGame(state, legal);

      // Sounds, the your-turn glow, and auto-roll all react to the new state.
      App._reactToState(prev, state, legal);
      // A rolled 7 kicks you out of the casino: the robber wants placing and
      // the board is hidden behind the modal otherwise. (A required discard
      // already replaces the casino with the discard picker.)
      if (UI._openModalKind === "casino" && legal.robberMove) {
        UI.closeModal();
        UI.toast("A 7! Leave the table and place the robber.", "info");
      }
      if (UI._openModalKind === "casino" && window.Casino) Casino.refresh();
    },

    // Sounds + visual cues + auto-roll, driven by what changed since last state.
    _reactToState: function (prev, state, legal) {
      var ended = state.phase === "ended";
      var mine = state.currentPlayer === state.yourId;
      var wasMine = prev && prev.currentPlayer === state.yourId;

      // Your-turn cue (red border glow + chime) on the transition into your turn.
      if (mine && !wasMine && !ended) {
        UI.flashTurn();
        Sound.play("turn");
      }

      // Dice landed (any player) — the roll histogram total ticks up by one.
      var rolls = App._sumStats(state.rollStats);
      if (App._lastRolls != null && rolls > App._lastRolls) Sound.play("dice");
      App._lastRolls = rolls;

      // A piece was placed anywhere on the board.
      var pieces = App._countPieces(state);
      if (App._lastPieces != null && pieces > App._lastPieces) Sound.play("build");
      App._lastPieces = pieces;

      if (state.robberPhase && !(prev && prev.robberPhase)) Sound.play("robber");
      if (ended && !(prev && prev.phase === "ended")) Sound.play("win");

      App._maybeAutoRoll(state, legal);
      App._maybeAutoSell(state, legal);
    },

    setAutoSell: function (on) {
      App.autoSell = !!on;
      try { localStorage.setItem("hexara.autosell", on ? "1" : "0"); } catch (e) {}
      if (on && App.state && App.legal) App._maybeAutoSell(App.state, App.legal);
    },

    // When on, automatically buy development cards from your resources (so you
    // can cash them for beans). Dev cards can only be bought on your turn after
    // rolling, so this fires then, one card per state push until you can't.
    _maybeAutoSell: function (state, legal) {
      if (!App.autoSell || !state || state.phase !== "main" || App._autoSellPending) return;
      if (!legal || !legal.canBuyDev) return;
      App._autoSellPending = true;
      Net.sendAction({ type: "buy_dev_card" }).then(function () {
        App._autoSellPending = false;
      });
    },

    _sumStats: function (stats) {
      var n = 0;
      if (stats) for (var k in stats) if (stats.hasOwnProperty(k)) n += stats[k] || 0;
      return n;
    },
    _countPieces: function (state) {
      var n = 0;
      (state.players || []).forEach(function (p) {
        n += (p.builtSettlements || 0) + (p.builtCities || 0) + (p.builtRoads || 0);
      });
      return n;
    },

    setAutoRoll: function (on) {
      App.autoRoll = !!on;
      try { localStorage.setItem("hexara.autoroll", on ? "1" : "0"); } catch (e) {}
      if (on && App.state && App.legal) App._maybeAutoRoll(App.state, App.legal);
    },

    _maybeAutoRoll: function (state, legal) {
      if (!App.autoRoll || state.phase !== "main") return;
      if (!(legal.yourTurn && legal.canRoll && state.robberPhase == null && !state.diceRolled)) return;
      // One auto-roll per roll opportunity (keyed so a re-render can't double it).
      var key = state.currentPlayer + "#" + App._sumStats(state.rollStats);
      if (App._autoRolledKey === key) return;
      App._autoRolledKey = key;
      setTimeout(function () {
        if (App.autoRoll && App.legal && App.legal.canRoll && App.state && !App.state.diceRolled) {
          Net.sendAction({ type: "roll_dice" });
        }
      }, 350);
    },

    /* ===================== interaction modes ===================== */
    // Decide whether to auto-enter a mandatory mode, or clear a stale one.
    _reconcileMode: function (prev, state, legal) {
      // If it's not our turn / nothing actionable, clear any manual mode.
      if (state.phase === "ended") {
        App._setMode(null);
        return;
      }

      // 1) Discard is handled by a modal (UI auto-opens). Don't enter a board
      // mode, but DO raise an urgent, clickable banner over the board as a second
      // always-visible way back into the picker, so a 7 can never lock the game.
      if (legal.mustDiscard && legal.mustDiscard > 0) {
        App._setMode(null);
        var n = legal.mustDiscard;
        UI.setBanner(
          "Discard " + n + " card" + (n === 1 ? "" : "s") + " — click to choose.",
          null,
          { urgent: true, onClick: function () { UI.openDiscard(App.state, App.legal); } }
        );
        return;
      }

      // 2) Robber move (forced) — auto-enter robber mode.
      if (legal.robberMove) {
        App._setMode("robber", false);
        return;
      }

      // 3) Setup — auto-enter the correct placement mode.
      if (state.phase === "setup" && legal.yourTurn) {
        if ((legal.setupSettlementSpots || []).length) {
          App._setMode("setup_settlement", false);
          return;
        }
        if ((legal.setupRoadSpots || []).length) {
          App._setMode("setup_road", false);
          return;
        }
      }

      // 4) Free roads (Road Building / setup-style) — auto-enter road mode.
      if (state.freeRoads && state.freeRoads > 0 && (legal.roadSpots || []).length) {
        App._setMode("road", false);
        return;
      }

      // 5) Otherwise: keep a manual build mode only if it's still legal.
      if (App._modeManual && App.mode) {
        if (App._modeStillValid(App.mode, legal)) {
          App._setMode(App.mode, true); // refresh banner/highlights
          return;
        }
      }
      App._setMode(null);
    },

    _modeStillValid: function (mode, legal) {
      if (mode === "road") return (legal.roadSpots || []).length > 0;
      if (mode === "settlement") return (legal.settlementSpots || []).length > 0;
      if (mode === "city") return (legal.citySpots || []).length > 0;
      return false;
    },

    _enterMode: function (mode, manual) {
      // Toggle off if same manual mode clicked again.
      if (manual && App.mode === mode) {
        App._cancelMode();
        return;
      }
      App._setMode(mode, !!manual);
    },

    _setMode: function (mode, manual) {
      App.mode = mode;
      App._modeManual = mode ? !!manual : false;
      App._updateBanner();
      BoardView.setHighlight(App._highlightSpec());
    },

    _cancelMode: function () {
      // Don't cancel forced modes (robber/setup/free-road) — only manual ones.
      if (!App._modeManual) {
        // still clear manual selection visuals but keep forced highlights
        return;
      }
      App._setMode(null);
    },

    _updateBanner: function () {
      var m = App.mode;
      if (!m) {
        UI.setBanner(null);
        return;
      }
      var texts = {
        road: "Build mode: click a highlighted edge to place a road.",
        settlement: "Build mode: click a highlighted spot to place a settlement.",
        city: "Upgrade mode: click one of your settlements to build a city.",
        robber: "Move the robber: click a highlighted hex.",
        setup_settlement: "Setup: place your settlement on a highlighted spot.",
        setup_road: "Setup: place your road on a highlighted edge.",
      };
      var freeNote = App.state && App.state.freeRoads > 0 && m === "road" ? " (free road ×" + App.state.freeRoads + ")" : "";
      var cancellable = App._modeManual;
      UI.setBanner((texts[m] || "") + freeNote, cancellable ? function () {
        App._cancelMode();
      } : null);
    },

    // Build the highlight spec for BoardView from current mode + legal.
    _highlightSpec: function () {
      var legal = App.legal || {};
      var m = App.mode;
      var spec = { vertices: new Set(), edges: new Set(), hexes: new Set(), mode: m };
      if (!m) return spec;
      function addAll(set, arr) {
        (arr || []).forEach(function (x) {
          set.add(x);
        });
      }
      if (m === "road") addAll(spec.edges, legal.roadSpots);
      else if (m === "settlement") addAll(spec.vertices, legal.settlementSpots);
      else if (m === "city") addAll(spec.vertices, legal.citySpots);
      else if (m === "setup_settlement") addAll(spec.vertices, legal.setupSettlementSpots);
      else if (m === "setup_road") addAll(spec.edges, legal.setupRoadSpots);
      else if (m === "robber") {
        var byHex = legal.stealTargetsByHex || {};
        Object.keys(byHex).forEach(function (hid) {
          // hex ids may be numeric; Board stores numeric ids — coerce.
          var n = Number(hid);
          spec.hexes.add(isNaN(n) ? hid : n);
        });
      }
      return spec;
    },

    /* ===================== board clicks ===================== */
    _onBoardClick: function (hit, ev) {
      if (!hit || !App.state) return;
      var legal = App.legal || {};
      var m = App.mode;

      if (m === "road" && hit.edge != null) {
        if (App._inList(legal.roadSpots, hit.edge)) {
          Net.sendAction({ type: "build_road", edge: hit.edge });
        }
        return;
      }
      if (m === "settlement" && hit.vertex != null) {
        if (App._inList(legal.settlementSpots, hit.vertex)) {
          Net.sendAction({ type: "build_settlement", vertex: hit.vertex });
        }
        return;
      }
      if (m === "city" && hit.vertex != null) {
        if (App._inList(legal.citySpots, hit.vertex)) {
          Net.sendAction({ type: "build_city", vertex: hit.vertex });
        }
        return;
      }
      if (m === "setup_settlement" && hit.vertex != null) {
        if (App._inList(legal.setupSettlementSpots, hit.vertex)) {
          Net.sendAction({ type: "place_setup_settlement", vertex: hit.vertex });
        }
        return;
      }
      if (m === "setup_road" && hit.edge != null) {
        if (App._inList(legal.setupRoadSpots, hit.edge)) {
          Net.sendAction({ type: "place_setup_road", edge: hit.edge });
        }
        return;
      }
      if (m === "robber" && hit.hex != null) {
        App._handleRobberHex(hit.hex);
        return;
      }
    },

    _inList: function (list, val) {
      if (!list) return false;
      for (var i = 0; i < list.length; i++) {
        if (list[i] === val || String(list[i]) === String(val)) return true;
      }
      return false;
    },

    _handleRobberHex: function (hexId) {
      var legal = App.legal || {};
      var byHex = legal.stealTargetsByHex || {};
      // candidate hexes are the keys; clicking a non-candidate is ignored.
      var key = byHex.hasOwnProperty(hexId) ? hexId : (byHex.hasOwnProperty(String(hexId)) ? String(hexId) : null);
      if (key === null) {
        // The hex might still be a legal robber destination with no targets.
        // Server lists every hex (except current robber hex) in stealTargetsByHex,
        // so a missing key means it's the robber's current hex — ignore.
        return;
      }
      var targets = byHex[key] || [];
      if (targets.length === 0) {
        App._sendMoveRobber(hexId, null);
      } else if (targets.length === 1) {
        App._sendMoveRobber(hexId, targets[0]);
      } else {
        // ask which player to rob
        UI.openStealChooser(App.state, hexId, targets);
      }
    },

    _sendMoveRobber: function (hex, target) {
      Net.sendAction({ type: "move_robber", hex: hex, target: target == null ? null : target });
    },

    /* ===================== dev cards ===================== */
    _playDev: function (kind) {
      if (kind === "knight") {
        // After this, the server sends a new state with robberMove → we auto-enter robber mode.
        Net.sendAction({ type: "play_knight" });
      } else if (kind === "road_building") {
        // Grants 2 free roads; subsequent state has freeRoads>0 → auto road mode.
        Net.sendAction({ type: "play_road_building" });
      } else if (kind === "year_of_plenty") {
        UI.openYearOfPlenty(App.state);
      } else if (kind === "monopoly") {
        UI.openMonopoly(App.state);
      }
    },
  };

  window.App = App;

  document.addEventListener("DOMContentLoaded", function () {
    App.boot();
  });
})();
