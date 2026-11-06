# 给外部 AI 的调研提示词（iOS 18 地址空间变化）

复制下面整段发给能联网检索权威来源的助手。

---

## 背景

我们在做一个 iOS/iPadOS 上的 Windows 游戏兼容层（Wine + FEX-Emu 把 x86 翻译成 ARM64）。为了跑 32 位 Windows 程序，必须给每个 32 位进程预留一块**4 GB 对齐的连续虚拟地址区间**，作为 guest 地址窗口。

不能随便放的原因：FEX 用 `[Xbase, Wea, UXTW]` 这种寻址方式把 guest 地址 B + zext32(EA) 转成 host 地址，只有 B 的低 32 位为 0（即 4 GB 对齐）时，32 位地址回绕才不会越界。

## 实测数据（两台真实设备，同一份 App、同一个游戏 EDEN）

两台都是 **iPad8,9 / A 系列芯片 / `TASK_VM_INFO.max_address = 0xfc0000000`（63 GB 用户地址空间）**，
都**没有** `com.apple.developer.kernel.extended-virtual-addressing` entitlement（`extended-va=0`）。

### iPadOS 15.6（19G69）— **正常**

```
[wow-window] small address map: it ends at 0xfc0000000
             -- candidates come from [0x400000000, 0xb00000000), at most 2 window(s)
[layerkit-range] post-limit: 128 x 256MB tag-51 chunks before kr=0;
                 span 0x12a8e0000..0xad0000000 (39511 MB) -- guest views must stay OUT of this
[wow-window] B=0x400000000: reserved 4GB + own guard page (+0x4000)      ← 成功
```

CoreAnimation 的保护区间在 **4.6 GB – 43.4 GB**（低地址）。

### iPadOS 18.6（22G86）— **失败**

```
[wow-window] small address map: it ends at 0xfc0000000
             -- candidates come from [0x400000000, 0xb00000000), at most 2 window(s)
[layerkit-range] post-limit: 29 x 256MB tag-51 chunks before kr=3;
                 span 0xdda2d0000..0xfaa2d0000 (7424 MB) -- guest views must stay OUT of this
[wow-window] B=0x400000000 REJECTED: PARTIALLY OCCUPIED:
             free 0x400000000..0x458000000, then OCCUPIED 0x458000000+0xea000000 prot=0/0
[wow-window] B=0x500000000 REJECTED: OCCUPIED 0x458000000+0xea000000 prot=0/0
[wow-window] B=0x600000000 REJECTED: OCCUPIED 0x546000000+0x1fc000000 prot=0/0
[wow-window] B=0x700000000 REJECTED: OCCUPIED 0x546000000+0x1fc000000 prot=0/0
[wow-window] B=0x800000000 REJECTED: OCCUPIED 0x742000000+0x5ea000000 prot=0/0
[wow-window] B=0x900000000 REJECTED: OCCUPIED 0x742000000+0x5ea000000 prot=0/0
[wow-window] B=0xa00000000 REJECTED: OCCUPIED 0x742000000+0x5ea000000 prot=0/0
[wow-window] main-process reserve FAILED 0xc0000017 — a 32-bit main image
             cannot run without a guest window; terminating this process
```

把占用区间换算成 GB：

```
16.0 – 17.4 GB   空（1.4 GB，不够 4 GB）
17.4 – 21.0 GB   被占 3.7 GB
21.1 – 29.0 GB   被占 7.9 GB
29.0 – 52.7 GB   被占 23.7 GB
52.7 – 55.5 GB   空（2.8 GB，不够 4 GB）
55.5 – 62.7 GB   CoreAnimation 保护区间（7.4 GB）
```

**结论：整台设备在 63 GB 用户地址空间里找不到任何连续的 4 GB 空洞。**

占用的区间 `prot=0/0`（PROT_NONE），`shared=0`。

同为 63 GB 地图的 iOS 15.6 上没有这些问题。

## 想请教的问题

1. **iOS 18 是否改变了虚拟地址空间的默认布局？** 具体来说，18.x 是否引入了新的固定地址映射（system shared cache 位置、JIT 相关预留、dyld 预分配、Metal/CoreAnimation 的地址池、内存压缩器、或者新的 sandbox 相关区域），把 17–53 GB 这一段连续占住？

2. **`vm_region` 报告 `prot=0/0` 的连续 35 GB 区域可能是什么？** 有没有已知的 iOS 18 系统映射是这个特征（PROT_NONE、大块连续、跨 17–53 GB）？

3. **CoreAnimation 的地址池位置为什么从低地址（4.6–43.4 GB）移到了高地址（55.5–62.7 GB）？** 是否有公开记录或逆向分析说明 iOS 18 调整了这一块？

4. **有没有办法让一个 4 GB 对齐的固定 mmap 在这种布局下成功？** 例如：
   - 是否有 `vm_map` 的 hint/overwrite 变体能插进已占用区间的缝隙
   - 是否可以在 App 启动早期（系统映射建立之前）抢注地址
   - 是否有已知的 entitlement 或 Info.plist 键会改变地址布局

5. **`com.apple.developer.kernel.extended-virtual-addressing` 在 iOS 18 上是否仍然把用户地址空间扩到 512 GB？** 在越狱 + TrollStore 环境下给 IPA 加这个 entitlement 是否可行、有什么已知限制？

6. **其他 iOS 上的 Wine/模拟器项目（MeloNX、Ryujinx 移植、UTM、iSH 等）在 iOS 18 上如何解决类似的地址布局问题？** 特别是需要固定对齐大块预留的场景。

## 我们已排除的

- 不是内存不足（App 只用了 177 MB）
- 不是后台进程太多
- 不是 TrollStore Lite 的问题（JIT 已成功：双映射 + 真执行探针通过，1120 MB JIT 池分配成功）
- 不是我们代码里 Cage holdback / jumbo holdback 造成的（那两项在两台设备上都是 `FAILED`）
- 不是 `patch`/构建问题（同一份二进制在 15.6 正常）


## 补充：两台的系统环境

| | 设备 A（正常） | 设备 B（失败） |
|---|---|---|
| 系统 | **iPadOS 15.6**（19G69） | **iPadOS 18.6**（22G86） |
| 机型 | iPad13,4（M1） | iPad8,9（A12Z） |
| 越狱 | Dopamine + **标准 TrollStore** | **TrollStore Lite** |
| `get-task-allow` | 1 | 1 |
| `cs-debugged` | 1 | 1 |
| `dynamic-codesigning` | 0 | 0 |
| 用户地址空间 | 63 GB | 63 GB |
| JIT 池 | 成功 | **成功**（1120 MB，双映射 + 真执行探针均通过） |
| 32 位 guest window | **成功** | **失败** |

**注意**：JIT 路径两边都成功，失败的只是「4 GB 对齐的 32 位 guest window 预留」。
也就是说问题不在 JIT/代码执行，而在**地址空间的空闲布局**。

## 额外要问的问题

7. **iPadOS 18 相比 15.6，是否重排了虚拟地址布局？** 我们观察到 CoreAnimation 的保护区间从
   「4.6–43.4 GB」移到了「55.5–62.7 GB」，同时 17–53 GB 变成连续占用。这是已知的 18.x 变化吗？

8. **机型和系统哪个是主因？** 两台设备机型也不同（M1 vs A12Z）。有没有公开数据说明
   不同芯片或不同 iOS 版本下，63 GB 用户地址空间的默认布局差异？

9. **TrollStore 与 TrollStore Lite 是否影响地址布局？** （我们倾向于不影响，因为 JIT 路径两边都成功，
   但想确认没有已知的反例。）

请给出**有依据的结论**（官方文档、开源项目源码、可信的逆向分析），并明确区分「已证实」与「推测」。
