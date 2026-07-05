"""
timing71.py — reader for Timing71 replay archives (offline validation source).

A Timing71 replay is a zip of JSON frames:
  • manifest.json          — colSpec (column layout), startTime, uuid
  • <10-digit-ts>.json     — full-state frames: {cars, session, messages, ...}
  • <10-digit-ts>i.json    — incremental frames: per-cell deltas (mostly VFT ticks)

The richest signal is the per-frame `messages` log, which carries authoritative
pit in/out events (with stated durations), driver changes, and race control —
so we drive pit detection off messages, not the noisy Pits counter.

This module is read-only: it parses a replay into timelines we can validate
against and (later) drive through our own DB/calculator for accuracy testing.

CLI:  python src/timing71.py <replay.zip>   → prints a summary for verification.
"""

import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Optional


_PIT_DUR = re.compile(r"pit time:\s*(\d+):(\d+)")
_DRIVER_CHANGE = re.compile(r"Driver change \((.+?) to (.+?)\)")


def _cell(v):
    """Timing71 cells are sometimes [value, annotation] — return the value."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _num(v):
    v = _cell(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class PitStop:
    car: str
    in_ts: Optional[int] = None       # epoch ms entered pits
    out_ts: Optional[int] = None      # epoch ms left pits
    duration_s: Optional[float] = None  # stated pit time
    driver_from: Optional[str] = None
    driver_to: Optional[str] = None

    @property
    def is_driver_change(self) -> bool:
        return self.driver_to is not None


@dataclass
class Replay:
    name: str
    start_time: float
    col: dict
    full_frames: list          # [(ts_int, frame_dict)] sorted
    messages: list             # deduped [ts_ms, category, text, type, car] sorted asc
    series_name: str = ""      # manifest.name — the series identifier (e.g. "IMSA
                                # WeatherTech SportsCar Championship"), distinct from
                                # `name` which is manifest.description (the event title)

    # ── flag timeline ────────────────────────────────────────────────────────
    def flag_timeline(self) -> list:
        """[(ts_ms, flagState)] at each change."""
        out, last = [], None
        for ts, fr in self.full_frames:
            f = (fr.get("session") or {}).get("flagState")
            if f and f != last:
                out.append((ts * 1000, f))
                last = f
        return out

    # ── pit stops from the message log (authoritative) ───────────────────────
    def pit_stops(self) -> dict:
        """Per car → [PitStop], paired from 'entered'/'left' messages."""
        stops: dict[str, list] = {}
        open_stop: dict[str, PitStop] = {}
        for m in self.messages:
            ts, text = m[0], (m[2] if len(m) > 2 else "")
            typ = m[3] if len(m) > 3 else None
            car = m[4] if len(m) > 4 else None
            if not car:
                continue
            if typ == "pit" or "has entered the pits" in text:
                ps = open_stop.get(car) or PitStop(car=car)
                ps.in_ts = ts
                open_stop[car] = ps
            elif typ == "out" or "has left the pits" in text:
                ps = open_stop.pop(car, None) or PitStop(car=car)
                ps.out_ts = ts
                dur_match = _PIT_DUR.search(text)
                if dur_match:
                    ps.duration_s = int(dur_match.group(1)) * 60 + int(dur_match.group(2))
                elif ps.in_ts and ps.out_ts:
                    ps.duration_s = (ps.out_ts - ps.in_ts) / 1000
                stops.setdefault(car, []).append(ps)
            dc = _DRIVER_CHANGE.search(text)
            if dc:
                # attach to the most recent stop for this car (driver change at the stop)
                tgt = open_stop.get(car) or (stops.get(car) or [None])[-1]
                if tgt:
                    tgt.driver_from, tgt.driver_to = dc.group(1), dc.group(2)
        # flush any stop still open at end of replay (entered, never left)
        for car, ps in open_stop.items():
            stops.setdefault(car, []).append(ps)
        return stops

    # ── final standings snapshot (last full frame) ───────────────────────────
    def final_cars(self) -> dict:
        """Per car → {class, laps, pits, vft, last, best, pic} from the last frame.
        class/vft/pic are None when the archive's column set doesn't have them
        (single-class archives carry no Class/VFT/PIC)."""
        if not self.full_frames:
            return {}
        _ts, fr = self.full_frames[-1]
        cls_i, vft_i, pic_i = self.col.get("Class"), self.col.get("VFT"), self.col.get("PIC")
        out = {}
        for row in fr.get("cars", []):
            num = row[self.col["Num"]]
            out[num] = {
                "class": row[cls_i] if cls_i is not None else None,
                "laps": _num(row[self.col["Laps"]]),
                "pits": _num(row[self.col["Pits"]]),
                "vft": _num(row[vft_i]) if vft_i is not None else None,
                "last": _cell(row[self.col["Last"]]),
                "best": _cell(row[self.col["Best"]]),
                "pic": row[pic_i] if pic_i is not None else None,
            }
        return out


def load(zip_path: str) -> Replay:
    z = zipfile.ZipFile(zip_path)
    names = z.namelist()
    manifest = json.loads(z.read("manifest.json"))
    # resolve every column the manifest actually declares (different series'
    # archives carry different column sets — single-class ones have no
    # Class/PIC/VFT but may add T/PTP). Don't merge in the IMSA COL defaults:
    # doing so mapped a missing "Class" onto IMSA's index 2, which silently
    # aliased onto another archive's "Driver" column at that same index.
    # Callers use col.get(name) and must handle None for a column this
    # archive doesn't have.
    col = {spec[0]: i for i, spec in enumerate(manifest["colSpec"])}

    full = []
    for n in names:
        base = n[:-5] if n.endswith(".json") else n
        if base == "manifest" or base.endswith("i") or not base.lstrip("0").isdigit():
            continue
        full.append((int(base), json.loads(z.read(n))))
    full.sort(key=lambda t: t[0])

    # union + dedup messages across frames (each frame repeats a trailing window)
    seen, msgs = set(), []
    for _ts, fr in full:
        for m in fr.get("messages", []):
            key = (m[0], m[2])         # (timestamp_ms, text)
            if key not in seen:
                seen.add(key)
                msgs.append(m)
    msgs.sort(key=lambda m: m[0])

    return Replay(name=manifest.get("description", "?"),
                  start_time=manifest.get("startTime", 0),
                  col=col, full_frames=full, messages=msgs,
                  series_name=manifest.get("name", ""))


def _main():
    if len(sys.argv) < 2:
        print("usage: python src/timing71.py <replay.zip>")
        return
    r = load(sys.argv[1])
    span = (r.full_frames[-1][0] - r.full_frames[0][0]) if r.full_frames else 0
    print(f"Replay: {r.name}")
    print(f"  full frames: {len(r.full_frames)}  span: {span/3600:.2f} h  "
          f"messages: {len(r.messages)}")
    flags = r.flag_timeline()
    print(f"  flag changes: {len(flags)} → "
          + ", ".join(f"{f}" for _ts, f in flags[:12]))
    stops = r.pit_stops()
    finals = r.final_cars()
    total = sum(len(v) for v in stops.values())
    print(f"  pit stops parsed: {total} across {len(stops)} cars")
    # report the top class (GTP for IMSA, HYPERCAR for WEC, first alphabetically otherwise)
    all_classes = {(fin.get("class") or "").upper() for fin in finals.values() if fin.get("class")}
    top_class = next((c for c in ("GTP", "HYPERCAR") if c in all_classes), sorted(all_classes)[0] if all_classes else None)
    if top_class:
        print(f"  {top_class} pit counts (parsed vs replay's Pits column at last frame):")
        for car in sorted(stops, key=lambda c: c.lstrip('0') or c):
            fin = finals.get(car, {})
            if (fin.get("class") or "").upper() != top_class:
                continue
            dur = [s.duration_s for s in stops[car] if s.duration_s]
            avg = f"{sum(dur)/len(dur):.0f}s avg" if dur else "—"
            dcs = sum(1 for s in stops[car] if s.is_driver_change)
            print(f"    #{car:>3}  parsed={len(stops[car]):>2}  Pits_col={fin.get('pits')}"
                  f"  dchg={dcs}  {avg}")


if __name__ == "__main__":
    _main()
