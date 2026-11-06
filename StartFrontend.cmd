@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m sensor_demo.launcher frontend
if errorlevel 1 pause
