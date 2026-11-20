@echo off
rem Stops ClipForge, including when it runs in the background (start-background.vbs / Windows sign-in),
rem and its live-stream recorders (ffmpeg writing into data\live).
powershell -NoProfile -Command "$live = (Join-Path '%~dp0' 'data\live'); $p = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and (($_.Name -like 'python*.exe' -and $_.CommandLine -like '*clipforge.server:app*') -or ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*clipforge\start.bat*') -or ($_.Name -eq 'ffmpeg.exe' -and $_.CommandLine -like ('*' + $live + '*'))) }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; 'ClipForge stopped.' } else { 'ClipForge was not running.' }"
ping -n 3 127.0.0.1 >nul
