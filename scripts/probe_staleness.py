"""Measure hybrid-table cross-session staleness for the §7 read-after-write.

Reproduces the exact hazard: connection A writes ``json_state``, then
connection B reads it back, as fast as the client can issue the read. Counts
how often B observes the PRE-write value, and records the gap between A's
write returning and B's read being issued.

Also measures the same loop with READ_LATEST_WRITES=true to confirm the
documented mitigation actually closes it.

Throwaway diagnostic; not part of the app.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sea_of_colours.snowpark import backend  # noqa: E402

SCHEMA = "SOC_HACKATHON_DB.SEA_OF_COLOURS"
SID = "__staleness_probe__"
ROUNDS = 40

WRITE = (
    "MERGE INTO {t} tgt USING (SELECT ? AS session_id, PARSE_JSON(?) AS js) s "
    "ON tgt.session_id = s.session_id "
    "WHEN MATCHED THEN UPDATE SET json_state = s.js "
    "WHEN NOT MATCHED THEN INSERT (session_id, width, height, seed, day, phase, json_state) "
    "VALUES (s.session_id, 1, 1, 1, 1, 'probe', s.js)"
)
READ = "SELECT json_state:marker::INT m FROM {t} WHERE session_id = ?"


def run(cur_a, cur_b, table: str, label: str, payload_kb: int) -> None:
    filler = "x" * (payload_kb * 1024)
    stale = 0
    gaps: list[float] = []
    lags: list[float] = []
    for i in range(1, ROUNDS + 1):
        blob = json.dumps({"marker": i, "filler": filler})
        cur_a.execute(WRITE.format(t=table), [SID, blob])
        cur_a.fetchall()
        t_written = time.perf_counter()
        cur_b.execute(READ.format(t=table), [SID])
        t_issued = time.perf_counter()
        got = cur_b.fetchone()
        seen = int(got[0]) if got and got[0] is not None else -1
        gaps.append((t_issued - t_written) * 1000)
        if seen != i:
            stale += 1
            # keep reading until it catches up, to measure the real lag
            deadline = time.perf_counter() + 5.0
            while time.perf_counter() < deadline:
                cur_b.execute(READ.format(t=table), [SID])
                r = cur_b.fetchone()
                if r and int(r[0]) == i:
                    break
            lags.append((time.perf_counter() - t_written) * 1000)
    print(f"  {label:44s} stale {stale:2d}/{ROUNDS}  "
          f"median gap write->read {statistics.median(gaps):6.1f}ms  "
          f"min gap {min(gaps):6.1f}ms"
          + (f"  catch-up {statistics.median(lags):.0f}ms" if lags else ""))


def main() -> None:
    sess_a = backend._build_snowpark_session()
    sess_b = backend._build_snowpark_session()
    a = sess_a._conn._conn.cursor()  # noqa: SLF001
    b = sess_b._conn._conn.cursor()  # noqa: SLF001
    for c in (a, b):
        c.execute("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
    print(f"two independent Snowpark sessions, {ROUNDS} rounds each\n")

    ht = f"{SCHEMA}.SOC_GAME_SESSION"
    fdn = f"{SCHEMA}.SOC_GAME_SESSION_FDN_BAK"

    print("HYBRID (live table) — the new risk:")
    run(a, b, ht, "375KB payload, default consistency", 375)
    run(a, b, ht, "  4KB payload, default consistency", 4)
    b.execute("ALTER SESSION SET READ_LATEST_WRITES = true")
    run(a, b, ht, "375KB payload, READ_LATEST_WRITES=true", 375)
    b.execute("ALTER SESSION SET READ_LATEST_WRITES = false")

    print("\nFDN (pre-swap backup) — the old behaviour, for comparison:")
    run(a, b, fdn, "375KB payload", 375)

    for c, t in ((a, ht), (a, fdn)):
        c.execute(f"DELETE FROM {t} WHERE session_id = ?", [SID])
    print("\ncleaned up probe row")


if __name__ == "__main__":
    main()
