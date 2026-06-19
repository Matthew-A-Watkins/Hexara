/* ============================================================
   casino.js  ·  window.Casino
   The shared casino table: an animated 8-bit dealer, a felt table where
   every seated player's hand is visible, smooth card dealing, a chatty
   dealer, tipping, an in-casino End Turn + Auto-sell, and a cashier that
   swaps resources / dev cards / victory points for beans. Reads the
   viewer's private `casino` block from App.state; all wagers are
   server-authoritative. app.js calls refresh() on every state push.
   ============================================================ */
(function () {
  "use strict";

  var RES = ["wood", "brick", "sheep", "wheat", "ore"];
  var RES_LABEL = { wood: "Wood", brick: "Brick", sheep: "Sheep", wheat: "Wheat", ore: "Ore" };
  var DEV_ORDER = ["knight", "victory_point", "road_building", "year_of_plenty", "monopoly"];
  var DEV_LABEL = { knight: "Knight", victory_point: "Victory Pt", road_building: "Road Build", year_of_plenty: "Yr Plenty", monopoly: "Monopoly" };
  var SUIT = { S: "♠", H: "♥", D: "♦", C: "♣" };

  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }
  function clear(n) { while (n && n.firstChild) n.removeChild(n.firstChild); }
  function nowMs() { return (window.performance && performance.now) ? performance.now() : 0; }

  var Casino = {
    bet: 1,
    tip: 1,
    vpAmt: 1,
    sel: { wood: 0, brick: 0, sheep: 0, wheat: 0, ore: 0 },
    devSel: { knight: 0, victory_point: 0, road_building: 0, year_of_plenty: 0, monopoly: 0 },
    showCount: false,
    showCashier: false,
    _mood: "happy",
    _raf: null,
    _cardKeys: {},
    _lastState: null,
    _lastBjState: null,

    open: function () {
      var self = this;
      try { this._sprite = localStorage.getItem("hexara.dealer") || "m"; } catch (e) { this._sprite = "m"; }
      var modal = el("div", "modal modal-wide casino-modal");
      modal.appendChild(el("h2", null, "🎰 Casino"));
      this._balance = el("div", "casino-balance");
      modal.appendChild(this._balance);

      // Felt table with the persistent dealer canvas + speech bubble.
      var felt = el("div", "bj-felt");
      var dealerWrap = el("div", "dealer-wrap");
      var dcol = el("div", "dealer-col");
      this._canvas = el("canvas", "dealer-canvas");
      this._canvas.width = 132; this._canvas.height = 132;
      dcol.appendChild(this._canvas);
      this._dealerTag = el("div", "dealer-name", this._dealerName());
      dcol.appendChild(this._dealerTag);
      var swap = el("button", "btn btn-sm dealer-swap", "↺ Switch dealer");
      swap.title = "Swap who's working the table";
      swap.addEventListener("click", function () {
        self._sprite = self._sprite === "f" ? "m" : "f";
        try { localStorage.setItem("hexara.dealer", self._sprite); } catch (e) {}
        if (self._dealerTag) self._dealerTag.textContent = self._dealerName();
        self._chatSig = null;  // re-render chat under the new dealer's name
        self.refresh();
      });
      dcol.appendChild(swap);
      dealerWrap.appendChild(dcol);
      this._bubble = el("div", "dealer-bubble");
      dealerWrap.appendChild(this._bubble);
      felt.appendChild(dealerWrap);
      this._ctx = this._canvas.getContext("2d");
      this._ctx.imageSmoothingEnabled = false;

      this._dealerHand = el("div", "dealer-hand");
      felt.appendChild(this._dealerHand);
      this._seatsBox = el("div", "bj-seats");
      felt.appendChild(this._seatsBox);
      modal.appendChild(felt);

      this._controls = el("div", "bj-area");
      modal.appendChild(this._controls);

      // Table talk: the input is built once and never re-rendered, so typing
      // is never interrupted by state pushes — only the message log updates.
      var chatSec = el("div", "bj-chat");
      chatSec.appendChild(el("div", "bj-label", "💬 Table talk — chat with the dealer"));
      this._chatLog = el("div", "chat-log");
      chatSec.appendChild(this._chatLog);
      var crow = el("div", "chat-row");
      this._chatInput = el("input", "chat-input");
      this._chatInput.type = "text";
      this._chatInput.maxLength = 200;
      this._chatInput.placeholder = "Say something to the dealer…";
      var sendB = el("button", "btn casino-btn chat-send", "Send");
      function sendChat() {
        var v = self._chatInput.value.trim();
        if (!v) return;
        self._chatInput.value = "";
        if (window.Sound) Sound.play("card");
        Net.sendAction({ type: "bj_chat", text: v, dealer: self._sprite });
      }
      sendB.addEventListener("click", sendChat);
      this._chatInput.addEventListener("keydown", function (e) { if (e.key === "Enter") sendChat(); });
      crow.appendChild(this._chatInput);
      crow.appendChild(sendB);
      chatSec.appendChild(crow);
      modal.appendChild(chatSec);

      var actions = el("div", "modal-actions");
      var close = el("button", "btn", "Close");
      close.addEventListener("click", function () { UI.closeModal(); });
      actions.appendChild(close);
      modal.appendChild(actions);

      UI._openModal(modal, "casino");
      this._startAnim();
      this.refresh();
    },

    close: function () {
      if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
    },

    _me: function () {
      var s = (window.App && App.state) || {};
      return (s.players || []).filter(function (p) { return p.id === s.yourId; })[0] || null;
    },

    /* ===================== dealer animation ===================== */
    _startAnim: function () {
      var self = this;
      function frame() {
        if (UI._openModalKind !== "casino" || !self._ctx) { self._raf = null; return; }
        self._drawDealer(self._ctx, self._canvas.width, self._canvas.height, self._mood, nowMs());
        self._raf = requestAnimationFrame(frame);
      }
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = requestAnimationFrame(frame);
    },

    _drawDealer: function (ctx, W, H, mood, t) {
      ctx.clearRect(0, 0, W, H);
      var G = 18, px = Math.floor(Math.min(W, H) / G);
      var ox = Math.floor((W - px * G) / 2), oy = Math.floor((H - px * G) / 2);
      function R(x, y, w, h, col) { ctx.fillStyle = col; ctx.fillRect(ox + x * px, oy + y * px, w * px, h * px); }
      var blink = (t % 3000) < 110;
      if (this._sprite === "f") this._galPixels(R, mood, blink, t);
      else this._guyPixels(R, mood, blink, t);
      // floating hearts when thankful (both dealers)
      if (mood === "thankful") {
        var hy = 4 - ((t / 320) % 7);
        R(2, Math.round(hy), 1, 1, "#e8527a"); R(15, Math.round(hy + 1.5) % 7, 1, 1, "#e8527a");
      }
    },

    // Marv: visor, bowtie, honest mustache-free service.
    _guyPixels: function (R, mood, blink, t) {
      // head + ears
      R(4, 4, 10, 10, "#f0c090");
      R(4, 4, 10, 1, "#d8a874");
      R(3, 7, 1, 3, "#f0c090"); R(14, 7, 1, 3, "#f0c090");
      // hair + green dealer visor
      R(4, 3, 10, 1, "#3a2a18");
      R(3, 2, 12, 1, "#17593a");
      R(3, 3, 12, 1, "#1f6b43");
      R(2, 4, 14, 1, "#2e9c63");
      this._drawEyes(R, mood, blink);
      this._drawMouth(R, mood);
      if (mood === "thankful" || mood === "excited") { R(4, 10, 1, 1, "#f0a0a0"); R(13, 10, 1, 1, "#f0a0a0"); }
      // neck, collar, bowtie
      R(7, 14, 4, 1, "#e8b06a");
      R(3, 15, 12, 3, "#f3e6c8");
      R(7, 15, 1, 2, "#c0392b"); R(10, 15, 1, 2, "#c0392b"); R(8, 15, 2, 1, "#c0392b"); R(8, 16, 2, 1, "#a52f22");
      // dealing arm waggle
      if (mood === "dealing") {
        var arm = Math.round(Math.sin(t / 110));
        R(14, 12 + arm, 3, 1, "#f3e6c8"); R(16, 12 + arm, 1, 1, "#f0c090");
      }
    },

    // Bella: flowing hair, lashes, red lips, earrings, a flower and a wine dress.
    _galPixels: function (R, mood, blink, t) {
      var HAIR = "#e8c25a", SKIN = "#f5cfa6", GOLD = "#e3b23c", PINK = "#e8527a";
      // flowing hair: crown, long sides, tips past the shoulders
      R(3, 2, 12, 2, HAIR);
      R(2, 3, 3, 10, HAIR); R(13, 3, 3, 10, HAIR);
      R(2, 13, 2, 2, HAIR); R(14, 13, 2, 2, HAIR);
      // face + side-swept bangs
      R(5, 4, 8, 10, SKIN);
      R(5, 3, 8, 1, HAIR); R(5, 4, 2, 1, HAIR); R(11, 4, 2, 1, HAIR);
      // flower tucked in the hair
      R(13, 2, 1, 1, PINK); R(12, 3, 1, 1, PINK); R(14, 3, 1, 1, PINK); R(13, 4, 1, 1, PINK);
      R(13, 3, 1, 1, GOLD);
      // lashes above open eyes
      if (!blink) { R(6, 5, 2, 1, "#1a1a1a"); R(10, 5, 2, 1, "#1a1a1a"); }
      this._drawEyes(R, mood, blink);
      // blush + red lips
      R(5, 9, 1, 1, "#f0a0a0"); R(12, 9, 1, 1, "#f0a0a0");
      this._drawMouth(R, mood, "#d12a45");
      // earrings at the hairline
      R(4, 11, 1, 1, GOLD); R(13, 11, 1, 1, GOLD);
      // neck + wine dress with a neckline and pendant
      R(7, 14, 4, 1, "#ecbf92");
      R(3, 15, 12, 3, "#8e2440");
      R(8, 15, 2, 1, "#ecbf92");
      R(9, 16, 1, 1, GOLD);
      // dealing arm waggle (dress sleeve)
      if (mood === "dealing") {
        var arm = Math.round(Math.sin(t / 110));
        R(14, 12 + arm, 3, 1, "#8e2440"); R(16, 12 + arm, 1, 1, SKIN);
      }
    },
    _drawEyes: function (R, mood, blink) {
      if (blink) { R(6, 7, 2, 1, "#3a2a18"); R(10, 7, 2, 1, "#3a2a18"); return; }
      if (mood === "thankful") {
        R(6, 7, 1, 1, "#1a1a1a"); R(7, 6, 1, 1, "#1a1a1a"); R(8, 7, 1, 1, "#1a1a1a");
        R(10, 7, 1, 1, "#1a1a1a"); R(11, 6, 1, 1, "#1a1a1a"); R(12, 7, 1, 1, "#1a1a1a"); return;
      }
      if (mood === "excited") { R(6, 6, 2, 2, "#1a1a1a"); R(10, 6, 2, 2, "#1a1a1a"); R(6, 6, 1, 1, "#fff"); R(10, 6, 1, 1, "#fff"); return; }
      if (mood === "sad") { R(6, 6, 2, 1, "#d8a874"); R(10, 6, 2, 1, "#d8a874"); R(6, 7, 2, 1, "#1a1a1a"); R(10, 7, 2, 1, "#1a1a1a"); return; }
      R(6, 6, 1, 2, "#1a1a1a"); R(11, 6, 1, 2, "#1a1a1a");
    },
    _drawMouth: function (R, mood, col) {
      col = col || "#7d231a";
      if (mood === "excited") { R(7, 10, 4, 2, col); R(8, 11, 2, 1, "#fff"); return; }
      if (mood === "happy" || mood === "thankful" || mood === "dealing") { R(6, 11, 1, 1, col); R(7, 12, 4, 1, col); R(11, 11, 1, 1, col); return; }
      if (mood === "sad") { R(6, 12, 1, 1, col); R(7, 11, 4, 1, col); R(11, 12, 1, 1, col); return; }
      R(7, 11, 4, 1, col);
    },

    /* ===================== refresh ===================== */
    refresh: function () {
      if (UI._openModalKind !== "casino") return;
      var me = this._me();
      var c = me && me.casino;
      if (!c) {
        this._mood = "happy";
        if (this._bubble) this._bubble.textContent = "The casino opens once the game is underway.";
        clear(this._controls); clear(this._dealerHand); clear(this._seatsBox);
        return;
      }
      this._mood = c.mood || "happy";
      this._bubble.textContent = c.message || "Place a bet!";

      // balances
      clear(this._balance);
      this._balance.appendChild(el("span", "bean-pill", "🫘 " + c.beans + " beans"));
      this._balance.appendChild(el("span", "vp-pill", "★ " + c.boughtVp + " bought VP"));
      this._balance.appendChild(el("span", "tip-pill", "🎩 " + c.tips + " tipped"));
      var decks = (c.shoeLeft / 52).toFixed(1);
      this._balance.appendChild(el("span", "rate-note", "Shoe " + c.shoeLeft + " (~" + decks + " decks)"));

      // new round -> animate every card; otherwise only new ones
      var t = c.table;
      var st = t ? t.state : "idle";
      if (st === "player" && this._lastState !== "player") this._cardKeys = {};
      this._lastState = st;

      this._renderDealerHand(t);
      this._renderSeats(c);
      // Don't rebuild the controls (and lose focus) while you're typing a bet.
      var ae = document.activeElement;
      var typing = ae && ae.classList && ae.classList.contains("stepper-input") && this._controls.contains(ae);
      if (!typing) this._renderControls(me, c);
      this._renderChat(c);
      this._bjSounds(c);
    },

    _dealerName: function () {
      return this._sprite === "f" ? "Bella" : "Marv";
    },

    // Each dealer message remembers which dealer said it, so a table mixing
    // Marv and Bella shows the right name per line (not just your current pick).
    _msgDealerName: function (m) {
      if (m.dealer === "f") return "Bella";
      if (m.dealer === "m") return "Marv";
      return this._dealerName();
    },

    _renderChat: function (c) {
      var log = this._chatLog;
      if (!log) return;
      var msgs = c.chat || [];
      // Include each id so an async (LLM) rewrite of an existing line still
      // triggers a rebuild even though the message count is unchanged.
      var sig = msgs.map(function (m) { return (m.id || 0) + ":" + m.text; }).join("|");
      if (this._chatSig === sig) return;  // cheap diff: only rebuild on change
      this._chatSig = sig;
      clear(log);
      if (!msgs.length) {
        log.appendChild(el("div", "chat-line dealer",
          this._dealerName() + ": Talk to me, friend — ask about rates, the count, or what to do with that hand."));
      }
      var self = this;
      msgs.forEach(function (m) {
        var dealer = m.from === "dealer";
        var line = el("div", "chat-line" + (dealer ? " dealer" : ""));
        line.appendChild(el("strong", null, (dealer ? self._msgDealerName(m) : m.name) + ": "));
        line.appendChild(document.createTextNode(m.text));
        log.appendChild(line);
      });
      log.scrollTop = log.scrollHeight;
    },

    _isNew: function (key) {
      if (this._cardKeys[key]) return false;
      this._cardKeys[key] = 1;
      return true;
    },

    _card: function (card, key) {
      var box;
      if (card === "back") {
        box = el("div", "bjcard back");
      } else {
        var rank = card.slice(0, -1), suit = card.slice(-1);
        var red = suit === "H" || suit === "D";
        box = el("div", "bjcard" + (red ? " red" : ""));
        box.appendChild(el("span", "bjcard-rank", rank));
        box.appendChild(el("span", "bjcard-suit", SUIT[suit] || suit));
      }
      if (key && this._isNew(key)) box.classList.add("deal-in");
      return box;
    },

    _renderDealerHand: function (t) {
      clear(this._dealerHand);
      var lbl = el("div", "bj-label", "Dealer" + (t && t.dealerValue != null ? " — " + t.dealerValue : ""));
      this._dealerHand.appendChild(lbl);
      var row = el("div", "bj-cards");
      var self = this;
      ((t && t.dealer) || []).forEach(function (card, i) { row.appendChild(self._card(card, "D:" + i)); });
      if (!t || !t.dealer || !t.dealer.length) row.appendChild(el("span", "bj-empty", "—"));
      this._dealerHand.appendChild(row);
    },

    _renderSeats: function (c) {
      clear(this._seatsBox);
      var self = this;
      var seats = c.seats || [];
      if (!seats.length) {
        this._seatsBox.appendChild(el("div", "bj-empty-seats", "No one's playing yet — be the first to bet!"));
        return;
      }
      seats.forEach(function (s) {
        var seat = el("div", "bj-seat" + (s.you ? " you" : "") + (s.state === "player" ? " active" : ""));
        var head = el("div", "seat-head");
        var sw = el("span", "swatch"); sw.style.background = s.color || "#888";
        head.appendChild(sw);
        head.appendChild(el("span", "seat-name", s.name + (s.you ? " (you)" : "")));
        head.appendChild(el("span", "seat-bet", "🫘 " + s.bet));
        seat.appendChild(head);
        s.hands.forEach(function (h, hi) {
          var hand = el("div", "seat-hand");
          var cards = el("div", "bj-cards");
          h.cards.forEach(function (card, ci) { cards.appendChild(self._card(card, "S:" + s.id + ":" + hi + ":" + ci)); });
          hand.appendChild(cards);
          var meta = el("span", "seat-val", h.value + (h.bust ? " BUST" : ""));
          if (h.result) {
            meta.textContent += " · " + h.result;
            if (h.result === "win" || h.result === "blackjack") meta.classList.add("win");
            if (h.result === "lose" || h.result === "bust") meta.classList.add("lose");
          }
          hand.appendChild(meta);
          seat.appendChild(hand);
        });
        if (s.net != null) seat.appendChild(el("div", "seat-net" + (s.net >= 0 ? " win" : " lose"), (s.net >= 0 ? "+" : "") + s.net + " beans"));
        self._seatsBox.appendChild(seat);
      });
    },

    /* ===================== controls ===================== */
    _renderControls: function (me, c) {
      var self = this;
      clear(this._controls);
      var t = c.table;
      var playing = t && t.state === "player";

      var main = el("div", "bj-controls");
      if (playing) {
        main.appendChild(this._cbtn("Hit", "hit", t.canHit, "bj_hit", "card"));
        main.appendChild(this._cbtn("Stand", "stand", t.canStand, "bj_stand", null));
        main.appendChild(this._cbtn("Double", "double", t.canDouble, "bj_double", "card"));
        main.appendChild(this._cbtn("Split", "split", t.canSplit, "bj_split", "card"));
        main.appendChild(this._cbtn("Surrender", "surrender", t.canSurrender, "bj_surrender", null));
      } else {
        var betWrap = el("div", "bet-wrap");
        betWrap.appendChild(el("span", "bet-label", "Bet"));
        var betStep = this._stepper("bet", Math.max(c.minBet, this.bet), c.minBet, c.beans);
        betWrap.appendChild(betStep.node);
        var maxBtn = el("button", "btn btn-sm", "Max");
        maxBtn.title = "Bet everything";
        maxBtn.addEventListener("click", function () { betStep.setMax(); });
        betWrap.appendChild(maxBtn);
        main.appendChild(betWrap);
        var deal = this._cbtn("Deal", "deal", c.canBet, "bj_bet", "chip", function () { return { amount: Math.max(c.minBet, self.bet) }; });
        main.appendChild(deal);
        if (c.beans < c.minBet) main.appendChild(el("span", "bj-warn", "Need beans — use the cashier below."));
      }
      this._controls.appendChild(main);

      // tip + end turn + auto-sell
      var extra = el("div", "bj-extra");
      var tipWrap = el("div", "tip-wrap");
      tipWrap.appendChild(el("span", "bet-label", "Tip"));
      tipWrap.appendChild(this._stepper("tip", this.tip, 1, c.beans).node);
      var tipBtn = this._cbtn("🎩 Tip dealer", "tip", c.beans >= this.tip && this.tip >= 1, "bj_tip", "cash", function () { return { amount: self.tip }; });
      tipWrap.appendChild(tipBtn);
      extra.appendChild(tipWrap);

      var legal = (window.App && App.legal) || {};
      var endBtn = el("button", "btn casino-btn end-turn", "End Turn");
      endBtn.disabled = !legal.canEndTurn;
      endBtn.title = legal.canEndTurn ? "End your turn without leaving the table" : "You can end your turn here once it's your turn and you've rolled";
      endBtn.addEventListener("click", function () {
        if (window.Sound) Sound.play("chip");
        Net.sendAction({ type: "end_turn" });
      });
      extra.appendChild(endBtn);

      var autoWrap = el("label", "opt-toggle casino-toggle");
      autoWrap.title = "Automatically buy development cards from your resources on your turn (cash them in below for beans)";
      var cb = el("input"); cb.type = "checkbox"; cb.checked = !!(window.App && App.autoSell);
      cb.addEventListener("change", function () { if (window.App) App.setAutoSell(this.checked); });
      autoWrap.appendChild(cb);
      autoWrap.appendChild(document.createTextNode(" Auto-buy dev"));
      extra.appendChild(autoWrap);

      var countBtn = el("button", "btn btn-sm", this.showCount ? "Hide count" : "Counting aid");
      countBtn.addEventListener("click", function () { self.showCount = !self.showCount; self.refresh(); });
      extra.appendChild(countBtn);
      this._controls.appendChild(extra);

      if (this.showCount) this._controls.appendChild(this._seenCards(c));

      // cashier (collapsible)
      var cashHead = el("button", "cashier-head", (this.showCashier ? "▼" : "▶") + " Cashier — swap resources, dev cards & VP for beans");
      cashHead.addEventListener("click", function () { self.showCashier = !self.showCashier; self.refresh(); });
      this._controls.appendChild(cashHead);
      if (this.showCashier) this._controls.appendChild(this._cashier(me, c));
    },

    _cbtn: function (label, kind, enabled, action, sound, payload) {
      var b = el("button", "btn casino-btn bj-" + kind, label);
      b.disabled = !enabled;
      if (enabled) b.addEventListener("click", function () {
        if (sound && window.Sound) Sound.play(sound);
        Net.sendAction(Object.assign({ type: action }, payload ? payload() : {}));
      });
      return b;
    },

    // A stepper with a typeable number input + / − (and an optional Max via
    // the returned setMax). You can drag the bet anywhere by just typing it.
    _stepper: function (field, value, min, max) {
      var self = this;
      var v = Math.max(min, Math.min(value, max == null ? value : max));
      this[field] = v;
      var node = el("span", "mini-stepper");
      var minus = el("button", "btn btn-sm", "−");
      var input = el("input", "stepper-input");
      input.type = "number"; input.min = min; if (max != null) input.max = max;
      input.value = String(v);
      var plus = el("button", "btn btn-sm", "+");
      function set(x, fromInput) {
        if (isNaN(x)) x = min;
        v = Math.max(min, max == null ? x : Math.min(x, max));
        self[field] = v;
        if (!fromInput) input.value = String(v);
      }
      minus.addEventListener("click", function () { set(v - 1); });
      plus.addEventListener("click", function () { set(v + 1); });
      input.addEventListener("input", function () { set(parseInt(this.value, 10), true); });
      input.addEventListener("change", function () { set(parseInt(this.value, 10)); });
      node.appendChild(minus); node.appendChild(input); node.appendChild(plus);
      return { node: node, setMax: function () { if (max != null) set(max); } };
    },

    _seenCards: function (c) {
      var box = el("div", "bj-seen");
      var seen = c.seen || [];
      box.appendChild(el("div", "bj-label", "Cards seen this shoe (" + seen.length + ") — shared by the whole table"));

      // The actual count, computed Hi-Lo: 2-6 = +1, 7-9 = 0, 10/J/Q/K/A = -1.
      var run = 0, tally = {};
      seen.forEach(function (card) {
        var r = card.slice(0, -1);
        tally[r] = (tally[r] || 0) + 1;
        if (r === "A" || r === "K" || r === "Q" || r === "J" || r === "10") run -= 1;
        else if (parseInt(r, 10) >= 2 && parseInt(r, 10) <= 6) run += 1;
      });
      var decksLeft = (c.shoeLeft || 0) / 52;
      var bias = c.countBias || 0;  // gamble mode: tips warm the count
      var runB = run + bias;
      var trueC = runB / Math.max(decksLeft, 0.5);
      function sgn(n) { return (n >= 0 ? "+" : "") + n; }
      function sgnf(n) { return (n >= 0 ? "+" : "") + n.toFixed(2); }
      box.appendChild(el("div", "count-summary",
        "Running count: " + (bias ? sgnf(runB) + " (" + sgn(run) + " cards " + sgnf(bias) + " tip)" : sgn(run)) +
        "  ·  True count: " + (trueC >= 0 ? "+" : "") + trueC.toFixed(1) +
        "  ·  " + decksLeft.toFixed(1) + " decks left"));

      // Per-rank tally of everything that's hit the felt.
      var tr = el("div", "rank-tally");
      ["A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2"].forEach(function (r) {
        tr.appendChild(el("span", "seen-chip", r + " ×" + (tally[r] || 0)));
      });
      box.appendChild(tr);

      var row = el("div", "bj-seen-row");
      seen.forEach(function (card) {
        var rank = card.slice(0, -1), suit = card.slice(-1);
        var red = suit === "H" || suit === "D";
        row.appendChild(el("span", "seen-chip" + (red ? " red" : ""), rank + (SUIT[suit] || "")));
      });
      box.appendChild(row);
      return box;
    },

    /* ===================== cashier ===================== */
    _cashier: function (me, c) {
      var self = this;
      var have = me.resources || {};
      var wrap = el("div", "cashier");

      // resources <-> beans
      wrap.appendChild(el("h4", null, "Buy resources · " + c.beansPerResource + " beans each"));
      var resRow = el("div", "res-click-row");
      RES.forEach(function (r) {
        resRow.appendChild(UI._resClickCard(r, self.sel[r], 0, true, {
          have: (have[r] || 0),
          onChange: function (v) { self.sel[r] = v; upd(); },
        }));
      });
      wrap.appendChild(resRow);
      var resBtns = el("div", "trade-btns");
      var buyBtn = el("button", "btn casino-btn", "Buy with beans");
      resBtns.appendChild(buyBtn);
      wrap.appendChild(resBtns);

      // dev cards -> beans (only when enabled in Gamble mode; VP cards excluded —
      // they're worth a real point and can't be cashed).
      var owned = DEV_ORDER.filter(function (d) {
        return d !== "victory_point" && (c.dev[d] || 0) + (c.devNew[d] || 0) > 0;
      });
      if (c.canCashDev && owned.length) {
        wrap.appendChild(el("h4", null, "Development cards · " + c.beansPerDev + " beans each"));
        var devRow = el("div", "dev-cash-row");
        owned.forEach(function (d) {
          var max = (c.dev[d] || 0) + (c.devNew[d] || 0);
          var cell = el("div", "dev-cash");
          cell.appendChild(el("div", "dev-cash-name", DEV_LABEL[d] + " ×" + max));
          cell.appendChild(self._devStepper(d, max).node);
          devRow.appendChild(cell);
        });
        wrap.appendChild(devRow);
        var devBtn = el("button", "btn casino-btn", "Cash in dev cards");
        devBtn.addEventListener("click", function () {
          var bundle = {}, any = false;
          DEV_ORDER.forEach(function (d) { if (self.devSel[d] > 0) { bundle[d] = self.devSel[d]; any = true; } });
          if (!any) { UI.toast("Pick development cards to cash in."); return; }
          if (window.Sound) Sound.play("cash");
          Net.sendAction({ type: "convert_dev_to_beans", cards: bundle });
          self.devSel = { knight: 0, victory_point: 0, road_building: 0, year_of_plenty: 0, monopoly: 0 };
        });
        wrap.appendChild(devBtn);
      }

      // VP <-> beans
      wrap.appendChild(el("h4", null, "Victory points · " + c.beansPerVp + " beans each"));
      var vpRow = el("div", "vp-exchange");
      vpRow.appendChild(this._stepper("vpAmt", this.vpAmt, 1, null).node);
      var buyVp = el("button", "btn casino-btn", "Buy VP");
      buyVp.disabled = c.beans < this.vpAmt * c.beansPerVp;
      buyVp.addEventListener("click", function () { if (window.Sound) Sound.play("chip"); Net.sendAction({ type: "buy_vp", amount: self.vpAmt }); });
      var sellVp = el("button", "btn casino-btn", "Sell VP");
      sellVp.disabled = c.boughtVp < this.vpAmt;
      sellVp.addEventListener("click", function () { if (window.Sound) Sound.play("cash"); Net.sendAction({ type: "sell_vp", amount: self.vpAmt }); });
      vpRow.appendChild(buyVp); vpRow.appendChild(sellVp);
      wrap.appendChild(vpRow);
      var vpHint = el("span", "sub", this.vpAmt + " VP = " + this.vpAmt * c.beansPerVp + " beans");
      wrap.appendChild(vpHint);

      function total() { return RES.reduce(function (a, r) { return a + self.sel[r]; }, 0); }
      function upd() {
        var n = total();
        buyBtn.disabled = !(n > 0 && c.beans >= n * c.beansPerResource);
        buyBtn.textContent = n > 0 ? ("Buy " + n + " for " + n * c.beansPerResource + " beans") : "Buy with beans";
      }
      function bundle() { var o = {}, any = false; RES.forEach(function (r) { if (self.sel[r] > 0) { o[r] = self.sel[r]; any = true; } }); return any ? o : null; }
      buyBtn.addEventListener("click", function () { var b = bundle(); if (!b) return; if (window.Sound) Sound.play("chip"); Net.sendAction({ type: "convert_to_resources", resources: b }); self.sel = { wood: 0, brick: 0, sheep: 0, wheat: 0, ore: 0 }; });
      upd();
      return wrap;
    },

    _devStepper: function (d, max) {
      var self = this;
      var v = Math.min(this.devSel[d] || 0, max);
      this.devSel[d] = v;
      var node = el("span", "mini-stepper");
      var minus = el("button", "btn btn-sm", "−");
      var cnt = el("span", "pcount", String(v));
      var plus = el("button", "btn btn-sm", "+");
      function set(x) { v = Math.max(0, Math.min(x, max)); cnt.textContent = String(v); self.devSel[d] = v; }
      minus.addEventListener("click", function () { set(v - 1); });
      plus.addEventListener("click", function () { set(v + 1); });
      node.appendChild(minus); node.appendChild(cnt); node.appendChild(plus);
      return { node: node };
    },

    _bjSounds: function (c) {
      var st = c.table ? c.table.state : null;
      if (st === "done" && this._lastBjState !== "done") {
        var net = c.table.net || 0;
        if (window.Sound) Sound.play(net > 0 ? "bjwin" : (net < 0 ? "bust" : "card"));
      } else if (this._lastBeans != null && c.beans > this._lastBeans && window.Sound) {
        // Beans climbed without a hand settling — the dealer slipped you a tip.
        Sound.play("cash");
      }
      this._lastBjState = st;
      this._lastBeans = c.beans;
    },
  };

  window.Casino = Casino;
})();
