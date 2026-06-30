"""
Inspection script — Phase 1.
Loads a Timing71 page with Playwright, dumps the rendered HTML/DOM,
and identifies CSS selectors for the fields we need.

Usage:
    python src/inspect_page.py [URL]

If URL is omitted, uses https://www.timing71.org (landing page).
Point it at a live event URL for real selector discovery.
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ── config ──────────────────────────────────────────────────────────────────
DEFAULT_URL = "https://www.timing71.org"
PAGE_LOAD_TIMEOUT = 30_000   # ms
RENDER_WAIT = 8_000           # ms — SPA render settle time
SCREENSHOT_PATH = Path("logs/inspect_screenshot.png")

# Candidate selectors to probe (ordered from most to least specific).
# These are educated guesses based on common Timing71 DOM patterns;
# the script will tell us which ones actually match.
SELECTOR_CANDIDATES = {
    "timing_table":    [
        "table.timing-table",
        "table[class*='timing']",
        "table[class*='leaderboard']",
        "div[class*='timing'] table",
        "div[class*='leaderboard'] table",
        "[class*='timing-app'] table",
        "table",
    ],
    "table_rows":      [
        "table tr:not(:first-child)",
        "table tbody tr",
        "tr[class*='car']",
        "tr[class*='entry']",
        "[class*='row']:not([class*='header'])",
    ],
    "position":        ["td:nth-child(1)", "[class*='position']", "[class*='pos']"],
    "car_number":      ["td:nth-child(2)", "[class*='num']", "[class*='car-number']", "[class*='car_number']"],
    "car_class":       ["td:nth-child(3)", "[class*='class']", "[class*='category']"],
    "driver_name":     ["td:nth-child(4)", "[class*='driver']", "[class*='name']"],
    "current_lap":     ["td:nth-child(5)", "[class*='laps']", "[class*='lap']"],
    "last_pit_lap":    ["[class*='pit']", "[class*='last-pit']", "[class*='pit-lap']"],
    "last_lap_time":   ["td:nth-child(6)", "[class*='last-lap']", "[class*='last_lap']", "[class*='laptime']"],
}


async def probe_selector(page, selector: str) -> int:
    """Return count of elements matching selector, or 0 on error."""
    try:
        els = await page.query_selector_all(selector)
        return len(els)
    except Exception:
        return 0


async def extract_table_sample(page, row_selector: str, cols: int = 10) -> list[dict]:
    """Try to pull the first 5 rows × first `cols` cells as text."""
    rows = []
    try:
        row_els = await page.query_selector_all(row_selector)
        for row_el in row_els[:5]:
            cells = await row_el.query_selector_all("td, th")
            texts = []
            for cell in cells[:cols]:
                texts.append((await cell.inner_text()).strip())
            if any(texts):
                rows.append({"cells": texts})
    except Exception as e:
        rows.append({"error": str(e)})
    return rows


async def inspect(url: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Timing71 DOM Inspector")
    print(f"  URL : {url}")
    print(f"  Time: {datetime.now().isoformat()}")
    print(f"{'='*60}\n")

    SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        # ── 1. Navigate ──────────────────────────────────────────────────────
        print(f"[1] Navigating to {url} ...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        except PWTimeout:
            print("    [WARN] domcontentloaded timed out — continuing anyway")

        print(f"    Waiting {RENDER_WAIT/1000:.0f}s for SPA to render ...")
        await page.wait_for_timeout(RENDER_WAIT)

        # Try to wait for any table to appear
        try:
            await page.wait_for_selector("table", timeout=5_000)
            print("    [OK] <table> element found")
        except PWTimeout:
            print("    [WARN] No <table> found after render — page may be landing/lobby")

        # ── 2. Screenshot ────────────────────────────────────────────────────
        await page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
        print(f"\n[2] Screenshot saved → {SCREENSHOT_PATH}\n")

        # ── 3. Page title & URL ──────────────────────────────────────────────
        title = await page.title()
        final_url = page.url
        print(f"[3] Page title : {title!r}")
        print(f"    Final URL  : {final_url}\n")

        # ── 4. Probe candidate selectors ─────────────────────────────────────
        print("[4] Probing candidate selectors:\n")
        results: dict[str, dict] = {}

        for field, candidates in SELECTOR_CANDIDATES.items():
            print(f"  {field}:")
            best = None
            for sel in candidates:
                count = await probe_selector(page, sel)
                status = f"  {count:3d} match{'es' if count != 1 else ' '}"
                marker = " ◀ BEST" if count > 0 and best is None else ""
                print(f"    [{status}]  {sel}{marker}")
                if count > 0 and best is None:
                    best = {"selector": sel, "count": count}
            results[field] = best or {"selector": None, "count": 0}
            print()

        # ── 5. Table sample ──────────────────────────────────────────────────
        row_sel = (results["table_rows"] or {}).get("selector")
        if row_sel:
            print(f"[5] First 5 rows via '{row_sel}':\n")
            rows = await extract_table_sample(page, row_sel)
            for i, row in enumerate(rows):
                if "error" in row:
                    print(f"  Row {i}: ERROR — {row['error']}")
                else:
                    print(f"  Row {i}: {row['cells']}")
        else:
            print("[5] No row selector matched — dumping raw <body> text snippet:")
            body_text = await page.inner_text("body")
            print("  ", body_text[:1000].replace("\n", " | "))

        # ── 6. DOM structure hint ─────────────────────────────────────────────
        print("\n[6] Top-level class names on the page (first 30 unique):")
        class_names = await page.evaluate("""() => {
            const all = document.querySelectorAll('[class]');
            const seen = new Set();
            for (const el of all) {
                for (const c of el.classList) seen.add(c);
                if (seen.size >= 30) break;
            }
            return [...seen];
        }""")
        for c in class_names:
            print(f"    .{c}")

        # ── 7. Links to sub-events (landing page) ────────────────────────────
        print("\n[7] Links that might point to live timing events:")
        links = await page.evaluate("""() => {
            return [...document.querySelectorAll('a[href]')]
                .map(a => ({ text: a.innerText.trim().slice(0, 60), href: a.href }))
                .filter(l => l.href.includes('service') || l.href.includes('event') ||
                             l.href.includes('timing') || l.href.includes('live'))
                .slice(0, 15);
        }""")
        if links:
            for lnk in links:
                print(f"    {lnk['text']!r:40s}  →  {lnk['href']}")
        else:
            print("    (none found matching event/service/timing/live keywords)")

        # ── 8. Dump raw HTML snippet ──────────────────────────────────────────
        html_dump_path = Path("logs/inspect_dom.html")
        content = await page.content()
        html_dump_path.write_text(content, encoding="utf-8")
        print(f"\n[8] Full rendered HTML saved → {html_dump_path}  ({len(content):,} bytes)")

        # ── 9. Summary JSON ───────────────────────────────────────────────────
        summary = {"url": final_url, "title": title, "selectors": results}
        summary_path = Path("logs/inspect_selectors.json")
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[9] Selector summary saved → {summary_path}")

        await browser.close()

    print("\n[DONE] Inspection complete.\n")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(inspect(url))
