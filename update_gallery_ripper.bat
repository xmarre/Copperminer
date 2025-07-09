@echo off
REM Change to script directory
cd /d "%~dp0"

REM Pull latest changes from repository
if exist .git (
    git remote set-url origin https://github.com/xmarre/Copperminer.git
    git pull
) else (
    echo This directory is not a Git repository.
)

REM Optionally update dependencies
if exist requirements.txt (
    call .venv\Scripts\activate.bat >nul 2>&1
    pip install -r requirements.txt
)

echo.
echo Update complete.
pause
