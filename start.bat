@echo off
rem ClipForge on Windows: double-click to start with a window (close it to stop).
rem For no window at all use start-background.vbs, and stop.bat to stop it.
cd /d "%~dp0"
title ClipForge

rem /autostart = background / Windows sign-in: never wait for a key press (nobody can see the window)
set "WAIT=pause"
if /i "%~1"=="/autostart" set "WAIT=cd ."

if not exist .venv\Scripts\python.exe (
  echo Creating the Python environment ^(first run only^)...
  python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & %WAIT% & exit /b 1)
)
echo Checking dependencies...
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || (echo Dependency install failed. & %WAIT% & exit /b 1)

rem open the UI once the server has had a few seconds to start (not in background mode)
if /i not "%~1"=="/autostart" start "" /b cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:8800"

echo ClipForge is running at http://127.0.0.1:8800  -  close this window to stop it.
.venv\Scripts\python.exe -m uvicorn clipforge.server:app --host 127.0.0.1 --port 8800
%WAIT%
