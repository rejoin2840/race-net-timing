#!/usr/bin/env python3
"""Port of ui/electron/main.cjs buildPayload — dumps the exact RowsPayload JSON
the board consumes, so real replay frames can be shown in the browser board.
Uses stdlib sqlite3 (no better-sqlite3 ABI headache)."""
import sqlite3, json, sys, time, os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB = os.path.join(REPO, "data/race.db")
OUT = sys.argv[1] if len(sys.argv) > 1 else "snapshots"
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 170
EVERY = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
os.makedirs(OUT, exist_ok=True)

CLASS_ORDER = ['GTP', 'HYPERCAR', 'LMP2', 'GTD PRO', 'LMGT3', 'GTD']
NA_COLS = ['net_position','net_gap_ms','net_gap_band_ms','class_gap_ms','laps_down',
  'est_stops_left','penalty_s','penalty_note','owes_driver_change','net_settled',
  'projected_finish','fuel_due','catching','catch_in_laps','strategy_note',
  'fuel_laps_left','must_pit_lap','next_stop_ms','next_stop_std_ms']

MAIN_SQL = f"""
  SELECT s.car_number, CAST(s.pos_in_class AS INTEGER) AS pos, s.car_class AS class_code,
    s.gap_ms, CAST(s.laps AS INTEGER) AS laps, s.track_status,
    CAST(s.pits AS INTEGER) AS stops, s.is_running, s.last_lap_ms, s.best_lap_ms,
    CAST(s.last_pit_lap AS INTEGER) AS last_pit_lap, s.fuel_pct,
    COALESCE(e.name, s.car_number) AS driver, COALESCE(e.team,'') AS team,
    ss.current_flag, CAST(ss.current_lap AS INTEGER) AS current_lap,
    ss.is_running AS session_running, ss.is_finished AS session_finished,
    ss.final_type, ss.final_time_s, ss.final_laps, ss.start_time_s, ss.stopped_s,
    ss.has_extra_time, ss.extra_time_s, ss.updated_at AS session_updated_at,
    {', '.join('na.'+c for c in NA_COLS)}, na.updated_at AS net_updated_at
  FROM standings_current s
  LEFT JOIN session_entry e ON e.session_oid=s.session_oid AND e.car_number=s.car_number
  LEFT JOIN session_status ss ON ss.session_oid=s.session_oid
  LEFT JOIN net_analysis na ON na.session_oid=s.session_oid AND na.car_number=s.car_number
  WHERE s.session_oid=(SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1)
  ORDER BY s.car_class, CAST(s.pos_in_class AS INTEGER)
"""
PITS_SQL = "SELECT car_number, stop_number, pit_lap, flag, stop_duration_ms FROM pit_events WHERE session_oid=(SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1) ORDER BY car_number, stop_number"
RC_SQL = "SELECT ts, message, tier, kind, detected_at FROM race_control WHERE session_oid=(SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1) ORDER BY ts DESC LIMIT 40"
BAT_SQL = "SELECT car_class, car_ahead, car_chaser, gap_ms, closing, rate_s_per_lap FROM rail_battles WHERE session_oid=(SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1)"
SC_SQL = "SELECT remaining_s, elapsed_s, updated_at FROM session_computed WHERE session_oid=(SELECT session_oid FROM session_status ORDER BY updated_at DESC LIMIT 1)"

def age_s(u):
    if not u: return None
    try:
        import datetime as dt
        s = u if (u.endswith('Z') or u[-6] in '+-') else u+'Z'
        s = s.replace('+00:00','Z')
        t = dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
        return (time.time()-t)
    except Exception: return None

def build(conn):
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(MAIN_SQL)]
    if not rows: return None
    pits = [dict(r) for r in conn.execute(PITS_SQL)]
    rc   = [dict(r) for r in conn.execute(RC_SQL)]
    bats = [dict(r) for r in conn.execute(BAT_SQL)]
    try: sc = conn.execute(SC_SQL).fetchone()
    except Exception: sc = None
    sc = dict(sc) if sc else None

    pitsBy = {}
    for p in pits:
        pitsBy.setdefault(p['car_number'], []).append({
            'stop': p['stop_number'], 'lap': p['pit_lap'],
            'flag': p['flag'] or None, 'durationMs': p['stop_duration_ms']})
    rcMessages = [{'ts': r['ts'], 'message': r['message'], 'tier': r['tier'],
                   'kind': r['kind'] or None, 'detectedAt': r['detected_at'] or None} for r in rc]
    battles = [{'carClass': b['car_class'], 'carAhead': b['car_ahead'], 'carChaser': b['car_chaser'],
                'gapMs': b['gap_ms'], 'closing': bool(b['closing']), 'rateSPerLap': b['rate_s_per_lap']} for b in bats]

    remaining = None
    if sc and sc.get('remaining_s') is not None:
        a = age_s(sc.get('updated_at'))
        if a is not None and a <= 30: remaining = sc['remaining_s']

    classMap = {}
    session = {'flag':None,'lap':None,'isRunning':False,'ageS':None,'finalType':None,
               'remainingS':None,'finalLaps':None,'isFinished':False}
    read = False
    for r in rows:
        if not read and r.get('session_updated_at'):
            session = {'flag': r['current_flag'] or None, 'lap': r['current_lap'],
                'isRunning': bool(r['session_running']), 'ageS': age_s(r['session_updated_at']),
                'finalType': r['final_type'] or None, 'remainingS': remaining,
                'finalLaps': r['final_laps'], 'isFinished': bool(r['session_finished'])}
            read = True
        laps, lpl = r['laps'], r['last_pit_lap']
        stint = max(0, laps-lpl) if (lpl is not None and laps is not None) else None
        classMap.setdefault(r['class_code'], []).append({
            'car': r['car_number'], 'pos': r['pos'], 'driver': r['driver'], 'team': r['team'],
            'gapMs': r['gap_ms'], 'laps': laps or 0, 'trackStatus': r['track_status'] or None,
            'stops': r['stops'] or 0, 'isRunning': bool(r['is_running']),
            'lastLapMs': r['last_lap_ms'], 'bestLapMs': r['best_lap_ms'], 'fuelPct': r['fuel_pct'],
            'stintLaps': stint, 'netPos': r['net_position'], 'netGapMs': r['net_gap_ms'],
            'netGapBandMs': r['net_gap_band_ms'], 'classGapMs': r['class_gap_ms'],
            'lapsDown': r['laps_down'], 'stopsLeft': r['est_stops_left'], 'penaltyS': r['penalty_s'],
            'penaltyNote': r['penalty_note'] or None, 'owesDC': bool(r['owes_driver_change']),
            'netSettled': bool(r['net_settled']), 'projectedFinish': r['projected_finish'],
            'fuelDue': r['fuel_due'] or None, 'catching': r['catching'] or None,
            'catchInLaps': r['catch_in_laps'], 'strategyNote': r['strategy_note'] or None,
            'fuelLapsLeft': r['fuel_laps_left'], 'mustPitLap': r['must_pit_lap'],
            'nextStopMs': r['next_stop_ms'], 'nextStopStdMs': r['next_stop_std_ms'],
            'netUpdatedAt': r['net_updated_at'] or None, 'pitEvents': pitsBy.get(r['car_number'], [])})
    for rws in classMap.values():
        leader = next((x for x in rws if x['netPos']==1), rws[0])
        ls = leader.get('stopsLeft')
        for x in rws: x['classLeaderStopsLeft'] = ls
    def order(c):
        return CLASS_ORDER.index(c) if c in CLASS_ORDER else 99
    classes = [{'code': c, 'rows': classMap[c]} for c in sorted(classMap, key=order)]
    return {'session': session, 'classes': classes, 'rcMessages': rcMessages,
            'battles': battles, 'updatedAt': int(time.time()*1000)}

print(f"recorder(py) -> {OUT}  {COUNT} @ {EVERY}s", flush=True)
for i in range(COUNT):
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        pl = build(conn); conn.close()
    except Exception as e:
        pl = None
    if pl:
        s = pl['session']
        nnet = sum(1 for c in pl['classes'] for r in c['rows'] if r['netPos'] is not None)
        ncar = sum(len(c['rows']) for c in pl['classes'])
        seq = f"{i:03d}"
        json.dump(pl, open(os.path.join(OUT, f"p_{seq}.json"), 'w'))
        print(f"{seq} flag={s['flag']} lap={s['lap']} cars={ncar} net={nnet} rc={len(pl['rcMessages'])} bat={len(pl['battles'])} rem={round(s['remainingS']) if s['remainingS'] is not None else '-'}", flush=True)
    else:
        print(f"{i:03d} (no data)", flush=True)
    time.sleep(EVERY)
print("done", flush=True)
