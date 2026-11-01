@echo off
rem Set up draft transcripts: a speech recognizer and a speech model, kept in this folder. Run it once; it asks first.
rem It downloads from PyPI and Hugging Face, so it needs an internet connection. The program itself never goes online.
rem You can give it a model size and a language: setup-transcripts.bat small --language de
cd /d "%~dp0"

rem The installed program has its own Python; a copy of this repository has the .venv that setup.bat made.
set "PY=.venv\Scripts\python.exe"
if exist "python\python.exe" set "PY=python\python.exe"
if not exist "%PY%" (
    echo Fel's Voice Slate is not set up yet. Run setup.bat first.
    pause
    exit /b 1
)

"%PY%" -m recorder --setup-transcripts %*
set "CODE=%errorlevel%"
echo.
pause
exit /b %CODE%
