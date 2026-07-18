#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import os, time

OUT = os.path.join(os.path.dirname(__file__), "scenes")
os.makedirs(OUT, exist_ok=True)
BASE = "http://localhost:5173"
W, H = 1600, 1040

scenes = [
    ("green",  f"{BASE}/?scene=green", None),
    ("fcy",    f"{BASE}/?scene=fcy",   None),
    ("wywa",   f"{BASE}/?scene=green&wywa=1", "text=WYWA"),
    ("detail", f"{BASE}/?scene=green&car=31", "text=Net position"),
]

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": W, "height": H}, device_scale_factor=2,
                        color_scheme="dark")
    pg = ctx.new_page()
    for name, url, waitsel in scenes:
        pg.goto(url, wait_until="networkidle")
        pg.wait_for_selector(waitsel or "text=BATTLES", timeout=8000)
        time.sleep(1.2)  # let flag-band color transition + fonts settle
        out = os.path.join(OUT, f"{name}.png")
        pg.screenshot(path=out, clip={"x":0,"y":0,"width":W,"height":H})
        print("saved", out)
    b.close()
print("done")
