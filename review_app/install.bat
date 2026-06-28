@echo off
rem Wrapper around install.py. Passes args through, e.g. install.bat --backend
cd /d "%~dp0"
python install.py %*
