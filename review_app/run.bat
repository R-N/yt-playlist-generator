@echo off
rem Wrapper around run.py. Passes all args through, e.g. run.bat --dev
cd /d "%~dp0"
python run.py %*
