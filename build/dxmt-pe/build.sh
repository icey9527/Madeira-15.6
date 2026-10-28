#!/bin/bash
# Rebuild DXMT's Windows-side (ARM64EC) d3d11.dll with its EMBEDDED command
# library compiled for this deployment target's iOS Metal level.
#
# Upstream's arm64ec-windows/d3d11.dll embeds a library built for macOS 14.0 /
# Metal 3.1 (its metallib header reads platform 0x8001 / OS 0x81 / version 14.0),
# and iOS 15.6 refuses it at device creation:
#
#   err: Failed to create internal command library: Error Domain=MTLLibraryErrorDomain
#        Code=1 "This library format is not supported on this platform (or was
#        built with an old version of the tools)"
#
# The iOS 16 fork's CI rebuilds the same DLL with -Dmetal_std / -Dmetal_sdk /
# -Dmetal_target (ci/patches/dxmt/0002); this does the same with the standard the
# deployment target actually knows ("Metal 2.4 (iOS)" for iOS 15), and refuses to
# ship a DLL whose embedded library is not an iOS one.
#
# Run this AFTER tools/ci/verify-upstream-compat.py: it overwrites one committed
# compatibility file on purpose (the committed copy is upstream's macOS build).
set -eu

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TC="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
export PATH="$TC:$PATH"
if command -v brew >/dev/null 2>&1; then
    BISON_BIN="$(brew --prefix bison 2>/dev/null)/bin"
    [ -x "$BISON_BIN/bison" ] && export PATH="$BISON_BIN:$PATH"
fi

W="$R/wine/build-arm64ec"
B="$R/dxmt/build-arm64ec"
OUT="$R/app/Madeira/arm64ec-windows/d3d11.dll"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 8)}"

MIN="${IPHONEOS_DEPLOYMENT_TARGET:-15.6}"; MIN="${MIN%%.*}"
case "$MIN" in
    1[0-5]) DEFAULT_STD="ios-metal2.4" ;;   # Metal 2.4 (iOS) is iOS 15's level
    16)     DEFAULT_STD="metal3.0" ;;
    *)      DEFAULT_STD="metal3.1" ;;
esac
METAL_STD="${DXMT_METAL_STD:-$DEFAULT_STD}"
METAL_TARGET="${DXMT_METAL_TARGET:-air64-apple-ios$MIN}"

python3 "$R/build/dxmt-pe/prepare-meson.py" "$R/dxmt"

# Madeira-15.6: compile the SAME sources the iOS library does.
#
# prepare-compat.py patches the D3D11 front end (and the rest of DXMT) into
# build/dxmt-ios/compat-src, and build.sh there consumes those files one by one.
# meson cannot, so the patched files are written back over the submodule before
# configuring. Without this, d3d11.dll shipped upstream code while the unix side
# shipped ours: the argument-buffer ring allocation probe added to
# dxmt_command_queue.hpp on 2026-10-28 never appeared in a device log, while the
# probes in winemetal_unix.c (unix side) did.
python3 "$R/build/dxmt-ios/prepare-compat.py" "$R/dxmt/src" "$R/build/dxmt-ios/compat-src" \
    "${IPHONEOS_DEPLOYMENT_TARGET:-15.6}"
if [ -d "$R/build/dxmt-ios/compat-src" ]; then
    ( cd "$R/build/dxmt-ios/compat-src" && find . -type f -print0 ) |
    while IFS= read -r -d '' f; do
        f="${f#./}"
        mkdir -p "$R/dxmt/src/$(dirname "$f")"
        cp "$R/build/dxmt-ios/compat-src/$f" "$R/dxmt/src/$f"
    done
fi

if [ ! -f "$W/config.status" ]; then
    mkdir -p "$W"
    (cd "$W" && ../configure --enable-archs=arm64ec --without-x --disable-tests --enable-winegstreamer)
fi
make -C "$W" -j"$JOBS" \
    libs/winecrt0/arm64ec-windows/libwinecrt0.a \
    dlls/ntdll/arm64ec-windows/libntdll.a \
    dlls/dbghelp/arm64ec-windows/libdbghelp.a

# DXMT's cross file names the toolchain relative to its own source root.
ln -sfn ../toolchains "$R/dxmt/toolchains"
if [ ! -f "$B/build.ninja" ]; then
    # -u IPHONEOS_DEPLOYMENT_TARGET: the workflow exports it for the app build,
    # and with it set Apple's driver compiled meson's build-machine sanity
    # program for iOS ("using sysroot for MacOSX but targeting iPhone" -> no
    # _main). This step is a Windows cross build; that variable must not apply.
    (cd "$R/dxmt" && env -u IPHONEOS_DEPLOYMENT_TARGET \
        SDKROOT="$(xcrun --sdk macosx --show-sdk-path)" \
        meson setup "$B" --cross-file build-arm64ec-win.txt \
            --native-file "$R/build/dxmt-pe/native-osx.txt" \
            --buildtype release -Dwine_build_path="$W" \
            -Dmetal_std="$METAL_STD" -Dmetal_sdk=iphoneos -Dmetal_target="$METAL_TARGET")
fi
env -u IPHONEOS_DEPLOYMENT_TARGET SDKROOT="$(xcrun --sdk macosx --show-sdk-path)" \
    ninja -C "$B" src/d3d11/d3d11.dll

echo "d3d11.dll: committed $(wc -c < "$OUT") bytes, freshly built $(wc -c < "$B/src/d3d11/d3d11.dll") bytes"
"$TC/arm64ec-w64-mingw32-strip" --strip-debug -o "$OUT.tmp" "$B/src/d3d11/d3d11.dll"
mv "$OUT.tmp" "$OUT"
echo "d3d11.dll: shipped $(wc -c < "$OUT") bytes (-std=$METAL_STD --target=$METAL_TARGET)"

python3 "$R/build/dxmt-pe/verify-metallib.py" "$OUT"
