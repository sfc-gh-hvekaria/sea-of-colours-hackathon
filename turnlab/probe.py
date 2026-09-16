"""Drive the turn lab in a real browser, end to end.

The lab is a launcher plus one restored control, and almost everything
it shows belongs to the ordinary game UI. So the failures worth catching
are wiring failures: a launcher that lands on a session the app will not
open, an invoke button that never appears because the lab flag did not
survive the redirect, a plan overlay that paints nothing, or — the one
that started all this — a frozen turn that quietly runs itself to the
end of the season the moment the page loads.

    python turnlab/probe.py [--url http://127.0.0.1:8022/lab]
"""

from __future__ import annotations

import argparse

DEFAULT_URL = "http://127.0.0.1:8022/lab"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--p1", default="red_harvest")
    ap.add_argument("--p2", default="red_harvest_lite")
    ap.add_argument("--rack", default="both", help="ordnance for p1")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    console: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1500, "height": 1050})
        page.on("console", lambda m: (
            console.append(f"{m.type}: {m.text}") if m.type == "error" else None
        ))
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        # ── the launcher ──────────────────────────────────────────────
        page.goto(args.url, wait_until="networkidle")
        nights = page.locator("#boardpick option")
        print(f"nights offered : {[nights.nth(i).inner_text() for i in range(nights.count())]}")
        if nights.count() == 0:
            problems.append("no frozen boards offered")
            browser.close()
            return _report(problems, console)

        print(f"night selected : {page.locator('#boardpick').input_value()}")

        # Hand p1 a rack. The boards are frozen out of ordinary seasons
        # and carry no ordnance, so without this there is no way to ask
        # the question the redsign nights exist for — does the agent play
        # differently armed? Asserted below on the orders it comes back
        # with, because arming that silently does nothing looks exactly
        # like arming that works.
        rack = page.locator('.rack-row select[data-seat="p1"]')
        if rack.count():
            rack.select_option(args.rack)
            print(f"p1 ordnance    : {args.rack}")
        else:
            problems.append("the launcher offered no ordnance picker")

        page.click("#go")
        # The app normalises "/" to "/play", so match on the query only.
        page.wait_for_url("**session=**", timeout=30_000)
        print(f"landed on      : {page.url.split('/')[-1][:90]}")
        if "lab=" not in page.url:
            problems.append("the lab flag did not survive the redirect")

        # ── the real game UI, on a frozen turn ────────────────────────
        page.wait_for_timeout(2000)
        # The seat picker only shows when the link did not bind a seat, so
        # this is opportunistic — clicking a picker that is not there is a
        # 30-second timeout, not a failure.
        seat = page.locator("button").filter(has_text="WHITE")
        if seat.count() and seat.first.is_visible():
            seat.first.click()
        page.wait_for_function(
            "() => document.querySelectorAll('#map-player .cell').length > 500",
            timeout=60_000,
        )
        print(f"cells painted  : {page.locator('#map-player .cell').count()}")

        # The lab is one night. ORBIT buys for a tomorrow this board does
        # not have and INTEL reads a history it does not carry, so both
        # are tabs onto nothing.
        tabs = page.evaluate("""() => [...document.querySelectorAll('.cc-tab')]
            .filter((t) => t.offsetParent !== null)
            .map((t) => t.dataset.ccTab)""")
        print(f"tabs offered   : {tabs}")
        for gone in ("orbit", "intel"):
            if gone in tabs:
                problems.append(f"the {gone.upper()} tab is still in the lab")

        day_before = page.evaluate(
            "() => document.querySelector('.cc-clock-day')?.textContent?.trim() || ''"
        )
        print(f"day on arrival : {day_before}")

        # ── the restored invoke control ───────────────────────────────
        # It lives in the AGENT tab, which is hidden until selected.
        page.keyboard.press("7")
        page.wait_for_timeout(600)
        try:
            page.wait_for_selector("#lab-invoke", timeout=20_000)
        except Exception:
            problems.append("the invoke control never appeared in the AGENT tab")
            page.screenshot(path="/tmp/lab_probe.png", full_page=False)
            browser.close()
            return _report(problems, console)

        rows = page.locator(".cc-lab-seat")
        print(f"seat blocks    : {rows.count()}")
        if rows.count() < 2:
            problems.append("both seats should have an invoke block")

        # The drawer clips at its edge rather than scrolling, so a control
        # that overflows does not move — it disappears, and the seat cannot
        # be cast at all. Worth asserting because both ways of causing it
        # are invisible from the markup: a grid item defaults to
        # min-width:auto and so cannot shrink below the widest <select>
        # option, and adding a child to the panel shifts its 1fr row onto
        # the seat tabs, which then stretch into full-height slabs.
        layout = page.evaluate("""() => {
            const p = document.querySelector('#cc-panel-log').getBoundingClientRect();
            const clipped = [];
            for (const el of document.querySelectorAll('#lab-invoke button, #lab-invoke select')) {
                // Hidden controls measure as a zero rect at the origin,
                // which reads as clipped off the left edge of everything.
                if (el.offsetParent === null) continue;
                const r = el.getBoundingClientRect();
                if (r.x < p.x - 1 || r.x + r.width > p.x + p.width + 1)
                    clipped.push((el.textContent || el.className).trim().slice(0, 24));
            }
            const shown = (sel) => {
                const el = document.querySelector(sel);
                return !!el && el.offsetParent !== null;
            };
            return {
                clipped,
                box: document.querySelector('#lab-invoke')?.getBoundingClientRect().height || 0,
                history: ['#agent-seat-tabs', '#agent-feed',
                          '#agent-cards-read'].filter(shown),
            };
        }""")
        print(f"panel layout   : invoke box {layout['box']:.0f}px")
        if layout["clipped"]:
            problems.append(f"clipped outside the panel: {layout['clipped']}")
        if layout["box"] < 150:
            problems.append(
                f"the invoke box was squashed to {layout['box']:.0f}px — the "
                f"panel's grid rows are off by one again"
            )
        # The season feed replays whoever held the seat in the run this
        # board was cut from. Beside the take you just asked for, that
        # reads as this agent's thinking, and it is not.
        if layout["history"]:
            problems.append(
                f"the season's own thinking is back in the pane: {layout['history']}"
            )

        for seat_id, agent in (("p1", args.p1), ("p2", args.p2)):
            block = page.locator(f'.cc-lab-seat[data-seat="{seat_id}"]')
            if not block.count():
                problems.append(f"no invoke block for {seat_id}")
                continue
            block.locator("select").select_option(agent)
            print(f"\ninvoking {seat_id} = {agent} …")
            block.locator(".cc-lab-invoke-btn").click()
            try:
                block.locator(".cc-lab-accept").wait_for(timeout=240_000)
            except Exception as exc:
                err = block.locator(".cc-lab-err")
                detail = err.inner_text() if err.count() else str(exc)
                problems.append(f"{seat_id} never returned a plan: {detail}")
                continue

            orders = block.locator(".cc-lab-moves li")
            print(f"   {block.locator('.cc-lab-meta').inner_text()}")
            for j in range(min(2, orders.count())):
                print(f"        {orders.nth(j).inner_text()}")
            if orders.count() == 0:
                problems.append(f"{seat_id} planned no orders")

            # Did the armed seat reach for the rack? Reported always,
            # asserted only for a deterministic agent.
            #
            # A rack written to the wrong key would leave any agent
            # planning an ordinary night, which is the silent failure
            # worth catching — but an LLM declining to fire is a real
            # answer, and the lab exists to show you answers like that
            # rather than to mark them wrong. So the heuristics carry
            # the assertion and the model seats carry an observation.
            if seat_id == "p1" and args.rack != "empty":
                text = block.locator(".cc-lab-moves").inner_text().lower()
                fired = [w for w in ("emp", "chaff") if w in text]
                print(f"   reached for the rack: {fired or 'NOTHING'}")
                if not fired and not _is_llm(page, args.p1):
                    problems.append(
                        f"p1 ({args.p1}) was handed '{args.rack}' but planned "
                        f"no weapon order — the rack did not reach the agent"
                    )

            # Drawn by the ORDINARY planned-orders renderer, so what is
            # counted here is what a human's queued policy puts on the
            # board: numbered path badges, probe/weapon markers, blast
            # footprints. A bespoke lab layer is exactly what this
            # replaced, so the probe counts the real classes — it would
            # otherwise pass happily against a reintroduced one.
            drawn = _plan_marks(page)
            print(f"   plan on the board: {_fmt_marks(drawn)}")
            if not sum(_drawn(drawn).values()):
                problems.append(f"{seat_id}'s plan drew nothing on the board")
            if not drawn["badges"]:
                problems.append(
                    f"{seat_id}'s plan drew no path badges — the harvester "
                    f"chain is the part a human queue always shows"
                )

            # Asking must not play the turn. Only ACCEPT may do that.
            pending = page.evaluate(
                """async (s) => {
                    const r = await fetch(`/api/game/${s}/status`);
                    const j = await r.json();
                    return j.pending || {};
                }""",
                page.url.split("session=")[1].split("&")[0],
            )
            if pending.get(seat_id):
                problems.append(
                    f"{seat_id} was committed by INVOKE — ACCEPT means nothing"
                )
            else:
                print("   committed nothing yet (correct)")

            # Both plans exist only in the window between the second
            # INVOKE and the first ACCEPT — a resolved night clears them,
            # which made a tail-end check of this vacuously pass.
            if seat_id == "p2":
                _check_hover_swap(page, problems)

            # The divergence view, on p1 only — one is enough to prove the
            # wiring, and each one opens a tab.
            if seat_id == "p1":
                _check_divergence(page, block, problems)

            block.locator(".cc-lab-accept").click()
            page.wait_for_timeout(2500)
            print(f"   accepted → {block.locator('.cc-lab-meta').inner_text()}")

            # A seat that has played cannot be cast again. Its orders are
            # with the engine, so a second invocation would either be
            # ignored or land on a night that has already resolved.
            invoke = block.locator(".cc-lab-invoke-btn")
            print(f"   invoke now: {invoke.inner_text()} "
                  f"(disabled={invoke.is_disabled()})")
            if not invoke.is_disabled():
                problems.append(f"{seat_id} can still be re-invoked after ACCEPT")
            if "SELECTED" not in invoke.inner_text().upper():
                problems.append(
                    f"{seat_id}'s button does not read as chosen after ACCEPT"
                )

        # ── the night, resolved by the engine ─────────────────────────
        # Checked on the invoke result, not on the day badge. The badge
        # keeps reading night 2 while night 2 animates, which is correct
        # and made an earlier version of this probe report a failure that
        # was not one.
        try:
            page.wait_for_function(
                """() => document.body.innerText.includes("night resolved")""",
                timeout=120_000,
            )
            print("\nnight           : resolved by the engine")
        except Exception:
            problems.append("the night never resolved after both seats played")

        # The runaway that started all this: a frozen turn whose seats
        # were bots played itself to season_complete on page load. One
        # night, and one only.
        page.wait_for_timeout(6000)
        day_after = page.evaluate(
            "() => document.querySelector('.cc-clock-day')?.textContent?.trim() || ''"
        )
        print(f"day badge       : {day_before} → {day_after}")
        if "04/" in day_after or "complete" in day_after.lower():
            problems.append(f"the turn ran away past its own night: {day_after}")

        # The per-seat fog views: live play hides them, a lab clone must
        # not — both seats are yours, so there is no fog to leak.
        views = page.locator("#cc-replay-view-row .cc-replay-view-btn--seat")
        obs = page.locator("#replay-view-obs")
        print(f"\nfog views      : {views.count()} seat(s) + OBS visible={obs.is_visible()}")
        if views.count() < 2:
            problems.append("cannot switch between the two seats' vision")
        if not obs.is_visible():
            problems.append("the observer view is hidden on a lab turn")

        page.screenshot(path="/tmp/lab_probe.png", full_page=False)
        print("\nscreenshot: /tmp/lab_probe.png")

        _check_spent_and_reset(page, problems)
        browser.close()

    return _report(problems, console)


def _plan_marks(page) -> dict:
    """What the planned-orders overlay currently has on the board."""
    return page.evaluate("""() => ({
        badges: document.querySelectorAll('.harvester-path-badge').length,
        legs: document.querySelectorAll('.harvester-path-leg').length,
        markers: document.querySelectorAll('.cc-order-marker').length,
        aoe: document.querySelectorAll('.aoe-shape').length,
        // Which seat the panel BELIEVES is on the board. Printed beside
        // the counts because the two disagreeing is the whole bug class
        // here: a hover that registers but never reaches the renderer.
        seat: (document.querySelector('.cc-lab-seat--shown') || {})
            .dataset?.seat || '-',
    })""")


def _fmt_marks(m: dict) -> str:
    return (f"[{m['seat']}] {m['badges']} badge(s) · {m['legs']} leg(s) · "
            f"{m['markers']} marker(s) · {m['aoe']} footprint(s)")


def _drawn(m: dict) -> dict:
    """Just the counts — ``seat`` is a label, not a tally."""
    return {k: v for k, v in m.items() if k != "seat"}


def _check_hover_swap(page, problems: list[str]) -> None:
    """Hovering a seat lays its plan over the board; the other's leaves.

    One plan at a time is the whole design. Both at once cannot work on
    this renderer — each seat's path is numbered from hour one, so two
    overlapping chains put two badges reading "3" in the same square
    with nothing to say whose is whose.
    """
    def hover(sel):
        page.locator(sel).first.hover()
        page.wait_for_timeout(350)
        return _plan_marks(page)

    p1 = hover('.cc-lab-seat[data-seat="p1"]')
    p2 = hover('.cc-lab-seat[data-seat="p2"]')
    off = hover("#lab-invoke .cc-block-head")
    print(f"   hover p1: {_fmt_marks(p1)}")
    print(f"   hover p2: {_fmt_marks(p2)}")
    print(f"   hover head (clear): {_fmt_marks(off)}")

    if not sum(_drawn(p1).values()) or not sum(_drawn(p2).values()):
        problems.append("hovering a seat does not lay its plan on the board")
    if _drawn(p1) == _drawn(p2):
        problems.append(
            "both seats draw the same overlay — the hover is not swapping"
        )
    if sum(_drawn(off).values()):
        problems.append("the plan will not come off the board")

    shown = page.evaluate(
        """() => [...document.querySelectorAll('.cc-lab-seat--shown')]
            .map((el) => el.dataset.seat)"""
    )
    if shown:
        problems.append(f"a seat still reads as ON BOARD with nothing drawn: {shown}")

    # Put one back, so the accept path runs with a plan up.
    hover('.cc-lab-seat[data-seat="p2"]')


def _check_spent_and_reset(page, problems: list[str]) -> None:
    """The night is over: the board is a record, and RESET is the way out.

    Left open, a lab turn stops being one frozen decision and quietly
    becomes a little season — you get a second night on top of the first,
    and whatever you were comparing is gone. So the lockout is the
    feature, and RESET has to genuinely rebuild the turn rather than
    just wipe the panel: the engine has resolved this night and there is
    no un-resolving it.
    """
    page.keyboard.press("7")
    page.wait_for_timeout(500)
    try:
        page.wait_for_selector("#lab-reset:not([hidden])", timeout=20_000)
    except Exception:
        problems.append("no RESET offered after the night resolved")
        return

    spent = page.evaluate("""() => ({
        // The proposals stop being proposals when the night resolves —
        // the board now shows what happened, so a seat still flagged
        // ON BOARD is pointing at an overlay that is no longer there.
        shown: [...document.querySelectorAll('.cc-lab-seat--shown')]
            .map((el) => el.dataset.seat),
        drawn: document.querySelectorAll(
            '.harvester-path-badge, .cc-order-marker, .aoe-shape').length,
        clipped: (() => {
            const p = document.querySelector('#cc-panel-log').getBoundingClientRect();
            const r = document.querySelector(
                '#lab-reset .cc-lab-reset-btn').getBoundingClientRect();
            return r.x < p.x - 1 || r.x + r.width > p.x + p.width + 1;
        })(),
        body: document.body.classList.contains('cc-lab-spent'),
        live: [...document.querySelectorAll('.cc-lab-invoke-btn')]
            .filter((b) => !b.disabled).length,
        orders: getComputedStyle(
            document.querySelector('#cc-panel-orders')).pointerEvents,
    })""")
    print(f"\nafter praxis    : locked={spent['body']} · "
          f"{spent['live']} invoke button(s) still live · "
          f"orders pointer-events={spent['orders']} · "
          f"{spent['drawn']} plan mark(s) left · shown={spent['shown'] or '-'}")
    if spent["shown"] or spent["drawn"]:
        problems.append(
            f"the proposal overlay outlived the night it proposed "
            f"({spent['drawn']} mark(s), shown={spent['shown']})"
        )
    if not spent["body"]:
        problems.append("the board did not lock after the night resolved")
    if spent["live"]:
        problems.append(f"{spent['live']} seat(s) can still be cast on a spent turn")
    if spent["orders"] != "none":
        problems.append("orders can still be typed into a resolved night")
    if spent["clipped"]:
        problems.append("the RESET button hangs outside the panel — unclickable")

    before = page.url.split("session=")[1].split("&")[0]
    page.click("#lab-reset .cc-lab-reset-btn")
    try:
        page.wait_for_function(
            "(old) => !location.href.includes(old)", arg=before, timeout=60_000
        )
    except Exception:
        problems.append("RESET did not open a fresh copy of the board")
        return
    after = page.url.split("session=")[1].split("&")[0]
    print(f"reset           : {before} → {after}")

    # Same board, same racks — otherwise "replay the turn" is replaying a
    # different one, which is worse than not offering it.
    for carried in ("lab=", "arms="):
        if carried not in page.url:
            problems.append(f"RESET dropped {carried.strip('=')} from the turn")

    page.wait_for_function(
        "() => document.querySelectorAll('#map-player .cell').length > 500",
        timeout=60_000,
    )
    page.keyboard.press("7")
    try:
        page.wait_for_selector("#lab-invoke", timeout=20_000)
    except Exception:
        problems.append("the fresh turn has no invoke control")
        return
    fresh = page.evaluate("""() => ({
        live: [...document.querySelectorAll('.cc-lab-invoke-btn')]
            .filter((b) => !b.disabled).length,
        takes: document.querySelectorAll('.cc-lab-moves li').length,
        plans: document.querySelectorAll(
            '.harvester-path-badge, .cc-order-marker, .aoe-shape').length,
        locked: document.body.classList.contains('cc-lab-spent'),
    })""")
    print(f"fresh turn      : {fresh['live']} seat(s) castable · "
          f"{fresh['takes']} carried-over order(s) · {fresh['plans']} plan cell(s)")
    if fresh["locked"]:
        problems.append("the fresh turn came up already locked")
    if fresh["live"] < 2:
        problems.append("RESET did not give both seats back")
    if fresh["takes"] or fresh["plans"]:
        problems.append("RESET left the old plans on the board")
    page.screenshot(path="/tmp/lab_reset.png")
    print("screenshot: /tmp/lab_reset.png")


def _is_llm(page, agent: str) -> bool:
    """Does this seat's agent think with a model? Asked of the roster."""
    return bool(page.evaluate(
        """async (want) => {
            const r = await fetch('/api/lab/agents');
            const j = await r.json();
            const hit = (j.agents || []).find((a) => a.value === want);
            return !!(hit && hit.needs_llm);
        }""",
        agent,
    ))


def _check_divergence(page, block, problems: list[str]) -> None:
    """Open [ vs V12 ] and confirm the page built itself.

    The failure this exists for is quiet: the page is written into a tab
    by its opener rather than fetched, so a broken hand-off gives a blank
    white tab and no error anywhere the main window can see.
    """
    btn = block.locator(".cc-lab-vs")
    if not btn.count():
        problems.append("no [ vs V12 ] button on the take")
        return

    errs: list[str] = []
    try:
        with page.expect_popup(timeout=60_000) as popup:
            btn.click()
        tab = popup.value
    except Exception as exc:
        problems.append(f"the divergence view never opened: {exc}")
        return

    tab.on("pageerror", lambda e: errs.append(str(e)))
    try:
        tab.wait_for_selector("section", timeout=45_000)
        tab.wait_for_timeout(900)
    except Exception:
        problems.append("the divergence view opened but rendered nothing")
        tab.close()
        return

    facts = tab.evaluate("""() => ({
        // A red row above a green one whose text is character-for-
        // character the same is the view's worst failure mode: the diff
        // is telling the truth (the line moved, or the edit is two
        // digits deep in six hundred characters) and reads as a lie. So
        // every changed pair has to carry its own evidence — either a
        // mark on the token that changed, or the moved note.
        liars: [...document.querySelectorAll('.row.del')].filter((d) => {
            const a = d.nextElementSibling;
            if (!a || !a.classList.contains('add')) return false;
            const txt = (el) => el.querySelector('.txt').textContent
                .replace(/whitespace only|moved, text unchanged/g, '');
            if (txt(d) !== txt(a)) return false;
            return !(d.classList.contains('moved')
                     || d.querySelector('mark') || a.querySelector('mark'));
        }).length,
        marks: document.querySelectorAll('.row mark').length,
        moved: document.querySelectorAll('.row.moved').length,
        sections: document.querySelectorAll('section').length,
        nav: document.querySelectorAll('nav a').length,
        orders: document.querySelectorAll('table.orders tbody tr').length,
        differ: document.querySelectorAll('tr.differ').length,
        objobj: document.body.innerText.includes('[object Object]'),
        head: (document.querySelector('#head b') || {}).textContent || '',
        titles: [...document.querySelectorAll('section h2')].map(
            (h) => h.childNodes[0].textContent.trim()),
        // A piece that came through unchanged is folded into the
        // roll-up rather than given a section, so the breakdown has to
        // be read from both places.
        pieces: [...document.querySelectorAll('section h2, .quiet summary b')]
            .map((h) => h.childNodes[0].textContent.trim()),
        quiet: document.querySelectorAll('.quiet details').length,
        menurows: document.querySelectorAll('table.menu tbody tr:not(.grp)').length,
        menugroups: document.querySelectorAll('table.menu tr.grp').length,
        badges: [...document.querySelectorAll('section')].map((s) => {
            const h = s.querySelector('h2');
            const b = h.querySelector('.badge');
            return b
                ? `${h.textContent.replace(b.textContent, '').trim()}=${b.textContent}`
                : null;
        }).filter(Boolean),
    })""")
    print(f"   divergence view: {facts['sections']} sections · "
          f"{facts['orders']} order rows · {facts['differ']} differing · "
          f"{facts['menurows']} menu rows in {facts['menugroups']} group(s) · "
          f"{len(facts['pieces'])} prompt piece(s), {facts['quiet']} folded")
    for badge in facts["badges"]:
        print(f"        {badge}")
    if facts["sections"] < 4:
        problems.append(f"divergence view has only {facts['sections']} sections")

    # The prompt is shown in the three labelled sections the agent is
    # actually handed. The failure mode is the splitter over-matching:
    # the redsign playbook draws bare rules of sixty equals signs, and
    # reading those as banners shatters SECTION 1 into eight nameless
    # fragments and buries the structure it exists to show.
    for want in ("SECTION 1", "SECTION 2", "SECTION 3"):
        if not any(want in t for t in facts["pieces"]):
            problems.append(f"the prompt was not broken out by {want}")
    stray = [t for t in facts["pieces"] if "untitled" in t or t == "(opening)"]
    if stray:
        problems.append(f"the prompt splitter produced nameless pieces: {stray[:4]}")
    # The menu is the other half of the decision, and it has to arrive
    # as options rather than as a wall of prose.
    if not facts["menurows"]:
        problems.append("the option menu was not broken out into plays")
    if not facts["menugroups"]:
        problems.append("the option menu lost its group headings")
    # Diffing the raw passes whole showed the same 55KB three times.
    if any("prompt" == t.strip().lower() for t in facts["titles"]):
        problems.append("the whole-pass prompt dump is back")
    if not facts["orders"]:
        problems.append("divergence view showed no orders")
    if facts["nav"] != facts["sections"]:
        problems.append("divergence nav does not match its sections")
    if facts["objobj"]:
        problems.append("divergence view rendered a raw [object Object]")
    if not facts["head"]:
        problems.append("divergence view has no header")
    print(f"   marks: {facts['marks']} highlighted token(s) · "
          f"{facts['moved']} moved row(s)")
    if facts["liars"]:
        problems.append(
            f"{facts['liars']} changed pair(s) render as identical text — the "
            f"view is reporting a change it cannot show"
        )

    _check_no_phantom_mover(tab, facts, problems)
    _check_raw_view(tab, problems)

    for e in errs:
        problems.append(f"divergence pageerror: {e}")
    tab.screenshot(path="/tmp/lab_diff.png")
    tab.close()


def _check_no_phantom_mover(tab, facts, problems: list[str]) -> None:
    """V12 makes two model calls, so the view must not imply three.

    The mover prompt is BUILT every turn and recorded in the audit
    whether or not it is sent — on a normal night the packager compiles
    the plan and no third model ever reads it. Showing it beside THINK
    and PLAN invented a pass V12 does not have, and sent a reader
    looking for the bug in a prompt that had no effect on anything.
    """
    pipeline = tab.evaluate(
        """() => (document.querySelector('#head .meta') || {}).innerText || ''"""
    )
    # Same rule the page uses. A heuristic reports `packager_used: false`
    # truthfully — it has no packager — so the flag on its own reads every
    # bot as having run a mover, which is the confusion under test.
    ran = tab.evaluate("""() => {
        const d = window.__LAB_DIFF__ || {};
        const f = (t) => !!((t || {}).prompts || {}).mover
            && ((t || {}).plan || {}).packager_used === false;
        return {base: f(d.base), mine: f(d.mine)};
    }""")
    mover_mentions = [t for t in facts["pieces"] if "mover" in t.lower()]
    print(f"   pipeline: {pipeline.splitlines()[-1] if pipeline else '(none)'}")
    print(f"   mover ran: {ran} · sections naming it: {len(mover_mentions)}")

    if "packager" not in pipeline:
        problems.append(
            "the header does not say how the moves were produced — the "
            "packager is most of the pipeline and is invisible otherwise"
        )
    if not (ran["base"] or ran["mine"]) and mover_mentions:
        problems.append(
            f"the mover prompt is shown though no mover ran: {mover_mentions[:3]}"
        )


def _check_raw_view(tab, problems: list[str]) -> None:
    """The other question: both takes in full, one scroll, changes marked."""
    tab.locator('.modes button[data-mode="raw"]').click()
    tab.wait_for_timeout(700)

    raw = tab.evaluate("""() => ({
        rows: document.querySelectorAll('#raw .row').length,
        folds: document.querySelectorAll('#raw .row.fold').length,
        same: document.querySelectorAll('#raw .row.same').length,
        changed: document.querySelectorAll(
            '#raw .row.add:not(.moved), #raw .row.del:not(.moved)').length,
        sections: document.querySelectorAll('section').length,
        // The banners the flattened document is assembled from. Without
        // them the raw view is an undifferentiated 3,000-line wall.
        marks: [...document.querySelectorAll('#raw .row .txt')]
            .map((e) => e.textContent)
            .filter((t) => t.includes('##########')).length,
    })""")
    print(f"   raw view: {raw['rows']} row(s) · {raw['changed']} changed · "
          f"{raw['same']} agreeing · {raw['folds']} fold(s) · "
          f"{raw['marks']} section banner(s)")

    if raw["sections"] != 1:
        problems.append(
            f"raw view rendered {raw['sections']} sections — it is meant to be one"
        )
    if raw["rows"] < 200:
        problems.append(f"raw view is only {raw['rows']} rows — it is not the whole take")
    if raw["folds"]:
        problems.append(
            f"raw view folded {raw['folds']} run(s) away by default — "
            f"'everything' has to mean everything"
        )
    if not raw["marks"]:
        problems.append("the raw document has no section banners to navigate by")

    # …and the fold is still there when you ask for it.
    tab.locator('.modes button[data-ctx="near"]').click()
    tab.wait_for_timeout(500)
    near = tab.evaluate("""() => ({
        rows: document.querySelectorAll('#raw .row').length,
        folds: document.querySelectorAll('#raw .row.fold').length,
    })""")
    print(f"   raw, changes only: {near['rows']} row(s) · {near['folds']} fold(s)")
    # Only meaningful when there is agreement to fold. Against a
    # heuristic there is almost none — it has no prompts, so nearly every
    # row is a deletion — and demanding a smaller page would be demanding
    # the view hide something.
    if raw["same"] > 30 and near["rows"] >= raw["rows"]:
        problems.append(
            f"'changes only' did not shrink a raw view with {raw['same']} "
            f"agreeing rows in it"
        )

    tab.screenshot(path="/tmp/lab_diff_raw.png", full_page=False)
    tab.locator('.modes button[data-mode="structured"]').click()
    tab.wait_for_timeout(500)
    if not tab.locator("#menu").count():
        problems.append("switching back to STRUCTURED did not rebuild the page")


def _report(problems: list[str], console: list[str]) -> int:
    if console:
        print("\nconsole errors:")
        for line in console[:10]:
            print("   ", line)
    if problems:
        print("\nPROBLEMS")
        for p in problems:
            print("   -", p)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
