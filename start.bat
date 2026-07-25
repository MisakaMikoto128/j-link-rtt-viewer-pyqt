@echo off
chcp 65001 >nul
REM Launch the app from source. Picks venv\ first, then .venv\ (uv default).
REM If neither exists, tell the user how to create one and exit.
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [start.bat] No virtual environment found in project root.
    echo Expected either "venv\" or ".venv\". Create one with either:
    echo   python -m venv venv        ^(then: venv\Scripts\activate ^& pip install -r requirements.txt^)
    echo   uv venv .venv              ^(then: uv pip install -r requirements.txt^)
    echo See README.md "Run from source" section for details.
    pause
    exit /b 1
)
python src\main.py
