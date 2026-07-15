# User Guide — Overcut Race Strategy Dashboard

A field guide for race fans. You don't need to read the code — just this.

---

## What it does

Overcut answers the question TV timing can't: **who is really winning once everyone's remaining pit stops are accounted for?**

Standard timing shows where each car physically is on track right now. But in endurance racing, that's misleading — a car running P4 might actually be P2 once the two cars ahead of it have served their outstanding stops. Overcut adjusts for that, live, every few seconds.

Two ideas drive the app:

- **NET position** — effective standing adjusted for stops still owed by each car.
- **Catch-up card** — when you step away and come back, a brief tells you exactly what changed while you were gone so you don't have to re-read the whole board.

---

## Launching the app

### First time
Run `./setup.sh` from the project folder. It creates the Python environment and launches the dashboard automatically.

### After that
```bash
./venv/bin/python src/dashboard_calm.py
```
Or double-click **Overcut.app** in the project folder (Mac only; only works at the original path).

### The web board (new — preview)
There's also a browser-style version of the board with a couple of extras:
**click any car** to open a side panel explaining its net position (the real gap,
what its remaining stops cost, any penalty it's carrying, and its full pit-stop
history), plus a NET column on every row.

It needs a one-time extra setup step — see [ui/README.md](ui/README.md) for the
recipe. For race day, stick with the PyQt6 dashboard above: it has survived full
live races; the web board hasn't yet.

---

## Picking a session

When the dashboard opens, click the **Session** button in the top-left header to open the session picker.

**IMSA Live** — connects to the Al Kamel live timing feed during an active IMSA race weekend. Nothing to configure; the feed is public.

**IMSA Replay** — loads a Timing71 `.zip` archive and streams it at 60× speed so you can watch a past race as if it were live. Use this to explore the dashboard between race weekends.

**WEC Live** — connects to the Griiip/FIA WEC feed. Proven in the São Paulo 6-hour race (2026-07-12); net-ordering bugs found in that race were fixed 2026-07-13.

**Sample archives** (included in `sample-archives/`): Long Beach 2026 and Detroit 2026 are bundled so you can try the replay without downloading anything. The session picker's "replay" path will find them automatically when pointed at that folder.

---

## The main board

The board lists every car, grouped by class (GTP · LMP2 · GTDPRO · GTD for IMSA; Hypercar · LMGT3 for WEC). A coloured bar on the left edge of each row shows the class at a glance.

Each row has these columns, left to right:

### Track position (big left number)
Where the car physically is on track right now, within its class. The **class leader** is highlighted green. Cars currently stopped in the pits are dimmed.

### NET overlay (▲ / ▼ arrow, to the right of track position)
The effective standing once the pit-stop cycle resolves. Only shown when the class is out of sequence on stops — before anyone has pitted, every car shares one plan and showing net would be noise.

- **▲P3** (green) — this car will gain to P3 when stops cycle out. It has fewer stops remaining than the cars ahead of it.
- **▼P6** (red) — this car will drop to P6. It still owes a stop that others ahead of it have already served.
- **—** (faint) — settled: net has converged to track order for this car, or there isn't enough data to say anything reliable.

The net overlay is intentionally conservative. It only appears when the app has a trustworthy gap measurement and the class is genuinely out of sequence. **Silence means calm, not broken.**

### Car number
The car number. Dimmed when the car is currently in the pits.

### Team / Driver name
Team name and current driver. Driver is shown as "F. Lastname" format.

### STOPS
How many pit stops this car has completed. Faint when zero (they haven't stopped yet).

### Pit status (IN PIT / OUT)
- **● IN PIT** (blue) — the car is physically stopped in the box right now.
- **OUT** (green) — the car is on its out lap, just exiting the pits.
- Blank — the car is racing normally.

### GAP
Time gap to the class leader on track (in seconds). Shows **LEAD** for the class leader, **+1L** if the car is a full lap down.

### CALL
The most critical car-specific alert. Only two things appear here:
- **Penalty** (amber) — the car has a time penalty to serve.
- **DQ — to back** (amber) — the car has been disqualified.

Undercuts and catch calls live in the right rail, not here — keeping the board calm.

---

## The right rail

The right column shows field-wide strategic information, updated live.

### RACE AT A GLANCE
Actionable calls across the field: penalties being issued, undercut/overcut opportunities, cars catching rivals. These are things worth knowing even if you're not watching a specific car.

### RACE CONTROL
Official messages from the race director — caution calls, safety car deployments, incident notes. The same messages appear on the TV broadcast ticker.

### DUE TO PIT
Cars approaching their fuel window. When a car appears here, it needs to pit within the next few laps. The list updates as the race progresses and cars burn through their stint.

### BATTLES
Close in-class fights. Shows "catching" with a rate (seconds per lap) when a gap is genuinely and consistently shrinking. Conservative: requires a confirmed trend across several laps before it appears. **Silence means the battle isn't real yet.**

---

## Row glow (the "breath")

A row briefly glows (~2.4 seconds) when something changes:

- **Blue glow** — the NET position just changed for this car (it moved up or down in the effective order).
- **Amber glow** — a penalty just landed on this car.

The glow is a motion cue, not an alarm. It's designed to be noticeable at the edge of your vision without demanding your attention.

---

## The "while you were away" catch-up card

This is the main feature for casual watching.

**Automatically:** when you switch away from the app and come back, a card appears listing what changed while the window wasn't in focus — class-lead changes, penalties, retirements, real position moves, pit stops. The most significant events appear first. Press any key or click the card to dismiss it.

If there are more events than fit on the card, a **"show N smaller moves"** link appears at the bottom. Click it to expand.

**Manually:** press **M** once to mark the current moment. Press **M** again (or click away and return) to see the brief since the mark. Useful when you're watching but want to mark a point in time ("I'm going to the kitchen — mark this").

After dismissing the card, a **faint inline badge** (e.g., "▲ moved" or "pitted") lingers on each car row for about 30 seconds, so the board itself still shows what moved at a glance.

---

## The legend card

Press **?** (or click the **?** button in the header) to open an on-screen key explaining every symbol and colour. Press any key or click to dismiss.

---

## Confidence and accuracy

**What to trust:**
- NET position is most reliable **early to mid-race** when there are stops left to cycle. Late in the race it converges to track order by design.
- DUE TO PIT windows are approximate (±5 laps for IMSA; stop duration model learns from actual observed stops).
- **Catch** calls are deliberately conservative — the app requires a consistent closing trend over multiple laps before saying anything. If you don't see a catch call, it's probably not happening.

**What to treat as a rough guide:**
- Exact catch ETAs (in laps). The closing rate varies with traffic, caution periods, and driver changes.
- NET position for cars that are very close together or running unusual fuel strategies.

**Known accuracy limits (from the 6-race test suite):**
- Finish-position predictions: mean error of about 2.5 in-class spots — currently on par with just watching track order. NET adds a modest edge on deep-pit-cycle races; it's not a crystal ball.
- Stop-duration estimates: accurate to tens of seconds (enough for window math, not exact ETA).

The README has full numbers. The app is honest about uncertainty — it would rather show "—" than show a confident-looking number on noisy data.

---

## Getting updates

```bash
git pull
./venv/bin/python src/dashboard_calm.py
```
