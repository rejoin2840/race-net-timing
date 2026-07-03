"""
build_rc_fixtures.py — regenerate tests/fixtures/rc_messages_imsa.txt from the
6 IMSA Timing71 archives in ARCHIVE_DIR.

Run from the repo root:
  venv/bin/python tests/fixtures/build_rc_fixtures.py

Commit the output file so tests never depend on ~/Downloads being present.
Re-run when a new archive is added to the regression set.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import config  # noqa: E402
import timing71  # noqa: E402

DL = os.path.expanduser(config.CONFIG.ARCHIVE_DIR)

ARCHIVES = [
    f"{DL}/2026-01-24 18-37 IMSA WeatherTech SportsCar Championship - Rolex 24 at Daytona - Race.zip",
    f"{DL}/2025-10-11 16-07 IMSA WeatherTech SportsCar Championship - 28th Annual Motul Petit Le Mans - Race.zip",
    f"{DL}/2025-09-21 15-37 IMSA WeatherTech SportsCar Championship - Tire Rack.com Battle On The Bricks - Race.zip",
    f"{DL}/2026-05-03 19-57 IMSA WeatherTech SportsCar Championship - StubHub Monterey SportsCar Championship - Race.zip",
    f"{DL}/2026-04-18 20-02 IMSA WeatherTech SportsCar Championship - Acura Grand Prix of Long Beach - Race.zip",
    f"{DL}/2026-05-30 19-57 IMSA WeatherTech SportsCar Championship - Chevrolet Detroit Sports Car Classic - Race.zip",
]

OUT = os.path.join(os.path.dirname(__file__), "rc_messages_imsa.txt")


def build() -> int:
    seen: set[str] = set()
    lines: list[str] = []
    for path in ARCHIVES:
        if not os.path.exists(path):
            print(f"  MISSING: {os.path.basename(path)}", file=sys.stderr)
            continue
        r = timing71.load(path)
        rc = [m for m in r.messages if m[3] == "raceControl"]
        before = len(seen)
        for m in rc:
            text = (m[2] if len(m) > 2 else "").strip()
            if text and text not in seen:
                seen.add(text)
                lines.append(text)
        new_unique = len(seen) - before
        print(f"  {len(rc):4d} RC rows  {new_unique:3d} new unique  {os.path.basename(path)[:60]}")

    lines.sort()
    with open(OUT, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {len(lines)} unique RC messages → {OUT}")
    return len(lines)


if __name__ == "__main__":
    build()
