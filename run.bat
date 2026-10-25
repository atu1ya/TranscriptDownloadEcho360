@echo off
REM ===================================================================
REM  Echo360 Transcript Downloader -- Windows launcher
REM  Double-click this file. It sets everything up the first time.
REM  Do NOT run it as administrator: see the note below.
REM ===================================================================
setlocal
cd /d "%~dp0"
title Echo360 Transcript Downloader

REM --- running elevated breaks the logins ----------------------------
REM An elevated app opens the browser in a separate session that does not
REM share your signed-in Chrome/Edge profile, so Echo360 hands it a
REM logged-out token. A per-user Python also drops off PATH here.
net session >nul 2>&1
if not errorlevel 1 (
  echo.
  echo   This is running as administrator. Nothing here needs that, and
  echo   two things break because of it:
  echo.
  echo     * Echo360 logins fail -- the browser it opens is not signed in
  echo     * a Python installed "for me only" is not on PATH here
  echo.
  echo   Close this window and double-click run.bat normally instead.
  echo.
  pause
)

REM --- already set up? then no system Python is needed at all --------
if exist ".venv\Scripts\python.exe" goto launch

REM --- find a usable Python ------------------------------------------
REM Require BOTH a new enough version AND a working Tkinter: some Pythons
REM on PATH (MSYS2, minimal builds, the Store alias stub) have no Tkinter,
REM and the app would then die invisibly because pythonw has no console to
REM print the error to.
set PY=
for %%C in (py python python3) do (
  if not defined PY (
    %%C -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set PY=%%C
  )
)

REM Not on PATH does not mean not installed -- look where the installers
REM actually put it.
if not defined PY call :probe "%LocalAppData%\Programs\Python"
if not defined PY call :probe "%ProgramFiles%"
if not defined PY call :probe "%ProgramFiles(x86)%"
if not defined PY call :probe "C:"

if not defined PY (
  echo.
  echo   No suitable Python was found on this computer.
  echo   (It must be version 3.10 or newer, and include Tkinter.)
  echo.
  echo   If you do have Python, check you are not running this as
  echo   administrator -- a Python installed "for me only" is invisible
  echo   from there.
  echo.
  echo   Otherwise, installing takes about two minutes:
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
echo Setting up for first use. This takes a minute, one time only...
%PY% -m venv .venv
if errorlevel 1 goto venv_failed
.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\python -m pip install --quiet requests
if errorlevel 1 goto install_failed
echo Setup finished.

REM --- launch (pythonw = no console window) --------------------------
:launch
if exist ".venv\Scripts\pythonw.exe" (
  if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m echo360dl
    exit /b 0
  )
)
.venv\Scripts\python -m echo360dl %*
exit /b %errorlevel%

REM --- look for python.exe in the Python3xx folders under a directory -
:probe
for /d %%P in ("%~1\Python3*") do (
  if not defined PY (
    "%%P\python.exe" -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set PY="%%P\python.exe"
  )
)
exit /b 0

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
