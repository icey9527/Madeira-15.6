#!/usr/bin/env python3
"""Code=7 must not be a device error; the active window must be <= 16 ms.

Extracts production fragments verbatim from dxmt/src/dxmt/dxmt_command_queue.cpp
and compiles them with the host g++:
  1. madeira_active_cache_fresh: a cached answer is trusted for < 16 ms, so a
     background transition is noticed within one 60 Hz frame (the old 200 ms
     window is rejected);
  2. madeira_is_background_rejection: only the BackgroundExecutionNotPermitted
     token (winemetal's Error exposes no domain/code) classifies as Code=7;
  3. WaitForFinishThread: an Error status whose text is Code=7 retires the
     chunk WITHOUT MarkDeviceError whether the app is active or not (the
     rejection already happened; it is logged with the active value);
  4. a non-Code=7 error still calls MarkDeviceError;
  5. a missing command buffer still fails and retires the chunk.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "dxmt" / "src" / "dxmt" / "dxmt_command_queue.cpp"
GXX_CANDIDATES = [
    os.environ.get("CXX", ""),
    r"C:/TDM-GCC-32/bin/g++.exe",
    "g++",
]


def brace_func(lines, marker):
    for i, line in enumerate(lines):
        if marker in line:
            depth = 0
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if j > i and depth == 0:
                    return i + 1, "\n".join(lines[i:j + 1])
            break
    raise SystemExit(f"production helper not found: {marker}")


def find_finish(lines):
    begin = None
    for i, line in enumerate(lines):
        if "CommandQueue::WaitForFinishThread()" in line:
            begin = i
            break
    if begin is None:
        raise SystemExit("production WaitForFinishThread not found")
    start = None
    for i in range(begin, len(lines)):
        if "auto &chunk = chunks[internal_seq % kCommandChunkCount];" in lines[i]:
            start = i
            break
    if start is None:
        raise SystemExit("finish start anchor not found")
    end = None
    for i in range(start, len(lines)):
        if "argbuf_allocator.free_blocks(internal_seq);" in lines[i]:
            end = i
            break
    if end is None or end <= start:
        raise SystemExit("finish retirement end anchor not found")
    text = "\n".join(lines[start:end + 1])
    for required in ("madeira_is_background_rejection", "madeira_get_app_active()",
                     "MarkDeviceError();", "rejected-by-ios", "chunk.reset()"):
        if required not in text:
            raise SystemExit(f"finish lost {required!r}")
    return start + 1, text


HARNESS = r"""
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#define WARN(...) do { (void)0; } while (0)
static int g_err_calls = 0;
#define ERR(...) do { g_err_calls++; } while (0)

enum WMTCommandBufferStatus : uint64_t {
  WMTCommandBufferStatusNotEnqueued = 0,
  WMTCommandBufferStatusEnqueued = 1,
  WMTCommandBufferStatusCommitted = 2,
  WMTCommandBufferStatusScheduled = 3,
  WMTCommandBufferStatusCompleted = 4,
  WMTCommandBufferStatusError = 5,
};

enum class GpuCompletionStatus : uint8_t { Pending, Complete, Failed };

struct GpuCompletionTarget {
  virtual void CompleteGpuWork(GpuCompletionStatus) noexcept = 0;
  virtual ~GpuCompletionTarget() = default;
};

struct String { std::string value; std::string getUTF8String() const { return value; } };
struct CmdbufError { std::string text; String description() const { return String{text}; } };
struct LogElement { String description() const { return String{}; } };
struct Logs {
  std::vector<LogElement> list;
  explicit operator bool() const { return !list.empty(); }
  const std::vector<LogElement> &elements() const { return list; }
};

namespace WMT {
struct CommandBuffer {
  int status_value = 0;
  std::string error_text;
  int status() const { return status_value; }
  Logs logs() const { return {}; }
  CmdbufError error() const { return CmdbufError{error_text}; }
};
template <typename T>
class Reference : public T {
public:
  uintptr_t handle = 0;
  operator bool() const { return handle != 0; }
};
} // namespace WMT

namespace dxmt {
template <typename T> void atomic_notify_one(T &) {}
}

struct RecTarget : GpuCompletionTarget {
  int calls = 0;
  GpuCompletionStatus last = GpuCompletionStatus::Pending;
  void CompleteGpuWork(GpuCompletionStatus s) noexcept override { calls++; last = s; }
};

struct FakeChunk {
  uint64_t frame_ = 0;
  uint64_t signal_frame_latency_fence_ = ~0ull;
  WMT::Reference<WMT::CommandBuffer> attached_cmdbuf;
  std::vector<std::shared_ptr<GpuCompletionTarget>> completion_targets;
  bool reset_called = false;
  void reset() {
    reset_called = true;
    attached_cmdbuf.handle = 0;
    completion_targets.clear();
  }
};

struct FakeFence {
  uint64_t last = 0;
  unsigned count = 0;
  void signal(uint64_t v) { last = v; count++; }
};
struct FakeAlloc {
  uint64_t last = ~0ull;
  unsigned calls = 0;
  void free_blocks(uint64_t s) { last = s; calls++; }
};

constexpr uint32_t kCommandChunkCount = 32;
static std::atomic<int> g_active{1};
extern "C" int madeira_get_app_active(void) {
  return g_active.load(std::memory_order_acquire);
}

@@WINDOW@@
@@CLASSIFY@@

struct FinishHarness {
  std::atomic_bool stopped{false};
  std::atomic<bool> device_error{false};
  unsigned mark_calls = 0;
  FakeChunk chunks[kCommandChunkCount];
  FakeFence frame_latency_fence_;
  FakeFence cpu_coherent;
  std::atomic_uint64_t chunk_ongoing{0};
  FakeAlloc staging_allocator, copy_temp_allocator, argbuf_allocator;
  unsigned gpu_time_calls = 0;

  void MarkDeviceError() {
    mark_calls++;
    device_error.store(true, std::memory_order_release);
  }
  void AccountGpuTime(uintptr_t, uint64_t) { gpu_time_calls++; }

  void finish_once(uint64_t internal_seq) {
    for (;;) {
@@FINISH@@
      break;
    }
  }
};

static int failures = 0;
static void check(bool ok, const char *name) {
  std::printf("%s %s\n", ok ? "PASS" : "FAIL", name);
  if (!ok)
    failures++;
}

static const char *kCode7 =
    "Error Domain=MTLCommandBufferErrorDomain Code=7 \"... "
    "kIOGPUCommandBufferCallbackErrorBackgroundExecutionNotPermitted ...\"";

int main() {
  check(madeira_active_cache_fresh(0), "cache fresh at 0 ms");
  check(madeira_active_cache_fresh(15), "background noticed within 16 ms");
  check(!madeira_active_cache_fresh(16), "cache stale at 16 ms");
  check(!madeira_active_cache_fresh(200), "old 200 ms window rejected");

  check(madeira_is_background_rejection(kCode7),
        "Code=7 background token classified");
  check(!madeira_is_background_rejection(
            "Error Domain=MTLCommandBufferErrorDomain Code=1 \"Invalid Resource\""),
        "other Metal errors are not background");
  check(!madeira_is_background_rejection(nullptr), "null description is not background");

  {
    FinishHarness h;
    const uint64_t seq = 1;
    auto &chunk = h.chunks[seq % kCommandChunkCount];
    chunk.frame_ = 3209;
    chunk.attached_cmdbuf.handle = 0x1;
    chunk.attached_cmdbuf.status_value = WMTCommandBufferStatusError;
    chunk.attached_cmdbuf.error_text = kCode7;
    chunk.signal_frame_latency_fence_ = 4321;
    auto target = std::make_shared<RecTarget>();
    chunk.completion_targets.push_back(target);
    h.chunk_ongoing.store(1);
    g_active.store(0);
    g_err_calls = 0;
    h.finish_once(seq);
    check(h.mark_calls == 0 && !h.device_error.load(),
          "Code=7 in background does not mark a device error");
    check(g_err_calls == 1, "Code=7 in background is logged once");
    check(chunk.reset_called, "rejected chunk is reset");
    check(target->calls == 1 && target->last == GpuCompletionStatus::Complete,
          "rejected chunk retires as complete");
    check(h.frame_latency_fence_.count == 1 && h.frame_latency_fence_.last == 4321,
          "rejected chunk signals its frame-latency fence");
    check(h.cpu_coherent.count == 1 && h.cpu_coherent.last == seq,
          "rejected chunk advances cpu_coherent");
    check(h.chunk_ongoing.load() == 0, "rejected chunk releases chunk_ongoing");
    check(h.staging_allocator.calls == 1 && h.copy_temp_allocator.calls == 1 &&
              h.argbuf_allocator.calls == 1,
          "rejected chunk frees the ring allocators");

    const uint64_t seq2 = 2;
    auto &ok = h.chunks[seq2 % kCommandChunkCount];
    ok.attached_cmdbuf.handle = 0x2;
    ok.attached_cmdbuf.status_value = WMTCommandBufferStatusCompleted;
    ok.signal_frame_latency_fence_ = 4322;
    auto target2 = std::make_shared<RecTarget>();
    ok.completion_targets.push_back(target2);
    h.chunk_ongoing.store(1);
    g_active.store(1);
    h.finish_once(seq2);
    check(target2->calls == 1 && target2->last == GpuCompletionStatus::Complete,
          "finish continues after a rejected chunk");
    check(h.frame_latency_fence_.count == 2 && h.frame_latency_fence_.last == 4322,
          "next chunk signals its own fence");
  }

  {
    FinishHarness h;
    const uint64_t seq = 3;
    auto &chunk = h.chunks[seq % kCommandChunkCount];
    chunk.attached_cmdbuf.handle = 0x3;
    chunk.attached_cmdbuf.status_value = WMTCommandBufferStatusError;
    chunk.attached_cmdbuf.error_text =
        "Error Domain=MTLCommandBufferErrorDomain Code=1 \"Invalid Resource\"";
    chunk.signal_frame_latency_fence_ = 5555;
    auto target = std::make_shared<RecTarget>();
    chunk.completion_targets.push_back(target);
    h.chunk_ongoing.store(1);
    g_active.store(0);
    h.finish_once(seq);
    check(h.mark_calls == 1 && h.device_error.load(),
          "non-Code=7 error still marks a device error");
    check(target->last == GpuCompletionStatus::Failed,
          "non-Code=7 error reports Failed to the target");
    check(chunk.reset_called, "device-error chunk still retires");
    check(h.frame_latency_fence_.last == 5555 && h.cpu_coherent.last == seq &&
              h.staging_allocator.calls == 1 && h.copy_temp_allocator.calls == 1 &&
              h.argbuf_allocator.calls == 1,
          "device-error chunk retires every field");
  }

  {
    FinishHarness h;
    const uint64_t seq = 4;
    auto &chunk = h.chunks[seq % kCommandChunkCount];
    chunk.frame_ = 4322;
    chunk.attached_cmdbuf.handle = 0x4;
    chunk.attached_cmdbuf.status_value = WMTCommandBufferStatusError;
    chunk.attached_cmdbuf.error_text = kCode7;
    chunk.signal_frame_latency_fence_ = 4444;
    auto target = std::make_shared<RecTarget>();
    chunk.completion_targets.push_back(target);
    h.chunk_ongoing.store(1);
    g_active.store(1);
    g_err_calls = 0;
    h.finish_once(seq);
    check(h.mark_calls == 0 && !h.device_error.load(),
          "Code=7 while active does not mark a device error");
    check(g_err_calls == 1, "Code=7 while active is logged once (with active=)");
    check(target->calls == 1 && target->last == GpuCompletionStatus::Complete,
          "active Code=7 chunk retires as complete");
    check(chunk.reset_called && h.frame_latency_fence_.last == 4444 &&
              h.cpu_coherent.last == seq && h.chunk_ongoing.load() == 0 &&
              h.staging_allocator.calls == 1 && h.copy_temp_allocator.calls == 1 &&
              h.argbuf_allocator.calls == 1,
          "active Code=7 chunk retires every field");
  }

  {
    FinishHarness h;
    const uint64_t seq = 5;
    auto &chunk = h.chunks[seq % kCommandChunkCount];
    // No attached command buffer at all: the old fatal path is unchanged.
    auto target = std::make_shared<RecTarget>();
    chunk.completion_targets.push_back(target);
    h.chunk_ongoing.store(1);
    g_active.store(0);
    g_err_calls = 0;
    h.finish_once(seq);
    check(h.mark_calls == 1 && h.device_error.load(),
          "missing cmdbuf still marks a device error");
    check(target->last == GpuCompletionStatus::Failed,
          "missing cmdbuf reports Failed to the target");
    check(chunk.reset_called && h.cpu_coherent.last == seq &&
              h.staging_allocator.calls == 1 && h.copy_temp_allocator.calls == 1 &&
              h.argbuf_allocator.calls == 1,
          "missing cmdbuf still retires every field");
  }

  std::printf(failures ? "FAILED %d\n" : "OK\n", failures);
  return failures ? 1 : 0;
}
"""


def pick_compiler():
    from shutil import which
    for cand in GXX_CANDIDATES:
        if not cand:
            continue
        if os.path.sep in cand or "/" in cand:
            if Path(cand).is_file():
                return cand
        elif which(cand):
            return cand
    return None


def main():
    if not SRC.is_file():
        print(f"missing production source: {SRC}", file=sys.stderr)
        return 2
    lines = SRC.read_text(encoding="utf-8").splitlines()
    window_line, window = brace_func(lines, "static bool madeira_active_cache_fresh(")
    classify_line, classify = brace_func(lines, "static bool madeira_is_background_rejection(")
    finish_line, finish = find_finish(lines)
    print(f"extracted window at dxmt_command_queue.cpp:{window_line}")
    print(f"extracted classify at dxmt_command_queue.cpp:{classify_line}")
    print(f"extracted finish at dxmt_command_queue.cpp:{finish_line}")

    cxx = pick_compiler()
    if not cxx:
        print("no C++ compiler found", file=sys.stderr)
        return 2

    code = (HARNESS.replace("@@WINDOW@@", window)
                   .replace("@@CLASSIFY@@", classify)
                   .replace("@@FINISH@@", finish))
    work = Path(tempfile.mkdtemp(prefix="dxmt-bg7-"))
    src = work / "harness.cpp"
    exe = work / "harness.exe"
    src.write_text(code, encoding="utf-8")
    build = subprocess.run([cxx, "-std=c++17", "-pthread", "-O1", str(src), "-o", str(exe)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stdout)
        print(build.stderr, file=sys.stderr)
        print("FAILED: harness did not compile", file=sys.stderr)
        return 2
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    print(run.stdout, end="")
    if run.stderr:
        print(run.stderr, file=sys.stderr)
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
