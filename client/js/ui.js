/* ============================================================
   ui.js  ·  window.UI
   All non-canvas DOM: join/lobby/game screens, top bar, players
   panel, your hand, action bar, modals, toasts and the log.
   UI is dumb about rules — it renders state + enables controls purely
   from the server's `legal` object, and calls back into App for actions.
   ============================================================ */
(function () {
  "use strict";

  var RES = ["wood", "brick", "sheep", "wheat", "ore"];
  var RES_COLOR = {
    wood: "#2e7d32",
    brick: "#c1572e",
    sheep: "#8bc34a",
    wheat: "#e3b23c",
    ore: "#6b7785",
  };
  var RES_LABEL = { wood: "Wood", brick: "Brick", sheep: "Sheep", wheat: "Wheat", ore: "Ore" };
  var DEV_LABEL = {
    knight: "Knight",
    victory_point: "Victory Point",
    road_building: "Road Building",
    year_of_plenty: "Year of Plenty",
    monopoly: "Monopoly",
  };
  var DEV_ORDER = ["knight", "victory_point", "road_building", "year_of_plenty", "monopoly"];

  function $(id) {
    return document.getElementById(id);
  }
  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }
  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }
  function num(n) {
    return n == null ? 0 : n;
  }

  // <img> with an automatic coded fallback if the SVG fails to load.
  function assetImg(src, cls, fbText, fbColor) {
    var img = el("img", cls);
    img.alt = fbText || "";
    img.src = src;
    img.onerror = function () {
      var fb = el("div", (cls || "") + " " + (cls ? cls + "-fb" : "img-fb"), fbText || "");
      if (fbColor) fb.style.background = fbColor;
      if (img.parentNode) img.parentNode.replaceChild(fb, img);
    };
    return img;
  }

  var UI = {
    cb: {}, // App-provided callbacks
    _lastTradeSig: null,

    init: function (callbacks) {
      this.cb = callbacks || {};
      this._wireJoin();
      this._wireLobbyStatic();
    },

    /* ===================== SCREEN SWITCHING ===================== */
    show: function (which) {
      ["join", "lobby", "game"].forEach(function (s) {
        var node = $("screen-" + s);
        if (node) node.hidden = s !== which;
      });
    },

    /* ===================== JOIN ===================== */
    _wireJoin: function () {
      var self = this;
      var form = $("join-form");
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var name = $("join-name").value.trim();
        var room = $("join-room").value.trim();
        var passEl = $("join-pass");
        var pass = passEl ? passEl.value : "";
        if (!name) {
          self.joinError("Please enter a name.");
          return;
        }
        self.setJoinBusy(true);
        if (self.cb.onJoin) self.cb.onJoin(name, room, pass);
      });
    },
    showPasswordField: function (show) {
      var f = $("join-pass-field");
      if (f) f.hidden = !show;
    },
    setJoinBusy: function (busy) {
      var b = $("join-btn");
      if (b) {
        b.disabled = busy;
        b.textContent = busy ? "Connecting…" : "Join / Create Game";
      }
    },
    joinError: function (msg) {
      var e = $("join-error");
      if (e) e.textContent = msg || "";
      this.setJoinBusy(false);
    },

    /* ===================== LOBBY ===================== */
    _wireLobbyStatic: function () {
      var self = this;
      $("lobby-copy").addEventListener("click", function () {
        var code = $("lobby-code").textContent;
        if (navigator.clipboard && code) {
          navigator.clipboard.writeText(code).then(
            function () {
              self.toast("Room code copied", "info");
            },
            function () {}
          );
        }
      });
      $("lobby-name").addEventListener("change", function () {
        var v = this.value.trim();
        if (v && self.cb.onSetName) self.cb.onSetName(v);
      });
      $("lobby-add-bot").addEventListener("click", function () {
        if (self.cb.onAddBot) self.cb.onAddBot();
      });
      $("lobby-start").addEventListener("click", function () {
        if (self.cb.onStart) self.cb.onStart();
      });
      $("lobby-leave").addEventListener("click", function () {
        if (self.cb.onLeave) self.cb.onLeave();
      });
    },

    renderLobby: function (msg) {
      var lobby = (msg && msg.lobby) || {};
      var youId = msg && msg.youId;
      this.show("lobby");
      $("lobby-code").textContent = lobby.code || "----";

      var me = (lobby.players || []).filter(function (p) {
        return p.id === youId;
      })[0];
      var isHost = !!(me && me.isHost) || lobby.host === youId;

      // name field (don't clobber while focused)
      var nameInput = $("lobby-name");
      if (document.activeElement !== nameInput && me) nameInput.value = me.name || "";

      // players list
      var list = $("lobby-players");
      clear(list);
      (lobby.players || []).forEach(function (p) {
        var li = el("li", "lobby-player");
        var sw = el("span", "swatch");
        sw.style.background = p.color || "#888";
        li.appendChild(sw);
        var nm = el("span", "pname", p.name || "Player");
        li.appendChild(nm);
        if (p.isHost) li.appendChild(el("span", "tag tag-host", "Host"));
        if (p.isBot) li.appendChild(el("span", "tag tag-bot", "Bot"));
        if (p.id === youId) li.appendChild(el("span", "tag tag-you", "You"));
        li.appendChild(el("span", "tag " + (p.connected ? "tag-on" : "tag-off"), p.connected ? "Online" : "Offline"));
        // host can remove others
        if (isHost && p.id !== youId) {
          var kick = el("button", "btn btn-sm btn-danger kick", "Remove");
          kick.addEventListener("click", function () {
            if (UI.cb.onRemove) UI.cb.onRemove(p.id);
          });
          li.appendChild(kick);
        }
        list.appendChild(li);
      });

      // palette
      var pal = $("lobby-palette");
      clear(pal);
      var taken = {};
      (lobby.players || []).forEach(function (p) {
        if (p.id !== youId && p.color) taken[p.color.toLowerCase()] = true;
      });
      var myColor = me && me.color ? me.color.toLowerCase() : null;
      (lobby.palette || []).forEach(function (c) {
        var hex = (c.hex || "").toLowerCase();
        var sw = el("button", "swatch-pick");
        sw.style.background = c.hex;
        sw.title = c.name || c.hex;
        if (hex === myColor) sw.classList.add("sel");
        if (taken[hex]) {
          sw.classList.add("taken");
          sw.disabled = true;
        } else {
          sw.addEventListener("click", function () {
            if (UI.cb.onSetColor) UI.cb.onSetColor(c.hex);
          });
        }
        pal.appendChild(sw);
      });

      // host controls
      $("lobby-host-controls").style.display = isHost ? "flex" : "none";
      var minP = lobby.minPlayers || 2;
      var count = (lobby.players || []).length;
      var startBtn = $("lobby-start");
      startBtn.disabled = !isHost || count < minP;
      var maxP = lobby.maxPlayers || 6;
      $("lobby-add-bot").disabled = !isHost || count >= maxP;

      var hint = $("lobby-hint");
      if (!isHost) hint.textContent = "Waiting for the host to start the game…";
      else if (count < minP) hint.textContent = "Need at least " + minP + " players to start (" + count + "/" + minP + ").";
      else hint.textContent = "Ready when you are — press Start Game.";
    },

    /* ===================== GAME: full render ===================== */
    renderGame: function (state, legal) {
      legal = legal || {};
      this.show("game");
      this._renderTopbar(state, legal);
      this._renderPlayers(state, legal);
      this._renderHand(state, legal);
      this._renderActionBar(state, legal);
      this._renderLog(state);
      this._maybeAutoModals(state, legal);
      if (state.phase === "ended") this._renderWin(state);
    },

    colorOf: function (state, pid) {
      var found = null;
      (state.players || []).some(function (p) {
        if (p.id === pid) {
          found = p.color;
          return true;
        }
        return false;
      });
      return found || "#cccccc";
    },
    nameOf: function (state, pid) {
      var found = null;
      (state.players || []).some(function (p) {
        if (p.id === pid) {
          found = p.name;
          return true;
        }
        return false;
      });
      return found || "Player";
    },

    /* ---- top bar ---- */
    _renderTopbar: function (state, legal) {
      // turn indicator
      var ti = $("turn-indicator");
      var sw = ti.querySelector(".turn-swatch");
      var tx = ti.querySelector(".turn-text");
      var cur = state.currentPlayer;
      if (state.phase === "ended") {
        ti.classList.remove("you");
        sw.style.background = state.winner ? this.colorOf(state, state.winner) : "#888";
        tx.textContent = state.winner ? this.nameOf(state, state.winner) + " wins!" : "Game over";
      } else if (cur) {
        sw.style.background = this.colorOf(state, cur);
        var mine = cur === state.yourId;
        ti.classList.toggle("you", mine);
        var label = mine ? "Your turn" : this.nameOf(state, cur) + "'s turn";
        if (state.phase === "setup") label += " · setup";
        tx.textContent = label;
      } else {
        ti.classList.remove("you");
        tx.textContent = "Waiting…";
      }

      // dice
      var dice = state.dice;
      for (var i = 0; i < 2; i++) {
        var d = $("die-" + i);
        var val = dice && dice[i];
        this._setDie(d, i, val, state.diceRolled);
      }

      // bank counts
      var bank = $("bank-counts");
      clear(bank);
      var bk = state.bank || {};
      RES.forEach(function (r) {
        var item = el("span", "bank-res");
        var dot = el("span", "bank-dot");
        dot.style.background = RES_COLOR[r];
        dot.title = RES_LABEL[r];
        item.appendChild(dot);
        item.appendChild(document.createTextNode(String(num(bk[r]))));
        bank.appendChild(item);
      });

      // dev deck
      var devEl = $("dev-deck");
      var dv = devEl.querySelector(".stat-val");
      if (dv) dv.textContent = String(num(state.devDeckCount));

      // awards
      this._renderAward($("award-road"), state, state.longestRoadOwner, "🛣", state.longestRoadLen ? "Road " + state.longestRoadLen : "Longest Road");
      this._renderAward($("award-army"), state, state.largestArmyOwner, "⚔", "Largest Army");
    },

    _setDie: function (node, idx, val, rolled) {
      if (!node) return;
      // replace failed-img fallback divs if present
      if (node.tagName !== "IMG") {
        var img = el("img", "die");
        img.id = "die-" + idx;
        node.parentNode.replaceChild(img, node);
        node = img;
      }
      if (!rolled || !val) {
        node.classList.add("hidden-die");
        return;
      }
      node.classList.remove("hidden-die");
      node.src = "/assets/ui/die_" + val + ".svg";
      node.alt = "die " + val;
      node.onerror = function () {
        var fb = el("div", "die-fb", String(val));
        fb.id = "die-" + idx;
        if (node.parentNode) node.parentNode.replaceChild(fb, node);
      };
    },

    _renderAward: function (node, state, owner, ico, baseLabel) {
      if (!node) return;
      var val = node.querySelector(".stat-val");
      node.classList.toggle("held", !!owner);
      if (owner) {
        if (val) val.textContent = this.nameOf(state, owner);
        node.style.boxShadow = "0 0 0 2px " + this.colorOf(state, owner) + " inset";
        node.title = baseLabel + " — " + this.nameOf(state, owner);
      } else {
        if (val) val.textContent = "—";
        node.style.boxShadow = "";
        node.title = baseLabel + " — unclaimed";
      }
    },

    /* ---- players panel ---- */
    _renderPlayers: function (state, legal) {
      var panel = $("players-panel");
      clear(panel);
      var order = state.order && state.order.length ? state.order : (state.players || []).map(function (p) { return p.id; });
      var byId = {};
      (state.players || []).forEach(function (p) {
        byId[p.id] = p;
      });
      var self = this;
      order.forEach(function (pid) {
        var p = byId[pid];
        if (!p) return;
        var card = el("div", "pcard");
        if (pid === state.currentPlayer) card.classList.add("current");
        if (p.connected === false) card.classList.add("disconnected");

        var head = el("div", "pcard-head");
        var sw = el("span", "swatch");
        sw.style.background = p.color || "#888";
        head.appendChild(sw);
        var nm = el("span", "pname", p.name + (pid === state.yourId ? " (you)" : ""));
        head.appendChild(nm);
        head.appendChild(el("span", "vp-badge", num(p.vp) + " VP"));
        card.appendChild(head);

        var stats = el("div", "pcard-stats");
        stats.appendChild(self._st("✋", num(p.resourceCount), "Cards in hand"));
        stats.appendChild(self._st("🃏", num(p.devCount), "Development cards"));
        stats.appendChild(self._st("⚔", num(p.playedKnights), "Knights played"));
        if (p.ports && p.ports.length) {
          stats.appendChild(self._st("⚓", p.ports.length, "Ports: " + p.ports.join(", ")));
        }
        card.appendChild(stats);

        var pieces = el("div", "pieces-left");
        pieces.appendChild(el("span", null, "🛣 " + num(p.roadsLeft)));
        pieces.appendChild(el("span", null, "🏠 " + num(p.settlementsLeft)));
        pieces.appendChild(el("span", null, "🏰 " + num(p.citiesLeft)));
        card.appendChild(pieces);

        if (p.hasLongestRoad || p.hasLargestArmy) {
          var badges = el("div", "pcard-badges");
          if (p.hasLongestRoad) badges.appendChild(el("span", "mini-badge", "Longest Road"));
          if (p.hasLargestArmy) badges.appendChild(el("span", "mini-badge army", "Largest Army"));
          card.appendChild(badges);
        }
        if (p.connected === false) {
          var off = el("div", "pcard-badges");
          off.appendChild(el("span", "tag tag-off", "Disconnected"));
          card.appendChild(off);
        }
        panel.appendChild(card);
      });
    },
    _st: function (ico, val, title) {
      var s = el("span", "st");
      if (title) s.title = title;
      s.appendChild(document.createTextNode(ico + " " + val));
      return s;
    },

    /* ---- your hand ---- */
    _renderHand: function (state, legal) {
      var panel = $("hand-panel");
      clear(panel);
      var me = null;
      (state.players || []).some(function (p) {
        if (p.id === state.yourId) {
          me = p;
          return true;
        }
        return false;
      });
      panel.appendChild(el("h3", null, "Your Hand"));
      if (!me) {
        panel.appendChild(el("p", "empty-note", "Spectating."));
        return;
      }

      // resources
      var resRow = el("div", "hand-row");
      var res = me.resources || {};
      var anyRes = false;
      RES.forEach(function (r) {
        var n = num(res[r]);
        if (n <= 0) return;
        anyRes = true;
        resRow.appendChild(UI._card("/assets/cards/res_" + r + ".svg", RES_LABEL[r], RES_COLOR[r], n, false));
      });
      if (!anyRes) panel.appendChild(el("p", "empty-note", "No resource cards."));
      else panel.appendChild(resRow);

      // dev cards: playable (face up) + new (greyed, face-down)
      var devRow = el("div", "hand-row");
      var dev = me.dev || {};
      var devNew = me.devNew || {};
      var anyDev = false;
      DEV_ORDER.forEach(function (d) {
        var n = num(dev[d]);
        if (n <= 0) return;
        anyDev = true;
        devRow.appendChild(UI._card("/assets/cards/dev_" + d + ".svg", DEV_LABEL[d], "#3b2a6b", n, false));
      });
      // new (bought this turn) — show as face-down/greyed, not playable yet
      var newTotal = 0;
      DEV_ORDER.forEach(function (d) {
        newTotal += num(devNew[d]);
      });
      if (newTotal > 0) {
        anyDev = true;
        var c = UI._card("/assets/cards/dev_back.svg", "New (next turn)", "#3b2a6b", newTotal, true);
        c.title = "Bought this turn — playable next turn";
        devRow.appendChild(c);
      }
      if (anyDev) {
        panel.appendChild(el("h3", null, "Development Cards"));
        panel.appendChild(devRow);
      }
    },

    _card: function (src, label, color, count, faded) {
      var stack = el("div", "card-stack");
      var img = assetImg(src, "card-img" + (faded ? " faded" : ""), label, color);
      // give the coded fallback some readable content
      img.onerror = (function (origLabel, origColor) {
        return function () {
          var fb = el("div", "card-fb" + (faded ? " faded" : ""));
          fb.style.background = origColor;
          fb.appendChild(el("strong", null, origLabel));
          if (img.parentNode) img.parentNode.replaceChild(fb, img);
        };
      })(label, color);
      stack.appendChild(img);
      if (count != null && count > 1) {
        stack.appendChild(el("span", "card-count", "×" + count));
      } else if (count === 1) {
        stack.appendChild(el("span", "card-count", "1"));
      }
      stack.title = label + (count ? " ×" + count : "");
      return stack;
    },

    /* ---- action bar ---- */
    _renderActionBar: function (state, legal) {
      var bar = $("action-bar");
      clear(bar);
      var self = this;
      var yourTurn = !!legal.yourTurn;

      // During setup there are no buttons (board click drives it) — show a hint.
      if (state.phase === "setup") {
        var hint = el("span", "mode-hint");
        if (yourTurn) {
          hint.textContent =
            state.setup && state.setup.sub === "road"
              ? "Setup: click a highlighted edge to place your road."
              : "Setup: click a highlighted spot to place your settlement.";
        } else {
          hint.textContent = "Setup — waiting for " + this.nameOf(state, state.currentPlayer) + "…";
        }
        bar.appendChild(hint);
        return;
      }
      if (state.phase === "ended") {
        bar.appendChild(el("span", "mode-hint", "Game over."));
        return;
      }

      // Roll
      var roll = this._actBtn("/assets/ui/icon_card.svg", "Roll Dice", legal.canRoll, function () {
        if (self.cb.onRoll) self.cb.onRoll();
      });
      bar.appendChild(roll);

      // Build road / settlement / city
      bar.appendChild(this._actBtn("/assets/ui/icon_road.svg", "Build Road", (legal.roadSpots || []).length > 0, function () {
        if (self.cb.onEnterMode) self.cb.onEnterMode("road");
      }));
      bar.appendChild(this._actBtn("/assets/ui/icon_settlement.svg", "Settlement", (legal.settlementSpots || []).length > 0, function () {
        if (self.cb.onEnterMode) self.cb.onEnterMode("settlement");
      }));
      bar.appendChild(this._actBtn("/assets/ui/icon_city.svg", "City", (legal.citySpots || []).length > 0, function () {
        if (self.cb.onEnterMode) self.cb.onEnterMode("city");
      }));

      // Buy dev
      bar.appendChild(this._actBtn("/assets/cards/dev_back.svg", "Buy Dev Card", !!legal.canBuyDev, function () {
        if (self.cb.onBuyDev) self.cb.onBuyDev();
      }));

      // Trade
      bar.appendChild(this._actBtn(null, "Trade", !!legal.canTrade || !!legal.tradeRespond || (state.trade && state.trade.from === state.yourId), function () {
        UI.openTrade(state, legal);
      }));

      // Play dev (dropdown)
      var playable = legal.playableDev || [];
      var devWrap = el("span", "dev-play-wrap");
      var devBtn = this._actBtn(null, "Play Dev ▾", playable.length > 0, function (ev) {
        ev.stopPropagation();
        self._toggleDevMenu(devWrap, playable);
      });
      devWrap.appendChild(devBtn);
      bar.appendChild(devWrap);

      bar.appendChild(el("span", "spacer"));

      // End turn
      bar.appendChild(this._actBtn(null, "End Turn", !!legal.canEndTurn, function () {
        if (self.cb.onEndTurn) self.cb.onEndTurn();
      }, "btn-primary"));

      // If there's an open trade addressed to me, surface a quick badge button too
      if (legal.tradeRespond) {
        var resp = this._actBtn(null, "Respond to Trade", true, function () {
          UI.openTrade(state, legal);
        }, "btn-primary");
        bar.insertBefore(resp, bar.firstChild);
      }
    },

    _actBtn: function (icon, label, enabled, onClick, extraCls) {
      var b = el("button", "btn btn-act" + (extraCls ? " " + extraCls : ""));
      if (icon) {
        var img = assetImg(icon, "bico", "");
        img.onerror = function () {
          if (img.parentNode) img.parentNode.removeChild(img);
        };
        b.appendChild(img);
      }
      b.appendChild(document.createTextNode(label));
      b.disabled = !enabled;
      if (enabled) b.addEventListener("click", onClick);
      return b;
    },

    _toggleDevMenu: function (wrap, playable) {
      var existing = wrap.querySelector(".dev-menu");
      if (existing) {
        existing.remove();
        return;
      }
      var menu = el("div", "dev-menu");
      var self = this;
      playable.forEach(function (d) {
        var b = el("button", null, DEV_LABEL[d] || d);
        b.addEventListener("click", function () {
          menu.remove();
          if (self.cb.onPlayDev) self.cb.onPlayDev(d);
        });
        menu.appendChild(b);
      });
      wrap.appendChild(menu);
      // close on outside click
      setTimeout(function () {
        function off(e) {
          if (!wrap.contains(e.target)) {
            menu.remove();
            document.removeEventListener("click", off);
          }
        }
        document.addEventListener("click", off);
      }, 0);
    },

    /* ---- log ---- */
    _renderLog: function (state) {
      var log = $("log");
      var lines = state.log || [];
      // cheap diff: only rebuild if length/last changed
      var sig = lines.length + "|" + (lines[lines.length - 1] || "");
      if (this._logSig === sig) return;
      this._logSig = sig;
      clear(log);
      lines.forEach(function (line) {
        log.appendChild(el("div", "log-line", line));
      });
      log.scrollTop = log.scrollHeight;
    },

    /* ===================== MODALS ===================== */
    _modalRoot: function () {
      return $("modal-root");
    },
    closeModal: function () {
      var root = this._modalRoot();
      root.hidden = true;
      clear(root);
      this._openModalKind = null;
    },
    _openModal: function (node, kind) {
      var root = this._modalRoot();
      clear(root);
      root.hidden = false;
      root.appendChild(node);
      this._openModalKind = kind;
    },

    // Auto-open mandatory modals (discard). Year/Monopoly are opened on demand
    // by App when the player chooses to play them.
    _maybeAutoModals: function (state, legal) {
      if (legal.mustDiscard && legal.mustDiscard > 0) {
        if (this._openModalKind !== "discard") this.openDiscard(state, legal);
        return;
      }
      // If a discard modal is up but no longer required, close it.
      if (this._openModalKind === "discard" && (!legal.mustDiscard || legal.mustDiscard <= 0)) {
        this.closeModal();
      }
      // Keep trade modal in sync if an incoming trade changed/cleared.
      if (this._openModalKind === "trade") {
        this.openTrade(state, legal); // re-render in place
      }
    },

    /* ---- discard modal ---- */
    openDiscard: function (state, legal) {
      var need = legal.mustDiscard || 0;
      var me = this._me(state);
      var have = (me && me.resources) || {};
      var pick = { wood: 0, brick: 0, sheep: 0, wheat: 0, ore: 0 };

      var modal = el("div", "modal");
      modal.appendChild(el("h2", null, "Discard " + need + " card" + (need === 1 ? "" : "s")));
      modal.appendChild(el("p", "sub", "You have more than 7 cards — choose exactly " + need + " to discard."));

      var picker = el("div", "res-picker");
      var totalEl = el("div", "pick-total");
      function refresh() {
        var sum = RES.reduce(function (a, r) {
          return a + pick[r];
        }, 0);
        totalEl.textContent = "Selected " + sum + " / " + need;
        totalEl.className = "pick-total " + (sum === need ? "good" : "bad");
        confirm.disabled = sum !== need;
      }
      var confirm;
      RES.forEach(function (r) {
        var max = num(have[r]);
        var cell = UI._resStepper(r, max, function (v) {
          pick[r] = v;
          refresh();
        });
        picker.appendChild(cell);
      });
      modal.appendChild(picker);
      modal.appendChild(totalEl);

      var actions = el("div", "modal-actions");
      confirm = el("button", "btn btn-primary", "Discard");
      confirm.addEventListener("click", function () {
        var out = {};
        RES.forEach(function (r) {
          if (pick[r] > 0) out[r] = pick[r];
        });
        if (UI.cb.onDiscard) UI.cb.onDiscard(out);
        // leave modal up; it closes when state says mustDiscard===0
      });
      actions.appendChild(confirm);
      modal.appendChild(actions);
      this._openModal(modal, "discard");
      refresh();
    },

    // A resource stepper used in discard / year-of-plenty.
    _resStepper: function (r, max, onChange, allowUnbounded) {
      var cell = el("div", "res-pick");
      cell.appendChild(UI._smallResCard(r));
      var ctrls = el("div", "picker-controls");
      var minus = el("button", null, "−");
      var cnt = el("span", "pcount", "0");
      var plus = el("button", null, "+");
      var val = 0;
      function set(v) {
        val = Math.max(0, allowUnbounded ? v : Math.min(max, v));
        cnt.textContent = String(val);
        minus.disabled = val <= 0;
        plus.disabled = !allowUnbounded && val >= max;
        onChange(val);
      }
      minus.addEventListener("click", function () {
        set(val - 1);
      });
      plus.addEventListener("click", function () {
        set(val + 1);
      });
      ctrls.appendChild(minus);
      ctrls.appendChild(cnt);
      ctrls.appendChild(plus);
      cell.appendChild(ctrls);
      cell.appendChild(el("span", "have", "have " + max));
      set(0);
      return cell;
    },

    _smallResCard: function (r) {
      var img = assetImg("/assets/cards/res_" + r + ".svg", "", RES_LABEL[r], RES_COLOR[r]);
      img.onerror = function () {
        var fb = el("div", "card-fb");
        fb.style.background = RES_COLOR[r];
        fb.appendChild(el("strong", null, RES_LABEL[r]));
        if (img.parentNode) img.parentNode.replaceChild(fb, img);
      };
      return img;
    },

    /* ---- year of plenty ---- */
    openYearOfPlenty: function (state) {
      var pick = { wood: 0, brick: 0, sheep: 0, wheat: 0, ore: 0 };
      var modal = el("div", "modal");
      modal.appendChild(el("h2", null, "Year of Plenty"));
      modal.appendChild(el("p", "sub", "Take any 2 resources from the bank."));
      var picker = el("div", "res-picker");
      var totalEl = el("div", "pick-total");
      var confirm;
      function refresh() {
        var sum = RES.reduce(function (a, r) {
          return a + pick[r];
        }, 0);
        totalEl.textContent = "Selected " + sum + " / 2";
        totalEl.className = "pick-total " + (sum === 2 ? "good" : "bad");
        confirm.disabled = sum !== 2;
      }
      RES.forEach(function (r) {
        // bound by 2 (the gift size); bank availability is enforced server-side
        picker.appendChild(UI._resStepper(r, 2, function (v) {
          pick[r] = v;
          refresh();
        }));
      });
      modal.appendChild(picker);
      modal.appendChild(totalEl);
      var actions = el("div", "modal-actions");
      actions.appendChild(this._cancelBtn());
      confirm = el("button", "btn btn-primary", "Take");
      confirm.addEventListener("click", function () {
        var arr = [];
        RES.forEach(function (r) {
          for (var i = 0; i < pick[r]; i++) arr.push(r);
        });
        UI.closeModal();
        if (UI.cb.onPlayYearOfPlenty) UI.cb.onPlayYearOfPlenty(arr);
      });
      actions.appendChild(confirm);
      modal.appendChild(actions);
      this._openModal(modal, "yop");
      refresh();
    },

    /* ---- monopoly ---- */
    openMonopoly: function (state) {
      var chosen = null;
      var modal = el("div", "modal");
      modal.appendChild(el("h2", null, "Monopoly"));
      modal.appendChild(el("p", "sub", "Name one resource — every opponent gives you all of theirs."));
      var picker = el("div", "res-picker");
      var confirm;
      RES.forEach(function (r) {
        var cell = el("div", "res-pick");
        cell.style.cursor = "pointer";
        cell.appendChild(UI._smallResCard(r));
        cell.appendChild(el("span", "have", RES_LABEL[r]));
        cell.addEventListener("click", function () {
          chosen = r;
          Array.prototype.forEach.call(picker.children, function (c) {
            c.style.borderColor = "transparent";
          });
          cell.style.borderColor = "#2e8b57";
          confirm.disabled = false;
        });
        picker.appendChild(cell);
      });
      modal.appendChild(picker);
      var actions = el("div", "modal-actions");
      actions.appendChild(this._cancelBtn());
      confirm = el("button", "btn btn-primary", "Take all");
      confirm.disabled = true;
      confirm.addEventListener("click", function () {
        if (!chosen) return;
        UI.closeModal();
        if (UI.cb.onPlayMonopoly) UI.cb.onPlayMonopoly(chosen);
      });
      actions.appendChild(confirm);
      modal.appendChild(actions);
      this._openModal(modal, "monopoly");
    },

    /* ---- steal-target chooser (robber) ---- */
    openStealChooser: function (state, hex, targets) {
      var modal = el("div", "modal");
      modal.appendChild(el("h2", null, "Rob a player"));
      modal.appendChild(el("p", "sub", "Choose whom to steal a random card from."));
      var list = el("div", "res-picker");
      var self = this;
      targets.forEach(function (pid) {
        var cell = el("div", "res-pick");
        cell.style.cursor = "pointer";
        var sw = el("div", "swatch");
        sw.style.width = "40px";
        sw.style.height = "40px";
        sw.style.background = self.colorOf(state, pid);
        cell.appendChild(sw);
        cell.appendChild(el("span", "have", self.nameOf(state, pid)));
        cell.addEventListener("click", function () {
          UI.closeModal();
          if (UI.cb.onMoveRobber) UI.cb.onMoveRobber(hex, pid);
        });
        list.appendChild(cell);
      });
      modal.appendChild(list);
      var actions = el("div", "modal-actions");
      var none = el("button", "btn", "Rob no one");
      none.addEventListener("click", function () {
        UI.closeModal();
        if (UI.cb.onMoveRobber) UI.cb.onMoveRobber(hex, null);
      });
      actions.appendChild(none);
      modal.appendChild(actions);
      this._openModal(modal, "steal");
    },

    /* ---- trade panel (bank / propose / incoming) ---- */
    openTrade: function (state, legal) {
      var self = this;
      var me = this._me(state);
      var have = (me && me.resources) || {};
      // preserve chosen tab across re-renders
      var tab = this._tradeTab || (legal.tradeRespond && state.trade && state.trade.from !== state.yourId ? "incoming" : "bank");
      // If an incoming trade exists, default to it the first time.
      if (state.trade && state.trade.from !== state.yourId && !this._tradeTabUserSet) tab = "incoming";

      var modal = el("div", "modal");
      modal.appendChild(el("h2", null, "Trade"));

      var tabs = el("div", "trade-tabs");
      function mkTab(key, label, show) {
        if (!show) return;
        var t = el("div", "trade-tab" + (tab === key ? " sel" : ""), label);
        t.addEventListener("click", function () {
          self._tradeTab = key;
          self._tradeTabUserSet = true;
          self.openTrade(state, legal);
        });
        tabs.appendChild(t);
      }
      var hasIncoming = !!(state.trade && state.trade.from !== state.yourId);
      var iAmProposer = !!(state.trade && state.trade.from === state.yourId);
      mkTab("bank", "Bank / Port", true);
      mkTab("player", "Player Trade", legal.canTrade && !iAmProposer);
      mkTab("incoming", iAmProposer ? "Your Offer" : "Incoming Offer", hasIncoming || iAmProposer);
      modal.appendChild(tabs);

      var body = el("div");
      if (tab === "bank") body.appendChild(this._tradeBankBody(state, legal, have));
      else if (tab === "player") body.appendChild(this._tradePlayerBody(state, legal, have));
      else body.appendChild(this._tradeIncomingBody(state, legal));
      modal.appendChild(body);

      var actions = el("div", "modal-actions");
      actions.appendChild(this._cancelBtn("Close"));
      modal.appendChild(actions);
      this._openModal(modal, "trade");
    },

    _tradeBankBody: function (state, legal, have) {
      var wrap = el("div");
      var bankTrades = legal.bankTrades || {};
      var giveable = Object.keys(bankTrades);
      if (!giveable.length) {
        wrap.appendChild(el("p", "sub", "You don't have enough of any resource to trade with the bank yet."));
        return wrap;
      }
      var give = giveable[0];
      var receive = RES.filter(function (r) {
        return r !== give;
      })[0];

      var giveRow = el("div", "bank-trade-row");
      giveRow.appendChild(el("strong", null, "Give"));
      var giveSel = el("select");
      giveable.forEach(function (r) {
        var o = el("option", null, RES_LABEL[r] + "  (" + bankTrades[r] + ":1, have " + num(have[r]) + ")");
        o.value = r;
        giveSel.appendChild(o);
      });
      giveSel.addEventListener("change", function () {
        give = this.value;
      });
      giveRow.appendChild(giveSel);
      wrap.appendChild(giveRow);

      var recvRow = el("div", "bank-trade-row");
      recvRow.appendChild(el("strong", null, "Receive"));
      var recvSel = el("select");
      RES.forEach(function (r) {
        var o = el("option", null, RES_LABEL[r]);
        o.value = r;
        recvSel.appendChild(o);
      });
      recvSel.value = receive;
      recvSel.addEventListener("change", function () {
        receive = this.value;
      });
      recvRow.appendChild(recvSel);
      wrap.appendChild(recvRow);

      var go = el("button", "btn btn-primary", "Trade with bank");
      go.addEventListener("click", function () {
        if (give === receive) {
          UI.toast("Pick two different resources.");
          return;
        }
        UI.closeModal();
        if (UI.cb.onBankTrade) UI.cb.onBankTrade(give, receive);
      });
      wrap.appendChild(go);
      return wrap;
    },

    _tradePlayerBody: function (state, legal, have) {
      var wrap = el("div");
      var give = { wood: 0, brick: 0, sheep: 0, wheat: 0, ore: 0 };
      var receive = { wood: 0, brick: 0, sheep: 0, wheat: 0, ore: 0 };
      var grid = el("div", "trade-grid");

      var giveCol = el("div", "trade-col");
      giveCol.appendChild(el("h3", null, "You give"));
      RES.forEach(function (r) {
        giveCol.appendChild(UI._miniStep(r, "have " + num(have[r]), num(have[r]), function (v) {
          give[r] = v;
        }));
      });
      grid.appendChild(giveCol);

      var recvCol = el("div", "trade-col");
      recvCol.appendChild(el("h3", null, "You receive"));
      RES.forEach(function (r) {
        recvCol.appendChild(UI._miniStep(r, "", 99, function (v) {
          receive[r] = v;
        }, true));
      });
      grid.appendChild(recvCol);
      wrap.appendChild(grid);

      // target
      var tsel = el("div", "target-select");
      tsel.appendChild(el("strong", null, "Offer to: "));
      var sel = el("select");
      var anyO = el("option", null, "Anyone");
      anyO.value = "";
      sel.appendChild(anyO);
      (state.players || []).forEach(function (p) {
        if (p.id === state.yourId) return;
        var o = el("option", null, p.name + (p.connected === false ? " (offline)" : ""));
        o.value = p.id;
        sel.appendChild(o);
      });
      tsel.appendChild(sel);
      wrap.appendChild(tsel);

      var go = el("button", "btn btn-primary", "Propose Trade");
      go.style.marginTop = "12px";
      go.addEventListener("click", function () {
        var g = {},
          rc = {};
        RES.forEach(function (r) {
          if (give[r] > 0) g[r] = give[r];
          if (receive[r] > 0) rc[r] = receive[r];
        });
        if (!Object.keys(g).length && !Object.keys(rc).length) {
          UI.toast("Add at least one resource to the offer.");
          return;
        }
        UI.closeModal();
        if (UI.cb.onProposeTrade) UI.cb.onProposeTrade(g, rc, sel.value || null);
      });
      wrap.appendChild(go);
      return wrap;
    },

    _miniStep: function (r, hint, max, onChange, unbounded) {
      var row = el("div", "give-row");
      var dot = el("span", "bank-dot");
      dot.style.background = RES_COLOR[r];
      row.appendChild(dot);
      row.appendChild(el("span", null, RES_LABEL[r]));
      var minus = el("button", "btn btn-sm", "−");
      var cnt = el("span", "pcount", "0");
      var plus = el("button", "btn btn-sm", "+");
      var v = 0;
      function set(x) {
        v = Math.max(0, unbounded ? x : Math.min(max, x));
        cnt.textContent = String(v);
        minus.disabled = v <= 0;
        plus.disabled = !unbounded && v >= max;
        onChange(v);
      }
      minus.addEventListener("click", function () {
        set(v - 1);
      });
      plus.addEventListener("click", function () {
        set(v + 1);
      });
      row.appendChild(minus);
      row.appendChild(cnt);
      row.appendChild(plus);
      if (hint) {
        var h = el("span", "have", hint);
        h.style.marginLeft = "6px";
        row.appendChild(h);
      }
      set(0);
      return row;
    },

    _tradeIncomingBody: function (state, legal) {
      var wrap = el("div");
      var t = state.trade;
      if (!t) {
        wrap.appendChild(el("p", "sub", "No active trade offer."));
        return wrap;
      }
      var iAmProposer = t.from === state.yourId;
      var box = el("div", "incoming-trade");
      var who = iAmProposer ? "You" : this.nameOf(state, t.from);
      var toWhom = t.to ? this.nameOf(state, t.to) : "anyone";
      box.appendChild(el("p", "sub", who + " → " + toWhom));

      var line = el("div", "bundle");
      // From the responder's perspective: the proposer GIVES `give` and wants `receive`.
      line.appendChild(el("span", null, (iAmProposer ? "You give:" : who + " gives:")));
      line.appendChild(this._bundleChips(t.give));
      line.appendChild(el("span", "arrow", "⇄"));
      line.appendChild(el("span", null, (iAmProposer ? "You want:" : who + " wants:")));
      line.appendChild(this._bundleChips(t.receive));
      box.appendChild(line);
      wrap.appendChild(box);

      if (iAmProposer) {
        var cancel = el("button", "btn btn-danger", "Cancel Offer");
        cancel.addEventListener("click", function () {
          UI.closeModal();
          if (UI.cb.onCancelTrade) UI.cb.onCancelTrade();
        });
        wrap.appendChild(cancel);
      } else {
        var accept = el("button", "btn btn-primary", "Accept Trade");
        accept.disabled = !legal.tradeRespond;
        if (!legal.tradeRespond) {
          wrap.appendChild(el("p", "sub", "You don't have the resources to accept this trade."));
        }
        accept.addEventListener("click", function () {
          UI.closeModal();
          if (UI.cb.onAcceptTrade) UI.cb.onAcceptTrade();
        });
        wrap.appendChild(accept);
      }
      return wrap;
    },

    _bundleChips: function (bundle) {
      var b = el("span", "bundle");
      var any = false;
      RES.forEach(function (r) {
        var n = num(bundle && bundle[r]);
        if (n <= 0) return;
        any = true;
        var chip = el("span", "chip");
        var dot = el("span", "bank-dot");
        dot.style.background = RES_COLOR[r];
        chip.appendChild(dot);
        chip.appendChild(document.createTextNode(n + " " + RES_LABEL[r]));
        b.appendChild(chip);
      });
      if (!any) b.appendChild(el("span", "chip", "nothing"));
      return b;
    },

    _cancelBtn: function (label) {
      var b = el("button", "btn", label || "Cancel");
      b.addEventListener("click", function () {
        UI.closeModal();
      });
      return b;
    },

    /* ---- win modal ---- */
    _renderWin: function (state) {
      if (this._openModalKind === "win") return;
      var modal = el("div", "modal");
      modal.appendChild(el("div", "win-crown", "👑"));
      var winName = state.winner ? this.nameOf(state, state.winner) : "Nobody";
      modal.appendChild(el("h2", null, winName + " wins HEXARA!"));
      var standings = (state.players || []).slice().sort(function (a, b) {
        return num(b.vp) - num(a.vp);
      });
      var ol = el("ul", "standings");
      standings.forEach(function (p, i) {
        var li = el("li", p.id === state.winner ? "winner" : "");
        li.appendChild(el("span", "rank", "#" + (i + 1)));
        var sw = el("span", "swatch");
        sw.style.background = p.color || "#888";
        li.appendChild(sw);
        li.appendChild(el("span", "pname", p.name));
        li.appendChild(el("span", "fvp", num(p.vp) + " VP"));
        ol.appendChild(li);
      });
      modal.appendChild(ol);
      var actions = el("div", "modal-actions");
      var leave = el("button", "btn btn-primary", "Back to Menu");
      leave.addEventListener("click", function () {
        if (UI.cb.onLeave) UI.cb.onLeave();
      });
      actions.appendChild(leave);
      modal.appendChild(actions);
      this._openModal(modal, "win");
    },

    /* ===================== TOASTS ===================== */
    toast: function (msg, kind) {
      var area = $("toast-area");
      if (!area) return;
      var t = el("div", "toast" + (kind === "info" ? " info" : ""), msg);
      area.appendChild(t);
      setTimeout(function () {
        t.style.opacity = "0";
        t.style.transition = "opacity .4s";
        setTimeout(function () {
          if (t.parentNode) t.parentNode.removeChild(t);
        }, 400);
      }, kind === "info" ? 1800 : 3200);
    },

    /* ---- mode banner over the board ---- */
    setBanner: function (text, onCancel) {
      var b = $("mode-banner");
      if (!b) return;
      clear(b);
      if (!text) {
        b.hidden = true;
        return;
      }
      b.hidden = false;
      b.appendChild(el("span", null, text));
      if (onCancel) {
        var x = el("button", "cancel-x", "✕");
        x.title = "Cancel (Esc)";
        x.addEventListener("click", onCancel);
        b.appendChild(x);
      }
    },

    _me: function (state) {
      var me = null;
      (state.players || []).some(function (p) {
        if (p.id === state.yourId) {
          me = p;
          return true;
        }
        return false;
      });
      return me;
    },
  };

  window.UI = UI;
})();
