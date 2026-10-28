#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 125hz
#
# Build the aarch64-windows PE side of DXMT — d3d11.dll, dxgi.dll,
# winemetal.dll, d3d10core.dll — and stage them into app/Madeira.
#
# Why this exists: those four DLLs used to be committed binaries compiled by
# hand on one machine (README "Build steps", PE side). Everything the D3D11
# front end does lives inside d3d11.dll, so source changes there — including
# every probe and fix under src/d3d11 and src/dxmt — never reached the device.
# The D3D9 half did, because it is linked into libdxmt_combined.a. That split
# is why a stale 2026-10-12 d3d11.dll could ship for weeks: TheShell binds its
# argument buffer at index 30 with a null Metal buffer ([d11-newbuf] NIL
# len=4194304 while the unix side of the same tree builds fine).
#
# The PE is compiled from the same compat sources as the unix library: the
# overlay below writes prepare-compat.py's output over the dxmt submodule, so
# both halves of a build come from one patched tree.
#
# Prerequisites, all present in CI by the time this runs:
#   * toolchains/llvm-mingw-20260421-ucrt-macos-universal/
#   * wine/build-macos (config.h + widl headers; stage `wine`)
#   * meson + ninja (stage `deps`)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/build/dxmt-ios"
DXMT="$ROOT/dxmt"
OUT="$ROOT/app/Madeira/aarch64-windows"
MINGW="$ROOT/toolchains/llvm-mingw-20260421-ucrt-macos-universal"
WINE_BUILD="$ROOT/wine/build-macos"
PE_BUILD="$DXMT/build-pe"
DEPLOYMENT="${IPHONEOS_DEPLOYMENT_TARGET:-15.6}"

say() { printf '\n===== [pe] %s =====\n' "$*"; }

[ -x "$MINGW/bin/aarch64-w64-mingw32-clang" ] \
  || { echo "llvm-mingw missing at $MINGW" >&2; exit 1; }
[ -d "$MINGW/bin" ] || { echo "llvm-mingw bin missing" >&2; exit 1; }
[ -f "$WINE_BUILD/config.h" ] \
  || { echo "wine/build-macos/config.h missing — run the wine stage first" >&2; exit 1; }
command -v meson >/dev/null || { echo "meson missing" >&2; exit 1; }

say "compat sources (same tree the unix library uses)"
# prepare-compat.py only reads the submodule and writes compat-src; it is
# idempotent and already runs for the unix build, so this is cheap.
if [ -f "$DXMT/src/dxmt/dxmt_command_queue.hpp" ]; then
  python3 "$BUILD_DIR/prepare-compat.py" "$DXMT" "$BUILD_DIR/compat-src" "$DEPLOYMENT"
else
  echo "dxmt submodule is not checked out" >&2; exit 1
fi

say "overlay compat sources onto the dxmt submodule"
# The unix build compiles compat-src files one by one; meson cannot, so the
# patched files are written back over the submodule instead. The checkout is
# disposable in CI and the overlay is a plain copy of what prepare-compat.py
# produced from that same tree.
if [ -d "$BUILD_DIR/compat-src" ]; then
  count=0
  while IFS= read -r -d '' rel; do
    rel="${rel#./}"
    mkdir -p "$DXMT/$(dirname "$rel")"
    cp "$BUILD_DIR/compat-src/$rel" "$DXMT/$rel"
    count=$((count + 1))
  done < <(cd "$BUILD_DIR/compat-src" && find . -type f -print0)
  echo "  overlayed $count file(s)"
fi

say "cross file prerequisites"
# build-aarch64-win.txt reaches the toolchain through @GLOBAL_SOURCE_ROOT@
# /toolchains, i.e. relative to the dxmt submodule.
ln -sfn ../toolchains "$DXMT/toolchains"
ln -sfn ../build "$DXMT/build-link" 2>/dev/null || true

export PATH="$MINGW/bin:$PATH"
export SDKROOT="$(xcrun --sdk macosx --show-sdk-path)"
export CCACHE_DIR="${CCACHE_DIR:-$ROOT/.ccache}"
export CCACHE_COMPILERCHECK=content

say "meson configure"
WINE_PATH_ABS="$(cd "$WINE_BUILD" && pwd)"
# --wipe keeps a reused runner checkout honest; setup is cheap next to compile.
meson setup "$PE_BUILD" "$DXMT" \
  --cross-file "$DXMT/build-aarch64-win.txt" \
  --native-file "$DXMT/build-osx.txt" \
  -Dwine_build_path="$WINE_PATH_ABS" \
  --wipe

say "meson compile (only the four DLLs)"
# Only these four are shipped: everything else in the tree (airconv's static
# library, the CLI, the macOS nativemetal backend) is either built for iOS by
# build.sh or needs a windows-hosted LLVM we do not carry here.
PE_TARGETS="src/d3d11/d3d11.dll src/dxgi/dxgi.dll src/winemetal/winemetal.dll src/d3d10/d3d10core.dll"
missing=""
for t in $PE_TARGETS; do
  ninja -C "$PE_BUILD" -t targets all 2>/dev/null | grep -q "^$t:" || missing="$missing $t"
done
if [ -n "$missing" ]; then
  echo "  ninja 里没有这些目标:$missing —— 退回全量编译" >&2
  meson compile -C "$PE_BUILD"
else
  ninja -C "$PE_BUILD" $PE_TARGETS
fi

say "stage the PE DLLs"
mkdir -p "$OUT"
for rel in src/d3d11/d3d11.dll src/dxgi/dxgi.dll src/winemetal/winemetal.dll src/d3d10/d3d10core.dll; do
  src="$PE_BUILD/$rel"
  [ -s "$src" ] || { echo "missing $rel in $PE_BUILD" >&2; exit 1; }
  cp "$src" "$OUT/$(basename "$rel")"
  printf '  %-14s %s bytes\n' "$(basename "$rel")" "$(wc -c < "$OUT/$(basename "$rel")" | tr -d ' ')"
done

say "done"
