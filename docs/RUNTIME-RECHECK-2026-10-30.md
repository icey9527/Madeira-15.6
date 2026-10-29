# 2026-10-30 修复复核（本地，未推送）

分支：`fix/runtime-recheck-20261030`，基于 `9f0e9681`。
本轮没有推送、触发 CI、改写设备配置或启动游戏。

## 已有证据支持的修复

### 后台恢复冻结

设备两轮日志已保存到 `F:\Madeira\refs\tmp\recheck-20261030\`。
`previous.log:14967-14969` 丢弃 frame 1172/1173/1174；随后在 15460、15599、15682 行打印
`[gpu-wait] frame=1172 status=0 waited_ms=2000/4000/6000`。

`CommitChunkInternal` 后台分支跳过 `chunk.encode`，却增加 `ready_for_commit`。
`CommandChunk::reset` 把 `attached_cmdbuf` 清空，因此 finish 线程得到空句柄，status 为 0，
永久轮询。帧延迟 fence 与 cpu_coherent 不再推进。D3D9 与 D3D11 使用同一份队列实现，
所以不能根据 BSD 没有 `[present-pause]` 就断言是游戏自己停止。

改动：后台等待恢复后继续编码原 chunk，不发布空工作；等待支持停止退出。
若以后意外收到空 cmdbuf，以失败状态退役，唤醒相关 fence、回调和资源回收。
PE getter 使用线程局部缓存；原生构建直接链接真实状态，不再使用恒为 1 的弱定义。

验证：`python tests/host/check-dxmt-background.py`，从生产代码抽取门禁与退役块，
主机 C++ 真线程测试 21 项通过。覆盖后台零提交、恢复一次提交、停止退出、空句柄失败退役后继续处理下一块。
这不是完整 Metal/iOS 集成测试；仍需新包实测。

### 纹理镜像失败处理

撤销 40×5ms sleep。扩容由同一次同步 unix 调用返回后的 shim 完成，调用内部等待
本线程扩容不成立。日志只到 320MB 不能证明存在 320MB 上限；每次首次分配告警后
shim 可能已经扩容并静默重试成功。

ensureMirror 原本已经按需分配，不需要基于旧猜测再做一次“懒分配”。
2D/cube ensureMirror 与 restoreMirrorForSource 现在返回分配结果；真正分配失败
通过 LockRect/UpdateTexture/UpdateSurface 等调用返回失败，进入现有扩容重试流程，
不再被 D3D_OK 吞掉。UpdateSurface 的目的镜像检查位于入队上传之前，避免重放重复排上传。
合法无镜像资源仍成功。Volume 本轮未改。

验证：`python tests/host/check-d3d9-mirror-retry.py`，生产函数抽取测试通过（42 项）。
**未证明 BSD 白底与这些分配失败有直接因果，白底仍待新包验证与具体 draw 定位。**

## 下载改动

- 手动 steam-cdn 直接返回单节点，真正跳过目录与 HEAD 探测。
- 自动模式停止把清单响应快的节点写为手动默认；清单延迟不等于游戏分块吞吐。
- 有可达节点时，探测失败节点不再混入分块轮转；海外备用在清单全部失败时才单独尝试。
- 成功提供清单的节点纳入对应 depot 的分块池。
- 重试跟踪本分块已经试过的节点，避免降级重新排序后再次选中刚失败的同一节点。
- 记录每节点请求数、实际字节数与累计请求耗时。并发重叠下的 per-request-bps 不等于总下载速度。
- 公开目录移除非必要 access_token，授权仍通过 CM depot key/request code 完成。
- 手动 URL 只允许受信任 CDN 的源地址；拒绝用户信息、查询参数、路径及非标准端口。

现有 Steam 主机测试静态部分通过；修正了过时的地区 UserDefaults 白名单与磁盘容量替身锚点。
完整测试需要 Swift、OpenSSL/liblzma/zlib 等主机依赖，本机没有 Swift/所需 C 库，未能运行完整编译。
**没有实测提速倍数，不承诺达到 Windows 客户端速度。**

## 启动卡住：已纠正旧结论，新增诊断而非宣称修复

Madeira Dock 加载官方 steamclient64.dll，经 IClientUser 登录、授权，再调用
IClientAppManager::LaunchApp。没有独立 steam.exe 不代表没走官方客户端。
旧交接文档中相反结论不成立。

Dead Cells 日志 Machine=0x14c，是 32 位。其游戏主线程在 NtWriteFile 的 async
完成等待处阻塞超过两分钟。STEAM_DIPC_CONSUME 属于 Dock 内客户端常驻线程，
不能将它当作游戏等待 Steam 的证据。

本轮新增有界 `[write-src]` 诊断，记录写入 handle/fd 类型和等待前后状态，
不记录数据内容、账号或文件路径。Wine 子模块不修改，通过既有 build/ntdll-unix
源文件副本流程接入。

雨霏霏日志含 Electron/Chromium 子进程与隐藏窗口；这些只能证明尚无可见输出，
不足以认定与 Dead Cells 同因。用户后续闪退仍需新现场。太阳骑士也未修复。

## 下一轮验收

用户允许统一推送/出包后再做：
1. BSD/壳之少女：下拉通知中心或切后台，检查 `[bg-commit] paused` 后同序号 resumed，
   frame/fence 继续推进，不再 status=0 永久等。
2. BSD 第1章：观察白底是否恢复；若不恢复，应关联具体绘制，不能再只凭前600条状态定因。
3. Dead Cells：获取 `[write-src] server-path` 与 wait-begin/wait-end，识别主线程写入目标。
4. 下载：记录实际节点与传输统计；手动模式不再自动改写配置。历史自动写入的
   steam-cdn 仍保留，切换自动需用户明确清除该设置。

`git diff --check` 与修改的 shell/Python 语法检查通过。
全仓库 check-c-quotes.py 仍报告 5 条已有含转义引号的可疑行，均不在本轮改动内。
