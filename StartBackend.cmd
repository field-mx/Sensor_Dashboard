@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m sensor_demo.launcher backend
if errorlevel 1 pause
