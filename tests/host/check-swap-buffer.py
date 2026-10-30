#!/usr/bin/env python3
"""从生产 GetBuffer 提取函数体，检查零/非零索引、空输出指针与 SEQUENTIAL extra。"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "dxmt/src/d3d11/d3d11_swapchain.cpp").read_text(encoding="utf-8")
start = source.index("GetBuffer(UINT buffer_idx, REFIID riid, void **surface) final {")
brace = source.index("{", start)
depth = 1
end = brace + 1
while depth:
    if source[end] == "{": depth += 1
    elif source[end] == "}": depth -= 1
    end += 1
body = source[brace + 1:end - 1]
compiler = os.environ.get("CXX") or shutil.which("g++")
if not compiler and Path("C:/TDM-GCC-32/bin/g++.exe").exists():
    compiler = "C:/TDM-GCC-32/bin/g++.exe"
if not compiler:
    raise SystemExit("需要 C++ 编译器，未执行测试")
program = r'''
#include <atomic>
#include <cstddef>
#include <cstdio>
#include <vector>
using UINT = unsigned;
using REFIID = int;
using HRESULT = int;
constexpr int E_POINTER=-1, DXGI_ERROR_INVALID_CALL=-2, DXGI_ERROR_UNSUPPORTED=-3;
constexpr unsigned DXGI_SWAP_EFFECT_DISCARD=0, DXGI_SWAP_EFFECT_SEQUENTIAL=1,
    DXGI_SWAP_EFFECT_FLIP_DISCARD=4;
#define ERR(...) ((void)0)
struct BackBuffer {
  int calls=0;
  HRESULT QueryInterface(int, void **out) { ++calls; *out=this; return 0; }
};
struct Chain {
  BackBuffer *backbuffer_=nullptr;
  std::vector<BackBuffer*> sequential_backbuffers_;
  struct { unsigned BufferCount=2, SwapEffect=DXGI_SWAP_EFFECT_FLIP_DISCARD; } desc_;
  HRESULT GetBuffer(UINT buffer_idx, REFIID riid, void **surface) {
BODY
  }
};
int main() {
  BackBuffer b0, b1, b2;
  Chain c;
  c.backbuffer_ = &b0;
  c.desc_.SwapEffect = DXGI_SWAP_EFFECT_SEQUENTIAL;
  c.desc_.BufferCount = 3;
  c.sequential_backbuffers_ = {&b1, &b2};

  void *out = reinterpret_cast<void*>(1);
  if (c.GetBuffer(0, 0, nullptr) != E_POINTER) return 1;
  if (c.GetBuffer(0, 0, &out) != 0 || out != &b0 || b0.calls != 1) return 2;
  out = reinterpret_cast<void*>(1);
  if (c.GetBuffer(1, 0, &out) != 0 || out != &b1 || b1.calls != 1) return 3;
  out = reinterpret_cast<void*>(1);
  if (c.GetBuffer(2, 0, &out) != 0 || out != &b2 || b2.calls != 1) return 4;
  out = &b0;
  if (c.GetBuffer(3, 0, &out) != DXGI_ERROR_INVALID_CALL || out != nullptr) return 5;

  // flip 模型非零索引是合法 DXGI 调用，但 DXMT 仍不提供独立缓冲。
  c.desc_.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
  c.desc_.BufferCount = 2;
  out = &b0;
  if (c.GetBuffer(1, 0, &out) != DXGI_ERROR_UNSUPPORTED || out != nullptr) return 6;

  // DISCARD 的 BufferCount 按规范只能是 1，非零索引非法。
  c.desc_.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
  c.desc_.BufferCount = 1;
  out = &b0;
  if (c.GetBuffer(1, 0, &out) != DXGI_ERROR_INVALID_CALL || out != nullptr) return 7;

  // 初始化/ResizeBuffers 失败后没有 0 号资源，也不能崩。
  c.desc_.SwapEffect = DXGI_SWAP_EFFECT_SEQUENTIAL;
  c.desc_.BufferCount = 2;
  c.backbuffer_ = nullptr;
  out = &b0;
  if (c.GetBuffer(0, 0, &out) != DXGI_ERROR_INVALID_CALL || out != nullptr) return 8;

  // extra 未建好时不能越界，也不能把非零索引别名成 0。
  c.backbuffer_ = &b0;
  c.sequential_backbuffers_.clear();
  out = &b0;
  if (c.GetBuffer(1, 0, &out) != DXGI_ERROR_UNSUPPORTED || out != nullptr) return 9;
  c.sequential_backbuffers_ = {nullptr};
  out = &b0;
  if (c.GetBuffer(1, 0, &out) != DXGI_ERROR_UNSUPPORTED || out != nullptr) return 10;

  std::puts("PASS: GetBuffer null output, index 0, SEQUENTIAL extras, bounds, discard, flip, missing backing");
}
'''.replace("BODY", body)
with tempfile.TemporaryDirectory(prefix="madeira-swap-") as tmp:
    cpp = Path(tmp) / "swap.cpp"
    exe = Path(tmp) / ("swap.exe" if os.name == "nt" else "swap")
    cpp.write_text(program, encoding="utf-8")
    subprocess.run([compiler, "-std=c++17", "-Werror=return-type", str(cpp), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=5)
