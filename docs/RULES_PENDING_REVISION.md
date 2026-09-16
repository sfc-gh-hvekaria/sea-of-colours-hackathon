# Rules pending revision

Design backlog for **rule changes** we want to make (distinct from
`OUTSTANDING_ISSUES.md`, which tracks *bugs*). These are deliberate mechanic
revisions — each entry captures the current behaviour, why it's unsatisfying,
and the proposed direction. Nothing here is implemented yet; treat it as the
staging area before a change lands in `RULEBOOK.md`.

Logged 2026-07-10.

Status legend: 💡 idea · 📐 designing · 🔨 ready to build.

---

## 1. ✅ SHIPPED — Terminal "Final Refinery" run (RULEBOOK §4.3.1)

**Decision (2026-07-13).** The tier-weighted fire-sale idea was **rejected**.
The fire-sale stays exactly as-is: **`0.5 × purity`, no tier weight** (§4.7).
Shipping *is* the game — if you don't ship, you lose. Instead of rewarding
hoarding, we make the **last night's refine actually shippable** so leftover
BLUE has a real, aggressive use on the buzzer.

**What shipped.** On the **terminal settlement orbit only** (`final_orbit`,
`day = cap + 1`) the orbit turns into a special **Final Refinery**:

- **No caps.** The 3-action-per-orbit cap and the 5-parcel-per-refine cap are
  both lifted — a seat can fold its whole vault in one turn.
- **Same-turn cascade.** A refined output is immediately eligible to be
  re-refined *and* shipped in the same pass (normal orbits still forbid this:
  trace→vein→mass→ship in one turn is a terminal-only privilege).
- **Refine → ship in the same turn.** Because refined parcels get their ids at
  settlement time, there's a new **auto ship** bid
  (`ship_catapult{auto:true, credits, count}`) that bids on the seat's best-N
  RED parcels by tier-weighted value *after* the refinery folds — so the fresh
  mass parcels actually make the catapult.
- **One-click convenience.** `refine_cascade{target_tier}` folds every eligible
  RED parcel up to `vein` or `mass` (default `mass`) in a single action —
  paired with an auto ship, the whole terminal turn is two clicks.
- **BLUE is the only limiter.** Each fold still pays `REFINE_BLUE_COST`; when
  the blue runs out, the cascade stops. Everything else (fire-sale on the
  leftovers, green penalty, catapult draft) is unchanged.

**Why this shape.** It preserves "shipping is the game" (the fire-sale is still
a loss), but converts the previously-dead last-orbit refine into a high-tension
economic choice: burn all your blue to concentrate unshipped RED into a few
high-multiplier mass parcels and ship them, vs. hold the blue / ship raw.

**Status — fully shipped (v1.7).** Engine + parser + resolver
(`orbit_resolver.py`, `session.apply_refine_cascade`), tests
(`tests/test_final_refinery.py`), RULEBOOK §4.3.1 + v1.7 changelog, agent view
(`view.py` surfaces `orbit.final_orbit` / lifted caps / `final_refinery`),
heuristic + pilot_v2 orbit doctrine (auto cascade + auto-ship on the terminal
orbit), the human UI (lifted queue/refine caps + "Refine everything up" /
"Ship best (auto)" banner buttons), and all agent prompt specs
(`AGENT_PROMPT_SAMPLES.md`, `soc_create_agent_{pilot_v2,pilot_v3,scratch,tactical}.sql`).
Deployed Cortex agents pick up the new grammar on their next redeploy; the
heuristic + pilot_v2 harness already use it locally.

**Cross-ref.** RULEBOOK §4.2, §4.3, §4.3.1, §4.4, §4.7;
`sea_of_colours/game/orbit_resolver.py`, `session.apply_refine_cascade`.

---

## 2. 💡 Green catapult redesign — it doesn't play interestingly

**Current behaviour (§3.4, §4.7).** GREEN is dead weight: it fills hold/vault
space, costs **−100 per parcel** at season end, and can't score. The only
sanctioned disposal is the **solar jettison / GREEN catapult** — a 12-slot
shared lane where a House submits `solar_jettison{green_parcels, red_fuel}`,
paying RED purity as fuel (slot cost diminishes by global slot position, RED
fuel forfeit whether or not it clears). **There is no reward beyond avoiding the
penalty.**

**Why it's uninteresting.**
- It's a **pure tax with a chore attached** — the "decision" is almost always
  "flush everything you can afford," so there's no meaningful trade-off and bots
  effectively never engage with it in an interesting way.
- The shared-lane discount rewards *coincidental* co-flushing rather than any
  real interaction between Houses.
- Paying RED (your scoring substance) to delete GREEN (a nuisance) is a flat
  value-destruction step with no upside, upside timing, or counterplay.

**Proposed directions (pick / combine).**
- **Make green a resource, not just a tax.** e.g. green becomes fuel/feedstock
  for *something* (repairs, EMP/chaff manufacture, a refine catalyst) so
  carrying some is a real hedge rather than strictly bad.
- **Interactive lane.** Turn the shared catapult into genuine contention —
  limited slots that Houses compete for, so flushing is a timing/priority game
  rather than "everyone flushes."
- **Green as a weapon.** Since green is "poison you can inflict," lean into
  §3.4's tactical-punishment framing — a way to *deposit* green onto contested
  ground or a rival's path rather than only jettison it.
- **Rebalance the tax** so the penalty + disposal cost create a smooth "how much
  green is worth carrying" curve instead of a cliff.

**Validation note.** We currently can't even *see* the green lane exercised —
bots rarely jettison, so we need a **green-heavy season** to observe the
mechanic before/after any change (`OUTSTANDING_ISSUES` green-visibility item;
`scripts/green_diag.py`).

**Open questions.**
- What is green *for* in the fun version — hazard, resource, or both?
- Does the RED-as-fuel cost survive, or is that the core of what's unfun?

**Cross-ref.** RULEBOOK §3.4, §4.7; `scripts/green_diag.py`.

---

## 3. ✅ SHIPPED — Redsign, a pure-RED beacon revealed to all when spotted (RULEBOOK §4.11)

**Shipped v1.8 (2026-07-13); presentation reworked v1.9 (2026-07-14).** Built as
designed below, with these resolved decisions:

- **Trigger:** any seat's probe *or* harvester vision includes a cell with
  `tile == RED and purity == 255` not already discovered.
- **Visibility:** minted on first sighting, immediately public to every house,
  fog-independent, surfaced top-level as `redsign` (agent/player/observer/replay
  views). Regions carry the exact praxis `hour` of discovery so replay lights
  the sign at the precise frame, not the top of the night.
- **Persist, don't fade** — matches blue-sign; stays after the seam is
  harvested. Depletion is inferred from rival activity.
- **Threshold:** pure RED (255) only.
- **Attribution:** anonymous.
- **Rendering (v1.9):** rough/jittered smear (not the exact square) painted as a
  red **unicode fill** (`░░`/`▒▒`) that pulses uniformly and shows **only on
  fog** — it recedes on live/echo tiles where the terrain is already visible. A
  one-shot filled **red rhombus** discovery burst (a diamond echo of the
  probe-landing explosion) fires once at discovery, delayed to land after the
  probe/harvester that revealed the seam. The Orbital Observations entry is an
  in-line red text line (`RED SIGN — pure RED seam near …`), not a boxed banner.
  (The original v1.8 centre beacon + radial-gradient overlay were removed.)
- **Engine:** `GameSession.redsign` + `redsign_seen`, minted in
  `_pulse_vision_intel`; `_mint_redsign_region` stamps `day` + `hour`
  (`simulator.py` sets `_redsign_hour` before each pulse). Persisted in
  `to_dict`/`from_dict`. Tests in `tests/test_redsign.py`.

Open question left for balancing later: interaction with the RED catapult draft
(§4.4) — a public pure seam concentrates bids; watch for runaway-leader risk.

Original proposal preserved below for context.

---

**Inspiration.** BLUE already emits a **blue-sign** (§4.10): a fuzzy radiative
smear, computed once at season birth, that **every House sees from orbit
regardless of fog** — it points roughly at blue pockets without revealing exact
squares or purity, and never fades. It's a great "shared knowledge" tension
mechanic.

**Idea.** Add a **redsign**: a beacon that marks **pure RED** (purity 255 /
solid `pure`) seams — but with a crucial twist that makes it a *race* rather
than a birthright.

**Key difference from blue-sign.** Blue-sign is **always on from birth**.
Redsign is **discovery-triggered**: it appears **only once any probe (any
House's) observes a pure RED square**, and from that moment it is visible to
**all players**. So spotting a solid seam **broadcasts it to your rivals** —
scouting a jackpot tips everyone off.

**Why it could be very fun.**
- Creates a genuine dilemma: probe aggressively to find pure red early, but
  every pure square you illuminate becomes public → a land-grab race.
- Rewards *acting* on your own sighting fast (you saw it first; you have a head
  start before rivals reposition to the sign).
- Adds a public "hot seam" layer to the map that evolves during play, unlike the
  static blue-sign — reading rival movement toward a fresh redsign becomes real
  intel.

**Proposed shape (mirroring blue-sign's data model).**
- Trigger: any seat's probe vision (or harvester LOS) includes a cell with
  `tile == RED and purity == 255`.
- On first sighting, mint a redsign region (`{id, center, cells:[[x,y,intensity],…]}`)
  and surface it **top-level in every seat's view**, fog-independent — same
  plumbing as `GameSession.blue_sign` (persist in `to_dict`/`from_dict`,
  recompute for legacy saves, paint in the frontend).
- Roughness: like blue-sign, deliberately off-centre/fuzzy so it points *near*
  the pure seam, not at the exact square.

**Open questions.**
- **Fade vs. persist.** Blue-sign never fades. Should a redsign **fade/clear
  once the pure square is harvested** (it converts RED→GREEN, so it's genuinely
  gone)? Fading makes it a live "is it still there?" signal; persisting keeps
  parity with blue-sign and forces rivals to infer depletion from activity.
- **Trigger threshold.** Only solid `pure` (255), or also high `mass` tiers? Pure
  is the cleanest "jackpot" signal.
- **Attribution.** Should the sign hint *who* spotted it, or stay anonymous?
  Anonymous is closer to blue-sign and probably better for tension.
- **Interaction with the RED catapult draft (§4.4).** A public pure seam will
  concentrate bids — is that a feature (contested jackpot) or a runaway-leader
  risk?

**Cross-ref.** RULEBOOK §4.10 (blue-sign), §2.2 (RED purity bands),
`GameSession.blue_sign` in `sea_of_colours/game/session.py`,
`tests/test_blue_sign.py`.

---

### How to promote an item

When a rule here is agreed and built: write it into `RULEBOOK.md` (with a
changelog entry), add/refresh tests, and delete or mark the entry here as
shipped with a pointer to the RULEBOOK section.
