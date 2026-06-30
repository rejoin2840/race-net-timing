"""
WebSocket interceptor — discovers the WAMP/WS endpoint and data format
that the Timing71 web app uses.

Usage:
    python src/intercept_ws.py [URL]
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_URL = "https://www.timing71.org/services"
OBSERVE_SECONDS = 20
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


async def intercept(url: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Timing71 WebSocket Interceptor")
    print(f"  URL: {url}")
    print(f"{'='*60}\n")

    ws_messages = []
    ws_connections = []
    network_requests = []

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

        # ── intercept all network requests ───────────────────────────────────
        async def on_request(request):
            if any(k in request.url for k in ["api", "service", "timing", "live", "data"]):
                network_requests.append({
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers),
                })

        async def on_response(response):
            if any(k in response.url for k in ["api", "service", "timing", "live", "data"]):
                try:
                    body = await response.text()
                    if len(body) > 10:
                        network_requests.append({
                            "type": "RESPONSE",
                            "status": response.status,
                            "url": response.url,
                            "body_preview": body[:500],
                        })
                except Exception:
                    pass

        # ── intercept WebSocket frames ────────────────────────────────────────
        async def on_websocket(ws):
            print(f"  [WS] New connection: {ws.url}")
            ws_connections.append(ws.url)

            async def on_frame_sent(payload):
                record = {
                    "direction": "→ SENT",
                    "time": datetime.utcnow().isoformat(),
                    "url": ws.url,
                    "payload_preview": str(payload)[:300],
                }
                ws_messages.append(record)
                print(f"  [WS→] {record['payload_preview'][:120]}")

            async def on_frame_received(payload):
                record = {
                    "direction": "← RECV",
                    "time": datetime.utcnow().isoformat(),
                    "url": ws.url,
                    "payload_preview": str(payload)[:300],
                }
                ws_messages.append(record)
                print(f"  [WS←] {record['payload_preview'][:120]}")

            # Playwright 1.60: event passes the payload string directly, not an object
            ws.on("framesent", lambda p: asyncio.ensure_future(on_frame_sent(p)))
            ws.on("framereceived", lambda p: asyncio.ensure_future(on_frame_received(p)))
            ws.on("close", lambda: print(f"  [WS] Closed: {ws.url}"))

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("websocket", on_websocket)

        print(f"[1] Navigating to {url} ...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"    [WARN] {e}")

        print(f"[2] Observing for {OBSERVE_SECONDS}s ...")
        await page.wait_for_timeout(OBSERVE_SECONDS * 1000)

        # ── capture all XHR / fetch calls via JS ─────────────────────────────
        print("\n[3] JS network activity (XHR/fetch intercepted):")
        try:
            js_resources = await page.evaluate("""() => {
                return performance.getEntriesByType('resource').map(r => ({
                    name: r.name,
                    type: r.initiatorType,
                    size: r.transferSize,
                })).filter(r => r.type === 'fetch' || r.type === 'xmlhttprequest');
            }""")
            for r in js_resources[:20]:
                print(f"  {r['type'].upper():5s}  {r['name']}")
        except Exception as e:
            print(f"  Error: {e}")

        # ── full resource list ────────────────────────────────────────────────
        print("\n[4] All resources (first 30):")
        try:
            all_resources = await page.evaluate("""() => {
                return performance.getEntriesByType('resource').map(r => r.name).slice(0, 30);
            }""")
            for r in all_resources:
                print(f"  {r}")
        except Exception as e:
            print(f"  Error: {e}")

        await browser.close()

    # ── summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"\nWebSocket connections ({len(ws_connections)}):")
    for u in ws_connections:
        print(f"  {u}")

    print(f"\nWebSocket messages ({len(ws_messages)}):")
    for m in ws_messages[:20]:
        print(f"  [{m['direction']}]  {m['payload_preview'][:100]}")

    print(f"\nInteresting network requests ({len(network_requests)}):")
    for r in network_requests[:20]:
        print(f"  {r}")

    # Save full capture
    out = LOG_DIR / "ws_intercept.json"
    out.write_text(
        json.dumps({"ws_connections": ws_connections, "ws_messages": ws_messages,
                    "network": network_requests}, indent=2),
        encoding="utf-8",
    )
    print(f"\nFull capture saved → {out}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(intercept(url))
