@echo off
REM Runs the OpenStorm US pipeline on Windows. Safe to re-run; finished downloads are skipped.
cd /d "%~dp0"
py -m pip install --quiet requests || goto :err
py -m unittest discover -s tests -q || goto :err
py code\01_census.py || goto :err
py code\02_fetch_layers.py --workers 4 || goto :err
py code\03_draft_crosswalks.py || goto :err
py code\04_score.py --provisional || goto :err
echo Done. Open data\processed\qa_report_provisional.txt first.
pause
exit /b 0
:err
echo Something failed. Copy the last lines above into the Claude chat.
pause
exit /b 1
