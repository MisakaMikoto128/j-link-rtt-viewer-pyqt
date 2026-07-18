@echo off
REM Onefile build: single build\onefile\JLinkRTTViewer.exe
REM Startup notes (see docs\packaging_startup_report.md):
REM   - Cold start extracts payload once per version; cached launches are
REM     ~0.2s slower than standalone (measured 1.96s vs 1.63s median).
REM   - For fastest startup ship the standalone build (build_nuitka.bat).
REM Build speed: ccache/clcache/bytecode caches under .\temp make rebuilds
REM much faster; keep the temp dir between builds.
REM Keep PRODUCT_VERSION in sync with pyproject.toml / about_page.py
set PRODUCT_VERSION=0.6.0
set COMPANY_NAME=MisakaMikoto128
set PRODUCT_NAME=JLinkRTTViewer

call venv\Scripts\activate.bat
set NUITKA_CACHE_DIR_DOWNLOADS=.\temp\nuitka_cache_downloads
set NUITKA_CACHE_DIR_CCACHE=.\temp\nuitka_cache_ccache
set NUITKA_CACHE_DIR_CLCACHE=.\temp\nuitka_cache_clcache
set NUITKA_CACHE_DIR_BYTECODE=.\temp\nuitka_cache_bytecode
set NUITKA_CACHE_DIR_DLL_DEPENDENCIES=.\temp\nuitka_cache_dll_dependencies

python -m nuitka ^
    --onefile ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=assets\icons\app_icon.ico ^
    --lto=yes ^
    --remove-output ^
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
