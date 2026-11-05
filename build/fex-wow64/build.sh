#!/bin/bash
# Configure and build FEX's aarch64 WOW64 module (libwow64fex.dll,
# shipped as xtajit.dll), the CPU backend wow64.dll loads for 32-bit x86
# programs. See docs/WOW64.md, "Building". The options are the iOS host build's:
# FEX_IOS_HOST_BUILD turns on the 32-bit guest window (ENABLE_GUEST_WINDOW),
# which FEX refuses for the ARM64EC module.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin:$PATH"
# The workflow exports IPHONEOS_DEPLOYMENT_TARGET/SDKROOT for the iOS half; the
# PE cross build here is a macOS-host build and must not inherit them.
unset IPHONEOS_DEPLOYMENT_TARGET SDKROOT CC CXX
python3 "$R/build/fex-ios/prepare.py" "$R"   # the low-RIP guard patch, idempotent
B="$R/FEX/build-wow64"
# Madeira's own FEX changes (build/fex-patches/README.md).
bash "$R/build/fex-patches/apply.sh"
TOOLCHAIN="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal"
# Keep package discovery inside the MinGW sysroot; host Homebrew packages can
# expose macOS import libraries that a PE target cannot use.
# Reconfigure on each run so a partial configure and changed build options
# recover (an existing CMakeCache.txt alone does not prove a complete one).
cmake -S "$R/FEX" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$R/FEX/Data/CMake/toolchain_mingw.cmake" \
    -DMINGW_TRIPLE=aarch64-w64-mingw32 \
    -DCMAKE_FIND_ROOT_PATH="$TOOLCHAIN/aarch64-w64-mingw32" \
    -DCMAKE_DISABLE_FIND_PACKAGE_fmt=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_range-v3=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_unordered_dense=ON \
    -DFEX_IOS_HOST_BUILD=ON -DCMAKE_C_FLAGS=-DFEX_IOS_HOST \
    -DCMAKE_CXX_FLAGS=-DFEX_IOS_HOST -DCMAKE_ASM_FLAGS=-DFEX_IOS_HOST \
    -DENABLE_LTO=OFF -DENABLE_ASSERTIONS=OFF -DENABLE_JEMALLOC_GLIBC_ALLOC=OFF \
    -DBUILD_TESTING=OFF -DBUILD_FEXCONFIG=OFF -DTUNE_ARCH=generic -DTUNE_CPU=none \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_SHARED_LINKER_FLAGS="-static -static-libgcc -static-libstdc++ -Wl,--file-alignment=4096,/mllvm:-align-loops=1,-Map,$B/wow64fex.map"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
cmake --build "$B" --target wow64fex --parallel "$JOBS"
cp "$B/Bin/libwow64fex.dll" "$R/app/Madeira/aarch64-windows/xtajit.dll" && ls -l "$R/app/Madeira/aarch64-windows/xtajit.dll"
