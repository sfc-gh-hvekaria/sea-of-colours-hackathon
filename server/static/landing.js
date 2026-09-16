/* Sea of Colours — landing page behaviour.
   - Wires the Play / Multiplayer / Replay buttons.
   - Polls /api/meta/status to drive the bottom-right health badge.
   - Multiplayer starts a cloudflared quick tunnel, then sends the browser
     to the public origin's /play?new=multi so invite QR codes are public.
*/
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  // Tagline auto-cycle disabled - now controlled by button hovers

  // ── terminal glitch effect ────────────────────────────────────────
  // The three title words ("sea", "of", "colours") periodically glitch
  // into random ASCII and reconstitute.
  //
  // v1.37 — the words used to REST in saturated red/green/blue, one
  // each. On the old dark-video page that was already busy; against the
  // acid wordmark it would be three clashing colours competing with the
  // only loud element on the page. The three tile colours now arrive as
  // a chromatic split for the length of a glitch and then leave, so
  // "sea of colours" is still literally true — it is just an event
  // rather than a permanent state. Colour lives in `.is-split` (CSS),
  // not in an inline style.
  (function initGlitch() {
    var asciiChars = "!@#$%^&*()_+-=[]{}|;:,.<>?/~`";

    function splitWord(el, ms) {
      el.classList.add("is-split");
      setTimeout(function () { el.classList.remove("is-split"); }, ms);
    }

    // Glitch a single word: scramble -> reconstitute
    function glitchWord(el) {
      var original = el.dataset.word;
      var frames = 12; // total frames for scramble + reconstitute
      var scrambleFrames = 6;
      var frame = 0;

      var interval = setInterval(function () {
        frame++;
        if (frame <= scrambleFrames) {
          // Scramble phase
          var scrambled = "";
          for (var i = 0; i < original.length; i++) {
            scrambled += asciiChars[Math.floor(Math.random() * asciiChars.length)];
          }
          el.textContent = scrambled;
        } else {
          // Reconstitute phase
          var progress = (frame - scrambleFrames) / (frames - scrambleFrames);
          var charsRevealed = Math.floor(original.length * progress);
          var partial = original.slice(0, charsRevealed);
          for (var i = charsRevealed; i < original.length; i++) {
            partial += asciiChars[Math.floor(Math.random() * asciiChars.length)];
          }
          el.textContent = partial;
        }

        if (frame >= frames) {
          clearInterval(interval);
          el.textContent = original;
        }
      }, 50); // 50ms per frame
    }

    // Trigger glitch sequence: pick random word, glitch it, swap all colors
    function triggerGlitch() {
      var words = document.querySelectorAll(".glitch-word");
      if (words.length === 0) return;
      
      var target = words[Math.floor(Math.random() * words.length)];
      glitchWord(target);
      splitWord(target, 640); // 12 frames at 50ms, plus a beat to settle
    }

    // Run glitch every 1.5-3 seconds (but wait for typing to complete)
    function scheduleNext() {
      if (!systemReady) {
        setTimeout(scheduleNext, 500);
        return;
      }
      var delay = 1500 + Math.random() * 1500;
      setTimeout(function () {
        triggerGlitch();
        scheduleNext();
      }, delay);
    }
    scheduleNext();
  })();

  // ── instrument chrome ─────────────────────────────────────────────
  // Decoration, but not invented decoration: every number in the band
  // is a real constant from the rules, so the page cannot drift into
  // promising a game we do not ship. If a multiplier or a capacity
  // changes, this is a fan-out surface (see AGENTS.md).
  (function initChrome() {
    var BAND = [
      "SEA OF COLOURS",
      "AN AGENTIC GAME OF STRATEGY AND SUBTERFUGE",
      "RED IN THE VAULT IS THE WHOLE GAME",
      "TRACE \u00D70.75",
      "VEIN \u00D71.0",
      "MASS \u00D71.5",
      "PURE \u00D73.0",
      "GREEN \u2212100 PER PARCEL",
      "21 HOURS TO A NIGHT",
      "HOLD 6 \u00B7 VAULT 15",
      "PROBE RADIUS 4",
      "LIFT OR LOSE IT",
      "FOG OF WAR \u00B7 LIVE / ECHO",
      "HUMANS AND ALGORITHMS, SAME SEAT",
    ];
    var runs = document.querySelectorAll(".ticker-run");
    if (runs.length) {
      var text = BAND.join("   \u25AA   ") + "   \u25AA   ";
      runs.forEach(function (r) { r.textContent = text; });
    }

    var clock = document.getElementById("clock-utc");
    if (clock) {
      var tick = function () {
        var d = new Date();
        var p = function (n) { return String(n).padStart(2, "0"); };
        clock.textContent = p(d.getUTCHours()) + ":" + p(d.getUTCMinutes())
          + ":" + p(d.getUTCSeconds()) + "Z";
      };
      tick();
      setInterval(tick, 1000);
    }

    var reduce = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ── the barcode re-reads ──────────────────────────────────────
     * A barcode that never changes is wallpaper. This regenerates the
     * bar pattern on an interval so the mark reads as a scanner picking
     * up a new code rather than as a texture.
     *
     * Bars are built as a gradient referencing `var(--acid)` rather than
     * a resolved colour, so a new code drawn mid-transition still comes
     * out on the cycle instead of freezing whatever the accent happened
     * to be when it was generated. */
    function barcode(el) {
      var w = el.offsetWidth || 92;
      var stops = [], x = 0;
      while (x < w) {
        var bar = 1 + Math.floor(Math.random() * 3);
        var gap = 1 + Math.floor(Math.random() * 3);
        stops.push("var(--acid) " + x + "px " + (x + bar) + "px");
        stops.push("transparent " + (x + bar) + "px " + (x + bar + gap) + "px");
        x += bar + gap;
      }
      el.style.backgroundImage = "linear-gradient(to right," + stops.join(",") + ")";
    }

    var bars = document.querySelectorAll(".barcode");
    if (bars.length && !reduce) {
      bars.forEach(function (el) { barcode(el); });
      setInterval(function () {
        bars.forEach(function (el) {
          // Blank for a frame on the swap. Without it the change is a
          // silent substitution you only notice by comparing; the drop
          // out is what sells it as a re-read.
          el.style.opacity = "0.12";
          setTimeout(function () {
            barcode(el);
            el.style.opacity = "";
          }, 70);
        });
      }, 2300);
    }

    /* ── the purity ladder ─────────────────────────────────────────
     * Flicks through the four RED tiers. Every value here is the
     * engine's: the bands are `GameSession._tier_for_purity`
     * (RULEBOOK §2.2) and the multipliers are `RED_QUALITY_MULTIPLIER`,
     * so this is a fan-out surface — see AGENTS.md. The glyphs are the
     * board's own ramp from `catTierGlyph` in app.js.
     *
     * It scrambles before it settles, which is not just decoration: the
     * scramble runs the whole ░▒▓█ ramp, so you see the ladder the tier
     * sits on before you see the tier. */
    var TIERS = [
      { name: "TRACE", band: "001\u2013050", mult: "\u00D70.75", ch: "\u2591" },
      { name: "VEIN",  band: "051\u2013150", mult: "\u00D71.0",  ch: "\u2592" },
      { name: "MASS",  band: "151\u2013254", mult: "\u00D71.5",  ch: "\u2593" },
      { name: "PURE",  band: "255",          mult: "\u00D73.0",  ch: "\u2588" },
    ];
    var RAMP = "\u2591\u2592\u2593\u2588";
    var BLOCKS = 8;

    var elBlocks = document.getElementById("tier-blocks");
    var elName = document.getElementById("tier-name");
    var elBand = document.getElementById("tier-band");
    var elMult = document.getElementById("tier-mult");

    if (elBlocks && elName && elBand && elMult) {
      var ti = 0;

      var paint = function (t) {
        elBlocks.textContent = new Array(BLOCKS + 1).join(t.ch);
        elName.textContent = t.name;
        elBand.textContent = t.band;
        elMult.textContent = t.mult;
      };

      var advance = function () {
        ti = (ti + 1) % TIERS.length;
        var t = TIERS[ti];
        if (reduce) { paint(t); return; }
        var n = 0;
        var churn = setInterval(function () {
          n++;
          var s = "";
          for (var i = 0; i < BLOCKS; i++) {
            // Settle left to right, so it resolves rather than just
            // stopping.
            s += n > i + 3
              ? t.ch
              : RAMP.charAt(Math.floor(Math.random() * RAMP.length));
          }
          elBlocks.textContent = s;
          if (n === 2) { elName.textContent = t.name; elBand.textContent = t.band; }
          if (n >= BLOCKS + 3) { clearInterval(churn); paint(t); }
        }, 45);
      };

      paint(TIERS[0]);
      if (!reduce) setInterval(advance, 2800);
    }
  })();

  // ── hero video autoplay kick ──────────────────────────────────────
  // Muted autoplay is still blocked in some setups (Safari Low Power
  // Mode, strict Chrome autoplay settings, reduced-motion). Force a
  // play() and retry on the first user interaction so the loop starts
  // even when the declarative ``autoplay`` attribute is ignored.
  (function kickHeroVideo() {
    var hero = document.getElementById("hero-bg");
    if (!hero) return;
    hero.muted = true;            // property form — required for autoplay
    hero.defaultMuted = true;
    hero.setAttribute("muted", "");
    var attempt = function () {
      var p = hero.play();
      if (p && typeof p.catch === "function") p.catch(function () { /* blocked */ });
    };
    attempt();
    // Retry once the page is fully ready and on the first interaction.
    window.addEventListener("load", attempt, { once: true });
    ["pointerdown", "keydown", "touchstart"].forEach(function (evt) {
      document.addEventListener(evt, attempt, { once: true });
    });
  })();

  var btnQuick = $("btn-quick");
  var btnPlay = $("btn-play");
  var btnMulti = $("btn-multiplayer");
  var btnReplay = $("btn-replay");
  // An <a>, not a <button> — the lab is a page, so it should middle-click
  // into a new tab. It needs no click handler for that, only the hover
  // treatment the other four get.
  var btnLab = $("btn-lab");
  var hintEl = $("landing-hint");
  var badge = $("status-badge");
  var dot = $("status-dot");
  var label = $("status-label");

  var systemReady = false; // gates glitch effects until typing completes

  // ── initial typing animation ──────────────────────────────────────
  // Types out "sea_of_colours_" character by character on page load
  (function typeTitle() {
    var titleContent = document.getElementById("title-content");
    if (!titleContent) return;

    var fullText = "sea_of_colours_";
    var typed = "";
    var charIndex = 0;

    titleContent.style.opacity = "1";
    titleContent.innerHTML = '<span class="cursor-blink">_</span>'; // just cursor initially

    function typeNextChar() {
      if (charIndex < fullText.length) {
        typed += fullText[charIndex];
        charIndex++;
        
        // Rebuild with proper structure and colors as we type
        var display = "";
        var parts = typed.split("_");
        
        if (parts[0]) {
          display += '<span class="glitch-word" data-word="sea">' + parts[0] + '</span>';
        }
        if (typed.includes("_") && parts.length > 1) {
          display += "_";
          if (parts[1]) {
            display += '<span class="glitch-word" data-word="of">' + parts[1] + '</span>';
          }
        }
        if (typed.split("_").length > 2) {
          display += "_";
          if (parts[2]) {
            display += '<span class="glitch-word" data-word="colours">' + parts[2] + '</span>';
          }
        }
        if (typed.endsWith("_") && typed.length === fullText.length) {
          display += '<span class="cursor-blink">_</span>';
        } else if (!typed.endsWith("_")) {
          display += '<span class="cursor-blink">_</span>';
        }
        
        titleContent.innerHTML = display;
        setTimeout(typeNextChar, 80 + Math.random() * 60); // variable typing speed
      } else {
        // Typing complete - enable glitch effects
        systemReady = true;
      }
    }

    setTimeout(typeNextChar, 800); // pause before starting
  })();

  function setHint(text, isError) {
    if (!hintEl) return;
    hintEl.textContent = text || "";
    hintEl.classList.toggle("is-error", Boolean(isError));
  }

  // ── button hover scramble + subtitle change ───────────────────────
  // Scrambles button text on hover AND changes subtitle to description
  (function initButtonScramble() {
    var taglineEl = document.getElementById("tagline");
    if (!taglineEl) return;

    var defaultTagline = "an agentic game of strategy and subterfuge also playable by algorithms and humans";
    var isTaglineScrambling = false;
    var currentInterval = null;

    var buttonDescriptions = {
      // v1.14 — say what it costs as well as what it gives. Quick game
      // pins the memory backend, so it is fast and nothing is kept; the
      // launcher is where you choose an LLM agent and a saved season.
      "btn-quick": "learn the game in three nights against the RED_HARVEST_LITE bot — nothing is saved",
      "btn-play": "the full launcher: pick your opponent, the map, and whether the season is saved",
      "btn-multiplayer": "play with other human friends as well as agents",
      "btn-replay": "relive past games and learn new strategies",
      "btn-lab": "one frozen turn from a real season — invoke any agent on any seat and diff it against V12"
    };

    var asciiChars = "!@#$%^&*()_+-=[]{}|;:,.<>?/~`";

    function scrambleTaglineToText(targetText, accent) {
      if (isTaglineScrambling) return;
      isTaglineScrambling = true;
      if (currentInterval) clearInterval(currentInterval);

      var currentText = taglineEl.textContent;
      var frame = 0;
      var totalFrames = 24;
      var scrambleStart = 6;
      var scrambleEnd = 18;

      taglineEl.classList.toggle("is-accent", Boolean(accent));

      currentInterval = setInterval(function () {
        frame++;
        var output = "";
        var currentLen = Math.round(
          currentText.length + (targetText.length - currentText.length) * (frame / totalFrames)
        );

        for (var i = 0; i < currentLen; i++) {
          var charStart = scrambleStart + (i / currentLen) * 4;
          var charEnd = scrambleEnd + (i / currentLen) * 4;

          if (frame < charStart) {
            output += i < currentText.length ? currentText[i] : " ";
          } else if (frame >= charStart && frame < charEnd) {
            output += asciiChars[Math.floor(Math.random() * asciiChars.length)];
          } else {
            var reconProgress = (frame - charEnd) / (totalFrames - charEnd);
            if (i < targetText.length && reconProgress > Math.random() * 0.2) {
              output += targetText[i];
            } else if (i < targetText.length) {
              output += asciiChars[Math.floor(Math.random() * asciiChars.length)];
            }
          }
        }

        taglineEl.textContent = output;

        if (frame >= totalFrames) {
          clearInterval(currentInterval);
          currentInterval = null;
          taglineEl.textContent = targetText;
          isTaglineScrambling = false;
        }
      }, 35);
    }

    // Button text scramble
    var buttons = [btnQuick, btnPlay, btnMulti, btnReplay, btnLab];
    buttons.forEach(function (btn) {
      if (!btn) return;
      var textEl = btn.querySelector(".landing-btn-text");
      if (!textEl) return;

      var originalText = textEl.textContent;
      var isScrambling = false;

      btn.addEventListener("mouseenter", function () {
        // Scramble button text
        if (!isScrambling) {
          isScrambling = true;
          var frame = 0;
          var totalFrames = 8;
          var scrambleFrames = 4;

          var interval = setInterval(function () {
            frame++;
            if (frame <= scrambleFrames) {
              var scrambled = "";
              for (var i = 0; i < originalText.length; i++) {
                scrambled += asciiChars[Math.floor(Math.random() * asciiChars.length)];
              }
              textEl.textContent = scrambled;
            } else {
              var progress = (frame - scrambleFrames) / (totalFrames - scrambleFrames);
              var charsRevealed = Math.floor(originalText.length * progress);
              var partial = originalText.slice(0, charsRevealed);
              for (var i = charsRevealed; i < originalText.length; i++) {
                partial += asciiChars[Math.floor(Math.random() * asciiChars.length)];
              }
              textEl.textContent = partial;
            }

            if (frame >= totalFrames) {
              clearInterval(interval);
              textEl.textContent = originalText;
              isScrambling = false;
            }
          }, 40);
        }

        // Scramble subtitle to description
        var description = buttonDescriptions[btn.id];
        if (description) {
          scrambleTaglineToText(description, true);
        }
      });

      btn.addEventListener("mouseleave", function () {
        // Scramble back to default tagline
        scrambleTaglineToText(defaultTagline, false);
      });
    });
  })();

  // ── navigation ────────────────────────────────────────────────────
  // v1.12 — "Quick game" spawns you vs RED_HARVEST_LITE and lands on the
  // board. "Play" still goes to the launcher for anyone who wants to
  // choose seats, map size or opponent.
  // v1.32 — the three ways in. Names only: what each preset MEANS lives
  // in sea_of_colours/game/tutorial.py, so this list can go stale on its
  // copy but never on its rules.
  var TUTORIAL_CARDS = [
    {
      preset: "tut-basic",
      name: "Basic",
      blurb: "three nights, small map, no weapons \u00B7 films as you go",
    },
    {
      preset: "tut-advanced",
      name: "Advanced",
      blurb: "same board, four nights, weapons and signage switched on",
    },
    {
      preset: "quick",
      name: "Quick game",
      blurb: "full map, full rules, three nights \u2014 skip the teaching",
    },
  ];

  function openTutorialChooser() {
    if (document.getElementById("landing-tut")) return;
    var wrap = document.createElement("div");
    wrap.id = "landing-tut";
    wrap.className = "landing-tut";
    TUTORIAL_CARDS.forEach(function (card) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "landing-tut-card";
      var h = document.createElement("span");
      h.className = "landing-tut-name";
      h.textContent = card.name;
      var p = document.createElement("span");
      p.className = "landing-tut-blurb";
      p.textContent = card.blurb;
      b.appendChild(h);
      b.appendChild(p);
      b.addEventListener("click", function () {
        window.location.href = "/play?new=" + card.preset;
      });
      wrap.appendChild(b);
    });
    var nav = document.querySelector(".landing-actions");
    if (nav && nav.parentNode) nav.parentNode.insertBefore(wrap, nav.nextSibling);
    var first = wrap.querySelector("button");
    if (first) first.focus();
  }

  if (btnQuick) {
    btnQuick.addEventListener("click", openTutorialChooser);
  }
  if (btnPlay) {
    btnPlay.addEventListener("click", function () {
      window.location.href = "/play";
    });
  }
  if (btnReplay) {
    btnReplay.addEventListener("click", function () {
      window.location.href = "/watch.html?watch=1";
    });
  }
  if (btnMulti) {
    btnMulti.addEventListener("click", function () { startMultiplayer(); });
  }

  // Internet multiplayer over a public quick tunnel. We start the tunnel
  // and, as soon as a provider has published a public URL, send the browser
  // to that origin's /play?new=multi so every invite link/QR is reachable
  // from anywhere. We deliberately do NOT wait on the server-side readiness
  // probe: that probe resolves the tunnel hostname through the OS resolver,
  // which a corporate VPN/security agent can block even though the browser
  // (using its own DoH / encrypted DNS) reaches the tunnel fine. Gating on
  // it produced false "url not reachable" failures.
  //
  // v1.16 — the server may try several providers in turn (see
  // server/tunnel.py), so this can take longer than a single spawn. The
  // poll below is what makes that invisible.
  async function startMultiplayer() {
    if (!btnMulti) return;
    btnMulti.disabled = true;
    setHint("starting public tunnel\u2026");
    var data = null;
    try {
      var res = await fetch("/api/tunnel/start", { method: "POST", cache: "no-store" });
      data = await res.json();
    } catch (e) {
      data = { ok: false, error: "could not reach the server" };
    }

    if (data && data.error) {
      // No cloudflared / launch failed — fall back to same-Wi-Fi LAN play.
      setHint(data.error + " \u2014 starting a local game instead\u2026", true);
      setTimeout(function () { window.location.href = "/play?new=multi"; }, 1600);
      return;
    }

    var url = data && data.url;
    if (!url) {
      // cloudflared is still publishing — poll a few times for the URL.
      url = await pollForTunnelUrl(20);
    }

    if (url) {
      var dest = url.replace(/\/+$/, "") + "/play?new=multi";
      // Brief grace for DNS/edge propagation before the browser navigates;
      // the tunnel is up once the URL exists, this just smooths first load.
      setHint("tunnel live \u2014 opening " + url + " \u2026");
      setTimeout(function () { window.location.href = dest; }, 2500);
    } else {
      setHint("tunnel is taking a while \u2014 starting a local game instead\u2026", true);
      setTimeout(function () { window.location.href = "/play?new=multi"; }, 1200);
    }
  }

  async function pollForTunnelUrl(tries) {
    for (var i = 0; i < tries; i++) {
      await new Promise(function (r) { setTimeout(r, 750); });
      try {
        var res = await fetch("/api/tunnel/status", { cache: "no-store" });
        var s = await res.json();
        if (s && s.url) return s.url;
        if (s && s.running === false && s.installed === false) return null;
      } catch (e) { /* keep trying */ }
    }
    return null;
  }

  // ── status badge ──────────────────────────────────────────────────
  function applyStatus(s) {
    if (!badge || !dot || !label) return;
    var stores = (s && s.stores) || {};
    var state = "off";
    var text = "off";
    if (stores.snowflake === "ok") {
      state = "snowflake"; text = "snowflake";
    } else if (stores.local === "ok") {
      // Snowflake down / not yet contacted, but local persistence is live.
      state = "local"; text = "local";
    } else if (s && s.backend) {
      state = s.backend === "snowflake" ? "snowflake" : "local";
      // v1.12 — name the actual backend rather than the generic
      // "local". Since the backend is now auto-detected, "local" left
      // people unable to tell durable file storage from an in-RAM store
      // that drops every season on restart.
      text = s.backend;
    }
    badge.dataset.state = state;
    // The reason is the part that answers "where did my game go?".
    badge.title = (s && s.reason)
      ? s.reason + (s.persists === false
          ? " — sessions are lost when the server restarts"
          : "")
      : "Server status";
    var tunnelOn = s && s.tunnel && s.tunnel.running;
    label.innerHTML = text + (tunnelOn
      ? ' <span class="status-tunnel">&middot; public</span>'
      : "");
  }

  async function pollStatus() {
    try {
      var res = await fetch("/api/meta/status", { cache: "no-store" });
      applyStatus(await res.json());
    } catch (e) {
      if (badge) badge.dataset.state = "off";
      if (label) label.textContent = "off";
    }
  }
  pollStatus();
  setInterval(pollStatus, 8000);
})();
