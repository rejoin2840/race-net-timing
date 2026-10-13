# Overcut

> Living reference — current status: [BACKLOG.md](BACKLOG.md).

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

## Confidence and validation

NET is a pit-cycle situational gauge; projected finish is a conservative,
track-anchored estimate. The product's goal is comprehension with honest
confidence signals. Unavailable or stale data suppresses actionable strategy.

Replay ingests stops and drivers chronologically. Stop duration is unavailable
until pit exit; final classification is used only for scoring. The regression
runner checks required inputs, terminal race evidence, and a reviewed numerical
baseline. Current evidence, limitations, and live acceptance checks live in
[BACKLOG.md](BACKLOG.md#epic-11--validation-integrity-and-race-state-reliability).

## Series support

| Series | Live timing | Replay | Strategy |
|--------|-------------|--------|----------|
| IMSA | Al Kamel DDP | Timing71 ZIP | Pit-cycle order, penalties, inferred stint windows |
| WEC | Griiip SignalR | Timing71 ZIP and raw captures | Pit-cycle order, penalties, virtual energy tank |

## Quick start

### Prerequisites
- Python 3.12
- macOS or Linux (Windows untested)
- For live IMSA races: nothing extra — feed is public
- For offline replay: a Timing71 `.zip` archive (two sample races are included, see below)

First-time setup: run `./setup.sh`. This creates the Python environment and launches the dashboard. The macOS launcher resolves the project folder relative to its bundle.

```bash
./setup.sh
```

After first setup, launch directly:
```bash
./venv/bin/python src/dashboard_calm.py

# or the dense pit-wall table
./venv/bin/python src/dashboard.py
```

### Web UI
A second display layer — Electron + React — lives in `ui/`. Same engine, same
database; denser rows than the PyQt6 board (last lap vs. personal best, real
fuel % + stint laps, next-pit timing, NET projection, a notes lane for battle/
undercut/overcut calls), a tap-to-explain panel (click any car for its net-math
breakdown, per-stop cost, and pit history), and a full-width flag band. Setup
and run recipe: [ui/README.md](ui/README.md). The PyQt6 dashboard remains the
reliability-proven race-day display until the web board earns that trust at a
live event.

![Web board with the tap-to-explain panel open for car #60 — rows show last
lap vs. personal best, fuel % and stint laps, next-pit timing, gap, NET
projection, and a notes lane; the panel breaks down net position: on-track
gap, stops left, penalty carry, net gap ±band, and pit history](docs/img/web-board.png)
*(shown with mock data — browser dev mode)*

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
caffeinate -s venv/bin/python src/weekend_conductor.py --schedule config/weekend_schedule.json

# test gate — run before committing (add --full for the regression suite)
./check.sh
```

The conductor uses the Python interpreter that launched it for every child. To
use an isolated verified environment, launch it with `venv312/bin/python`.
Each window saves `logs/capture_<session>_<timestamp>.json` with PASS/WARN/FAIL,
its acquired session, runtime, and WEC capture identity. A failed window makes
the conductor exit nonzero. Old sessions cannot satisfy a new window; races need
recorded predictions and an observed terminal state. WEC raw capture validation
uses a scratch DB and requires timing frames in that acquisition window.

Check a raw WEC race capture independently (practice/quali: add `--activity-only`):

```sh
./venv312/bin/python src/capture_readiness.py --capture data/CAPTURE.jsonl.gz --session SESSION_OID
```

A terminal capture with a missing gzip trailer reports WARN; intact gzip without
terminal race evidence fails. Malformed frames, dispatch failures and mismatched
sessions fail. These checks establish captured-window activity and terminal state;
they do not prove uninterrupted coverage since green or steward-amended final results.
Live recovery and display acceptance remain in [BACKLOG.md](BACKLOG.md).


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

## Evaluation gate

`./check.sh --full` validates the IMSA set against `data/validation_baseline.json`.
WEC changes additionally require `src/validate_races.py --wec`; finish changes
require `--all`. Missing inputs and failed/invalid evaluations fail the command.
`--json /tmp/results.json` writes metrics and code/config/input identities.

A deliberate baseline refresh uses `--all --write-baseline /tmp/new-baseline.json`.
The output path must not exist. Review the complete result before replacing the
tracked baseline; a regression failure never refreshes it automatically.

For scheduled capture, copy `config/weekend_schedule.example.json` to the ignored
`config/weekend_schedule.json`. Set timezone, starts, and capture-window durations.
Overlapping same-series windows are rejected. Child exits are monitored with a
three-restart limit; stale acquisition is reported and can force a restart.
