# Architecture

A technical map of the codebase for developers reading the code.

---

## Data flow

```
Live feeds                         Replay
  alkameldp.py  (IMSA Al Kamel)       replay.py  (Timing71 .zip archive)
  wec_live.py   (WEC Griiip/SignalR)  └── streams rows into the DB at configurable speed
       │
       ▼
   data/race.db  (SQLite — the single shared state)
       │
       ▼
  calculator.analyse()   ← pure math, reads DB, never writes
       │
  poller.py  (Qt-free poll loop; writes computed net math back to the
       │      DB's net_analysis table as a side effect of every poll)
       ├── dashboard_calm.py  (main PyQt6 UI — "calm board")
       ├── dashboard.py       (dense pit-wall table, fallback)
       ├── poller_daemon.py   (headless net_analysis writer — run this
       │                       when using the web UI without a dashboard)
       ├── ui/                (Electron/React web UI — reads the DB
       │                       readonly via better-sqlite3, no Python)
       ▼
  predictor.py  →  evaluator.py  →  validate_races.py
                   (grades predictions against actual finish — the accuracy loop)
```

**The hard rule:** ingestion never touches the UI, the calculator never touches the UI, and the UI never touches a websocket. Everything passes through SQLite. (The one sanctioned write from the display side: `Poller` persists the calculator's *outputs* to `net_analysis` so non-Python readers can display them — it never writes feed data.)

---

## Key modules

### Ingestion

**`alkameldp.py`** — IMSA live feed client. Polls the Al Kamel DDP (Data Distribution Protocol) websocket, normalises the payload, and writes rows into `race_positions`, `pit_events`, `race_control_messages`, and `flag_state`. Runs as a managed `QProcess` from the dashboard's session picker (or standalone with `./venv/bin/python src/alkameldp.py`).

**`wec_live.py`** — WEC live feed client (Epic 8, in progress as of 2026-07-05). Connects to Griiip SignalR Core + MessagePack v2, hub at `insights.griiip.com/live-session-stream`, `seriesId=10`. Supports `--record` (gzip-compressed JSONL capture), `--replay` (offline replay of a capture for parser iteration), and `--discover` (list available sessions). The `--record` path flushes after every frame so a hard kill only loses the frame in flight, not a whole compressed buffer.

**`replay.py`** — offline replay from a Timing71 `.zip` archive. Streams rows into the DB at configurable speed (`--speed N`, default 1×; dashboard uses 60×). The same path used for the 6-race regression suite.

**`timing71.py`** — parses the Timing71 ZIP format (manifest + per-car CSV rows) into the normalised schema the DB and calculator expect.

### Computation

**`calculator.py`** — pure, read-only analysis. Takes the DB state and produces a `CarAnalysis` dataclass per car per refresh cycle. No side effects. Designed to be called repeatedly (every 2–5 seconds from the dashboard's `QTimer`) with low overhead. All tuning knobs are read from `config.CONFIG`, which hot-reloads from `config.json` every cycle.

**`config.py`** — hot-reloadable config. Reads `config.json` from the project root. Watches the file's mtime; if it changed, re-reads and re-applies. Knob changes take effect within one refresh cycle (~2 seconds), mid-race, no restart required.

**`penalties.py`** — penalty parsing and carry. Extracts time penalties from race-control message text (regex + heuristics) and maintains a per-car running total. Feeds into the NET calculation and the CALL column on the board.

**`race_control.py`** — live race-control feed parser. Classifies messages (safety car, penalty, incident, caution) and pushes them to the right-rail RACE CONTROL list.

**`series_profiles.py`** — per-series constants: class names, class-spine colours, stint-length priors, identity mode ("team" vs "driver"). `IMSA` and `WEC` are the two active profiles.

**`catchup.py`** — the "while you were away" diff engine. Pure module (no PyQt). Takes two `Snapshot` objects (before/after), diffs them, and returns a ranked list of `Event` objects with semantic tones (`lead`, `gain`, `loss`, `penalty`, `pit`, `retired`, etc.). Rank priority: class-lead change > DQ > penalty > caution > retired > position move > pit.

### Prediction and evaluation

**`predictor.py`** — logs per-car predictions (stop time, catch time, net position) to the DB at each refresh so the evaluator has a historical record to score.

**`evaluator.py`** — scores logged predictions against what actually happened. Three metrics: stop-duration accuracy, net-position predictiveness (does net at T predict actual position at T+30min better than track position does?), and catch-call accuracy (±3 laps). Prints a report with tuning suggestions. Runs on demand or from the optional AUTO-TUNE toggle in the dashboard (bounded, audited knob nudges only). Never runs in the first hour of a race.

**`validate_races.py`** — 6-race regression suite. Replays each archived race, runs the full prediction pipeline, and computes aggregate MAE for track order, pure net, and the shipped blend. Any change to `calculator.py` or `replay.py` must pass this before committing.

**`check.sh`** — the test gate. Runs the unit tests + a quick smoke-replay. Run before every commit; `--full` adds the full regression suite.

### Data-layer bridge

**`poller.py`** — the Qt-free polling loop (extracted from `dashboard.py`, Epic 9). Owns
the DB connection, calls `calculator.analyse()` every 2 s, tracks live events (box
timers, just-pitted flashes, net trends), buffers snapshots for broadcast-delay mode —
and writes the computed net math (`net_position`, `net_gap_ms` ±band, `class_gap_ms`,
`laps_down`, stops left, penalty carry) back to the DB's `net_analysis` table so
non-Python readers can display it. Also home of `PIT_LANE_STATES`/`BOX_STATES`
(timing_table re-imports them). Importable without PyQt6 — that boundary is load-bearing.

**`poller_daemon.py`** — headless runner for the loop above (same pattern as
`headless_predictor.py`). Run it when using the web UI without a PyQt6 dashboard open;
either one keeps `net_analysis` fresh.

### UI — PyQt6 (primary, race-day proven)

**`dashboard_calm.py`** — the main dashboard ("calm board"). Custom-painted `RowWidget` (single `paintEvent` pass — no child labels — for pixel-accurate breath + hairline). The right rail (RACE AT A GLANCE, RACE CONTROL, DUE TO PIT, BATTLES) is a fixed-width panel beside the scrollable board. The catch-up card (`CatchupCard`) and legend card (`LegendCard`) are floating children of the central widget, positioned and sized at runtime.

**`dashboard.py`** — the original dense pit-wall table. Left intact as a fallback (`./venv/bin/python src/dashboard.py`). Shares `Row`, `_build_rows`, and `FLAG_STYLE` with the calm board; re-exports `Poller` from `poller.py` for backward compatibility.

**`session_picker.py`** — dialog launched from the header button. Manages a `QProcess` for the selected adapter (live or replay) and hands the series identity back to the dashboard so `Poller` can scope to the right session.

### UI — web (Epic 9, shipped 2026-07-15, not yet race-proven)

**`ui/`** — Electron + React + Vite + Tailwind. The Electron main process
(`ui/electron/main.cjs`) opens `race.db` readonly via `better-sqlite3`, joins
`standings_current` + `session_entry` + `session_status` + `net_analysis` +
`pit_events` every 2 s, and ships a JSON payload to the renderer over IPC
(`contextBridge` → `window.racenet.onRows`). The React renderer falls back to mock
data in a plain browser (`npm run browser`) for UI development with no race data.
Features: class-grouped board with NET projection column (▲/▼ by direction),
tap-to-explain panel (net-math breakdown + pit history per car), session clock,
race-control ticker. Setup/run: `ui/README.md`. Design language borrowed from
F1OpenViewer (MIT): Rajdhani/Space Grotesk, HSL variable palette, 36 px rows with
class-color spines.

---

## Core math

### NET position

For each car in a class, NET answers: "if every car pits once more from here, what order do they finish in?"

The algorithm:
1. Determine how many stops each car still owes (from its class's mandatory-stop count minus stops completed, capped at the fuel window).
2. Project each car forward by `stops_remaining × pit_cost` seconds (pit-cost model below).
3. Add any outstanding time penalties.
4. Re-rank by projected time to the virtual finish line.

NET is only shown when the class is out of sequence on stops (someone has pitted and others haven't). When all cars are on the same stop count, NET collapses to track order and the overlay is suppressed (`net_settled` flag).

NET is further suppressed when the gap reading is unreliable (feed sentinel values, lapped cars, cars mid-stop). Conservative silence over misleading arrows.

### Projected finish blend

`projected = w · net_position + (1 − w) · track_position`

`w` scales with stops remaining: when many stops are left, net dominates; as the race nears its end and everyone has cycled, w→0 and the blend converges to track order. The weight schedule is linear over the expected number of remaining stops.

### Pit-cost model (learned in-race)

The app starts each session with class-specific priors from `config.json` (`DEFAULT_GREEN_PIT_MS`). As actual pit stops are observed (`pit_events`), it fits a running median and replaces the prior. Outliers (drive-throughs, mechanical holds) are excluded via a MAD filter (`STOP_OUTLIER_MAD`). The cost is flag-aware: a stop under yellow costs less (you lose less track position), scaled by `CAUTION_PENALTY_FACTOR`.

### Catch & pass gating

A catch call requires:
1. The chaser's gap to the car ahead is decreasing consistently over `CATCH_TREND_LAPS` green-flag laps (not just one data point).
2. The current gap is within `CATCH_GAP_S` seconds (2.0 s default).
3. The closing rate (extrapolated at `CATCH_CLOSING_EFFICIENCY` to account for traffic/variability) puts the catch within `CATCH_MAX_LAPS` laps.

All three gates must pass. One fast lap, a sensor glitch, or a traffic anomaly won't trigger a call. The expected result is that catch calls are rare and trustworthy.

### Undercut / overcut detection

The app flags an undercut opportunity when a car's projected position after pitting NOW is better than its current effective position — i.e., pitting before the rival ahead locks in a gap advantage. Overcut is the mirror: staying out while a rival pits and using fresh-tyre pace to pull a gap.

---

## Key design decisions

**All config in `config.json`, hot-reloaded.** Lets the operator tweak stint lengths, catch gates, and pace windows mid-race without restarting. A bad edit reverts automatically on the next read cycle (the module catches parse errors and keeps the previous config).

**Conservative gating throughout.** The app is designed for "catch up at a glance." A false catch call or wrong NET arrow is worse than silence because it trains the viewer to distrust the board. Every computed field has an explicit "not enough signal" path that shows "—" rather than a number.

**Track-anchored blend for projected finish.** Pure NET diverges from the actual finish because it doesn't account for pace differences — a fast car with one stop remaining is better off than a slow car with zero. The blended projection anchors on track order (which already reflects pace) and uses NET as a modifier.

**SQLite as the shared bus.** The adapter, calculator, and dashboard are decoupled by design — you can kill and restart the adapter mid-race without losing the dashboard. The DB also makes it trivial to replay any session by just substituting a replay adapter.

**Tests are standalone scripts, not a pytest suite.** Keeps the dependency footprint minimal and makes `check.sh` fast to iterate. The regression suite (`validate_races.py`) is the accuracy contract.

---

## WEC-specific notes (Epic 8 — race-proven in the São Paulo 6h, 2026-07-12)

The WEC live path (`wec_live.py`) survived a full 6-hour race live; net-ordering bugs
found in that race were fixed 2026-07-13 (see BACKLOG decisions log). Key differences
from IMSA:

- **Protocol:** Griiip SignalR Core + MessagePack v2. Bootstrap hydration delivers full field state on connect; incremental `ReceiveBatch` pushes thereafter.
- **`--record` is mandatory.** Raw-capture every session, always. The capture is crash-safe (frame-level flush). Replay a capture offline with `--replay` to iterate parser corrections without waiting for the next live session.
- **Field names are provisional.** The 2026 WEC field shapes (energy, override state) are inferred from the 07-03 Griiip rehearsal. Still open: regenerate `tests/fixtures/wec_capture_sample.jsonl.gz` from a real São Paulo capture (FP1 or race, both on disk) and tighten the test assertions.
- **Timing71 fallback is data-only, post-session.** Unlike IMSA where Timing71 has a live feed, the WEC archive only becomes available after the session ends. If `wec_live.py` fails mid-race, the raw capture is the only real-time record.
