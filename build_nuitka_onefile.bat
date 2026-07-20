@echo off
REM Onefile build: single build\onefile\JLinkRTTViewer.exe
REM Startup notes (see docs\packaging_startup_report.md):
REM   - Cold start extracts payload once per version; cached launches are
REM     ~0.2s slower than standalone (measured 1.96s vs 1.63s median).
REM   - For fastest startup ship the standalone build (build_nuitka.bat).
REM Build speed: ccache/clcache/bytecode caches under .\temp make rebuilds
REM much faster; keep the temp dir between builds.
REM Keep PRODUCT_VERSION in sync with pyproject.toml / about_page.py.
set PRODUCT_VERSION=0.6.0
set COMPANY_NAME=MisakaMikoto128
set PRODUCT_NAME=JLinkRTTViewer
set FILE_DESCRIPTION=J-Link RTT Viewer GUI

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
    --company-name="%COMPANY_NAME%" ^
    --product-name="%PRODUCT_NAME%" ^
    --product-version=%PRODUCT_VERSION% ^
    --file-version=%PRODUCT_VERSION% ^
    --onefile-tempdir-spec={CACHE_DIR}\%PRODUCT_NAME%\Cache\{VERSION} ^
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
    --nofollow-import-to=PySide6.Qt3DCore ^
    --nofollow-import-to=PySide6.Qt3DRender ^
    --nofollow-import-to=PySide6.Qt3DInput ^
    --nofollow-import-to=PySide6.Qt3DLogic ^
    --nofollow-import-to=PySide6.Qt3DAnimation ^
    --nofollow-import-to=PySide6.Qt3DExtras ^
    --nofollow-import-to=PySide6.QtBluetooth ^
    --nofollow-import-to=PySide6.QtCharts ^
    --nofollow-import-to=PySide6.QtConcurrent ^
    --nofollow-import-to=PySide6.QtDataVisualization ^
    --nofollow-import-to=PySide6.QtDBus ^
    --nofollow-import-to=PySide6.QtDesigner ^
    --nofollow-import-to=PySide6.QtGraphs ^
    --nofollow-import-to=PySide6.QtGraphsWidgets ^
    --nofollow-import-to=PySide6.QtHelp ^
    --nofollow-import-to=PySide6.QtHttpServer ^
    --nofollow-import-to=PySide6.QtLocation ^
    --nofollow-import-to=PySide6.QtMultimedia ^
    --nofollow-import-to=PySide6.QtMultimediaWidgets ^
    --nofollow-import-to=PySide6.QtNetwork ^
    --nofollow-import-to=PySide6.QtNetworkAuth ^
    --nofollow-import-to=PySide6.QtNfc ^
    --nofollow-import-to=PySide6.QtOpenGL ^
    --nofollow-import-to=PySide6.QtOpenGLWidgets ^
    --nofollow-import-to=PySide6.QtPdf ^
    --nofollow-import-to=PySide6.QtPdfWidgets ^
    --nofollow-import-to=PySide6.QtPositioning ^
    --nofollow-import-to=PySide6.QtPrintSupport ^
    --nofollow-import-to=PySide6.QtQml ^
    --nofollow-import-to=PySide6.QtQuick ^
    --nofollow-import-to=PySide6.QtQuick3D ^
    --nofollow-import-to=PySide6.QtQuickControls2 ^
    --nofollow-import-to=PySide6.QtQuickTemplates2 ^
    --nofollow-import-to=PySide6.QtQuickWidgets ^
    --nofollow-import-to=PySide6.QtRemoteObjects ^
    --nofollow-import-to=PySide6.QtScxml ^
    --nofollow-import-to=PySide6.QtSensors ^
    --nofollow-import-to=PySide6.QtSerialBus ^
    --nofollow-import-to=PySide6.QtSerialPort ^
    --nofollow-import-to=PySide6.QtShaderTools ^
    --nofollow-import-to=PySide6.QtSpatialAudio ^
    --nofollow-import-to=PySide6.QtSql ^
    --nofollow-import-to=PySide6.QtStateMachine ^
    --nofollow-import-to=PySide6.QtTest ^
    --nofollow-import-to=PySide6.QtTextToSpeech ^
    --nofollow-import-to=PySide6.QtUiTools ^
    --nofollow-import-to=PySide6.QtWebChannel ^
    --nofollow-import-to=PySide6.QtWebEngineCore ^
    --nofollow-import-to=PySide6.QtWebEngineQuick ^
    --nofollow-import-to=PySide6.QtWebEngineWidgets ^
    --nofollow-import-to=PySide6.QtWebSockets ^
    --nofollow-import-to=PySide6.QtWebView ^
    --nofollow-import-to=PySide6.QtAxContainer ^
    --include-package-data=qfluentwidgets ^
    --include-package-data=pylink ^
    --include-package=elftools ^
    --include-data-files=src\config.json=config.json ^
    --include-data-files=assets\icons\app_icon.ico=app_icon.ico ^
    --include-data-files=assets\icons\app_icon_256.png=app_icon_256.png ^
    --include-data-dir=src\i18n\=i18n\ ^
    --output-dir=build\onefile ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py

echo.
echo Build complete. Output: build\onefile\JLinkRTTViewer.exe
