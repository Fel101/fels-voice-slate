@echo off
rem Set up Fel's Voice Slate: create the .venv, install what the program needs, and offer a desktop icon. Run this once.
rem It downloads the packages listed in requirements.txt from PyPI, so it needs an internet connection.
cd /d "%~dp0"

rem Find a Python 3.12 or newer: the newest one the Python launcher knows, else the one on PATH.
set "PY="
for %%V in (3.14 3.13 3.12) do (
    if not defined PY (
        py -%%V -c "pass" >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython

if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment in .venv using %PY% ...
    %PY% -m venv .venv
    if errorlevel 1 goto :failed
)
echo Installing the requirements ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Checking the install ...
".venv\Scripts\python.exe" -m recorder --check
if errorlevel 1 goto :failed

echo.
set "ICON=y"
set /p "ICON=Put a Fel's Voice Slate icon on your desktop? [Y/n] "
if /i "%ICON%"=="n" goto :done
".venv\Scripts\python.exe" -m recorder --shortcut desktop

:done
echo.
echo Done. Start Fel's Voice Slate from its desktop icon, or with run.bat (which also shows any error messages).
pause
exit /b 0

:nopython
echo Fel's Voice Slate needs Python 3.12 or newer, and it was not found.
echo Install it from https://www.python.org/downloads/ and run this file again.
pause
exit /b 1

:failed
echo.
echo Setup did not finish. Read the messages above for the reason.
pause
exit /b 1
