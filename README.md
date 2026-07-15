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

Validated on 6 complete IMSA replay archives (`src/validate_races.py`), post the 2026-07-13
honest recalibration (PR #13):

- Finish-prediction MAE (in-class positions): **track order ≈2.45 · pure net ≈2.75 ·
  shipped blend ≈2.45**. A 14-race sweep across IMSA + WEC found the previous blend
  weights were the *worst* option tested — they leaned on net hardest early-race, exactly
  when net is least reliable. Weights were halved (`FINISH_BLEND_MAX_W`/`W_PER_STOP`
  0.6/0.15 → 0.3/0.08); the blend is now track-level on average and only pulls ahead on
  deep-pit-cycle races (Daytona 24h, Lone Star, Imola).
- Net position is not a crystal ball — it adds signal while stops remain to cycle, and
  converges to track order late by design. Its real job is **situational awareness**
  ("effective position now"), where it is not graded against the finish at all.
- Three net-ordering bugs on the WEC/Griiip feed (un-lapping heuristic misfiring,
  laps/elapsed channel desync, lapped-car tie-breaking) were fixed in the same pass —
  WEC net MAE 5.20 → 4.15 on the São Paulo capture, and the error now decays through the
  race the way track order's does, instead of staying flat.
- Stop-duration MAE ≈ tens of seconds; catch calls are conservatively gated (trustworthy
  silence over noisy alerts).
- Biggest known accuracy levers, in order: **fuel telemetry integration → penalty parsing
  → series-specific tuning** (see `BACKLOG.md`).

## Series support

| Series | Live | Replay | Strategy model | Status |
|--------|------|--------|----------------|--------|
| IMSA   | ✅ `alkameldp.py` (Al Kamel DDP) | ✅ Timing71 zips | full (refuel + DC + penalties) | primary; 1 live race validated |
| WEC    | ✅ `wec_live.py` (SignalR + MessagePack, Griiip feed) | Timing71 zips (TBD) | net position live, refuel path planned | Epic 8 — proven in the São Paulo 6h race (07-12); net-ordering bugs fixed 07-13 |

## Quick start

### Prerequisites
- Python 3.11+ (3.12 recommended)
- macOS or Linux (Windows untested)
- For live IMSA races: nothing extra — feed is public
- For offline replay: a Timing71 `.zip` archive (two sample races are included, see below)

First-time setup: run `./setup.sh`. This creates the Python environment and launches the dashboard. Ignore `Overcut.app` on machines other than the original dev machine (hardcoded path inside).

```bash
./setup.sh
```

After first setup, launch directly:
```bash
./venv/bin/python src/dashboard_calm.py

# or the dense pit-wall table
./venv/bin/python src/dashboard.py
```

### Web UI (new, 2026-07-15)
A second display layer — Electron + React — lives in `ui/`. Same engine, same
database; adds a tap-to-explain panel (click any car for its net-math breakdown
and pit history) and a NET projection column. Setup and run recipe:
[ui/README.md](ui/README.md). The PyQt6 dashboard remains the reliability-proven
race-day display until the web board earns that trust at a live event.

### Try it immediately (no live race needed)
Two complete IMSA sprint-race archives are included in `sample-archives/` (Long Beach 2026 · Detroit 2026). To load one: launch the dashboard, click **Session** in the top-left header, choose **IMSA → Replay**, and select a file from `sample-archives/`.

If the replay picker doesn't find them automatically, set `ARCHIVE_DIR` in `config.json` to the folder where your archives live.

New here? Read [USER_GUIDE.md](USER_GUIDE.md) for a screen-by-screen walkthrough. Developers, see [ARCHITECTURE.md](ARCHITECTURE.md).

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
default `IMSA Archives/` in the repo root). **Only complete run-to-chequered archives belong in regression
sets** — truncated ones skew the numbers.

## Architecture

```
adapters (alkameldp / wec_live / replay)  →  SQLite data/race.db
    →  calculator.analyse()  (pure math; hot-reloads config.json)
    →  poller.py  (Qt-free polling loop; writes net_analysis back to the DB)
    ├──→  PyQt6 dashboards (dashboard_calm = main; dashboard = dense table)
    ├──→  Electron/React web UI (ui/ — reads the DB readonly; net math via
    │     net_analysis, populated by a dashboard or src/poller_daemon.py)
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
