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

// Class order mirrors timing_table.py CLASS_ORDER
const CLASS_ORDER = ['GTP', 'HYPERCAR', 'LMP2', 'GTD PRO', 'LMGT3', 'GTD'];

let win      = null;
let db       = null;
let stmt     = null;
let stmtPits = null;
let stmtRc   = null;

function openDb() {
  if (db) return db;
  if (!fs.existsSync(DB_PATH)) return null;
  try {
    db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    stmt = db.prepare(`
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
        na.net_position,
        na.net_gap_ms,
        na.net_gap_band_ms,
        na.est_stops_left,
        na.penalty_s,
        na.penalty_note,
        na.owes_driver_change,
        na.net_settled
      FROM standings_current s
      LEFT JOIN session_entry e
        ON e.session_oid = s.session_oid
       AND e.car_number  = s.car_number
      LEFT JOIN session_status ss
        ON ss.session_oid = s.session_oid
      LEFT JOIN net_analysis na
        ON na.session_oid = s.session_oid
       AND na.car_number  = s.car_number
      WHERE s.session_oid = (
        SELECT session_oid FROM session_status
        ORDER BY updated_at DESC LIMIT 1
      )
      ORDER BY s.car_class, CAST(s.pos_in_class AS INTEGER)
    `);
    stmtPits = db.prepare(`
      SELECT car_number, stop_number, pit_lap, flag, stop_duration_ms
      FROM pit_events
      WHERE session_oid = (
        SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1
      )
      ORDER BY car_number, stop_number
    `);
    stmtRc = db.prepare(`
      SELECT ts, message FROM race_control
      WHERE session_oid = (
        SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1
      )
      ORDER BY ts DESC, rowid DESC LIMIT 5
    `);
    return db;
  } catch (err) {
    console.error('[racenet] DB open failed:', err.message);
    db = null;
    return null;
  }
}

function ageSeconds(updatedAt) {
  if (!updatedAt) return null;
  try {
    const ts = new Date(updatedAt.endsWith('Z') ? updatedAt : updatedAt + 'Z');
    return (Date.now() - ts.getTime()) / 1000;
  } catch { return null; }
}

function calcRemainingS(r) {
  if (!r.final_type) return null;
  if (r.session_finished) return 0;
  if (r.final_type === 'BY_TIME') {
    const total = (r.final_time_s || 0) + (r.has_extra_time ? (r.extra_time_s || 0) : 0);
    const elapsed = Math.max(0, Date.now() / 1000 - (r.start_time_s || 0) - (r.stopped_s || 0));
    return Math.max(0, total - elapsed);
  }
  return null; // BY_LAPS: use lap delta instead
}

function buildPayload(rawRows, pitRows, rcRows) {
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

  const rcMessages = (rcRows || []).map(r => ({ ts: r.ts, message: r.message }));

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
        remainingS: calcRemainingS(r),
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
      netPos:       r.net_position  ?? null,
      netGapMs:     r.net_gap_ms    ?? null,
      netGapBandMs: r.net_gap_band_ms ?? null,
      stopsLeft:    r.est_stops_left ?? null,
      penaltyS:     r.penalty_s     ?? null,
      penaltyNote:  r.penalty_note  || null,
      owesDC:       Boolean(r.owes_driver_change),
      netSettled:   Boolean(r.net_settled),
      pitEvents:    pitsBycar.get(r.car_number) ?? [],
    });
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

  return { session, classes, rcMessages, updatedAt: Date.now() };
}

function queryAndSend() {
  if (!win || win.isDestroyed()) return;
  const d = openDb();
  if (!d || !stmt) return;
  try {
    const rows = stmt.all();
    if (rows.length > 0) {
      const pits = stmtPits ? stmtPits.all() : [];
      const rc   = stmtRc   ? stmtRc.all()   : [];
      win.webContents.send('rows-update', buildPayload(rows, pits, rc));
    }
  } catch (err) {
    console.error('[racenet] query failed:', err.message);
    try { db.close(); } catch (_) {}
    db       = null;
    stmt     = null;
    stmtPits = null;
    stmtRc   = null;
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
