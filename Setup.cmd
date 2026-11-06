@echo off
cd /d "%~dp0"
if not "%~1"=="" (
  "%~1" -m demo_tools.setup_environment
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    python -m demo_tools.setup_environment
  ) else (
    py -3.11 -m demo_tools.setup_environment
  )
)
if errorlevel 1 pause
