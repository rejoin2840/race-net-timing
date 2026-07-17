'use strict';

// schema.cjs — probe the DB shape and build SQL that matches it.
//
// The web UI reads DBs of several vintages: scraper-only (no poller tables),
// archives written by an older poller (net_analysis without the Epic 10
// columns), and current ones. A prepared statement that names a missing
// column or table fails at prepare time and would blank the whole board, so
// every optional column/table degrades to NULL / an absent statement instead.
// Only uses prepare().get()/.all(), so it runs under node:sqlite in tests.

const SESSION_SUBQ = `(
        SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1
      )`;

// net_analysis columns the UI wants, with NULL fallbacks when the DB predates them
const NA_COLS = [
  'net_position', 'net_gap_ms', 'net_gap_band_ms', 'class_gap_ms', 'laps_down',
  'est_stops_left', 'penalty_s', 'penalty_note', 'owes_driver_change',
  'net_settled', 'projected_finish', 'fuel_due', 'catching', 'catch_in_laps',
  'strategy_note', 'next_stop_ms', 'next_stop_std_ms',
];

function tableExists(d, name) {
  return !!d.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
}

function tableColumns(d, name) {
  if (!tableExists(d, name)) return new Set();
  return new Set(d.prepare(`PRAGMA table_info(${name})`).all().map(r => r.name));
}

function probeSchema(d) {
  const naCols = tableColumns(d, 'net_analysis');
  const rcCols = tableColumns(d, 'race_control');
  const scCols = tableColumns(d, 'standings_current');
  return {
    naCols,
    rcHasTier:      rcCols.has('tier') && rcCols.has('kind'),
    hasBattles:     tableExists(d, 'rail_battles'),
    hasSessionComp: tableExists(d, 'session_computed'),
    // fuel_pct arrived via ALTER TABLE (2026-07-15, WEC VET wiring) — older
    // archives/scraper DBs predate it, same degrade-to-NULL treatment as
    // everything else here. last_lap_ms/best_lap_ms/last_pit_lap are in the
    // original CREATE TABLE, so no probe needed for those.
    hasFuelPct:     scCols.has('fuel_pct'),
  };
}

// stable signature so the poller upgrading the DB mid-session can be detected
function schemaSig(p) {
  return [
    [...p.naCols].sort().join(','),
    p.rcHasTier, p.hasBattles, p.hasSessionComp, p.hasFuelPct,
  ].join('|');
}

// true when every optional feature is present — nothing left to re-probe for
function schemaComplete(p) {
  return p.rcHasTier && p.hasBattles && p.hasSessionComp && p.hasFuelPct
    && NA_COLS.every(c => p.naCols.has(c));
}

function buildMainSql(p) {
  const hasNa = p.naCols.size > 0;
  const naSel = NA_COLS
    .map(c => (hasNa && p.naCols.has(c)) ? `na.${c}` : `NULL AS ${c}`)
    .join(',\n        ');
  const netUpdated = (hasNa && p.naCols.has('updated_at'))
    ? 'na.updated_at                   AS net_updated_at'
    : 'NULL AS net_updated_at';
  const naJoin = hasNa
    ? `LEFT JOIN net_analysis na
        ON na.session_oid = s.session_oid
       AND na.car_number  = s.car_number`
    : '';
  const fuelPctSel = p.hasFuelPct ? 's.fuel_pct' : 'NULL AS fuel_pct';
  return `
      SELECT
        s.car_number,
        CAST(s.pos_in_class AS INTEGER) AS pos,
        s.car_class                     AS class_code,
        s.gap_ms,
        CAST(s.laps AS INTEGER)         AS laps,
        s.track_status,
        CAST(s.pits AS INTEGER)         AS stops,
        s.is_running,
        s.updated_at                    AS car_updated_at,
        s.last_lap_ms,
        s.best_lap_ms,
        CAST(s.last_pit_lap AS INTEGER) AS last_pit_lap,
        ${fuelPctSel},
        COALESCE(e.name,  s.car_number) AS driver,
        COALESCE(e.team,  '')           AS team,
        ss.current_flag,
        CAST(ss.current_lap AS INTEGER) AS current_lap,
        ss.is_running                   AS session_running,
        ss.is_finished                  AS session_finished,
        ss.final_type,
        ss.final_time_s,
        ss.final_laps,
        ss.start_time_s,
        ss.stopped_s,
        ss.has_extra_time,
        ss.extra_time_s,
        ss.updated_at                   AS session_updated_at,
        ${naSel},
        ${netUpdated}
      FROM standings_current s
      LEFT JOIN session_entry e
        ON e.session_oid = s.session_oid
       AND e.car_number  = s.car_number
      LEFT JOIN session_status ss
        ON ss.session_oid = s.session_oid
      ${naJoin}
      WHERE s.session_oid = ${SESSION_SUBQ}
      ORDER BY s.car_class, CAST(s.pos_in_class AS INTEGER)
    `;
}

function buildRcSql(p) {
  // detected_at is baseline race_control schema (original CREATE TABLE) —
  // safe unconditionally. It's real wall-clock at ingest, used for "N ago"
  // display; `ts` (message's race-original time) stays the sort key so
  // ordering is unaffected by whether this is live or a replay/archive.
  const cols   = p.rcHasTier ? 'ts, message, tier, kind, detected_at'
                             : 'ts, message, NULL AS tier, NULL AS kind, detected_at';
  const filter = p.rcHasTier ? 'AND (tier IS NULL OR tier > 0)' : '';
  // 50 deep: the rail shows 6, but the WYWA card diffs against this list and
  // must see everything that happened across a long absence
  return `
      SELECT ${cols} FROM race_control
      WHERE session_oid = ${SESSION_SUBQ}
        ${filter}
      ORDER BY ts DESC, rowid DESC LIMIT 50
    `;
}

function buildBattlesSql() {
  return `
      SELECT car_class, car_ahead, car_chaser, gap_ms, closing, rate_s_per_lap
      FROM rail_battles
      WHERE session_oid = ${SESSION_SUBQ}
      ORDER BY rank
    `;
}

function buildSessionCompSql() {
  return `
      SELECT remaining_s, elapsed_s, updated_at FROM session_computed
      WHERE session_oid = ${SESSION_SUBQ}
    `;
}

function buildPitsSql() {
  return `
      SELECT car_number, stop_number, pit_lap, flag, stop_duration_ms
      FROM pit_events
      WHERE session_oid = ${SESSION_SUBQ}
      ORDER BY car_number, stop_number
    `;
}

module.exports = {
  probeSchema, schemaSig, schemaComplete,
  buildMainSql, buildRcSql, buildBattlesSql, buildSessionCompSql, buildPitsSql,
};
