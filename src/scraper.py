"""
Timing71 async polling scraper — Phase 1.
Polls a Timing71 page every POLL_INTERVAL seconds and prints a structured
row per car each cycle. No persistence yet — stdout/log output only.

Usage:
    python src/scraper.py <TIMING71_EVENT_URL> [poll_interval_seconds]

Examples:
    python src/scraper.py https://www.timing71.org/service/abc123
    python src/scraper.py https://www.timing71.org/service/abc123 10
"""

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PWTimeout

# ── config ───────────────────────────────────────────────────────────────────
POLL_INTERVAL   = 10          # seconds between polls
PAGE_LOAD_TIMEOUT = 30_000    # ms — initial navigation timeout
RENDER_WAIT     = 8_000       # ms — SPA settle on first load
SELECTOR_WAIT   = 5_000       # ms — wait for timing table on each poll

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── logging setup ─────────────────────────────────────────────────────────────
log_file = LOG_DIR / f"scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
log = logging.getLogger("scraper")


# ── data model ───────────────────────────────────────────────────────────────
@dataclass
class CarEntry:
    """
    One row of timing data per car per scrape cycle.
    Field names mirror Timing71's colSpec:
      NUM, STATE, CLASS, POS_IN_CLASS, TEAM, DRIVER, CAR,
      LAPS, GAP, INT, S1, S2, S3, LAST_LAP, BEST_LAP, PITS
    Extra: scraped_at, overall_position (row index), last_pit_lap (inferred).
    """
    scraped_at:       str
    overall_position: Optional[str]   # row index as fallback for absolute position
    car_number:       Optional[str]   # NUM
    state:            Optional[str]   # STATE (On Track / In Pit / Out Lap)
    car_class:        Optional[str]   # CLASS
    pos_in_class:     Optional[str]   # POS_IN_CLASS
    team:             Optional[str]   # TEAM
    driver_name:      Optional[str]   # DRIVER
    car_model:        Optional[str]   # CAR
    current_lap:      Optional[str]   # LAPS
    gap:              Optional[str]   # GAP (to leader)
    interval:         Optional[str]   # INT (to car ahead)
    last_lap_time:    Optional[str]   # LAST_LAP
    best_lap:         Optional[str]   # BEST_LAP
    pits:             Optional[str]   # PITS (total pit stops)
    last_pit_lap:     Optional[str]   # inferred from PITS changes
    raw_cells:        list            # full row text for debugging


# ── selector config (updated from inspect_page.py findings) ──────────────────
# These are the INITIAL guesses. Adjust after running inspect_page.py and
# confirming which selectors actually match on the live event page.
"""
COLUMN SPEC discovered from timing71 JS source (services chunk, field "colSpec"):
    NUM, STATE, CLASS, POS_IN_CLASS, TEAM, DRIVER, CAR, LAPS,
    GAP, INT, S1, S2, S3, LAST_LAP, BEST_LAP, PITS

These are Timing71's internal field names — the actual column ORDER in the
rendered HTML varies by event and display mode. Adjust indices after the
inspection script confirms the live layout, or switch to header-text matching.
"""
SELECTORS = {
    # The row selector — one <tr> per car
    # Timing71 renders a styled-components table; try both <table> rows and
    # class-based rows in case the layout uses divs instead of a <table>.
    "row": "table tbody tr",
    "row_fallback": "[class*='Row']:not([class*='Header'])",

    # Column indices within each row (0-based).
    # Default layout order for Timing71's standard endurance view:
    #   0=NUM  1=STATE  2=CLASS  3=POS_IN_CLASS  4=TEAM  5=DRIVER
    #   6=CAR  7=LAPS   8=GAP    9=INT  10=S1  11=S2  12=S3
    #   13=LAST_LAP  14=BEST_LAP  15=PITS
    "col_position":     None,   # no dedicated col — derived from row order OR POS_IN_CLASS
    "col_car_number":   0,      # NUM
    "col_state":        1,      # STATE  (In Pit / On Track / Out Lap etc.)
    "col_car_class":    2,      # CLASS
    "col_pos_in_class": 3,      # POS_IN_CLASS
    "col_team":         4,      # TEAM
    "col_driver_name":  5,      # DRIVER
    "col_car_model":    6,      # CAR
    "col_current_lap":  7,      # LAPS
    "col_gap":          8,      # GAP  (gap to overall leader)
    "col_interval":     9,      # INT  (gap to car ahead)
    "col_last_lap_time":13,     # LAST_LAP
    "col_best_lap":     14,     # BEST_LAP
    "col_pits":         15,     # PITS  (total stops — use to detect last pit)

    # last_pit_lap: Timing71 doesn't expose the raw lap number of last stop
    # in the colSpec; it must be inferred from PITS count changes over time.
    "col_last_pit_lap": None,
    "sel_last_pit_lap": "[class*='pit']",
}


def _cell(cells: list[str], idx: Optional[int]) -> Optional[str]:
    """Safe index into a list; returns None if index is None or out of range."""
    if idx is None or idx >= len(cells):
        return None
    val = cells[idx].strip()
    return val if val else None


async def _safe_text(el) -> str:
    """Get inner text from a playwright element, returning '' on error."""
    try:
        return (await el.inner_text()).strip()
    except Exception:
        return ""


# ── scrape one poll cycle ─────────────────────────────────────────────────────
async def scrape_once(page: Page) -> Optional[list[CarEntry]]:
    """
    Extract all car rows from the currently loaded page.
    Returns a list of CarEntry, or None if the table couldn't be found.
    """
    now = datetime.utcnow().isoformat() + "Z"
    row_sel = SELECTORS["row"]

    # Wait for at least one row to be present
    try:
        await page.wait_for_selector(row_sel, timeout=SELECTOR_WAIT)
    except PWTimeout:
        log.warning("Selector '%s' not found within %dms — table may not be loaded yet",
                    row_sel, SELECTOR_WAIT)
        return None

    row_els = await page.query_selector_all(row_sel)
    if not row_els:
        log.warning("Selector '%s' matched 0 rows", row_sel)
        return None

    entries: list[CarEntry] = []
    for row_el in row_els:
        cell_els = await row_el.query_selector_all("td")
        cells = [await _safe_text(c) for c in cell_els]

        if not any(cells):   # skip fully empty rows (spacers, footers)
            continue

        entry = CarEntry(
            scraped_at       = now,
            overall_position = str(len(entries) + 1),  # row index = overall position
            car_number       = _cell(cells, SELECTORS["col_car_number"]),
            state            = _cell(cells, SELECTORS["col_state"]),
            car_class        = _cell(cells, SELECTORS["col_car_class"]),
            pos_in_class     = _cell(cells, SELECTORS["col_pos_in_class"]),
            team             = _cell(cells, SELECTORS["col_team"]),
            driver_name      = _cell(cells, SELECTORS["col_driver_name"]),
            car_model        = _cell(cells, SELECTORS["col_car_model"]),
            current_lap      = _cell(cells, SELECTORS["col_current_lap"]),
            gap              = _cell(cells, SELECTORS["col_gap"]),
            interval         = _cell(cells, SELECTORS["col_interval"]),
            last_lap_time    = _cell(cells, SELECTORS["col_last_lap_time"]),
            best_lap         = _cell(cells, SELECTORS["col_best_lap"]),
            pits             = _cell(cells, SELECTORS["col_pits"]),
            last_pit_lap     = None,  # Phase 3: inferred from PITS changes over time
            raw_cells        = cells,
        )
        entries.append(entry)

    return entries


def _print_entries(entries: list[CarEntry], cycle: int) -> None:
    """Pretty-print a cycle's results to stdout."""
    print(f"\n{'─'*90}")
    print(f"  Cycle #{cycle:04d}  |  {datetime.utcnow().strftime('%H:%M:%S')} UTC  |  {len(entries)} cars")
    print(f"{'─'*90}")
    print(f"  {'OVR':>3}  {'CAR':>4}  {'CLS':>3}  {'CLP':>3}  {'DRIVER':<22}  {'LAP':>4}  {'STATE':<8}  {'LAST LAP':>9}  {'GAP':>9}  {'PITS':>4}")
    print(f"  {'---':>3}  {'---':>4}  {'---':>3}  {'---':>3}  {'------':<22}  {'---':>4}  {'-----':<8}  {'--------':>9}  {'---':>9}  {'----':>4}")
    for e in entries:
        print(
            f"  {(e.overall_position or '?'):>3}  "
            f"{(e.car_number or '?'):>4}  "
            f"{(e.car_class or '?'):>3}  "
            f"{(e.pos_in_class or '?'):>3}  "
            f"{(e.driver_name or e.team or '?'):<22}  "
            f"{(e.current_lap or '?'):>4}  "
            f"{(e.state or '?'):<8}  "
            f"{(e.last_lap_time or '?'):>9}  "
            f"{(e.gap or '?'):>9}  "
            f"{(e.pits or '-'):>4}"
        )

    # Also dump raw JSON so nothing is hidden
    raw_path = LOG_DIR / f"cycle_{cycle:04d}.json"
    raw_path.write_text(
        json.dumps([asdict(e) for e in entries], indent=2),
        encoding="utf-8",
    )
    log.info("Raw JSON → %s", raw_path)


# ── browser / page lifecycle ──────────────────────────────────────────────────
async def load_page(browser: Browser, url: str) -> Optional[Page]:
    """
    Open a new browser page and navigate to `url`.
    Waits for the SPA to render before returning.
    Returns None if the page fails to load.
    """
    try:
        context = await browser.new_context(
            viewport={"width": 1600, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        log.info("Navigating to %s ...", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        log.info("Waiting %ds for SPA render ...", RENDER_WAIT // 1000)
        await page.wait_for_timeout(RENDER_WAIT)
        log.info("Page loaded: %s", await page.title())
        return page
    except PWTimeout:
        log.error("Page load timed out (%dms): %s", PAGE_LOAD_TIMEOUT, url)
        return None
    except Exception as e:
        log.error("Failed to load page: %s", e)
        return None


# ── main poll loop ────────────────────────────────────────────────────────────
async def run(url: str, interval: int = POLL_INTERVAL) -> None:
    log.info("Starting scraper  url=%s  interval=%ds", url, interval)
    log.info("Log file: %s", log_file)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await load_page(browser, url)

        if page is None:
            log.error("Could not load the page. Exiting.")
            await browser.close()
            return

        cycle = 0
        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = 5
        PAGE_RELOAD_AFTER = 3   # reload page after this many consecutive failures

        try:
            while True:
                cycle += 1
                log.info("─── Poll cycle #%d ───", cycle)

                try:
                    entries = await scrape_once(page)
                except Exception as e:
                    log.error("Unexpected error in scrape_once: %s", e)
                    entries = None

                if entries is None:
                    consecutive_failures += 1
                    log.warning("Cycle #%d failed (%d consecutive)", cycle, consecutive_failures)

                    if consecutive_failures >= PAGE_RELOAD_AFTER:
                        log.warning("Reloading page after %d failures ...", consecutive_failures)
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
                            await page.wait_for_timeout(RENDER_WAIT)
                            log.info("Page reloaded successfully")
                            consecutive_failures = 0
                        except Exception as reload_err:
                            log.error("Page reload failed: %s", reload_err)

                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        log.error(
                            "Reached %d consecutive failures — closing and re-opening browser",
                            MAX_CONSECUTIVE_FAILURES,
                        )
                        try:
                            await page.context.close()
                        except Exception:
                            pass
                        page = await load_page(browser, url)
                        if page is None:
                            log.error("Re-open failed. Waiting %ds before retry ...", interval)
                        consecutive_failures = 0

                else:
                    consecutive_failures = 0
                    _print_entries(entries, cycle)
                    log.info("Cycle #%d: extracted %d car entries", cycle, len(entries))

                log.info("Sleeping %ds until next poll ...", interval)
                await asyncio.sleep(interval)

        except KeyboardInterrupt:
            log.info("Interrupted by user — shutting down cleanly.")
        finally:
            await browser.close()
            log.info("Browser closed. Bye.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    target_url = sys.argv[1]
    poll_seconds = int(sys.argv[2]) if len(sys.argv) > 2 else POLL_INTERVAL
    asyncio.run(run(target_url, poll_seconds))
