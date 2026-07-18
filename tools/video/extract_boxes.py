#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json, os
BASE="http://localhost:5173"
with sync_playwright() as p:
    b=p.chromium.launch()
    pg=b.new_context(viewport={"width":1600,"height":1040},device_scale_factor=2,color_scheme="dark").new_page()
    pg.goto(f"{BASE}/?scene=green", wait_until="networkidle")
    pg.wait_for_selector("text=BATTLES")
    boxes=pg.evaluate(r"""() => {
      const out={};
      const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
      // first GTP class section (rounded border block)
      const sections=[...document.querySelectorAll('div.rounded-md.overflow-hidden')];
      // find the one whose header text starts with GTP (not GTDPRO)
      const gtp=sections.find(s=>/^GTP\b/.test(s.textContent.trim()));
      if(gtp) out.gtpSection=rect(gtp);
      // first car row
      const row=document.querySelector('[role=button]');
      if(row) out.firstRow=rect(row);
      // NET column header (first)
      const net=[...document.querySelectorAll('div')].find(d=>d.textContent.trim()==='NET');
      if(net) out.netHeader=rect(net);
      // right rail
      const rail=document.querySelector('aside');
      if(rail) out.rail=rect(rail);
      // rail sections by label
      const labelSection=(name)=>{
        const lab=[...document.querySelectorAll('aside div')].find(d=>d.textContent.trim().toUpperCase()===name);
        return lab?rect(lab.parentElement):null;
      };
      out.raceControl=labelSection('RACE CONTROL');
      out.battles=labelSection('BATTLES');
      out.podium=labelSection('PROJECTED PODIUM');
      out.viewport={w:1600,h:1040};
      return out;
    }""")
    print(json.dumps(boxes,indent=2))
    json.dump(boxes, open(os.path.join(os.path.dirname(__file__),"boxes.json"),"w"))
    b.close()
