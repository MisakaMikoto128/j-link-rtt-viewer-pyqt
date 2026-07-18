#!/usr/bin/env bash
# Linux onefile build: single build/onefile/JLinkRTTViewer binary.
#
# The payload is extracted once per version to
#   ${XDG_CACHE_HOME:-~/.cache}/JLinkRTTViewer/Cache/0.6.0/
# so subsequent launches skip extraction (persistent cache, same as Windows).
#
# Prerequisites and J-Link notes: see build_nuitka.sh header.
set -euo pipefail
cd "$(dirname "$0")"

PRODUCT_VERSION=0.6.0
COMPANY_NAME=MisakaMikoto128
PRODUCT_NAME=JLinkRTTViewer
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
    --onefile \
    --enable-plugin=pyside6 \
    --lto=yes \
    --remove-output \
    --python-flag=-O \
    --python-flag=no_warnings \
    --jobs="$JOBS" \
    --assume-yes-for-downloads \
    --company-name="$COMPANY_NAME" \
    --product-name="$PRODUCT_NAME" \
    --product-version="$PRODUCT_VERSION" \
    --file-version="$PRODUCT_VERSION" \
    '--onefile-tempdir-spec={CACHE_DIR}/JLinkRTTViewer/Cache/{VERSION}' \
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
    --output-dir=build/onefile \
    --output-filename=JLinkRTTViewer \
    src/main.py

echo
echo "Build complete. Output: build/onefile/JLinkRTTViewer"
