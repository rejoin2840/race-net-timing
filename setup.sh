#!/bin/bash
set -e
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
echo "Setup complete. Launching dashboard..."
./venv/bin/python src/dashboard_calm.py
