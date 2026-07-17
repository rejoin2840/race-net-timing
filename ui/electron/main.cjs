'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs   = require('fs');

const IS_DEV  = process.env.ELECTRON_DEV === '1';
const DEV_URL = 'http://localhost:5173';
const DB_PATH = path.resolve(__dirname, '../../data/race.db');

// Resolve better-sqlite3 from the ui/ package (not global node_modules)
const Database = require(
  path.resolve(__dirname, '../node_modules/better-sqlite3')
);
const schema = require('./schema.cjs');

// Class order mirrors timing_table.py CLASS_ORDER
const CLASS_ORDER = ['GTP', 'HYPERCAR', 'LMP2', 'GTD PRO', 'LMGT3', 'GTD'];

let win              = null;
let db               = null;
let stmt             = null;
let stmtPits         = null;
let stmtRc           = null;
let stmtBattles      = null;
let stmtSessionComp  = null;
let dbSchemaSig      = null;   // signature of the shape we prepared against
let dbSchemaComplete = false;  // false → keep re-probing (poller may migrate the DB live)

function closeDb() {
  try { if (db) db.close(); } catch (_) {}
  db              = null;
  stmt            = null;
  stmtPits        = null;
  stmtRc          = null;
  stmtBattles     = null;
  stmtSessionComp = null;
  dbSchemaSig     = null;
}

function openDb() {
  if (db) return db;
  if (!fs.existsSync(DB_PATH)) return null;
  try {
    db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    // Build SQL against whatever shape this DB actually has: archives and
    // scraper-only DBs predate the poller's tables/columns, and a prepare
    // naming a missing column would otherwise blank the whole board.
    const p = schema.probeSchema(db);
    dbSchemaSig      = schema.schemaSig(p);
    dbSchemaComplete = schema.schemaComplete(p);
    stmt     = db.prepare(schema.buildMainSql(p));
    stmtPits = db.prepare(schema.buildPitsSql());
    stmtRc   = db.prepare(schema.buildRcSql(p));
    stmtBattles     = p.hasBattles     ? db.prepare(schema.buildBattlesSql())     : null;
    stmtSessionComp = p.hasSessionComp ? db.prepare(schema.buildSessionCompSql()) : null;
    console.log('[racenet] db opened:', DB_PATH,
                dbSchemaComplete ? '' : '(partial schema — will re-probe)');
    return db;
  } catch (err) {
    console.error('[racenet] DB open failed:', err.message);
    closeDb();
    return null;
  }
}

// If we opened against a partial schema, the poller may add its tables/columns
// at any moment — re-probe cheaply each tick and rebuild when the shape grows.
function maybeReprobe() {
  if (!db || dbSchemaComplete) return;
  try {
    if (schema.schemaSig(schema.probeSchema(db)) !== dbSchemaSig) {
      console.log('[racenet] schema changed — rebuilding statements');
      closeDb();
    }
  } catch (_) {}
}

function ageSeconds(updatedAt) {
  if (!updatedAt) return null;
  try {
    // scraper stamps end in 'Z', the poller's in '+00:00' — only append 'Z'
    // when there's no timezone at all (appending to an offset makes NaN)
    const iso = /(Z|[+-]\d\d:?\d\d)$/.test(updatedAt) ? updatedAt : updatedAt + 'Z';
    const t = new Date(iso).getTime();
    return Number.isNaN(t) ? null : (Date.now() - t) / 1000;
  } catch { return null; }
}

// Python-computed clock (fixes the replay capture-clock gotcha) — but only
// when it's a real value and the poller is alive: pre-start the calculator
// leaves remaining_s at 0.0 (not "finished"), and a dead poller must not
// freeze the header clock, so both cases fall back to calcRemainingS.
function pythonRemainingS(sc) {
  if (!sc || sc.remaining_s == null) return null;
  const age = ageSeconds(sc.updated_at);
  if (age === null || age > 30) return null;
  return sc.remaining_s;
}

function calcRemainingS(r) {
  if (!r.final_type) return null;
  if (r.session_finished) return 0;
  if (r.final_type === 'BY_TIME') {
    // no start_time_s = clock hasn't started yet (pre-race) — mirrors
    // calculator.py, which only computes elapsed once start_time_s is set
    if (!r.start_time_s || !r.final_time_s) return null;
    const total = r.final_time_s + (r.has_extra_time ? (r.extra_time_s || 0) : 0);
    const elapsed = Math.max(0, Date.now() / 1000 - r.start_time_s - (r.stopped_s || 0));
    return Math.max(0, total - elapsed);
  }
  return null; // BY_LAPS: use lap delta instead
}

function buildPayload(rawRows, pitRows, rcRows, battleRows, sessionComp) {
  // index pit events by car_number for O(1) lookup
  const pitsBycar = new Map();
  for (const p of (pitRows || [])) {
    if (!pitsBycar.has(p.car_number)) pitsBycar.set(p.car_number, []);
    pitsBycar.get(p.car_number).push({
      stop:     p.stop_number,
      lap:      p.pit_lap      ?? null,
      flag:     p.flag         || null,
      durationMs: p.stop_duration_ms ?? null,
    });
  }

  const rcMessages = (rcRows || []).map(r => ({
    ts:          r.ts,
    message:     r.message,
    tier:        r.tier         ?? null,
    kind:        r.kind         || null,
    detectedAt:  r.detected_at  || null,
  }));

  const battles = (battleRows || []).map(r => ({
    carClass:     r.car_class,
    carAhead:     r.car_ahead,
    carChaser:    r.car_chaser,
    gapMs:        r.gap_ms,
    closing:      Boolean(r.closing),
    rateSPerLap:  r.rate_s_per_lap ?? null,
  }));

  const classMap = new Map();
  let session = { flag: null, lap: null, isRunning: false, ageS: null,
                  finalType: null, remainingS: null, finalLaps: null, isFinished: false };
  let sessionRead = false;

  for (const r of rawRows) {
    if (!sessionRead && r.session_updated_at) {
      session = {
        flag:       r.current_flag  || null,
        lap:        r.current_lap   ?? null,
        isRunning:  Boolean(r.session_running),
        ageS:       ageSeconds(r.session_updated_at),
        finalType:  r.final_type    || null,
        remainingS: pythonRemainingS(sessionComp) ?? calcRemainingS(r),
        finalLaps:  r.final_laps    ?? null,
        isFinished: Boolean(r.session_finished),
      };
      sessionRead = true;
    }
    if (!classMap.has(r.class_code)) classMap.set(r.class_code, []);
    classMap.get(r.class_code).push({
      car:          r.car_number,
      pos:          r.pos,
      driver:       r.driver,
      team:         r.team,
      gapMs:        r.gap_ms        ?? null,
      laps:         r.laps          ?? 0,
      trackStatus:  r.track_status  || null,
      stops:        r.stops         ?? 0,
      isRunning:    Boolean(r.is_running),
      lastLapMs:    r.last_lap_ms   ?? null,
      bestLapMs:    r.best_lap_ms   ?? null,
      fuelPct:      r.fuel_pct      ?? null,
      // laps into the current stint — same "laps minus last pit lap" the
      // Python calculator uses (calculator.py analyse(), ~L817)
      stintLaps:    (r.last_pit_lap != null && r.laps != null)
                      ? Math.max(0, r.laps - r.last_pit_lap) : null,
      netPos:       r.net_position  ?? null,
      netGapMs:     r.net_gap_ms    ?? null,
      netGapBandMs: r.net_gap_band_ms ?? null,
      classGapMs:   r.class_gap_ms  ?? null,
      lapsDown:     r.laps_down     ?? null,
      stopsLeft:    r.est_stops_left ?? null,
      penaltyS:     r.penalty_s     ?? null,
      penaltyNote:  r.penalty_note  || null,
      owesDC:           Boolean(r.owes_driver_change),
      netSettled:       Boolean(r.net_settled),
      projectedFinish:  r.projected_finish  ?? null,
      fuelDue:          r.fuel_due          || null,
      catching:         r.catching          || null,
      catchInLaps:      r.catch_in_laps     ?? null,
      strategyNote:     r.strategy_note     || null,
      nextStopMs:       r.next_stop_ms      ?? null,
      nextStopStdMs:    r.next_stop_std_ms  ?? null,
      netUpdatedAt:     r.net_updated_at    || null,
      pitEvents:        pitsBycar.get(r.car_number) ?? [],
    });
  }

  // add classLeaderStopsLeft per car (net P1 in that car's class)
  for (const rows of classMap.values()) {
    const leader = rows.find((r) => r.netPos === 1) ?? rows[0];
    const leaderStops = leader?.stopsLeft ?? null;
    for (const r of rows) r.classLeaderStopsLeft = leaderStops;
  }

  const classes = [...classMap.entries()]
    .sort(([a], [b]) => {
      const ai = CLASS_ORDER.indexOf(a);
      const bi = CLASS_ORDER.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    })
    .map(([code, rows]) => ({ code, rows }));

  return { session, classes, rcMessages, battles, updatedAt: Date.now() };
}

function queryAndSend() {
  if (!win || win.isDestroyed()) return;
  maybeReprobe();
  const d = openDb();
  if (!d || !stmt) return;
  try {
    const rows = stmt.all();
    if (rows.length > 0) {
      const pits       = stmtPits       ? stmtPits.all()       : [];
      const rc         = stmtRc         ? stmtRc.all()         : [];
      const battles    = stmtBattles    ? stmtBattles.all()    : [];
      let sessComp = null;
      try { sessComp = stmtSessionComp ? stmtSessionComp.get() : null; } catch (_) {}
      const payload = buildPayload(rows, pits, rc, battles, sessComp);
      if (!queryAndSend.logged) {
        queryAndSend.logged = true;
        console.log(`[racenet] first payload: ${rows.length} cars, ` +
                    `${payload.classes.length} classes, flag=${payload.session.flag}`);
      }
      win.webContents.send('rows-update', payload);
    }
  } catch (err) {
    console.error('[racenet] query failed:', err.message);
    closeDb();
  }
}

function createWindow() {
  win = new BrowserWindow({
    width:  1280,
    height: 800,
    title:  'Race Net Timing',
    backgroundColor: '#0d1115',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
  });

  if (IS_DEV) {
    win.loadURL(DEV_URL);
    win.webContents.openDevTools({ mode: 'detach' });
  } else {
    win.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  win.on('closed', () => { win = null; });
}

app.whenReady().then(() => {
  createWindow();
  // Poll DB every 2 s, same cadence as PyQt6 dashboard
  setInterval(queryAndSend, 2000);
  // Send one shot quickly so the first frame renders fast
  setTimeout(queryAndSend, 500);
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
