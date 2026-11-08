// SPDX-License-Identifier: GPL-3.0-or-later
//
// iOS 15.6 兼容层：上游新代码用到的 iOS 16/17 类型。
//
// 这些替换保持调用点写法不变，只在低版本上换成等价实现：
//   * `Duration` / `.milliseconds`（iOS 16）——用 `TimeInterval` 秒表示，
//     `Task.sleep(for:)` 换成 `Task.sleep(nanoseconds:)`。
//   * `OSAllocatedUnfairLock`（iOS 16）——用 `os_unfair_lock` 包一层，
//     接口保持 `withLock`。
//   * `UIView.animate(springDuration:bounce:...)`（iOS 17）——换算成
//     `usingSpringWithDamping`，参数由弹簧时长/弹性反推。
//   * `ProposedViewSize`（iOS 16）——在低版本用 `CGSize` 的等价逻辑。

import Foundation
import UIKit
import os

// MARK: - Duration

/// iOS 16 的 `Duration` 在 15.6 上不存在；这里用秒表示同一数量。
typealias MadeiraDuration15 = TimeInterval

private func seconds15(_ duration: MadeiraDuration15) -> Double { duration }

extension Double {
    /// `.milliseconds(2)` 的低版本写法：`2.ms15`。
    var ms15: Double { self / 1000.0 }
}

/// `Task.sleep(for:)` 的低版本替代。
func sleep15(for seconds: MadeiraDuration15) async throws {
    try await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
}

// MARK: - OSAllocatedUnfairLock

/// `OSAllocatedUnfairLock`（iOS 16）在 15.6 上的等价物：一个不公平锁加状态。
final class LockedState15<State>: @unchecked Sendable {
    private var state: State
    private var lock = os_unfair_lock_s()

    init(initialState: State) { state = initialState }

    func withLock<R>(_ body: (inout State) throws -> R) rethrows -> R {
        os_unfair_lock_lock(&lock)
        defer { os_unfair_lock_unlock(&lock) }
        return try body(&state)
    }
}

// MARK: - Spring animation

extension UIView {
    /// `UIView.animate(springDuration:bounce:...)`（iOS 17）在 15.6 上的等价调用。
    /// 弹簧时长和弹性换算成 UIKit 的 `damping`/`velocity` 参数。
    static func animateSpring15(duration: TimeInterval, bounce: Double, delay: TimeInterval = 0,
                                options: UIView.AnimationOptions = [], animations: @escaping () -> Void) {
        // bounce 0 → 临界阻尼 1.0；bounce 越大阻尼越小（UIKit 的经验映射）。
        let damping = max(0.05, min(1.0, 1.0 - bounce / 2.0))
        animate(withDuration: duration, delay: delay, usingSpringWithDamping: damping,
                initialSpringVelocity: 0, options: options, animations: animations)
    }
}

// MARK: - ProposedViewSize

/// iOS 16 的 `ProposedViewSize` 在 15.6 上没有；这里只需要「未指定时取默认值」。
struct ProposedSize15 {
    var width: CGFloat?
    var height: CGFloat?

    func replacingUnspecified(by fallback: CGSize) -> CGSize {
        CGSize(width: width ?? fallback.width, height: height ?? fallback.height)
    }
}
