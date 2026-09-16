"""A/B the real save_session / load_session statements: FDN vs hybrid table.

Uses the EXACT SQL from snowpark_store.save_session, a real json_state
payload pulled from the account, and interleaves the variants so warehouse
warm-up cannot bias one side. Throwaway; not part of the app.
"""
from __future__ import annotations

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sea_of_colours.snowpark import backend  # noqa: E402

SCHEMA = "SOC_HACKATHON_DB.SEA_OF_COLOURS"
ROUNDS = 6

MERGE_SQL = """MERGE INTO {t} t USING (SELECT ? AS session_id, ? AS season_name,
 ?::INT AS width, ?::INT AS height, ?::INT AS seed, ?::INT AS day,
 ? AS phase, PARSE_JSON(?) AS json_state) s ON t.session_id = s.session_id
 WHEN MATCHED THEN UPDATE SET season_name = s.season_name, width = s.width,
 height = s.height, seed = s.seed, day = s.day, phase = s.phase,
 json_state = s.json_state, last_touched_at = CURRENT_TIMESTAMP()
 WHEN NOT MATCHED THEN INSERT (session_id, season_name, width, height, seed,
 day, phase, json_state) VALUES (s.session_id, s.season_name, s.width,
 s.height, s.seed, s.day, s.phase, s.json_state)"""

UPDATE_SQL = """UPDATE {t} SET season_name = ?, width = ?::INT, height = ?::INT,
 seed = ?::INT, day = ?::INT, phase = ?, json_state = PARSE_JSON(?),
 last_touched_at = CURRENT_TIMESTAMP() WHERE session_id = ?"""

SELECT_BOUND = ("SELECT session_id, season_name, width, height, seed, day, "
                "phase, json_state FROM {t} WHERE session_id = ?")
SELECT_INLINE = ("SELECT session_id, season_name, width, height, seed, day, "
                 "phase, json_state FROM {t} WHERE session_id = '{sid}'")


def main() -> None:
    sess = backend._build_snowpark_session()
    conn = sess._conn._conn  # noqa: SLF001
    cur = conn.cursor()
    cur.execute("ALTER SESSION SET USE_CACHED_RESULT = FALSE")

    cur.execute(
        f"SELECT session_id, season_name, width, height, seed, day, phase, "
        f"to_json(json_state) FROM {SCHEMA}.SOC_GAME_SESSION "
        f"WHERE json_state IS NOT NULL "
        f"ORDER BY length(to_json(json_state)) DESC LIMIT 1"
    )
    sid, season, w, h, seed, day, phase, blob = cur.fetchone()
    print(f"payload: session {sid[:12]}  json_state {len(blob)/1024:.0f} KB\n")

    merge_binds = [sid, season, int(w), int(h), int(seed), int(day), phase, blob]
    upd_binds = [season, int(w), int(h), int(seed), int(day), phase, blob, sid]

    variants = [
        ("MERGE  FDN    (app's current statement)",
         MERGE_SQL.format(t=f"{SCHEMA}.SOC_GAME_SESSION"), merge_binds),
        ("MERGE  HYBRID", MERGE_SQL.format(t=f"{SCHEMA}.SOC_GAME_SESSION_HT"),
         merge_binds),
        ("UPDATE FDN", UPDATE_SQL.format(t=f"{SCHEMA}.SOC_GAME_SESSION"),
         upd_binds),
        ("UPDATE HYBRID (docs-recommended shape)",
         UPDATE_SQL.format(t=f"{SCHEMA}.SOC_GAME_SESSION_HT"), upd_binds),
        ("SELECT FDN    bound",
         SELECT_BOUND.format(t=f"{SCHEMA}.SOC_GAME_SESSION"), [sid]),
        ("SELECT HYBRID bound",
         SELECT_BOUND.format(t=f"{SCHEMA}.SOC_GAME_SESSION_HT"), [sid]),
        ("SELECT FDN    inlined (app's current)",
         SELECT_INLINE.format(t=f"{SCHEMA}.SOC_GAME_SESSION", sid=sid), None),
        ("SELECT HYBRID inlined",
         SELECT_INLINE.format(t=f"{SCHEMA}.SOC_GAME_SESSION_HT", sid=sid), None),
    ]

    # one warm-up of each, then interleaved rounds
    for _, sql, binds in variants:
        cur.execute(sql, binds)
        cur.fetchall()

    walls: dict[str, list[float]] = {label: [] for label, _, _ in variants}
    ids: dict[str, list[str]] = {label: [] for label, _, _ in variants}
    for _ in range(ROUNDS):
        for label, sql, binds in variants:
            t0 = time.perf_counter()
            cur.execute(sql, binds)
            cur.fetchall()
            walls[label].append((time.perf_counter() - t0) * 1000)
            ids[label].append(cur.sfqid)

    allids = [i for v in ids.values() for i in v]
    cur.execute(
        "SELECT query_id, total_elapsed_time, compilation_time, "
        "execution_time FROM TABLE(information_schema.query_history("
        "result_limit=>10000)) WHERE query_id IN ("
        + ",".join(f"'{i}'" for i in allids) + ")"
    )
    srv = {r[0]: r for r in cur.fetchall()}

    print(f"{'variant':40s} {'client':>9} {'server':>8} {'compile':>8} {'exec':>7}")
    print("-" * 76)
    for label, _, _ in variants:
        got = [srv[i] for i in ids[label] if i in srv]
        med = (lambda k: statistics.median([g[k] or 0 for g in got])) if got else None
        c = statistics.median(walls[label])
        if got:
            print(f"{label:40s} {c:8.0f}ms {med(1):7.0f}ms "
                  f"{med(2):7.0f}ms {med(3):6.0f}ms")
        else:
            print(f"{label:40s} {c:8.0f}ms   (no history)")


if __name__ == "__main__":
    main()
