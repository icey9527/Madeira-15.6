import Foundation
import SwiftUI

@main
struct MadeiraApp: App {
    init() {
        madeira_startup_log("startup-v2: SwiftUI App init begin")
        // Opt-in iPhone pointer experiments (PhonePointer.m): before any scene exists.
        madeira_phone_pointer_setup()
        // madeira-bcd: the Metal Performance HUD's performance insights (the
        // notes that stack up left of its panel: shader compiles, similar render
        // passes, blit encoders) cover the game. Off unless madeira.cfg says
        // env.MTL_HUD_INSIGHTS_ENABLED = 1, which is exported later and wins.
        setenv("MTL_HUD_INSIGHTS_ENABLED", "0", 0)
        // Host pads as Windows.Gaming.Input gamepads (upstream's opt-in provider, willfaust/wine #40):
        // on by default here; madeira.cfg env.MADEIRA_WGI_HOST_PADS = 0 is exported later and wins.
        setenv("MADEIRA_WGI_HOST_PADS", "1", 0)
        JITMemoryMode.applyStored()
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
        // A sign-in an installer left goes into the Keychain before anything else runs
        // (SigningRefresh.swift), only while the refresh is switched on.
        if SigningRefresh.enabled { SigningAccountStore.takeIn() }
        // Use one profile before Wine or Steam Cloud reads the prefix.
        ProfileUnification.run()
        // Games and downloads keep going when Madeira leaves the screen (BackgroundRun.swift).
        BackgroundRun.shared.start()
        // Read the cached library before the first grid page is built.
        if LibraryGridArm.enabled, SteamOwnedLibrary.enabled { SteamOwnedLibrary.shared.start() }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .background(InteractionFrameRateInstaller())   // touch frame-rate request (New library grid)
                .modifier(ClaimGamepadEvents())
                .onAppear {
                    madeira_startup_log("startup-v2: ContentView appeared")
                    ExternalDrives.shared.mountAll()
                    GamepadInput.shared.start()
                    madeira_startup_log("startup-v2: gamepad input started")
                    HardwareInput.shared.start()
                    madeira_startup_log("startup-v2: hardware input started")
                    JITNetworkShortcut.shared.restoreLeftover()   // also starts its network path monitor
                    madeira_startup_log("startup-v2: network shortcut restored")
                    WineGeckoModel.shared.installAutomatically()  // on Wi-Fi, so Wine never asks (WineGecko.swift)
                    AchievementWatcher.shared.start()             // unlock banners from Steam's stats files (SteamAchievements.swift)
                    ClaudeHelp.install()                          // the fixes guide beside madeira-log.txt (ClaudeHelp.swift)
                }
                // madeira://jit-network/... (the Madeira JIT shortcut returning, JITNetwork.swift),
                // else a shared game setup (a .madeirasetup file or madeira://setup?d=..., GameSetupShare.swift),
                // else madeira://play?exe=... (Home Screen shortcuts, SavesAndShortcuts.swift).
                .onOpenURL { url in
                    if JITNetworkShortcut.shared.handle(url) || GameSetupInbox.shared.handle(url) { return }
                    ShortcutRouter.shared.handle(url)
                }
        }
    }
}
