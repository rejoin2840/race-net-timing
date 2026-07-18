#!/bin/bash
# Regenerate the dashboard explainer video against the current board UI.
# Prereqs: board dev server on :5173 serving the demo scenes
#   (npm --prefix ui run vite, then confirm http://localhost:5173/?scene=green
#   renders — needs the ?scene= demo mode + ui/public/demo/*.json).
# Output: tools/video/out/dashboard_demo.mp4
set -euo pipefail
cd "$(dirname "$0")"
PY="../../venv/bin/python3"

$PY capture_scenes.py   # 4 board screenshots at 2x -> scenes/
$PY extract_boxes.py    # live DOM rects -> boxes.json (keeps highlights aligned)
$PY build_frames.py     # captioned 3200x2080 frames -> frames/
$PY assemble.py         # ffmpeg xfade chain -> out/dashboard_demo.mp4
echo "done: $(pwd)/out/dashboard_demo.mp4"
