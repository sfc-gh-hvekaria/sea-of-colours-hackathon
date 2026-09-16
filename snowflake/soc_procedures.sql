-- ============================================================================
-- Sea of Colours — Snowpark Python Stored Procedures
-- ============================================================================
-- Each procedure is a thin wrapper around a function in the packaged
-- module `sea_of_colours.snowpark.procs`. The package is uploaded to
-- @SOC_PY_STAGE by scripts/deploy_soc_schema.py before this file runs.
--
-- All procedures EXECUTE AS OWNER so they can write to the SOC_* tables
-- regardless of the caller's role; they are CALLER-friendly because the
-- only side effect is into our schema.
-- ============================================================================

-- ⚠️ TEMPLATED — deploy via scripts/deploy_soc_schema.py, which fills in
--     {{SOC_DATABASE}} / {{SOC_SCHEMA}} from sea_of_colours/snowpark/naming.py.
USE DATABASE {{SOC_DATABASE}};
USE SCHEMA {{SOC_SCHEMA}};

CREATE STAGE IF NOT EXISTS SOC_PY_STAGE
    DIRECTORY = (ENABLE = TRUE)
    COMMENT = 'Python imports for SOC_* Snowpark procedures.';

-- --------------------------------------------------------------------------
-- SOC_INIT_SESSION — create + persist a fresh game session
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_INIT_SESSION(
    p_seed   INTEGER,
    p_width  INTEGER DEFAULT 40,
    p_height INTEGER DEFAULT 28
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_init_session'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_SUBMIT_POLICY — stash one seat's move queue
--
-- ``p_policy`` is a STRING (not VARIANT) because Snowflake Cortex agent
-- tool calls cannot pass OBJECT-typed parameters into stored procedures
-- running in warehouse mode — the agent gets back a "warehouse-environment
-- limitation: object-type parameter unsupported" error. The handler
-- ``soc_submit_policy`` parses the JSON itself and accepts either the
-- canonical ``{"moves":[...]}`` envelope or the bare ``[...]`` array.
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_SUBMIT_POLICY(
    p_session_id STRING,
    p_player     STRING,
    p_policy     STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_submit_policy'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_SUBMIT_ORBIT_ACTIONS — stash one seat's Orbit phase queue (v0.9.6)
--
-- ``p_actions`` is a STRING (same DDL constraint as ``p_policy`` on
-- :sql:`SOC_SUBMIT_POLICY`); the handler parses either the canonical
-- ``{"actions":[...]}`` envelope or the bare ``[...]`` array. Each item
-- is one of: build_harvester / build_probe / build_emp /
-- build_chaff / repair (RULEBOOK §4). Retired tags are still accepted
-- by the parser and refused with a named reason rather than a shrug:
-- refine / ship_catapult / solar_jettison (v1.13) and build_mine
-- (v1.31, §4.9.4). The handler validates the queue, surfaces any
-- per-item parse errors as yellow log lines, and — once every seat
-- is ready — triggers the Orbit settlement (credits award, builds,
-- refine, single-lane catapult shipping with row-threshold gating,
-- solar jettison) and flips ``phase`` to PLANNING so the night
-- queue can open.
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_SUBMIT_ORBIT_ACTIONS(
    p_session_id STRING,
    p_player     STRING,
    p_actions    STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_submit_orbit_actions'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_RUN_NIGHT — force-resolve the current night
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_RUN_NIGHT(p_session_id STRING)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_run_night'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_GET_VIEW — player percept + agent-friendly structured view
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_GET_VIEW(
    p_session_id STRING,
    p_player     STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_get_view'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_GET_OBSERVER — cheat omniscient mosaic
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_GET_OBSERVER(p_session_id STRING)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_get_observer'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_GET_REPLAY — multi-day replay scrub
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_GET_REPLAY(
    p_session_id STRING,
    p_day_from   INTEGER DEFAULT NULL,
    p_day_to     INTEGER DEFAULT NULL
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_get_replay'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_GET_LOG — structured log range
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_GET_LOG(
    p_session_id STRING,
    p_day_from   INTEGER DEFAULT NULL,
    p_day_to     INTEGER DEFAULT NULL,
    p_limit      INTEGER DEFAULT NULL
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_get_log'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_GET_INVENTORY — hoard + shipped + assets_by_status
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_GET_INVENTORY(
    p_session_id STRING,
    p_player     STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_get_inventory'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_GET_SESSION_STATUS — for /api/game/{id}/status
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_GET_SESSION_STATUS(p_session_id STRING)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_get_session_status'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_LIST_SESSIONS — for /api/game/latest
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_LIST_SESSIONS()
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_list_sessions'
EXECUTE AS OWNER
AS
$$
$$;

-- --------------------------------------------------------------------------
-- SOC_SAVE_RATIONALE — Cortex agent audit-trail row
-- --------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SOC_SAVE_RATIONALE(
    p_session_id    STRING,
    p_day           INTEGER,
    p_agent_id      STRING,
    p_player        STRING,
    p_rationale     STRING,
    p_prompt_excerpt STRING DEFAULT NULL,
    p_tool_calls    VARIANT DEFAULT NULL,
    p_response_text STRING DEFAULT NULL,
    p_ms_elapsed    INTEGER DEFAULT NULL
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@SOC_PY_STAGE/sea_of_colours.zip')
HANDLER = 'sea_of_colours.snowpark.procs.soc_save_rationale'
EXECUTE AS OWNER
AS
$$
$$;
