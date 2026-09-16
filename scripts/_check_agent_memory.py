"""Round-trip V12's journal through the live SOC_AGENT_MEMORY table.

These writes are best-effort inside bare excepts, so a broken table is
invisible at runtime -- the agent just silently plays with no history.
This asserts the write really lands and really rehydrates.
"""
from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "snowflake")

from sea_of_colours.snowpark import backend as soc_backend  # noqa: E402
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import (  # noqa: E402
    memory as v12_memory,
)

SID = "_memcheck_roundtrip"

store = soc_backend.get_store()
session = soc_backend.snowpark_session_for(store)
if session is None:
    raise SystemExit("no snowpark session -- not on the snowflake backend")

ctx = session.sql("SELECT CURRENT_DATABASE() D, CURRENT_SCHEMA() S").collect()[0]
print(f"context: db={ctx['D']} schema={ctx['S']}")

entry = v12_memory.new_entry(
    day=3, plan_this_turn="round-trip probe", rationale="TEST"
)
v12_memory.save_entry(SID, "p2", entry, store=store, season_name="memcheck")

# Drop the process-local copy so read_recent is forced to hydrate from
# Snowflake -- which is the exact path a restarted server takes.
v12_memory.clear_in_memory_store()

got = v12_memory.read_recent(SID, "p2", store=store)
print(f"rehydrated {len(got)} entry(ies) from Snowflake")
for g in got:
    print("   ", {k: g.get(k) for k in ("day", "plan_this_turn", "rationale")})

session.sql(
    f"DELETE FROM SOC_AGENT_MEMORY WHERE session_id = '{SID}'"
).collect()
print("cleaned up test row")
print("RESULT:", "PASS" if got else "FAIL -- write or hydrate is still broken")
