# WEC Calibration — Phase C Findings (checkpoint for review)

Date: 2026-07-12 · Branch `feature/wec-calibration` · Data: SP 2026 race capture
replayed via `--replay-predict` (155,613 frames, 0 dispatch errors; reproduces the
live scorecard: stop MAE 19.2s identical, net 5.24 vs live 5.23) + all 13
Timing71 archives re-scored with all-rows AND late-race views.

## Headline: each metric failure now has a mechanism

### 1. Net-vs-finish "loses to track" — three findings, one big caveat

**a. The hypothesis "net wins late" is FALSE — everywhere.** Across all 14
races, in every view (all rows / last 25% / stops_left ≤ 2), current track
position beats raw net position in 13 of 14 (Lone Star the only exception).
Late-race views make track look *better*, not worse. No goalpost survives.

**b. The headline metric scores a column the product doesn't ship.** The
dashboard's finish forecast is `projected_finish` (the track-anchored blend,
calculator.py:983), not raw `net_position`. Scored on the same data,
`projected_finish` beats raw net in all 14 races and beats BOTH net and track
on the SP live capture (4.70 vs 5.24/5.00 all-rows; 4.14 late-race). The
product's actual forecast already outperforms naive track position on the
target race. Raw net's job is "true current standing through the pit cycle",
not finish prediction — the evaluator conflates the two.

**c. `est_stops_left` has a systematic +1 bias** (mode of signed error = +1 in
50% of 13,230 rows; early race +1.7 to +2.1). Every phantom stop injects
~80s into that car's net gap. This is the concrete mechanism inflating raw
net's error — prime fix target, and it also skews the blend weight
`w = 0.15·stops_left`.

*Caveat:* SP 2026 was an extreme-churn race — mean |pos@25% − final| = 5.03,
so ~5.0 is the intrinsic error floor there (2× any archive). Absolute MAEs
across races aren't comparable; only net-vs-track-vs-proj deltas are.

*(Fixed during diagnosis: a replay-clock bug was intermittently zeroing
elapsed-time and exploding est_stops_left for single cycles — commit b8c3927.
Also fixed: replayed pit durations were wall-clock garbage — commit 16a014a.
Both were replayer bugs, not live bugs.)*

### 2. Stop-time MAE 19.2s decomposes into two different problems

| slice | n | bias | MAE |
|---|---|---|---|
| normal stints (>30 laps) | 144 | **+6 to +9s over** | ~11.4s |
| short stints (≤20 laps) | 15 | −34s | **93.8s** |

- The 15 short-stint stops are unpredictable events (one 991s repair, several
  ~25s splash/penalty stops) — no model sees those coming. They add ~8s to the
  headline MAE. The *predictable* stop error is ~11.4s with a consistent
  +6–9s over-bias.
- **Mechanism for the over-bias: driver-change accounting is broken for WEC.**
  `driver_changes` has 0 rows (wec_live never populates it), so
  `_driver_obligation` marks every car as owing a DC on all 13,230 prediction
  rows → +12s `DRIVER_CHANGE_DELTA_MS` is added to *every* predicted stop.
  Worse, with 0 DC labels the fuel fit trains on all stops *including* real
  DC stops, so DC time is double-counted. Remove the blanket +12s and the
  normal-stop bias lands near zero.
- Caution-awareness (planned D2): SP has n=1 caution stop — cannot be fitted
  or validated on SP. The archives show it matters (Qatar bias −58s, Imola
  −24s, Daytona −15s), so D2 proceeds on archive data as planned.

### 3. Catch predictions: 0 of 14 were real

Every evaluator "hit" registers at lap 242 — the final classification — i.e.
the target eventually finished behind, never an on-track pass near the
predicted horizon (0.5–8 laps). Realized 10-lap pace deltas at prediction
time: noise (±300ms) or pit-window artifacts (+8–10s/lap = target's in-lap).
Two distinct defects:
- **Model:** the catching gate (nose-to-tail + closing trend) fires on noise
  in multiclass WEC; the predicted pace advantage doesn't exist on clean laps.
- **Metric:** `eval_catch` credits end-of-race coincidences as hits, so the
  reported "47% hit rate" was already generous. A hit should require the pass
  within a bounded horizon (e.g. 2× predicted laps).

## Proposed Phase D (revised, smallest-risk first)

| # | fix | expected effect | risk |
|---|---|---|---|
| D1 | promote finish-blend literals to config (zero-diff) | none (refactor) | none |
| D2 | wire WEC driver-change detection in wec_live (feed carries driver names in participants channel) + don't double-count DC in the fuel fit; fallback: suppress DC delta when obligation is unknowable | stop bias +6–9s → ~0; MAE ~11.4 → ~9s | low |
| D3 | `_stops_left` +1-bias fix (drivers pit before fuel-exhaustion; `ceil` rounds the deficit up) — cross-validated on all 7 WEC + 6 IMSA archives | net MAE down ~0.2–0.5 where stops remain; better blend weights | medium |
| D4 | catch gating: require the pace delta to hold on clean laps (widen PACE_WINDOW / traffic filter), WEC-scoped via SERIES_OVERRIDES | fewer, real calls | low |
| D5 | evaluator honesty fixes (flagging separately — these change the scorecard, not the model): score `projected_finish` as the headline finish metric alongside net; bound catch hits to a horizon; report stop MAE with the unpredictable ≤20-lap-stint stops split out | reporting only | — |

Not proposed: chasing SP's absolute MAE ~5 (intrinsic churn floor), broad
constant re-tuning of CATCH_CLOSING_EFFICIENCY on SP alone (n=14 catches).
