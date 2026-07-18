@echo off
REM Standalone build: outputs build\main.dist\ (multi-file, fastest startup).
REM Measured startup 1.63s median, faster than "python src\main.py" (2.00s)
REM and faster than cached onefile (1.96s). See docs\packaging_startup_report.md.
REM Build speed: ccache/clcache/bytecode caches under .\temp make rebuilds
REM much faster; keep the temp dir between builds.
call venv\Scripts\activate.bat
set NUITKA_CACHE_DIR_DOWNLOADS=.\temp\nuitka_cache_downloads
set NUITKA_CACHE_DIR_CCACHE=.\temp\nuitka_cache_ccache
set NUITKA_CACHE_DIR_CLCACHE=.\temp\nuitka_cache_clcache
set NUITKA_CACHE_DIR_BYTECODE=.\temp\nuitka_cache_bytecode
set NUITKA_CACHE_DIR_DLL_DEPENDENCIES=.\temp\nuitka_cache_dll_dependencies
python -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=assets\icons\app_icon.ico ^
    --lto=yes ^
    --remove-output ^
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
    --include-data-dir=src\i18n\=i18n\ ^
    --output-dir=build ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py

echo.
echo Build complete. Output: build\main.dist\JLinkRTTViewer.exe
