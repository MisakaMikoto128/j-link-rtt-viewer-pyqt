@echo off
chcp 65001 >nul

REM Activate the venv if it exists; otherwise tell the user to create it first.
if not exist "venv\Scripts\activate.bat" (
    echo [start.bat] venv not found.
    echo Create it first:  python -m venv venv  ^&^&  venv\Scripts\activate.bat  ^&^&  pip install -r requirements.txt
    echo Or use uv:  uv venv venv  ^&^&  uv pip install -r requirements.txt
    exit /b 1
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [start.bat] failed to activate venv.
    exit /b 1
)

python src\main.py