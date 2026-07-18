#!/usr/bin/env bash
# One-command packaging for Linux: Nuitka build (standalone + onefile) ->
# organize artifacts into a per-version folder under dist/.
#
# Output layout:
#   dist/<basename>/
#     <basename>          onefile binary
#     <basename>.tar.gz   standalone, xz-max-compressed (tar.gz name kept for
#                         familiarity; uses gzip -9 unless xz is available)
#     <basename>/         standalone, uncompressed (for testing)
#
# Version is auto-detected from git describe (same rules as the Windows
# package_release.ps1). Override with --version / --detail.
#
# Options:
#   --skip-build        package existing build output (no Nuitka run)
#   --skip-standalone   only onefile
#   --skip-onefile      only standalone
#   --version X.Y.Z     override auto-detected version
#   --detail STR        override auto-detected detail (default release/dev.N.gHASH)
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_BUILD=0
SKIP_STANDALONE=0
SKIP_ONEFILE=0
VERSION=""
DETAIL=""

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-build)      SKIP_BUILD=1 ;;
        --skip-standalone) SKIP_STANDALONE=1 ;;
        --skip-onefile)    SKIP_ONEFILE=1 ;;
        --version)         VERSION="$2"; shift ;;
        --detail)          DETAIL="$2"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

# ---- Version / detail detection from git ------------------------------------
if [ -z "$VERSION" ]; then
    desc="$(git describe --tags 2>/dev/null || true)"
    if [ -z "$desc" ]; then
        VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' pyproject.toml | head -1)"
        [ -n "$VERSION" ] || { echo "cannot determine version" >&2; exit 1; }
        [ -n "$DETAIL" ] || DETAIL="untagged.g$(git rev-parse --short HEAD)"
    elif [[ "$desc" =~ ^v?([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
        VERSION="${BASH_REMATCH[1]}"
        [ -n "$DETAIL" ] || DETAIL="release"
    elif [[ "$desc" =~ ^v?([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)-g([0-9a-f]+)$ ]]; then
        VERSION="${BASH_REMATCH[1]}"
        [ -n "$DETAIL" ] || DETAIL="dev.${BASH_REMATCH[2]}.g${BASH_REMATCH[3]}"
    else
        echo "cannot parse git describe output: $desc" >&2; exit 1
    fi
fi
[ -n "$DETAIL" ] || DETAIL="release"

BASENAME="JLinkRTTViewer-v${VERSION}-${DETAIL}-linux-x86_64"
OUT_DIR="dist/${BASENAME}"

echo "== package_release: ${BASENAME}"

# ---- Build -------------------------------------------------------------------
if [ "$SKIP_BUILD" -eq 1 ]; then
    echo "[1/4] skip build (--skip-build)"
else
    echo "[1/4] Nuitka build (standalone + onefile)"
    [ "$SKIP_STANDALONE" -eq 1 ] || ./build_nuitka.sh
    [ "$SKIP_ONEFILE" -eq 1 ]    || ./build_nuitka_onefile.sh
fi

# ---- Prepare output dir --------------------------------------------------------
# Overwrite policy: an artifact is (re)generated when missing OR when its build
# source is newer (fresh build => refresh dist). Rerun without rebuild = no-op.
echo "[2/4] prepare ${OUT_DIR}"
mkdir -p "$OUT_DIR"

# ---- Onefile binary -------------------------------------------------------------
if [ "$SKIP_ONEFILE" -eq 0 ]; then
    echo "[3/4] onefile binary"
    src="build/onefile/JLinkRTTViewer"
    [ -f "$src" ] || { echo "missing $src - run without --skip-build first" >&2; exit 1; }
    dst="${OUT_DIR}/${BASENAME}"
    if [ -f "$dst" ] && [ ! "$src" -nt "$dst" ]; then
        echo "   keep existing: $dst"
    else
        [ -f "$dst" ] && echo "   rebuild (source newer): $dst"
        cp "$src" "$dst"
        chmod +x "$dst"
        echo "   OK: $dst ($(du -h "$dst" | cut -f1))"
    fi
fi

# ---- Standalone: uncompressed dir + tarball --------------------------------------
if [ "$SKIP_STANDALONE" -eq 0 ]; then
    echo "[4/4] standalone dir + tarball"
    dist_dir="build/main.dist"
    src_marker="$dist_dir/JLinkRTTViewer"
    [ -f "$src_marker" ] || { echo "missing $src_marker" >&2; exit 1; }
    stage="${OUT_DIR}/${BASENAME}"
    tarball="${OUT_DIR}/${BASENAME}.tar.gz"

    if [ -d "$stage" ] && [ -f "$stage/JLinkRTTViewer" ] && [ ! "$src_marker" -nt "$stage/JLinkRTTViewer" ]; then
        echo "   keep existing dir: $stage"
    else
        [ -d "$stage" ] && { echo "   rebuild dir (source newer): $stage"; rm -rf "$stage"; }
        cp -r "$dist_dir" "$stage"
        echo "   OK: $stage/"
    fi

    if [ -f "$tarball" ] && [ -f "$stage/JLinkRTTViewer" ] && [ ! "$stage/JLinkRTTViewer" -nt "$tarball" ]; then
        echo "   keep existing: $tarball"
    else
        [ -f "$tarball" ] && { echo "   rebuild (source newer): $tarball"; rm -f "$tarball"; }
        if command -v xz >/dev/null 2>&1; then
            # xz -9e is the practical max for distribution tarballs
            tar -C "$OUT_DIR" -cf - "$BASENAME" | xz -9e -T0 > "$tarball"
        else
            tar -C "$OUT_DIR" -czf "$tarball" "$BASENAME"
        fi
        echo "   OK: $tarball ($(du -h "$tarball" | cut -f1))"
    fi
fi

echo
echo "Done: $OUT_DIR"
ls -lh "$OUT_DIR" | tail -n +2
