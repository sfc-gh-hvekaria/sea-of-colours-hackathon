-- ============================================================================
-- orchestrator_2 schema additions.
--
-- This file is AUTHORED, NOT YET DEPLOYED. Apply with:
--   snowsql -c <conn> -f sea_of_colours/orchestrator_2/snowflake/orchestrator_v2_schema.sql
-- after reviewing.
--
-- Contains two tables:
--
--   1. SOC_AGENT_BINDING — per-session, per-player routing for the
--      binding-driven dispatcher (orchestrator_2). When a row exists
--      for (session_id, player) the dispatcher routes that seat to
--      the named kind+locator; absent rows fall back to env vars +
--      the KNOWN_AGENT_BINDINGS map in
--      sea_of_colours.orchestrator_2.binding_registry.
--
--   2. SOC_AGENT_MEMORY — RELOCATED from soc_schema.sql. Harness-specific
--      persistent intel (a harness's enemy-vision history). Wiped by
--      SOC_INIT_SESSION alongside the rest of SOC_*; uses the existing
--      _SESSION_TABLES wipe ordering once the table name is added back
--      to SnowparkSocStore._SESSION_TABLES.
-- ============================================================================

-- ⚠️ TEMPLATED — deploy via scripts/deploy_soc_schema.py, which fills in
--     {{SOC_DATABASE}} / {{SOC_SCHEMA}} from sea_of_colours/snowpark/naming.py.
USE DATABASE {{SOC_DATABASE}};
USE SCHEMA {{SOC_SCHEMA}};

-- ----------------------------------------------------------------------------
-- 1) SOC_AGENT_BINDING
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS SOC_AGENT_BINDING (
    session_id   STRING NOT NULL,
    player       STRING NOT NULL,
    kind         STRING NOT NULL,    -- 'cortex_agent'|'harness_in_process'|'harness_proc'|'harness_spcs'|'heuristic'
    locator      STRING NOT NULL,    -- agent name | module:callable | proc identifier | URL | 'RED_HARVEST'
    agent_label  STRING,             -- display name (e.g. 'TABULA_V12')
    notes        STRING,
    created_at   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, player)
);

-- ----------------------------------------------------------------------------
-- 2) SOC_AGENT_MEMORY — relocated from soc_schema.sql.
--    Currently used by V12's harness
--    (sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.memory) to record
--    every cell ever touched by a known enemy probe disk so the candidate
--    compiler can flag chains the rival has had prior vision on.
--
--    PAYLOAD shape for kind='enemy_probe_disk_history':
--      { "cells": { "x,y": {"first_day":N,"last_day":N,"count":N}, ... },
--        "last_updated_day": N }
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS SOC_AGENT_MEMORY (
    session_id   STRING NOT NULL,
    season_name  STRING,
    player       STRING NOT NULL,
    kind         STRING NOT NULL,           -- e.g. 'enemy_probe_disk_history'
    payload      VARIANT,
    updated_at   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, player, kind)
);

-- ----------------------------------------------------------------------------
-- Grants (mirror soc_schema.sql's GRANT pattern).
-- ----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE SOC_AGENT_BINDING TO ROLE SYSADMIN;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE SOC_AGENT_MEMORY  TO ROLE SYSADMIN;
