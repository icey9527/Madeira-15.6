#!/usr/bin/env python3
"""从 Present1 提取 SEQUENTIAL 轮换块，验证 A/B/C -> B/C/A、present 先捕获、不 alias。"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "dxmt/src/d3d11/d3d11_swapchain.cpp").read_text(encoding="utf-8")
marker = "if (!sequential_backbuffers_.empty()"
start = source.index(marker)
brace = source.index("{", start)
depth = 1
end = brace + 1
while depth:
    if source[end] == "{": depth += 1
    elif source[end] == "}": depth -= 1
    end += 1
block = source[start:end]

# Present 必须在轮换前捕获 0 号纹理；轮换只能拷贝内容，不能改身份/索引。
present_capture = source.index("backbuffer = backbuffer_->texture()")
if present_capture > start:
    raise SystemExit("Present: present 必须在轮换块之前捕获 backbuffer")
if "backbuffer_ =" in block or "GetCurrentBackBufferIndex()" in block:
    raise SystemExit("轮换块不得改写 backbuffer_ 身份或切换 current index")

compiler = os.environ.get("CXX") or shutil.which("g++")
if not compiler and Path("C:/TDM-GCC-32/bin/g++.exe").exists():
    compiler = "C:/TDM-GCC-32/bin/g++.exe"
if not compiler:
    raise SystemExit("需要 C++ 编译器，未执行测试")
program = r'''
#include <cstdio>
#include <utility>
#include <vector>
struct Tex { const char *name; };
struct Com {
  Tex *t = nullptr;
  Tex *ptr() const { return t; }
  explicit operator bool() const { return t != nullptr; }
};
struct Ctx {
  std::vector<std::pair<Tex*,Tex*>> copies;
  void CopyResource(Tex *dst, Tex *src) { copies.push_back({dst, src}); }
  void PrepareFlush() {}
};
struct Chain {
  Ctx *device_context_ = nullptr;
  Com backbuffer_{};
  std::vector<Com> sequential_backbuffers_;
  Com sequential_rotation_temp_{};
  void rotate() {
BLOCK
  }
};
int main() {
  Tex A{"A"}, B{"B"}, C{"C"}, T{"T"};
  Ctx ctx;
  Chain ch;
  ch.device_context_ = &ctx;
  ch.backbuffer_.t = &A;
  ch.sequential_backbuffers_ = {Com{&B}, Com{&C}};
  ch.sequential_rotation_temp_.t = &T;

  Tex *presented = ch.backbuffer_.ptr();  /* Present 在轮换前捕获 A */
  ch.rotate();

  if (presented != &A) return 1;
  const std::vector<std::pair<Tex*,Tex*>> expected = {
      {&T, &A}, {&A, &B}, {&B, &C}, {&C, &T}};
  if (ctx.copies.size() != expected.size()) return 2;
  for (size_t i = 0; i < expected.size(); ++i)
    if (ctx.copies[i].first != expected[i].first || ctx.copies[i].second != expected[i].second)
      return 3;

  /* 内容环转：A<-B, B<-C, C<-A；资源指针各自不同，不 alias。 */
  std::vector<std::pair<Tex*,Tex*>> content = {
      {&A, &A}, {&B, &B}, {&C, &C}, {&T, &T}};
  auto get = [&](Tex *p) -> Tex* {
    for (auto &kv : content) if (kv.first == p) return kv.second;
    return nullptr;
  };
  for (auto &cp : ctx.copies) {
    for (auto &kv : content) if (kv.first == cp.first) kv.second = get(cp.second);
  }
  if (get(&A) != &B || get(&B) != &C || get(&C) != &A) return 4;
  if (&A == &B || &B == &C || &A == &C || &T == &A) return 5;

  /* 没有 extra 时轮换是 no-op，且不会解引用空指针。 */
  Ctx empty_ctx;
  Chain empty;
  empty.device_context_ = &empty_ctx;
  empty.rotate();
  if (!empty_ctx.copies.empty()) return 6;

  std::puts("PASS: present captures backbuffer before rotation, content A/B/C -> B/C/A, no alias");
}
'''.replace("BLOCK", block)
with tempfile.TemporaryDirectory(prefix="madeira-rotate-") as tmp:
    cpp = Path(tmp) / "rotate.cpp"
    exe = Path(tmp) / ("rotate.exe" if os.name == "nt" else "rotate")
    cpp.write_text(program, encoding="utf-8")
    subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=5)
