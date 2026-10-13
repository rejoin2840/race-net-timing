"""Window-bound acquisition evidence and strict WEC capture validation.

Checks are read-only against production data; raw capture dispatch uses a scratch
DB. A terminal protocol state is required for race completeness. A missing gzip
trailer is reported separately and cannot substitute for that terminal state.
"""
import gzip
import hashlib
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path



def _hash(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def window_session(conn, series, kind, start_s, end_s):
    """Require one session of the scheduled kind with actual laps in this window."""
    if series not in ('imsa','wec') or kind not in ('practice','quali','race','sprint_race'):
        raise ValueError('unsupported acquisition series/kind')
    if not math.isfinite(start_s) or not math.isfinite(end_s) or end_s < start_s:
        raise ValueError('invalid acquisition window')
    rows = conn.execute('''SELECT DISTINCT s.session_oid, s.session_type
        FROM sessions s JOIN lap_history l ON l.session_oid=s.session_oid
        WHERE s.series=? AND julianday(l.recorded_at) BETWEEN julianday(?) AND julianday(?)''',
        (series, _iso(start_s), _iso(end_s))).fetchall()
    def matches(session_type):
        t=(session_type or '').upper()
        if kind in ('race','sprint_race'):
            return t in ('RACE', 'SESSION')
        if kind=='practice':
            return t in ('PRACTICE','WARM_UP')
        return t in ('QUALIFYING','QUALIFYING_BEST_LAP','QUALIFYING_AVG_LAP')
    candidates = [oid for oid, t in rows if matches(t)]
    if len(candidates) != 1:
        raise ValueError(f'expected one {series} {kind} session with captured laps in this window; found {len(candidates)}')
    oid=candidates[0]
    timing = conn.execute('''SELECT COUNT(*) FROM standings_current
        WHERE session_oid=? AND overall_position IS NOT NULL
        AND julianday(timing_updated_at) BETWEEN julianday(?) AND julianday(?)''',
        (oid,_iso(start_s),_iso(end_s))).fetchone()[0]
    if not timing:
        raise ValueError('no positioned timing observations in acquisition window')
    if kind in ('race','sprint_race'):
        status=conn.execute('SELECT is_finished FROM session_status WHERE session_oid=?',(oid,)).fetchone()
        if not status or not status[0]:
            raise ValueError('race has no observed terminal state; elapsed clock alone is insufficient')
        predictions=conn.execute('''SELECT COUNT(*) FROM predictions
            WHERE session_oid=? AND ts BETWEEN ? AND ? AND net_position IS NOT NULL
            AND pos_in_class IS NOT NULL AND projected_finish IS NOT NULL''',
            (oid,int(start_s*1000),int(end_s*1000))).fetchone()[0]
        if not predictions:
            raise ValueError('no usable predictions recorded in acquisition window')
    return oid


def _reject_constant(value):
    raise ValueError(f'nonfinite capture value: {value}')


def check_wec_capture(path, expected_oid, require_terminal=True,
                      window_start_s=None, window_end_s=None):
    """Strict parsing/dispatch gate, retaining the legacy trailer-recovery behavior."""
    from db import RaceDB
    from wec_live import WecLiveClient
    path=Path(path)
    input_sha=_hash(path)
    frames=0
    timing_frames=0
    window_timing_frames=0
    trailer_complete=True
    first_ts=last_ts=None
    with tempfile.TemporaryDirectory(prefix='capture-check-') as tmp:
        client=WecLiveClient(db_path='',no_db=True)
        client.db=RaceDB(Path(tmp)/'race.db')
        try:
            with gzip.open(path,'rt',encoding='utf-8') as stream:
                try:
                    for line in stream:
                        if not line.strip():continue
                        row=json.loads(line,parse_constant=_reject_constant)
                        if not isinstance(row,dict) or not isinstance(row.get('channel'),str) or not row['channel']:
                            raise ValueError('invalid capture frame/channel')
                        ts=row.get('ts')
                        if type(ts) is not int or ts <= 0 or (last_ts is not None and ts < last_ts):
                            raise ValueError('missing or nonchronological recorded timestamp')
                        if first_ts is None:first_ts=ts
                        last_ts=ts
                        frames+=1
                        timing_channel = row['channel'] in ('ranks','gaps','laps','official-rank')
                        before = client.db.conn.execute('SELECT MAX(timing_updated_at) FROM standings_current').fetchone()[0] if timing_channel else None
                        if row['channel']=='_bootstrap':
                            if not isinstance(row.get('data'),dict):raise ValueError('invalid bootstrap')
                            client._hydrate_bootstrap(row['data'])
                        else:
                            client._dispatch_channel(row['channel'],row.get('data'),ts_ms=ts)
                        if timing_channel:
                            after = client.db.conn.execute('SELECT MAX(timing_updated_at) FROM standings_current').fetchone()[0]
                            if after is not None and after != before:
                                timing_frames+=1
                                if window_start_s is None or window_start_s <= ts/1000 <= window_end_s:
                                    window_timing_frames+=1
                except EOFError:
                    trailer_complete=False
            if not frames or client.state.session_oid != expected_oid:
                raise ValueError('raw capture does not match the acquired session')
            if not window_timing_frames:
                raise ValueError('raw capture contains no timing frames beyond bootstrap in the acquisition window')
            if require_terminal and not client.state.is_finished:
                raise ValueError('raw capture has no observed terminal race state')
            if _hash(path) != input_sha:
                raise ValueError('capture changed during validation')
            return {'input':path.name,'sha256':input_sha,'session_oid':expected_oid,
                    'frames':frames,'timing_frames':timing_frames,'window_timing_frames':window_timing_frames,'first_ts_ms':first_ts,'last_ts_ms':last_ts,
                    'terminal_observed':client.state.is_finished,
                    'gzip_trailer_complete':trailer_complete}
        finally:
            client.db.close()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--activity-only', action='store_true', help='practice/quali data validation; no race-completion claim')
    args = parser.parse_args(argv)
    try:
        result = check_wec_capture(args.capture, args.session, require_terminal=not args.activity_only)
    except Exception as exc:
        print(json.dumps({'verdict': 'FAIL', 'error': str(exc)}))
        return 1
    result['verdict'] = 'PASS' if result['gzip_trailer_complete'] else 'WARN'
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
