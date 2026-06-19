/* ============================================================
   board.js  ·  window.BoardView
   Pure canvas renderer + hit-testing for the HEXARA board.
   It owns no game logic: it draws whatever GameState.board says and
   reports which vertex/edge/hex the user clicked. Highlights are driven
   by a highlightSpec built from the server's `legal` object.
   ============================================================ */
(function () {
  "use strict";

  /* ---- asset image cache with graceful failure ----
     We load SVGs as <img> once and reuse. If one fails, ok stays false
     and the renderer falls back to a coded shape/colour. */
  var ImgCache = {};
  function getImg(src) {
    var rec = ImgCache[src];
    if (rec) return rec;
    rec = { img: new Image(), ok: false, failed: false };
    rec.img.onload = function () {
      rec.ok = true;
      if (BoardView._onAssetLoad) BoardView._onAssetLoad();
    };
    rec.img.onerror = function () {
      rec.failed = true;
    };
    rec.img.src = src;
    ImgCache[src] = rec;
    return rec;
  }

  // Fallback flat colours per terrain (used if a tile SVG fails to load).
  var TERRAIN_FALLBACK = {
    forest: "#2e7d32",
    hills: "#c1572e",
    pasture: "#8bc34a",
    fields: "#e3b23c",
    mountains: "#6b7785",
    desert: "#e6d3a8",
    gold: "#e0b81e",
    beans: "#b07a2a",
  };

  var BoardView = {
    canvas: null,
    ctx: null,
    dpr: 1,
    _state: null,
    _highlight: null, // {vertices:Set, edges:Set, hexes:Set, mode:string}
    _colorMap: {}, // playerId -> hex
    _t: null, // transform {scale, ox, oy, hexR}
    _clickCb: null, // (hit) => void  hit={vertex|edge|hex, x, y}
    _pulse: 0,
    _raf: null,
    _onAssetLoad: null,

    init: function (canvas, colorMap) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this._colorMap = colorMap || {};
      var self = this;

      // Re-render once any asset finishes loading (so first paint isn't bare).
      this._onAssetLoad = function () {
        if (self._state) self.draw(self._state, self._highlight);
      };

      // Click / tap → hit test.
      canvas.addEventListener("click", function (ev) {
        if (!self._clickCb) return;
        var p = self._eventPoint(ev);
        var hit = self.hitTest(p.x, p.y);
        self._clickCb(hit, ev);
      });
      // Right-click handled by app (cancel mode); just suppress menu here.
      canvas.addEventListener("contextmenu", function (ev) {
        ev.preventDefault();
      });

      this.resize();
      window.addEventListener("resize", function () {
        self.resize();
      });

      // Highlights pulse via a render loop that runs ONLY while a highlight is
      // active, then stops so the page can go idle (no wasted CPU, and headless
      // screenshots can settle). draw()/setHighlight() restart it as needed.
      this._ensureAnim();
    },

    _animLoop: function (ts) {
      this._pulse = (Math.sin(ts / 380) + 1) / 2; // 0..1
      if (this._state && this._highlightActive()) {
        this._render();
        var self = this;
        this._raf = requestAnimationFrame(function (t) { self._animLoop(t); });
      } else {
        this._raf = null; // nothing animating → go idle
      }
    },

    _ensureAnim: function () {
      if (this._raf == null && this._state && this._highlightActive()) {
        var self = this;
        this._raf = requestAnimationFrame(function (t) { self._animLoop(t); });
      }
    },

    setColorMap: function (map) {
      this._colorMap = map || {};
    },
    onClick: function (cb) {
      this._clickCb = cb;
    },

    _highlightActive: function () {
      var h = this._highlight;
      return !!(h && ((h.vertices && h.vertices.size) || (h.edges && h.edges.size) || (h.hexes && h.hexes.size)));
    },

    /* ---- DPR-aware sizing ---- */
    resize: function () {
      var c = this.canvas;
      if (!c) return;
      var rect = c.getBoundingClientRect();
      var w = Math.max(1, Math.floor(rect.width));
      var h = Math.max(1, Math.floor(rect.height));
      this.dpr = window.devicePixelRatio || 1;
      c.width = Math.floor(w * this.dpr);
      c.height = Math.floor(h * this.dpr);
      this._cssW = w;
      this._cssH = h;
      if (this._state) this._computeTransform(this._state.board);
      if (this._state) this._render();
    },

    /* ---- transform: board bounds -> canvas (css px) with margin ---- */
    _computeTransform: function (board) {
      if (!board || !board.bounds) {
        this._t = { scale: 1, ox: 0, oy: 0, hexR: 30 };
        return;
      }
      var b = board.bounds;
      var bw = Math.max(1e-6, b.maxx - b.minx);
      var bh = Math.max(1e-6, b.maxy - b.miny);
      var margin = 46; // px of breathing room (ports stick out a bit)
      var availW = Math.max(1, this._cssW - margin * 2);
      var availH = Math.max(1, this._cssH - margin * 2);
      var scale = Math.min(availW / bw, availH / bh);
      // centre
      var drawW = bw * scale;
      var drawH = bh * scale;
      var ox = (this._cssW - drawW) / 2 - b.minx * scale;
      var oy = (this._cssH - drawH) / 2 - b.miny * scale;

      // hex radius in px: distance from a hex centre to one of its vertices.
      var hexR = 30;
      if (board.hexes && board.hexes.length && board.vertices && board.vertices.length) {
        var vmap = this._vertexMap(board);
        var h0 = board.hexes[0];
        var corners = this._hexCorners(board, h0, vmap);
        if (corners.length) {
          var d = Math.hypot(corners[0].x - h0.cx, corners[0].y - h0.cy);
          if (d > 0) hexR = d * scale;
        }
      }
      this._t = { scale: scale, ox: ox, oy: oy, hexR: hexR };
    },

    // world -> screen (css px)
    _wx: function (x) {
      return x * this._t.scale + this._t.ox;
    },
    _wy: function (y) {
      return y * this._t.scale + this._t.oy;
    },
    // screen (css px) -> world
    _ux: function (px) {
      return (px - this._t.ox) / this._t.scale;
    },
    _uy: function (py) {
      return (py - this._t.oy) / this._t.scale;
    },

    _vertexMap: function (board) {
      if (board._vmap) return board._vmap;
      var m = {};
      (board.vertices || []).forEach(function (v) {
        m[v.id] = v;
      });
      try {
        Object.defineProperty(board, "_vmap", { value: m, enumerable: false });
      } catch (e) {
        board._vmap = m;
      }
      return m;
    },

    /* ---- hex polygon corners (world coords) ----
       Prefer the 6 nearest actual vertices (exact geometry); fall back to
       computed pointy-top corners from (cx,cy) if needed. */
    _hexCorners: function (board, hex, vmap) {
      var verts = board.vertices || [];
      if (verts.length) {
        // distance from each vertex to this hex centre
        var arr = [];
        for (var i = 0; i < verts.length; i++) {
          var v = verts[i];
          var d = (v.x - hex.cx) * (v.x - hex.cx) + (v.y - hex.cy) * (v.y - hex.cy);
          arr.push({ v: v, d: d });
        }
        arr.sort(function (a, b) {
          return a.d - b.d;
        });
        var six = arr.slice(0, 6).map(function (o) {
          return o.v;
        });
        // sort the 6 by angle around the centre for a clean polygon
        six.sort(function (a, b) {
          return (
            Math.atan2(a.y - hex.cy, a.x - hex.cx) - Math.atan2(b.y - hex.cy, b.x - hex.cx)
          );
        });
        return six.map(function (v) {
          return { x: v.x, y: v.y };
        });
      }
      // fallback: pointy-top corners, radius guessed from layout
      var r = this._approxHexRadius(board);
      var out = [];
      for (var k = 0; k < 6; k++) {
        var ang = ((60 * k - 30) * Math.PI) / 180;
        out.push({ x: hex.cx + r * Math.cos(ang), y: hex.cy + r * Math.sin(ang) });
      }
      return out;
    },

    _approxHexRadius: function (board) {
      if (board._approxR) return board._approxR;
      var r = 1;
      if (board.bounds) {
        // 5 hexes across roughly -> width ~ 5 * (sqrt(3)*r)
        var bw = board.bounds.maxx - board.bounds.minx;
        r = bw / (5 * Math.sqrt(3));
      }
      board._approxR = r;
      return r;
    },

    /* ===================== MAIN RENDER ===================== */
    draw: function (state, highlightSpec) {
      this._state = state;
      this._highlight = highlightSpec || null;
      if (state && state.board) this._computeTransform(state.board);
      this._render();
      this._ensureAnim();
    },

    setHighlight: function (highlightSpec) {
      this._highlight = highlightSpec || null;
      this._render();
      this._ensureAnim();
    },

    _render: function () {
      var ctx = this.ctx;
      if (!ctx) return;
      var W = this.canvas.width,
        H = this.canvas.height;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, W, H);
      // scale to DPR so all subsequent drawing uses css px
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

      this._drawSea();

      var state = this._state;
      if (!state || !state.board || !this._t) return;
      var board = state.board;
      var vmap = this._vertexMap(board);

      this._drawHexes(board, vmap);
      this._drawPorts(board);
      this._drawRoads(board, vmap);
      this._drawHighlightEdges(board, vmap);
      // Hex highlights (robber placement) go UNDER the buildings so you can
      // still see whose settlements sit where while choosing a target.
      this._drawHighlightHexes(board, vmap);
      this._drawBuildings(board);
      this._drawHighlightVertices(board);
      this._drawRobber(board);
    },

    _drawSea: function () {
      var ctx = this.ctx;
      var rec = getImg("/assets/ui/sea.svg");
      var W = this._cssW,
        H = this._cssH;
      if (rec.ok) {
        var p = ctx.createPattern(rec.img, "repeat");
        if (p) {
          ctx.fillStyle = p;
          ctx.fillRect(0, 0, W, H);
          return;
        }
      }
      // fallback gradient
      var g = ctx.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, "#2b6c8f");
      g.addColorStop(1, "#1d4f6b");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    },

    _hexPathScreen: function (corners) {
      var ctx = this.ctx;
      ctx.beginPath();
      for (var i = 0; i < corners.length; i++) {
        var sx = this._wx(corners[i].x),
          sy = this._wy(corners[i].y);
        if (i === 0) ctx.moveTo(sx, sy);
        else ctx.lineTo(sx, sy);
      }
      ctx.closePath();
    },

    _drawHexes: function (board, vmap) {
      var ctx = this.ctx;
      var self = this;
      (board.hexes || []).forEach(function (hex) {
        var corners = self._hexCorners(board, hex, vmap);
        // bounding box for tile image
        var minx = Infinity,
          miny = Infinity,
          maxx = -Infinity,
          maxy = -Infinity;
        for (var i = 0; i < corners.length; i++) {
          var sx = self._wx(corners[i].x),
            sy = self._wy(corners[i].y);
          if (sx < minx) minx = sx;
          if (sy < miny) miny = sy;
          if (sx > maxx) maxx = sx;
          if (sy > maxy) maxy = sy;
        }

        ctx.save();
        self._hexPathScreen(corners);
        ctx.clip();
        var rec = getImg("/assets/tiles/" + hex.terrain + ".svg");
        if (rec.ok) {
          // tile SVG is full-bleed square; cover the hex bbox (square-ish)
          var side = Math.max(maxx - minx, maxy - miny) + 2;
          ctx.drawImage(rec.img, minx - 1, miny - 1, side, side);
        } else {
          ctx.fillStyle = TERRAIN_FALLBACK[hex.terrain] || "#999";
          ctx.fillRect(minx - 1, miny - 1, maxx - minx + 2, maxy - miny + 2);
        }
        ctx.restore();

        // hex outline
        ctx.save();
        self._hexPathScreen(corners);
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(58,42,24,.85)";
        ctx.stroke();
        ctx.restore();

        // number chit
        if (hex.number != null && hex.terrain !== "desert") {
          self._drawChit(self._wx(hex.cx), self._wy(hex.cy), hex.number);
        }
      });
    },

    _drawChit: function (cx, cy, number) {
      var ctx = this.ctx;
      var red = number === 6 || number === 8;
      var r = this._t.hexR * (red ? 0.34 : 0.31);
      r = Math.max(11, r);
      // disc
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = "#f3e6c8";
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "rgba(58,42,24,.7)";
      ctx.stroke();
      // number
      ctx.fillStyle = red ? "#c0392b" : "#3a2a18";
      ctx.font = "700 " + Math.round(r * (red ? 1.15 : 1.0)) + "px " + numFont();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(number), cx, cy - r * 0.12);
      // pips (probability dots)
      var pips = PIPS[number] || 0;
      if (pips) {
        var dotR = Math.max(1.1, r * 0.075);
        var gap = dotR * 2.4;
        var totalW = (pips - 1) * gap;
        var startX = cx - totalW / 2;
        var py = cy + r * 0.52;
        ctx.fillStyle = red ? "#c0392b" : "#3a2a18";
        for (var i = 0; i < pips; i++) {
          ctx.beginPath();
          ctx.arc(startX + i * gap, py, dotR, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    },

    _drawPorts: function (board) {
      var ctx = this.ctx;
      var self = this;
      var vmap = this._vertexMap(board);
      (board.ports || []).forEach(function (port) {
        if (port.x == null || port.y == null) return;
        var px = self._wx(port.x),
          py = self._wy(port.y);
        // dock lines to the two vertices
        ctx.save();
        ctx.strokeStyle = "rgba(90,52,23,.85)";
        ctx.lineWidth = Math.max(2, self._t.hexR * 0.09);
        ctx.lineCap = "round";
        (port.vertices || []).forEach(function (vid) {
          var v = vmap[vid];
          if (!v) return;
          ctx.beginPath();
          ctx.moveTo(px, py);
          ctx.lineTo(self._wx(v.x), self._wy(v.y));
          ctx.stroke();
        });
        ctx.restore();

        // badge
        var key = port.type === "3:1" ? "generic" : port.type;
        var rec = getImg("/assets/ui/port_" + key + ".svg");
        var size = Math.max(24, self._t.hexR * 0.95);
        if (rec.ok) {
          ctx.drawImage(rec.img, px - size / 2, py - size / 2, size, size);
        } else {
          ctx.save();
          ctx.beginPath();
          ctx.arc(px, py, size / 2, 0, Math.PI * 2);
          ctx.fillStyle = "#7a4a23";
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = "#3a2a18";
          ctx.stroke();
          ctx.fillStyle = "#fff";
          ctx.font = "700 " + Math.round(size * 0.3) + "px " + numFont();
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(port.type === "3:1" ? "3:1" : "2:1", px, py - size * 0.12);
          ctx.font = "600 " + Math.round(size * 0.2) + "px " + numFont();
          ctx.fillText(port.type === "3:1" ? "?" : port.type, px, py + size * 0.22);
          ctx.restore();
        }
      });
    },

    _edgeMidAngle: function (board, vmap, edge) {
      var v1 = vmap[edge.v1],
        v2 = vmap[edge.v2];
      if (!v1 || !v2) return null;
      var x1 = this._wx(v1.x),
        y1 = this._wy(v1.y),
        x2 = this._wx(v2.x),
        y2 = this._wy(v2.y);
      return {
        x1: x1,
        y1: y1,
        x2: x2,
        y2: y2,
        mx: (x1 + x2) / 2,
        my: (y1 + y2) / 2,
      };
    },

    _drawRoads: function (board, vmap) {
      var ctx = this.ctx;
      var self = this;
      var w = Math.max(4, this._t.hexR * 0.2);
      (board.edges || []).forEach(function (edge) {
        if (!edge.road) return;
        var seg = self._edgeMidAngle(board, vmap, edge);
        if (!seg) return;
        var col = self._colorMap[edge.road] || "#cccccc";
        // dark casing then colour for a piece-y look
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(seg.x1, seg.y1);
        ctx.lineTo(seg.x2, seg.y2);
        ctx.lineWidth = w + 3;
        ctx.strokeStyle = "rgba(20,12,4,.55)";
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(seg.x1, seg.y1);
        ctx.lineTo(seg.x2, seg.y2);
        ctx.lineWidth = w;
        ctx.strokeStyle = col;
        ctx.stroke();
      });
    },

    _drawBuildings: function (board) {
      var ctx = this.ctx;
      var self = this;
      (board.vertices || []).forEach(function (v) {
        if (!v.building) return;
        var x = self._wx(v.x),
          y = self._wy(v.y);
        var col = self._colorMap[v.building.owner] || "#cccccc";
        if (v.building.type === "city") self._drawCity(x, y, col);
        else self._drawSettlement(x, y, col);
      });
    },

    _drawSettlement: function (x, y, col) {
      var ctx = this.ctx;
      var s = Math.max(9, this._t.hexR * 0.34);
      ctx.save();
      ctx.translate(x, y);
      ctx.lineJoin = "round";
      ctx.beginPath();
      // little house: square body + triangular roof
      ctx.moveTo(-s * 0.6, s * 0.6); // bottom-left
      ctx.lineTo(-s * 0.6, -s * 0.1); // up left wall
      ctx.lineTo(0, -s * 0.75); // roof peak
      ctx.lineTo(s * 0.6, -s * 0.1); // down right
      ctx.lineTo(s * 0.6, s * 0.6); // right wall
      ctx.closePath();
      ctx.fillStyle = col;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(20,12,4,.85)";
      ctx.stroke();
      ctx.restore();
    },

    _drawCity: function (x, y, col) {
      var ctx = this.ctx;
      var s = Math.max(11, this._t.hexR * 0.42);
      ctx.save();
      ctx.translate(x, y);
      ctx.lineJoin = "round";
      // wide base + a tower with battlement
      ctx.beginPath();
      ctx.moveTo(-s * 0.8, s * 0.6);
      ctx.lineTo(-s * 0.8, -s * 0.1);
      ctx.lineTo(-s * 0.2, -s * 0.45);
      ctx.lineTo(s * 0.2, -s * 0.1);
      ctx.lineTo(s * 0.2, -s * 0.35);
      ctx.lineTo(s * 0.8, -s * 0.35);
      ctx.lineTo(s * 0.8, s * 0.6);
      ctx.closePath();
      ctx.fillStyle = col;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(20,12,4,.9)";
      ctx.stroke();
      ctx.restore();
    },

    _drawRobber: function (board) {
      var ctx = this.ctx;
      var rid = (this._state && this._state.robberHex) != null ? this._state.robberHex : null;
      if (rid == null) {
        // fall back to a hex flagged hasRobber
        (board.hexes || []).some(function (h) {
          if (h.hasRobber) {
            rid = h.id;
            return true;
          }
          return false;
        });
      }
      if (rid == null) return;
      var hex = null;
      (board.hexes || []).some(function (h) {
        if (h.id === rid) {
          hex = h;
          return true;
        }
        return false;
      });
      if (!hex) return;
      var x = this._wx(hex.cx),
        y = this._wy(hex.cy);
      var hsize = Math.max(26, this._t.hexR * 0.95);
      var rec = getImg("/assets/ui/robber.svg");
      // place slightly above the chit so both read
      var ry = y - this._t.hexR * 0.05;
      if (rec.ok) {
        var w = hsize * (48 / 64);
        ctx.drawImage(rec.img, x - w / 2, ry - hsize / 2, w, hsize);
      } else {
        ctx.save();
        ctx.translate(x, ry);
        ctx.beginPath();
        ctx.moveTo(0, -hsize * 0.45);
        ctx.bezierCurveTo(hsize * 0.32, -hsize * 0.45, hsize * 0.3, hsize * 0.45, 0, hsize * 0.45);
        ctx.bezierCurveTo(-hsize * 0.3, hsize * 0.45, -hsize * 0.32, -hsize * 0.45, 0, -hsize * 0.45);
        ctx.fillStyle = "#2c2c2c";
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#000";
        ctx.stroke();
        ctx.restore();
      }
    },

    /* ---- highlights ---- */
    _drawHighlightVertices: function (board) {
      var h = this._highlight;
      if (!h || !h.vertices || !h.vertices.size) return;
      var ctx = this.ctx;
      var self = this;
      var pulse = 0.5 + this._pulse * 0.5;
      var r = Math.max(6, this._t.hexR * 0.22) * (0.85 + this._pulse * 0.25);
      (board.vertices || []).forEach(function (v) {
        if (!h.vertices.has(v.id)) return;
        var x = self._wx(v.x),
          y = self._wy(v.y);
        ctx.save();
        ctx.beginPath();
        ctx.arc(x, y, r + 4, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(243,226,200," + 0.25 * pulse + ")";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = "#fff7e0";
        ctx.globalAlpha = 0.9;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = "#e3b23c";
        ctx.stroke();
        ctx.restore();
      });
    },

    _drawHighlightEdges: function (board, vmap) {
      var h = this._highlight;
      if (!h || !h.edges || !h.edges.size) return;
      var ctx = this.ctx;
      var self = this;
      var w = Math.max(5, this._t.hexR * 0.22);
      var dash = w * 1.6;
      var offset = (this._pulse * dash * 2) % (dash * 2);
      (board.edges || []).forEach(function (edge) {
        if (!h.edges.has(edge.id)) return;
        var seg = self._edgeMidAngle(board, vmap, edge);
        if (!seg) return;
        ctx.save();
        ctx.lineCap = "round";
        ctx.setLineDash([dash, dash]);
        ctx.lineDashOffset = -offset;
        ctx.beginPath();
        ctx.moveTo(seg.x1, seg.y1);
        ctx.lineTo(seg.x2, seg.y2);
        ctx.lineWidth = w;
        ctx.strokeStyle = "#fff7e0";
        ctx.shadowColor = "#e3b23c";
        ctx.shadowBlur = 8;
        ctx.stroke();
        ctx.restore();
      });
    },

    _drawHighlightHexes: function (board, vmap) {
      var h = this._highlight;
      if (!h || !h.hexes || !h.hexes.size) return;
      var ctx = this.ctx;
      var self = this;
      var pulse = 0.5 + this._pulse * 0.5;
      (board.hexes || []).forEach(function (hex) {
        if (!h.hexes.has(hex.id)) return;
        var corners = self._hexCorners(board, hex, vmap);
        self._hexPathScreen(corners);
        ctx.save();
        ctx.fillStyle = "rgba(192,57,43," + 0.22 * pulse + ")";
        ctx.fill();
        ctx.lineWidth = Math.max(3, self._t.hexR * 0.12);
        ctx.strokeStyle = "#ffd54a";
        ctx.shadowColor = "#c0392b";
        ctx.shadowBlur = 10;
        ctx.stroke();
        ctx.restore();
      });
    },

    /* ===================== HIT TESTING ===================== */
    _eventPoint: function (ev) {
      var rect = this.canvas.getBoundingClientRect();
      return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    },

    // Returns {vertex} | {edge} | {hex} | null depending on current mode-agnostic proximity.
    // app.js decides what matters; we expose all three helpers too.
    hitTest: function (px, py) {
      var mode = this._highlight && this._highlight.mode;
      // Prefer the kind relevant to the active mode for forgiving clicks.
      if (mode === "robber") {
        var hx = this.hexAt(px, py);
        if (hx != null) return { hex: hx };
        return null;
      }
      if (mode === "road") {
        var e = this.edgeAt(px, py);
        if (e != null) return { edge: e };
        return null;
      }
      if (mode === "settlement" || mode === "city" || mode === "setup_settlement") {
        var v = this.vertexAt(px, py);
        if (v != null) return { vertex: v };
        return null;
      }
      if (mode === "setup_road") {
        var e2 = this.edgeAt(px, py);
        if (e2 != null) return { edge: e2 };
        return null;
      }
      // No active mode: report closest meaningful element (vertex > edge > hex).
      var vv = this.vertexAt(px, py);
      if (vv != null) return { vertex: vv };
      var ee = this.edgeAt(px, py);
      if (ee != null) return { edge: ee };
      var hh = this.hexAt(px, py);
      if (hh != null) return { hex: hh };
      return null;
    },

    vertexAt: function (px, py) {
      var board = this._state && this._state.board;
      if (!board || !this._t) return null;
      var thresh = Math.max(14, this._t.hexR * 0.45);
      var best = null,
        bestD = thresh * thresh;
      var verts = board.vertices || [];
      for (var i = 0; i < verts.length; i++) {
        var v = verts[i];
        var dx = this._wx(v.x) - px,
          dy = this._wy(v.y) - py;
        var d = dx * dx + dy * dy;
        if (d < bestD) {
          bestD = d;
          best = v.id;
        }
      }
      return best;
    },

    edgeAt: function (px, py) {
      var board = this._state && this._state.board;
      if (!board || !this._t) return null;
      var vmap = this._vertexMap(board);
      var thresh = Math.max(10, this._t.hexR * 0.28);
      var best = null,
        bestD = thresh * thresh;
      var edges = board.edges || [];
      for (var i = 0; i < edges.length; i++) {
        var e = edges[i];
        var v1 = vmap[e.v1],
          v2 = vmap[e.v2];
        if (!v1 || !v2) continue;
        var d = this._distToSeg(
          px,
          py,
          this._wx(v1.x),
          this._wy(v1.y),
          this._wx(v2.x),
          this._wy(v2.y)
        );
        if (d < bestD) {
          bestD = d;
          best = e.id;
        }
      }
      return best;
    },

    hexAt: function (px, py) {
      var board = this._state && this._state.board;
      if (!board || !this._t) return null;
      var vmap = this._vertexMap(board);
      var hexes = board.hexes || [];
      for (var i = 0; i < hexes.length; i++) {
        var corners = this._hexCorners(board, hexes[i], vmap);
        if (this._pointInPoly(px, py, corners)) return hexes[i].id;
      }
      return null;
    },

    _distToSeg: function (px, py, x1, y1, x2, y2) {
      var dx = x2 - x1,
        dy = y2 - y1;
      var len2 = dx * dx + dy * dy;
      var t = len2 ? ((px - x1) * dx + (py - y1) * dy) / len2 : 0;
      t = Math.max(0, Math.min(1, t));
      var cx = x1 + t * dx,
        cy = y1 + t * dy;
      var ex = px - cx,
        ey = py - cy;
      return ex * ex + ey * ey;
    },

    _pointInPoly: function (px, py, corners) {
      // corners are in world coords -> convert to screen
      var inside = false;
      var n = corners.length;
      for (var i = 0, j = n - 1; i < n; j = i++) {
        var xi = this._wx(corners[i].x),
          yi = this._wy(corners[i].y);
        var xj = this._wx(corners[j].x),
          yj = this._wy(corners[j].y);
        var intersect =
          yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi || 1e-9) + xi;
        if (intersect) inside = !inside;
      }
      return inside;
    },
  };

  // pip table mirrors engine NUMBER_PIPS (purely visual).
  var PIPS = { 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1 };
  function numFont() {
    return '"Segoe UI",Roboto,Arial,sans-serif';
  }

  window.BoardView = BoardView;
})();
