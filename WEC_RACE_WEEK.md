# WEC Race Week Runbook — Rolex 6 Hours of São Paulo (2026-07-12)

Reconstructs the 4-phase Epic 8 plan (original plan doc lost; this file is now the
durable copy — see `BACKLOG.md` Epic 8 for the decisions log and `git log` for the
commit-by-commit history).

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Protocol discovery + `wec_live.py` client build | ✅ done |
| 2 | Raw capture (`--record`), tests, picker wiring | ✅ done |
| 3 | Pre-event validation (rehearsal, archive fallback, discovery check) | ⬜ open |
| 4 | Race-week execution (FP1 capture → field corrections → race) | ⬜ calendar-blocked |

**Protocol (confirmed 2026-07-03):** Griiip SignalR Core + msgpack v2, hub at
`insights.griiip.com/live-session-stream`, WEC seriesId=10. Full field/channel map in
`src/wec_live.py` docstring and memory `project_wec_pipeline`.

## Phase 3 — pre-event validation checklist

- [x] **Rehearsal (2026-07-03, done early — see Finding below).** Ran
  `venv/bin/python src/wec_live.py --sid <live sid> --no-db --record F.jsonl.gz`
  against live Griiip traffic (F1 British GP FP1, then ELMS Imola test session — no WEC
  session exists yet, same infra). Confirmed: discovery, bootstrap hydration, SignalR
  connect, and steady ReceiveBatch heartbeat all work end-to-end.
- [ ] **Reconnect test.** A hard kill (`SIGTERM`) stopped the process but did **not**
  exercise the app's own auto-reconnect loop (that fires on connection-level errors, not
  process death). A true "kill wifi mid-session" test needs a live network interruption
  on the dev machine — do this manually once real WEC traffic is up (~07-07+), not via a
  sandboxed process kill.
- [ ] **Timing71 archive fallback.** *(Corrected 07-04, per owner: the Timing71 app
  does NOT record live — it provides an archive file after the session ends.)* So
  fallback #2 rescues the DATA (post-session archive → `timing71.py`/`replay.py`,
  IMSA-proven) but NOT the live board mid-race — if `wec_live` fails live, watch
  Timing71 itself and parse the archive afterward. Verify at FP1: confirm the WEC
  session appears in Timing71 and an archive is downloadable after the session.
- [ ] **WEC discovery check (~07-07).** Griiip session pools typically open 3–5 days
  pre-event. Run `--discover` and confirm a `seriesId=10` entry appears:
  ```
  venv/bin/python src/wec_live.py --discover
  ```
  - *07-03 result:* no `seriesId=10` yet — only NASCAR (id=355), already chequered.
    Transport healthy end-to-end: SignalR msgpack connect + `SID-*` group join worked;
    0 batches only because the session had finished. Re-check daily from 07-06.

### ✅ Fixed: raw-capture archive crash-safety (found + fixed 07-03)

`--record` writes via `gzip.open(path, "ab")`, which buffers compressed data
internally. A `SIGTERM` (simulating a crash, not a clean `Ctrl-C`) left the archive
**truncated** — `gzip -t` failed "unexpected end of file"; only the frames written
before the last internal flush were recoverable.

This mattered because BACKLOG.md calls `--record` "mandatory insurance" for race day —
a laptop sleep, crash, or ungraceful kill during the real 6-hour race could have cost
the tail of the archive right when it matters most.

**Fix applied:** `self._recorder.flush()` after every frame write in `_record_frame()`
(`src/wec_live.py:307`). `gzip.GzipFile.flush()` defaults to `zlib.Z_SYNC_FLUSH`, making
the stream decodable up to that point immediately.

**Verified by a second live rehearsal + kill test:** after the fix, a `SIGTERM` mid-capture
left 49/50 recovered frames as clean, valid JSON — only the exact frame being written at
the instant of the kill was torn (an unavoidable race with any hard-kill signal; a
downstream JSONL reader just skips one bad trailing line). Before the fix, an entire
buffered chunk could be lost instead of just the one in-flight frame. Regression test:
`tests/test_wec_live.py::TestRecordFrameFlush`.

## Phase 4 — race-week execution checklist

**Expectation-setting (07-06):** live accuracy will read WORSE than the replay-suite
baselines — replay archives have cleaner pit detection (message-log-driven) than live
feed diffing. Judge the live board against the broadcast, not against the suite MAEs.

- [ ] **07-10/11 FP1-FP2/quali:** `--record` running through every session
  (non-negotiable). **Data-validation only** (BACKLOG 07-04 decision) — confirm
  team/class names resolve, RC messages flow, and telemetry/VET populates.
  Practice pit stops are setup/tire/fuel-load experiments with arbitrary
  durations and non-strategy driver swaps — comparing predicted vs actual stop
  time against them is meaningless noise, not a tuning signal. Do NOT touch
  `SERIES_OVERRIDES`/`DRIVER_CHANGE_DELTA_MS` from anything seen in practice.
- [ ] **07-11:** Commit 5 — field corrections from the real capture (class names, VET
  `cars-energy-tanks` shape, pit timing); iterate parser offline against Friday's file.
- [ ] **07-12 race:** `--record` = must-have; live board = best-effort. This is the
  first legitimate stop-cost signal — if early green-flag stops show the suite's
  −8…−24s WEC stop-time bias holding, tune live via `SERIES_OVERRIDES` (prime
  suspect: the 12s `DRIVER_CHANGE_DELTA_MS` prior):
  ```json
  "SERIES_OVERRIDES": {"wec": {"DRIVER_CHANGE_DELTA_MS": 45000}}
  ```
  IMSA calibration is untouched by anything inside the "wec" block.
- [ ] **Post-race:** add the São Paulo archive to `validate_races.py`'s held-out set once
  the parser is proven against it; run the standard post-event memory-QA pass.

## Fallback ladder

**Posture (07-04): if live WEC timing is broken during practice, the plan is
to debug and FIX it together during FP sessions — not to lean on the Timing71
fallback. The ladder below is robustness insurance, not the plan.**

1. Raw frame capture (`--record`) → post-race parsing.
2. Timing71 post-session archive → existing `timing71.py` / `replay.py` path
   (IMSA-proven; data-only — no live board, archive available only after the session).
3. Browser HAR/WS capture (manual last resort).

## Launch commands

```bash
# run from the project root
venv/bin/python src/wec_live.py --discover                 # list live sessions
venv/bin/python src/wec_live.py --record data/wec_raw.jsonl.gz   # auto-discover WEC, record
venv/bin/python src/wec_live.py --sid <sid> --no-db --record F.jsonl.gz  # rehearsal/dry run
venv/bin/python src/wec_live.py --replay F.jsonl.gz --db data/scratch.db # offline replay
```

## Offline capture replay (Commit-5 workflow, added 07-04)

`--replay` feeds a `--record` capture through the full parse/dispatch path
offline — record FP1, replay against a scratch DB, fix field mappings, repeat.
Torn trailing lines (hard-kill artifact) are skipped automatically.
`tests/test_wec_capture_replay.py` runs the same path in `check.sh` against
`tests/fixtures/wec_capture_sample.jsonl.gz` (real 07-03 Griiip bootstrap +
live-shape frames). **After FP1: regenerate the fixture from the real WEC
capture** — the test assertions are the contract.
