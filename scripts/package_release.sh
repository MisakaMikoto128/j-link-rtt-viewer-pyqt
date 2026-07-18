#!/usr/bin/env bash
# One-command packaging for Linux: Nuitka build (standalone + onefile) ->
# organize artifacts into a per-version folder under build/dist/.
#
# Run with NO options for an interactive menu (arrow keys, remembers your
# last choice in scripts/.package_release.prefs). Run WITH options for
# scripted/agent use - the menu is skipped entirely.
#
# Output layout:
#   build/dist/<basename>/
#     <basename>          onefile binary
#     <basename>.tar.gz   standalone, xz-max-compressed (falls back to gzip -9)
#     <basename>/         standalone, uncompressed (for testing)
#
# Version is auto-detected from git describe (same rules as the Windows
# package_release.ps1). Override with --version / --detail.
#
# Options:
#   --skip-build        package existing build output (no Nuitka run)
#   --build-only        build only, no packaging
#   --skip-standalone   only onefile
#   --skip-onefile      only standalone
#   --version X.Y.Z     override auto-detected version
#   --detail STR        override auto-detected detail (default release/dev.N.gHASH)
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_BUILD=0
BUILD_ONLY=0
SKIP_STANDALONE=0
SKIP_ONEFILE=0
VERSION=""
DETAIL=""

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-build)      SKIP_BUILD=1 ;;
        --build-only)      BUILD_ONLY=1 ;;
        --skip-standalone) SKIP_STANDALONE=1 ;;
        --skip-onefile)    SKIP_ONEFILE=1 ;;
        --version)         VERSION="$2"; shift ;;
        --detail)          DETAIL="$2"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

PREFS_FILE="$(dirname "$0")/.package_release.prefs"

# ---- Interactive menu (only when no action flags given) -----------------------
if [ "$SKIP_BUILD" -eq 0 ] && [ "$BUILD_ONLY" -eq 0 ] && [ "$SKIP_STANDALONE" -eq 0 ] && \
   [ "$SKIP_ONEFILE" -eq 0 ] && [ -z "$VERSION" ] && [ -z "$DETAIL" ]; then
    options=(
        "Build + package (full)"
        "Package only (use existing build output)"
        "Build only (no packaging)"
        "Exit"
    )
    descriptions=(
        "run both Nuitka builds, then refresh build/dist artifacts (~15-25 min)"
        "skip Nuitka; tarball/copy whatever is already in build/ (~1 min)"
        "run both Nuitka builds; do not touch build/dist"
        "do nothing"
    )
    saved=0
    [ -f "$PREFS_FILE" ] && { read -r saved < "$PREFS_FILE" || saved=0; }
    [[ "$saved" =~ ^[0-9]+$ ]] || saved=0

    echo "package_release - choose action (up/down + Enter):"
    [ -f "$PREFS_FILE" ] && echo "  (last choice preselected; Enter to repeat)"
    pos=$saved
    while true; do
        for i in "${!options[@]}"; do
            marker=" "; [ "$i" -eq "$pos" ] && marker=">"
            printf " %s %s - %s\n" "$marker" "${options[$i]}" "${descriptions[$i]}"
        done
        IFS= read -rsn1 key
        if [ "$key" = "" ]; then break; fi                    # Enter
        if [ "$key" = $'\x1b' ]; then
            read -rsn2 -t 0.1 seq || true
            case "$seq" in
                "[A") pos=$(( (pos - 1 + ${#options[@]}) % ${#options[@]} )) ;;
                "[B") pos=$(( (pos + 1) % ${#options[@]} )) ;;
            esac
        fi
        printf "\033[%dA" "${#options[@]}"                    # move cursor up to redraw
    done
    echo "$pos" > "$PREFS_FILE"
    case "$pos" in
        0) ;;
        1) SKIP_BUILD=1 ;;
        2) BUILD_ONLY=1 ;;
        3) echo "bye."; exit 0 ;;
    esac
    echo "-> ${options[$pos]}"
fi

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
OUT_DIR="build/dist/${BASENAME}"

echo "== package_release: ${BASENAME}"

# ---- Build -------------------------------------------------------------------
if [ "$SKIP_BUILD" -eq 1 ]; then
    echo "[1/4] skip build (--skip-build)"
else
    echo "[1/4] Nuitka build (standalone + onefile)"
    [ "$SKIP_STANDALONE" -eq 1 ] || ./build_nuitka.sh
    [ "$SKIP_ONEFILE" -eq 1 ]    || ./build_nuitka_onefile.sh
fi
if [ "$BUILD_ONLY" -eq 1 ]; then
    echo
    echo "Done (build only). Output under build/"
    exit 0
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
