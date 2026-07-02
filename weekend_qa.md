# Weekend QA — IndyCar race replay + F1 British GP live (2026-07-04/05)

Two independent things to test this weekend. Both have logging built in already —
no manual note-taking needed, just run the commands and look at what they produce.

> **Picker note:** the F1 session picker now shows **Race/Sprint only**. To use Friday
> practice for endpoint validation, set `"DEV_SHOW_ALL_SESSIONS": true` in `config.json`
> and reopen the picker (flip back to `false` after the weekend).

## A. IndyCar race replay (primary — new work this session)

1. After the race finishes, download the Timing71 **Race** archive (same source as the
   Road America one already in the repo root) and drop it in the repo root or anywhere handy.

2. **Metrics pass** — one-line stop/net/catch accuracy table, no dashboard needed:
   ```bash
   venv/bin/python src/validate_races.py "<archive>.zip"
   ```
   This is `validate_races.py`'s normal IMSA regression tool, run ad-hoc against one archive
   (it's series-agnostic — takes any Timing71 zip as a CLI arg).

3. **Feel test** — stream it through the real dashboard:
   ```bash
   venv/bin/python src/replay.py "<archive>.zip" --stream --speed 60 --db data/race.db
   ```
   Then open the dashboard (`venv/bin/python src/dashboard_calm.py` or double-click
   `IMSA Strategy.app`) — or use the new picker: **Session ▾ → IndyCar → Replay → choose
   the archive**. Watch a full green → pit → caution → restart cycle.

   Stream mode logs predictions automatically and runs the evaluator at the end — no extra
   step needed. Report lands at `logs/stream_<event>_<timestamp>.txt`.

4. **What to watch for on the dashboard:**
   - Single-class header collapse (no class dividers, like F1)
   - Driver identity + team colors in the identity slot (not car numbers)
   - Tyre chips: `P` (Primary) and `O` (Alternate/red) with age numbers
   - DUE TO PIT firing sensibly (no telemetry — inferred from stint length only)
   - Net position tracking sanely through pit cycles (not jumping erratically)
   - Caution periods handled (flag color, pit stop bunching)
   - Checkered flag / FINISHED state at the end

5. If anything looks wrong, the raw archive + `logs/stream_*.txt` + the temp DB
   (`data/race.db`) are enough for me to debug without re-running the race.

## B. F1 British GP live (Phase 3 step 6 — the long-pending live validation)

1. **Auth check** (token expires 2026-07-05 — re-auth if needed):
   ```bash
   venv/bin/python -m fastf1 auth --status f1tv
   # if expired:
   venv/bin/python -m fastf1 auth --authenticate f1tv
   ```

2. **Protocol sanity check** (no auth needed, run anytime before the session):
   ```bash
   venv/bin/python src/f1_live.py --discover --no-auth
   ```
   30-second dump of the raw feed topics — confirms the connection/subscribe handshake
   still works before committing to a full session.

3. **Full live run** during an actual session (FP1/Q/Race):
   ```bash
   venv/bin/python src/f1_live.py --db data/f1_live.db
   ```
   Then open the dashboard and use **Session ▾ → F1 → Live → Launch Live Feed** (or launch
   it yourself and just open the dashboard — it auto-discovers the live F1 session).

4. This is a **feed-correctness** test — does it lock on, decode data right, survive a
   reconnect — not a prediction-accuracy test (F1 v1's net position = track position,
   no fuel model). Logging is automatic to `logs/f1live_<timestamp>.log`.

5. Watch for: positions/gaps updating live, tyre compound + age showing, pit stops
   detected, flag changes (yellow/SC/VSC) reflected, no crashes/silent stalls over
   a full session.

---

## Do we need more IndyCar archives?

**Yes — to harden the predictions, not to make replay work.** One archive (Road America) is
enough for the engine to predict *that* race — the pit-cost model learns within a single race
from its own stop durations. But tuning the model well needs a **diverse set across track
types**, because IndyCar's pit/fuel/caution dynamics differ sharply by circuit type:

- Current set: 1 road course.
- Worth adding: at least one **oval** (very different fuel windows and caution frequency)
  and one **street circuit** (Detroit/Long Beach/Toronto), plus more road courses as the
  season goes.

As archives accumulate, run them together:
```bash
venv/bin/python src/validate_races.py race1.zip race2.zip race3.zip
```
to see whether the IMSA-tuned config knobs (`STOP_OUTLIER_MAD`, `CAUTION_PENALTY_FACTOR`,
`DEFAULT_STINT_LAPS`) generalize to IndyCar or need their own values. Once there are a few,
it's worth adding an `INDYCAR_RACES` list to `validate_races.py` (mirroring the existing
IMSA `RACES` list) so this becomes a one-command regression check like the IMSA suite already is.

**Action:** grab the archive after each of the next few IndyCar rounds (prioritizing track-type
diversity over just accumulating more road courses) and drop them somewhere handy — we'll build
the regression set next session.
