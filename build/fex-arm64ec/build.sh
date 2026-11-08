#!/bin/bash
# Configure and build the ARM64EC FEX module (libarm64ecfex.dll,
# shipped as xtajit64.dll), the CPU backend used by Wine's ARM64EC ntdll.
# Keep this build isolated from the iOS SDK environment and hermetic against
# host Homebrew packages; the latter can supply unusable macOS import libraries.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin:$PATH"
unset IPHONEOS_DEPLOYMENT_TARGET SDKROOT CC CXX
# Apply the repository's FEX changes and the iOS low-RIP guard, both idempotently.
bash "$R/build/fex-patches/apply.sh"
python3 "$R/build/fex-ios/prepare.py" "$R"
B="$R/FEX/build-arm64ec"
TOOLCHAIN="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal"
# Reconfigure on each run so a partial configure and changed build options
# recover (an existing CMakeCache.txt alone does not prove a complete one).
cmake -S "$R/FEX" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$R/FEX/Data/CMake/toolchain_mingw.cmake" \
    -DMINGW_TRIPLE=arm64ec-w64-mingw32 -DTUNE_CPU=cortex-a78 \
    -DCMAKE_FIND_ROOT_PATH="$TOOLCHAIN/arm64ec-w64-mingw32" \
    -DCMAKE_DISABLE_FIND_PACKAGE_fmt=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_range-v3=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_unordered_dense=ON \
    -DFEX_IOS_HOST_BUILD=ON -DCMAKE_C_FLAGS=-DFEX_IOS_HOST=1 \
    -DCMAKE_CXX_FLAGS=-DFEX_IOS_HOST=1 -DCMAKE_ASM_FLAGS=-DFEX_IOS_HOST=1 \
    -DENABLE_LTO=OFF -DENABLE_FEX_ALLOCATOR=ON -DENABLE_JEMALLOC_GLIBC_ALLOC=ON -DENABLE_OFFLINE_RUNTIME=ON \
    -DBUILD_FEXCONFIG=ON -DENABLE_CLANG_THUNKS=ON -DENABLE_CCACHE=ON \
    -DBUILD_TESTING=OFF -DBUILD_THUNKS=OFF -DENABLE_ASSERTIONS=OFF \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
cmake --build "$B" --target arm64ecfex --parallel "$JOBS"
mkdir -p "$R/app/Madeira/arm64ec-windows"
cp "$B/Bin/libarm64ecfex.dll" "$R/app/Madeira/arm64ec-windows/xtajit64.dll" && ls -l "$R/app/Madeira/arm64ec-windows/xtajit64.dll"
