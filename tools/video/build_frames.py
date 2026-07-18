#!/usr/bin/env python3
"""Compose captioned 3200x2080 (2x) scene frames for the dashboard explainer."""
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import json, os

SCR = os.path.dirname(__file__)
SCENES = os.path.join(SCR, "scenes")
OUT = os.path.join(SCR, "frames"); os.makedirs(OUT, exist_ok=True)
# live DOM rects from extract_boxes.py (1600-space CSS px) — regenerate boxes.json
# whenever board columns move so highlights track the real layout
BOXES = json.load(open(os.path.join(SCR, "boxes.json")))
W, H = 3200, 2080  # 2x

F = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FIT = "/System/Library/Fonts/Supplemental/Arial.ttf"
def font(sz, bold=True): return ImageFont.truetype(F if bold else FIT, sz)

GOLD = (245, 198, 74)
WHITE = (245, 247, 250)
SUB = (168, 176, 186)

def wrap(draw, text, fnt, maxw):
    words = text.split(); lines=[]; cur=""
    for w in words:
        t=(cur+" "+w).strip()
        if draw.textlength(t, font=fnt) <= maxw: cur=t
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines

def caption(img, text, size=60, accent=None):
    """Bottom scrim + wrapped caption. `accent` = list of substrings to color gold."""
    d = ImageDraw.Draw(img, "RGBA")
    fnt = font(size)
    lines = wrap(d, text, fnt, W-260)
    lh = int(size*1.34)
    block_h = lh*len(lines)
    scrim_h = block_h + 220
    # gradient scrim
    grad = Image.new("L", (1, scrim_h), 0)
    for y in range(scrim_h):
        a = int(238 * (y/scrim_h)**1.15)
        grad.putpixel((0,y), a)
    grad = grad.resize((W, scrim_h))
    black = Image.new("RGBA",(W,scrim_h),(6,9,13,255)); black.putalpha(grad)
    img.alpha_composite(black,(0,H-scrim_h))
    y0 = H - block_h - 90
    for ln in lines:
        x = 130
        # draw with optional gold accent words
        if accent:
            # tokenize preserving spaces; color any token containing an accent key
            words = ln.split(" ")
            for i,wd in enumerate(words):
                col = GOLD if any(a in wd for a in accent) else WHITE
                seg = wd + (" " if i<len(words)-1 else "")
                d.text((x,y0), seg, font=fnt, fill=col)
                x += int(d.textlength(seg, font=fnt))
        else:
            d.text((x,y0), ln, font=fnt, fill=WHITE)
        y0 += lh
    return img

def load(name): return Image.open(os.path.join(SCENES,name)).convert("RGBA")

def title_frame():
    img = Image.new("RGBA",(W,H),(9,11,15,255))
    d = ImageDraw.Draw(img)
    tf = font(150); sf = font(62, bold=False)
    t = "RACE STRATEGY DASHBOARD"
    tw = d.textlength(t, font=tf)
    d.text(((W-tw)/2, H/2-160), t, font=tf, fill=GOLD)
    s = "A real 24-hour race, replayed — the Rolex 24 at Daytona"
    sw = d.textlength(s, font=sf)
    d.text(((W-sw)/2, H/2+40), s, font=sf, fill=SUB)
    # thin gold rule
    d.rectangle([ (W-tw)/2, H/2+14, (W-tw)/2+tw, H/2+18 ], fill=(245,198,74,90))
    return img

def accuracy_frame():
    """Closing slide: current + projected accuracy, in plain language.
    Numbers come from the evaluator's 14-race regression set (BACKLOG.md
    decisions log, 07-13 recalibration) — update them when the evaluator does."""
    img = Image.new("RGBA",(W,H),(9,11,15,255))
    d = ImageDraw.Draw(img)
    tf = font(120); lf = font(56); bf = font(54, bold=False); sf = font(46, bold=False)
    t = "HOW ACCURATE IS IT?"
    tw = d.textlength(t, font=tf)
    d.text(((W-tw)/2, 170), t, font=tf, fill=GOLD)
    s = "Today's numbers, graded against 14 full recorded races — honesty over hype"
    sw = d.textlength(s, font=sf)
    d.text(((W-sw)/2, 330), s, font=sf, fill=SUB)

    rows = [
        ("PROJECTED FINISH",
         "typically off by 2–4 positions mid-race — and the error shrinks "
         "every lap as real pit stops replace estimates."),
        ("CATCH CALLS",
         "“the #7 is coming for the leader” — right about 9 times in 10 in "
         "testing so far, usually flagged within ~3 laps of the real move."),
        ("PIT-STOP COSTS",
         "every estimate carries a visible ± band. Crash repairs and long "
         "garage stops are reported as they happen, never guessed."),
        ("COMING NEXT",
         "live fuel telemetry — pit-window calls within ~2 laps of the actual "
         "stop, and tighter finish projections from real tank data."),
    ]
    y = 660
    for label, body in rows:
        d.text((260, y), label, font=lf, fill=GOLD)
        ly = y
        for ln in wrap(d, body, bf, W-1320):
            d.text((1060, ly), ln, font=bf, fill=WHITE)
            ly += int(54*1.4)
        y = max(y + 290, ly + 120)
    return img

def zoom_rows(highlight_net=False):
    src = load("green.png")
    cx0,cy0,cw,ch = 12,168,1968,1280   # 2x source crop, aspect 1.5375
    crop = src.crop((cx0,cy0,cx0+cw,cy0+ch)).resize((W,H), Image.LANCZOS)
    if highlight_net:
        d = ImageDraw.Draw(crop,"RGBA")
        sc = W/cw
        net = BOXES["netHeader"]  # CSS px; source screenshots are 2x
        nx0 = (net["x"]*2-cx0)*sc - 26; nx1 = ((net["x"]+net["w"])*2-cx0)*sc + 26
        d.rounded_rectangle([nx0, 70, nx1, H-560], radius=26, outline=GOLD, width=8)
    return crop

def spotlight(box, pad=26):
    src = load("green.png")
    dim = ImageEnhance.Brightness(src).enhance(0.30)
    x,y,w,h = [v*2 for v in box]  # box given in 1600-space
    x0,y0,x1,y1 = x-pad, y-pad, x+w+pad, y+h+pad
    region = src.crop((x0,y0,x1,y1))
    dim.paste(region,(int(x0),int(y0)))
    d = ImageDraw.Draw(dim,"RGBA")
    d.rounded_rectangle([x0,y0,x1,y1], radius=22, outline=GOLD, width=7)
    return dim

def save(img, name): img.convert("RGB").save(os.path.join(OUT,name), quality=95)

# ── scenes ──────────────────────────────────────────────────────────────────
save(title_frame(), "00_title.png")

save(caption(load("green.png"),
    "One screen for a 24-hour, 60-car race. The cars run in separate classes at once, so the board groups them — and every row updates live.",
    size=60), "01_overview.png")

save(caption(zoom_rows(),
    "Each row is one car: its recent lap and pace, fuel and stint, when it has to pit next, the gap to its class, and stops made.",
    size=58), "02_rows.png")

save(caption(zoom_rows(highlight_net=True),
    "The NET column is the real edge — it accounts for the pit stops a car still owes and projects where it will actually finish. Green gains, red loses.",
    size=56, accent=["NET","Green","gains,","red","loses."]), "03_net.png")

save(caption(load("detail.png"),
    "Click any car for its full read — net-position math, the stops it still owes, next-stop cost, and its entire pit history.",
    size=58, accent=["net-position"]), "04_detail.png")

save(caption(load("wywa.png"),
    "Step away, then come back — the “While you were away” card recaps what you missed: lead changes, big moves, and penalties.",
    size=58, accent=["“While","you","were","away”"]), "05_wywa.png")

save(caption(load("fcy.png"),
    "The moment race control calls a full-course yellow, the entire top band turns amber — impossible to miss from across the garage.",
    size=58, accent=["full-course","yellow,","amber"]), "06_fcy.png")

save(caption(spotlight([1403,410,185,318]),
    "Official race-control messages stream in live — penalties, investigations, retirements, cars off course — timestamped as they land.",
    size=58), "07_rc.png")

save(caption(spotlight([1403,53,185,336]),
    "Close fights are flagged the instant they form, and each class’s projected podium is recomputed lap after lap.",
    size=58), "08_battles.png")

save(accuracy_frame(), "09_accuracy.png")

print("frames:", sorted(os.listdir(OUT)))
