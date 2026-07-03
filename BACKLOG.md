# Backlog

Epic-structured roadmap, reconciled 2026-07-03 from the original phased plan + an
external (Cursor) audit + Paul's feedback. This file is the single source of truth for
"what's next and why" — code comments should point here, not at phase numbers.

**Product identity:** primary = *endurance race strategy companion* (IMSA → WEC) with
honest confidence signaling. Secondary = *strategy learning lab* (replay → evaluate →
tune). Paul's top pain points, which shape acceptance criteria everywhere:
**#1 wrong class-leader gap · #2 DUE TO PIT called too early.**

## Decisions log (do not relitigate without new information)

- **2026-07-03 — F1 and IndyCar scrapped entirely (supersedes 07-02 freeze).** Paul's
  call. Full delete of source, tests, data, docs, and archive folders. Recovery path:
  `git checkout pre-endurance-refocus -- <file>` or `git show pre-endurance-refocus:src/f1_live.py`.
- **2026-07-03 — Epic W cancelled.** F1 British GP / IndyCar Mid-Ohio live-validation
  weekend (07-03..05) called off. WEC pivot starts immediately.
- **2026-07-03 — WEC live client jumps the queue (Epic 8, top priority).** Supersedes the
  07-02 timing-tab-first note under its own "new information" clause: that note's rationale
  was "long calendar gap before the next race," but the WEC spike surfaced a race in 9 days
  (Rolex 6H São Paulo, 07-12). Missing the São Paulo capture window costs ~8 weeks to the
  next WEC event. Epic 6 (timing tab) becomes fill work when Epic 8 is blocked on live
  traffic.
- **2026-07-02 — Timing tab stays first in line. DO NOT FLIP-FLOP.** *Superseded
  2026-07-03 by WEC São Paulo discovery — see entry above.*
- **2026-07-02 — CI = local `check.sh`** (repo has no remote). Revisit only if
  regressions start slipping through.
- **2026-07-02 — Timing71 integration rejected** (private connector package; T71 consumes
  the same Al Kamel feed we already ingest). Dense timing tab is built from our own DB.
- **2026-06-28 — Net position is a situational gauge, not a finish predictor.**
  `projected_finish` is the track-anchored blend; any change to it must pass
  `validate_races.py` across ALL races, never one.

## Epics

### Epic 0 — Docs & hygiene ✅ 2026-07-02
README, this file, stale docstrings fixed, `requirements.txt` pinned (signalrcore),
`ARCHIVE_DIR` config key replacing the hardcoded `~/Downloads`, `check.sh` gate,
`test_catchup.py` main runner added.

### Epic 4 — WEC research spike ✅ 2026-07-03
Findings committed in `wec_spike_findings.md`. Key result: WEC timing is Al Kamel data,
but the public frontend at `livetiming.fiawec.com` is **SignalR + MessagePack** — NOT
the Meteor/DDP transport `alkameldp.py` speaks. `livetimingFeed("wec")` against the
existing DDP client will not work. Fields visible from the spike: class, driver, gap,
interval, pit count, laps, sector times + unknown `VET` column (hypothesis: Virtual
Energy Tank % — the WEC analog of IMSA fuel telemetry; to confirm from a live capture).
Next WEC race: Rolex 6H São Paulo 2026-07-12.

### Epic 8 — WEC live pipeline *(top priority, deadline-driven: FP1 ≈ 07-10)*

**Goal:** raw-capture-first WEC client ready before São Paulo FP1.
Raw capture = must-have; live board = nice-to-have for race day.

**Status (2026-07-03) — commits 1–4 done, full runbook in `WEC_RACE_WEEK.md`:**
1. ✅ WEC `SeriesProfile` (HYPERCAR/LMGT3).
2. ✅ `src/wec_live.py` — full Griiip SignalR+msgpack client, DB persistence.
3. ✅ `--record` raw-capture mode.
4. ✅ `session_picker.py` WEC Live page wiring (enabled, not a stub).
- ✅ Fix: `ranks`/`gaps` nested-`items` unwrapping (found from live F1 traffic).
- ✅ `tests/test_wec_live.py`: 70 tests, all green (`./check.sh`).
- ⬜ **Open (Phase 3, see `WEC_RACE_WEEK.md`):** kill-network reconnect test, Timing71
  DVR fallback verification, `--discover` check for WEC seriesId=10 (~07-07).
- ✅ **Fixed (07-03):** `--record`'s gzip archive wasn't crash-safe (a hard process kill
  truncated the tail). Now flushes after every frame write; verified with a second
  kill test. See `WEC_RACE_WEEK.md` for detail.
- ⬜ **Blocked on FP1 (07-10):** Commit 5, field corrections from a real WEC capture.

**Feasibility:** `signalrcore 1.0.2` already in venv with full `MessagePackHubProtocol`;
`msgpack 1.1.2` importable. If WEC is SignalR Core (vs classic ASP.NET SignalR — fails
fast at negotiate, easy to distinguish), the `HubConnectionBuilder` pattern from the
deleted `f1_live.py` works unchanged. Reference patterns recoverable via
`git show pre-endurance-refocus:src/f1_live.py` and `:src/indycar_live.py`.

**Raw-capture-first (mandatory insurance):** `--record` writes every decoded frame
BEFORE dispatch/parsing; dispatch wrapped in try/except, capture-write never is. Even a
zero-parse race day still yields a complete archive to finish the parser replay-style.

**Commit order (each gated by `./check.sh`):**
1. WEC `SeriesProfile` in `series_profiles.py` (pure data, no risk).
2. `src/wec_live.py` skeleton + pure parser fns + `tests/test_wec_live.py`; pin `msgpack`.
3. `--record` raw-capture mode (needs only transport, not correct parsing).
4. `session_picker.py` WEC Live page wiring (~10 lines, enables the disabled stub).
5. Post-discovery: correct field mappings/topics from real `--discover` dumps.

**Protocol discovery:** attempt `livetiming.fiawec.com` NOW — the feed may serve a frozen
session between events (IndyCar's did). Inspect `/negotiate` response first (settles
Core-vs-classic on day 1). Retry 07-06 (session pools often open 3–5 days pre-event).

**Runway 07-03 → 07-12:**
- Now–07-05: skeleton, msgpack decode harness, guessed mappings, `--record` mode.
- 07-06: retry discovery; verify Timing71 Desktop DVR fallback works NOW, not race day.
- 07-07/08: picker wiring, tests, hardening.
- 07-09: full rehearsal — `--record` under `--no-db`, kill-wifi reconnect test.
- 07-10 FP1: first guaranteed live traffic; `--record` runs through every session.
- 07-11: iterate parser offline against Friday's capture.
- 07-12 race: `--record` = non-negotiable; live board = best-effort.

**Fallbacks (ranked):**
1. Raw frame capture → post-race parsing.
2. Timing71 Desktop DVR → existing `timing71.py`/`replay.py` path (IMSA-proven).
3. Browser HAR/WS capture (manual last resort).

**Top risks:** SignalR classic-vs-Core (day-1 check) · auth/cookie gate on negotiate ·
msgpack schema opacity (capped by raw capture) · hub-target/session-ID discovery ·
rate limiting on pre-race-week negotiate attempts.

**Research note (no v1 work):** `VET` column hypothesis = Virtual Energy Tank % for
Hypercar. If confirmed in captures, it's WEC's fuel-telemetry analog — a natural Epic 2
sibling for WEC.

### Epic 6 — Timing tab *(fill work when Epic 8 is blocked on live traffic)*
Pure-move `dashboard.py` table pieces → `src/timing_table.py` (byte-identical render
verified), then a separate wiring commit into the calm board's `Timing ↗` stub. Replace
hard-coded IMSA class colors with `ctx.profile` during the move. Two independently
revertible commits.

### Epic 1 — Confidence UX + IMSA live validation
- **1a (replay-driven, after the timing tab):** resurface `net_gap_band_ms` on the calm
  board (reuse dashboard.py's band formatting + hide-when-≥20s rule); "early race / low
  pit data" badge; pit-model scope indicator (car/class/field fallback level). Feel-test
  via `replay.py --stream`.
- **1b (calendar-blocked, next IMSA event):** 2–3 live sessions; watch **class-leader gap
  correctness** (pain point #1) through a full pit cycle; compare live vs replay eval metrics.
- **Acceptance:** NET order trustworthy through one full live pit cycle; class-leader gaps
  match broadcast.

### Epic 2 — IMSA fuel telemetry (biggest accuracy lever; capture blocked on next IMSA session)
- Capture with `src/telemetry_capture.py` at next IMSA practice. **First verify Paul's
  report that GTD + GTD Pro now have live telemetry** (feed was GTP-only when captured;
  only LMP2 confirmed without).
- Then `src/telemetry_adapter.py` → `standings_current.fuel_pct/fuel_flag` → `fuel_due`
  → DUE TO PIT rail. Debounce/cross-check mandatory — raw VFT is documented-unreliable
  (`calculator.py` ~L647: reads near-empty for laps after refuelling). Telemetry
  overrides the stint estimate only when sane; stint estimate remains the LMP2/fallback
  path.
- **Acceptance (targets pain point #2):** DUE calls within ~2 laps of the actual pit
  window on replay + one live session, per telemetry-covered class.

### Epic 3 — Penalty parsing hardening (no calendar block)
- Fixture library from real race-control text: the 66 captured Sahlen's 6H rows + message
  logs inside the 6 IMSA Timing71 archives.
- Expand `tests/test_penalties.py` with race-sourced cases; UI shows parsed penalties
  plus an "unparsed RC message" fallback alert so penalties never *silently* break net.
- Re-run `validate_races.py`; record the net MAE delta here.

**Progress as of 2026-07-02 (commit 15bb728) — Steps 1–3 done:**
- `tests/fixtures/rc_messages_imsa.txt`: 716 unique RC messages extracted from all 6 IMSA
  archives. `test_known_unparsed_invariant` enforces no silent drops.
- Parser fixes: STOP PLUS N, STOP + MM:SS, post-race STOP+N (all backed by corpus tests).
- `replay.py` now persists RC rows via `db.record_race_control()`.
- **Step 3b (option 1, approved + done):** RC rows are now persisted *incrementally* as
  replay time advances (`_rc_feed` cursor in replay.py), so every analyse() cycle —
  batch AND stream — sees only penalties issued up to that moment, matching live.
  (Upfront loading had leaked future penalties into early predictions.)
- **Honest MAE result with time-consistent penalty carry (6-race mean):**
  net **3.00** / trk **2.44**. Current state: **accept as-is.**
- **Step 4 (fill work):** `race_control.classify()` new `"unparsed_penalty"` kind +
  calm-board dim-amber rail alert.

### Epic 7 — WYWA & calm-board polish (Paul wants to lean in)
Catch-up card refinements, breath-noise evaluation, a visible honest home for
`projected_finish`, WATCH/BATTLES knob tuning (`BUDGET_PER_CLASS`, `CATCH_GAP_S`,
`CATCH_TREND_LAPS`, `BATTLE_GAP_S`) via hot-reload against streaming replays.

## Research items (unscheduled)

- **Driver-change / min-drive-time rules:** vary per race and per class; need a reliable
  per-event source (IMSA supplementary regulations, WEC sporting regs). Until found, the
  `(lineup_size − 1)` heuristic stays.
- **WEC VET column:** hypothesis = Virtual Energy Tank % for Hypercar. Confirm from first
  São Paulo capture; if correct, scope as Epic 2 sibling for WEC.
- **"+1 lap" flicker** — cosmetic; hysteresis fix idea documented in session notes.

## Parked north star (no work — direction only)

- **Broadcast video linkage:** connect the app to the livestream and pull video clips of
  notable overtakes/incidents, feeding the "while you were away" card. Paul's "final
  boss" idea — everything WYWA-related should avoid foreclosing it.

## Held-out / regression sets

- **IMSA (6 complete archives, the default gate):** Daytona 24h, Petit Le Mans 10h,
  Indy 6h, Monterey, Long Beach, Detroit — paths in `validate_races.py` under
  `ARCHIVE_DIR`.
- **WEC:** São Paulo 2026-07-12 raw capture will be the first WEC archive. Add to
  `validate_races.py` once the parser is proven against it.
- Only complete run-to-chequered archives count; verify span + final flag before adding.

## Standing gates

- Any calculator/replay/evaluator change → `./check.sh --full` (tests + IMSA suite).
- UI changes → offscreen render + `replay.py --stream` feel-test.
- Finish-predictor changes → validated across ALL races, never a single archive.
