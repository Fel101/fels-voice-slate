@echo off
rem Start Fel's Voice Slate with a console window, which shows any error message it prints. Day to day, use the icon that
rem setup.bat makes in this folder (or: .venv\Scripts\python.exe -m recorder --shortcut folder), which starts it with no console.
rem Run setup.bat once first: it creates the .venv this uses.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Fel's Voice Slate is not set up yet. Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m recorder
if errorlevel 1 pause
