@echo off
cd /d "%~dp0"
python -B "interface\panel.py" --live --watch-camera
if errorlevel 1 pause
