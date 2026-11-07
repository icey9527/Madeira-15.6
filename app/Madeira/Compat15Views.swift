// SPDX-License-Identifier: GPL-3.0-or-later
//
// iOS 15.6 兼容视图：
//   * `ViewThatFits`（iOS 16）——低版本没有尺寸测量，直接用第一个布局。
//   * `Path.subtracting`（iOS 17）——低版本把它们合成一条路径，配合
//     `contentShape(_, eoFill: true)` 的奇偶填充得到同样的差集。

import SwiftUI

/// `ViewThatFits` 的低版本替身：iOS 15 上取第一个子视图，16 及以上交给系统。
struct StoreTabsFitting15<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        if #available(iOS 16.0, *) {
            ViewThatFits(in: .horizontal) { content() }
        } else {
            content()
        }
    }
}

extension Path {
    /// iOS 17 才有 `Path.subtracting(_:eoFill:)`；低版本把另一条路径并进来，
    /// 由调用方用奇偶填充（`.contentShape(_, eoFill: true)`）画成差集。
    func subtracting15(_ other: Path) -> Path {
        if #available(iOS 17.0, *) {
            return subtracting(other)
        }
        var result = self
        result.addPath(other)
        return result
    }
}
