@echo off
rem Starts By the Book, the AI factory's app, and opens it in your browser. Double-click this file.
rem It uses your project list (the one you set up with --add-project, or the New project button).
rem Leave this window open while you use the app; close it to stop the app.
title By the Book - leave this window open
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 console.py --open %*
) else (
  python console.py --open %*
)
if errorlevel 1 (
  echo.
  echo The app could not start. Is Python 3.11 or newer installed?
  pause
)
