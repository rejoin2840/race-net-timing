"""
evaluator.py — scores logged predictions against what actually happened.

Compute-aware: refuses to run until EVAL_START_AFTER_S (default 1 hour) of race
has elapsed, then does ONE bounded pass over everything logged since the start.
Run it on demand or from a scheduler every 30–60 min — never continuously.

Metrics:
  1. STOP TIME   — predicted next-stop duration vs the actual stop (from pit_events).
  2. NET POSITION — does our net position predict a car's FUTURE in-class position
                    better than its current track position does? (the core claim)
  3. CATCH        — did predicted catches actually happen, and how late?

It prints a report with tuning suggestions; it never changes the math itself.

Usage:
  python src/evaluator.py [--db X] [--session OID] [--force]
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import json
import os

import calculator
import config
import predictor

DB_PATH            = Path("data/race.db")
EVAL_START_AFTER_S = 3600      # hold off until 1 h of race elapsed
NET_HORIZON_S      = 1800      # "future" = 30 min ahead for net-position scoring
CATCH_TOL_LAPS     = 3         # a catch counts as hit within ± this many laps

# auto-tune guardrails: (min, max) bounds + step per run for each tunable knob
TUNE_BOUNDS = {
    "PACE_WINDOW":            (3, 10, 1),
    "PACE_OUTLIER_FACTOR":    (1.03, 1.15, 0.01),
}
TUNE_MIN_SAMPLES = 5          # need this many catch cases before touching pace knobs


CAUTION_FLAGS = {"YF", "FCY", "CY", "SC", "VSC", "FCY1", "SCS"}


# ── metric 1: stop-time accuracy ────────────────────────────────────────────
SHORT_STINT_LAPS = 20   # a stop after ≤ this many laps is a splash / penalty /
                        # repair — no model can foresee those, so they're
                        # reported separately from the predictable service stops

def eval_stop_time(conn, oid):
    stops = conn.execute(
        """SELECT car_number, pit_lap, stop_duration_ms, flag FROM pit_events
             WHERE session_oid=? AND stop_duration_ms IS NOT NULL AND pit_lap IS NOT NULL
             ORDER BY car_number, stop_number""", (oid,)).fetchall()
    buckets = {k: ([], []) for k in
               ("all", "caution", "green", "predictable", "short")}

    def _add(key, err, bias):
        buckets[key][0].append(err); buckets[key][1].append(bias)

    prev_pit_lap = {}
    for car, pit_lap, actual, flag in stops:
        stint = pit_lap - prev_pit_lap.get(car, 0)
        prev_pit_lap[car] = pit_lap
        pred = conn.execute(
            """SELECT next_stop_ms FROM predictions
                 WHERE session_oid=? AND car_number=? AND session_lap < ?
                       AND next_stop_ms IS NOT NULL
                 ORDER BY ts DESC LIMIT 1""", (oid, car, pit_lap)).fetchone()
        if pred and pred[0]:
            err = abs(pred[0] - actual)
            bias = pred[0] - actual
            _add("all", err, bias)
            _add("caution" if (flag or "").upper() in CAUTION_FLAGS else "green",
                 err, bias)
            _add("short" if stint <= SHORT_STINT_LAPS else "predictable",
                 err, bias)
    if not buckets["all"][0]:
        return None

    def _stats(key):
        errs, biases = buckets[key]
        return {"n": len(errs), "mae_ms": sum(errs) / len(errs),
                "bias_ms": sum(biases) / len(biases)} if errs else None

    result = _stats("all")
    for key in ("caution", "green", "predictable", "short"):
        s = _stats(key)
        if s:
            result[key] = s
    return result


# ── metric 2: net-position predictive power ─────────────────────────────────
def eval_net_position(conn, oid):
    rows = conn.execute(
        """SELECT ts, car_number, net_position, pos_in_class, projected_finish
             FROM predictions
             WHERE session_oid=? AND net_position IS NOT NULL AND pos_in_class IS NOT NULL
             ORDER BY car_number, ts""", (oid,)).fetchall()
    by_car: dict[str, list] = {}
    for ts, car, net, pic, proj in rows:
        by_car.setdefault(car, []).append((ts, net, pic, proj))

    # ── HEADLINE: does projected_finish — the forecast the dashboard actually
    # shows — predict the FINAL classification better than current track
    # position? Scored over every prediction vs each car's last observed
    # pos_in_class. Raw net_position is kept alongside as the ingredient
    # diagnostic: it is the pit-cycle-adjusted running order, and scoring it
    # as a finish forecast (the pre-2026-07 headline) penalised a number the
    # product never ships as its prediction.
    fnet, ftrack, fproj, fn, pn = 0.0, 0.0, 0.0, 0, 0
    for car, seq in by_car.items():
        final = seq[-1][2]                     # last pos_in_class = finishing spot
        for _ts, net, pic, proj in seq:
            fnet   += abs(net - final)
            ftrack += abs(pic - final)
            fn += 1
            if proj is not None:
                fproj += abs(proj - final)
                pn += 1

    # ── secondary metric: short-horizon stability (kept for reference only).
    # NOTE this STRUCTURALLY favours stay-put — 30 min out the pit shuffles
    # net forecasts haven't happened yet — so it is NOT the pass/fail signal.
    net_err, track_err, n = 0.0, 0.0, 0
    for car, seq in by_car.items():
        for i, (ts, net, pic, _proj) in enumerate(seq):
            target_ts = ts + NET_HORIZON_S * 1000
            future = next((p for (t, _, p, _pr) in seq[i + 1:] if t >= target_ts), None)
            if future is None:
                continue
            net_err   += abs(net - future)
            track_err += abs(pic - future)
            n += 1
    if fn == 0:
        return None
    fne, fte = fnet / fn, ftrack / fn
    fpe = fproj / pn if pn else None
    ne, te = (net_err / n, track_err / n) if n else (None, None)
    return {
        # headline (projected_finish vs final classification)
        "proj_mae": fpe, "proj_n": pn,
        "proj_improvement_pct": (((fte - fpe) / fte * 100)
                                 if (fpe is not None and fte) else None),
        # ingredient diagnostic (raw net vs final classification)
        "n": fn, "net_mae": fne, "track_mae": fte,
        "improvement_pct": ((fte - fne) / fte * 100) if fte else 0.0,
        # secondary (30-min horizon)
        "h_n": n, "h_net_mae": ne, "h_track_mae": te,
        "h_improvement_pct": ((te - ne) / te * 100) if te else 0.0,
    }


# ── metric 3: catch accuracy ────────────────────────────────────────────────
def eval_catch(conn, oid):
    preds = conn.execute(
        """SELECT ts, session_lap, car_number, catching, catch_in_laps
             FROM predictions
             WHERE session_oid=? AND catching IS NOT NULL AND catch_in_laps IS NOT NULL
             ORDER BY ts""", (oid,)).fetchall()
    # dedupe to first prediction of each (chaser→target) pairing
    seen, cases = set(), []
    for ts, lap, car, tgt, cl in preds:
        key = (car, tgt)
        if key in seen:
            continue
        seen.add(key)
        cases.append((ts, lap, car, tgt, lap + cl))
    if not cases:
        return None

    hits, eventual, lates = 0, 0, []
    for ts, lap, car, tgt, target_lap in cases:
        # find the first later moment the chaser was ahead of the target in class
        ahead = conn.execute(
            """SELECT pc.session_lap
                 FROM predictions pc JOIN predictions pt
                   ON pc.session_oid=pt.session_oid AND pc.ts=pt.ts
                WHERE pc.session_oid=? AND pc.car_number=? AND pt.car_number=?
                      AND pc.ts>? AND pc.pos_in_class < pt.pos_in_class
                ORDER BY pc.ts LIMIT 1""", (oid, car, tgt, ts)).fetchone()
        if ahead:
            eventual += 1
            lates.append(ahead[0] - target_lap)
            # a HIT means the pass landed near the predicted horizon — being
            # ahead 150 laps later is coincidence (retirement / strategy /
            # end-of-race shuffle), not a caught prediction. The pre-2026-07
            # any-time rule credited exactly those (all 14 SP "hits"
            # registered on the final lap); it survives as eventual_rate.
            horizon_laps = max(2 * (target_lap - lap), 5)
            if ahead[0] <= lap + horizon_laps:
                hits += 1
    med_late = sorted(lates)[len(lates) // 2] if lates else None
    return {"n": len(cases), "hits": hits,
            "hit_rate": hits / len(cases),
            "eventual_rate": eventual / len(cases),
            "median_late_laps": med_late}


# ── auto-tune ───────────────────────────────────────────────────────────────
def autotune(stop, net, catch, cfg) -> list[tuple]:
    """Bounded, damped knob adjustments from the metrics. Returns
    [(param, old, new, reason)]. Only touches knobs the data can actually inform.

    NOTE: fuel/stint/pit/DC costs are NOT here — those already self-learn from
    observed data inside the calculator. CAUTION_PENALTY_FACTOR is also not here:
    it only scales the live pit_now_position projection (calculator.py), never
    next_stop_ms — the value the STOP TIME caution bucket actually grades — so
    that bias can't inform it. Nudging it here would just walk the knob to a
    bound without ever moving the number that triggered the nudge. This nudges
    the pace knobs only, the one thing the catch data can directly inform."""
    changes = []

    if catch and catch["n"] >= TUNE_MIN_SAMPLES:
        ml = catch.get("median_late_laps")
        pw = cfg["PACE_WINDOW"]
        lo, hi, step = TUNE_BOUNDS["PACE_WINDOW"]
        if ml is not None and ml > CATCH_TOL_LAPS and pw < hi:
            changes.append(("PACE_WINDOW", pw, pw + step,
                            f"catches ~{ml:.1f}L late → smooth pace (overstated deltas)"))
        elif ml is not None and ml < -CATCH_TOL_LAPS and pw > lo:
            changes.append(("PACE_WINDOW", pw, pw - step,
                            f"catches ~{-ml:.1f}L early → sharpen pace"))
        elif catch["hit_rate"] < 0.5:
            of = cfg["PACE_OUTLIER_FACTOR"]
            lo2, hi2, step2 = TUNE_BOUNDS["PACE_OUTLIER_FACTOR"]
            if of < hi2:
                changes.append(("PACE_OUTLIER_FACTOR", of, round(of + step2, 3),
                                "low catch hit-rate → admit more laps into pace"))
    return changes


def apply_autotune(changes) -> None:
    """Atomically write the adjusted values into config.json (hot-reloaded by the app)."""
    if not changes:
        return
    config.CONFIG.reload_if_changed()
    cfg = config.CONFIG.as_dict()
    for param, _old, new, _reason in changes:
        cfg[param] = new
    tmp = str(config.CONFIG_PATH) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, str(config.CONFIG_PATH))           # atomic — no partial reads
    logp = Path("logs") / "autotune.log"
    logp.parent.mkdir(exist_ok=True)
    with logp.open("a", encoding="utf-8") as f:
        for param, old, new, reason in changes:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {param}: {old} → {new}  ({reason})\n")


# ── reporting ───────────────────────────────────────────────────────────────
def _suggest(stop, net, catch):
    s = []
    pstop = (stop or {}).get("predictable") or stop
    if pstop and abs(pstop["bias_ms"]) > 3000:
        d = "OVER" if pstop["bias_ms"] > 0 else "UNDER"
        s.append(f"Predictable stops {d}-predicted by {abs(pstop['bias_ms'])/1000:.1f}s "
                 f"avg — adjust transit floor / fuel fit / DC delta.")
    if stop and stop.get("caution") and abs(stop["caution"]["bias_ms"]) > 3000:
        cb = stop["caution"]["bias_ms"]
        s.append(f"Caution stops {('OVER' if cb > 0 else 'UNDER')}-predicted by "
                 f"{abs(cb)/1000:.1f}s — model.predict_stop() has no caution-awareness. "
                 f"(CAUTION_PENALTY_FACTOR won't fix this: it only scales the live "
                 f"pit_now_position call, not this prediction.)")
    if net and net.get("proj_improvement_pct") is not None:
        if net["proj_improvement_pct"] < 0:
            s.append("The shipped finish forecast (projected_finish) is WORSE than "
                     "current track position — check the blend weights and "
                     "est_stops_left.")
        elif net["proj_improvement_pct"] < 5:
            s.append("Finish forecast barely beats 'stay put' (may just be early — "
                     "its edge grows once stops start cycling).")
    if net and net["improvement_pct"] < 0:
        s.append("Raw net position (diagnostic) trails track position — "
                 "check est_stops_left and the pit-cost model.")
    if catch and catch["hit_rate"] < 0.5:
        s.append("Under half of predicted catches happened near the predicted "
                 "horizon — pace window may be too reactive; consider widening "
                 "PACE_WINDOW or filtering traffic laps.")
    if catch and catch["median_late_laps"] and catch["median_late_laps"] > CATCH_TOL_LAPS:
        s.append(f"Catches land ~{catch['median_late_laps']:.1f} laps later than predicted "
                 "— pace deltas likely overstated.")
    return s or ["No tuning flags — predictions tracking actuals within tolerance."]


def report(ctx, stop, net, catch) -> str:
    L = []
    L.append("=" * 72)
    L.append(f"  ACCURACY REPORT — {ctx.event} · {ctx.session_name}")
    L.append(f"  elapsed {ctx.elapsed_s/60:.0f} min · lap {ctx.current_lap} · "
             f"{datetime.now():%H:%M:%S}")
    L.append("=" * 72)
    if stop:
        p = stop.get("predictable")
        if p:
            L.append(f"  STOP TIME    n={p['n']:<3}  MAE {p['mae_ms']/1000:5.1f}s  "
                     f"bias {p['bias_ms']/1000:+5.1f}s  (predictable: stint >"
                     f" {SHORT_STINT_LAPS} laps)")
        else:
            L.append(f"  STOP TIME    (no predictable-stint stops yet)")
        if stop.get("short"):
            s = stop["short"]
            L.append(f"    short-stint n={s['n']:<3}  MAE {s['mae_ms']/1000:5.1f}s  "
                     f"bias {s['bias_ms']/1000:+5.1f}s  ← splash/penalty/repair, "
                     f"unforecastable")
        L.append(f"    combined   n={stop['n']:<3}  MAE {stop['mae_ms']/1000:5.1f}s  "
                 f"bias {stop['bias_ms']/1000:+5.1f}s")
        if stop.get("green"):
            g = stop["green"]
            L.append(f"    green      n={g['n']:<3}  MAE {g['mae_ms']/1000:5.1f}s  "
                     f"bias {g['bias_ms']/1000:+5.1f}s")
        if stop.get("caution"):
            c = stop["caution"]
            flag = ("  ← predict_stop has no caution-awareness"
                    if abs(c["bias_ms"]) > 3000 else "")
            L.append(f"    caution    n={c['n']:<3}  MAE {c['mae_ms']/1000:5.1f}s  "
                     f"bias {c['bias_ms']/1000:+5.1f}s{flag}")
    else:
        L.append("  STOP TIME    (no completed stops with a prior prediction yet)")
    if net:
        if net.get("proj_mae") is not None:
            pv = "FORECAST WINS" if net["proj_improvement_pct"] > 0 else "track wins"
            L.append(f"  FINISH       n={net['proj_n']:<3}  proj MAE {net['proj_mae']:.2f}  "
                     f"track {net['track_mae']:.2f}  "
                     f"→ {net['proj_improvement_pct']:+.0f}%  [{pv}]")
        L.append(f"    net (diag) n={net['n']:<3}  net MAE {net['net_mae']:.2f}  "
                 f"track {net['track_mae']:.2f}  → {net['improvement_pct']:+.0f}%"
                 f"  (pit-cycle running order, not the shipped forecast)")
        if net.get("h_net_mae") is not None:
            L.append(f"               30-min horizon (ref only, favours stay-put): "
                     f"net {net['h_net_mae']:.2f} vs {net['h_track_mae']:.2f}")
    else:
        L.append("  FINISH       (no predictions with a final classification yet)")
    if catch:
        ml = catch["median_late_laps"]
        ml_s = f"{ml:.1f}" if ml is not None else "—"
        L.append(f"  CATCH        n={catch['n']:<3}  hit-rate {catch['hit_rate']*100:.0f}%  "
                 f"(within 2× predicted laps; eventually-passed "
                 f"{catch['eventual_rate']*100:.0f}%, median lateness {ml_s} laps)")
    else:
        L.append("  CATCH        (no catch predictions yet)")
    L.append("-" * 72)
    L.append("  SUGGESTIONS:")
    for s in _suggest(stop, net, catch):
        L.append(f"    • {s}")
    L.append("=" * 72)
    return "\n".join(L)


def oneline(stop, net, catch, changes=None) -> str:
    parts = [f"acc {datetime.now():%H:%M}"]
    if net:
        if net.get("proj_improvement_pct") is not None:
            parts.append(f"proj {net['proj_improvement_pct']:+.0f}%")
        else:
            parts.append(f"net {net['improvement_pct']:+.0f}%")
    if stop:
        pstop = stop.get("predictable") or stop
        parts.append(f"stop {pstop['bias_ms']/1000:+.1f}s")
    if catch:
        parts.append(f"catch {catch['hit_rate']*100:.0f}%")
    if not (net or stop or catch):
        parts.append("no data yet")
    if changes:
        parts.append("tuned " + ", ".join(f"{p} {o}→{n}" for p, o, n, _ in changes))
    return " · ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--session", default=None)
    ap.add_argument("--force", action="store_true",
                    help="evaluate even before the 1-hour gate")
    ap.add_argument("--oneline", action="store_true",
                    help="print a single compact summary line (for the app); still logs full report")
    ap.add_argument("--auto", action="store_true",
                    help="apply bounded knob adjustments to config.json (logs to logs/autotune.log)")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print("acc no data" if args.oneline else "No database — run a session first.",
              file=sys.stderr if not args.oneline else sys.stdout)
        sys.exit(0 if args.oneline else 1)
    conn = sqlite3.connect(args.db); conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")   # runs concurrently with the live writers
    predictor.ensure(conn)          # tolerate DBs created before the predictions table
    oid = args.session or calculator.latest_session(conn)
    if not oid:
        print("acc no session" if args.oneline else "No sessions in database.")
        return

    ctx = calculator._load_context(conn, oid)
    if not args.force and ctx.elapsed_s < EVAL_START_AFTER_S:
        mins = (EVAL_START_AFTER_S - ctx.elapsed_s) / 60
        if args.oneline:
            print(f"acc holding · {mins:.0f} min to first read")
        else:
            print(f"Holding off: {ctx.elapsed_s/60:.0f} min elapsed, "
                  f"evaluator starts at {EVAL_START_AFTER_S/60:.0f} min "
                  f"(~{mins:.0f} min to go). Use --force to override.")
        return

    stop  = eval_stop_time(conn, oid)
    net   = eval_net_position(conn, oid)
    catch = eval_catch(conn, oid)
    text  = report(ctx, stop, net, catch)

    changes = []
    if args.auto:
        changes = autotune(stop, net, catch, config.CONFIG.as_dict())
        apply_autotune(changes)                # writes config.json + autotune.log

    out = Path("logs") / f"eval_{datetime.now():%Y%m%d_%H%M%S}.txt"
    out.parent.mkdir(exist_ok=True)
    if changes:
        text += "\n  AUTO-TUNED:\n" + "\n".join(
            f"    • {p}: {o} → {n}  ({r})" for p, o, n, r in changes)
    out.write_text(text, encoding="utf-8")     # always log the full report

    if args.oneline:
        print(oneline(stop, net, catch, changes))   # compact line for the app to capture
    else:
        print(text)
        print(f"\nsaved → {out}")
    conn.close()


if __name__ == "__main__":
    main()
