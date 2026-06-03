/* ============================================================
   sound.js  ·  window.Sound
   Procedural sound effects via the Web Audio API — no audio files to
   ship. A handful of short synthesized cues (dice, build, trade, robber,
   your-turn, win, plus blackjack chips/cards). Muteable; state persists.
   ============================================================ */
(function () {
  "use strict";
  var KEY = "hexara.muted";
  var ctx = null;

  function ac() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) { ctx = null; }
    }
    if (ctx && ctx.state === "suspended") { try { ctx.resume(); } catch (e) {} }
    return ctx;
  }

  function tone(o) {
    var c = ac(); if (!c) return;
    var t0 = c.currentTime + (o.delay || 0);
    var osc = c.createOscillator(), g = c.createGain();
    osc.type = o.type || "sine";
    osc.frequency.setValueAtTime(o.freq, t0);
    if (o.slideTo) osc.frequency.exponentialRampToValueAtTime(o.slideTo, t0 + o.dur);
    var peak = o.gain == null ? 0.2 : o.gain;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + o.dur);
    osc.connect(g); g.connect(c.destination);
    osc.start(t0); osc.stop(t0 + o.dur + 0.03);
  }

  function noise(o) {
    var c = ac(); if (!c) return;
    var t0 = c.currentTime + (o.delay || 0);
    var n = Math.max(1, Math.floor(c.sampleRate * o.dur));
    var buf = c.createBuffer(1, n, c.sampleRate);
    var d = buf.getChannelData(0);
    for (var i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
    var src = c.createBufferSource(); src.buffer = buf;
    var f = c.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = o.freq || 1200;
    var g = c.createGain(); g.gain.value = o.gain == null ? 0.15 : o.gain;
    src.connect(f); f.connect(g); g.connect(c.destination);
    src.start(t0);
  }

  var FX = {
    dice: function () { noise({ dur: 0.18, gain: 0.13, freq: 1500 }); noise({ dur: 0.12, delay: 0.12, gain: 0.1, freq: 900 }); },
    build: function () { tone({ freq: 180, slideTo: 90, dur: 0.16, type: "square", gain: 0.18 }); },
    trade: function () { tone({ freq: 660, dur: 0.08, type: "triangle", gain: 0.16 }); tone({ freq: 990, dur: 0.1, delay: 0.08, type: "triangle", gain: 0.16 }); },
    robber: function () { tone({ freq: 130, slideTo: 60, dur: 0.5, type: "sawtooth", gain: 0.16 }); },
    turn: function () { tone({ freq: 523, dur: 0.12, type: "sine", gain: 0.2 }); tone({ freq: 784, dur: 0.2, delay: 0.1, type: "sine", gain: 0.2 }); },
    win: function () { [523, 659, 784, 1047].forEach(function (f, i) { tone({ freq: f, dur: 0.26, delay: i * 0.12, type: "triangle", gain: 0.22 }); }); },
    card: function () { noise({ dur: 0.06, gain: 0.09, freq: 2600 }); },
    chip: function () { tone({ freq: 880, dur: 0.05, type: "square", gain: 0.13 }); },
    bust: function () { tone({ freq: 300, slideTo: 110, dur: 0.42, type: "sawtooth", gain: 0.18 }); },
    cash: function () { tone({ freq: 784, dur: 0.08, type: "triangle", gain: 0.16 }); tone({ freq: 1175, dur: 0.13, delay: 0.07, type: "triangle", gain: 0.16 }); },
    bjwin: function () { tone({ freq: 659, dur: 0.1, type: "triangle", gain: 0.2 }); tone({ freq: 988, dur: 0.18, delay: 0.09, type: "triangle", gain: 0.2 }); },
  };

  var Sound = {
    muted: false,
    init: function () {
      try { this.muted = localStorage.getItem(KEY) === "1"; } catch (e) {}
      // Browsers require a user gesture before audio can start.
      function unlock() {
        ac();
        document.removeEventListener("pointerdown", unlock);
        document.removeEventListener("keydown", unlock);
      }
      document.addEventListener("pointerdown", unlock);
      document.addEventListener("keydown", unlock);
    },
    play: function (name) {
      if (this.muted) return;
      var f = FX[name];
      if (f) { try { f(); } catch (e) {} }
    },
    toggle: function () {
      this.muted = !this.muted;
      try { localStorage.setItem(KEY, this.muted ? "1" : "0"); } catch (e) {}
      return this.muted;
    },
    isMuted: function () { return this.muted; },
  };

  window.Sound = Sound;
})();
