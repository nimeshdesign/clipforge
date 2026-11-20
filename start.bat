@echo off
rem ClipForge on Windows: double-click to start. Close this window to stop the server.
cd /d "%~dp0"
title ClipForge

if not exist .venv\Scripts\python.exe (
  echo Creating the Python environment ^(first run only^)...
  python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
)
echo Checking dependencies...
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || (echo Dependency install failed. & pause & exit /b 1)

rem open the UI once the server has had a few seconds to start (not when launched at Windows sign-in)
if /i not "%~1"=="/autostart" start "" /b cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:8800"

echo ClipForge is running at http://127.0.0.1:8800  -  close this window to stop it.
.venv\Scripts\python.exe -m uvicorn clipforge.server:app --host 127.0.0.1 --port 8800
pause
