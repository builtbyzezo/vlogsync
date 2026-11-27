@echo off
cd /d "%~dp0"
python -B "interface\panel.py" --workspace "interface\Runs\Phase 3 Visual Review"
if errorlevel 1 pause
