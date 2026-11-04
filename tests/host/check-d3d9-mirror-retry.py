#!/usr/bin/env python3
"""Lazy d3d9 sysmem-mirror allocation results: no Wine, no Metal, no device.

Compiles the PRODUCTION bodies of MTLD3D9Texture::ensureMirror,
MTLD3D9CubeTexture::ensureMirror and both restoreMirrorForSource functions,
extracted verbatim from dxmt/src/d3d9/*.cpp, against stubs that model the
guest arena, the device backing pool and the Metal newBuffer thunk.

What it pins (WOW64_DESIGN.md 8.2(c) / 2026-10-29 texture-mirror retry):
  * ensureMirror returns true whenever a mirror is present OR the resource
    legitimately needs none (GPU-only pool, buffer-backed, empty offsets);
  * it returns false ONLY when guest_alloc or newBuffer failed, leaving the
    backing null so the unix epilogue can report ARENA_EXHAUSTED and the shim
    can grow once and replay the call;
  * a failed newBuffer frees the guest backing instead of leaking it;
  * restoreMirrorForSource returns false only for that allocation failure; a
    GPU readback failure stays best-effort (returns true) as before.

It also statically checks the declarations/overrides and that every call site
in dxmt/src/d3d9 either consumes the bool or is the one documented
best-effort call inside stageDirtyRegionUpload.

Run:  python -I tests/host/check-d3d9-mirror-retry.py
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
D3D9 = ROOT / "dxmt/src/d3d9"

TEXTURE_CPP = D3D9 / "d3d9_texture.cpp"
CUBE_CPP = D3D9 / "d3d9_cube_texture.cpp"
TEXTURE_HPP = D3D9 / "d3d9_texture.hpp"
CUBE_HPP = D3D9 / "d3d9_cube_texture.hpp"
SURFACE_HPP = D3D9 / "d3d9_surface.hpp"
SURFACE_CPP = D3D9 / "d3d9_surface.cpp"
DEVICE_CPP = D3D9 / "d3d9_device.cpp"

COMPILERS = [
    os.environ.get("CXX", ""),
    "C:/TDM-GCC-32/bin/g++.exe",
    "g++",
]


def extract_body(path: Path, signature: str) -> str:
    """Return the text between the matching braces of `signature ... {`."""
    text = path.read_text(encoding="utf-8")
    at = text.index(signature)
    brace = text.index("{", at)
    depth = 0
    i = brace
    state = None  # None, 'line', 'block', 'str', 'chr'
    while i < len(text):
        c = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        prev = text[i - 1] if i else ""
        if state == "line":
            if c == "\n":
                state = None
        elif state == "block":
            if c == "*" and nxt == "/":
                state = None
                i += 1
        elif state == "str":
            if c == "\\":
                i += 1
            elif c == '"':
                state = None
        elif state == "chr":
            if c == "\\":
                i += 1
            elif c == "'":
                state = None
        else:
            if c == "/" and nxt == "/":
                state = "line"
                i += 1
            elif c == "/" and nxt == "*":
                state = "block"
                i += 1
            elif c == '"':
                state = "str"
            elif c == "'" and prev != "\\":
                state = "chr"
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[brace + 1 : i]
        i += 1
    raise RuntimeError(f"unterminated body for {signature}")


STUB = r'''
#include <bitset>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

typedef unsigned long DWORD;
typedef unsigned int UINT;
typedef int D3DPOOL;
typedef unsigned int D3DFORMAT;

#define D3DPOOL_DEFAULT 1
#define D3DUSAGE_DYNAMIC 0x00000002u

enum WMTResourceOptions {
  WMTResourceCPUCacheModeDefaultCache = 1,
  WMTResourceStorageModeShared = 2,
};

namespace WMT {
struct Buffer {};
template <class T>
struct Reference {
  T *p = nullptr;
  bool operator==(std::nullptr_t) const { return p == nullptr; }
  bool operator!=(std::nullptr_t) const { return p != nullptr; }
};
} // namespace WMT

struct WMTBufferInfo {
  uint32_t length = 0;
  uint32_t options = 0;
  struct Memory {
    void *ptr = nullptr;
    void set(void *p) { ptr = p; }
  } memory;
};

namespace str {
template <class... A>
std::string format(A &&...) {
  return {};
}
} // namespace str

struct Logger {
  template <class... A>
  static void warn(A &&...) {}
};

static int g_alloc_calls = 0;
static int g_alloc_ok = 1;
static int g_newbuf_ok = 1;
static int g_acquire_ok = 0;
static int g_readback_ok = 1;
static int g_freed = 0;
static int g_patches = 0;
static int g_3dc = 0;
static unsigned char g_buf[1 << 16];
static unsigned char g_pool_buf[1 << 16];

static size_t guest_page_size() { return 16384; }

static void *guest_alloc(size_t size, size_t) {
  ++g_alloc_calls;
  if (!g_alloc_ok || size > sizeof(g_buf))
    return nullptr;
  return g_buf;
}

static void guest_free(void *ptr) {
  if (ptr)
    ++g_freed;
}

static bool Is3DcFormat(D3DFORMAT) { return g_3dc != 0; }

static uint32_t D3DFormatLockPitch(D3DFORMAT, UINT width) { return width * 4u; }

struct Surface {
  void patchMirror(void *, uint32_t) { ++g_patches; }
};
using MTLD3D9Surface = Surface;

struct Level {
  Surface s;
  Surface *operator->() { return &s; }
  Surface *ptr() { return &s; }
};

struct MetalDevice {
  WMT::Reference<WMT::Buffer> newBuffer(WMTBufferInfo &) {
    WMT::Reference<WMT::Buffer> ref;
    static WMT::Buffer keep;
    if (g_newbuf_ok)
      ref.p = &keep;
    return ref;
  }
};

struct Device {
  MetalDevice md;
  MetalDevice &metalDevice() { return md; }
  bool acquireBufferBacking(size_t, WMT::Reference<WMT::Buffer> &out, uint64_t &, void *&,
                            void *&backing) {
    if (!g_acquire_ok)
      return false;
    static WMT::Buffer keep;
    out.p = &keep;
    backing = g_pool_buf;
    return true;
  }
  bool readbackSurfaceMirrors(Surface **, size_t) { return g_readback_ok != 0; }
};

struct DxmtBuf {
  void *b = nullptr;
  void *buffer() { return b; }
};
struct DxmtTex {
  DxmtBuf cur;
  DxmtBuf *current() { return &cur; }
};

static Device g_dev;

struct Tex {
  DWORD m_usage = 0;
  D3DPOOL m_pool = 2; /* any non-DEFAULT pool: MANAGED needs a mirror */
  D3DFORMAT m_format = 0;
  UINT m_width = 64;
  UINT m_height = 64;
  DxmtTex *m_dxmtTexture = nullptr;
  std::vector<size_t> m_mirrorOffsets;
  void *m_mirrorBacking = nullptr;
  WMT::Reference<WMT::Buffer> m_mirrorBuffer;
  Device *m_device = &g_dev;
  std::vector<Level> m_levels;
  uint32_t m_mirror_stale_mask = 0;
  uint32_t m_mirror_download_count = 0;

  bool ensureMirror() {@TEX_ENSURE@}
  bool restoreMirrorForSource() {@TEX_RESTORE@}
};

struct Cube {
  DWORD m_usage = 0;
  D3DPOOL m_pool = D3DPOOL_DEFAULT;
  D3DFORMAT m_format = 0;
  UINT m_edge_length = 64;
  uint32_t m_level_count = 1;
  bool m_needs_mirror = false;
  std::vector<size_t> m_mirrorOffsets;
  void *m_mirrorBacking = nullptr;
  WMT::Reference<WMT::Buffer> m_mirrorBuffer;
  Device *m_device = &g_dev;
  std::vector<Level> m_levels;
  std::bitset<128> m_staleSubres;
  uint32_t m_mirror_download_count = 0;

  bool ensureMirror() {@CUBE_ENSURE@}
  bool restoreMirrorForSource() {@CUBE_RESTORE@}
};

static int g_fails = 0;
static void check(bool ok, const char *name) {
  std::printf("%s %s\n", ok ? "PASS" : "FAIL", name);
  if (!ok)
    ++g_fails;
}

static void reset() {
  g_alloc_calls = 0;
  g_alloc_ok = 1;
  g_newbuf_ok = 1;
  g_acquire_ok = 0;
  g_readback_ok = 1;
  g_freed = 0;
  g_patches = 0;
  g_3dc = 0;
}

int main() {
  /* ---- 2D ensureMirror ---- */
  reset();
  {
    Tex t;
    t.m_mirrorBacking = g_buf;
    check(t.ensureMirror(), "2d: already-allocated early-out is success");
    check(g_alloc_calls == 0, "2d: already-allocated does not allocate");
  }
  reset();
  {
    Tex t;
    t.m_pool = D3DPOOL_DEFAULT;
    t.m_usage = 0;
    t.m_mirrorOffsets = {0, 4096};
    check(t.ensureMirror(), "2d: GPU-only DEFAULT needs no mirror");
    check(g_alloc_calls == 0, "2d: GPU-only DEFAULT does not allocate");
  }
  reset();
  {
    Tex t;
    static DxmtTex dx;
    dx.cur.b = g_pool_buf;
    t.m_dxmtTexture = &dx;
    t.m_mirrorOffsets = {0, 4096};
    check(t.ensureMirror(), "2d: buffer-backed texture needs no mirror");
    check(g_alloc_calls == 0, "2d: buffer-backed does not allocate");
  }
  reset();
  {
    Tex t;
    check(t.ensureMirror(), "2d: empty mirror offsets is success");
    t.m_mirrorOffsets = {0, 0};
    check(t.ensureMirror(), "2d: zero-byte mirror is success");
  }
  reset();
  {
    Tex t;
    t.m_mirrorOffsets = {0, 4096};
    g_acquire_ok = 1;
    check(t.ensureMirror(), "2d: backing-pool hit is success");
    check(t.m_mirrorBacking == g_pool_buf, "2d: backing-pool hit sets the backing");
    check(g_alloc_calls == 0, "2d: backing-pool hit skips guest_alloc");
  }
  reset();
  {
    Tex t;
    t.m_mirrorOffsets = {0, 4096};
    t.m_levels.resize(1);
    g_alloc_ok = 0;
    check(!t.ensureMirror(), "2d: guest_alloc failure reports false");
    check(t.m_mirrorBacking == nullptr, "2d: guest_alloc failure leaves no backing");
  }
  reset();
  {
    Tex t;
    t.m_mirrorOffsets = {0, 4096};
    t.m_levels.resize(1);
    g_newbuf_ok = 0;
    check(!t.ensureMirror(), "2d: newBuffer failure reports false");
    check(t.m_mirrorBacking == nullptr, "2d: newBuffer failure clears the backing");
    check(g_freed == 1, "2d: newBuffer failure frees the guest backing");
  }
  reset();
  {
    Tex t;
    t.m_mirrorOffsets = {0, 4096};
    t.m_levels.resize(1);
    check(t.ensureMirror(), "2d: cold allocation succeeds");
    check(t.m_mirrorBacking == g_buf, "2d: cold allocation sets the backing");
    check(g_patches == 1, "2d: cold allocation patches every level");
  }

  /* ---- cube ensureMirror ---- */
  reset();
  {
    Cube c;
    check(c.ensureMirror(), "cube: needs_mirror=false is success");
    c.m_needs_mirror = true;
    check(c.ensureMirror(), "cube: empty offsets is success");
  }
  reset();
  {
    Cube c;
    c.m_needs_mirror = true;
    c.m_mirrorOffsets = {0, 4096};
    g_acquire_ok = 1;
    check(c.ensureMirror(), "cube: backing-pool hit is success");
    check(c.m_mirrorBacking == g_pool_buf, "cube: backing-pool hit sets the backing");
  }
  reset();
  {
    Cube c;
    c.m_needs_mirror = true;
    c.m_mirrorOffsets = {0, 4096};
    c.m_levels.resize(1);
    g_alloc_ok = 0;
    check(!c.ensureMirror(), "cube: guest_alloc failure reports false");
  }
  reset();
  {
    Cube c;
    c.m_needs_mirror = true;
    c.m_mirrorOffsets = {0, 4096};
    c.m_levels.resize(1);
    g_newbuf_ok = 0;
    check(!c.ensureMirror(), "cube: newBuffer failure reports false");
    check(c.m_mirrorBacking == nullptr, "cube: newBuffer failure clears the backing");
    check(g_freed == 1, "cube: newBuffer failure frees the guest backing");
  }
  reset();
  {
    Cube c;
    c.m_needs_mirror = true;
    c.m_mirrorOffsets = {0, 4096};
    c.m_levels.resize(1);
    check(c.ensureMirror(), "cube: cold allocation succeeds");
    check(g_patches == 1, "cube: cold allocation patches every face");
  }

  /* ---- restoreMirrorForSource ---- */
  reset();
  {
    Tex t;
    t.m_mirrorOffsets = {0, 4096};
    check(t.restoreMirrorForSource(), "2d restore: nothing stale is success");
    t.m_mirror_stale_mask = 1u;
    g_alloc_ok = 0;
    check(!t.restoreMirrorForSource(), "2d restore: allocation failure reports false");
  }
  reset();
  {
    Tex t;
    t.m_mirrorOffsets = {0, 4096};
    t.m_mirror_stale_mask = 1u;
    t.m_levels.resize(1);
    g_readback_ok = 0;
    check(t.restoreMirrorForSource(), "2d restore: readback failure stays best-effort");
    check(t.m_mirror_stale_mask == 1u, "2d restore: readback failure keeps stale bits");
  }
  reset();
  {
    Tex t;
    t.m_mirrorOffsets = {0, 4096};
    t.m_mirror_stale_mask = 1u;
    t.m_levels.resize(1);
    check(t.restoreMirrorForSource(), "2d restore: success");
    check(t.m_mirror_stale_mask == 0u, "2d restore: success clears stale bits");
  }
  reset();
  {
    Cube c;
    check(c.restoreMirrorForSource(), "cube restore: nothing stale is success");
    c.m_staleSubres.set(0);
    g_alloc_ok = 0;
    c.m_needs_mirror = true;
    c.m_mirrorOffsets = {0, 4096};
    check(!c.restoreMirrorForSource(), "cube restore: allocation failure reports false");
  }
  reset();
  {
    Cube c;
    c.m_needs_mirror = true;
    c.m_mirrorOffsets = {0, 4096};
    c.m_levels.resize(1);
    c.m_staleSubres.set(0);
    g_readback_ok = 0;
    check(c.restoreMirrorForSource(), "cube restore: readback failure stays best-effort");
  }
  reset();
  {
    Cube c;
    c.m_needs_mirror = true;
    c.m_mirrorOffsets = {0, 4096};
    c.m_levels.resize(1);
    c.m_staleSubres.set(0);
    check(c.restoreMirrorForSource(), "cube restore: success");
    check(c.m_staleSubres.none(), "cube restore: success clears stale bits");
  }

  return g_fails == 0 ? 0 : 1;
}
'''


def strip_comments(text: str) -> str:
    """Remove // and /* */ comments, keeping strings and newlines."""
    out = []
    i = 0
    state = None
    while i < len(text):
        c = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "line":
            if c == "\n":
                state = None
                out.append(c)
        elif state == "block":
            if c == "*" and nxt == "/":
                state = None
                i += 1
            elif c == "\n":
                out.append(c)
        elif state == "str":
            out.append(c)
            if c == "\\":
                if nxt:
                    out.append(nxt)
                    i += 1
            elif c == '"':
                state = None
        elif state == "chr":
            out.append(c)
            if c == "\\":
                if nxt:
                    out.append(nxt)
                    i += 1
            elif c == "'":
                state = None
        else:
            if c == "/" and nxt == "/":
                state = "line"
                i += 1
            elif c == "/" and nxt == "*":
                state = "block"
                i += 1
            else:
                out.append(c)
                if c == '"':
                    state = "str"
                elif c == "'":
                    state = "chr"
        i += 1
    return "".join(out)


def strip_comment_lines(text: str):
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        yield lineno, line


def static_checks():
    problems = []

    def need(cond, msg):
        if not cond:
            problems.append(msg)

    surface_hpp = SURFACE_HPP.read_text(encoding="utf-8")
    texture_hpp = TEXTURE_HPP.read_text(encoding="utf-8")
    cube_hpp = CUBE_HPP.read_text(encoding="utf-8")
    texture_cpp = TEXTURE_CPP.read_text(encoding="utf-8")

    need("virtual bool ensureMirror() = 0;" in surface_hpp,
         "D9LazyMirrorHost::ensureMirror is not declared bool")
    need("bool ensureHostMirror();" in surface_hpp,
         "MTLD3D9Surface::ensureHostMirror is not declared bool")
    need(texture_hpp.count("bool ensureMirror() override;") == 1,
         "MTLD3D9Texture::ensureMirror is not a bool override")
    need(texture_hpp.count("bool restoreMirrorForSource();") == 1,
         "MTLD3D9Texture::restoreMirrorForSource is not declared bool")
    need(cube_hpp.count("bool ensureMirror() override;") == 1,
         "MTLD3D9CubeTexture::ensureMirror is not a bool override")
    need(cube_hpp.count("bool restoreMirrorForSource();") == 1,
         "MTLD3D9CubeTexture::restoreMirrorForSource is not declared bool")

    need("#include <chrono>" not in texture_cpp and "#include <thread>" not in texture_cpp,
         "d3d9_texture.cpp still includes chrono/thread")
    need("sleep_for" not in texture_cpp,
         "d3d9_texture.cpp still sleeps inside ensureMirror")

    call_files = [TEXTURE_CPP, CUBE_CPP, SURFACE_CPP, DEVICE_CPP]
    ignored = []
    for path in call_files:
        text = path.read_text(encoding="utf-8")
        for lineno, line in strip_comment_lines(text):
            for sym in ("ensureMirror", "ensureHostMirror", "restoreMirrorForSource"):
                if sym + "(" not in line:
                    continue
                if "::" + sym in line:  # definition
                    continue
                if line.lstrip().startswith("return "):
                    continue
                if "!" in line:  # if (!f()) or if (cond && !f())
                    continue
                ignored.append(f"{path.name}:{lineno}: {line.strip()}")

    need(len(ignored) == 1 and ignored[0].startswith("d3d9_texture.cpp:"),
         "unexpected unconsumed mirror call(s): " + "; ".join(ignored))
    if ignored:
        need("restoreMirrorForSource();" in ignored[0],
             "the one allowed ignored call is not restoreMirrorForSource")
        texture_lines = texture_cpp.splitlines()
        lineno = int(ignored[0].split(":")[1])
        window = "\n".join(texture_lines[max(0, lineno - 15) : lineno])
        need("stageDirtyRegionUpload" in window,
             "the ignored restoreMirrorForSource is not inside stageDirtyRegionUpload")

    # UpdateSurface replay idempotency: the DYNAMIC destination mirror must be
    # ensured once, before the first stageTextureUpload, so an OOM aborts before
    # any upload is queued. After the upload there must be reads only.
    try:
        surface_body = extract_body(DEVICE_CPP, "MTLD3D9Device::UpdateSurface(")
    except (ValueError, RuntimeError) as exc:
        need(False, f"cannot extract UpdateSurface: {exc}")
        surface_body = ""
    if surface_body:
        code = strip_comments(surface_body)
        ensure_at = code.find("dst->ensureHostMirror()")
        stage_at = code.find("stageTextureUpload(\n")
        need(code.count("dst->ensureHostMirror()") == 1,
             "UpdateSurface must ensure the DYNAMIC dst mirror exactly once")
        need("!dst->ensureHostMirror()" in code,
             "UpdateSurface must abort on a failed DYNAMIC dst mirror")
        need(ensure_at != -1 and stage_at != -1 and ensure_at < stage_at,
             "UpdateSurface ensures the DYNAMIC dst mirror after (or without) the upload")
        if stage_at != -1:
            need("ensureHostMirror" not in code[stage_at:],
                 "UpdateSurface still ensures the dst mirror after stageTextureUpload")
        dynamic_at = code.find("const bool dynamic_dst")
        need(dynamic_at != -1 and dynamic_at < stage_at,
             "UpdateSurface dynamic_dst is not decided before the upload")

    return problems


def find_compiler():
    for cand in COMPILERS:
        if not cand:
            continue
        found = shutil.which(cand) if os.path.basename(cand) == cand else (cand if Path(cand).is_file() else None)
        if found:
            return found
    return None


def main():
    problems = static_checks()
    for p in problems:
        print("FAIL static: " + p)
    if problems:
        return 1
    print("PASS static: declarations, overrides and all call sites consume the bool")

    tex_ensure = extract_body(TEXTURE_CPP, "MTLD3D9Texture::ensureMirror()")
    tex_restore = extract_body(TEXTURE_CPP, "MTLD3D9Texture::restoreMirrorForSource()")
    cube_ensure = extract_body(CUBE_CPP, "MTLD3D9CubeTexture::ensureMirror()")
    cube_restore = extract_body(CUBE_CPP, "MTLD3D9CubeTexture::restoreMirrorForSource()")

    harness = (
        STUB.replace("@TEX_ENSURE@", tex_ensure)
        .replace("@TEX_RESTORE@", tex_restore)
        .replace("@CUBE_ENSURE@", cube_ensure)
        .replace("@CUBE_RESTORE@", cube_restore)
    )

    cxx = find_compiler()
    if not cxx:
        print("FAIL: no compiler (set CXX or install C:/TDM-GCC-32/bin/g++.exe)")
        return 1

    with tempfile.TemporaryDirectory(prefix="d3d9-mirror-") as tmp:
        tmp_path = Path(tmp)
        cpp = tmp_path / "mirror_retry.cpp"
        exe = tmp_path / "mirror_retry.exe"
        cpp.write_text(harness, encoding="utf-8")
        build = subprocess.run(
            [cxx, "-std=c++17", "-O0", "-w", str(cpp), "-o", str(exe)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            print("FAIL build:")
            print(build.stdout)
            print(build.stderr)
            return 1
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        print(run.stdout, end="")
        if run.returncode != 0:
            print(run.stderr, end="")
            print("FAIL: mirror retry semantics")
            return 1
        print("PASS: production ensureMirror/restoreMirrorForSource semantics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
