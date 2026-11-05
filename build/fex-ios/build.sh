#!/bin/bash
# Configure and build the FEXCore static libraries the app links
# (FEX/build-ios/FEXCore/Source/*.a and External/*). Options mirror the
# development build's CMakeCache.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python3 "$R/build/fex-ios/prepare.py" "$R"
B="$R/FEX/build-ios"
cmake -S "$R/FEX" -B "$B" -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_SYSTEM_PROCESSOR=arm64 -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_SYSROOT=iphoneos -DCMAKE_OSX_DEPLOYMENT_TARGET="${IPHONEOS_DEPLOYMENT_TARGET:-17.0}" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_DISABLE_FIND_PACKAGE_fmt=ON -DCMAKE_DISABLE_FIND_PACKAGE_range-v3=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_unordered_dense=ON \
    -DBUILD_TESTING=OFF -DBUILD_THUNKS=OFF -DBUILD_FEXCONFIG=OFF -DBUILD_FEX_LINUX_TESTS=OFF \
    -DENABLE_FEX_ALLOCATOR=OFF -DENABLE_ASSERTIONS=OFF -DENABLE_CLANG_THUNKS=ON -DENABLE_CCACHE=ON \
    -DTUNE_CPU=cortex-a78
# FEX_CONFIGURE_ONLY=1: generate headers for app-only CI without compiling libraries.
if [ "${FEX_CONFIGURE_ONLY:-0}" = "1" ]; then
    echo "configure-only: generated headers; skipping compilation"
    exit 0
fi
cmake --build "$B" --target FEXCore FEXCore_Base JemallocLibs fmt cephes_128bit softfloat_3e xxhash \
    --parallel "${JOBS:-$(sysctl -n hw.ncpu)}"
ls "$B/FEXCore/Source/"*.a
