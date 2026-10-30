/*
 * Host tests for dxmt/src/d3d9/d3d9_draw_trace.hpp (pure logic).
 *
 * Build/run (no iPhone SDK needed):
 *   C:/TDM-GCC-32/bin/g++.exe -std=c++17 -Wall -Wextra \
 *     -I F:/Madeira/Madeira/dxmt/src/d3d9 \
 *     F:/Madeira/Madeira/tests/host/d3d9-draw-trace-test.cpp -o /tmp/d9trace.exe
 *   /tmp/d9trace.exe
 */
#include "d3d9_draw_trace.hpp"

#include <cstdio>
#include <cstring>

namespace {

int g_failures = 0;

void
check(bool cond, const char *what) {
  if (!cond) {
    std::printf("FAIL: %s\n", what);
    ++g_failures;
  }
}

} // namespace

int
main() {
  using dxmt::D9DrawTrace;
  using dxmt::D9DrawTracePoll;
  using dxmt::D9DrawTraceRecord;
  using dxmt::FormatD9DrawTrace;

  /* Default off, zero cost. */
  D9DrawTrace t;
  check(!t.Active() && !t.WantsDraw(), "default off");

  /* Rising edge opens: 8 frames, 256 draws, frame 1. */
  t.SetEnabled(true);
  check(t.Active() && t.WantsDraw(), "enable opens");
  check(t.frames_left == 8 && t.draws_left == 256, "budget 8/256");
  check(t.frame == 1 && t.seq == 0, "frame 1 / seq 0");

  /* A repeated 1 must not restart a live window. */
  t.seq = 7;
  t.SetEnabled(true);
  check(t.seq == 7 && t.draws_left == 256, "repeat 1 does not reset");

  /* Falling edge closes immediately. */
  t.SetEnabled(false);
  check(!t.Active() && !t.WantsDraw(), "disable closes");

  /* Rising again resets the sequence and budget. */
  t.SetEnabled(true);
  check(t.Active() && t.seq == 0 && t.frame == 1 && t.draws_left == 256, "re-enable resets");

  /* 256 captured draws exhaust the window and latch it closed. */
  for (unsigned i = 0; i < D9DrawTrace::kDraws; ++i) {
    check(t.WantsDraw(), "draw within budget");
    t.seq++;
    t.ConsumeDraw();
  }
  check(!t.Active() && !t.WantsDraw(), "256th draw closes");
  t.SetEnabled(true); /* config still says 1 */
  check(!t.Active(), "exhausted window stays latched while cfg is 1");

  /* Only 0 -> 1 re-arms. */
  t.SetEnabled(false);
  t.SetEnabled(true);
  check(t.Active() && t.frame == 1 && t.draws_left == 256, "0->1 re-arms after exhaustion");

  /* The 8th Present closes even when few draws were recorded. */
  for (unsigned i = 0; i < 7; ++i) {
    t.EndFrame();
    check(t.Active(), "frame 1..7 still open");
    check(t.frame == i + 2, "frame advances");
  }
  t.EndFrame();
  check(!t.Active() && !t.WantsDraw(), "8th Present closes");

  /* Draw budget and frame budget are independent. */
  t.SetEnabled(false);
  t.SetEnabled(true);
  for (unsigned i = 0; i < 10; ++i) {
    check(t.WantsDraw(), "draws before frame limit");
    t.seq++;
    t.ConsumeDraw();
  }
  for (unsigned i = 0; i < 8; ++i)
    t.EndFrame();
  check(!t.Active(), "8 frames closes with draws left");

  /* Poll pacing: at most one cfg read per 250 ms, regardless of mtime. */
  D9DrawTracePoll p;
  check(p.Due(0), "poll due at 0");
  check(!p.Due(100), "poll not due at 100");
  check(!p.Due(249), "poll not due at 249");
  check(p.Due(250), "poll due at 250");
  check(!p.Due(499), "poll not due at 499");
  check(p.Due(500), "poll due at 500");

  /* Record formatting carries every requested axis. */
  D9DrawTraceRecord r{};
  r.device = 2;
  r.seq = 3;
  r.frame = 4;
  r.primitive = 5;
  r.count = 6;
  r.tex0_w = 64;
  r.tex0_h = 32;
  r.tex0_format = 21;
  r.blend_enable = 1;
  r.src_blend = 2;
  r.dst_blend = 3;
  r.blend_op = 1;
  r.write_mask = 0xf;
  r.alpha_test = 0;
  r.z_enable = 1;
  r.z_write = 1;
  char line[640];
  int n = FormatD9DrawTrace(line, sizeof line, r);
  check(n > 0 && static_cast<size_t>(n) < sizeof line, "format fits");
  check(std::strstr(line, "dev=2 seq=3 frame=4") != nullptr, "format identity");
  check(std::strstr(line, "64x32 fmt=21") != nullptr, "format tex0");
  check(std::strstr(line, "blend=1 src=2 dst=3 op=1 wmask=0xf") != nullptr, "format blend");
  check(std::strstr(line, "z=1/1") != nullptr, "format depth");

  if (g_failures) {
    std::printf("%d check(s) failed\n", g_failures);
    return 1;
  }
  std::printf("d3d9_draw_trace host tests passed\n");
  return 0;
}
