import SwiftUI

@main
struct MadeiraApp: App {
    init() {
        madeira_startup_log("startup-v2: SwiftUI App init begin")
        // ml1172: read the screen on the main thread; library entries, whose
        // default Resolution comes from it, are also made on other threads.
        _ = ResolutionChoices.screen
        madeira_startup_log("startup-v2: SwiftUI App init complete")
        // 封面与商店图来自 Steam 的 CDN（cdn.cloudflare.steamstatic.com），网络那一层已经是
        // CDN 了；慢的是本地：iOS 15 的 AsyncImage 不走应用层缓存，滚出屏幕再滚回来
        // 会重新下载并重新解码。先给共享会话一个够大的 URL 缓存，免掉重复下载。
        // 64 MB 磁盘：一张 library_600x900 约 40–60 KB，够放上千张，也远小于设备上
        // 动辄上百 MB 的存档；URLCache 落在 Caches 目录，系统空间紧张时会自己清掉。
        URLCache.shared = URLCache(memoryCapacity: 16 << 20, diskCapacity: 64 << 20,
                                   diskPath: "madeira-steam-art")
        // Bring-up diagnostic (see MetalSelfTest): runs only while the marker
        // file exists, off the main thread so launch stays fast.
        DispatchQueue.global(qos: .utility).async { MetalSelfTest.runIfRequested() }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .modifier(ClaimGamepadEvents())
                .onAppear {
                    madeira_startup_log("startup-v2: ContentView appeared")
                    GamepadInput.shared.start()
                    madeira_startup_log("startup-v2: gamepad input started")
                    HardwareInput.shared.start()
                    madeira_startup_log("startup-v2: hardware input started")
                    JITNetworkShortcut.shared.restoreLeftover()   // also starts its network path monitor
                    madeira_startup_log("startup-v2: network shortcut restored")
                }
                // madeira://jit-network/... (the Madeira JIT shortcut returning, JITNetwork.swift),
                // else madeira://play?exe=... (Home Screen shortcuts, SavesAndShortcuts.swift).
                .onOpenURL { url in if !JITNetworkShortcut.shared.handle(url) { ShortcutRouter.shared.handle(url) } }
        }
    }
}
