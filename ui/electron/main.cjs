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

let win = null;
let db  = null;
let stmt = null;

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
        ss.updated_at                   AS session_updated_at
      FROM standings_current s
      LEFT JOIN session_entry e
        ON e.session_oid = s.session_oid
       AND e.car_number  = s.car_number
      LEFT JOIN session_status ss
        ON ss.session_oid = s.session_oid
      WHERE s.session_oid = (
        SELECT session_oid FROM session_status
        ORDER BY updated_at DESC LIMIT 1
      )
      ORDER BY s.car_class, CAST(s.pos_in_class AS INTEGER)
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

function buildPayload(rawRows) {
  const classMap = new Map();
  let session = { flag: null, lap: null, isRunning: false, ageS: null };
  let sessionRead = false;

  for (const r of rawRows) {
    if (!sessionRead && r.session_updated_at) {
      session = {
        flag:      r.current_flag  || null,
        lap:       r.current_lap   ?? null,
        isRunning: Boolean(r.session_running),
        ageS:      ageSeconds(r.session_updated_at),
      };
      sessionRead = true;
    }
    if (!classMap.has(r.class_code)) classMap.set(r.class_code, []);
    classMap.get(r.class_code).push({
      car:         r.car_number,
      pos:         r.pos,
      driver:      r.driver,
      team:        r.team,
      gapMs:       r.gap_ms  ?? null,
      laps:        r.laps    ?? 0,
      trackStatus: r.track_status || null,
      stops:       r.stops   ?? 0,
      isRunning:   Boolean(r.is_running),
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

  return { session, classes, updatedAt: Date.now() };
}

function queryAndSend() {
  if (!win || win.isDestroyed()) return;
  const d = openDb();
  if (!d || !stmt) return;
  try {
    const rows = stmt.all();
    if (rows.length > 0) {
      win.webContents.send('rows-update', buildPayload(rows));
    }
  } catch (err) {
    console.error('[racenet] query failed:', err.message);
    // Drop the connection so openDb() reconnects next tick
    try { db.close(); } catch (_) {}
    db   = null;
    stmt = null;
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
