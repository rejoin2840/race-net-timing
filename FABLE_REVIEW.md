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

## 4. Tier 2 — whole-repo architecture pass (PENDING, fill work 07-05→07-09)

First thing cut under time pressure (feeds Epic 9, which runs post-race-week).
Planned scope: engine/display boundary (how cleanly could the PyQt6 layer be
swapped for web tech — the Epic 9 phase-2 question), the two-process
WAL/sqlite interface design, `dashboard_calm.py` (1571 lines) structure,
config hot-reload pattern, dead-code sweep after the F1/IndyCar deletion
(`inspect_page.py`, `intercept_ws.py`, `weekend_conductor.py`,
`session_healthcheck.py` — confirm still used), `alkameldp.py` review
(IMSA-proven live, lowest priority). Findings land here when done.
