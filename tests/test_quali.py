"""Unit tests for src/quali.py — the knockout-qualifying cut-line math.

Run (no pytest dependency — matches the other tests in this dir):
  ./venv/bin/python tests/test_quali.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import quali  # noqa: E402


def test_advance_counts_classic_20_car_grid():
    # 20 entries: Q1 top 15 advance, Q2 top 10 advance — the format F1 ran for years.
    c = quali.advance_counts(20)
    assert c == {"Q1": 15, "Q2": 10}


def test_advance_counts_2026_22_car_grid():
    # 22 entries (Cadillac expansion): Q1 top 16 advance, Q2 top 10 — confirmed
    # against real 2026 FastF1 results (Austrian GP, Miami GP), not assumed.
    c = quali.advance_counts(22)
    assert c == {"Q1": 16, "Q2": 10}


def test_advance_counts_odd_grid_size():
    # 21 entries: total cut = 11, split ceil/floor → Q1 cuts 6 (→15), Q2 cuts 5 (→10).
    c = quali.advance_counts(21)
    assert c == {"Q1": 15, "Q2": 10}


def test_advance_counts_field_at_or_below_q3_size():
    # a field this small never gets cut at all
    c = quali.advance_counts(10)
    assert c == {"Q1": 10, "Q2": 10}
    c = quali.advance_counts(8)
    assert c == {"Q1": 8, "Q2": 8}


def test_next_segment_progression():
    assert quali.next_segment("Q1") == "Q2"
    assert quali.next_segment("Q2") == "Q3"
    assert quali.next_segment("Q3") is None


def test_analyse_ranks_by_best_lap_and_flags_the_cut(tmp_path=None):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sessions (session_oid TEXT PRIMARY KEY, event_name TEXT);
        CREATE TABLE quali_status (
            session_oid TEXT PRIMARY KEY, segment TEXT, entries INTEGER,
            segment_elapsed_s INTEGER, segment_total_s INTEGER, is_finished INTEGER);
        CREATE TABLE quali_standings (
            session_oid TEXT, segment TEXT, car_number TEXT, best_lap_ms INTEGER,
            last_lap_ms INTEGER, laps INTEGER, rank INTEGER,
            PRIMARY KEY (session_oid, segment, car_number));
    """)
    oid = "test_q"
    conn.execute("INSERT INTO sessions VALUES (?,?)", (oid, "Test GP"))
    conn.execute("INSERT INTO quali_status VALUES (?,?,?,?,?,?)",
                 (oid, "Q1", 4, 300, 1080, 0))
    # 4 entries, Q3_ADVANCE=10 so entries<=10 → nobody cut (advance_n == entries == 4)
    laps = [("1", 90000), ("2", 91000), ("3", 89000), ("4", None)]
    for car, best in laps:
        conn.execute(
            "INSERT INTO quali_standings VALUES (?,?,?,?,?,?,?)",
            (oid, "Q1", car, best, best, 1 if best else 0, None))
    ctx, cars = quali.analyse(conn, oid)
    assert ctx.segment == "Q1"
    assert ctx.advance_n == 4          # field too small to cut
    order = [c.car_number for c in cars]
    assert order == ["3", "1", "2", "4"]     # fastest first, untimed car last
    assert all(c.advancing for c in cars)     # nobody cut when advance_n == entries


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
