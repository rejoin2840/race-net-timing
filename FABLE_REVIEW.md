# Fable code & architecture review — 2026-07-04

Two-tier review run by Claude Fable 5 ahead of São Paulo race week (07-12),
per Paul's call: deep race-day-path review first, whole-repo architecture pass
second. Fix policy: race-day bugs fixed immediately (check.sh-gated), all other
findings logged here. Recovery tag: `pre-fable-review`.

Tier 1 scope actually reviewed: `wec_live.py`, `db.py`, `calculator.py`
(WEC data-flow paths), `replay.py`/`timing71.py` (fallback ladder),
`series_profiles.py`, `session_picker.py` wiring. The IMSA-side math in
`calculator.py` was reviewed only where WEC rows flow through it — it is
already regression-gated by the 6-race IMSA suite.

## 1. Fixed (race-day-critical) — commits 65a26c0, d2360e3

1. **First WEC pit stop per car silently dropped.** `db.update_pit_info`'s
   baseline rule (first `lastPitHour` = possible pre-connect stop, don't count)
   is correct for IMSA feed values but wrong for wec_live, which generates the
   timestamp from pit-in/pit-out events it watched happen. Stop counts ran one
   behind all race; `predict_stop` was underfed. Fixed with a `live_observed`
   flag; IMSA callers unchanged.
2. **Partial status updates nulled each other's `session_status` columns.**
   `db.update_status` overwrites every column from the dict it gets; wec_live
   sent partial dicts (flag-only, clock-only, length-only), so each flag change
   wiped the session length → time-remaining math broken. Fixed with a merged
   status accumulator in wec_live (`_push_status`). The db semantics themselves
   are a foot-gun — see §3.
3. **Session start time never persisted on Python ≤3.10.** Griiip sends
   JS-style `Z`-suffixed ISO timestamps; `datetime.fromisoformat` can't parse
   `Z` before 3.11 and the venv is 3.9.6. The ValueError was swallowed, so
   `startTime` silently never landed. Fixed by normalizing `Z` → `+00:00`.
4. **Best lap frozen at connect time.** Live `laps` frames never updated
   `best_ms` (bootstrap-only seed). Fixed in `_handle_laps`, with
   `isValid: false` laps excluded.
5. **`--discover` crashed with NameError when the schedule fetch failed**
   (`sessions` referenced before assignment) — the exact command in the daily
   07-06+ checklist. Fixed.
6. **Timing71 DVR fallback loaded WEC archives as IMSA.** `_detect_series`
   hardcoded `"imsa"`; a WEC DVR capture (fallback #2) would have gotten the
   wrong profile/classes. Now detects WEC manifests.

Plus: capture-replay harness (`tests/test_wec_capture_replay.py`, real 07-03
bootstrap fixture) and `wec_live.py --replay` offline mode — the Commit-5
field-mapping loop, ready before FP1.

## 2. Verify at FP1 (~07-10) — data-contract unknowns, now checkable via --replay

- **`gapToFirstMillis` semantics.** Real bootstrap confirms lapped cars get
  `-1` (laps in `gapToFirstLaps`) — handled. Verify the leader's value and
  whether a same-lap car can ever *lack* the field: wec_live coerces
  missing/negative → 0, which would fake a tie with the class leader
  (pain point #1) if that case exists.
- **Real `classId` strings for HYPERCAR/LMGT3.** `normalize_class` defaults
  unknown/empty → HYPERCAR; wrong strings would silently misclass cars.
- **`cars-energy-tanks` (VET) shape** — Hypercar virtual-energy hypothesis;
  Epic 2 sibling. Handler is currently a no-op by design.
- **`participants-running-status` strings** vs the stopped set
  (`retired/dnf/withdrawn/disqualified`) — unverified guesses.
- **`sessionType` strings** (`Free Practice`? `FP1`?) for the type map.
- **`RaceLog` message format** vs the IMSA-corpus penalty parser — unparsed
  messages alert dim-amber rather than fail silently, so this degrades safely.

## 3. Logged — post-race-week hardening (do NOT churn before 07-12)

- **`pit_in_times` staleness.** A missed pit-out (reconnect during a stop)
  leaves the car flagged PIT forever *and* loses that stop. Consider a
  timeout, or reconciling from the `pit-standing-finish` channel.
- **Write amplification in `_flush_car`.** One sqlite commit per car per
  frame; a full-field ranks batch = ~23 commits. Batch per ReceiveBatch
  instead. Not observed to be a problem in rehearsals — measure at FP1 first.
- **Recorder close race.** `_record_frame` (SignalR callback thread) vs
  `_cleanup` closing the gzip handle during a stale-timeout restart — small
  write-after-closed window. Guard with the connection stopped first or a lock.
- **`main()` restart loop pins the original sid.** Session rollover
  (FP1→FP2) needs a process restart. Acceptable for race day (one session);
  runbook already runs `--record` per session. Make discovery re-run on
  restart post-race-week.
- **`db.update_status` full-overwrite semantics** is the foot-gun behind fix
  #2. Post-race-week: switch to key-presence updates (only SET provided
  fields); IMSA callers pass full dicts today so behavior is preserved.
- **`laptime_ms` float heuristic**: a float `< 1000` is treated as seconds, so
  a 999.0 ms value would misread as 999 s. Only matters if Griiip ever sends
  float fields — check against the FP1 capture before using it for sectors.

## 4. Tier 2 — whole-repo architecture pass (done 2026-07-04)

Log-only. Primary customer: Epic 9 (product definition + front-end direction).

### 4.1 Engine/display boundary — the Epic 9 phase-2 answer

**The boundary is already good.** PyQt6 imports are confined to exactly four
modules: `dashboard.py`, `dashboard_calm.py`, `timing_table.py`,
`session_picker.py`. Everything else — `calculator`, `db`, `catchup`,
`penalties`, `race_control`, `series_profiles`, `config`, `predictor`,
`timing71`, `replay`, `evaluator`, `wec_live`, `alkameldp` — is Qt-free and
would survive a front-end swap untouched. The valuable IP (the calculator) is
fully display-agnostic.

**One leak, and it's the whole pre-work for a web front end:** `Poller`,
`Row`, `_build_rows`, and the class/flag constants live in `dashboard.py` (a
UI module), and `dashboard_calm` imports them from there. `Poller` itself is
pure data logic (sqlite + deques, zero Qt) — extracting Poller + row-building
(~350 lines) into an engine-side `poller.py` is mechanical and would make
`dashboard.py` deletable-in-principle. Recommend doing that extraction as the
FIRST commit of whatever Epic 9 decides — it pays off in both futures
(PyQt6-kept or web).

**Web-swap cost estimate:** moderate and well-contained. `_row_vm()` in
`dashboard_calm.py` already builds a plain-dict view-model per row — that is
90% of a JSON contract for a web UI. The sqlite-WAL two-process pattern means
a display layer could be fed by a thin local HTTP/WS bridge reading the same
DB the Qt board reads today; engine untouched. The real Epic 9 cost is
re-implementing the *widgets* (cards, rails, animations), not the data path.

### 4.2 Two-process sqlite design — sound

Scraper writes / dashboard reads+predicts, WAL + `busy_timeout=5000` on both
sides (`db.py:243`, `dashboard.py:109`), history tables idempotent by schema.
This is a solid little architecture and the reason replay/live/evaluator all
compose. Keep it regardless of front-end direction.

### 4.3 Config hot-reload — sound, two content gaps (fixed)

Schema-gated (`config.py` DEFAULTS is the schema), mtime-checked once per
cycle, malformed-JSON-safe. Pattern is right. Content gaps found and fixed
07-04 since they're race-day-relevant, values-only, freeze-compatible:
`DEFAULT_STINT_LAPS` had no WEC classes (HYPERCAR/LMGT3 fell to the generic
30-lap fallback; priors 33/30 added — **Paul: sanity-check against FP1**),
and `TRACK_LAT/LON` still pointed at Watkins Glen (weather card) — now
Interlagos. Post-race-week idea: move per-track values into event/series data
so a stale manual edit can't ship (logged, low priority).

### 4.4 dashboard_calm.py (1571 lines) — big but not tangled

Clear internal strata: pure helpers → view-model builders (`_row_vm`,
`_columns`) → self-contained card/row widgets → `CalmDashboard` orchestration
(`refresh()` is the only place everything meets). If PyQt6 stays, a
3-file split (viewmodel / widgets / window) is low-risk; if web wins, only
the view-model layer ports. No action until Epic 9 decides — the structure
does not block either path.

### 4.5 Dead code (post-F1/IndyCar-deletion sweep)

Import-orphans, all from the pre-DDP browser-scraping era:
- `scraper.py` (334 ln) + `inspect_page.py` (211 ln, only imported by
  scraper) — superseded by `alkameldp.py`. Safe to delete post-race-week
  (recovery: git history).
- `intercept_ws.py` (167 ln) — orphaned, BUT it is generic WS-interception
  tooling and race-week fallback #3 is "browser HAR/WS capture" — **keep at
  least through São Paulo**, then decide.
Alive and correctly wired: `weekend_conductor` → `session_healthcheck` +
`headless_predictor` (via session_picker), `telemetry_capture` (Epic 2),
`predictor` (5 consumers).

### 4.6 Misc (logged)

- Importing `wec_live`/`alkameldp` has side effects: logging file handlers
  created at import time — every test run drops a `weclive_*.log` in `logs/`
  (visible: dozens of near-empty logs from today's runs). Move handler setup
  into `main()`/client start post-race-week.
- `laptime_ms` in wec_live is currently unused by handlers (fields arrive as
  int ms) — it exists for field corrections; fine, but delete after Commit 5
  if still unused.
- `alkameldp.py` structure reviewed at outline level only (IMSA-proven live;
  same _parse/_state/_client strata as wec_live). No concerns worth the read
  before race week.

### 4.7 What Tier 2 did NOT find

No race-day-critical issues beyond the two config content gaps. Nothing in
the architecture blocks Epic 9's web option; nothing demands it either. The
honest summary for the Epic 9 spike: the code is *ready* for either decision,
so the decision can be made purely on product/UX grounds.
