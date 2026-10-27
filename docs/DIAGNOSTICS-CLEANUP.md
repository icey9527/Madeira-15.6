# 过时诊断代码清理清单

> 2026-10-27 盘点。**动手前先建存档分支**：`archive/diagnostics-2026-10`（已存在并推送）。
> 迁移方式：主分支分几个提交删除 A 类；B/C 类不动。

## A. 可以撤（问题已解决，只为一次性取证加的）

| 标签 | 位置 | 说明 |
|---|---|---|
| `[d9-locked-flush] capture/entry/matched` | `build/dxmt-ios/prepare-compat.py` | 32 位 D3D9 黑屏取证；功能性的 `g_textureLeafObject` 指针回退**必须保留** |
| `[d9-locked-flush] level= / texture levels=` | 同上 | 每次 flush 路径，限前 8 行 |
| `[d9-lock-result]`、`[d9-mirror]` | 同上 | 限 8 次 / 仅失败路径 |
| `[ios15-probe]` | 同上 | 每次 shader 编译一行；intrinsic lowering 与属性裁剪是功能，保留 |
| `[carve-free-self] ml1431` | `build/ntdll-unix/virtual_ios.c` | 每进程前 32 次 free |
| `[mad-raise]` | `tools/ci/build-all.sh` | 仅 32 位异常路径 |
| `startup-v2` 时间线 | `app/Madeira/MadeiraApp.swift`、`ContentView.swift` | 启动各 1 次 |

## B. 建议保留

- `[fg-click]`（`build/win32u-unix/message_ios.c`，限 16 行）、`[key-hold]`（`app/Madeira/Winios/Winios.m`，限 16 行）——输入问题仍在观察
- `[low-rip]`（FEX 补丁）——空指针崩溃的唯一线索
- `[guest-stack]`（`build/ntdll-unix/signal_arm64_ios.c`）——定位 32 位崩溃调用者
- `[first-fault]`、`[callret-dup]`、`[floor-rip]`、`[unwind-stall]`、`[wow-native-tls]`
- MetalSelfTest / airconv_probe / `DXMT_AIRCONV_DUMP`（默认关）——D3D11 问题唯一的在机复现工具

## C. 不能直接删（CI 硬回执依赖）

| 依赖点 | 现在检查什么 | 删诊断前要改成 |
|---|---|---|
| `build/dxmt-ios/build.sh:434` | `grep 'd9-locked-flush'` 于三个 .o | `'flushLocked'`（三个 TU 都有） |
| `build/dxmt-ios/prepare-compat.py:775` | marker `'attribute masks removed from'` | 改成 `'removeFnAttrs'` 之类仍存在的字符串 |
| `build/dxmt-ios/build.sh:446/452` | MTLCopyAllDevices 回执 | 功能性改写，不动 |
| `build/dxmt-ios/build.sh:460-479` | `air64-apple-ios15.0.0` AIR 章回执 | 功能性，不动 |
| `tools/ci/verify-startup.sh:225-233` | `_madeira_metal_probe_run` | 真删 probe 要同步删这块 |

## 执行顺序

1. 存档分支已推送，确认后开始；
2. 第一个提交只删 `[d9-*]` 取证日志并同步改 `build.sh:434`；
3. 每改一次都跑 `build.sh` 到三个回执段，避免「绿色但空」的假通过；
4. 之后依次 `[ios15-probe]`、`[carve-free-self]`、`[mad-raise]`、`startup-v2`。
