#!/bin/bash
# Cross-compile FFmpeg as Windows (x86_64) PE DLLs for Steam's video64.dll.
#
# Steam's client ships a video component (video64.dll) that imports the FFmpeg
# runtime by these exact module names:
#
#   libavcodec-62.dll  libavformat-62.dll  libavfilter-11.dll
#   libavutil-60.dll   libswresample-6.dll libswscale-9.dll
#
# The client packages we pin for Steam (SteamRuntime.swift, three Valve zips from
# client-update.akamai.steamstatic.com) do not contain them, and the client's own
# updater stalls on this port before it would fetch them. Games whose Steam
# integration loads video64.dll then die at startup with
#
#   import_dll Library libavcodec-62.dll ... not found
#
# (NOeSIS01, amehazu, SunriderMask of Arcadius, 2026-10-29).
#
# The sonames come from FFmpeg's major version: 62/62/11/60/6/9 is FFmpeg 8.0.x.
# A different major produces different file names and does not satisfy the
# import, so FFMPEG_VERSION must not be bumped without checking what video64
# asks for.
#
# Licensing: --disable-gpl --disable-nonfree --disable-version3, same as the iOS
# build (build/ffmpeg/build.sh). The result is LGPL-2.1-or-later; its notices are
# staged by build/stage-licenses.sh from the source tree.
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FFMPEG_VERSION=8.0.1
FFMPEG_SHA256="__SHA__"
CACHE="$R/build/ffmpeg-pe/cache"
SRC_DIR="$CACHE/src"
PREFIX="$CACHE/prefix"
OUT="$R/app/Madeira/steam-libs"
TARBALL="$SRC_DIR/ffmpeg-$FFMPEG_VERSION.tar.xz"
URL="https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"

MINGW="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
CROSS="$MINGW/x86_64-w64-mingw32-"

say() { printf '\n===== [ffmpeg-pe] %s =====\n' "$*"; }

[ -x "${CROSS}clang" ] || { echo "llvm-mingw missing at $MINGW" >&2; exit 1; }

say "source tarball"
mkdir -p "$SRC_DIR"
if [ ! -f "$TARBALL" ]; then
  echo "  downloading $URL"
  curl -fL --retry 3 --retry-delay 5 -o "$TARBALL.part" "$URL"
  mv "$TARBALL.part" "$TARBALL"
fi
actual="$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)"
if [ "$FFMPEG_SHA256" = "__SHA__" ]; then
  # Not pinned yet: say what it is and refuse to build from it unless the caller
  # explicitly opts in (CI does that once, so the value can be pinned here).
  echo "  tarball sha256: $actual  <- pin this as FFMPEG_SHA256"
  [ "${FFMPEG_PE_UNPINNED:-0}" = "1" ] || { echo "refusing to build from an unpinned tarball" >&2; exit 1; }
else
  [ "$actual" = "$FFMPEG_SHA256" ] || { echo "tarball sha256 $actual != $FFMPEG_SHA256" >&2; exit 1; }
fi

say "extract"
rm -rf "$CACHE/ffmpeg-$FFMPEG_VERSION"
tar -xJf "$TARBALL" -C "$CACHE"
cd "$CACHE/ffmpeg-$FFMPEG_VERSION"

say "configure (mingw x86_64, shared, LGPL)"
rm -rf "$PREFIX"
# The libraries are what video64 imports; the components are only what a game
# hands to Steam's video path. Keeping them few keeps the DLLs small, and the
# import table is satisfied by the libraries existing at all.
./configure \
  --prefix="$PREFIX" \
  --arch=x86_64 --target-os=mingw32 --cross-prefix="$CROSS" \
  --enable-shared --disable-static \
  --disable-programs --disable-doc --disable-network \
  --disable-gpl --disable-nonfree --disable-version3 \
  --disable-everything \
  --enable-decoder=h264,aac,mpeg1video,mpeg2video,mp2,mp3,pcm_s16le,pcm_s24le \
  --enable-demuxer=mov,mpegts,mpeg,mp3,wav \
  --enable-parser=h264,aac,mpegvideo,mpegaudio \
  --enable-protocol=file \
  --enable-avfilter --enable-swscale --enable-swresample

say "build"
make -j"$(sysctl -n hw.ncpu 2>/dev/null || echo 8)"
make install

say "stage the DLLs"
mkdir -p "$OUT"
rm -f "$OUT"/*.dll
# mingw names them avcodec-62.dll; Steam's component imports libavcodec-62.dll.
# Stage both spellings: the lib-prefixed name satisfies video64's import, and the
# bare one keeps FFmpeg's own inter-DLL imports (avcodec -> avutil-60.dll) happy.
for f in "$PREFIX"/bin/*.dll; do
  base="$(basename "$f")"
  cp "$f" "$OUT/$base"
  case "$base" in
    lib*) : ;;
    *) cp "$f" "$OUT/lib$base" ;;
  esac
  printf '  %-24s %s bytes\n' "$(basename "$f")" "$(wc -c < "$f" | tr -d ' ')"
done
ls -1 "$OUT"/*.dll | wc -l | awk '{print "  staged " $1 " DLL(s)"}'

say "done"
