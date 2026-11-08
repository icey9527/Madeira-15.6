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
# dxmt is vendored in-tree and the iOS port is committed to it (2026-10-29), so
# d3d11.dll and libdxmt_unix.a are built from one tree with no patching step in
# between. Before that, prepare-compat.py had to be replayed over the submodule
# here, in a private directory, because the unix build's output could not be
# patched twice.

if [ ! -f "$W/config.status" ]; then
    mkdir -p "$W"
    # Native CC must be Apple clang: this llvm-mingw toolchain also installs a
    # plain `gcc`, and it sits first on PATH, so configure picked the Windows
    # cross driver for its own host tests and died with "cannot run C compiled
    # programs" the first time the wine build cache missed (2026-10-29).
    # CROSSCC names the arm64ec driver for the cross archs wine builds alongside.
    (cd "$W" && CC=/usr/bin/clang CXX=/usr/bin/clang++ CROSSCC=arm64ec-w64-mingw32-gcc ../configure --enable-archs=arm64ec --without-x --disable-tests --enable-winegstreamer)
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
# BOTH front ends carry an embedded command library, so both have to be built
# with the iOS target. Only d3d11.dll used to be: arm64ec-windows/d3d9.dll was
# whatever the vendored farm shipped, and that one is Apple's macOS build --
# `platform=0x8001 OS=0x81`. A 64-bit d3d9 game (Half-Life's hl1mp_win64.exe)
# then dies at start-up with
#     Failed to create internal command library: Code=1
#     "This library format is not supported on this platform"
# because iOS 15.6's Metal refuses a macOS container. 32-bit d3d9 was never
# affected: it goes through the i386 shim into the unix library, whose shaders
# come from this same iOS-targeted build.
for dll in d3d11 d3d9; do
    env -u IPHONEOS_DEPLOYMENT_TARGET SDKROOT="$(xcrun --sdk macosx --show-sdk-path)" \
        ninja -C "$B" "src/$dll/$dll.dll"

    dst="$R/app/Madeira/arm64ec-windows/$dll.dll"
    echo "$dll.dll: committed $(wc -c < "$dst") bytes, freshly built $(wc -c < "$B/src/$dll/$dll.dll") bytes"
    "$TC/arm64ec-w64-mingw32-strip" --strip-debug -o "$dst.tmp" "$B/src/$dll/$dll.dll"
    mv "$dst.tmp" "$dst"
    echo "$dll.dll: shipped $(wc -c < "$dst") bytes (-std=$METAL_STD --target=$METAL_TARGET)"

    python3 "$R/build/dxmt-pe/verify-metallib.py" "$dst"
done
