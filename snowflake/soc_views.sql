-- ============================================================================
-- Sea of Colours — Views over the SOC_* schema
-- ============================================================================
-- Cheap, idempotent. Re-runnable any time — views are pure queries.
-- ============================================================================

-- ⚠️ TEMPLATED — deploy via scripts/deploy_soc_schema.py, which fills in
--     {{SOC_DATABASE}} / {{SOC_SCHEMA}} from sea_of_colours/snowpark/naming.py.
USE DATABASE {{SOC_DATABASE}};
USE SCHEMA {{SOC_SCHEMA}};

-- --------------------------------------------------------------------------
-- V1) Leaderboard — total red harvested per House per session
-- --------------------------------------------------------------------------
-- The asset ledger's total_red_harvested counter is the canonical
-- lifetime tally; sum across all of a player's harvesters.
--
-- v1.41 — DERIVED FROM json_state, not from SOC_ASSET_RECORD. That table
-- was written on every save (~690ms a turn) and read by nothing except
-- this view, so the write was pure cost. The ledger already lives inside
-- SOC_GAME_SESSION.json_state, which is the authoritative row, so
-- flattening it here is both free and impossible to get out of sync.
-- See docs/SNOWFLAKE_LATENCY_BRIEF.md.
--
-- Cast destroyed_on_day before the NULL test: a JSON null is NOT a SQL
-- NULL inside a VARIANT, but casting one yields SQL NULL.
CREATE OR REPLACE VIEW SOC_LEADERBOARD AS
SELECT
    s.session_id,
    r.value:owner::STRING                             AS player,
    SUM(r.value:total_red_harvested::INT)             AS total_red_harvested,
    SUM(r.value:total_days_on_surface::INT)           AS total_days_on_surface,
    COUNT_IF(r.value:destroyed_on_day::INT IS NULL)     AS alive_assets,
    COUNT_IF(r.value:destroyed_on_day::INT IS NOT NULL) AS destroyed_assets
FROM SOC_GAME_SESSION s,
     LATERAL FLATTEN(input => s.json_state:asset_records) r
GROUP BY s.session_id, r.value:owner::STRING;

-- --------------------------------------------------------------------------
-- V2) Day index — frame counts + captions per (session, day)
-- --------------------------------------------------------------------------
-- Powers the Phase-4 scrub bar: one row per night, used to render day
-- dividers and "Day N" badges over the timeline cursor.
CREATE OR REPLACE VIEW SOC_DAY_INDEX AS
SELECT
    session_id,
    day,
    COUNT(*)                       AS frame_count,
    MIN(global_idx)                AS first_global_idx,
    MAX(global_idx)                AS last_global_idx,
    MIN(ts)                        AS started_at,
    MAX(ts)                        AS ended_at,
    MIN_BY(caption, frame_idx)     AS first_caption,
    MAX_BY(caption, frame_idx)     AS last_caption
FROM SOC_REPLAY_FRAME
GROUP BY session_id, day;

-- --------------------------------------------------------------------------
-- V3) Latest frame per day — quick "what does the map look like at dawn"
-- --------------------------------------------------------------------------
-- Used by Phase 5's agent prompts: "show me the board at the end of
-- yesterday's night" without scanning every frame.
CREATE OR REPLACE VIEW SOC_LATEST_FRAME_PER_DAY AS
SELECT
    f.session_id,
    f.day,
    f.frame_idx,
    f.global_idx,
    f.caption,
    f.tag,
    f.owner,
    f.cells,
    f.cells_player_p1,
    f.cells_player_p2,
    f.entities,
    f.hoard,
    f.ts
FROM SOC_REPLAY_FRAME f
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY f.session_id, f.day
    ORDER BY f.frame_idx DESC
) = 1;

-- --------------------------------------------------------------------------
-- V4) Session standings — single row per (session, player) summary
-- --------------------------------------------------------------------------
-- Joins assets + hoard + shipped to power the front-end status pane.
-- ``score`` is the canonical season win condition (§3.1, v0.8.0): sum
-- of every SHIPPED parcel's RED purity (0..255 per square). Hoard
-- contents no longer contribute — players must push parcels through
-- the Orbit catapult to realise score. ``hoard_score`` is still
-- emitted for diagnostic visibility but is intentionally excluded
-- from the ``score`` / ``rank`` calculations.
CREATE OR REPLACE VIEW SOC_SESSION_STANDINGS AS
SELECT
    s.session_id,
    s.season_name,
    s.day,
    p.player,
    COALESCE(lb.total_red_harvested, 0)  AS total_red_harvested,
    COALESCE(h.hoard_count, 0)           AS hoard_count,
    COALESCE(sh.shipped_count, 0)        AS shipped_count,
    COALESCE(sh.shipped_score, 0)        AS score,
    COALESCE(h.hoard_score, 0)           AS hoard_score,
    COALESCE(sh.shipped_score, 0)        AS shipped_score,
    COALESCE(lb.alive_assets, 0)         AS alive_assets,
    COALESCE(lb.destroyed_assets, 0)     AS destroyed_assets,
    RANK() OVER (
        PARTITION BY s.session_id
        ORDER BY COALESCE(sh.shipped_score, 0) DESC
    )                                     AS rank
FROM SOC_GAME_SESSION s
CROSS JOIN (
    SELECT 'p1' AS player UNION ALL SELECT 'p2'
) p
LEFT JOIN SOC_LEADERBOARD lb
       ON lb.session_id = s.session_id AND lb.player = p.player
LEFT JOIN (
    SELECT
      session_id,
      owner AS player,
      COUNT(*) AS hoard_count,
      SUM(LEAST(255, GREATEST(0, COALESCE(origin_purity, 0)))) AS hoard_score
    FROM SOC_HOARD_PARCEL
    GROUP BY session_id, owner
) h ON h.session_id = s.session_id AND h.player = p.player
LEFT JOIN (
    SELECT
      session_id,
      owner AS player,
      COUNT(*) AS shipped_count,
      SUM(LEAST(255, GREATEST(0, COALESCE(origin_purity, 0)))) AS shipped_score
    FROM SOC_SHIPPED_PARCEL
    GROUP BY session_id, owner
) sh ON sh.session_id = s.session_id AND sh.player = p.player;

-- --------------------------------------------------------------------------
-- V5) Action history — denormalised player-action stream
-- --------------------------------------------------------------------------
-- Convenience view for the Phase-4 "all-days" log scrollback panel.
-- Filters to lines that mention a move (everything tagged anything but
-- 'open' / 'dawn') and pairs them with their replay frame for cross-link.
CREATE OR REPLACE VIEW SOC_ACTION_HISTORY AS
SELECT
    f.session_id,
    f.day,
    f.global_idx,
    f.frame_idx,
    f.tag,
    f.owner,
    f.caption,
    f.ts
FROM SOC_REPLAY_FRAME f
WHERE f.tag IN ('probe', 'drop', 'step', 'pickup', 'waste')
ORDER BY f.session_id, f.global_idx;
