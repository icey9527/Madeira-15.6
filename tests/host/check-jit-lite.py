#!/usr/bin/env python3
"""静态检查 TrollStore Lite 的门控与安全映射步骤。"""

from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
allocator = (root / "app/Madeira/JITAllocator.c").read_text(encoding="utf-8")
header = (root / "app/Madeira/JITAllocator.h").read_text(encoding="utf-8")
helper = (root / "app/Madeira/StikJITHelper.swift").read_text(encoding="utf-8")
setup = (root / "app/Madeira/JITSetup.swift").read_text(encoding="utf-8")
content = (root / "app/Madeira/ContentView.swift").read_text(encoding="utf-8")


def extract_function(text, signature):
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    probe = extract_function(allocator, "static bool jit_trollstore_lite_probe(")
    prepare = extract_function(allocator, "void *jit26_prepare_region(")
    ready = extract_function(helper, "static var ready: Bool {")
    allocate = extract_function(helper, "static func allocatePool(poolSize requestedPoolSize:")

    for marker in ('"/_TrollStore"', '"/_TrollStoreLite"'):
        require(marker in allocator, f"缺少安装标记检查：{marker}")
    require("JIT_INSTALL_CONFLICT" in allocator, "安装标记冲突没有单独分类")
    require("jit_trollstore_allocate_rx(size)" in probe, "Lite 探测未使用单页本地 RX allocator")
    require("FALSE, &current, &maximum, VM_INHERIT_NONE" in probe, "vm_remap 必须共享 backing（copy=false）")
    require("vm_protect(task, rw, size, FALSE, VM_PROT_READ | VM_PROT_WRITE)" in probe,
            "Lite 探测未将 RW alias 设置为 RW")
    require("VM_PROT_READ | VM_PROT_EXECUTE, VM_PROT_WRITE" in probe,
            "Lite 探测未检查 RX 为 R/X 且无 W")
    require("VM_PROT_READ | VM_PROT_WRITE, VM_PROT_EXECUTE" in probe,
            "Lite 探测未检查 RW 为 R/W 且无 X")
    require("mach_vm_write" in probe and "mach_vm_read_overwrite" in probe,
            "共享性探测必须使用 Mach 安全读写 API")
    require("failed:" in probe and "vm_deallocate(task, rw, size)" in probe
            and "vm_deallocate(task, (vm_address_t)rx, size)" in probe,
            "失败路径必须释放 RW 与 RX 映射")
    require("jit_trollstore_lite_ready()" in prepare,
            "jit26_prepare_region 入口未拒绝未验证的 Lite 映射")
    require("jit_trollstore_lite_ready()" in ready,
            "ready 未依赖 Lite 探测缓存")
    require("poolTaken" not in ready or "isTrollStoreLite" in ready,
            "poolTaken 可能绕过 Lite 门控")
    require("jit_trollstore_lite_ready()" in allocate,
            "pool 分配入口未检查 Lite 探测状态")
    require("jit_trollstore_probe()" in setup and "isTrollStoreLite" in setup,
            "启用流程未运行 Lite 探测")
    require("localTrollStorePath" in content,
            "本地 CS_DEBUGGED 路径仍可能被 get-task-allow 门控挡住")
    require("jit_trollstore_lite_ready" in header, "Lite readiness API 未声明")

    print("check-jit-lite: static source checks passed (Mach runtime behavior not mocked)")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"check-jit-lite: FAIL: {error}", file=sys.stderr)
        sys.exit(1)
