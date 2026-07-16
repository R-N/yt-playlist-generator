@echo off
rem Launch the review_app UI. Passes all args through, e.g. run.bat --dev
cd /d "%~dp0review_app"
python run.py %*
