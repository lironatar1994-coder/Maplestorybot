@echo off
rem Double-click to open the bot's control panel as administrator.
rem Admin is needed when the game itself runs as admin; otherwise Windows blocks the bot's key presses.
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    rem Not elevated yet: relaunch this file through the Windows admin prompt, then close this window.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist ".venv\Scripts\python.exe" (
    echo The bot's Python environment is missing. In this folder, run:
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

title Maple training bot (keep this window open while the panel runs)
".venv\Scripts\python.exe" gui.py
if errorlevel 1 pause
