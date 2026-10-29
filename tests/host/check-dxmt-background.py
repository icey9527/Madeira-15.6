#!/usr/bin/env python3
"""The background gate and the failed-retirement path in CommandQueue, compiled and executed.

Backgrounding iOS makes Metal refuse new submissions. The first fix dropped the
frame's chunk and still published it to the finish thread, which then polled a
NULL command buffer (status 0) forever: the chunk's shared event, frame-latency
fence, cpu_coherent watermark and completion targets were never retired, and
Present blocked on the frame-latency fence after max_latency frames
(previous.log: bg-commit frame=1172..1174, then gpu-wait frame=1172 status=0 at
2000/4000/6000 ms). The gate now parks the encoder and publishes nothing until
the app is active again; the finish thread now retires a missing command buffer
as a FAILED completion instead of exiting.

This check extracts the two production fragments verbatim from
dxmt/src/dxmt/dxmt_command_queue.cpp -- the CommitChunkInternal background gate
and the WaitForFinishThread body -- drops them into a tiny C++ harness with a
fake command buffer, fence, watermark and completion target, and runs them on
real threads:

  1. foreground: the gate publishes and encode/commit/ready run exactly once;
  2. background: the gate parks, publishes nothing, and release publishes
     exactly once (no double commit);
  3. background + stopped: the gate returns instead of hanging teardown;
  4. finish with a missing cmdbuf: the chunk is retired as failed -- target
     Failed, frame-latency fence signaled, cpu_coherent advanced, chunk reset,
     rings freed -- the thread does NOT exit;
  5. the same harness then retires a normal completed chunk, which is what
     proves the finish thread survived and no fence was left unsignaled;
  6. finish with status 0: the polling loop is interrupted by stopped.

The fragment is compiled with the host g++ (-std=c++17 -pthread). If the
compiler cannot build a thread program, the check reports that and fails --
it does not silently pass.
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


def extract_lines(lines, start, end):
    """Return the inclusive 0-based slice as text."""
    return "\n".join(lines[start:end + 1])


def find_gate(lines):
    begin = None
    for i, line in enumerate(lines):
        if "CommandQueue::CommitChunkInternal(" in line:
            begin = i
            break
    if begin is None:
        raise SystemExit("production CommitChunkInternal not found")
    start = None
    for i in range(begin, len(lines)):
        if "bool paused = false;" in lines[i]:
            start = i
            break
    if start is None:
        raise SystemExit("background gate not found (no 'bool paused = false;')")
    end = None
    for i in range(start, len(lines)):
        if "auto cmdbuf = commandQueue.commandBuffer();" in lines[i]:
            end = i - 1
            break
    if end is None or end <= start:
        raise SystemExit("background gate end anchor not found")
    text = extract_lines(lines, start, end)
    if "ready_for_commit.fetch_add" in text:
        raise SystemExit("gate still publishes a dropped chunk (old implementation)")
    if "madeira_get_app_active" not in text or "stopped.load" not in text:
        raise SystemExit("gate lost its active/stopped checks")
    return start + 1, text


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
    if end is None:
        raise SystemExit("finish retirement end anchor not found")
    text = extract_lines(lines, start, end)
    if "const bool missing_cmdbuf = !chunk.attached_cmdbuf;" not in text:
        raise SystemExit("finish lost the missing-cmdbuf latch")
    if "device_error = missing_cmdbuf ||" not in text:
        raise SystemExit("missing cmdbuf is not folded into device_error")
    if "MarkDeviceError();\n      break;" in text:
        raise SystemExit("finish still exits the thread on a missing cmdbuf")
    for required in ("frame_latency_fence_.signal", "cpu_coherent.signal",
                     "chunk.reset()", "GpuCompletionStatus::Failed",
                     "completion_targets", "!stopped.load(std::memory_order_acquire)"):
        if required not in text:
            raise SystemExit(f"finish retirement lost {required!r}")
    return start + 1, text


HARNESS = r"""
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <thread>
#include <vector>

#define WARN(...) do { (void)0; } while (0)
#define ERR(...)  do { (void)0; } while (0)

/* ---- winemetal shim, same shape the production fragments use ---- */
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

struct String { const char *getUTF8String() const { return ""; } };
struct CmdbufError { String description() const { return {}; } };
struct LogElement { String description() const { return {}; } };
struct Logs {
  std::vector<LogElement> list;
  explicit operator bool() const { return !list.empty(); }
  const std::vector<LogElement> &elements() const { return list; }
};

namespace WMT {
struct CommandBuffer {
  int status_value = 0;
  int status() const { return status_value; }
  Logs logs() const { return {}; }
  CmdbufError error() const { return {}; }
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
static std::atomic_bool stopped{false};
static std::atomic<unsigned> g_encoded{0}, g_committed{0}, g_ready{0};

extern "C" int madeira_get_app_active(void) {
  return g_active.load(std::memory_order_acquire);
}

static void publish_like_production(FakeChunk &chunk, uint64_t seq) {
  /* The three calls CommitChunkInternal makes after the gate. */
  (void)chunk; (void)seq;
  g_encoded.fetch_add(1, std::memory_order_relaxed);
  g_committed.fetch_add(1, std::memory_order_relaxed);
  g_ready.fetch_add(1, std::memory_order_release);
}

/* ---- %(GATE)s ---- */
static void run_gate(FakeChunk &chunk, uint64_t seq) {
%(GATE)s
  publish_like_production(chunk, seq);
}

struct FinishHarness {
  std::atomic_bool stopped{false};
  std::atomic<bool> device_error{false};
  FakeChunk chunks[kCommandChunkCount];
  FakeFence frame_latency_fence_;
  FakeFence cpu_coherent;
  std::atomic_uint64_t chunk_ongoing{0};
  FakeAlloc staging_allocator, copy_temp_allocator, argbuf_allocator;
  unsigned gpu_time_calls = 0;

  void MarkDeviceError() { device_error.store(true, std::memory_order_release); }
  void AccountGpuTime(uintptr_t, uint64_t) { gpu_time_calls++; }

  void finish_once(uint64_t internal_seq) {
    for (;;) {
%(FINISH)s
      break;
    }
  }
};

static int failures = 0;
static void check(bool ok, const char *name) {
  std::printf("%%s %%s\n", ok ? "PASS" : "FAIL", name);
  if (!ok) failures++;
}

static bool wait_flag(std::atomic<bool> &done, std::chrono::milliseconds budget) {
  const auto deadline = std::chrono::steady_clock::now() + budget;
  while (!done.load(std::memory_order_acquire)) {
    if (std::chrono::steady_clock::now() >= deadline)
      return false;
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  return true;
}

int main() {
  /* 1. foreground: gate publishes, encode/commit/ready each run once. */
  {
    FakeChunk chunk; chunk.frame_ = 1;
    g_active.store(1); stopped.store(false);
    g_encoded = 0; g_committed = 0; g_ready = 0;
    run_gate(chunk, 1);
    check(g_encoded.load() == 1 && g_committed.load() == 1 && g_ready.load() == 1,
          "foreground publishes encode/commit/ready exactly once");
  }

  /* 2. background parks, publishes nothing; resume publishes exactly once. */
  {
    FakeChunk chunk; chunk.frame_ = 2;
    g_active.store(0); stopped.store(false);
    g_encoded = 0; g_committed = 0; g_ready = 0;
    std::atomic<bool> done{false};
    std::thread t([&]() { run_gate(chunk, 2); done.store(true, std::memory_order_release); });
    const bool held = !wait_flag(done, std::chrono::milliseconds(150));
    const bool silent = g_ready.load() == 0;
    g_active.store(1, std::memory_order_release);
    const bool finished = wait_flag(done, std::chrono::milliseconds(3000));
    if (finished) t.join(); else { std::printf("FAIL background gate did not resume\n"); failures++; std::_Exit(1); }
    check(held, "background gate parks and does not publish");
    check(silent, "nothing is published while backgrounded");
    check(g_encoded.load() == 1 && g_committed.load() == 1 && g_ready.load() == 1,
          "resume publishes exactly once (no duplicate commit)");
  }

  /* 3. background + stopped: teardown gate returns instead of parking. */
  {
    FakeChunk chunk; chunk.frame_ = 3;
    g_active.store(0); stopped.store(true);
    g_encoded = 0; g_committed = 0; g_ready = 0;
    std::atomic<bool> done{false};
    std::thread t([&]() { run_gate(chunk, 3); done.store(true, std::memory_order_release); });
    const bool exited = wait_flag(done, std::chrono::milliseconds(500));
    if (exited) t.join(); else { std::printf("FAIL background+stopped gate hangs\n"); failures++; std::_Exit(1); }
    check(exited, "background gate exits on stopped");
    check(g_ready.load() == 0, "stopped gate publishes nothing");
    stopped.store(false); g_active.store(1);
  }

  /* 4+5. missing cmdbuf retires as failed and the finish thread survives. */
  {
    FinishHarness h;
    const uint64_t missing_seq = 3;
    auto &missing = h.chunks[missing_seq %% kCommandChunkCount];
    missing.frame_ = 30;
    missing.signal_frame_latency_fence_ = 700;
    auto missing_target = std::make_shared<RecTarget>();
    missing.completion_targets.push_back(missing_target);
    h.chunk_ongoing.store(1);

    std::atomic<bool> done{false};
    std::thread t([&]() { h.finish_once(missing_seq); done.store(true, std::memory_order_release); });
    const bool exited = wait_flag(done, std::chrono::milliseconds(1000));
    if (exited) t.join(); else { std::printf("FAIL missing cmdbuf did not retire\n"); failures++; std::_Exit(1); }
    check(exited, "missing cmdbuf retires instead of parking");
    check(h.device_error.load(), "missing cmdbuf is reported as a device error");
    check(missing.reset_called, "missing cmdbuf chunk is still reset");
    check(missing_target->calls == 1 && missing_target->last == GpuCompletionStatus::Failed,
          "completion target sees a Failed completion");
    check(h.frame_latency_fence_.count == 1 && h.frame_latency_fence_.last == 700,
          "frame-latency fence is signaled on a failed retirement");
    check(h.cpu_coherent.count == 1 && h.cpu_coherent.last == missing_seq,
          "cpu_coherent advances on a failed retirement");
    check(h.chunk_ongoing.load() == 0, "chunk_ongoing is released on a failed retirement");
    check(h.staging_allocator.calls == 1 && h.copy_temp_allocator.calls == 1 &&
          h.argbuf_allocator.calls == 1, "ring allocators are freed on a failed retirement");
    check(h.gpu_time_calls == 0, "no GPU-time accounting for a missing command buffer");

    /* The thread must be alive: retire a normal completed chunk next, whose
       fence must be the second signal (no deadlock on a stale fence). */
    const uint64_t ok_seq = 4;
    auto &ok = h.chunks[ok_seq %% kCommandChunkCount];
    ok.frame_ = 31;
    ok.attached_cmdbuf.handle = 0x55;
    ok.attached_cmdbuf.status_value = WMTCommandBufferStatusCompleted;
    ok.signal_frame_latency_fence_ = 900;
    auto ok_target = std::make_shared<RecTarget>();
    ok.completion_targets.push_back(ok_target);
    h.chunk_ongoing.store(1);
    h.finish_once(ok_seq);
    check(ok.reset_called && ok_target->calls == 1 && ok_target->last == GpuCompletionStatus::Complete,
          "finish continues after a missing cmdbuf and completes normally");
    check(h.frame_latency_fence_.count == 2 && h.frame_latency_fence_.last == 900,
          "second chunk signals its own frame-latency fence");
    check(h.cpu_coherent.last == ok_seq, "second chunk advances cpu_coherent past the failed one");
    check(h.gpu_time_calls == 1, "GPU-time accounting runs for a real command buffer");
  }

  /* 6. finish with status 0: the poll loop is interrupted by stopped. */
  {
    FinishHarness h;
    const uint64_t seq = 5;
    auto &chunk = h.chunks[seq %% kCommandChunkCount];
    chunk.frame_ = 50;
    chunk.attached_cmdbuf.handle = 0x1234;
    chunk.attached_cmdbuf.status_value = 0;
    std::atomic<bool> done{false};
    std::thread t([&]() { h.finish_once(seq); done.store(true, std::memory_order_release); });
    const bool parked = !wait_flag(done, std::chrono::milliseconds(100));
    h.stopped.store(true, std::memory_order_release);
    const bool exited = wait_flag(done, std::chrono::milliseconds(1000));
    if (exited) t.join(); else { std::printf("FAIL status-0 finish ignores stopped\n"); failures++; std::_Exit(1); }
    check(parked, "status-0 finish waits while running");
    check(exited, "status-0 finish exits when stopped");
  }

  std::printf(failures ? "FAILED %%d\n" : "OK\n", failures);
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
    gate_line, gate = find_gate(lines)
    finish_line, finish = find_finish(lines)
    print(f"extracted gate at dxmt_command_queue.cpp:{gate_line}")
    print(f"extracted finish at dxmt_command_queue.cpp:{finish_line}")

    cxx = pick_compiler()
    if not cxx:
        print("no C++ compiler found (tried CXX, C:/TDM-GCC-32/bin/g++.exe, g++)", file=sys.stderr)
        return 2

    code = HARNESS % {"GATE": gate, "FINISH": finish}
    work = Path(tempfile.mkdtemp(prefix="dxmt-bg-"))
    src = work / "harness.cpp"
    exe = work / "harness.exe"
    src.write_text(code, encoding="utf-8")

    build = subprocess.run([cxx, "-std=c++17", "-pthread", "-O0", "-g", str(src), "-o", str(exe)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        print(f"harness did not compile with {cxx} (-std=c++17 -pthread):", file=sys.stderr)
        print(build.stderr.strip(), file=sys.stderr)
        print(f"harness kept at {src}", file=sys.stderr)
        return 2

    try:
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print("harness timed out: the background gate or finish retirement hung", file=sys.stderr)
        return 1
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    if run.returncode != 0:
        print(f"harness failed (exit {run.returncode}); source kept at {src}", file=sys.stderr)
        return run.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
