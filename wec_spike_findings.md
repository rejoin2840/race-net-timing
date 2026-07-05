# WEC Spike Findings

## 1. Provider

**Verdict:** WEC timing data is provided by Al Kamel Systems, but the public live timing frontend is **not** the same public Al Kamel Meteor/DDP site used by IMSA.

**Verified:**
- Porsche's official WEC live timing page states: **"Data powered by AL KAMEL SYSTEMS S.L."**
  https://racing.porsche.com/en-TH/wec/live-timing

- FIA WEC's official "Live" page directs users to the championship's live timing experience.
  https://www.fiawec.com/en/page/live-fiawec

- The public live timing site used during WEC events is:
  https://livetiming.fiawec.com

- FIA WEC publishes official timing documents and results from an Al Kamel-hosted domain (`fiawec.alkamelsystems.com`), confirming Al Kamel remains the official timing contractor.
  https://fiawec.alkamelsystems.com/Results_NoticeBoard/

- The Timing71 developer states:

  > "Timing for the WEC and Le Mans is provided by Al Kamel Systems... LMEM/ACO choose not to use AKS' online live timing... This year, they've partnered with Griiip... via the livetiming.fiawec.com domain."

  https://www.reddit.com/r/wec/comments/1tvry8s/is_there_any_publiclive_timing_api_for_le_mans/

**Inferred/Unknown:**
- The public frontend at `livetiming.fiawec.com` appears to be separate from Al Kamel's public Meteor/DDP frontend.
- UNKNOWN whether the backend data source is still directly Al Kamel or exposed through another middleware layer.

---

## 2. Feed name

**Verdict:** UNKNOWN.

**Verified:**
- I found no verified source showing a WEC DDP subscription such as:

  ```javascript
  livetimingFeed("wec")
  ```

- Timing71 documentation describes its own internal architecture rather than Al Kamel feed identifiers.

  https://info.timing71.org/reference/network_architecture.html

- The Timing71 developer states that the **current** FIA WEC live timing uses:

  > "a SignalR-based system using msgpack"

  rather than describing any Meteor/DDP endpoint.

  https://www.reddit.com/r/wec/comments/1tvry8s/is_there_any_publiclive_timing_api_for_le_mans/

**Inferred/Unknown:**
- UNKNOWN whether an internal Al Kamel feed named `"wec"` exists.
- Current evidence suggests a Meteor `livetimingFeed("...")` subscription is **probably not** used by the public WEC frontend, but this has not been independently verified.

---

## 3. Timing71 WEC archives

**Verdict:** Timing71 supports replay recordings. I could not verify public downloadable ZIP archives specifically for WEC sessions.

**Verified:**
- Timing71 documents a DVR recording system.
  https://info.timing71.org/reference/network_architecture.html

- Timing71 exposes replay-related RPCs.
  https://info.timing71.org/reference/constants.html

- Timing71 Desktop supports offline replay loading.
  https://info.timing71.org/2020/06/11/announcing-desktop-client.html

**Inferred/Unknown:**
- UNKNOWN whether public downloadable WEC replay ZIPs currently exist.
- UNKNOWN how users obtain WEC replay recordings today.

---

## 4. WEC data fields

**Verdict:** Strong evidence exists for class, driver, gap, interval, and pit-stop count. No verified evidence was found for public Hypercar energy telemetry.

**Verified:**
Official/public WEC timing exposes fields including:

- Class
- Driver
- Interval
- Gap
- Pit count
- Last lap
- Best lap
- Sector times
- Position

https://racing.porsche.com/en-TH/wec/live-timing

Recent live URLs also expose configurable timing columns, for example:

```
PitCountAndLap
Interval
Gap
Sector1
Sector2
Sector3
LastLap
BestLap
VET
GainedLost
```

Example:

https://livetiming.fiawec.com/session/18130/live/timing?columns=Sector1,Sector2,Sector3,LastLap,BestLap,Interval,Gap,PitCountAndLap,VET,GainedLost

Timing71's manifest also includes:

- Class
- Driver
- Gap
- Interval
- Pits

https://info.timing71.org/reference/manifest.html

**Inferred/Unknown:**
- UNKNOWN what `VET` represents.
- UNKNOWN whether Hypercar virtual energy percentage is available anywhere in the public API.
- No verified evidence was found for battery/fuel/energy telemetry.

---

## 5. 2026 WEC calendar

**Verdict:** Verified.

**Verified:**

Next race after July 2, 2026:

- 12 July 2026 — Rolex 6 Hours of São Paulo — Interlagos

Remaining 2026 rounds:

- 12 Jul — São Paulo
- 6 Sep — Lone Star Le Mans (COTA)
- 27 Sep — Fuji
- 24 Oct — Qatar 1812 km
- 7 Nov — Bahrain

https://www.fiawec.com/en/page/live-fiawec

---

## 6. Driver rules (bonus)

**Verdict:** Yes.

**Verified:**
- FIA WEC sporting regulations and supplementary regulations are published via FIA/Al Kamel.

  https://fiawec.alkamelsystems.com/Results_NoticeBoard/

- IMSA publishes Sporting Regulations and Supplemental Sporting Regulations.

  https://www.imsa.com/competitors/2026-imsa-rules-regulations/

**Inferred/Unknown:**
- I did not verify the exact regulation articles defining minimum and maximum driver stint times.

---

## 7. Anything else relevant

- The biggest architectural finding is that **IMSA and WEC no longer appear to expose the same public transport layer.**

- IMSA:

  ```
  Browser
      ↓
  livetiming.alkamelsystems.com
      ↓
  Meteor DDP
  SockJS
  livetimingFeed("imsa")
  ```

- Current WEC:

  ```
  Browser
      ↓
  livetiming.fiawec.com
      ↓
  SignalR (reported)
      ↓
  MessagePack (reported)
      ↓
  UNKNOWN backend
  ```

- According to the Timing71 developer, the current implementation uses SignalR with MessagePack and is undocumented. Chrome DevTools is the recommended way to inspect the protocol.

  https://www.reddit.com/r/wec/comments/1tvry8s/is_there_any_publiclive_timing_api_for_le_mans/

- Multiple recent WEC users identify `https://livetiming.fiawec.com` as the free public live timing site, and Timing71 users report that the browser extension consumes the same data feed.

- **Practical implication for your Python client:** your collection-processing logic (entries, standings, race control, etc.) may still be reusable, but the Meteor/DDP transport layer should **not** be assumed to carry over to WEC. The first task is likely implementing a SignalR + MessagePack client rather than changing the existing DDP subscription name.