#!/usr/bin/env bash
# Runs the OpenStorm US pipeline in order. Safe to re-run; finished downloads are skipped.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install --quiet requests
python3 -m unittest discover -s tests -q
python3 code/01_census.py
python3 code/02_fetch_layers.py --workers 4
python3 code/03_draft_crosswalks.py
python3 code/04_score.py --provisional
echo "Done. Open data/processed/qa_report_provisional.txt first."
