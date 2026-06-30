"""
catchup.py — the "while you were away" diff engine.

Paul's #1 job-to-be-done is glancing back at the board after stepping away and
instantly seeing WHAT CHANGED — not re-reading the whole screen. This module marks a
moment (a Snapshot of the field) and, on return, diffs it against the current Snapshot
to produce a short, ranked brief of the meaningful changes: class-lead changes, served
penalties / DQs / retirements, cautions, real position moves, and pit stops.

Like race_control.py it is a PURE module — no PyQt, no dashboard import. It duck-types
the calculator's CarAnalysis / RaceContext (reads fields via getattr) so it can be
unit-tested headless against a replay DB. It returns semantic `tone` strings; the
dashboard maps tone → palette hex. Race-control events reuse race_control.feed() +
penalties (the same classifier the rail uses), so the brief and the rail stay consistent.
"""

from dataclasses import dataclass, field
from typing import Optional
import time

import race_control
import penalties

# ── tunables ──────────────────────────────────────────────────────────────────
MIN_POS_MOVE = 2     # in-class spots a car must move to be worth a line (filters jitter)
MAX_EVENTS   = 8     # cap on the brief — a glance, not a report
BADGE_TTL_S  = 30    # how long the inline "SINCE" trail lingers after the card is dismissed

# semantic tones (dashboard maps these → calm-palette hex)
LEAD = "lead"; GAIN = "gain"; LOSS = "loss"; PIT = "pit"
PENALTY = "penalty"; DQ = "dq"; RESCINDED = "rescinded"; RETIRED = "retired"
CAUTION = "caution"

# rank floors per event type (higher = more important; ties broken by magnitude)
_RANK = {LEAD: 100, DQ: 95, RESCINDED: 92, PENALTY: 90, CAUTION: 70,
         RETIRED: 60, GAIN: 40, LOSS: 40, PIT: 30}


# ── data model ────────────────────────────────────────────────────────────────
@dataclass
class CarState:
    car:       str
    cls:       str
    driver:    str = ""
    team:      str = ""
    pos:       Optional[int] = None     # in-class position (effective if mid-stop, else feed)
    overall:   Optional[int] = None
    net:       Optional[int] = None
    stops:     int = 0
    laps_down: int = 0
    penalty_s: float = 0.0
    dq:        bool = False


@dataclass
class Snapshot:
    ts:            float
    lap:           int = 0
    flag:          str = ""
    caution_count: int = 0
    cautions:      list = field(default_factory=list)   # [(start_lap, end_lap, dur_s)]
    cars:          dict = field(default_factory=dict)    # car_number → CarState


@dataclass
class Event:
    tone: str
    text: str                       # primary phrase (the card prepends "#car")
    car:  Optional[str] = None      # subject car (None = field-wide, e.g. a caution)
    cls:  Optional[str] = None
    sub:  str = ""                  # secondary detail
    rank: float = 0.0


def snapshot(ctx, cars, ts: Optional[float] = None) -> Snapshot:
    """Freeze the diff-relevant slice of the current analysis. getattr-defensive so it
    survives partial/early-race CarAnalysis objects and is testable with stubs."""
    out = {}
    for c in cars:
        num = str(getattr(c, "car_number", "") or "")
        if not num:
            continue
        out[num] = CarState(
            car=num,
            cls=getattr(c, "car_class", "") or "",
            driver=getattr(c, "driver", "") or "",
            team=getattr(c, "team", "") or "",
            pos=(getattr(c, "effective_pos_in_class", None) or getattr(c, "pos_in_class", None)),
            overall=getattr(c, "track_position", None),
            net=getattr(c, "net_position", None),
            stops=int(getattr(c, "stops", 0) or 0),
            laps_down=int(getattr(c, "laps_down", 0) or 0),
            penalty_s=float(getattr(c, "penalty_s", 0.0) or 0.0),
            dq=bool(getattr(c, "dq", False)),
        )
    return Snapshot(
        ts=(ts if ts is not None else time.time()),
        lap=int(getattr(ctx, "current_lap", 0) or 0),
        flag=getattr(ctx, "flag", "") or "",
        caution_count=int(getattr(ctx, "caution_count", 0) or 0),
        cautions=list(getattr(ctx, "cautions", []) or []),
        cars=out,
    )


def _class_leader(snap: Snapshot, cls: str) -> Optional[str]:
    """The car running P1 in a class in this snapshot (None if unknown)."""
    best, best_pos = None, None
    for s in snap.cars.values():
        if s.cls != cls or s.pos is None:
            continue
        if best_pos is None or s.pos < best_pos:
            best, best_pos = s.car, s.pos
    return best


def _strip_car_prefix(msg: str) -> str:
    """Trim a leading 'Car 7 - ' / 'Cars 1, 40: ' prefix so the chip doesn't repeat the
    car number the card already shows. Falls back to the full message."""
    low = msg.lower()
    if low.startswith("car"):
        for sep in (" - ", ": ", " — "):
            i = msg.find(sep)
            if 0 < i < 32:
                return msg[i + len(sep):].strip()
    return msg.strip()


def _rc_events(rc_since) -> list:
    """Penalty / DQ / retirement / rescind events that happened while away, from the RC
    feed (filtered upstream to ts > mark). Reuses race_control.classify + penalties so the
    brief matches the rail. One event per named car; field-wide if no car is parseable."""
    out = []
    for msg, _tier, kind in race_control.feed(rc_since, limit=20):
        tone = {"dq": DQ, "penalty": PENALTY, "rescinded": RESCINDED,
                "retired": RETIRED}.get(kind)
        if tone is None:                       # flag/review/warning/incident — rail-only
            continue
        cars = penalties._extract_cars(msg) or [None]
        text = _strip_car_prefix(msg)
        for car in cars:
            out.append(Event(tone=tone, text=text, car=(str(car) if car else None),
                             rank=_RANK[tone]))
    return out


def summarize(old: Snapshot, new: Snapshot, rc_since=None, cap: Optional[int] = None) -> list:
    """Diff two snapshots (+ the race-control lines logged between them) into a ranked,
    deduped brief. Car-scoped events keep only the highest-rank per car (a penalty
    outranks the position loss it caused); field-wide events (cautions) are always kept.
    Returns the full ranked list, most important first, unless `cap` is given."""
    per_car: dict = {}          # car → best Event so far
    globals_: list = []         # field-wide events (no single subject car)

    def offer(ev: Event):
        if ev.car is None:
            globals_.append(ev)
            return
        cur = per_car.get(ev.car)
        if cur is None or ev.rank > cur.rank:
            per_car[ev.car] = ev

    # 1. class-lead changes — the loudest "the race changed" signal
    classes = {s.cls for s in new.cars.values() if s.cls}
    for cls in classes:
        new_ldr = _class_leader(new, cls)
        old_ldr = _class_leader(old, cls)
        if new_ldr and old_ldr and new_ldr != old_ldr:
            offer(Event(tone=LEAD, text=f"new {cls} leader", car=new_ldr, cls=cls,
                        sub=f"passed #{old_ldr}", rank=_RANK[LEAD]))

    # 2. race-control: penalties / DQ / retirements / rescinds (authoritative text)
    for ev in _rc_events(rc_since or []):
        ev.cls = new.cars.get(ev.car).cls if ev.car in new.cars else None
        offer(ev)

    # 3. cautions opened while away (or one still running) — field-wide
    if new.caution_count > old.caution_count and new.cautions:
        start, end, _dur = new.cautions[-1]
        ongoing = end is None or race_control_under_caution(new.flag)
        if ongoing:
            text = f"caution since L{start}"          # not yet closed (or still yellow now)
        else:
            text = f"caution L{start}–L{end}"
        offer(Event(tone=CAUTION, text=text, rank=_RANK[CAUTION]))

    # 4. position moves + 5. pit stops (per car, lower rank — trimmed first by the cap)
    for num, ns in new.cars.items():
        os_ = old.cars.get(num)
        if os_ is None:
            continue
        pitted = ns.stops > os_.stops
        if pitted:                                     # a fresh stop while away
            sub = f"rejoined P{ns.pos}" if ns.pos else ""
            offer(Event(tone=PIT, text="pitted", car=num, cls=ns.cls, sub=sub,
                        rank=_RANK[PIT]))
        if ns.pos is not None and os_.pos is not None and ns.laps_down == 0:
            delta = os_.pos - ns.pos                    # +ve = gained spots
            if abs(delta) >= MIN_POS_MOVE:
                if delta > 0:
                    offer(Event(tone=GAIN, text=f"▲ gained {delta} → P{ns.pos}",
                                car=num, cls=ns.cls, rank=_RANK[GAIN] + delta))
                elif not pitted:
                    # a car that pitted dropped BECAUSE it pitted — the pit event already
                    # carries "rejoined P{n}", so don't double-report it as a bare loss.
                    offer(Event(tone=LOSS, text=f"▼ lost {-delta} → P{ns.pos}",
                                car=num, cls=ns.cls, rank=_RANK[LOSS] + (-delta)))

    events = globals_ + list(per_car.values())
    events.sort(key=lambda e: e.rank, reverse=True)
    return events[:cap] if cap else events


def race_control_under_caution(flag: str) -> bool:
    """Local caution test (mirrors calculator._is_caution without importing it — keeps
    this module dependency-light). Yellow / full-course-yellow / safety-car states."""
    f = (flag or "").upper()
    return f in ("FCY", "FULL_COURSE_YELLOW", "YELLOW", "SC", "SAFETY_CAR", "CAUTION")
