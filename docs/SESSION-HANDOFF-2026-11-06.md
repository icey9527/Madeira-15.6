# 会话交接 — 2026-11-06

> 给下一个接手的人/AI：这一页是本轮工作的全部状态。先读它，再读
> `MERGE-REGRESSIONS.md`（已知坑位）。

## 一句话现状

上游 36 个提交已合进 `dev` 并构建成功；**白巨魔 JIT 已在 iPadOS 18.6 真机验证通过**；
剩下两条独立的 bug：iPad 15.6 的 Steam/Baldr Sky 回归，和 iPad 18.6 的 `explorer.exe` 卡死。

---

## 分支状态

| 分支 | 提交 | 用途 |
|---|---|---|
| `port/ios15.6` | `7fbe98ea` | **发行版**。含完整白巨魔修复，构建成功，装在你 iPad 15.6 上 |
| `dev` | `f65b5d72` | 集成线。领先发行版 854 提交，落后上游 **0** |
| `archive/dev-upstream-20261106` | 同 dev | dev 的存档，重组时对照用 |
| `L0-win32u` | 基于 `v0.1.3-good` + win32u/Winios | **进行中**：单层验证分支 |
| tag `v0.1.3-good` | `7fbe98ea` | 已知可用的基线，diff 用 |

---

## 已完成的验证

### ✅ 白巨魔（TrollStore Lite）— 真机通过

iPadOS 18.6、iPad8,9、`dynamic-codesigning=0` 的日志：

```
[trollstore] 双映射与代码执行检查均通过 (Lite backend)   ← 真执行探针
[trollstore] RX mapped at 0xd08c58000 size=1207959552
[OK] TrollStore JIT pool ready.
[OK] JIT pool: RX=0xd08c58000, RW=0xd50c58000, size=1152MB
```

**结论：iOS 18.6 上 CS_DEBUGGED 足够，不需要 dynamic-codesigning。**
这和 MeloNX 的做法一致（只查 csops，不查 P_TRACED）。

> 已知无害噪音：`[no-footprint] pool-RW-alias: ALL 4 variants refused kr=4`，
> 只是 RW 别名所有权判定，不影响功能。

### ⚠️ iPad 15.6 — Steam/Baldr Sky 回归

| | v0.1.3（能玩） | dev（不能） |
|---|---|---|
| 游戏 | 其它游戏 | **Baldr Sky** |
| `presents` 60s | 1589 | 0 |
| 窗口树 | 正常 | 6 个窗口全 `vis=0`，只有 166×52 |

**注意**：早期对比跑的不是同一个游戏，已纠正。0.1.3 跑 Baldr Sky 也能进游戏，
所以 **dev 不能跑 Baldr Sky 是真回归**。

### ❓ iPad 18.6 — explorer.exe 卡死

```
[thread-sample] modmap modules=13 first=explorer.exe     ← 15.6 上是 22 个
[thread-sample] cpu=100% RUN pc=__wine_syscall_dispatcher+0xf0
```

**Dock 的 `dockhost.exe`（65 模块）从未启动。**
`#556` 的 JIT 池修复**不在发行版里**，所以与它无关——是 iOS 18.6 特有的。

---

## 下一步（按推荐顺序）

### 1. 继续 L0+win32u 单层验证（进行中）

分支 `L0-win32u` 已从 `v0.1.3-good` 取出：

```bash
git checkout archive/dev-upstream-20261106 -- build/win32u-unix app/Madeira/Winios
```

**待办**：`app/Madeira.xcodeproj/project.pbxproj` 里登记新文件
（`WiniosGL.m`、`MetalBackgroundGate.m`、`BackgroundCPUCap.c`、`VulkanLayerGeometry.h`），
否则链接缺符号。

**注意**：`driver_ios.c` 依赖 56 个新版 `winios_*` 符号，**必须与新版 `Winios.m` 一起换**，
两者不能拆。

然后构建 → 装 iPad 15.6 → 测 Baldr Sky：
- 崩 → win32u 是元凶，在层内再二分
- 正常 → 排除 win32u，嫌疑转向 ntdll/wine 引脚

### 2. 分层解封路线（如果需要）

| 层 | 内容 | 风险 |
|---|---|---|
| L1 | 文档/CI/测试/本地化 | 极低 |
| L2 | 新商店（Epic/GOG/EA/itch/Workshop） | 中 |
| L3 | Swift 逻辑（库/Steam/Dock） | 中高 |
| L4 | `build/ntdll-unix`、`build/wineserver` | 高 |
| L5 | `build/win32u-unix` + `Winios` | 最高（5253 行） |
| L6 | `wine`/`FEX` 引脚 | 最高 |
| L7 | DXMT | 高 |

**用「按模块取文件」而不是 cherry-pick**——上游提交间有依赖，cherry-pick 会撕裂。

### 3. iOS 18 explorer 卡死（低优先）

缺 9 个模块，需要找出少的是哪几个。**没有 iOS 18 真机**，只能靠日志推断，投入产出比低。

---

## 合并上游的正确姿势

见 `MERGE-REGRESSIONS.md`。核心三条：

1. **合并前先跑「必查清单」记基线，合并后再跑一遍对比**
2. 别忘了 `python3 tools/ci/update-upstream-compat.py`
3. `docs/CONTROLLERS.md` 说「on by default」但代码被改回 opt-in = 被覆盖了

---

## 本轮新增的工具

| 文件 | 用途 |
|---|---|
| `tools/ci/audit-ios-deployment.py` | 扫 26 类 iOS 16/17/18 API，应为 0 |
| `tools/ci/check-pbxproj-ids.py` | 查 pbxproj 对象 ID 重复（重复会让文件静默不编译） |
| `docs/MERGE-REGRESSIONS.md` | 已知坑位清单 + 合并后必查命令 |

---

## 学到的教训

1. **不要派子代理做「只读调查」**——这次两个代理跑了一小时、输出 0 字节。
   用精确的 grep 命令自己查更快，`docs/DEVICE-ACCESS.md` 里有现成的设备命令。
2. **对比必须同条件**——早期把「0.1.3 跑 A 游戏」和「dev 跑 B 游戏」对比，得出错误结论。
3. **`presents=0` 可能只是 30 秒快照**，不代表游戏没跑起来。
4. **`patch --fuzz` 不能用来适配架构差异**，只会把代码塞进错误位置。
