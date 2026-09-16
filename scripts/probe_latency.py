"""Throwaway probe: decompose the 260ms client floor and the 733ms MERGE exec.

Answers R7.1/R7.2/R7.3 (is a 1-row MERGE with a 375KB VARIANT inherently
733ms?) and R7.4 (what is the 260ms made of?). Not part of the app.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sea_of_colours.snowpark import backend  # noqa: E402

SCHEMA = "SOC_PROBE_DB.PROBE"
SRC = "SOC_HACKATHON_DB.SEA_OF_COLOURS"
BLOB_KB = 375


def _blob(kb: int) -> str:
    cell = {"x": 0, "y": 0, "purity": 3, "owner": "p1", "tag": "z" * 40}
    n = max(1, (kb * 1024) // len(json.dumps(cell)))
    return json.dumps({"cells": [dict(cell, x=i) for i in range(n)]})


def net_floor(host: str, rounds: int = 5) -> None:
    print("\n=== raw network floor to", host, "===")
    tcp, tls = [], []
    for _ in range(rounds):
        t0 = time.perf_counter()
        s = socket.create_connection((host, 443), timeout=10)
        t1 = time.perf_counter()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        s = ctx.wrap_socket(s, server_hostname=host)
        t2 = time.perf_counter()
        s.close()
        tcp.append((t1 - t0) * 1000)
        tls.append((t2 - t1) * 1000)
    print(f"  TCP connect  median {statistics.median(tcp):6.1f} ms  (~1 RTT)")
    print(f"  TLS handshake median {statistics.median(tls):6.1f} ms  (~2 RTT)")


def client_floor(cur) -> None:
    print("\n=== client floor, same connection, warm ===")
    for label, sql in (
        ("SELECT 1", "SELECT 1"),
        ("SELECT 1 (again)", "SELECT 1"),
    ):
        ts = []
        for _ in range(8):
            t0 = time.perf_counter()
            cur.execute(sql)
            cur.fetchall()
            ts.append((time.perf_counter() - t0) * 1000)
        print(f"  {label:20s} median {statistics.median(ts):6.1f} ms  "
              f"min {min(ts):6.1f}  max {max(ts):6.1f}")


def timed(cur, sql, params=None, n=3):
    ids, wall = [], []
    for _ in range(n):
        t0 = time.perf_counter()
        cur.execute(sql, params)
        cur.fetchall()
        wall.append((time.perf_counter() - t0) * 1000)
        ids.append(cur.sfqid)
    return ids, statistics.median(wall)


def merge_experiments(cur) -> None:
    blob = _blob(BLOB_KB)
    small = json.dumps({"cells": []})
    print(f"\n=== MERGE / UPDATE shape experiments (blob {len(blob)/1024:.0f} KB) ===")

    cur.execute(f"CREATE OR REPLACE TABLE {SCHEMA}.P_WIDE AS "
                f"SELECT session_id, season_name, width, height, seed, day, phase, "
                f"json_state FROM {SRC}.SOC_GAME_SESSION")
    cur.execute(f"CREATE OR REPLACE TABLE {SCHEMA}.P_ONE AS "
                f"SELECT * FROM {SCHEMA}.P_WIDE LIMIT 1")
    cur.execute(f"CREATE OR REPLACE TABLE {SCHEMA}.P_SPLIT "
                f"(session_id STRING, json_state VARIANT)")
    cur.execute(f"INSERT INTO {SCHEMA}.P_SPLIT SELECT session_id, json_state "
                f"FROM {SCHEMA}.P_WIDE")
    cur.execute(f"SELECT session_id FROM {SCHEMA}.P_WIDE LIMIT 1")
    sid = cur.fetchone()[0]

    results = {}

    def run(label, sql, params):
        ids, wall = timed(cur, sql, params)
        results[label] = (ids, wall)
        print(f"  {label:44s} client {wall:7.1f} ms")

    merge = ("MERGE INTO {t} t USING (SELECT ? AS session_id, PARSE_JSON(?) AS js) s "
             "ON t.session_id = s.session_id "
             "WHEN MATCHED THEN UPDATE SET json_state = s.js "
             "WHEN NOT MATCHED THEN INSERT (session_id, json_state) "
             "VALUES (s.session_id, s.js)")

    run("MERGE P_WIDE  (32 rows, 375KB payload)",
        merge.format(t=f"{SCHEMA}.P_WIDE"), [sid, blob])
    run("MERGE P_WIDE  (32 rows, ~0KB payload)",
        merge.format(t=f"{SCHEMA}.P_WIDE"), [sid, small])
    run("MERGE P_ONE   ( 1 row,  375KB payload)",
        merge.format(t=f"{SCHEMA}.P_ONE"), [sid, blob])
    run("MERGE P_SPLIT (32 rows, 375KB, blob-only table)",
        merge.format(t=f"{SCHEMA}.P_SPLIT"), [sid, blob])
    run("UPDATE P_WIDE (32 rows, 375KB payload)",
        f"UPDATE {SCHEMA}.P_WIDE SET json_state = PARSE_JSON(?) WHERE session_id = ?",
        [blob, sid])
    run("SELECT json_state P_WIDE (read 375KB back)",
        f"SELECT json_state FROM {SCHEMA}.P_WIDE WHERE session_id = ?", [sid])
    run("SELECT scalars P_WIDE (list query, no blob)",
        f"SELECT session_id, day, phase FROM {SCHEMA}.P_WIDE", [])
    run("SELECT scalars P_SPLIT-style (narrow table)",
        f"SELECT session_id, day, phase FROM {SCHEMA}.P_ONE", [])

    hybrid_ok = True
    try:
        cur.execute(f"CREATE OR REPLACE HYBRID TABLE {SCHEMA}.P_HYBRID "
                    f"(session_id STRING PRIMARY KEY, json_state VARIANT)")
        cur.execute(f"INSERT INTO {SCHEMA}.P_HYBRID SELECT session_id, json_state "
                    f"FROM {SCHEMA}.P_WIDE")
    except Exception as exc:  # noqa: BLE001
        hybrid_ok = False
        print(f"  HYBRID TABLE unavailable: {str(exc)[:120]}")
    if hybrid_ok:
        run("MERGE P_HYBRID (32 rows, 375KB payload)",
            merge.format(t=f"{SCHEMA}.P_HYBRID"), [sid, blob])
        run("UPDATE P_HYBRID (375KB payload)",
            f"UPDATE {SCHEMA}.P_HYBRID SET json_state = PARSE_JSON(?) WHERE session_id = ?",
            [blob, sid])
        run("SELECT P_HYBRID by PK (read 375KB back)",
            f"SELECT json_state FROM {SCHEMA}.P_HYBRID WHERE session_id = ?", [sid])

    print("\n=== server-side split for the above ===")
    allids = [i for ids, _ in results.values() for i in ids]
    inlist = ",".join(f"'{i}'" for i in allids)
    cur.execute(
        f"SELECT query_id, left(query_text,44), total_elapsed_time, "
        f"compilation_time, execution_time, transaction_blocked_time, bytes_scanned, bytes_scanned "
        f"FROM TABLE(information_schema.query_history(result_limit=>1000)) "
        f"WHERE query_id IN ({inlist})")
    rows = {r[0]: r for r in cur.fetchall()}
    print(f"  {'label':44s} {'total':>7} {'compile':>8} {'exec':>7} {'blockd':>7} {'scanned':>10}")
    for label, (ids, _) in results.items():
        got = [rows[i] for i in ids if i in rows]
        if not got:
            print(f"  {label:44s}  (history not yet materialised)")
            continue
        med = lambda k: statistics.median([g[k] or 0 for g in got])  # noqa: E731
        print(f"  {label:44s} {med(2):7.0f} {med(3):8.0f} {med(4):7.0f} "
              f"{med(5):7.0f} {med(6):10.0f}")

    for t in ("P_WIDE", "P_ONE", "P_SPLIT"):
        cur.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{t}")
    if hybrid_ok:
        cur.execute(f"DROP TABLE IF EXISTS {SCHEMA}.P_HYBRID")


def profile_one(conn) -> None:
    import cProfile, pstats, io
    cur = conn.cursor()
    cur.execute("SELECT 1")
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(5):
        cur.execute("SELECT 1")
        cur.fetchall()
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(22)
    print("\n=== cProfile: 5 x SELECT 1 (where the 270ms goes) ===")
    for line in buf.getvalue().splitlines():
        if line.strip():
            print("  " + line)


def main() -> None:
    sess = backend._build_snowpark_session()
    conn = sess._conn._conn  # noqa: SLF001
    cur = conn.cursor()
    cur.execute("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
    cur.execute("CREATE DATABASE IF NOT EXISTS SOC_PROBE_DB")
    cur.execute("CREATE SCHEMA IF NOT EXISTS SOC_PROBE_DB.PROBE")
    host = conn.host.lower()
    client_floor(cur)
    merge_experiments(cur)
    print("\ndone")


if __name__ == "__main__":
    main()
