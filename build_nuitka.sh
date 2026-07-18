#!/usr/bin/env bash
# Linux standalone build: build/main.dist/JLinkRTTViewer
#
# Prerequisites (Debian/Ubuntu names):
#   sudo apt install patchelf gcc python3-dev
#   # plus Qt runtime libs for PySide6 (usually pulled in automatically by pip):
#   #   libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1
#   pip install -r requirements.txt   # includes pylink-square==1.6.0
#
# Notes:
#   - Nuitka on Linux produces an ELF binary; no console-mode or icon flags.
#   - J-Link support requires SEGGER J-Link tools installed
#     (https://www.segger.com/downloads/jlink/) so that pylink can find
#     libjlinkarm.so. pylink-square bundles Windows/macOS libraries only.
#   - ccache is optional; if installed, Nuitka uses it automatically via the
#     cache dir below, making rebuilds much faster.
set -euo pipefail
cd "$(dirname "$0")"

JOBS="$(nproc 2>/dev/null || echo 4)"

if [ -d venv ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

export NUITKA_CACHE_DIR_DOWNLOADS=./temp/nuitka_cache_downloads
export NUITKA_CACHE_DIR_CCACHE=./temp/nuitka_cache_ccache
export NUITKA_CACHE_DIR_CLCACHE=./temp/nuitka_cache_clcache
export NUITKA_CACHE_DIR_BYTECODE=./temp/nuitka_cache_bytecode
export NUITKA_CACHE_DIR_DLL_DEPENDENCIES=./temp/nuitka_cache_dll_dependencies

python -m nuitka \
    --standalone \
    --enable-plugin=pyside6 \
    --lto=yes \
    --remove-output \
    --python-flag=-O \
    --python-flag=no_warnings \
    --jobs="$JOBS" \
    --assume-yes-for-downloads \
    --include-package=qfluentwidgets \
    --include-package-data=qfluentwidgets \
    --include-package=pylink \
    --include-package-data=pylink \
    --include-package=elftools \
    --include-package=intelhex \
    --include-data-files=src/config.json=config.json \
    --include-data-files=assets/icons/app_icon.ico=app_icon.ico \
    --include-data-files=assets/icons/app_icon_256.png=app_icon_256.png \
    --include-data-dir=src/i18n=i18n \
    --output-dir=build \
    --output-filename=JLinkRTTViewer \
    src/main.py

echo
echo "Build complete. Output: build/main.dist/JLinkRTTViewer"
echo "Run with:   ./build/main.dist/JLinkRTTViewer"
