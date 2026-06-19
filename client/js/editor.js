/* ============================================================
   editor.js  ·  window.MapEditor
   In-lobby custom map editor. A visual canvas (click hexes to paint
   terrain / numbers / robber, carve the island shape) kept in sync with a
   live JSON text definition. Produces a MapSpec consumed by the lobby
   (UI.applyCustomMap). Geometry is intentionally simple here — hex centres
   from axial (q,r); ports are auto-placed by the server at game start.
   ============================================================ */
(function () {
  "use strict";

  var TERRAINS = ["forest", "hills", "pasture", "fields", "mountains", "desert", "gold", "beans"];
  var TERRAIN_LABEL = {
    forest: "Forest", hills: "Hills", pasture: "Pasture",
    fields: "Fields", mountains: "Mountains", desert: "Desert",
    gold: "Gold", beans: "Beans",
  };
  var TERRAIN_COLOR = {
    forest: "#2e7d32", hills: "#c1572e", pasture: "#8bc34a",
    fields: "#e3b23c", mountains: "#6b7785", desert: "#e6d3a8",
    gold: "#e0b81e", beans: "#b07a2a",
  };
  var TERRAIN_RES = {
    forest: "wood", hills: "brick", pasture: "sheep",
    fields: "wheat", mountains: "ore", desert: null, gold: null, beans: null,
  };
  var NUMBERS = [2, 3, 4, 5, 6, 8, 9, 10, 11, 12];
  var PIPS = { 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1 };
  var NUM_CYCLE = [6, 8, 5, 9, 4, 10, 3, 11, 2, 12];
  var HEX_SIZE = 60;
  var MAX_RADIUS = 5;
  // Axial neighbour for each edge dir 0-5 (0 = the east-facing edge, then
  // clockwise). Must match the server's corner ordering in geometry.py.
  var NEIGH = [[1, 0], [0, 1], [-1, 1], [-1, 0], [0, -1], [1, -1]];
  var PORT_TYPES = ["3:1", "wood", "brick", "sheep", "wheat", "ore"];
  var PORT_COLOR = { "3:1": "#7a4a23", wood: "#2e7d32", brick: "#c1572e", sheep: "#8bc34a", wheat: "#e3b23c", ore: "#6b7785" };
  var PORT_LABEL = { "3:1": "3:1 Port", wood: "Wood 2:1", brick: "Brick 2:1", sheep: "Sheep 2:1", wheat: "Wheat 2:1", ore: "Ore 2:1" };
  var PORT_SHORT = { "3:1": "3:1", wood: "Wd", brick: "Br", sheep: "Sh", wheat: "Wt", ore: "Or" };

  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }
  function hexCenter(q, r) {
    return { cx: HEX_SIZE * Math.sqrt(3) * (q + r / 2), cy: HEX_SIZE * 1.5 * r };
  }
  function hexField(radius) {
    var out = [];
    for (var r = -radius; r <= radius; r++) {
      for (var q = -radius; q <= radius; q++) {
        if (Math.abs(q) <= radius && Math.abs(r) <= radius && Math.abs(q + r) <= radius) {
          out.push([q, r]);
        }
      }
    }
    return out;
  }
  function key(q, r) { return q + "," + r; }
  function key3(q, r, d) { return q + "," + r + "," + d; }
  // Midpoint of edge `d` (0-5) of a hex centred at c — corner i sits at 60i-30°.
  function edgeMid(c, d) {
    var a1 = (60 * d - 30) * Math.PI / 180, a2 = (60 * (d + 1) - 30) * Math.PI / 180;
    return { x: c.cx + (Math.cos(a1) + Math.cos(a2)) * HEX_SIZE / 2,
             y: c.cy + (Math.sin(a1) + Math.sin(a2)) * HEX_SIZE / 2 };
  }

  var MapEditor = {
    spec: null,        // working spec: {name, tiles:[{q,r,terrain,number}], robber:{q,r}, ports?}
    tiles: null,       // map "q,r" -> {q,r,terrain,number}
    robber: null,      // {q,r} or null
    portsMode: "auto", // "auto" | "none" | "custom"
    ports: null,       // map "q,r,dir" -> port type (only used in "custom" mode)
    name: "Custom Map",
    tool: { kind: "terrain", value: "forest" },
    onApply: null,
    canvas: null,
    ctx: null,
    _t: null,
    _hover: null,

    open: function (initial, onApply) {
      this.onApply = onApply;
      this._load(initial);
      this._buildModal();
      this._draw();
      this._syncText();
    },

    /* ---------- spec <-> internal state ---------- */
    _load: function (spec) {
      spec = spec || {};
      this.name = spec.name || "Custom Map";
      this.ports = {};
      if (spec.portsEdges && spec.portsEdges.length) {
        this.portsMode = "custom";
        var self = this;
        spec.portsEdges.forEach(function (p) {
          self.ports[key3(p.q, p.r, p.dir)] = p.type;
        });
      } else {
        this.portsMode = (spec.ports && spec.ports.length === 0) ? "none" : "auto";
      }
      var tiles = {};
      if (spec.tiles && spec.tiles.length) {
        spec.tiles.forEach(function (t) {
          tiles[key(t.q, t.r)] = {
            q: t.q, r: t.r, terrain: t.terrain,
            number: t.terrain === "desert" ? null : t.number,
          };
        });
      } else {
        var radius = spec.radius != null ? spec.radius : 2;
        radius = Math.max(1, Math.min(MAX_RADIUS, radius));
        var field = spec.axials && spec.axials.length
          ? spec.axials.map(function (a) { return [a[0], a[1]]; })
          : hexField(radius);
        field.forEach(function (a, i) { var t = MapEditor._defaultTile(a[0], a[1], i); tiles[key(a[0], a[1])] = t; });
      }
      this.tiles = tiles;
      this._prunePorts();
      // robber
      this.robber = null;
      if (spec.robber && spec.robber.q != null) this.robber = { q: spec.robber.q, r: spec.robber.r };
      if (!this.robber || !this.tiles[key(this.robber.q, this.robber.r)]) {
        this.robber = this._firstDesert() || this._anyTile();
      }
    },

    // Drop ports whose tile vanished or whose edge is no longer coastal.
    _prunePorts: function () {
      var self = this;
      Object.keys(this.ports || {}).forEach(function (k) {
        var p = k.split(",");
        var q = +p[0], r = +p[1], d = +p[2];
        if (!self.tiles[key(q, r)]) { delete self.ports[k]; return; }
        var nb = NEIGH[d];
        if (self.tiles[key(q + nb[0], r + nb[1])]) delete self.ports[k];
      });
    },

    _defaultTile: function (q, r, i) {
      if (q === 0 && r === 0) return { q: q, r: r, terrain: "desert", number: null };
      var res = ["forest", "fields", "pasture", "hills", "mountains"];
      return { q: q, r: r, terrain: res[i % res.length], number: NUM_CYCLE[i % NUM_CYCLE.length] };
    },
    _firstDesert: function () {
      var ks = Object.keys(this.tiles);
      for (var i = 0; i < ks.length; i++) if (this.tiles[ks[i]].terrain === "desert") return { q: this.tiles[ks[i]].q, r: this.tiles[ks[i]].r };
      return null;
    },
    _anyTile: function () {
      var ks = Object.keys(this.tiles);
      if (!ks.length) return null;
      return { q: this.tiles[ks[0]].q, r: this.tiles[ks[0]].r };
    },

    // Build the MapSpec to hand back to the lobby.
    toSpec: function () {
      var tiles = Object.keys(this.tiles).map(function (k) {
        var t = MapEditor.tiles[k];
        var o = { q: t.q, r: t.r, terrain: t.terrain };
        if (t.terrain !== "desert") o.number = t.number;
        return o;
      });
      tiles.sort(function (a, b) { return a.r - b.r || a.q - b.q; });
      var spec = { name: this.name || "Custom Map", tiles: tiles };
      if (this.robber) spec.robber = { q: this.robber.q, r: this.robber.r };
      if (this.portsMode === "none") {
        spec.ports = [];
      } else if (this.portsMode === "custom") {
        var self = this;
        var pe = Object.keys(this.ports).sort().map(function (k) {
          var p = k.split(",");
          return { q: +p[0], r: +p[1], dir: +p[2], type: self.ports[k] };
        });
        if (pe.length) spec.portsEdges = pe;
        else spec.ports = [];  // "custom" with nothing placed = no ports
      }
      return spec;
    },

    /* ---------- validation (client-side, friendly) ---------- */
    validate: function () {
      var ks = Object.keys(this.tiles);
      if (!ks.length) return "Add at least one tile.";
      var deserts = 0;
      for (var i = 0; i < ks.length; i++) {
        var t = this.tiles[ks[i]];
        if (t.terrain === "desert") { deserts++; continue; }
        if (t.number == null) return "Every land tile needs a number token.";
        if (NUMBERS.indexOf(t.number) < 0) return "Number tokens must be 2-12 (not 7).";
      }
      if (deserts === 0 && !this.robber) return "Place the robber on a tile (or add a desert).";
      return null;
    },

    /* ---------- modal ---------- */
    _buildModal: function () {
      var self = this;
      var modal = el("div", "modal modal-wide map-editor");
      modal.appendChild(el("h2", null, "Custom Map Editor"));
      modal.appendChild(el("p", "sub", "Click the board to paint with the selected tool. Edit the JSON directly on the right — both stay in sync."));

      // top settings row
      var top = el("div", "editor-top");
      var nameWrap = el("label", "field");
      nameWrap.appendChild(el("span", null, "Map name"));
      var nameIn = el("input"); nameIn.type = "text"; nameIn.maxLength = 40; nameIn.value = this.name;
      nameIn.addEventListener("input", function () { self.name = this.value || "Custom Map"; self._syncText(); });
      nameWrap.appendChild(nameIn);
      top.appendChild(nameWrap);

      var sizeWrap = el("label", "field");
      sizeWrap.appendChild(el("span", null, "Base size (radius)"));
      var sizeIn = el("input"); sizeIn.type = "number"; sizeIn.min = 1; sizeIn.max = MAX_RADIUS;
      sizeIn.value = this._guessRadius();
      sizeIn.addEventListener("change", function () { self._resize(parseInt(this.value, 10)); });
      sizeWrap.appendChild(sizeIn);
      top.appendChild(sizeWrap);

      var portWrap = el("label", "field");
      portWrap.appendChild(el("span", null, "Ports"));
      var portSel = el("select");
      [["auto", "Auto (spread around coast)"], ["none", "None"],
       ["custom", "Custom — place with the Port brushes"]].forEach(function (o) {
        var op = el("option", null, o[1]); op.value = o[0]; portSel.appendChild(op);
      });
      portSel.value = this.portsMode;
      this._portSel = portSel;
      portSel.addEventListener("change", function () { self.portsMode = this.value; self._draw(); self._syncText(); });
      portWrap.appendChild(portSel);
      top.appendChild(portWrap);
      modal.appendChild(top);

      // main split: canvas | text
      var split = el("div", "editor-split");
      var left = el("div", "editor-canvas-wrap");
      var canvas = el("canvas", "editor-canvas");
      this.canvas = canvas; this.ctx = canvas.getContext("2d");
      left.appendChild(canvas);
      split.appendChild(left);

      var right = el("div", "editor-text-wrap");
      right.appendChild(el("h3", null, "Map definition (JSON)"));
      var ta = el("textarea", "editor-text"); ta.spellcheck = false;
      this._textArea = ta;
      right.appendChild(ta);
      var taBtns = el("div", "editor-text-btns");
      var applyText = el("button", "btn btn-sm", "Apply JSON");
      applyText.addEventListener("click", function () { self._applyText(); });
      var fmtText = el("button", "btn btn-sm", "Format");
      fmtText.addEventListener("click", function () { self._syncText(); });
      taBtns.appendChild(applyText); taBtns.appendChild(fmtText);
      var textErr = el("p", "form-error"); this._textErr = textErr;
      right.appendChild(taBtns); right.appendChild(textErr);
      split.appendChild(right);
      modal.appendChild(split);

      // brushes
      modal.appendChild(this._buildBrushes());

      // actions
      var actions = el("div", "modal-actions");
      var err = el("span", "form-error editor-err"); this._errEl = err;
      actions.appendChild(err);
      var cancel = el("button", "btn", "Cancel");
      cancel.addEventListener("click", function () { UI.closeModal(); });
      var use = el("button", "btn btn-primary", "Use This Map");
      use.addEventListener("click", function () { self._apply(); });
      actions.appendChild(cancel); actions.appendChild(use);
      modal.appendChild(actions);

      UI._openModal(modal, "mapeditor");

      // size the canvas now that it's in the DOM, and wire clicks
      this._resizeCanvas();
      canvas.addEventListener("click", function (ev) { self._onCanvasClick(ev); });
      canvas.addEventListener("mousemove", function (ev) { self._onCanvasHover(ev); });
      canvas.addEventListener("mouseleave", function () { self._hover = null; self._draw(); });
      window.addEventListener("resize", this._onWinResize = function () { self._resizeCanvas(); self._draw(); });
    },

    _buildBrushes: function () {
      var self = this;
      var wrap = el("div", "editor-brushes");
      this._brushBtns = [];
      function brush(label, kind, value, bg, fg) {
        var b = el("button", "brush-btn", label);
        if (bg) { b.style.background = bg; if (fg) b.style.color = fg; }
        b.dataset.kind = kind; b.dataset.value = value == null ? "" : String(value);
        b.addEventListener("click", function () {
          self.tool = { kind: kind, value: value };
          self._refreshBrushSel();
        });
        self._brushBtns.push(b);
        return b;
      }
      var terrRow = el("div", "brush-row");
      terrRow.appendChild(el("span", "brush-label", "Terrain"));
      var darkText = { fields: 1, pasture: 1, desert: 1, gold: 1 };
      TERRAINS.forEach(function (t) {
        terrRow.appendChild(brush(TERRAIN_LABEL[t], "terrain", t, TERRAIN_COLOR[t], darkText[t] ? "#3a2a18" : "#fff"));
      });
      wrap.appendChild(terrRow);

      var numRow = el("div", "brush-row");
      numRow.appendChild(el("span", "brush-label", "Number"));
      NUMBERS.forEach(function (n) {
        numRow.appendChild(brush(String(n), "number", n, "#f3e6c8", n === 6 || n === 8 ? "#c0392b" : "#3a2a18"));
      });
      wrap.appendChild(numRow);

      var portRow = el("div", "brush-row");
      portRow.appendChild(el("span", "brush-label", "Ports"));
      PORT_TYPES.forEach(function (pt) {
        var dark = pt === "sheep" || pt === "wheat";
        portRow.appendChild(brush(PORT_LABEL[pt], "port", pt, PORT_COLOR[pt], dark ? "#3a2a18" : "#fff"));
      });
      wrap.appendChild(portRow);

      var toolRow = el("div", "brush-row");
      toolRow.appendChild(el("span", "brush-label", "Tools"));
      toolRow.appendChild(brush("⛺ Robber", "robber", null, "#2c2c2c", "#fff"));
      toolRow.appendChild(brush("✚ Add tile", "add", null));
      toolRow.appendChild(brush("✖ Remove", "remove", null, "#7d231a", "#fff"));
      wrap.appendChild(toolRow);

      setTimeout(function () { self._refreshBrushSel(); }, 0);
      return wrap;
    },

    _refreshBrushSel: function () {
      var self = this;
      (this._brushBtns || []).forEach(function (b) {
        var match = b.dataset.kind === self.tool.kind &&
          (self.tool.value == null ? b.dataset.value === "" : b.dataset.value === String(self.tool.value));
        b.classList.toggle("sel", match);
      });
    },

    _guessRadius: function () {
      var max = 1;
      var self = this;
      Object.keys(this.tiles).forEach(function (k) {
        var t = self.tiles[k];
        var d = Math.max(Math.abs(t.q), Math.abs(t.r), Math.abs(t.q + t.r));
        if (d > max) max = d;
      });
      return Math.min(MAX_RADIUS, max);
    },

    _resize: function (radius) {
      if (isNaN(radius)) return;
      radius = Math.max(1, Math.min(MAX_RADIUS, radius));
      var field = hexField(radius);
      var next = {};
      field.forEach(function (a, i) {
        var k = key(a[0], a[1]);
        next[k] = MapEditor.tiles[k] || MapEditor._defaultTile(a[0], a[1], i);
      });
      this.tiles = next;
      this._prunePorts();
      if (!this.robber || !this.tiles[key(this.robber.q, this.robber.r)]) {
        this.robber = this._firstDesert() || this._anyTile();
      }
      this._draw(); this._syncText();
    },

    /* ---------- canvas ---------- */
    _resizeCanvas: function () {
      var c = this.canvas;
      if (!c) return;
      var rect = c.getBoundingClientRect();
      var w = Math.max(200, Math.floor(rect.width));
      var h = Math.max(200, Math.floor(rect.height));
      this.dpr = window.devicePixelRatio || 1;
      c.width = Math.floor(w * this.dpr);
      c.height = Math.floor(h * this.dpr);
      this._cssW = w; this._cssH = h;
      this._computeTransform();
    },

    _allCenters: function () {
      var self = this;
      return Object.keys(this.tiles).map(function (k) {
        var t = self.tiles[k]; var c = hexCenter(t.q, t.r);
        return { q: t.q, r: t.r, cx: c.cx, cy: c.cy, t: t };
      });
    },

    _computeTransform: function () {
      var centers = this._allCenters();
      if (!centers.length) { this._t = { s: 1, ox: 0, oy: 0 }; return; }
      var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
      centers.forEach(function (c) {
        minx = Math.min(minx, c.cx - HEX_SIZE); maxx = Math.max(maxx, c.cx + HEX_SIZE);
        miny = Math.min(miny, c.cy - HEX_SIZE); maxy = Math.max(maxy, c.cy + HEX_SIZE);
      });
      var bw = Math.max(1, maxx - minx), bh = Math.max(1, maxy - miny);
      var margin = 14;
      var s = Math.min((this._cssW - margin * 2) / bw, (this._cssH - margin * 2) / bh);
      this._t = {
        s: s,
        ox: (this._cssW - bw * s) / 2 - minx * s,
        oy: (this._cssH - bh * s) / 2 - miny * s,
      };
    },

    _corners: function (cx, cy) {
      var out = [];
      for (var i = 0; i < 6; i++) {
        var a = (60 * i - 30) * Math.PI / 180;
        out.push([cx + HEX_SIZE * Math.cos(a), cy + HEX_SIZE * Math.sin(a)]);
      }
      return out;
    },
    _wx: function (x) { return x * this._t.s + this._t.ox; },
    _wy: function (y) { return y * this._t.s + this._t.oy; },

    _draw: function () {
      var ctx = this.ctx;
      if (!ctx || !this._t) return;
      this._computeTransform();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.fillStyle = "#1d4f6b";
      ctx.fillRect(0, 0, this._cssW, this._cssH);

      var self = this;
      this._allCenters().forEach(function (c) {
        var corners = self._corners(c.cx, c.cy);
        ctx.beginPath();
        corners.forEach(function (p, i) {
          var x = self._wx(p[0]), y = self._wy(p[1]);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.closePath();
        ctx.fillStyle = TERRAIN_COLOR[c.t.terrain] || "#999";
        ctx.fill();
        var isHover = self._hover && self._hover.q === c.q && self._hover.r === c.r;
        ctx.lineWidth = isHover ? 3 : 1.5;
        ctx.strokeStyle = isHover ? "#fff7e0" : "rgba(58,42,24,.8)";
        ctx.stroke();

        // number chit
        if (c.t.terrain !== "desert" && c.t.number != null) {
          self._chit(self._wx(c.cx), self._wy(c.cy), c.t.number);
        }
        // robber
        if (self.robber && self.robber.q === c.q && self.robber.r === c.r) {
          self._robberMark(self._wx(c.cx), self._wy(c.cy));
        }
      });

      // placed ports (custom mode): badge off the coast, like in-game
      if (this.portsMode === "custom") {
        Object.keys(this.ports).forEach(function (k) {
          var p = k.split(",");
          var q = +p[0], r = +p[1], d = +p[2];
          var t = self.ports[k];
          var c = hexCenter(q, r);
          var m = edgeMid(c, d);
          var vx = m.x - c.cx, vy = m.y - c.cy;
          var L = Math.hypot(vx, vy) || 1;
          var sx = self._wx(m.x + vx / L * 36), sy = self._wy(m.y + vy / L * 36);
          var rad = Math.max(8, 13 * self._t.s);
          ctx.beginPath();
          ctx.arc(sx, sy, rad, 0, Math.PI * 2);
          ctx.fillStyle = PORT_COLOR[t] || "#7a4a23";
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = "#0c0805";
          ctx.stroke();
          ctx.fillStyle = (t === "sheep" || t === "wheat") ? "#3a2a18" : "#fff";
          ctx.font = "700 " + Math.max(8, Math.round(rad * 0.72)) + 'px "Segoe UI",Arial,sans-serif';
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(PORT_SHORT[t] || "?", sx, sy);
        });
      }
    },

    _chit: function (cx, cy, n) {
      var ctx = this.ctx;
      var red = n === 6 || n === 8;
      var r = Math.max(9, 16 * this._t.s);
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = "#f3e6c8"; ctx.fill();
      ctx.lineWidth = 1; ctx.strokeStyle = "rgba(58,42,24,.6)"; ctx.stroke();
      ctx.fillStyle = red ? "#c0392b" : "#3a2a18";
      ctx.font = "700 " + Math.round(r * 1.0) + 'px "Segoe UI",Arial,sans-serif';
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(n), cx, cy - r * 0.12);
      var pips = PIPS[n] || 0;
      var dotR = Math.max(0.8, r * 0.08), gap = dotR * 2.4, total = (pips - 1) * gap;
      for (var i = 0; i < pips; i++) {
        ctx.beginPath(); ctx.arc(cx - total / 2 + i * gap, cy + r * 0.5, dotR, 0, Math.PI * 2); ctx.fill();
      }
    },
    _robberMark: function (cx, cy) {
      var ctx = this.ctx;
      var s = Math.max(10, 18 * this._t.s);
      ctx.beginPath();
      ctx.moveTo(cx, cy - s * 0.5);
      ctx.bezierCurveTo(cx + s * 0.35, cy - s * 0.5, cx + s * 0.32, cy + s * 0.5, cx, cy + s * 0.5);
      ctx.bezierCurveTo(cx - s * 0.32, cy + s * 0.5, cx - s * 0.35, cy - s * 0.5, cx, cy - s * 0.5);
      ctx.fillStyle = "rgba(20,20,20,.9)"; ctx.fill();
      ctx.lineWidth = 1.5; ctx.strokeStyle = "#000"; ctx.stroke();
    },

    _pick: function (ev) {
      var rect = this.canvas.getBoundingClientRect();
      var px = ev.clientX - rect.left, py = ev.clientY - rect.top;
      var best = null, bestD = Infinity;
      var self = this;
      // nearest hex centre within range (hexes are convex; nearest centre is fine)
      this._allCenters().forEach(function (c) {
        var dx = self._wx(c.cx) - px, dy = self._wy(c.cy) - py;
        var d = dx * dx + dy * dy;
        if (d < bestD) { bestD = d; best = c; }
      });
      var rpx = HEX_SIZE * this._t.s;
      if (best && bestD <= rpx * rpx) return { q: best.q, r: best.r };
      // for the "add" tool, also allow clicking just-outside existing tiles
      if (this.tool.kind === "add") return this._pickEmpty(px, py);
      return null;
    },

    // Map a click to the nearest empty axial neighbour of the board (for adding).
    _pickEmpty: function (px, py) {
      var self = this;
      var cand = {};
      var dirs = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, -1], [-1, 1]];
      Object.keys(this.tiles).forEach(function (k) {
        var t = self.tiles[k];
        dirs.forEach(function (d) {
          var nq = t.q + d[0], nr = t.r + d[1];
          if (!self.tiles[key(nq, nr)]) cand[key(nq, nr)] = [nq, nr];
        });
      });
      var best = null, bestD = Infinity;
      Object.keys(cand).forEach(function (k) {
        var c = hexCenter(cand[k][0], cand[k][1]);
        var dx = self._wx(c.cx) - px, dy = self._wy(c.cy) - py;
        var d = dx * dx + dy * dy;
        if (d < bestD) { bestD = d; best = cand[k]; }
      });
      var rpx = HEX_SIZE * this._t.s;
      return best && bestD <= rpx * rpx ? { q: best[0], r: best[1] } : null;
    },

    // Map a click to the nearest edge (tile + dir 0-5) of any existing tile.
    _pickEdge: function (ev) {
      var rect = this.canvas.getBoundingClientRect();
      var px = ev.clientX - rect.left, py = ev.clientY - rect.top;
      var best = null, bestD = Infinity;
      var self = this;
      Object.keys(this.tiles).forEach(function (k) {
        var t = self.tiles[k];
        var c = hexCenter(t.q, t.r);
        for (var d = 0; d < 6; d++) {
          var m = edgeMid(c, d);
          var dx = self._wx(m.x) - px, dy = self._wy(m.y) - py;
          var dd = dx * dx + dy * dy;
          if (dd < bestD) { bestD = dd; best = { q: t.q, r: t.r, dir: d }; }
        }
      });
      var rpx = HEX_SIZE * this._t.s * 0.55;
      return best && bestD <= rpx * rpx ? best : null;
    },

    _onCanvasHover: function (ev) {
      var hit = this._pick(ev);
      var changed = (!!hit !== !!this._hover) || (hit && this._hover && (hit.q !== this._hover.q || hit.r !== this._hover.r));
      this._hover = hit;
      if (changed) this._draw();
    },

    _onCanvasClick: function (ev) {
      var tool = this.tool;
      // Port brush: snap to the nearest coastal edge; same type again removes.
      if (tool.kind === "port") {
        var eh = this._pickEdge(ev);
        if (!eh) return;
        var nb = NEIGH[eh.dir];
        if (this.tiles[key(eh.q + nb[0], eh.r + nb[1])]) {
          if (window.UI) UI.toast("Ports must sit on the coast — that edge faces land.");
          return;
        }
        var pk = key3(eh.q, eh.r, eh.dir);
        if (this.ports[pk] === tool.value) delete this.ports[pk];
        else this.ports[pk] = tool.value;
        this.portsMode = "custom";
        if (this._portSel) this._portSel.value = "custom";
        this._draw();
        this._syncText();
        return;
      }
      var hit = this._pick(ev);
      if (!hit) return;
      var k = key(hit.q, hit.r);
      var t = this.tiles[k];
      if (tool.kind === "add") {
        if (!t) this.tiles[k] = { q: hit.q, r: hit.r, terrain: "fields", number: 6 };
        this._prunePorts();  // a new tile can landlock a neighbouring port
      } else if (tool.kind === "remove") {
        if (t) {
          delete this.tiles[k];
          this._prunePorts();
          if (this.robber && this.robber.q === hit.q && this.robber.r === hit.r) {
            this.robber = this._firstDesert() || this._anyTile();
          }
        }
      } else if (!t) {
        return;
      } else if (tool.kind === "terrain") {
        t.terrain = tool.value;
        if (tool.value === "desert") t.number = null;
        else if (t.number == null) t.number = 6;
      } else if (tool.kind === "number") {
        if (t.terrain !== "desert") t.number = tool.value;
      } else if (tool.kind === "robber") {
        this.robber = { q: hit.q, r: hit.r };
      }
      this._draw();
      this._syncText();
    },

    /* ---------- text sync ---------- */
    _syncText: function () {
      if (!this._textArea) return;
      if (document.activeElement === this._textArea) return; // don't fight the typist
      this._textArea.value = JSON.stringify(this.toSpec(), null, 2);
      if (this._textErr) this._textErr.textContent = "";
    },

    _applyText: function () {
      var raw = this._textArea ? this._textArea.value : "";
      var spec;
      try {
        spec = JSON.parse(raw);
      } catch (e) {
        if (this._textErr) this._textErr.textContent = "Invalid JSON: " + e.message;
        return;
      }
      try {
        this._load(spec);
      } catch (e) {
        if (this._textErr) this._textErr.textContent = "Could not load that map.";
        return;
      }
      if (this._textErr) this._textErr.textContent = "";
      this._resizeCanvas();
      this._draw();
      // refresh the JSON to the normalized form
      var ta = this._textArea; var prev = document.activeElement;
      if (prev === ta) ta.blur();
      this._syncText();
    },

    /* ---------- apply / close ---------- */
    _apply: function () {
      var err = this.validate();
      if (err) { if (this._errEl) this._errEl.textContent = err; return; }
      var spec = this.toSpec();
      if (this._onWinResize) window.removeEventListener("resize", this._onWinResize);
      UI.closeModal();
      if (this.onApply) this.onApply(spec);
    },
  };

  // Wire into the lobby's "Open Map Editor…" button.
  UI._openMapEditorImpl = function () {
    var initial = UI._customMap || { name: "Custom Map", radius: 2 };
    MapEditor.open(initial, function (spec) { UI.applyCustomMap(spec); });
  };

  window.MapEditor = MapEditor;
})();
