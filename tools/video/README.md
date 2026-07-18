# Explainer-video pipeline

> Living reference — regen instructions only; epic/PR status lives in BACKLOG.md.

Rebuilds the ~70s dashboard explainer (screenshot tour with captions and
crossfades) from real recorded Rolex-24 frames. Run it after any visible
board-UI change so the video never drifts from the product.

## One command

```bash
./tools/video/regen.sh
```

Prereq: the board dev server on `:5173` with the demo scenes working —
`http://localhost:5173/?scene=green` must render the board from
`ui/public/demo/*.json` (the `?scene=` query mode in `ui/src/App.tsx`).
Output lands at `tools/video/out/dashboard_demo.mp4`.

## Pieces

| script | job |
|---|---|
| `capture_scenes.py` | Playwright screenshots of 4 scenes (`green`, `fcy`, `wywa`, `detail`) at 2x |
| `extract_boxes.py` | reads element rects from the live DOM → `boxes.json`, so highlight boxes track layout changes automatically |
| `build_frames.py` | PIL: title card + 8 captioned frames, gold highlight/spotlight effects |
| `assemble.py` | ffmpeg xfade chain + fade in/out → mp4 (per-frame durations set here) |
| `payload_dump.py` | only needed to make NEW scene JSONs: dumps the board's RowsPayload from `data/race.db` while a replay runs; cherry-pick dumps into `ui/public/demo/` |

Captions and scene order live in `build_frames.py`; durations in `assemble.py`.

## Refreshing the demo data (rare)

Only when you want different race moments: run a replay
(`weekend_conductor.py` replay mode), let `payload_dump.py` snapshot payloads,
pick a green-flag frame, an FCY frame, and a WYWA-worthy stretch, and replace
`ui/public/demo/{green,fcy,wywa}.json`.
