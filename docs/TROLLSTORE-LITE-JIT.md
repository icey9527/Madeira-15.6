# TrollStore Lite 本地 JIT 检查

## 安装识别

Madeira 从应用容器根目录（`.app` 的 sibling）分别检查 `_TrollStore` 与 `_TrollStoreLite`：

- 仅 `_TrollStore`：标准 TrollStore 本地后端。
- 仅 `_TrollStoreLite`：TrollStore Lite 本地后端，启用前必须通过单页双映射检查。
- 两者都没有：按现有设置选择 StikDebug 或内置辅助工具。
- 两者同时存在：报告标记冲突并停止，不选择任何后端，也不发送 StikDebug BRK。

TrollStore 官方代码将 Lite 定义为切换 active marker；官方 RootHelper 的 JIT 路径仍使用 `ptrace` attach/detach。官方没有保证 Madeira 使用的 Mach RX/RW 双映射在 Lite 安装中兼容，因此本检查只验证本机映射行为，不推断代码执行可用。

## Lite 启用门控

启用时先检查 `CS_DEBUGGED`，然后用现有本地 RX allocator 创建一页 RX。探测通过 `vm_remap(copy=false)` 建立 RW alias、设置 RW 权限，并用 `vm_region` 读取两边权限：RX 必须可读可执行且不可写，RW 必须可读可写且不可执行。最后使用 `mach_vm_write` 写入测试值并用 `mach_vm_read_overwrite` 从 RX 读回验证共享 backing。探测不调用页面内容，也不执行生成代码。

任一阶段失败都会记录阶段和 `kr`，并释放本次 RW、RX 映射；RX allocator 在自身失败时释放 memory entry。通过结果只缓存到 `CS_DEBUGGED` 清除或安装类型变化，轮询 `ready` 不重复分配。池分配和 `jit26_prepare_region` 直接入口再次检查缓存状态；Lite 不会回落到 StikDebug BRK。成功状态严格显示“映射检查通过，JIT代码执行仍未验证”。

标准 TrollStore 保留原 RX 分配路径和系统调用参数。此改动没有调整现有池大小、RX 布局或 RW alias 搜索参数。标准路径仍需按设备实测，不能从源码参数未变推断兼容性已验证。

## iOS 可用性门控清点

对本地 JIT 调用文件及相邻设置/辅助能力代码静态搜索 `__builtin_available`、`#available`、`@available` 和系统版本比较，分类如下：

| 文件与位置 | 命中 | 分类 |
| --- | --- | --- |
| `JITAllocator.c` 本地 marker、probe、`jit26_prepare_region` | 无版本判断 | 本地 JIT 路径没有 iOS 16/17 排除；由安装类型、`CS_DEBUGGED` 和 Lite 映射检查控制。 |
| `StikJITHelper.swift` | 无版本判断 | ready 与池分配按 JIT 状态门控。 |
| `JITSetup.swift` | 无可执行版本判断 | 调用 `JITShortcutFile.supported` 与 `OnDevicePairing.isSupported`；具体版本判断在相应能力实现中。 |
| `JITBuiltInHost.swift:51` | `#available(iOS 26.0, *)` | 内置远程辅助工具能力要求，不是本地 TrollStore 路径门控，保留。 |
| `JITPairing.swift:53-54` | 系统版本至少 27 | Madeira 设备端配对功能要求，不是本地 JIT 门控，保留。 |
| `JITPairing.swift:135, 249, 252, 275, 294` | `#available` / `@available(iOS 26.0, *)` | 配对期间的 continued-processing 后台任务能力，不是 JIT enable 门控，保留。 |
| `JITNetwork.swift:178-180` | 系统版本至少 27 | Madeira JIT 快捷指令支持范围，不是本地 JIT 门控，保留。 |
| `ContentView.swift:1305, 4252, 4397` | `#available(iOS 26.0, *)` | 导航栏/Glass UI 表现分支，与 JIT 后端无关，保留。 |

其余命中是注释或文案中的系统版本说明。没有删除 helper、设备配对或快捷指令的能力门控。iOS 15.6 的标准安装逻辑保持可达；本文实现与静态检查不能替代 iOS 15.6 或 iOS 17 真机运行。

## 验证范围

`tests/host/check-jit-mode.py` 编译生产中的纯安装分类与后端解析函数，并覆盖 none、standard、lite、conflict 标记矩阵。`tests/host/check-jit-lite.py` 是源码结构检查，确认 Lite 探测步骤、失败清理、ready/enable/pool/直接入口门控及 get-task-allow 本地例外。Host 环境不模拟 Mach VM；Mach 返回码、`vm_region` 权限以及跨映射读回需要真机验证。
