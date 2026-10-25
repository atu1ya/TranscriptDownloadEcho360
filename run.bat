@echo off
REM ===================================================================
REM  Echo360 Transcript Downloader -- Windows launcher
REM  Double-click this file. It sets everything up the first time.
REM ===================================================================
setlocal
cd /d "%~dp0"
title Echo360 Transcript Downloader

REM --- find a usable Python ------------------------------------------
REM Require BOTH a new enough version AND a working Tkinter: some Pythons
REM on PATH (MSYS2, minimal builds) have no Tkinter, and the app would then
REM die invisibly because pythonw has no console to print the error to.
set PY=
for %%C in (py python python3) do (
  if not defined PY (
    %%C -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set PY=%%C
  )
)

if not defined PY (
  echo.
  echo   No suitable Python was found on this computer.
  echo   (It must be version 3.10 or newer, and include Tkinter.)
  echo.
  echo   This app needs it. Installing takes about two minutes:
  echo.
  echo     1. The download page will open in your browser
  echo     2. Download and run the installer
  echo     3. IMPORTANT: tick "Add python.exe to PATH" on the first screen
  echo     4. Finish the installer, then double-click this file again
  echo.
  pause
  start "" https://www.python.org/downloads/windows/
  exit /b 1
)

REM --- first run: build a private virtualenv -------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Setting up for first use. This takes a minute, one time only...
  %PY% -m venv .venv
  if errorlevel 1 goto venv_failed
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet requests
  if errorlevel 1 goto install_failed
  echo Setup finished.
)

REM --- launch (pythonw = no console window) --------------------------
if exist ".venv\Scripts\pythonw.exe" (
  if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m echo360dl
    exit /b 0
  )
)
.venv\Scripts\python -m echo360dl %*
exit /b %errorlevel%

:venv_failed
echo.
echo   Could not create the virtual environment.
echo   Try reinstalling Python from python.org, ticking "Add python.exe to PATH".
pause
exit /b 1

:install_failed
echo.
echo   Could not download the "requests" package.
echo   Check your internet connection (and any proxy or firewall) and try again.
pause
exit /b 1