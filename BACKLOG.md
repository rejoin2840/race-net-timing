# Backlog

Epic-structured roadmap, reconciled 2026-07-02 from the original phased plan + an
external (Cursor) audit + Paul's feedback. This file is the single source of truth for
"what's next and why" — code comments should point here, not at phase numbers.

**Product identity:** primary = *endurance race strategy companion* (IMSA → WEC) with
honest confidence signaling. Secondary = *strategy learning lab* (replay → evaluate →
tune). Paul's top pain points, which shape acceptance criteria everywhere:
**#1 wrong class-leader gap · #2 DUE TO PIT called too early.**

## Decisions log (do not relitigate without new information)

- **2026-07-02 — Timing tab stays first in line. DO NOT FLIP-FLOP.** Rationale: there is
  a long calendar gap before the next IMSA/WEC race, so the accuracy epics (live
  validation, telemetry capture) are calendar-blocked regardless — UI work is exactly
  what fits the gap. Paul explicitly asked that this note be kept so he doesn't reverse
  it later.
- **2026-07-02 — F1 and IndyCar are frozen after the 2026-07-03..05 weekend.** They were
  learning vehicles. F1 keeps its live feed + quali panel, nothing more (no tire-stint
  model, no F1 regression list). IndyCar gets exactly one held-out validation run, then
  no further investment. Both adapters stay working for tinkering.
- **2026-07-02 — CI = local `check.sh`** (repo has no remote). Revisit only if
  regressions start slipping through.
- **2026-07-02 — Timing71 integration rejected** (private connector package; T71 consumes
  the same Al Kamel feed we already ingest). Dense timing tab is built from our own DB.
- **2026-06-28 — Net position is a situational gauge, not a finish predictor.**
  `projected_finish` is the track-anchored blend; any change to it must pass
  `validate_races.py` across ALL races, never one.

## Epics

### Epic 0 — Docs & hygiene ✅ 2026-07-02
README, this file, stale docstrings fixed, `requirements.txt` pinned (fastf1,
signalrcore), `ARCHIVE_DIR` config key replacing the hardcoded `~/Downloads`,
`check.sh` gate, `test_catchup.py` main runner added.

### Epic W — Live weekend 2026-07-03..05 (validation only, NO builds)
`weekend_conductor.py` covers F1 (British GP) + IndyCar (Mid-Ohio) unattended; protocol
in `weekend_qa.md`. After Sunday: flip `DEV_SHOW_ALL_SESSIONS` back to `false`, grab the
IndyCar race archive, read `logs/weekend_conductor.log` before anything else.

### Epic 6 — Timing tab (next up, Mon 07-06+)
Pure-move `dashboard.py` table pieces → `src/timing_table.py` (byte-identical render
verified), then a separate wiring commit into the calm board's `Timing ↗` stub. Replace
hard-coded IMSA class colors with `ctx.profile` during the move. Two independently
revertible commits.

### Epic 1 — Confidence UX + IMSA live validation
- **1a (replay-driven, after the timing tab):** resurface `net_gap_band_ms` on the calm
  board (reuse dashboard.py's band formatting + hide-when-≥20s rule); "early race / low
  pit data" badge; pit-model scope indicator (car/class/field fallback level). Feel-test
  via `replay.py --stream`.
- **1b (calendar-blocked, next IMSA event):** 2–3 live sessions; IMSA live protocol added
  to `weekend_qa.md`; watch **class-leader gap correctness** (pain point #1) through a
  full pit cycle; compare live vs replay eval metrics.
- **Acceptance:** NET order trustworthy through one full live pit cycle; class-leader
  gaps match broadcast.

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
  `replay_f1.py:182` still loads RC upfront — F1 frozen, not in the gate, left alone.
- **Honest MAE result (per-race mean across the 6-race suite):**
  pre-RC baseline net 2.69 / trk 2.71 → with time-consistent penalty carry net **3.00**
  / trk 2.44. Penalty carry as modeled HURTS finish prediction. Cause is the carry
  model, not parsing or timing: (1) **pending_s never expires** — after the car serves
  its drive-through, track position already reflects the loss but NET keeps subtracting
  22s forever (double-count); (2) **rescissions don't cancel** — the RESCINDED line
  parses to nothing but the original penalty line stays counted. **Decision for Paul:**
  (a) served-detection (clear pending at the car's next pit stop) + rescission
  cancellation, or (b) keep pending_s in the live NET gauge only and exclude it from
  the finish-prediction path, or (c) accept as-is (penalties visible on the rail,
  slightly worse long-horizon numbers). No work until decided.
- **Step 4 (post-weekend):** `race_control.classify()` new `"unparsed_penalty"` kind +
  calm-board dim-amber rail alert.

### Epic 4 — WEC research spike (timeboxed ~1 session; slot into any gap)
- WEC timing is very likely Al Kamel: try `livetimingFeed("wec")` against the existing
  DDP client in `alkameldp.py`; check Timing71 WEC archive availability; find out whether
  WEC has any telemetry (unknown).
- Deliverable: findings + go/no-go. **Adapter build waits until IMSA is live-validated.**
- Also the recommended "tinker target" now that F1/IndyCar are frozen — same exploratory
  fun, pointed at the north star.

### Epic 5 — IndyCar close-out (one run, post-weekend)
Run the held-out `INDYCAR_RACES` suite once (including the fresh Mid-Ohio archive),
write the honest verdict here (net edge by track type: oval/street/road), freeze.
No tuning, no per-track configs.

### Epic 7 — WYWA & calm-board polish (Paul wants to lean in)
Catch-up card refinements, breath-noise evaluation, a visible honest home for
`projected_finish`, WATCH/BATTLES knob tuning (`BUDGET_PER_CLASS`, `CATCH_GAP_S`,
`CATCH_TREND_LAPS`, `BATTLE_GAP_S`) via hot-reload against streaming replays.

## Research items (unscheduled)

- **Driver-change / min-drive-time rules:** vary per race and per class; need a reliable
  per-event source (IMSA supplementary regulations, WEC sporting regs). Until found, the
  `(lineup_size − 1)` heuristic stays.
- **"+1 lap" flicker** — cosmetic; hysteresis fix idea documented in session notes.
- **IndyCar push-to-pass UI slot** — `OverTake_Remain`/`OverTake_Active` exist in the
  live feed; only if IndyCar is ever unfrozen.

## Parked north star (no work — direction only)

- **Broadcast video linkage:** connect the app to the livestream and pull video clips of
  notable overtakes/incidents, feeding the "while you were away" card. Paul's "final
  boss" idea — everything WYWA-related should avoid foreclosing it.

## Held-out / regression sets

- **IMSA (6 complete archives, the default gate):** Daytona 24h, Petit Le Mans 10h,
  Indy 6h, Monterey, Long Beach, Detroit — paths in `validate_races.py` under
  `ARCHIVE_DIR`.
- **IndyCar (10 archives, `indycar archives/`):** deliberately unrun until the Epic 5
  close-out so the first validation is honest.
- Only complete run-to-chequered archives count; verify span + final flag before adding.

## Standing gates

- Any calculator/replay/evaluator change → `./check.sh --full` (tests + IMSA suite).
- UI changes → offscreen render + `replay.py --stream` feel-test.
- Finish-predictor changes → validated across ALL races, never a single archive.
