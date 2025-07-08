@echo off
REM Change to script directory (in case launched from elsewhere)
cd /d "%~dp0"

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Run your script
python gallery_ripper.py

REM Keep the window open so you can read any output
pause
