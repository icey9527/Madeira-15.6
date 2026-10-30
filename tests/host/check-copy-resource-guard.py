#!/usr/bin/env python3
"""从生产 CopyResource 提取空参数早退块，确认它发生在 BlitObject 构造之前。"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "dxmt/src/d3d11/d3d11_context_impl.cpp").read_text(encoding="utf-8")
sig = "CopyResource(ID3D11Resource *pDstResource, ID3D11Resource *pSrcResource) override {"
sig_pos = source.index(sig)
guard_pos = source.index("if (!pDstResource || !pSrcResource)", sig_pos)
brace = source.index("{", guard_pos)
depth = 1
end = brace + 1
while depth:
    if source[end] == "{": depth += 1
    elif source[end] == "}": depth -= 1
    end += 1
guard = source[guard_pos:end]
blit_pos = source.index("BlitObject Dst", sig_pos)
if not guard_pos < blit_pos:
    raise SystemExit("CopyResource: null guard 必须早于 BlitObject 构造")
compiler = os.environ.get("CXX") or shutil.which("g++")
if not compiler and Path("C:/TDM-GCC-32/bin/g++.exe").exists():
    compiler = "C:/TDM-GCC-32/bin/g++.exe"
if not compiler:
    raise SystemExit("需要 C++ 编译器，未执行测试")
program = r'''
#include <atomic>
#include <cstdio>
static int touched = 0;
#define WARN(...) ((void)0)
static void probe(void *pDstResource, void *pSrcResource) {
GUARD
  ++touched;
}
int main() {
  probe(nullptr, (void*)1);
  if (touched != 0) return 1;
  probe((void*)1, nullptr);
  if (touched != 0) return 2;
  probe((void*)1, (void*)1);
  if (touched != 1) return 3;
  std::puts("PASS: CopyResource null guard returns before BlitObject");
}
'''.replace("GUARD", guard)
with tempfile.TemporaryDirectory(prefix="madeira-copyres-") as tmp:
    cpp = Path(tmp) / "copyres.cpp"
    exe = Path(tmp) / ("copyres.exe" if os.name == "nt" else "copyres")
    cpp.write_text(program, encoding="utf-8")
    subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=5)
