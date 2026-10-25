@echo off
rem Opens this project in the AI Factory app, in your browser. Double-click this file.
rem Leave this window open while you use the app; close it to stop the app.
title AI Factory - leave this window open
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 devtools\factory\console\console.py --open --project . %*
) else (
  python devtools\factory\console\console.py --open --project . %*
)
if errorlevel 1 (
  echo.
  echo The app could not start. Is Python 3.11 or newer installed?
  pause
)
