/*
 * d3d9_draw_trace.hpp -- Madeira native D3D9 dynamic draw trace (pure logic).
 *
 * No d3d9.h / Metal / file IO dependency: the device owns the cfg polling and
 * fills a D9DrawTraceRecord from the calling-thread state frozen at
 * QueueBatchedDraw. This header is host-testable (tests/host).
 *
 * Contract:
 *  - Default off. A rising edge in Documents/madeira.cfg
 *    "env.DXMT_D9_DRAW_TRACE = 1/on/true/yes" opens the window.
 *  - The window covers the next kFrames frames, at most kDraws records.
 *  - Exhausting either budget closes the window and latches it closed even
 *    while the config value stays 1. A run-out budget never re-opens by
 *    itself: only setting the value to 0 and back to 1 opens a new window.
 *  - Every device holds its own instance; there is no shared/global budget.
 */
#pragma once

#include <cinttypes>
#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace dxmt {

struct D9DrawTraceRecord {
  uint32_t device = 0;
  uint32_t frame = 0;
  uint64_t seq = 0;
  // Draw shape (frozen at QueueBatchedDraw).
  uint32_t primitive = 0;
  uint32_t count = 0;
  uint32_t start = 0;
  int32_t base_vertex = 0;
  uint32_t indexed = 0;
  uint32_t fixed_shader = 0;
  uintptr_t vs = 0;
  uintptr_t ps = 0;
  // Render target 0 / depth-stencil 0.
  uintptr_t rt0 = 0;
  uint32_t rt0_format = 0;
  uint32_t rt0_w = 0;
  uint32_t rt0_h = 0;
  uintptr_t ds = 0;
  uint32_t ds_format = 0;
  uint32_t ds_w = 0;
  uint32_t ds_h = 0;
  // Stage-0 bound texture (identity + declared format + level-0 size only;
  // no pixel content is ever read).
  uintptr_t tex0 = 0;
  uint32_t tex0_format = 0;
  uint32_t tex0_w = 0;
  uint32_t tex0_h = 0;
  // Blend / alpha / depth, straight from the per-draw render-state block.
  uint32_t blend_enable = 0;
  uint32_t src_blend = 0;
  uint32_t dst_blend = 0;
  uint32_t blend_op = 0;
  uint32_t write_mask = 0;
  uint32_t alpha_test = 0;
  uint32_t alpha_ref = 0;
  uint32_t z_enable = 0;
  uint32_t z_write = 0;
  // Viewport / scissor, from the per-draw POD snapshot.
  int32_t vp_x = 0;
  int32_t vp_y = 0;
  uint32_t vp_w = 0;
  uint32_t vp_h = 0;
  float vp_minz = 0.0f;
  float vp_maxz = 1.0f;
  int32_t sc_l = 0;
  int32_t sc_t = 0;
  int32_t sc_r = 0;
  int32_t sc_b = 0;
};

struct D9DrawTrace {
  static constexpr uint32_t kFrames = 8;
  static constexpr uint32_t kDraws = 256;

  bool config_on = false; // last value the device polled
  bool window = false;    // capture window open
  uint32_t frames_left = 0;
  uint32_t draws_left = 0;
  uint32_t frame = 0;
  uint64_t seq = 0;

  bool Active() const { return window; }
  bool WantsDraw() const { return window && draws_left != 0; }

  /* Called from the Present poll. Only a 0->1 edge opens a window; a
   * repeated 1 while the window is latched closed must NOT restart it. */
  void
  SetEnabled(bool enabled) {
    if (enabled && !config_on) {
      window = true;
      frames_left = kFrames;
      draws_left = kDraws;
      frame = 1;
      seq = 0;
    } else if (!enabled) {
      window = false;
      frames_left = 0;
      draws_left = 0;
    }
    config_on = enabled;
  }

  /* End of a frame (Present). The 8th Present closes the window. */
  void
  EndFrame() {
    if (!window)
      return;
    if (frames_left)
      --frames_left;
    if (!frames_left) {
      window = false;
      draws_left = 0;
      return;
    }
    ++frame;
  }

  /* One captured draw. The 256th closes the window. */
  void
  ConsumeDraw() {
    if (!window)
      return;
    if (draws_left)
      --draws_left;
    if (!draws_left) {
      window = false;
      frames_left = 0;
    }
  }
};

/* Poll pacing: at most one cfg read per kIntervalMs. Deliberately time-based
 * rather than mtime/size-based, because a 0->1 edit keeps the same length and
 * coarse timestamps can miss it. */
struct D9DrawTracePoll {
  static constexpr uint64_t kIntervalMs = 250;
  uint64_t next_ms = 0;

  bool
  Due(uint64_t now_ms) {
    if (now_ms < next_ms)
      return false;
    next_ms = now_ms + kIntervalMs;
    return true;
  }
};

inline int
FormatD9DrawTrace(char *out, size_t cap, const D9DrawTraceRecord &r) {
  return std::snprintf(
      out, cap,
      "dev=%u seq=%" PRIu64 " frame=%u prim=%u n=%u start=%u base=%d idx=%u fixed=%u "
      "vs=%p ps=%p rt0=%p %ux%u fmt=%u ds=%p %ux%u fmt=%u "
      "tex0=%p %ux%u fmt=%u blend=%u src=%u dst=%u op=%u wmask=0x%x "
      "atest=%u aref=%u z=%u/%u vp=%d,%d,%ux%u zf=%g..%g sc=%d,%d,%d,%d",
      r.device, r.seq, r.frame, r.primitive, r.count, r.start,
      r.base_vertex, r.indexed, r.fixed_shader, reinterpret_cast<const void *>(r.vs),
      reinterpret_cast<const void *>(r.ps), reinterpret_cast<const void *>(r.rt0), r.rt0_w, r.rt0_h,
      r.rt0_format, reinterpret_cast<const void *>(r.ds), r.ds_w, r.ds_h, r.ds_format,
      reinterpret_cast<const void *>(r.tex0), r.tex0_w, r.tex0_h, r.tex0_format, r.blend_enable,
      r.src_blend, r.dst_blend, r.blend_op, r.write_mask, r.alpha_test, r.alpha_ref, r.z_enable,
      r.z_write, r.vp_x, r.vp_y, r.vp_w, r.vp_h, r.vp_minz, r.vp_maxz, r.sc_l, r.sc_t, r.sc_r,
      r.sc_b
  );
}

} // namespace dxmt
