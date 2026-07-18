@echo off
REM Standalone build: outputs build\main.dist\ (multi-file, fastest startup).
REM Measured startup 1.63s median, faster than "python src\main.py" (2.00s)
REM and faster than cached onefile (1.96s). See docs\packaging_startup_report.md.
REM Build speed: ccache/clcache/bytecode caches under .\temp make rebuilds
REM much faster; keep the temp dir between builds.

call venv\Scripts\activate.bat

REM Nuitka caches -- keep these between builds to speed up incremental rebuilds.
set NUITKA_CACHE_DIR_DOWNLOADS=.\temp\nuitka_cache_downloads
set NUITKA_CACHE_DIR_CCACHE=.\temp\nuitka_cache_ccache
set NUITKA_CACHE_DIR_CLCACHE=.\temp\nuitka_cache_clcache
set NUITKA_CACHE_DIR_BYTECODE=.\temp\nuitka_cache_bytecode
set NUITKA_CACHE_DIR_DLL_DEPENDENCIES=.\temp\nuitka_cache_dll_dependencies

REM Embedded PE metadata. Keep PRODUCT_VERSION in sync with pyproject.toml / about_page.py.
set COMPANY_NAME=MisakaMikoto128
set PRODUCT_NAME=JLinkRTTViewer
set PRODUCT_VERSION=0.6.0
set FILE_DESCRIPTION=J-Link RTT Viewer GUI

python -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=assets\icons\app_icon.ico ^
    --windows-company-name="%COMPANY_NAME%" ^
    --windows-product-name="%PRODUCT_NAME%" ^
    --windows-file-version="%PRODUCT_VERSION%" ^
    --windows-product-version="%PRODUCT_VERSION%" ^
    --windows-file-description="%FILE_DESCRIPTION%" ^
    --lto=yes ^
    --remove-output ^
    --python-flag=-O ^
    --python-flag=no_warnings ^
    --python-flag=no_site ^
    --jobs=8 ^
    --assume-yes-for-downloads ^
    --nofollow-import-to=*.tests ^
    --nofollow-import-to=*.test ^
    --nofollow-import-to=*.testing ^
    --nofollow-import-to=setuptools ^
    --nofollow-import-to=pip ^
    --nofollow-import-to=wheel ^
    --nofollow-import-to=pytest ^
    --nofollow-import-to=docutils ^
    --nofollow-import-to=unittest ^
    --nofollow-import-to=ensurepip ^
    --nofollow-import-to=distutils ^
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
    --output-dir=build ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py

echo.
echo Build complete. Output: build\main.dist\JLinkRTTViewer.exe
