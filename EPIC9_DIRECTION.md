# Epic 9 direction — Fable's opinion (2026-07-06)

> Snapshot as of 2026-07-06 — current status: BACKLOG.md. Phase 1 closed 2026-07-14
> (decisions log); note the net-cluster recommendation evolved from "Option B" here to
> fact-forward ("Option C") in the phase-1 sign-off.

Written during the Tier-3 exit review, pulled forward so this input exists before
the model handoff. **This is a sounding-board document, not a decision.** The UI
freeze holds; nothing here is committed work. Each section gives options, a
recommendation, the reasoning, and what would change my mind. Decide after São
Paulo race week, with the live-race UX notes that weekend will generate.

---

## Phase 1 — what is this product?

### 1a. One screen or several?

- **Option A — one screen (calm board), everything else is a rail/card/popover on it.**
- **Option B — multi-view app** (calm board + dense timing + replay browser + eval reports).

**Recommendation: A.** The North Star is "catch up at a glance after stepping
away" — a glance has one destination. The dense timing table already exists as an
escape hatch (`Timing ↗` opens a separate window) and that's the right shape for
it: reachable, not resident. The eval/replay loop is a *builder's* tool, not a
race-day surface — it lives in the terminal and the logs, and forcing it into the
app would be UI for an audience of one-who-already-has-a-terminal-open.

*Changes my mind:* if post-race replay study becomes a weekly habit rather than a
tuning activity, a replay-browser view earns a slot.

### 1b. Race-day tool vs between-races study tool?

Already decided (07-04 decisions log): race-day-only; practice/quali are
data-validation only. Replay `--stream` already gives the study mode through the
SAME screen. No new surface needed. **Nothing in this spike should reopen this.**

### 1c. Does the broadcast-video "final boss" make the cut as a direction-setter?

- **Option A — treat it as decisive:** pick the front-end stack that can embed and
  sync video, because everything WYWA-related eventually feeds it.
- **Option B — treat it as parked:** decide the stack on today's needs only.

**Recommendation: A, as a tiebreaker only.** Don't build anything for video now.
But when two stacks are otherwise close, pick the one that doesn't foreclose it —
and that's web tech: F1OpenViewer's MIT sync engine (video anchored to live
timing) is React/Electron, `<video>` embedding is trivial in a browser and
painful in PyQt6. The final boss is exactly the kind of idea that dies if the
platform makes it a research project.

### 1d. Identity check

"Endurance race strategy companion with honest confidence signaling" survived
the Gemini pressure-test and a week of WEC work unchanged. The one sharpening
worth writing down: the product's edge is not prediction accuracy (net beats
track only mid-race, and only sometimes — the suites are honest about this), it
is **comprehension speed**: gaps, cycles, and threats assembled into one calm
view faster than a broadcast + timing page can. Confidence signaling is the
feature, not the caveat. Every phase-2 design choice should be graded on
"seconds to re-orient after stepping away."

---

## Phase 2 — front-end direction

### The decision: keep polishing PyQt6 vs move the display layer to web tech

**Recommendation: move the display layer to web tech, after race week, incrementally.**
Engine (scrapers → SQLite → calculator) untouched — Tier 2 confirmed the
boundary is clean and "the code is ready for either decision."

Honest trade-offs:

| | PyQt6 (keep) | Web (move) |
|---|---|---|
| Sunk cost | 2,500 lines of working dashboards | Rewrite of the display layer only — engine/tests untouched |
| The looks you actually like | None of them (Timing71, F1OpenViewer, MultiViewer are all web) | All of them; F1OpenViewer's Tailwind theme is MIT-stealable |
| Your gripe | "Hate the look, can't articulate why" — two sessions of knob-turning haven't fixed it | Different design vocabulary entirely; the gripe may simply be "it looks like a Qt app" |
| Video final boss | Effectively forecloses it | Native |
| AI-assisted development (how this project is built) | Qt polish is a thin corpus; every iteration is slow | React/Tailwind is the densest corpus there is; fastest iteration loop available |
| Risk | None new | Two runtimes to launch, packaging, a parity gap while migrating |
| Race week | — | **Do not start before 07-13.** |

The deciding argument isn't aesthetics — it's the last row of strengths: this
project is built by AI pair-work, and the web stack is where that workflow is
strongest. Combined with "every reference design you admire is web," PyQt6
polish is swimming upstream on both fronts.

### Migration shape (if accepted)

1. **First commit (per Tier-2 §4):** extract `Poller`/`_build_rows` from
   `dashboard.py` into a display-agnostic module. This pays off even if the
   answer is "stay PyQt6."
2. Thin local server: engine process exposes the analyse() output as JSON over a
   websocket (or the web page polls SQLite via a ~100-line FastAPI shim). No
   cloud, no auth — localhost only, same trust model as today.
3. Build the web calm board to **parity** against `replay.py --stream` as the
   harness; the PyQt board stays the race-day tool until parity is signed off.
   Parity checklist = calm board rows, NET overlay, rails (battles / podium /
   RC / pit), WYWA card.
4. Only then: the redesigns below, on the new canvas.

*Changes my mind:* if the parity build stalls past ~2-3 weeks of sessions, stop
and re-evaluate — a half-migrated display layer is worse than either endpoint.

---

## Redesign sketches (phase-2 raw material, either stack)

### Net cluster (the biggest North-Star hit, per 07-04 session)

Today's `±18 ▲ P7` cluster fails both comprehension (what is being measured?)
and legibility (unlabeled, cramped). Two candidate shapes:

- **Option A — labeled inline:** `NET P5 ▲2 ±1` in one cell. Compact, minimal
  layout change, still requires learning what NET means.
- **Option B — split columns:** `NOW | NET | Δ` as three narrow labeled columns,
  net column dimmed when `net_settled`, band shown only on hover/tap.

**Recommendation: B + the explainability panel.** Columns read at a glance
(that's the whole product thesis); labels amortize instantly; and the Gemini
review's explainability idea — tap a car → "P7 on track, P5 net: 2 stops left
(~72s ± 9s), leader has 1 (~36s), no penalty" — turns the number from a claim
into an argument. Trust follows inspectability.

### WYWA "tell the story" (30+ min absences)

Keep the 07-04 principle: **retrospective narration only, never net
projections.** Shape that fits the captured scope inputs:

```
WHILE YOU WERE AWAY — 42 min · 31 laps · 1 caution (L88–L93) · 2 lead changes
HYPERCAR   #8 leads (was #50, passed L94) · #6 +2 → P3 · #12 drive-thru served
LMGT3      #92 leads (unchanged) · #27 undercut brewing from P6 (2 stops fresh)
[expand for full timeline]
```

Header stat line answers "how much happened"; per-class lines cover the top-5
plus below-top-5 threats; expansion gives the ranked full list. Needs the
per-class event budget (`MAX_EVENTS` is field-wide today — flagged in Epic 7
notes). Mockup-level only; owner explicitly wants a design discussion first.

### Keepers (do not redesign)

Battles rail, Projected Podium rail, notes-column copy — validated 07-04.
Migrate them visually as-is; wording refinement pass only.

---

## Bottom line

| Question | Opinion |
|---|---|
| One screen or several? | One (calm board); dense table stays an escape hatch |
| Product identity | Comprehension speed with honest confidence — not prediction accuracy |
| PyQt6 or web? | Web, incrementally, post-race-week; Poller extraction first either way |
| Video final boss | Tiebreaker for web, zero work now |
| First redesigns on the new canvas | Net cluster (split columns + explainability panel), then WYWA story mode |
| What NOT to do | Start any of this before São Paulo is done |
