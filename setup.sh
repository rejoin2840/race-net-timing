#!/bin/bash
set -e
PYTHON="${OVERCUT_PYTHON:-python3.12}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.12 required. Install it, or set OVERCUT_PYTHON to a Python 3.12 executable."
  exit 1
fi
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 required"'
"$PYTHON" -m venv venv
./venv/bin/pip install -r requirements.txt
echo "Setup complete. Launching dashboard..."
./venv/bin/python src/dashboard_calm.py
