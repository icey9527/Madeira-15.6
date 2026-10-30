#!/usr/bin/env python3
"""从生产 ResizeBuffers 抽取函数体，验证分配失败/初始化失败的事务性与 count 语义。

harness 以 EnableMetalFX=true 实例化（走 Com/Reference 真分支），因此同时覆盖
MetalFX 分配与 scaler 创建失败；编译期 false 分支在 C++ 模板里本来就不会实例化。
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src_path = root / "dxmt/src/d3d11/d3d11_swapchain.cpp"
source = src_path.read_text(encoding="utf-8")
start = source.index("ResizeBuffers(UINT BufferCount, UINT Width, UINT Height, DXGI_FORMAT Format,")
brace = source.index("{", start)
depth = 1
end = brace + 1
while depth:
    if source[end] == "{": depth += 1
    elif source[end] == "}": depth -= 1
    end += 1
body = source[brace + 1:end - 1]

# ctor 失败必须变成 HRESULT，且 MetalFX scaler 失败在 commit 之前返回。
for needle in ("init_hr_ = ResizeBuffers(0, desc_.Width",
               "HRESULT InitResult() const { return init_hr_; }",
               "if (FAILED(init_hr_))",
               "chain->Release();"):
    if needle not in source:
        raise SystemExit("swapchain 初始化失败路径缺失: " + needle)
if source.count("InitResult()") < 3:
    raise SystemExit("CreateSwapChain 的两个模板分支都必须检查 InitResult()")
scaler_fail = source.index("if (!new_scaler)")
commit = source.index("// 提交：此后不再有会失败的分配。")
if scaler_fail > commit:
    raise SystemExit("MetalFX scaler 失败必须在 commit 之前返回")

compiler = os.environ.get("CXX") or shutil.which("g++")
if not compiler and Path("C:/TDM-GCC-32/bin/g++.exe").exists():
    compiler = "C:/TDM-GCC-32/bin/g++.exe"
if not compiler:
    raise SystemExit("需要 C++ 编译器，未执行测试")

program = r'''
#include <cstddef>
#include <cstdio>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

using UINT = unsigned;
using HRESULT = int;
using DXGI_FORMAT = int;
struct HWND__ {}; using HWND = HWND__ *;
constexpr int S_OK = 0, E_FAIL = -1, DXGI_ERROR_INVALID_CALL = -2, DXGI_FORMAT_UNKNOWN = 0;
constexpr unsigned DXGI_SWAP_EFFECT_DISCARD = 0, DXGI_SWAP_EFFECT_SEQUENTIAL = 1;
constexpr bool EnableMetalFX = true;
#define FAILED(hr) ((hr) < 0)
#define D3D11_ASSERT(x) ((void)0)
#define ERR(...) ((void)0)

struct Tex { int pixelFormat() const { return 1; } };
struct D3D11ResourceCommon {
  int priv = 0;
  void AddRefPrivate() { ++priv; }
  void Release() {}
  Tex *texture() { static Tex t; return &t; }
};
using ID3D11Texture2D1 = D3D11ResourceCommon;

template <typename T, bool = false>
struct Com {
  T *p = nullptr;
  Com() = default;
  Com(Com &&o) : p(o.p) { o.p = nullptr; }
  Com &operator=(Com &&o) { p = o.p; o.p = nullptr; return *this; }
  Com(const Com &) = delete;
  Com &operator=(const Com &) = delete;
  T *operator->() const { return p; }
  explicit operator bool() const { return p != nullptr; }
  bool operator!=(std::nullptr_t) const { return p != nullptr; }
};

struct D3D11_TEXTURE2D_DESC1 { UINT Width = 0, Height = 0; DXGI_FORMAT Format = 0; };

namespace wsi { inline void getWindowSize(HWND, UINT *w, UINT *h) { *w = 0; *h = 0; } }
enum WMTPixelFormat { WMTPixelFormatInvalid = -1, WMTPixelFormatBGRA8Unorm = 1 };
inline WMTPixelFormat ConvertSwapChainFormat(DXGI_FORMAT) { return WMTPixelFormatBGRA8Unorm; }

struct WMTFXSpatialScalerInfo {
  UINT input_width = 0, input_height = 0, output_width = 0, output_height = 0;
  int color_format = 0, output_format = 0;
};
namespace WMT {
struct FXSpatialScaler {};
template <typename T> struct Reference {
  T *p = nullptr;
  Reference() = default;
  Reference(Reference &&) = default;
  Reference &operator=(Reference &&) = default;
  explicit operator bool() const { return p != nullptr; }
};
}

struct Dev;
namespace dxmt {
inline int g_created = 0, g_fail_at = 0;
inline HRESULT CreateDeviceTexture2D(Dev *, D3D11_TEXTURE2D_DESC1 *, void *, ID3D11Texture2D1 **out);
}
struct MTLDeviceMock {
  bool scaler_fail = false;
  WMT::Reference<WMT::FXSpatialScaler> newSpatialScaler(WMTFXSpatialScalerInfo) {
    if (scaler_fail) return {};
    static WMT::FXSpatialScaler s;
    WMT::Reference<WMT::FXSpatialScaler> r;
    r.p = &s;
    return r;
  }
};
struct Dev {
  MTLDeviceMock mtl;
  MTLDeviceMock GetMTLDevice() { return mtl; }
};
namespace dxmt {
inline HRESULT CreateDeviceTexture2D(Dev *, D3D11_TEXTURE2D_DESC1 *, void *, ID3D11Texture2D1 **out) {
  ++g_created;
  if (g_fail_at && g_created == g_fail_at) return E_FAIL;
  *out = new D3D11ResourceCommon();
  return S_OK;
}
}

struct Desc {
  UINT Width = 800, Height = 600;
  DXGI_FORMAT Format = 3;
  UINT BufferCount = 2;
  unsigned SwapEffect = DXGI_SWAP_EFFECT_SEQUENTIAL;
};

struct Chain {
  Dev *device_ = nullptr;
  HWND hWnd = nullptr;
  Desc desc_{};
  D3D11_TEXTURE2D_DESC1 backbuffer_desc_{};
  Com<D3D11ResourceCommon, false> backbuffer_;
  std::vector<Com<D3D11ResourceCommon, false>> sequential_backbuffers_;
  Com<D3D11ResourceCommon, false> sequential_rotation_temp_;
  std::conditional<EnableMetalFX, Com<D3D11ResourceCommon>, std::monostate>::type upscaled_backbuffer_;
  std::conditional<EnableMetalFX, WMT::Reference<WMT::FXSpatialScaler>, std::monostate>::type metalfx_scaler;
  float scale_factor = 1.0f;
  int layer_calls = 0;
  void ApplyLayerProps() { ++layer_calls; }

  HRESULT ResizeBuffers(UINT BufferCount, UINT Width, UINT Height, DXGI_FORMAT Format, UINT flags) {
BODY
  }
};

static Dev g_dev;
static Com<D3D11ResourceCommon, false> g_b0, g_e0, g_t0;

static Chain make_chain() {
  Chain c;
  c.device_ = &g_dev;
  c.backbuffer_.p = new D3D11ResourceCommon();
  c.sequential_backbuffers_.push_back(Com<D3D11ResourceCommon, false>{});
  c.sequential_backbuffers_.back().p = new D3D11ResourceCommon();
  c.sequential_rotation_temp_.p = new D3D11ResourceCommon();
  c.backbuffer_desc_.Width = 800;
  c.backbuffer_desc_.Height = 600;
  c.backbuffer_desc_.Format = 3;
  return c;
}

int main() {
  // A: 第 2 次分配（extra）失败，旧状态必须原样保留。
  {
    Chain c = make_chain();
    auto *b0 = c.backbuffer_.p, *e0 = c.sequential_backbuffers_[0].p, *t0 = c.sequential_rotation_temp_.p;
    dxmt::g_created = 0; dxmt::g_fail_at = 2;
    HRESULT hr = c.ResizeBuffers(0, 1024, 768, DXGI_FORMAT_UNKNOWN, 0);
    if (hr != E_FAIL) return 1;
    if (c.desc_.Width != 800 || c.desc_.Height != 600 || c.desc_.BufferCount != 2 || c.desc_.Format != 3) return 2;
    if (c.backbuffer_.p != b0 || c.sequential_backbuffers_.size() != 1 ||
        c.sequential_backbuffers_[0].p != e0 || c.sequential_rotation_temp_.p != t0) return 3;
    if (c.layer_calls != 0) return 4;
  }

  // B: 空链上 0 号分配失败，不能留下半成品。
  {
    Chain c;
    c.device_ = &g_dev;
    c.desc_ = Desc{};
    dxmt::g_created = 0; dxmt::g_fail_at = 1;
    if (c.ResizeBuffers(0, 1024, 768, DXGI_FORMAT_UNKNOWN, 0) != E_FAIL) return 5;
    if (c.backbuffer_.p != nullptr || !c.sequential_backbuffers_.empty() ||
        c.sequential_rotation_temp_.p != nullptr) return 6;
    if (c.layer_calls != 0) return 7;
  }

  // C: count=0 沿用 2，成功后 0 号 + 1 个 extra + scratch + upscaled = 4 次分配。
  {
    Chain c = make_chain();
    dxmt::g_created = 0; dxmt::g_fail_at = 0;
    if (c.ResizeBuffers(0, 1024, 768, DXGI_FORMAT_UNKNOWN, 0) != S_OK) return 8;
    if (c.desc_.BufferCount != 2 || c.desc_.Width != 1024 || c.desc_.Height != 768) return 9;
    if (c.backbuffer_.p == nullptr || c.sequential_backbuffers_.size() != 1 ||
        c.sequential_backbuffers_[0].p == nullptr || c.sequential_rotation_temp_.p == nullptr) return 10;
    if (dxmt::g_created != 4) return 11;
    if (c.layer_calls != 1) return 12;
    if (c.backbuffer_desc_.Width != 1024 || c.backbuffer_desc_.Height != 768) return 13;
  }

  // D: 显式 count=3 -> 0 号 + 2 extra + scratch + upscaled。
  {
    Chain c = make_chain();
    dxmt::g_created = 0; dxmt::g_fail_at = 0;
    if (c.ResizeBuffers(3, 0, 0, DXGI_FORMAT_UNKNOWN, 0) != S_OK) return 14;
    if (c.desc_.BufferCount != 3 || c.sequential_backbuffers_.size() != 2 ||
        c.sequential_rotation_temp_.p == nullptr || dxmt::g_created != 5) return 15;
  }

  // E: 非法 count 不分配、不改状态。
  {
    Chain c = make_chain();
    auto *b0 = c.backbuffer_.p;
    dxmt::g_created = 0; dxmt::g_fail_at = 0;
    if (c.ResizeBuffers(17, 1024, 768, DXGI_FORMAT_UNKNOWN, 0) != DXGI_ERROR_INVALID_CALL) return 16;
    if (dxmt::g_created != 0 || c.backbuffer_.p != b0 || c.desc_.BufferCount != 2) return 17;
  }

  // F: MetalFX scaler 失败必须整体回滚（旧 desc/buffers/scaler 不变）。
  {
    Chain c = make_chain();
    g_dev.mtl.scaler_fail = true;
    auto *b0 = c.backbuffer_.p, *t0 = c.sequential_rotation_temp_.p;
    dxmt::g_created = 0; dxmt::g_fail_at = 0;
    if (c.ResizeBuffers(0, 1024, 768, DXGI_FORMAT_UNKNOWN, 0) != E_FAIL) return 18;
    g_dev.mtl.scaler_fail = false;
    if (c.desc_.Width != 800 || c.desc_.BufferCount != 2) return 19;
    if (c.backbuffer_.p != b0 || c.sequential_rotation_temp_.p != t0) return 20;
    if (c.layer_calls != 0) return 21;
  }

  std::puts("PASS: ResizeBuffers k-th alloc failure/scaler failure keeps old state, count=0 reuse, count2 -> 3 backbuffers+scratch");
  return 0;
}
'''.replace("BODY", body)

with tempfile.TemporaryDirectory(prefix="madeira-resize-") as tmp:
    cpp = Path(tmp) / "resize.cpp"
    exe = Path(tmp) / ("resize.exe" if os.name == "nt" else "resize")
    cpp.write_text(program, encoding="utf-8")
    subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=5)
