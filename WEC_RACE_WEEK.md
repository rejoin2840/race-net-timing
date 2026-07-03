# WEC Race Week Runbook — Rolex 6 Hours of São Paulo (2026-07-12)

Reconstructs the 4-phase Epic 8 plan (original plan doc lost; this file is now the
durable copy — see `BACKLOG.md` Epic 8 for the decisions log and `git log` for the
commit-by-commit history).

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Protocol discovery + `wec_live.py` client build | ✅ done |
| 2 | Raw capture (`--record`), tests, picker wiring | ✅ done |
| 3 | Pre-event validation (rehearsal, DVR fallback, discovery check) | ⬜ open |
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
  on Paul's machine — do this manually once real WEC traffic is up (~07-07+), not via a
  sandboxed process kill.
- [ ] **Timing71 Desktop DVR fallback.** Never verified. Manual step for Paul: open
  Timing71 Desktop, confirm it can see/record a WEC session before race week. This is
  fallback #2 if the live client fails race day.
- [ ] **WEC discovery check (~07-07).** Griiip session pools typically open 3–5 days
  pre-event. Run `--discover` and confirm a `seriesId=10` entry appears:
  ```
  venv/bin/python src/wec_live.py --discover
  ```

### ⚠️ Finding from the 07-03 rehearsal: raw-capture archive is not crash-safe

`--record` writes via `gzip.open(path, "ab")`, which buffers compressed data
internally. Killing the process with `SIGTERM` (simulating a crash/reconnect-loop
failure, not a clean `Ctrl-C`) left `wec_rehearsal.jsonl.gz` **truncated** —
`gzip -t` reports "unexpected end of file"; `gzip -dc` recovers only the frames
written before the last internal flush (295 of however many were sent), then errors.

This matters because BACKLOG.md calls `--record` "mandatory insurance" / "non-negotiable"
for race day — a laptop sleep, crash, or ungraceful kill during the real 6-hour race
could truncate the tail of the archive right when it matters most.

**Recommended fix (not yet applied — needs Paul's go-ahead, touches `wec_live.py`):**
call `self._recorder.flush()` after every frame write in `_record_frame()`
(`src/wec_live.py:307`). `gzip.GzipFile.flush()` defaults to `zlib.Z_SYNC_FLUSH`, which
makes the stream decodable up to that point even if the process dies immediately after
— turning "lose the whole tail" into "lose at most the frame in flight."

## Phase 4 — race-week execution checklist

- [ ] **07-10 FP1:** `--record` running through every session (non-negotiable).
- [ ] **07-11:** Commit 5 — field corrections from the real capture (class names, VET
  `cars-energy-tanks` shape, pit timing); iterate parser offline against Friday's file.
- [ ] **07-12 race:** `--record` = must-have; live board = best-effort.
- [ ] **Post-race:** add the São Paulo archive to `validate_races.py`'s held-out set once
  the parser is proven against it; run the standard post-event memory-QA pass.

## Fallback ladder

1. Raw frame capture (`--record`) → post-race parsing.
2. Timing71 Desktop DVR → existing `timing71.py` / `replay.py` path (IMSA-proven).
3. Browser HAR/WS capture (manual last resort).

## Launch commands

```bash
cd "/Users/paulkassan/Claude projects/race-net-timing"
venv/bin/python src/wec_live.py --discover                 # list live sessions
venv/bin/python src/wec_live.py --record data/wec_raw.jsonl.gz   # auto-discover WEC, record
venv/bin/python src/wec_live.py --sid <sid> --no-db --record F.jsonl.gz  # rehearsal/dry run
```
