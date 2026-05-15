@echo off
chcp 65001 >nul
call venv\Scripts\activate.bat
python -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --include-package=qfluentwidgets ^
    --include-package-data=qfluentwidgets ^
    --include-package=pylink ^
    --include-package-data=pylink ^
    --include-data-files=src\config.json=config.json ^
    --output-dir=build ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py

echo.
echo Build complete. Output: build\main.dist\JLinkRTTViewer.exe
