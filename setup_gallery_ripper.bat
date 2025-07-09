@echo off
REM Change to script directory
cd /d "%~dp0"

REM Create virtual environment if it doesn't exist
if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Install required packages
pip install -r requirements.txt

echo.
echo Setup complete. Run start_gallery_ripper.bat to launch the application.
pause
