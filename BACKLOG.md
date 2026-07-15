# Backlog

Epic-structured roadmap, reconciled 2026-07-03 from the original phased plan + an
external (Cursor) audit + owner feedback. This file is the single source of truth for
"what's next and why" — code comments should point here, not at phase numbers.

**Product identity:** primary = *endurance race strategy companion* (IMSA → WEC) with
honest confidence signaling. Secondary = *strategy learning lab* (replay → evaluate →
tune). top pain points, which shape acceptance criteria everywhere:
**#1 wrong class-leader gap · #2 DUE TO PIT called too early.**

## Decisions log (do not relitigate without new information)

- **2026-07-15 — Epic 10 scoped: web board content migration (owner call, same
  session as the Epic 9 merge).** Trigger: owner compared the shipped web UI v1
  against the phase-1 sounding-board mockups and asked why they look unrelated.
  Honest audit: the mockups' *decisions* shipped faithfully (Option C fact-forward
  NET trailing column, tap-to-explain panel, settled-dimming, ±band in panel not
  row) and the visual skin was the approved F1OpenViewer language — but v1 is
  scaffolding, not the phase-1 board: **no right rail, no WYWA, no Notes column**,
  and the panel lacks the sketch's leader-remaining comparison and per-stop cost
  estimates. Epic 10 (below) captures that gap as acceptance criteria so it can't
  get lost. Lesson logged: ship summaries must say "foundation, N% of the target"
  when that's what it is.

- **2026-07-15 — Epic 9 execution shipped and merged: web UI v1 on main.** The full
  planned sequence landed on `feature/epic9-web-direction` (7 commits): ① Poller
  extraction → ② Electron/React/Tailwind scaffold (browser mock mode included) → ③
  tap-to-explain panel (net-math breakdown + pit history) → ④ NET trailing column
  (▲/▼ by direction, dim when settled) → ⑤ session clock + RC ticker → ⑥ pre-merge
  review pass (5 defects: Qt leaking into poller via timing_table, per-class tables
  showing overall-leader gaps, pre-race clock showing FINISHED, Electron listener
  leak, TS duplicate-identifier) → ⑦ `poller_daemon.py` + Electron path proven on
  real São Paulo data (35 cars over IPC). Architecture: Python engine writes
  `net_analysis` (Poller side-effect; daemon or PyQt6 dashboard), Electron reads
  SQLite readonly. The PyQt6 dashboard remains the reliability-proven race-day
  display until the web UI earns that trust at a live event; open PyQt6-side bugs
  (P1 row drop, WEC quali `SESSION` ordering) stay open under Epic 9 inputs.

- **2026-07-14 — Epic 9 phase 2 closed: web tech direction decided (Electron + React +
  Vite + Tailwind). UI freeze lifted.** F1OpenViewer steal-audit (MIT) completed —
  design-language finds: Rajdhani + Space Grotesk fonts, HSL CSS-variable palette, 36px
  row pattern with 4px team-color spine, framer-motion for transitions. Sync engine
  (`useSyncEngine.ts`, ~150 lines MIT) is the blueprint for the broadcast-video north
  star. Decision: move display layer to Electron/React; Python data engine (calculator +
  SQLite WAL) unaffected. Trade weighed honestly — PyQt6 survived the 6H São Paulo race
  (reliability-proven) vs. web (every look owner likes, tap-to-explain panel trivial,
  iteration speed). Electron reliability not a meaningful risk (VS Code, Slack). First
  commit (Poller extraction to `src/poller.py`) landed same session, 118 tests green.
  Epic 9 sequence: ① Poller extraction ✅ → ② Electron scaffold + IPC bridge → ③
  tap-to-explain panel → ④ net cluster fact-forward. Open display bugs (P1 row drop,
  WEC quali ordering) fold into whichever commit touches their area.

- **2026-07-14 — Epic 9 phase 1 closed: product definition answered (owner sign-off,
  sounding-board session).** Five answers, all owner-approved:
  1. **One screen.** The calm board is the app; the dense timing table stays an
     escape-hatch window (`Timing ↗`); replay/eval remains a terminal workflow. Revisit
     only if between-events replay study becomes a habit in its own right.
  2. **Race-day only — confirmed**, not reopened (07-04 decision stands; race week
     validated it: the SP capture + replay drove the whole calibration cycle with no
     new UI).
  3. **Solo-first.** Design for an audience of one who knows the app; friend keeps the
     clone + setup.sh path. No onboarding/multi-user work ever. "Casually viewable
     board" noted only as a minor thumb on the web-tech scale for phase 2.
  4. **Broadcast-video "final boss" = phase-2 tiebreaker only.** Zero video work now,
     but where stacks are otherwise close, pick the one that doesn't foreclose it.
  5. **Identity sentence (grades every phase-2 choice):** *comprehension speed with
     honest confidence signaling — measured in seconds to re-orient after stepping
     away.* Prediction accuracy is explicitly NOT the edge (the 07-13 recalibration
     agrees from the math side); "strategy learning lab" stays real but lives in the
     replay/eval loop, outside the resident UI.
  Direction reactions (mockup-level, feed phase 2): net cluster goes **fact-forward**
  — facts (pos/gap/notes) lead the row, NET becomes a clearly-labeled trailing
  projection column with tap-to-explain panel (explainability = the confidence
  signaling made inspectable); ±band lives in the panel, not the row. WYWA story
  card: ship the **collapsed** version first (stat header + per-class top-5 lines +
  below-top-5 threats, retrospective narration only, never net projections); owner
  expects to want the ranked expand eventually — planned second, and it cannot be
  signed off before the next live race (event ranking has no evaluator; tabled
  07-04 for exactly that reason). Phase 2 must also grade the web lean honestly:
  PyQt6 just survived a live 6h race, and a web front end adds two runtimes to a
  race-day tool — reliability-proven vs. iteration-speed is the real trade, not
  aesthetics. Full option set + mockups archived in the phase-1 sounding-board
  artifact (session 2026-07-14).
- **2026-07-14 — WEC DC delta is ~5s at SP 2026, not 30-40s; the 07-05 claim is
  superseded.** With real driver-change labels finally flowing (record_driver wired
  into wec_live 07-13, PR #16), the fitted DC delta on the SP race capture is ~5.4s —
  the archived "30-40s longer" estimate was fit on unlabelled data where DC time
  leaked into the fuel fit. One-race sample; treat DRIVER_CHANGE_DELTA_MS=12s as a
  fallback-only constant and let the per-race fit speak. Revisit at the next WEC event.
- **2026-07-14 — No hardcodable pit-stop-minimum exists in the 2026 regs; model floors
  empirically if at all.** Read the actual 2026 sporting regulations (both series):
  WEC has no stopwatch minimum (sequential refuel-then-tires + crew/equipment limits +
  BoP-tuned fuel flow set the effective floor); IMSA's Minimum Full Refueling Time is
  LMP2-only and formula-based (Art. 37.8), while GTP/GTD PRO/GTD instead carry a
  per-stint energy CAP (Art. 22.3.14 — see the Epic 2 follow-on note). Decision: never
  hardcode regulation stop-time constants; any floor logic must be inferred from each
  race's observed stops.
- **2026-07-13 — Honest recalibration shipped (PR #13) — see wec_calibration_findings.md.**
  Three WEC net-ordering bugs fixed behind `trust_feed_laps_behind`; finish-blend
  halved to 0.3/0.08 after a 14-race sweep showed the old 0.6/0.15 was the worst
  option on honest data. SP net MAE 5.20→4.15 and error now decays through the race.
  Also 07-13: caution-aware predict_stop REJECTED on evidence (491 caution stops,
  effect is noise — full detail in Epic 7 B2 notes); robust fuel fit shipped instead.
- **2026-07-06 — Fable Tier-3 exit review (see `FABLE_REVIEW.md` §5).** Four fixes
  shipped: (1) evaluator catch metrics were non-deterministic AND cross-race
  contaminated in every validate_races run to date (`_GAP_HIST` never reset between
  builds under the same oid) — **all pre-07-06 catch% numbers are suspect**; (2)
  pending penalties never expired (feed never announces "served") — a served
  drive-through double-counted in net forever; now expires at the car's next
  pit-lane visit, WEC net MAE improved 5/7 races, IMSA unchanged; (3) WEC dash
  multi-car penalties dropped all but the first car; (4) `SERIES_OVERRIDES` in
  config.json — tune WEC knobs at FP1 without touching IMSA calibration.
  External (Gemini) review triaged same day: ~90% already shipped/decided; its
  three new items folded in (per-series config = fix 4; explainability panel →
  Epic 9 phase-2 input; live-vs-replay expectation note → `WEC_RACE_WEEK.md`).
- **2026-07-05 — DRIVER_CHANGE_DELTA_MS=12s is too low for WEC; tune live at FP1.**
  WEC archives show a consistent -7s to -24s stop under-prediction bias vs IMSA's
  near-zero. Root cause: WEC driver-change stops run 30-40s longer than IMSA, not 12s.
  Anomaly-heavy archives (Imola '26: penalties + wet weather; SP '25: 27-min + 57-min
  garage repairs; Qatar: 10h distance race) explain the outlier MAEs — not model
  failures. Decision: do not tune off archived data. Tune DRIVER_CHANGE_DELTA_MS live
  via config.json during São Paulo FP1, watching actual vs predicted stop times.
- **2026-07-05 — WEC regression set established (7 archives); Le Mans 24h excluded.**
  7 complete (run-to-chequered) WEC archives verified and added to `validate_races.py`
  WEC_RACES: SP 2024, SP 2025, COTA 2025, Fuji 2025, Bahrain 2025, Imola 2026, Qatar
  2025. Le Mans 24h excluded permanently — different field size (62 cars), entry
  structure, and overnight dynamics make it a poor regression baseline. Run with
  `python src/validate_races.py --wec`. Baseline (2026-07-05, 6h races):
  NET MAE 2.82–4.48, TRK MAE 2.01–4.20, CATCH% 78–100%.
- **2026-07-04 — NET suppresses itself once the final pit cycle is done.** owner's call
  (replay session): end-of-race net positions are knowingly wrong once no stops remain
  to model, so a per-car `net_settled` flag (calculator.py) collapses NET to track
  position (dim in the table, overlay quiet on the calm board). Exception: a pending
  in-race penalty keeps that car's NET live until served. Shipped commit 1ce501b,
  validated against the archived IMSA replay (3 penalty cars correctly stayed live).
- **2026-07-04 — Projection colours never ride on car numbers.** The green/red
  projected-gain/drop colouring on PROJECTED PODIUM numbers read as broken class
  colours (misread in testing it in replay). Rule going forward: class colours are permanent
  per class code via SeriesProfile (WEC now includes LMP2 blue for Le Mans), and any
  gain/drop signal is carried by a separate ▲/▼ glyph, not by recolouring the number.
  Ships in the same 1ce501b batch (exempt from the Epic 9 UI freeze as a
  comprehension/bug fix, same class as the NET rule above).
- **2026-07-04 — Race-day-only tool; practice/quali sessions are DATA-VALIDATION ONLY,
  never a supported dashboard mode.** owner's call, made while scoping the fuel-telemetry
  wiring (Epic 2): practice/qualifying will be used solely to confirm we can consume and
  display new live data streams (IMSA `telemetry.imsa.com` GTP/GTD/GTDPRO fuel feed, WEC
  `VET` column). Every other dashboard feature (catch-up, battles, projected podium, DUE
  TO PIT, net position) is designed around race conditions and won't be meaningfully
  exercised outside a race — so no practice/quali UI/mode will ever be surfaced. If the owner
  wants live timing for practice/quali, he'll use Timing71 directly ("already nearly
  perfect" for that). Do not build session-type toggles or practice-specific features.
- **2026-07-04 — UI code frozen until the direction spike (Epic 9) decides.** Owner's
  call: "do it right the first time." No new visual/cosmetic work (placement, wording,
  styling, brightness) until Epic 9 answers product purpose + front-end direction.
  NOT frozen: behavioral tuning via config knobs (Epic 7 B2 — values transfer to any
  front end), catch-up *logic* refinements, and all Epic 8 WEC work.
- **2026-07-03 — F1 and IndyCar scrapped entirely (supersedes 07-02 freeze).** Owner's
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

### Epic 8 — WEC live pipeline ✅ 2026-07-12 (race captured end-to-end)

**Outcome:** the 6H São Paulo race was captured complete (155k frames, zero dispatch
errors) and the capture drove the post-race calibration cycle (PRs #10–#13: replayer,
official-rank dispatch, robust fuel fit, honest recalibration — three WEC net-ordering
bugs fixed, blend halved). Commit 5 "field corrections from FP1" was superseded by that
calibration work. Timing71 fallback confirmed working: the post-race SP 2026 archive
was obtained and is now in the regression set. Remaining loose ends moved to their own
homes: P1-row display bug → open-bugs note under Epic 9 inputs; driver-change feed
detection → shipped 07-13 (see Research items).

*(Historical plan below kept for reference.)*

**Goal:** raw-capture-first WEC client ready before São Paulo FP1.
Raw capture = must-have; live board = nice-to-have for race day.

**Status (2026-07-03) — commits 1–4 done, full runbook in `WEC_RACE_WEEK.md`:**
1. ✅ WEC `SeriesProfile` (HYPERCAR/LMGT3).
2. ✅ `src/wec_live.py` — full Griiip SignalR+msgpack client, DB persistence.
3. ✅ `--record` raw-capture mode.
4. ✅ `session_picker.py` WEC Live page wiring (enabled, not a stub).
- ✅ Fix: `ranks`/`gaps` nested-`items` unwrapping (found from live F1 traffic).
- ✅ `tests/test_wec_live.py`: 70 tests, all green (`./check.sh`).
- ⬜ **Open (Phase 3, see `WEC_RACE_WEEK.md`):** Timing71 archive fallback
  confirmation (corrected 07-04: T71 can't record live, it provides a post-session
  archive — data-only fallback, no live board), `--discover` check for WEC
  seriesId=10 (~07-07). Kill-network reconnect test ✅ done live at FP1 07-10
  (see the FP1 entry below).
  *07-03 check run: no WEC sessions yet, transport healthy (connect + group join OK).*
- ✅ **Fixed (07-03):** `--record`'s gzip archive wasn't crash-safe (a hard process kill
  truncated the tail). Now flushes after every frame write; verified with a second
  kill test. See `WEC_RACE_WEEK.md` for detail.
- ✅ **Fable Tier-1 review (07-04, commits 65a26c0 + d2360e3):** 6 race-day bugs
  fixed (first pit stop dropped, status fields nulling each other, Z-timestamp
  parse failure on Py3.9, frozen best lap, --discover NameError, DVR fallback
  loading WEC as IMSA) + capture-replay harness (`tests/test_wec_capture_replay.py`,
  real bootstrap fixture) + `--replay` offline mode = the Commit-5 workflow, ready
  before FP1. Full findings + FP1 verification checklist: `FABLE_REVIEW.md`.
- ✅ **FP1 live-fire session (07-10, PR #2 merged, `feature/endurance-refocus`
  deleted):** `--record` ran live at São Paulo FP1 and the session doubled as a
  hardening pass — 4 race-day bugs found and fixed against real traffic:
  cross-thread SQLite crash dropping status/flag writes (0840be8), bootstrap
  hydration seeding phantom caution periods (e982255), zombie freeze after a
  network blip — reconnect re-joined the group but data never resumed and the
  stale watchdog was disarmed (39f96f5), and feed-liveness narrowed to
  ranks/gaps/laps with `data_age` in the heartbeat (87ac9e2). Both recovery
  paths verified live: quiet-feed watchdog auto-restart AND wifi-toggle
  in-place reconnect. Raw FP1 capture in hand
  (`data/wec_raw_20260710_162631.jsonl.gz`).
- ⬜ **UNBLOCKED — Commit 5 (target 07-11, before the race):** field corrections
  from the real FP1 capture — run it through `--replay` + the harness; work the
  `FABLE_REVIEW.md` §2 verify-at-FP1 checklist (class names, VET shape, pit
  timing, stint-prior sanity); regenerate the test fixture from the real capture.
- ⬜ **Open bug (found at FP1, display-only):** dashboard timing table drops the
  P1 row. Does NOT affect capture/eval; fix before the race if time allows.

**Race-week fill queue (07-04→07-12, between Epic 8 checklist items — all
freeze-compatible, reviewed 2026-07-04):**
1. ✅ **São Paulo WEC entry-list JSON** (07-04, commit d96547f): 35 cars from the
   FIA provisional entry list v1. Found + fixed along the way: 9 car numbers
   collide with the IMSA Monterey entries file and `_load_entries()` merges flat —
   static entries are now trusted only when their class matches the feed's class.
   Also 07-04: WEC stint priors (HYPERCAR 33 / LMGT3 30 — sanity-check at FP1) and
   Interlagos weather coords in config. Fable Tier-2 architecture review done same
   day — see `FABLE_REVIEW.md` §4 (headline: engine/display boundary is clean;
   Poller/_build_rows extraction from dashboard.py should be Epic 9's first commit).
2. **Driver-change / min-drive-time rules research, WEC sporting regs first**
   (promotes the unscheduled research item) — São Paulo-relevant, and replaces the
   `(lineup_size − 1)` heuristic eventually. Pure research, survives any UI direction.
3. **Epic 7 B3** catch-up logic — rides along with the B2 session.
Everything else is frozen (cosmetics → Epic 9) or calendar-blocked (Epics 1b/2 →
next IMSA event; Epic 9 → post-race-week).

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
- 07-06: retry discovery. (Timing71 fallback = post-session archive only, confirm at FP1.)
- 07-07/08: picker wiring, tests, hardening.
- 07-09: full rehearsal — `--record` under `--no-db`, kill-wifi reconnect test.
- 07-10 FP1: first guaranteed live traffic; `--record` runs through every session.
- 07-11: iterate parser offline against Friday's capture.
- 07-12 race: `--record` = non-negotiable; live board = best-effort.

**Fallbacks (ranked):**
1. Raw frame capture → post-race parsing.
2. Timing71 post-session archive → existing `timing71.py`/`replay.py` path
   (IMSA-proven; data-only, available after the session — not a live-board fallback).
3. Browser HAR/WS capture (manual last resort).

**Top risks:** SignalR classic-vs-Core (day-1 check) · auth/cookie gate on negotiate ·
msgpack schema opacity (capped by raw capture) · hub-target/session-ID discovery ·
rate limiting on pre-race-week negotiate attempts.

**Research note (no v1 work):** `VET` column hypothesis = Virtual Energy Tank % for
Hypercar. If confirmed in captures, it's WEC's fuel-telemetry analog — a natural Epic 2
sibling for WEC.

### Epic 6 — Timing tab ✅ 2026-07-03
Done in two commits as planned: 9be7f07 (pure-move `dashboard.py` table pieces →
`src/timing_table.py`) + 6ca9bc8 (wire `Timing ↗` to a separate Dashboard window).
Class colors resolve from `ctx.profile` at build time (`timing_table.py:72`); the
module-level IMSA dicts remain only as a fallback for callers without a live context.

### Epic 1 — Confidence UX + IMSA live validation
- **1a ✅ 2026-07-03 (commit 7dda651):** `predict_stop` returns scope (car/class/field/
  default); calm board paints ±Ns after NET overlay, header shows "LOW PIT DATA" when
  model is thin; dense table tooltip explains non-car scope. Zero regression (net 3.00 /
  trk 2.44). `ponytail: inline scope letter skipped, add when feel-test says tooltip
  isn't discoverable enough.`
- **1b (calendar-blocked, next IMSA event):** 2–3 live sessions; watch **class-leader gap
  correctness** (pain point #1) through a full pit cycle; compare live vs replay eval metrics.
- **Acceptance:** NET order trustworthy through one full live pit cycle; class-leader gaps
  match broadcast.

### Epic 2 — IMSA + WEC fuel telemetry (biggest accuracy lever; do it right the first time)

**IMSA side** — capture blocked on next IMSA practice/qualifying session:
- Capture with `src/telemetry_capture.py` at next IMSA practice. **First verify owner's
  report that GTD + GTD Pro now have live telemetry** (feed was GTP-only when captured;
  only LMP2 confirmed without).
- Then `src/telemetry_adapter.py` → `standings_current.fuel_pct/fuel_flag` → `fuel_due`
  → DUE TO PIT rail. Debounce/cross-check mandatory — raw VFT is documented-unreliable
  (`calculator.py` ~L647: reads near-empty for laps after refuelling). Telemetry
  overrides the stint estimate only when sane; stint estimate remains the LMP2/fallback
  path.
- **Acceptance (targets pain point #2):** DUE calls within ~2 laps of the actual pit
  window on replay + one live session, per telemetry-covered class.
- **Follow-on once telemetry lands:** 2026 IMSA Sporting Regs Art. 22.3.14 caps
  GTP/GTD PRO/GTD to a maximum energy allowance per stint (steep penalty for
  violation: Stop+100/200/300s) — a hard regulatory ceiling, not the soft
  consumption-rate estimate `fuel_due` currently leans on. Worth cross-checking
  predicted stint energy against that cap as an additional DUE TO PIT signal once
  live telemetry is flowing. Needs the actual per-class cap values (published in
  the Technical Regulations, not the Sporting Regs — not yet sourced). Likely
  partially redundant with the existing `_class_stint_laps` empirical stint-length
  tracking (`calculator.py`), so validate it adds real signal before wiring it in —
  don't build it speculatively.

**WEC side** — capture blocked on São Paulo FP1 (~07-10):
- Confirm the `VET` column hypothesis (Virtual Energy Tank % for Hypercar) from the
  first live `livetiming.fiawec.com` capture (see [[project-wec-pipeline]] +
  `wec_spike_findings.md:111`). Unconfirmed and, per current evidence, Hypercar-only —
  no LMP2/LMGT3 equivalent found yet.
- If confirmed, it rides the same SignalR/msgpack feed Epic 8 already builds — no
  separate scrape needed (unlike IMSA, which requires the standalone
  `telemetry.imsa.com` AppSync feed).
- Same adapter pattern as IMSA: wire into `fuel_due` only when sane, same debounce
  discipline.

**Validation scope for both:** practice/qualifying sessions exist ONLY to prove we can
consume + display these new streams — see the 2026-07-04 decisions-log entry
("race-day-only tool"). Not a vehicle for testing catch-up/battles/podium/etc.

### Epic 3 — Penalty parsing hardening ✅ 2026-07-03
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
- **Step 4 ✅ (commit 263ef3d):** `race_control.classify()` new `"unparsed_penalty"`
  kind (`race_control.py:99`) + calm-board dim-amber rail alert
  (`dashboard_calm.py:1407`). Closes the epic — penalties can no longer silently
  break net.

### Epic 7 — WYWA & calm-board polish *(in progress 2026-07-03)*

**Done (2026-07-03):**
- ✅ `projected_finish` visible home: PROJECTED PODIUM rail section (commit ce13669),
  last in attention order; dim when projection = current position, green/red on
  disagreement. Placement + wording deferred to the general UI refactor.
- ✅ Hot-reload wired at top of `refresh()` (fires once per poll cycle before any
  config read, so `BUDGET_PER_CLASS`/`CATCH_GAP_S`/`BATTLE_GAP_S`/`CATCH_TREND_LAPS`
  all pick up live edits reliably).
- ✅ **B2 knob-tuning session (2026-07-04)** — fresh Monterey stream (923 frames) +
  evaluator run (`logs/eval_20260704_090013.txt`) informed the values:
  - `BUDGET_PER_CLASS`: 1 → **3**. owner's call: capping at 1 net-overlay highlight
    per class hides real shakeups — after a long absence, several cars in one
    class can have changed order for different reasons. Caveat carried forward:
    evaluator shows NET currently *less* accurate than plain track position
    (MAE 2.48 vs 2.37) — raising the budget surfaces more of a signal that's
    presently underperforming; revisit once Epic 2 telemetry firms up net
    accuracy.
  - `CATCH_TREND_LAPS`: 3 → **5**. Evaluator: catches landed 10.5 laps later than
    predicted (78% hit-rate) — matches observed that multi-class
    traffic makes a chaser look like it's flying up only to have that reversed
    once it hits the same traffic. Requiring a longer sustained trend before
    calling "catching" should cut the false-early fires. open to further
    data validation — check the next evaluator run's CATCH lateness number
    against this baseline (10.5 laps late) once a new stream/eval pair exists.
  - `CATCH_GAP_S` / `BATTLE_GAP_S`: **unchanged** (2.0 each) this session —
    changed one variable (trend laps) at a time rather than compounding, and
    BATTLE_GAP_S needs a live-paced replay (not a completed/frozen one) to
    judge by eye.
  - `CAUTION_PENALTY_FACTOR`: 0.45 → **0.35** (bonus, not one of the original
    four). Evaluator explicitly flagged it "too HIGH" — caution-stop bias was
    +19.7s over-predicted.
    **Correction (07-04, B3 session):** this was chasing a knob with no causal
    path to the metric. `CAUTION_PENALTY_FACTOR` only scales the live
    `pit_now_position` projection (`calculator.py:869-876`), never
    `next_stop_ms` — the value the STOP TIME "caution" bucket actually grades
    (`calculator.py:802`, via `model.predict_stop()`). Confirmed empirically:
    a fresh evaluator run at 0.35 reproduced the *exact same* +19.7s bias as
    the 0.45 baseline, byte-for-byte. `evaluator.py`'s suggestion text and its
    `--auto` autotune branch both pointed at this knob incorrectly — both
    fixed (07-04): suggestion text now names the real cause, and the autotune
    branch that nudged `CAUTION_PENALTY_FACTOR` off this metric was removed
    (it would have walked the knob to a bound for zero effect). The 0.35 value
    stands on its own merits if there's a real reason to prefer it, but it was
    not fixing what B2 thought it was fixing.
  - **Real gap surfaced by the above:** `model.predict_stop()` has no
    caution-awareness at all — caution stops carry a genuine, unaddressed
    +19.7s over-prediction bias. Needs an actual modeling fix (a caution-stop
    branch/discount inside `predict_stop`, likely mirroring the logic
    `CAUTION_PENALTY_FACTOR` already applies to `pit_now_position`), not a
    knob tweak. Unscoped — next time this session has spare capacity, or when
    Epic 2 telemetry work is touching the stop-cost model anyway.
  - ❌ **Caution-aware predict_stop REJECTED after investigation (07-13).**
    Measured across all 491 caution stops in the 14-race regression set
    (offline replays): caution barely moves stop DURATION — pooled delta vs
    the green fuel fit is +1.0s mean/−2.7s median on IMSA (n=457), −6.1s
    mean/−1.8s median on WEC (n=34). The old "+19.7s caution bias" readings
    were tiny-sample noise (n=1-2 buckets). The genuine caution advantage is
    track-position cost during the stop, which `CAUTION_PENALTY_FACTOR`
    already models in `pit_now_position`. A duration branch would fit noise.
    Evaluator's caution suggestion now gates on n≥5. What the investigation
    DID find and fix: the fuel fit's raw-duration upper-tail clip biased
    predictions low on wide/bimodal races (up to −46s Qatar analysis-side) —
    replaced with two-sided residual-space rejection (`_robust_linfit`), and
    Qatar's remaining −49s eval "bias" decoded as ~12 unforecastable
    multi-minute LMGT3 garage stops (median error there is −6s) — the
    evaluator now reports median bias alongside mean.
  - **Validation (07-04, B3 session):** fresh full-race `--stream` + evaluator
    run (`logs/eval_20260704_102649.txt`) confirms `CATCH_TREND_LAPS` worked as
    intended — hit-rate 78%→88%, median lateness 10.5→2.6 laps vs the 090013
    baseline (small sample, n=8, treat as directional not final). NET MAE
    unchanged (2.48 vs 2.37 track) as expected — nothing this round touched
    that path. `BUDGET_PER_CLASS` isn't evaluator-measurable (display-only,
    no accuracy metric applies) — still just owner's-call, not re-litigated.
    `CATCH_GAP_S`/`BATTLE_GAP_S` themselves: still unchanged/untuned  
    explicitly deferred further calibration to the live WEC race (07-06+),
    same as the new `BATTLE_TREND_LAPS`/`BATTLE_MIN_DROP_MS`/
    `BATTLE_NOISE_TOL_MS` knobs added this session (see below).

- ✅ **B3 session (2026-07-04), BATTLES rail:** live-paced 60x replay + the owner at
  keyboard. Landed: plain-English "catching #X — Ns (Ys/lap)" text replacing
  the ambiguous bare `▼` (too easily confused with the unrelated `▼P{n}`
  net-position arrow); a rail-local closing gate (`BATTLE_TREND_LAPS`=3,
  `BATTLE_MIN_DROP_MS`=80, `BATTLE_NOISE_TOL_MS`=120, all hot-reloadable) so
  the signal is actually visible in practice, separate from the stricter
  main-board `catching` call (untouched); a rate-calc sign bug (`--0.0s/lap`)
  fixed along the way. owner: "fine for now, will fine-tune more once I know
  what's actually happening in a real race" — expect further BATTLE_* +
  BATTLE_GAP_S adjustment during the live WEC race (07-06+).
- ✅ **B3 session, RACE CONTROL rail:** was truncating mid-word at 340px even
  after widening from 238px. Switched to word-wrap + a bullet-column table
  (matches the existing legend's `row()`/`col()` idiom) so wrapped
  continuation lines stay visually distinct from the next message's bullet.
  Also color-codes car numbers in RC text to each car's class-spine color
  (reuses `penalties._CARS_RE`, no new parsing), bolded per follow-up.
- ✅ **B3 session, evaluator bug found + fixed:** `CAUTION_PENALTY_FACTOR` was
  never able to inform the STOP TIME caution-bias metric it was suggesting
  changes to — see the correction note above. Suggestion text and the
  `--auto` autotune branch both fixed; TUNE_BOUNDS entry removed.

**Open (needs manual testing — streaming session):**
- Catch-up LOGIC refinements (event ranking/selection in `catchup.py`) —
  **not started this session.** `MIN_POS_MOVE`, `MAX_EVENTS`, and the `_RANK`
  floors are all still at stock defaults (2, 8, and the original per-tone
  values respectively) — this was B3's originally-named scope and got
  displaced by the BATTLES/RC rail work above. Allowed (behavioral, not
  frozen). Cosmetic card/board tweaks — **frozen until Epic 9 decides**
  (07-04 decision).
  **Tabled (07-04):** owner's call — no automated/evaluator path exists for
  this one. Unlike `CAUTION_PENALTY_FACTOR`/`CATCH_TREND_LAPS`, there's no
  ground truth to grade a "while you were away" brief against (existing unit
  tests only check the code does what it's told — cap enforced, dedup,
  sort order — not whether the *values* feel right). Requires watching a real
  race. Tabled until the live WEC race (07-06+ practice, or São Paulo race
  07-12) rather than another replay session.
- Same session doubles as UX-input gathering: screenshot/note whatever bugs
  him visually → raw material for Epic 9's design phase.
- **Gate for any code change:** `./check.sh` + `replay.py --stream` feel-test;
  evaluator report (`logs/stream_*.txt`) as the honesty check metrics didn't slip.

### Epic 9 — Product definition + UI direction spike *(✅ CLOSED — spike 07-14, execution shipped+merged 07-15; see decisions log. Web UI v1 lives in `ui/`, run recipe in `ui/README.md`)*

**Open display bugs (PyQt6 side, still open):**
dashboard timing table drops the P1 row (found at FP1); WEC quali
`session_type="SESSION"` ordering; screen-lock freeze fix shipped (PR #9) but watch
next event's log for "table rebuild:" lines.

**Web UI v1 known-nexts:** promoted into **Epic 10** (scoped 07-15) along with the
full mockup-vs-shipped gap list — see that section.

owner (2026-07-04): research and decide before investing more in UI code — "do it
right the first time." Two phases, one spike; phase 1 feeds phase 2.

**Phase 1 — nail the product's main purpose.** Pressure-test the existing one-liners
(North Star = "catch up at a glance after stepping away"; BACKLOG identity =
"endurance strategy companion with honest confidence signaling / strategy learning
lab") into answers concrete enough to drive architecture: one screen or several?
race-day tool vs between-races study tool? solo-use or ever shared? does the
broadcast-video "final boss" make the cut? Sounding-board format (options/mockups,
not open questions — per the UX-redesign playbook).

**UX gripes captured (B2 knob-tuning session, 2026-07-04)** — raw material for
phase 1/2, not yet actioned (cosmetics frozen):
- "While you were away" card truncates text when a message string is long (e.g.
  a penalty explanation) — cut off mid-sentence.
- WYWA is "too basic" for longer absences (30+ min): it lists lead changes but
  doesn't convey magnitude/story — no sense of *how much* happened or how
  significant the gap in time was. goal: a "tell the story" version —
  probably both a ranked highlights list ("biggest things that happened") and
  a stat summary (laps run, cautions, lead changes) with drill-down. **Needs a
  planning/mockup pass, not a quick tweak** — flagged he may be gold-plating
  an already-working feature, so scope this carefully in Epic 9 phase 1/2
  rather than assuming it needs a rebuild.
  **Scope refinement (07-04, later in session):** WYWA should stay narrowly
  about on-track order/position changes and *why* they happened — explaining
  the shuffle since last watching the live TV feed. It should NOT fold in
  net-position projections — those already live on the main calm board and
  would just duplicate/complicate the card. Narrows the phase 1/2 design
  problem considerably. Principle: WYWA = retrospective narration (what a
  live-TV viewer would note down), main board = our forward-looking
  analytical layer (net/catch projections) — never blend the two.
  **Verified against code (07-04):** current `catchup.py` gain/loss/lead
  detection already honors this — it keys off `effective_pos_in_class`, not
  the `net_position` projection. `effective_pos_in_class`
  (`calculator.py:697`) isn't a forward prediction either; it only re-ranks
  currently-boxed cars by real time gap instead of the feed's stale frozen
  slot, so it matches live-broadcast reality rather than projecting it. No
  conflict found — implementation already matches the principle.
  **Scope input (07-04, Epic 7 B3 session):** current thinking on what
  WYWA needs to cover, ahead of an Epic 9 mockup pass — not a spec, a starting
  point for that discussion:
  - More than one event per class (current `MAX_EVENTS`=8 field-wide cap is too
    coarse for this).
  - On-track position changes for the **top 5 of each class**, specifically.
  - Anything happening **below** the top 5 that could plausibly *impact* the
    top 5 given known strategy/pit-cycle state (e.g. an undercut brewing outside
    the top 5 that will surface once cycles resolve).
  - Pit stops, cars stopped on track, incidents, and penalties that happened
    while away.
  framing: "this is going to be a lot of info... which means we need
  to discuss and come up with a great UX plan before implementing changes" —
  he does not want this built from this note alone. Treat as raw input for the
  Epic 9 phase 1/2 mockup/planning pass, same as the other WYWA gripes above.
- Net position display (the `±18` / arrow / P7 cluster) is unclear at a glance —
  **both** a comprehension problem (unclear what it's measuring: time
  gap? positions gained/lost? track position vs. net-of-pits?) **and** a
  legibility problem (cramped, no labels, requires already knowing the app to
  parse). Together these defeat the "calm, at-a-glance" design goal — this is
  the single biggest hit to the North Star casual-glance use case found this
  session.
- **Keepers, validated:** Battles rail and Projected Podium rail — liked as-is.
  Notes column message copy — liked the look, thinks it's "fairly accurate,"
  wording will still need refinement pass.
- Pit / Race Control / Race-at-a-Glance cards — hard to judge relevance/
  accuracy without a concurrent video stream to check against; deferred until
  that's available (ties to the parked broadcast-video north star below).

**Explainability panel (Gemini review, folded in 07-06):** tap/click a car →
show the net-math breakdown (real gap, remaining-stop cost ± band, penalty
carry, driver-change increment). Directly attacks the net-cluster
comprehension gripe above — the number becomes inspectable instead of
needing a legend. Phase-2 design input, not committed work.

**Phase 2 — front-end direction.** Inputs: phase 1 answers + the F1OpenViewer
steal-audit (MIT, github.com/npanu420/F1OpenViewer v1.2.0, Electron/React/Tailwind):
(a) design-language audit — screenshots, Tailwind theme values, layout/typography;
(b) its **video↔timing sync engine** — MIT prior art for the broadcast-video north
star; (c) MultiViewer = closed source, inspiration by use only. Decide: keep
polishing PyQt6 vs move display layer to web tech (engine/data layer unaffected
either way). Preferred looks (Timing71, F1OpenViewer, MultiViewer) is
web-tech — weigh honestly, decide once.

**Output:** a decision-log entry + a scoped plan for the chosen direction.
Until then: cosmetic UI work frozen (see 07-04 decision).

### Epic 10 — Web board content migration *(scoped 2026-07-15, not started)*

Close the gap between web UI v1 (scaffolding + two mockup-validated patterns) and
the phase-1 board the sounding-board mockups describe. Everything graded on the
Epic 9 identity metric: **seconds to re-orient after stepping away**.

**Acceptance criteria (the gap list, owner-acknowledged 07-15):**
1. **Right rail** — Battles, Projected Podium, RACE CONTROL list, DUE TO PIT,
   Race-at-a-Glance, migrated from the calm board ("keepers — migrated as-is",
   ▲/▼ glyph rule from 07-04 in force). The header RC ticker (Epic 9 ⑤) is a
   stopgap, not the rail.
2. **WYWA catch-up card** — collapsed story card first (stat header + per-class
   event budget). ⚠ Sequencing dependency from the phase-1 doc: the ranked
   expand/timeline has no evaluator and **cannot be signed off before the next
   live event** — build collapsed card, defer the expand.
3. **Notes column** — "1 stop in hand" / "undercut live" strategy notes in the
   row's first read, per the Option C mockup.
4. **Panel completeness** — add leader-remaining comparison and per-stop cost
   estimates (~Ns ± band) to the tap-to-explain panel, per the Option B/C sketch.
5. **Trust plumbing (carried from Epic 9 known-nexts):** stale-data guard (grey
   NET when `net_analysis.updated_at` ages while standings stay fresh); replay
   header clock fix (`start_time_s` capture-clock gotcha).
6. **Exit test:** run the web board alongside PyQt6 at the next live event.
   PyQt6 remains the race-day display until the web board passes that parity
   trial — no earlier switch, whatever it looks like.

Non-goals: broadcast-video work (still tiebreaker-parked), multi-user anything,
dense-timing-table port (stays a PyQt6 escape hatch until proven needed).

## Research items (unscheduled)

- ✅ **WEC driver-change detection from the Griiip feed (shipped 2026-07-13, PR #16).**
  `resolve_current_driver()` maps `currentDriverId` → `drivers[].externalDriverID`
  (top-level `displayName` is the TEAM name on real WEC data — trap avoided);
  `_handle_participants` now calls `record_driver` every frame. Verified on the SP
  race capture: 113 driver_changes rows, all 35 cars, plausible stint spacing.
  Fitted DC delta ~5.4s (see 07-14 decision entry). Live verification + delta
  re-fit at the next WEC event.

- ✅ **Driver-change / min-drive-time rules (resolved 07-04, source: owner).**
  `wec_imsa_2026_regulations.md` (owner's research) → codified in
  `data/regulations_2026.json` (both series, all event durations, plus the universal
  4h-per-rolling-6h max). **Analysis: the `(lineup_size − 1)` heuristic is
  count-correct for WEC 6H** — every legal lineup (2/3-driver Hypercar, mandatory
  3-driver LMGT3) requires exactly lineup−1 changes under the min/max constraints, so
  no code change needed for São Paulo. **Future feature (unscoped, post-race-week):**
  time-based obligations from the JSON — "change due soon" when the current driver
  approaches the 4h cap or remaining mins can't otherwise be met; needs per-driver
  seat-time accounting from `driver_changes` timestamps. Would upgrade
  `_driver_obligation` (calculator.py:612) from count-based to time-based.
- **WEC VET column:** hypothesis = Virtual Energy Tank % for Hypercar. Confirm from first
  São Paulo capture; if correct, scope as Epic 2 sibling for WEC.
- **"+1 lap" flicker** — cosmetic; hysteresis fix idea documented in session notes.
- **F1OpenViewer steal-audit** — promoted into Epic 9 phase 2 (2026-07-04).

## Parked north star (no work — direction only)

- **Broadcast video linkage:** connect the app to the livestream and pull video clips of
  notable overtakes/incidents, feeding the "while you were away" card. "final
  boss" idea — everything WYWA-related should avoid foreclosing it. Prior art:
  F1OpenViewer's MIT sync engine anchors video playback to live timing (see the
  steal-audit spike under Research items).

## Held-out / regression sets

- **IMSA (7 complete archives, the default gate):** Daytona 24h, Petit Le Mans 10h,
  Indy 6h, Monterey, Long Beach, Detroit, Chevy Grand Prix 2026 (added 07-13, PR #15;
  no GTP that weekend — LMP2/GTD PRO/GTD only, and the set's best single-race result:
  proj 1.36) — paths in `validate_races.py` under `ARCHIVE_DIR`.
- **WEC (8 complete archives, `--wec` flag):** SP 2024, SP 2025, COTA 2025, Fuji 2025,
  Bahrain 2025, Imola 2026, Qatar 2025, SP 2026 (T71 archive, added 07-13, PR #15) —
  paths in `validate_races.py` WEC_RACES under `wec-archives/`. Le Mans 24h permanently
  excluded. The SP 2026 Griiip live capture is ALSO in the set (WEC_CAPTURES, replayed
  via `--replay-predict`); it scores worse than the T71 archive of the same race
  (proj 3.16 vs 2.93) — feed-quality gap noted, unexplored.
  **WEC baseline (re-run 2026-07-06 post Tier-3 fixes — supersedes the 07-05 numbers,
  which predate the determinism + penalty-expiry fixes):** NET MAE 2.96–4.60,
  TRK 2.01–4.20, CATCH% 84–100% (COTA is the one net-beats-track race, +11%).
- Only complete run-to-chequered archives count; verify span + final flag before adding.

## Standing gates

- Any calculator/replay/evaluator change → `./check.sh --full` (tests + IMSA suite).
- WEC-specific changes → also run `python src/validate_races.py --wec`.
- UI changes → offscreen render + `replay.py --stream` feel-test.
- Finish-predictor changes → validated across ALL races, never a single archive.
