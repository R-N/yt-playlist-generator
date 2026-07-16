@echo off
rem One-time install of review_app deps (pip + npm). Passes args through.
cd /d "%~dp0review_app"
python install.py %*
