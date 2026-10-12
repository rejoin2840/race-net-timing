"""Validate event windows before any capture processes are started."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def load_schedule(path):
    data = json.loads(path.read_text())
    zone = ZoneInfo(data["timezone"])
    result = []
    labels = set()
    for item in data["sessions"]:
        series, label, kind = item["series"], item["label"], item["kind"]
        if series not in ("imsa", "wec") or kind not in ("practice", "quali", "sprint_race", "race"):
            raise ValueError("unsupported series/session kind")
        if not label or (series, label) in labels:
            raise ValueError("empty or duplicate session label")
        labels.add((series, label))
        start = datetime.fromisoformat(item["start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
            # Reject nonexistent/ambiguous local times; explicit offset resolves DST.
            if start.replace(fold=0).utcoffset() != start.replace(fold=1).utcoffset():
                raise ValueError("DST transition requires explicit UTC offset")
        start = start.astimezone(zone)
        duration = item["duration_minutes"]
        if type(duration) is not int or not 1 <= duration <= 48 * 60:
            raise ValueError("duration_minutes must be 1..2880")
        result.append((series, label, kind, start, duration))
    result.sort(key=lambda r: r[3])
    for i, a in enumerate(result):
        for b in result[i + 1:]:
            if a[0] == b[0] and b[3] - timedelta(minutes=15) < a[3] + timedelta(minutes=a[4]):
                raise ValueError(f"overlapping {a[0]} capture windows: {a[1]} / {b[1]}")
    return result
