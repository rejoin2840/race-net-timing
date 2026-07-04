# Race Net Timing

An **endurance race strategy companion** for watching IMSA and WEC at home.
It answers the question TV timing can't: **who is really winning once everyone's
remaining pit stops are accounted for?** — with honest confidence signaling, plus a
"while you were away" catch-up card for when you step away mid-race.

Secondary identity: a **strategy learning lab** — every race is replayed, predictions are
logged, and an evaluator grades them against the actual finish, closing the loop.

## What it computes

For every car, from live timing alone (no GPS/ECU access):

- **NET position** — track position adjusted for stops still owed (fuel, driver changes,
  pending penalties). The headline number; a *situational gauge*, strongest early/mid-race.
- **Projected finish** — a track-anchored blend (`w·net + (1−w)·track`, weight scales with
  stops remaining). Kept deliberately conservative.
- Pit-cost model (learned in-race from observed stops), pit windows / DUE TO PIT,
  catch & pass ETAs, undercut/overcut notes, penalty carry, caution tracking, tire deg,
  sector deltas, weather.

## Honest accuracy expectations

Validated on 6 complete IMSA replay archives (`src/validate_races.py`):

- Finish-prediction MAE (in-class positions): **track order 2.71 · pure net 2.80 ·
  shipped blend 2.69**. Net position is a *modest* edge, not a crystal ball — it adds
  signal while stops remain to cycle, and converges to track order late by design.
- Net's real job is **situational awareness** ("effective position now"), where it is not
  graded against the finish at all.
- Stop-duration MAE ≈ tens of seconds; catch calls are conservatively gated (trustworthy
  silence over noisy alerts).
- Biggest known accuracy levers, in order: **fuel telemetry integration → penalty parsing
  → series-specific tuning** (see `BACKLOG.md`).

## Series support

| Series | Live | Replay | Strategy model | Status |
|--------|------|--------|----------------|--------|
| IMSA   | ✅ `alkameldp.py` (Al Kamel DDP) | ✅ Timing71 zips | full (refuel + DC + penalties) | primary; 1 live race validated |
| WEC    | 🔧 `wec_live.py` in progress (SignalR + MessagePack) | Timing71 zips (TBD) | refuel path planned | Epic 8 — targeting São Paulo 07-12 |

## Quick start

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# main UI (calm board) — use the session picker button to launch feeds/replays
./venv/bin/python src/dashboard_calm.py

# or the dense pit-wall table
./venv/bin/python src/dashboard.py
```

No-terminal launch: `Overcut.app` at the project root (path is hardcoded inside
`Contents/MacOS/run` — update it if the folder moves).

## Common commands

```bash
# live IMSA scraper standalone (the dashboards can also launch it)
./venv/bin/python src/alkameldp.py            # add --discover to probe the feed

# stream a replay archive into the DB at 60× as if live (dashboard watches it)
./venv/bin/python src/replay.py "<archive>.zip" --stream --speed 60

# batch-build a replay DB + grade predictions
./venv/bin/python src/replay.py "<archive>.zip" --db /tmp/x.db
./venv/bin/python src/evaluator.py --db /tmp/x.db --session replay --force

# multi-race regression suite (any calculator/replay change MUST pass this)
./venv/bin/python src/validate_races.py

# unattended race-weekend supervisor (own Terminal tab, AC power, lid open)
caffeinate -s venv/bin/python src/weekend_conductor.py

# test gate — run before committing (add --full for the regression suite)
./check.sh
```

Replay archives are Timing71 zips. IMSA archives live in `ARCHIVE_DIR` (config.json,
default `~/Downloads`). **Only complete run-to-chequered archives belong in regression
sets** — truncated ones skew the numbers.

## Architecture

```
adapters (alkameldp / wec_live / replay)  →  SQLite data/race.db
    →  calculator.analyse()  (pure, read-only math; hot-reloads config.json)
    →  PyQt6 dashboards (dashboard_calm = main; dashboard = dense table)
    →  predictor → evaluator → validate_races  (the accuracy loop)
```

- Ingestion, computation, presentation, and evaluation are strictly separated; the UI
  never touches a websocket, the calculator never touches the UI.
- All tuning knobs live in `config.json` — edits apply in ~2s, mid-race, no restart.
- `evaluator.py --auto` optionally closes the loop with bounded, audited knob nudges
  (opt-in ⚙ AUTO-TUNE toggle in the dashboard; pauses in wet weather).
- Tests are standalone scripts (no pytest): `./venv/bin/python tests/test_calculator.py`.

## Roadmap

See `BACKLOG.md` — epics, acceptance criteria, decisions log, and the parked north-star
ideas live there, not in scattered code comments.
