@echo off
REM Onefile build: outputs single build\onefile\JLinkRTTViewer.exe
REM Notes:
REM   - Single .exe = maximum portability
REM   - First launch extracts to {LOCALAPPDATA}\JLinkRTTViewer\Cache\{VERSION}\
REM   - Subsequent launches hit the cache, near standalone speed
REM   - If you dislike first-launch extraction delay, use build_nuitka.bat (standalone) instead

REM Keep PRODUCT_VERSION in sync with pyproject.toml / about_page.py
set PRODUCT_VERSION=0.5.0
set COMPANY_NAME=MisakaMikoto128
set PRODUCT_NAME=JLinkRTTViewer

call venv\Scripts\activate.bat
python -m nuitka ^
    --onefile ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=assets\icons\app_icon.ico ^
    --lto=yes ^
    --python-flag=-O ^
    --python-flag=no_warnings ^
    --jobs=8 ^
    --assume-yes-for-downloads ^
    --company-name=%COMPANY_NAME% ^
    --product-name=%PRODUCT_NAME% ^
    --product-version=%PRODUCT_VERSION% ^
    --file-version=%PRODUCT_VERSION% ^
    --onefile-tempdir-spec={CACHE_DIR}\%PRODUCT_NAME%\Cache\{VERSION} ^
    --include-package=qfluentwidgets ^
    --include-package-data=qfluentwidgets ^
    --include-package=pylink ^
    --include-package-data=pylink ^
    --include-package=elftools ^
    --include-package=intelhex ^
    --include-data-files=src\config.json=config.json ^
    --include-data-files=assets\icons\app_icon.ico=app_icon.ico ^
    --include-data-files=assets\icons\app_icon_256.png=app_icon_256.png ^
    --include-data-dir=src\i18n\=i18n\ ^
    --output-dir=build\onefile ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py

echo.
echo Build complete. Output: build\onefile\JLinkRTTViewer.exe
