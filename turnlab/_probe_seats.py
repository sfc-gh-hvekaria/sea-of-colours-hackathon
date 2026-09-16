"""Does a three-seat board actually offer three seats?

Scratch probe for the v1.42 roster fix. A static assertion cannot answer
this: the seat rows are built from ``_activeSeatsForView()``, whose value
depends on *when* the first status poll lands, so only a real browser
running the real boot order can show whether the rebuild fires.

    python turnlab/_probe_seats.py [--url http://127.0.0.1:8023]
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8023")
    ap.add_argument("--board", default="LAB_cfa9912d_d6_p1")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    boards = json.load(urllib.request.urlopen(f"{args.url}/api/lab/boards"))
    board = next(
        (b for b in boards.get("boards", []) if b["id"] == args.board), None,
    )
    if board is None:
        print(f"FAIL: no board {args.board}")
        return 1
    print(f"board   : {board['label']}")
    print(f"players : {board.get('players')}")
    print(f"castable: {board.get('castable')}")
    if len(board.get("castable") or []) < 3:
        print("FAIL: the API is already short a seat")
        return 1

    req = urllib.request.Request(
        f"{args.url}/api/lab/open",
        data=json.dumps({"board": args.board, "seat": "p2"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    opened = json.load(urllib.request.urlopen(req))
    url = args.url.rstrip("/") + opened["url"]
    print(f"opened  : {url}")

    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1500, "height": 1050})
        page.goto(url, wait_until="networkidle")

        # The AGENT tab is where the invoke rows live.
        try:
            page.click("span.cc-tab-label:text-is('AGENT')", timeout=4000)
        except Exception as exc:
            problems.append(f"could not open the AGENT tab: {exc}")
        # The rows are rebuilt when the roster poll lands, not at boot.
        page.wait_for_timeout(3500)

        seats = page.eval_on_selector_all(
            ".cc-lab-seat", "els => els.map(e => e.dataset.seat)",
        )
        print(f"seat rows in the AGENT pane: {seats}")
        if len(seats) < 3:
            problems.append(
                f"only {len(seats)} invoke row(s): {seats} — expected p1, p2, p3"
            )
        for want in ("p1", "p2", "p3"):
            if want not in seats:
                problems.append(f"{want} has no invoke row")

        # v1.42 — no human turn is takeable here.
        shut = page.eval_on_selector_all(
            ".cc-tab[data-cc-tab]",
            "els => els.filter(e => getComputedStyle(e).display !== 'none')"
            "        .map(e => e.dataset.ccTab)",
        )
        print(f"tabs on screen: {shut}")
        for gone in ("orders", "orbit", "intel"):
            if gone in shut:
                problems.append(f"the {gone.upper()} tab is still reachable")

        active = page.eval_on_selector(
            ".cc-tab--active", "e => e.dataset.ccTab",
        )
        print(f"tab the lab opens on: {active}")
        if active != "log":
            problems.append(f"lab opened on {active!r}, not the AGENT pane")

        # postPolicy is module-private, so its refusal is pinned by a
        # static test rather than reached from here.
        page.screenshot(path="/tmp/lab_seats.png", full_page=False)
        browser.close()

    for p in problems:
        print(f"FAIL: {p}")
    print("OK — every seat is castable" if not problems else "")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
