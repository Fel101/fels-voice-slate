@echo off
rem Set up Fel's Voice Slate: create the .venv, install what the program needs, and make an icon to start it with. Run this once.
rem It downloads the packages listed in requirements.txt from PyPI, so it needs an internet connection.
cd /d "%~dp0"

rem Find a Python 3.12 or newer. 3.12 to 3.14 come first, because the packages for draft transcripts are not built for newer
rem versions yet; then the newest one the Python launcher knows, and last the one on PATH.
set "PY="
for %%V in (3.14 3.13 3.12) do (
    if not defined PY (
        py -%%V -c "pass" >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=py -3"
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
echo Making the Fel's Voice Slate icon in this folder ...
".venv\Scripts\python.exe" -m recorder --shortcut folder
set "ICON=y"
set /p "ICON=Put one on your desktop too? [Y/n] "
if /i "%ICON%"=="n" goto :done
".venv\Scripts\python.exe" -m recorder --shortcut desktop

:done
echo.
echo Draft transcripts (optional): Fel's Voice Slate can suggest a transcript from the audio. That needs a speech
echo recognizer and a speech model, about 240 MB with the middle model, downloaded once from PyPI and Hugging Face into
echo this folder. Nothing you record is sent anywhere. You can also do this later with setup-transcripts.bat.
set "ASR=n"
set /p "ASR=Set up draft transcripts now? [y/N] "
if /i "%ASR%"=="y" ".venv\Scripts\python.exe" -m recorder --setup-transcripts

echo.
echo Done. Start Fel's Voice Slate from its icon (in this folder, or on your desktop), or with run.bat (which also shows
echo any error messages).
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
