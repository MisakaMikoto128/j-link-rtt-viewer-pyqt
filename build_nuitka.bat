@echo off
REM Standalone build: outputs build\main.dist\ (multi-file, fastest startup)
REM Performance flags:
REM   --lto=yes              link-time optimization, smaller binary + faster startup
REM   --python-flag=-O       strip assert + __debug__, skip docstring processing
REM   --python-flag=no_warnings   skip warning framework init
REM   --jobs=4               parallel compile workers
call venv\Scripts\activate.bat
python -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=assets\icons\app_icon.ico ^
    --lto=yes ^
    --python-flag=-O ^
    --python-flag=no_warnings ^
    --jobs=8 ^
    --assume-yes-for-downloads ^
    --include-package=qfluentwidgets ^
    --include-package-data=qfluentwidgets ^
    --include-package=pylink ^
    --include-package-data=pylink ^
    --include-package=elftools ^
    --include-package=intelhex ^
    --include-data-files=src\config.json=config.json ^
    --include-data-files=assets\icons\app_icon.ico=app_icon.ico ^
    --include-data-files=assets\icons\app_icon_256.png=app_icon_256.png ^
    --output-dir=build ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py

echo.
echo Build complete. Output: build\main.dist\JLinkRTTViewer.exe
