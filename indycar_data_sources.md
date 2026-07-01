# IndyCar Data Sources — Dashboard Integration Spec

Context: extending the existing Net Position / VFT / Catch-and-Pass dashboard (currently built
around Timing71 for endurance racing) to also support IndyCar sessions, live or replay.
IndyCar does NOT use the Timing71-style aggregator — it needs its own ingestion path.

## 1. Primary data source: official IndyCar leaderboard site

- URLs: https://racecontrol.indycar.com/ and https://leaderboard.indycar.com/
- Underlying protocol: MyLaps RIS Server (replaced the older Multiloop Timing protocol).
  This is the same feed IndyCar teams and broadcast timing vendors (e.g. HH Timing) connect to
  directly, but that connection requires a licensed championship configuration — not something
  we can subscribe to freely.
- **What we're actually going to do:** treat the public leaderboard website like any other
  frontend backed by a polling JSON/websocket endpoint, and reverse-engineer it the same way
  the NASCAR/IndyCar scraper at github.com/jemorriso/nascar does:
  1. Open racecontrol.indycar.com (or leaderboard.indycar.com) during a live or replay session.
  2. DevTools → Network → filter XHR/Fetch (and WS if present).
  3. Identify the polling endpoint(s) feeding the scoreboard — expect JSON, likely re-issued
     per session with a session/event ID or token in the URL or headers.
  4. Confirm whether it's plain HTTP polling (simplest — just requests.get on a timer) or a
     websocket (would need a client similar to what we built for Timing71's WS feed).
  5. Capture one full session's worth of raw payloads to a local file first, so we can design
     the parser against real data before wiring it into the live pipeline.
- Fields to look for in the payload: position, car #, driver, gap/interval, last lap, best lap,
  lap count, pit status, and — per HH Timing's IndyCar docs — a `StintTyres`-style field for
  tire compound, since compound data rides along in the same feed rather than a separate source.
- Fallback/reference for field semantics: HH Timing's IndyCar page documents the RIS-derived
  columns in detail (No-Tow position/best, min gap, stint tyres, oval qualifying variants):
  https://help.hhtiming.com/series-specific-info/indycar/

## 2. Tire & strategy data

- No standalone tire API exists. Compound and stint length come from the same leaderboard feed
  (see StintTyres-equivalent field above).
- Firestone compound allocation per event is announced via press release, not an API — if we
  want per-weekend "this is the alternate/primary compound" context, that has to be a manually
  maintained lookup table per race, updated from IndyCar/Firestone announcements.
- Push-to-pass usage is not a labeled field — IndyCar itself determines P2P usage by inference
  from ECU/timing/telemetry data, not a dedicated flag. If we want to surface P2P usage, it'll
  have to be inferred client-side from unexplained speed/lap-time deltas, which is a stretch
  goal, not v1.

## 3. Telemetry — set expectations correctly

- True car telemetry (throttle, brake, steering, RPM) is **not publicly available**. It stays
  with the teams and only reaches the public as broadcast HUD overlays (FOX/SMT), not a
  downloadable feed.
- The INDYCAR App (powered by NTT DATA, package `com.vzw.indycar`) advertises "live car
  telemetry," but this is almost certainly enriched timing/position data (speed, gaps), not raw
  ECU channels — no public writeup or reverse-engineered API for this app exists as of this
  research. If we want to explore it further later: mitmproxy/Charles on the phone's traffic
  while the app runs a session, watch for cert pinning (likely blocker). Deprioritized vs. the
  website scrape — the website is a much easier, unprotected target and likely has everything
  we need for v1.
- **Decision: do not build a telemetry engine for IndyCar.** Net Position / Catch-and-Pass
  calculations will run on timing data only (lap times, gaps, sector splits if available),
  same as the endurance racing build.

## 4. Historical / replay backfill data (for testing before pointing at live)

- IndyCarPy — Python package, scrapes official session results (practice/qualifying/race) back
  to 1996: https://github.com/TMCabrera/indycarpy
  `pip install` isn't published on PyPI as far as we found — clone and use locally.
  ```python
  import indycarpy
  indycarpy.get_sessions_records(from_year=1996, to_year=2024, session_type="R", data_format="df")
  ```
- Racing-Reference.info — deeper historical archive (also covers NASCAR/ARCA), scrapable
  directly or via existing Apify actors if we want a no-code pull for one-off backfills.
- Use these to build/validate the Net Position and Catch-and-Pass engines against known race
  results before wiring up the live scrape.

## 5. Paid/commercial fallback (not needed for v1, noting for completeness)

- Sportradar IndyCar API — schedules, post-race results, stage-based structure, trial tier
  available: https://developer.sportradar.com/racing/reference/indycar-overview
  Would only be worth it if the free leaderboard scrape turns out to be unreliable or
  session-gated in a way we can't work around.

## Recommended build order for Claude Code

1. Manual capture session: hit racecontrol.indycar.com during a live or archived session,
   pull the raw JSON/WS payloads, save to `sample_data/indycar_raw/`.
2. Write a parser that normalizes the payload into the same internal schema the Timing71
   ingestion path already produces (car, position, gap, lap, tire compound if present),
   so the existing Net Position / VFT / Catch-and-Pass engines don't need to change.
3. Build the poller/websocket client for live use, modeled on whatever protocol step 1 reveals.
4. Add the Firestone compound-per-event lookup table as a small static config, manually updated
   per race weekend.
5. Explicitly skip telemetry ingestion for IndyCar — not available, don't build a placeholder
   that implies it's coming.
