@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import PySide6, PIL" >nul 2>&1
    if errorlevel 1 (
        echo Photo Curator dependencies are missing.
        echo Run: .venv\Scripts\python.exe -m pip install -e .
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m app
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python was not found. Install Python 3.11 or newer, then create .venv.
        pause
        exit /b 1
    )
    python -m app
)
endlocal

