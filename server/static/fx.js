/* Sea of Colours — machine-read FX overlay, drawn on #fx-cv.
 *
 * v1.37 — replaces the PCB circuit-trace overlay. That one filled the
 * whole viewport with white box-drawing glyphs at a uniform density,
 * which did three things wrong: it competed with the title for
 * attention, it greyed out the hero video underneath, and because it
 * never rested there was nothing for the eye to settle on.
 *
 * This one has a budget. At any moment most of the screen is empty; the
 * marks that do exist are acid on black, clustered, and short-lived,
 * and they sweep rather than shimmer. Five layers, cheapest first:
 *
 *   1. dither     a fixed 4x4 Bayer wash, very faint, never redrawn
 *   2. registers  columns of hex/dec that retype a digit at a time
 *   3. sweep      a scan bar that lights the dither as it passes
 *   4. bursts     short-lived blocks of glyphs, like a packet arriving
 *   5. storm      the periodic pixelation — see below
 *
 * Layers 1-4 keep out of the middle of the screen (see `inKeepout`) so
 * nothing ever crawls over the wordmark. Layer 5 deliberately does not.
 *
 * ── the storm (v1.38) ──────────────────────────────────────────────
 * Every ~14.3 seconds a front of thick blocks sweeps across the hero
 * until the screen is a single flat colour, holds there, and then the
 * same front runs across a second time lifting the blocks off, back to
 * the video. The wavefront is from `docs/scatter_fx.html` — a diagonal
 * sweep jittered by value noise, so it is ragged rather than a wipe.
 * What is new is the mark: that prototype drew 5px characters, this
 * draws blocks big enough to actually occlude.
 *
 * ONE COLOUR, ONE TONE, START TO FINISH. Every mark is the same flat
 * dimmed accent and it never changes value, so the middle of the cycle
 * is literally one colour edge to edge. Which colour depends on where
 * the page's own red → green → blue cycle has got to (see `--acid` in
 * landing.css); this file samples it rather than holding a copy, so the
 * pixelation is never a different colour from the page under it. Three
 * earlier cuts all
 * failed the same way, by putting more than one value on screen at
 * once: a fixed palette with red, green and blue all mixed in, which
 * read as confetti; a
 * per-block sample of the video, which gave hundreds of near-identical
 * greens; and a drain to black, which made the field a different colour
 * every second.
 *
 * What DOES vary is coverage, and it varies through the board's own
 * ramp: ░ ▒ ▓ █, the same glyphs `catTierGlyph` draws a cell's purity
 * tier with (v1.38). A cell the front has just reached comes up light,
 * thickens through medium and dark as the front moves past it, and only
 * then goes solid — so the sweep arrives as a stipple that densifies
 * rather than as blocks switching on. Each cell climbs at its OWN rate,
 * which is the difference between a ragged shading front and four clean
 * stripes of ░▒▓█ marching across in formation.
 *
 * The grid is measured off the font rather than chosen: `measureCell`
 * asks for █'s ink box and uses exactly that, so a screen of █ is
 * seamless and the hold really is one flat colour. Get that wrong by a
 * pixel and the grid reappears as hairlines. The solid level is drawn
 * as a rect, not a glyph — visually identical, and it cannot round.
 *
 * Staying dark is also what keeps the page legible with no help from
 * anywhere else: acid type sits on a dark field the whole way through,
 * so there is no ink inversion and no stroke around the letters. Both
 * of those existed only to survive a bright field, and both are gone.
 *
 * It also rests — nine of every fifteen seconds it is not there at all.
 *
 * To revert: comment out the canvas + script tags in landing.html and
 * the #fx-cv rule in landing.css. This file can stay untouched. To keep
 * the quiet layers but drop the pixelation, set STORM.enabled = false.
 */
(function () {
  "use strict";

  var cv = document.getElementById("fx-cv");
  if (!cv) return;
  var ctx = cv.getContext("2d", { alpha: true });
  if (!ctx) return;

  var reduce = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* The page's accent, and the turn order it moves through. This file
   * owns WHEN it changes because the change is a storm event — it has
   * to land on the frame the screen is fully covered, and the storm
   * clock is here. It publishes to CSS rather than keeping the colour
   * to itself, so the wordmark, the chrome and the pixel field are
   * never three different colours.
   *
   * `data-accent` rides along so the swatch legend can key off an
   * attribute instead of trying to match a colour string. */
  var ACCENTS = [
    { key: "r", css: "#ff2b2b" },
    { key: "g", css: "#00ff88" },
    { key: "b", css: "#3b5bff" },
  ];
  var accent = 0;

  function applyAccent() {
    var a = ACCENTS[accent];
    document.documentElement.style.setProperty("--acid", a.css);
    document.documentElement.setAttribute("data-accent", a.key);
  }

  /* Even though this file sets the colour, it READS it back rather than
   * using what it set: CSS tweens `--acid` over half a second, so the
   * computed value is the only thing that knows where the hand-over has
   * got to. Taking the target directly would snap the pixel field to
   * the new colour while the copy on top of it was still crossing.
   *
   * Sampled on an interval, not per frame — `getComputedStyle` forces a
   * style resolve. 60ms gives about nine steps across the transition,
   * which is under the banding threshold on a flat field. */
  var ACID = "255,43,43";
  var themeRead = -1e9;

  function readTheme(now) {
    if (now - themeRead < 60) return;
    themeRead = now;
    var v = getComputedStyle(document.documentElement)
      .getPropertyValue("--acid");
    var m = /(-?[\d.]+)[,\s]+(-?[\d.]+)[,\s]+(-?[\d.]+)/.exec(v || "");
    if (m) ACID = (+m[1] | 0) + "," + (+m[2] | 0) + "," + (+m[3] | 0);
  }

  function acidPart(i) {
    return parseInt(ACID.split(",")[i], 10) || 0;
  }
  var CELL = 13;                 // glyph grid pitch, px
  var FONT = '10px "JetBrains Mono", ui-monospace, monospace';
  var GLYPHS = "0123456789ABCDEF<>[]{}/\\|+-=*#%$@:;.·×÷▪▫░▒▓■□◆◇○●";
  var HEX = "0123456789ABCDEF";

  var W = 0, H = 0, dpr = 1, cols = 0, rows = 0;
  var ditherMask = null;         // white Bayer wash, built once per size
  var dither = null;             // the same wash tinted to the accent
  var ditherTint = "";

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth;
    H = window.innerHeight;
    cv.width = Math.floor(W * dpr);
    cv.height = Math.floor(H * dpr);
    cv.style.width = W + "px";
    cv.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cols = Math.ceil(W / CELL);
    rows = Math.ceil(H / CELL);
    buildDither();
    seedRegisters();
    buildStorm();
  }

  /* The keep-out box the wordmark occupies. Everything that draws asks
   * this first, which is the whole reason the overlay can be busy at
   * the edges without ever making the title hard to read. */
  function inKeepout(x, y) {
    var cx = W / 2, cy = H * 0.46;
    return Math.abs(x - cx) < W * 0.34 && Math.abs(y - cy) < H * 0.24;
  }

  /* ── 1. dither wash ─────────────────────────────────────────────── */
  // A 4x4 ordered Bayer matrix, painted once into an offscreen canvas
  // and then blitted. Drawing ~20k dots a frame is what made the old
  // overlay expensive; drawing them once and moving the result is free.
  var BAYER = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
  ];

  /* Laid down once per size in WHITE and then re-tinted by compositing,
   * because the accent moves and this does not: the mask is ~144k
   * one-pixel fills at 1440x900, far too many to redraw eight times a
   * second while a colour transition is running. `source-in` keeps the
   * mask's alpha and swaps only the hue, so a re-tint is two full-canvas
   * operations instead. */
  function buildDither() {
    var c = document.createElement("canvas");
    c.width = Math.max(1, Math.floor(W));
    c.height = Math.max(1, Math.floor(H));
    var g = c.getContext("2d");
    if (!g) { ditherMask = dither = null; return; }
    g.fillStyle = "rgba(255,255,255,0.05)";
    var step = 3;
    for (var y = 0; y < H; y += step) {
      for (var x = 0; x < W; x += step) {
        var bx = (x / step) & 3, by = (y / step) & 3;
        // Density falls off toward the centre so the wash frames the
        // stage instead of veiling it.
        var dx = (x - W / 2) / (W / 2), dy = (y - H / 2) / (H / 2);
        var edge = Math.min(1, (dx * dx + dy * dy) * 0.9);
        if (BAYER[by][bx] / 16 > edge * 0.55) continue;
        g.fillRect(x, y, 1, 1);
      }
    }
    ditherMask = c;
    dither = document.createElement("canvas");
    dither.width = c.width;
    dither.height = c.height;
    ditherTint = "";
    tintDither();
  }

  function tintDither() {
    if (!ditherMask || !dither || ditherTint === ACID) return;
    ditherTint = ACID;
    var g = dither.getContext("2d");
    if (!g) return;
    g.clearRect(0, 0, dither.width, dither.height);
    g.globalCompositeOperation = "source-over";
    g.drawImage(ditherMask, 0, 0);
    g.globalCompositeOperation = "source-in";
    g.fillStyle = "rgb(" + ACID + ")";
    g.fillRect(0, 0, dither.width, dither.height);
    g.globalCompositeOperation = "source-over";
  }

  /* ── 2. registers ───────────────────────────────────────────────── */
  // Short columns of hex that retype one character at a time. They sit
  // in the outer margins only, and each holds its value for a while
  // before changing, so they read as instrumentation and not as rain.
  var registers = [];

  function seedRegisters() {
    registers = [];
    if (reduce) return;
    var n = Math.max(4, Math.round(W / 190));
    for (var i = 0; i < n; i++) {
      var left = Math.random() < 0.5;
      registers.push({
        x: left
          ? 34 + Math.random() * Math.max(10, W * 0.17)
          : W - 34 - Math.random() * Math.max(10, W * 0.17),
        y: 90 + Math.random() * Math.max(10, H - 220),
        len: 3 + Math.floor(Math.random() * 5),
        val: [],
        next: Math.random() * 1400,
      });
      var reg = registers[registers.length - 1];
      for (var k = 0; k < reg.len; k++) {
        reg.val.push(
          HEX[(Math.random() * 16) | 0] + HEX[(Math.random() * 16) | 0]
          + HEX[(Math.random() * 16) | 0] + HEX[(Math.random() * 16) | 0]);
      }
    }
  }

  function drawRegisters(dt) {
    ctx.font = FONT;
    ctx.textBaseline = "top";
    for (var i = 0; i < registers.length; i++) {
      var r = registers[i];
      r.next -= dt;
      if (r.next <= 0) {
        var k = (Math.random() * r.len) | 0;
        r.val[k] = HEX[(Math.random() * 16) | 0] + HEX[(Math.random() * 16) | 0]
          + HEX[(Math.random() * 16) | 0] + HEX[(Math.random() * 16) | 0];
        r.next = 260 + Math.random() * 1500;
      }
      for (var j = 0; j < r.len; j++) {
        var y = r.y + j * (CELL + 1);
        if (y > H - 40 || inKeepout(r.x, y)) continue;
        ctx.fillStyle = "rgba(" + ACID + "," + (j === 0 ? 0.34 : 0.17) + ")";
        ctx.fillText(r.val[j], r.x, y);
      }
    }
  }

  /* ── 3. scan sweep ──────────────────────────────────────────────── */
  // One bar, travelling down, lighting the dither it crosses. This is
  // the layer doing most of the work: a slow global motion reads as a
  // machine reading the page, and it costs two fillRects.
  var sweep = { y: -200, speed: 0.19 };

  function drawSweep(dt) {
    if (reduce) return;
    sweep.y += sweep.speed * dt;
    if (sweep.y > H + 220) sweep.y = -220;
    var band = 150;
    var g = ctx.createLinearGradient(0, sweep.y - band, 0, sweep.y + band);
    g.addColorStop(0, "rgba(" + ACID + ",0)");
    g.addColorStop(0.5, "rgba(" + ACID + ",0.055)");
    g.addColorStop(1, "rgba(" + ACID + ",0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, sweep.y - band, W, band * 2);
    ctx.fillStyle = "rgba(" + ACID + ",0.16)";
    ctx.fillRect(0, sweep.y, W, 1);
  }

  /* ── 4. bursts ──────────────────────────────────────────────────── */
  // A packet arriving: a small block of glyphs that types on, holds,
  // and decays. Capped hard, because the failure mode of this kind of
  // effect is always "more of it".
  var bursts = [];
  var MAX_BURSTS = 5;
  var nextBurst = 600;

  function spawnBurst() {
    var w = 3 + ((Math.random() * 7) | 0);
    var h = 1 + ((Math.random() * 4) | 0);
    var x, y, tries = 0;
    do {
      x = 40 + Math.random() * Math.max(10, W - 80 - w * CELL);
      y = 70 + Math.random() * Math.max(10, H - 170 - h * CELL);
      tries++;
    } while (tries < 14 && (inKeepout(x, y)
      || inKeepout(x + w * CELL, y + h * CELL)));
    if (inKeepout(x, y)) return;
    var chars = [];
    for (var i = 0; i < w * h; i++) {
      chars.push(GLYPHS[(Math.random() * GLYPHS.length) | 0]);
    }
    bursts.push({ x: x, y: y, w: w, h: h, chars: chars, age: 0,
                  life: 900 + Math.random() * 1400 });
  }

  function drawBursts(dt) {
    if (reduce) return;
    nextBurst -= dt;
    if (nextBurst <= 0 && bursts.length < MAX_BURSTS) {
      spawnBurst();
      nextBurst = 420 + Math.random() * 1500;
    }
    ctx.font = FONT;
    ctx.textBaseline = "top";
    for (var i = bursts.length - 1; i >= 0; i--) {
      var b = bursts[i];
      b.age += dt;
      if (b.age > b.life) { bursts.splice(i, 1); continue; }
      var t = b.age / b.life;
      // type on over the first quarter, hold, fade over the last third
      var shown = t < 0.25
        ? Math.ceil((t / 0.25) * b.chars.length)
        : b.chars.length;
      var alpha = t > 0.66 ? (1 - (t - 0.66) / 0.34) : 1;
      for (var k = 0; k < shown; k++) {
        var cxk = b.x + (k % b.w) * CELL;
        var cyk = b.y + ((k / b.w) | 0) * CELL;
        if (inKeepout(cxk, cyk)) continue;
        ctx.fillStyle = "rgba(" + ACID + "," + (0.4 * alpha).toFixed(3) + ")";
        ctx.fillText(b.chars[k], cxk, cyk);
      }
      // a bracket around the packet, which is what makes it look framed
      // by something rather than sprayed on
      ctx.strokeStyle = "rgba(" + ACID + "," + (0.2 * alpha).toFixed(3) + ")";
      ctx.lineWidth = 1;
      var bw = b.w * CELL, bh = b.h * CELL;
      if (!inKeepout(b.x, b.y)) {
        ctx.beginPath();
        ctx.moveTo(b.x - 4, b.y + 5); ctx.lineTo(b.x - 4, b.y - 4);
        ctx.lineTo(b.x + 5, b.y - 4);
        ctx.moveTo(b.x + bw + 3, b.y + bh + 2);
        ctx.lineTo(b.x + bw + 3, b.y + bh + 7);
        ctx.lineTo(b.x + bw - 6, b.y + bh + 7);
        ctx.stroke();
      }
    }
  }

  /* ── 5. the storm ───────────────────────────────────────────────── */

  var STORM = {
    enabled: true,
    size: 52,         // block glyph size in px — "thick", not a mosaic
    tone: 0.45,       // the single tone, as a fraction of full accent
    // Two long dwells with a short move between them. The page should
    // spend most of its time being one of two things — the video, or a
    // flat colour — rather than permanently in transit between them.
    rest: 7000,       // dwell: video alone
    fill: 1900,       // the front sweeps in, block by block
    hold: 2500,       // dwell: one flat colour, whole screen
    melt: 1900,       // the same front sweeps out, video underneath
    turnAt: 0.3,      // where in the hold the accent changes over
  };

  var storm = { cells: [], t: 0, cols: 0, rows: 0, turned: false };

  /* The board's purity ramp, minus the solid — that level is a rect. */
  var SHADES = ["\u2591", "\u2592", "\u2593"];
  var FULL = "\u2588";
  var SHADE_FONT = '"JetBrains Mono", ui-monospace, "Cascadia Code", monospace';

  /* How long a cell takes to climb from first light to solid, as a
   * fraction of the whole sweep. The spread between these two is what
   * makes the front ragged in DEPTH as well as in shape: a cell with a
   * short ramp snaps to solid almost behind the edge, one with a long
   * ramp is still at ▒ when its neighbours are full. */
  var RAMP_MIN = 0.09, RAMP_MAX = 0.30;

  var cellW = 0, cellH = 0, cellBase = 0;
  var shadeMask = null, shadeAtlas = null, shadeTint = "";

  /* Take the grid from the font, not from a round number. █ is defined
   * to fill its character cell, so its ink box IS the tile that tiles;
   * anything else leaves seams at full coverage, and the whole point of
   * the hold is that there is no grid left to see. Fonts disagree about
   * block metrics, so this has to be asked at runtime rather than
   * assumed from the size. */
  function measureCell() {
    ctx.save();
    ctx.font = STORM.size + "px " + SHADE_FONT;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    var m = ctx.measureText(FULL);
    var asc = m.actualBoundingBoxAscent;
    var desc = m.actualBoundingBoxDescent;
    ctx.restore();
    // Fall back to typical monospace proportions if the browser will
    // not give us an ink box.
    if (!(asc > 0) && !(desc > 0)) { asc = STORM.size * 0.8; desc = 0; }
    cellW = Math.max(6, Math.round(m.width || STORM.size * 0.6));
    cellH = Math.max(6, Math.round(asc + desc));
    cellBase = Math.round(asc);
  }

  /* One tile per shade, white, built once per size and re-tinted by
   * compositing — same trick as the dither wash, and for the same
   * reason: the accent moves and glyph rasterisation is far too slow to
   * repeat for ~800 cells every frame. Blitting a tile is not. */
  function buildShades() {
    if (!cellW || !cellH) { shadeMask = shadeAtlas = null; return; }
    var c = document.createElement("canvas");
    c.width = cellW * SHADES.length;
    c.height = cellH;
    var g = c.getContext("2d");
    if (!g) { shadeMask = shadeAtlas = null; return; }
    g.font = STORM.size + "px " + SHADE_FONT;
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    g.fillStyle = "#fff";
    for (var i = 0; i < SHADES.length; i++) {
      g.fillText(SHADES[i], i * cellW, cellBase);
    }
    shadeMask = c;
    shadeAtlas = document.createElement("canvas");
    shadeAtlas.width = c.width;
    shadeAtlas.height = c.height;
    shadeTint = "";
    tintShades();
  }

  function fieldColour() {
    var k = STORM.tone;
    return "rgb(" + ((acidPart(0) * k) | 0) + ","
      + ((acidPart(1) * k) | 0) + "," + ((acidPart(2) * k) | 0) + ")";
  }

  function tintShades() {
    if (!shadeMask || !shadeAtlas || shadeTint === ACID) return;
    shadeTint = ACID;
    var g = shadeAtlas.getContext("2d");
    if (!g) return;
    g.clearRect(0, 0, shadeAtlas.width, shadeAtlas.height);
    g.globalCompositeOperation = "source-over";
    g.drawImage(shadeMask, 0, 0);
    g.globalCompositeOperation = "source-in";
    g.fillStyle = fieldColour();
    g.fillRect(0, 0, shadeAtlas.width, shadeAtlas.height);
    g.globalCompositeOperation = "source-over";
  }

  /* Bilinear value noise on a coarse lattice. Straight from
   * `docs/scatter_fx.html` — it is what turns a diagonal wipe into a
   * ragged wavefront, and a wipe is the one thing this must not look
   * like. */
  function valueNoise(cols, rows, scale) {
    var cg = Math.ceil(cols / scale) + 2, rg = Math.ceil(rows / scale) + 2;
    var r = [], y, x;
    for (y = 0; y < rg; y++) {
      r[y] = [];
      for (x = 0; x < cg; x++) r[y][x] = Math.random();
    }
    var out = [];
    for (y = 0; y < rows; y++) {
      out[y] = [];
      for (x = 0; x < cols; x++) {
        var fx = x / scale, fy = y / scale;
        var x0 = Math.floor(fx), y0 = Math.floor(fy);
        var tx = fx - x0, ty = fy - y0;
        var top = r[y0][x0] + (r[y0][x0 + 1] - r[y0][x0]) * tx;
        var bot = r[y0 + 1][x0] + (r[y0 + 1][x0 + 1] - r[y0 + 1][x0]) * tx;
        out[y][x] = top + (bot - top) * ty;
      }
    }
    return out;
  }

  /* Rebuilt every cycle, so no two storms sweep the same way. */
  function buildStorm() {
    measureCell();
    buildShades();
    storm.cols = Math.ceil(W / cellW);
    storm.rows = Math.ceil(H / cellH);
    if (storm.cols < 2 || storm.rows < 2) { storm.cells = []; return; }

    var timing = valueNoise(storm.cols, storm.rows,
                            Math.max(2, storm.cols / 5));
    // Which way the wavefront travels, so it is not always top-left.
    var dirX = Math.random() < 0.5 ? 1 : -1;
    var dirY = Math.random() < 0.5 ? 1 : -1;
    var wx = 0.35 + Math.random() * 0.3;

    storm.cells = [];
    for (var ry = 0; ry < storm.rows; ry++) {
      for (var cx = 0; cx < storm.cols; cx++) {
        var fx = dirX > 0 ? cx / storm.cols : 1 - cx / storm.cols;
        var fy = dirY > 0 ? ry / storm.rows : 1 - ry / storm.rows;
        var sweep = fx * wx + fy * (1 - wx);
        var t = sweep * 0.82 + (timing[ry][cx] - 0.5) * 0.26;
        var ramp = RAMP_MIN + Math.random() * (RAMP_MAX - RAMP_MIN);
        storm.cells.push({
          x: cx * cellW, y: ry * cellH,
          // Scaled into the headroom its own ramp leaves. A cell now
          // needs `ramp` more of the sweep AFTER the front reaches it
          // before it is solid, so the last cell to light has to start
          // early enough to still finish — otherwise the latest corner
          // of the screen is caught at ▓ when the hold begins, and a
          // storm that never quite closes is the one thing worse than
          // one that does.
          t: Math.max(0, Math.min(1, t)) * (1 - ramp),
          ramp: ramp,
        });
      }
    }
  }

  function drawStorm(dt) {
    if (!STORM.enabled || reduce) return;
    var S = STORM;
    var span = S.rest + S.fill + S.hold + S.melt;
    var was = storm.t;
    storm.t = (storm.t + dt) % span;
    if (storm.t < was) buildStorm();          // wrapped — new wavefront

    var e = storm.t;
    if (e < S.rest) {
      setOpacity(0.85);
      setCover(false);
      storm.turned = false;
      return;
    }
    e -= S.rest;

    // Turn the accent over a little way into the hold. Not on the first
    // frame of it: the fill's last blocks land on that frame, and a
    // colour that changes while anything is still arriving shows the
    // hand-over through the gap. A third of the way in, the screen has
    // been solid for the better part of a second, and the 520ms CSS
    // transition still finishes well before the melt starts.
    if (!storm.turned && e >= S.fill + S.hold * S.turnAt) {
      storm.turned = true;
      accent = (accent + 1) % ACCENTS.length;
      applyAccent();
    }

    // Tell the page when it is sitting on a solid field, so the copy can
    // go white for it. Nothing in the same hue as the field can be read
    // against it: a saturated colour tops out at a middling luminance,
    // so accent-on-accent is about 2:1 however the tone is set, and
    // darkening the field far enough to fix that stops it being a
    // colour. White clears 4.5:1 against all three.
    //
    // Unlike the ink inversion this replaces, the trigger here is a
    // phase boundary rather than an estimate of how much is covered —
    // the field is flat and the fill is known to have finished, so there
    // is no ambiguous middle for it to get wrong. Turned on just before
    // the screen closes and off just after it opens, so the change is
    // always hidden under a moving front.
    setCover(e >= S.fill * 0.8 && e < S.fill + S.hold + S.melt * 0.2);

    // `lo` and `hi` are the two edges of the band of the sweep that is
    // currently up. That is the whole state — there is no brightness
    // term, because the colour never changes.
    //
    // The band is what makes the storm arrive and leave in the SAME
    // direction. On the way in the leading edge advances and the
    // trailing edge stays home, so blocks accumulate behind the front.
    // On the way out the trailing edge advances instead, so the same
    // front runs across the screen a second time, this time lifting
    // blocks off and letting the video back through. An earlier cut
    // used one edge for both, which made the exit a reverse dissolve —
    // it unwound back the way it came, instead of continuing.
    // The trailing edge starts a full ramp's width OFF-SCREEN, so that
    // at the first frame of the melt even the cell at t=0 still reads as
    // solid. Started at zero it would flick straight to ▒, and the hold
    // would end with a stutter instead of a departure.
    var lo = -RAMP_MAX, hi = 1, cover = 1;
    if (e < S.fill) {
      hi = e / S.fill;
      cover = hi;
    } else if (e >= S.fill + S.hold) {
      var out = (e - S.fill - S.hold) / S.melt;
      lo = -RAMP_MAX + (1 + RAMP_MAX) * out;
      cover = 1 - out;
    }
    setOpacity(0.85 + 0.15 * Math.min(1, cover * 3));

    // One colour, one tone, and it stays that colour from the first
    // block to the last. Three earlier cuts all failed by having more
    // than one value on screen — a fixed palette that read as confetti,
    // a per-block sample of the video that gave hundreds of near-identical
    // greens, and a drain to black that meant the field was a different
    // colour every second of the cycle.
    ctx.fillStyle = fieldColour();

    // Overdrawn by a pixel so solid cells fuse. The point of the hold is
    // that the screen is ONE colour, and any seam — even a rounding
    // hairline off the device pixel ratio — turns it back into a grid.
    var fw = cellW + 1, fh = cellH + 1;
    var last = SHADES.length - 1;
    var i, c, p;

    for (i = 0; i < storm.cells.length; i++) {
      c = storm.cells[i];
      // How far this cell has climbed, in its own ramp units. The
      // leading edge drives it up, the trailing edge drives it back
      // down, and taking the lower of the two means one expression
      // covers arriving, holding and leaving.
      p = Math.min((hi - c.t) / c.ramp, (c.t - lo) / c.ramp);
      if (p <= 0) continue;
      if (p >= 1) { ctx.fillRect(c.x, c.y, fw, fh); continue; }
      if (!shadeAtlas) continue;
      var lvl = Math.min(last, (p * (last + 1)) | 0);
      ctx.drawImage(shadeAtlas, lvl * cellW, 0, cellW, cellH,
                    c.x, c.y, cellW, cellH);
    }
  }

  /* The canvas rests at 0.85 so the quiet layers sit behind the hero
   * rather than on top of it — but 15% of video bleeding through the
   * blocks is the difference between "obscured" and "gone", and gone is
   * the point of the hold. Ride the opacity up with the coverage. The
   * ramp is x3 so it is at full well before the hold, which keeps the
   * change hidden inside the fill instead of reading as the wash
   * brightening. */
  var covered = false;

  function setCover(on) {
    if (on === covered) return;
    covered = on;
    document.body.classList.toggle("fx-cover", on);
  }

  var opacity = -1;

  function setOpacity(v) {
    v = Math.round(v * 100) / 100;
    if (v === opacity) return;
    opacity = v;
    cv.style.opacity = String(v);
  }

  /* ── loop ───────────────────────────────────────────────────────── */

  var last = 0;

  function frame(now) {
    var dt = last ? Math.min(now - last, 60) : 16;
    last = now;
    readTheme(now);
    tintDither();
    tintShades();
    ctx.clearRect(0, 0, W, H);
    if (dither) ctx.drawImage(dither, 0, 0);
    drawSweep(dt);
    drawRegisters(dt);
    drawBursts(dt);
    drawStorm(dt);          // last: it is the only layer that occludes
    requestAnimationFrame(frame);
  }

  var rt = null;
  window.addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(resize, 140);
  });

  resize();
  applyAccent();
  if (reduce) {
    // One static pass: the wash and nothing that moves.
    ctx.clearRect(0, 0, W, H);
    if (dither) ctx.drawImage(dither, 0, 0);
  } else {
    requestAnimationFrame(frame);
  }
})();
