# 合并上游时被覆盖的 fork 修复（已知坑位清单）

> 用途：每次合并 `upstream/main` 之前先读这一页，合并后逐条验证。
> 每条记录：**症状 → 根因 → fork 修复提交 → 验证方法**。
> 新增条目请附上「怎么在 5 秒内证明它还在」的证据命令。

这个 fork 与上游分叉不小，上游改动频繁，**同一个修复被覆盖过不止一次**。
最近一次合并（2026-11-06，上游 36 个提交）就一次性覆盖了至少 3 条。

---

## 合并后必查清单

```bash
# 1. 前台输入默认开启（Eden / Horizon Zero Dawn 类）
grep -A3 'MADEIRA_INPUT_FOREGROUND' build/win32u-unix/driver_ios.c | grep -q 'e\[0\] == .0.' \
  && echo "OK: 默认开启" || echo "REGRESSED: 被改回 opt-in"

# 2. 首次点击不被吞掉
grep -q 'MADEIRA_CLICK_FOREGROUND' build/win32u-unix/message_ios.c \
  && echo "OK: 首次点击修复在" || echo "REGRESSED: 首次点击修复丢失"

# 3. 地址空间上限取内核值（63 GB 设备）
grep -q 'USING KERNEL' build/ntdll-unix/virtual_ios.c \
  && echo "OK: 采纳内核上限" || echo "REGRESSED: 又回到只升不降"

# 4. iOS 15.6 兼容层仍在
python3 tools/ci/audit-ios-deployment.py   # 应输出 0 unguarded site(s)

# 5. Xcode 工程对象 ID 无重复（重复会让文件静默不编译）
python3 tools/ci/check-pbxproj-ids.py

# 6. 中文/日文 locale 两条路径都在
grep -q 'madeira_force_gbk_codepage' app/Madeira/WineProcessBridge.m \
  && grep -q 'madeira_japanese_prefix' app/Madeira/WineProcessBridge.m \
  && echo "OK: 中英日 locale 并存" || echo "REGRESSED: 某个 locale 被覆盖"
```

---

## 坑位 1：窗口不激活 → 游戏收不到手柄/键盘

| 项 | 内容 |
|---|---|
| **症状** | Eden 等模拟器、Horizon Zero Dawn 启动后，必须**先点一下游戏窗口**，手柄和键盘才生效 |
| **根因** | iOS 没有窗口管理器。程序显示窗口但不激活时 `GetForegroundWindow()` 为 NULL，wineserver 只给**前台进程**排 `WM_INPUT`，输入全被丢弃 |
| **上游行为** | `MADEIRA_INPUT_FOREGROUND` 是 **opt-in**（默认关闭），上游 `#293` |
| **fork 修复** | `e9baa374`(上游引入) + `7333d62a`(改默认开启 + 首次点击不丢) + `40d235af`(再次恢复默认开启) |
| **被覆盖历史** | 2026-11-06 合并时，`driver_ios.c` 那行被改回上游 opt-in 版本 |
| **验证** | 日志里应出现 `[fg-none]` 和 `[fg-click] foreground lock refused ... ok=1` |
| **开关** | `MADEIRA_INPUT_FOREGROUND=0` 关闭；`MADEIRA_CLICK_FOREGROUND=0` 恢复 Wine 吞点击行为 |

**注意**：文档 `docs/CONTROLLERS.md` 一直写着「on by default in Madeira-15.6」，
但代码被覆盖时文档不会跟着变——**文档说法和代码不一致就是被覆盖的信号**。

---

## 坑位 2：63 GB 地图上地址窗口倒置 → CoreAnimation 区间被侵占

| 项 | 内容 |
|---|---|
| **症状** | Steam 启动后一帧不画（`presents=0`），90 秒后被 iOS 切后台/杀死 |
| **根因** | `get_host_addr_space_limit()` 的 2 的幂探测在 63 GB 地图上**高估**为 64 GB，旧规则「只升不降」保留了错的 64 GB → 所有触及顶部的窗口倒置 |
| **日志特征** | `[va-limit] walk=0x1000000000 kernel_max=0xfc0000000 -> keeping walk`<br>`[va-scan] INVERTED WINDOW base=0x7400000000 >= end=0x1000000000`<br>`[layerkit-range] exclusion DROPPED` |
| **fork 修复** | `f65b5d72`——内核值 ≥ 32 GB 就采纳 |
| **上游状态** | 上游的「只升不降」是为了防异常小值，但没考虑 63 GB 这种「非 2 的幂」真实上限 |
| **验证** | `[va-limit] ... -> USING KERNEL` |

---

## 坑位 3：iOS 15.6 API 兼容层被上游新代码绕过

| 项 | 内容 |
|---|---|
| **症状** | 编译失败：`'X' is only available in iOS 16.0 or newer` |
| **根因** | 上游新页面直接用 iOS 16/17/18 API，不看我们的 deployment target |
| **已封装** | `AppNavigationView`、`LabeledContent15`、`EmptyStateView`、`ActivityShareSheet`、`onGlobalFrameChange15`、`persistentSystemOverlays15`、`subtracting15`、`pickerStyleNavigationLink15`、`Compat15.swift`、`Compat15Views.swift` |
| **验证** | 跑 `tools/ci/` 里的 iOS 15.6 审计脚本，应为 0 处未守卫 |

**踩过的具体 API**：`onChange(of:)` 双参数、`URL.documentsDirectory`、`ViewThatFits`、
`Task.sleep(for:)`、`scrollContentBackground`、`pickerStyle(.navigationLink)`、
`Path.subtracting`、`UIHostingConfiguration`、`registerForTraitChanges`、
`traitOverrides`、`safeAreaRegions`、`Duration`、`OSAllocatedUnfairLock`、
`ProposedViewSize`、`animate(springDuration:bounce:)`。

---

## 坑位 4：Xcode 工程对象 ID 撞车 → 文件静默不编译

| 项 | 内容 |
|---|---|
| **症状** | 报「找不到符号」，但符号明明在某个 `.swift` 里定义 |
| **根因** | 手工往 `project.pbxproj` 加文件时，分配的对象 ID 和已有文件重复。**Xcode 不报错**，后一个文件直接不参与编译 |
| **fork 修复** | 加了唯一性检查脚本 |
| **验证** | 检查 `project.pbxproj` 里所有 `= {isa =` 前面的 8 位 ID 无重复 |
| **教训** | 加新文件后**必须**验证 ID 唯一，否则会浪费一整轮构建排查 |

---

## 坑位 5：DXMT 补丁与 vendored 源码不匹配

| 项 | 内容 |
|---|---|
| **症状** | DXMT 编译失败：`function definition is not allowed here`、`no member named X` |
| **根因** | 上游的 `build/dxmt-patches/*.patch` 是针对**较新的 dxmt 引脚**写的；我们的 `dxmt` 是 vendored 旧树，缺配套设施（`m_direct`、`BuildDrawCapture(begin,end)`） |
| **现状** | 补丁已禁用（`.patch.disabled`），等 dxmt 同步到上游引脚 `3d62913` 再启用 |
| **教训** | **不要用 `patch --fuzz` 硬套**。fuzz 会把代码塞进错误位置，产生一串级联错误 |

---

## 坑位 6：CI 兼容层哈希清单过期

| 项 | 内容 |
|---|---|
| **症状** | 构建第一步就失败，`committed compatibility layer does not match the upstream manifest` |
| **根因** | 合并带入新 DLL，但 `tools/ci/upstream-compat.sha256` 没更新 |
| **修复** | 合并后跑 `python3 tools/ci/update-upstream-compat.py` |
| **教训** | 这应该写进合并流程，不是每次现查 |

---

## 合并上游的标准姿势

1. `git fetch upstream && git log --oneline origin/dev..upstream/main` 看清范围
2. 合并前先跑上面「必查清单」，记录当前基线
3. `git merge --no-commit --no-ff upstream/main`
4. **逐条**解决冲突，特别注意：
   - `build/win32u-unix/*.c`（前台、输入、窗口）
   - `build/ntdll-unix/*.c`（地址空间、JIT 池）
   - `app/Madeira/*.swift`（iOS 15.6 API）
   - `Localizable.xcstrings`（中文本地化）
5. 跑 `tools/ci/update-upstream-compat.py`
6. **重新跑「必查清单」**，和步骤 2 的基线对比
7. app-only 构建快速验证 Swift 编译
8. 完整构建出包

---

## 坑位 7：iOS 16+ 才有的私有 API，在 15.6 上直接打死进程

| 项 | 内容 |
|---|---|
| **症状** | 进游戏后**整个 App 进程消失**（不是游戏崩）。日志尾巴是 `Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: '-[X yy]: unrecognized selector sent to instance'` |
| **实例** | `MadeiraWindowMetalLayer.nextDrawable` 读 `developerHUDProperties`（Apple 私有的 `CAMetalLayer` 属性）。iPadOS 16/17/18 有这个属性，**15.6 没有** |
| **根因** | dev 的改动是在 iPadOS 18.6 上验证的。ObjC 给对象发它不认识的消息**不是返回 nil，是抛异常终止进程**。第一个窗口图层第一次画的时候就死 |
| **日志取证** | `设备日志 2026-11-07 16:48:08`（iPad13,4 / 15.6）：Baldr Sky 16:48:08、水仙 16:48:32、壳之少女 15:55:42，三条同一个异常 |
| **fork 修复** | `S3c-hudfix`——统一走 `respondsToSelector:NSSelectorFromString(@"developerHUDProperties")` |
| **通用规则** | 凡是新加的私有 API（`developerHUDProperties`、`_kill:`、`pidForRequestIdentifier:` 这类），必须先 `respondsToSelector:` 探一下。`DonorPool.m` 里那两处已经是正确写法 |

**为什么 0.1.3 没事**：它一行都没碰过这个属性。**为什么 dev 没事**：18.6 上有这个属性。

---

## 坑位 8：分层边界不能按目录划，要按符号依赖划

把 dev 的改动一层层搬回发行版时，按目录切分会在链接期和运行期同时翻车。已踩到的三对强耦合：

| 耦合 | 证据 |
|---|---|
| `app/Madeira/Winios` ↔ `build/win32u-unix` | 新 `Winios.m` 调 21 个 `winios_drv_*`；新 `driver_ios.c` 调 131 个 `winios_*`。二者必须同换 |
| `app/Madeira` 前端 ↔ `Winios` | 76 个新 Swift 文件里有 30 个符号只有新原生层才定义（`winios_cancel_text`、`madeira_bg_cpu_cap`、`madeira_cab_*`、`madeira_phone_pointer_*`…） |
| `build/wineserver` ↔ `build/ntdll-unix` | 符号：`ios_hang_kill_pid`、`madeira_dircache_changed`。**更要命的是门铃**：新服务端靠客户端写完请求后敲 ring，旧 ntdll 不敲，于是每轮退化成全量 read() 约 1500 个 client fd，整机慢到 Eden 启动从「秒进」变 30 秒 |

**做法**：换一层之前先在临时分支上链接一次，看 `Undefined symbols` 再决定边界；运行期的耦合只能靠设备日志（`[srv-stuck]`、`wait_select_reply` 这类）判断。

---

## 坑位 9：新的 GL 驱动会把 d3d9 游戏的 d3d9 顶掉（上游代码本身就有这个坑）

| 项 | 内容 |
|---|---|
| **症状** | 某个 d3d9 游戏在旧包能玩，新包「画一帧就自己退出」——注意不是崩，是 `NtTerminateProcess(exit_code=0x0)`，调用来自游戏自己的代码 |
| **实例** | Baldr Sky（`BaldrSky.exe`）在 S3c 能玩，在 S5 上画一帧后 30 秒自行退出 |
| **决定性证据** | 同一台设备的两份日志：<br>S3c `[unixlib] opengl32 (GL-absent stub table)` → `[unixlib] d3d9shim -> wow64 table`、`[d3d9-native] ready: 324 slots`、79 条 `[d3d9-census]`<br>S5 `[unixlib] opengl32 (winios WGL)` → **一条 `[unixlib] d3d9shim` 都没有**、0 条 census |
| **根因** | `build/win32u-unix/driver_ios.c` 里 `pOpenGLInit = winios_OpenGLInit` 是**无条件安装**的。游戏启动时加载 `opengl32.dll`，GLES 驱动在那一刻接管，之后 DXMT 的 d3d9 unix 垫片就再也接不上——d3d9 设备根本没创建，游戏等不到画面就退了 |
| **开关验证** | 同一个 S5 包，`madeira.cfg` 加 `env.MADEIRA_NO_GL = 1`，BSD 立刻能玩。**配置层就能验证，不用重编** |
| **权宜修复** | `S5b-gl-gate`：**默认完全不安装** `pOpenGLInit`，维持 0.1.3 的「创建 GL 上下文一律失败」；只有进程环境里 `MADEIRA_GL_DRIVER=1` 才装上用于测试 |
| **踩过的弯路** | 第一次把门控放在「安装处」，用 `MADEIRA_GL_BACKEND` 判断——**安装时这个变量还不存在**（bridge 还没跑），门控等于没做。第二次改成「装上去、在 `winios_OpenGLInit` 里按调用时的 env 决定返回 `STATUS_NOT_IMPLEMENTED`」——**也不行**，因为决定权不在返回值上：`pOpenGLInit` 非空本身就让 opengl32 在 DLL 加载时初始化它的 unix 侧，d3d9 路径在那时就没了。**结论：判断必须在安装处，不能靠返回值** |
| **根治方向** | **惰性初始化**：GL 驱动应当只在 guest 真正创建 WGL 上下文时才拉起，而不是 `opengl32` 一附着就把整个 GL 栈初始化。这样 d3d9 游戏根本碰不到它，加不加 zink 都不会互相干扰。**这条还没做，是真正的 bug** |
| **上游状态** | **上游就是这段代码**，同一个坑。他们的发布包同样不带 `gl/` 那两个 dylib（`tools/build/prepare-graphics.sh` 是手动 bootstrap，`tools/build/build-ipa.sh` 不调用它），所以上游也只有 gles 后端 |

**以后要把 zink/OSMesa 打进去时必须做的**：重测 d3d9 游戏。如果那时 d3d9 仍被顶掉，就说明根因不是「缺后端」，而是**驱动初始化与 d3d9 的 unixlib 附着互斥**——那才是要修的地方。

---

## 待补充

以下问题已确认存在但**尚未修复**，查明后请补进对应坑位：

- **Steam wineserver 卡死**：`[srv-stuck]` 78 次，23 个线程 `(no server thread for tid ...)`，
  Steam 一帧不画。地址空间修复**不解决**这个。
- **JIT 池 mprotect 失败**：`Failed to mprotect last page of code buffer` 出现 8 次，
  池只用了 179 MB / 896 MB。
- **单独换 wineserver 会让整机变慢**（2026-11-07 实测，分支 `S4-wineserver`）：Eden 从秒进
  变 30 秒，Steam 一直加载。原因见坑位 8 的门铃那条——新服务端等 ntdll 敲 ring，
  旧 ntdll 不敲。**结论：wineserver 不能单独搬，必须和 ntdll 一起。**
- **GL 驱动惰性初始化**（坑位 9 的根治方向，尚未动手）：让 `pOpenGLInit` 只在 guest
  真正创建 WGL 上下文时才生效，而不是 `opengl32` 附着时就把 GL 栈拉起来。做完之后
  才能安全地把 zink/OSMesa 打进包里，而不必担心顶掉 d3d9 游戏。
- **BSD（Baldr Sky）在 S5 上自行退出**：已由 `S5b-gl-gate` 修复（就是坑位 9）。
  若之后又出现同类「画一帧就退」，先查 `[unixlib] d3d9shim` 有没有出现，再查
  `MADEIRA_NO_GL` / `MADEIRA_GL_BACKEND`。
