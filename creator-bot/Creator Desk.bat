@echo off
rem Starts Creator Desk (Windows). The first run creates a private Python
rem environment next to this file and installs what the app needs.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.10 or newer is required: https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" in the installer, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run - setting things up. This takes about a minute...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create the Python environment.
    pause
    exit /b 1
  )
)

if not exist ".venv\.ready" (
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo Could not install the requirements - are you online?
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-window.txt >nul 2>nul
  type nul > ".venv\.ready"
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m creator_bot.desktop %*
) else (
  ".venv\Scripts\python.exe" -m creator_bot.desktop %*
)
