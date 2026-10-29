#include "dxmt_command_queue.hpp"
#include "Metal.hpp"
#include "dxmt_statistics.hpp"
#include "util_env.hpp"
#include "util_win32_compat.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <thread>
#include <cstdio>
#if defined(_WIN32)
#include <sys/stat.h>   /* _stat64: the background marker below */
#endif

#if defined(_WIN32)
/* The arm64ec PE build cannot see winemetal's unix-side atomic, so the app
 * publishes the state as a file at the prefix root -- one byte means the app is
 * active, an empty file means it is backgrounded -- and this side stats it.
 * 200 ms is a cache, not a poll: without it this would be 60 file lookups a
 * second, and _stat64 costs one syscall and opens nothing.
 *
 * Why it matters: iOS refuses GPU submissions from a backgrounded app (Code=7,
 * kIOGPUCommandBufferCallbackErrorBackgroundExecutionNotPermitted) and the
 * error path that followed killed TheShell with c0000023 on 2026-10-29.
 *
 * Keyed on _WIN32, not DXMT_IOS: meson defines DXMT_IOS for every Windows host
 * system, so it cannot tell the PE module from the unix library. */
extern "C" int madeira_get_app_active(void) {
  static thread_local int cached = 1;
  static thread_local std::chrono::steady_clock::time_point last;
  auto now = std::chrono::steady_clock::now();
  if (last != std::chrono::steady_clock::time_point{} &&
      std::chrono::duration_cast<std::chrono::milliseconds>(now - last).count() < 200)
    return cached;
  last = now;
  struct _stat64 info;
  if (_stat64("C:\\madeira-active", &info) == 0)
    cached = info.st_size ? 1 : 0;
  return cached;
}
#else
// 原生库直接读取 winemetal 的真实状态，不用恒为前台的弱定义兜底。
extern "C" int madeira_get_app_active(void);
#endif

#define ASYNC_ENCODING 1

namespace dxmt {

void *
CommandChunk::allocate_cpu_heap(size_t size, size_t alignment) {
  return queue->AllocateCommandData(size, alignment);
}

CommandQueue::CommandQueue(WMT::Device device) :
    encodeThread([this]() { this->EncodingThread(); }),
    finishThread([this]() { this->WaitForFinishThread(); }),
    device(device),
    commandQueue(device.newCommandQueue(kCommandChunkCount)),
    shared_event_listener(SharedEventListener_create()),
    event_listener_thread([this]() { SharedEventListener_start(this->shared_event_listener); }),
    staging_allocator({
        device, WMTResourceOptionCPUCacheModeWriteCombined | WMTResourceHazardTrackingModeUntracked |
                    WMTResourceStorageModeManaged, false
    }),
    copy_temp_allocator({device, WMTResourceHazardTrackingModeUntracked | WMTResourceStorageModePrivate}),
    argbuf_allocator({
        device,
        WMTResourceHazardTrackingModeUntracked | WMTResourceCPUCacheModeWriteCombined | WMTResourceStorageModeShared
    }),
    cpu_command_allocator({}),
    reftracker_storage_allocator({}),
    cmd_library(device),
    argument_encoding_ctx(*this, device, cmd_library),
    initializer(device) {
  for (unsigned i = 0; i < kCommandChunkCount; i++) {
    auto &chunk = chunks[i];
    chunk.queue = this;
    chunk.reset();
  };
  event = device.newSharedEvent();

  std::string env = env::getEnvVar("DXMT_CAPTURE_FRAME");

  if (!env.empty()) {
    try {
      capture_state.scheduleNextFrameCapture(std::stoull(env));
    } catch (const std::invalid_argument &) {
    }
  }
}

CommandQueue::~CommandQueue() {
  TRACE("Destructing command queue");
  stopped.store(true);
  ready_for_encode++;
  dxmt::atomic_notify_one(ready_for_encode);
  ready_for_commit++;
  dxmt::atomic_notify_one(ready_for_commit);
  SharedEventListener_destroy(shared_event_listener);
  encodeThread.join();
  finishThread.join();
  for (unsigned i = 0; i < kCommandChunkCount; i++) {
    auto &chunk = chunks[i];
    chunk.reset();
  };
  event_listener_thread.join();
  TRACE("Destructed command queue");
}

void
CommandQueue::CommitCurrentChunk() {
  auto chunk_id = ready_for_encode.load(std::memory_order_relaxed);
  auto &chunk = chunks[chunk_id % kCommandChunkCount];
  chunk.chunk_id = chunk_id;
  chunk.chunk_event_id = GetNextEventSeqId();
  chunk.frame_ = frame_count;
  chunk.resource_initializer_event_id = initializer.flushToWait();
  auto& statistics = CurrentFrameStatistics();
  statistics.command_buffer_count++;
#if ASYNC_ENCODING
  ready_for_encode.fetch_add(1, std::memory_order_release);
  dxmt::atomic_notify_one(ready_for_encode);

  auto t0 = clock::now();
  dxmt::atomic_wait(chunk_ongoing, uint64_t(kCommandChunkCount - 1), std::memory_order_acquire);
  chunk_ongoing.fetch_add(1, std::memory_order_relaxed);
  auto t1 = clock::now();
  statistics.commit_interval += (t1 - t0);

#else
  CommitChunkInternal(chunk, ready_for_encode.fetch_add(1, std::memory_order_relaxed));
#endif

  cpu_command_allocator.free_blocks(cpu_coherent.signaledValue());
}

void
CommandQueue::CommitChunkInternal(CommandChunk &chunk, uint64_t seq) {

  auto pool = WMT::MakeAutoreleasePool();

  switch (capture_state.getNextAction(chunk.frame_)) {
  case CaptureState::NextAction::StartCapture: {
    WMTCaptureInfo info;
    auto capture_mgr = WMT::CaptureManager::sharedCaptureManager();
    info.capture_object = device;
    info.destination = WMTCaptureDestinationGPUTraceDocument;
    char filename[1024];
    std::time_t now;
    std::time(&now);
    std::strftime(filename, 1024, "_%H'%M'%S_%m-%d-%y.gputrace", std::localtime(&now));
    auto fileUrl = env::getUnixPath(env::getExeBaseName() + "_F." + std::to_string(chunk.frame_) + filename);
    WARN("A new capture will be saved to ", fileUrl);
    info.output_url.set(fileUrl.c_str());

    capture_mgr.startCapture(info);
    break;
  }
  case CaptureState::NextAction::StopCapture: {
    auto capture_mgr = WMT::CaptureManager::sharedCaptureManager();
    capture_mgr.stopCapture();
    break;
  }
  case CaptureState::NextAction::Nothing: {
    if (capture_state.shouldCaptureNextFrame()) {
      capture_state.scheduleNextFrameCapture(chunk.frame_ + 1);
    }
    break;
  }
  }

  // chunk 已持有查询、共享事件及帧延迟 fence，不能只跳过 encode 后发布。
  // 那会交给完成线程一个空 cmdbuf（status=0），它永远等不到完成。
  // 后台暂停消费，保留整个 chunk；前台恢复后仍按原顺序提交。
  bool paused = false;
  while (!madeira_get_app_active()) {
    if (stopped.load(std::memory_order_acquire))
      return;
    if (!paused) {
      WARN("[bg-commit] paused frame=", chunk.frame_, " seq=", seq);
      paused = true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  if (stopped.load(std::memory_order_acquire))
    return;
  if (paused)
    WARN("[bg-commit] resumed frame=", chunk.frame_, " seq=", seq);
  auto cmdbuf = commandQueue.commandBuffer();
  chunk.attached_cmdbuf = cmdbuf;
  if (chunk.resource_initializer_event_id) {
    cmdbuf.encodeWaitForEvent(initializer.event(), chunk.resource_initializer_event_id);
  }
  chunk.encode(chunk.attached_cmdbuf, this->argument_encoding_ctx);
  cmdbuf.commit();

  ready_for_commit.fetch_add(1, std::memory_order_release);
  dxmt::atomic_notify_one(ready_for_commit);
}

uint32_t
CommandQueue::EncodingThread() {
#if ASYNC_ENCODING
  env::setThreadName("dxmt-encode-thread");
  SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);
  uint64_t internal_seq = 1;
  while (!stopped.load()) {
    dxmt::atomic_wait(ready_for_encode, internal_seq, std::memory_order_acquire);
    if (stopped.load())
      break;
    // perform...
    auto &chunk = chunks[internal_seq % kCommandChunkCount];
    CommitChunkInternal(chunk, internal_seq);
    internal_seq++;
  }
  TRACE("encoder thread gracefully terminates");
#endif
  return 0;
}

uint32_t
CommandQueue::WaitForFinishThread() {
  env::setThreadName("dxmt-finish-thread");
  SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);
  uint64_t internal_seq = 1;
  while (!stopped.load()) {
    dxmt::atomic_wait(ready_for_commit, internal_seq, std::memory_order_acquire);
    if (stopped.load())
      break;
    auto &chunk = chunks[internal_seq % kCommandChunkCount];
    const bool missing_cmdbuf = !chunk.attached_cmdbuf;
    if (missing_cmdbuf)
      ERR("[gpu-wait] missing command buffer frame=", chunk.frame_, " seq=", internal_seq);
    if (!missing_cmdbuf && chunk.attached_cmdbuf.status() <= WMTCommandBufferStatusScheduled) {
      unsigned madeira_wait_ms = 0;
      while (!stopped.load(std::memory_order_acquire) &&
             chunk.attached_cmdbuf.status() <= WMTCommandBufferStatusScheduled) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        madeira_wait_ms += 10;
        if ((madeira_wait_ms % 2000) == 0) {
          static unsigned madeira_gpu_wait_said;
          if (madeira_gpu_wait_said++ < 20)
            ERR("[gpu-wait] frame=", chunk.frame_, " status=",
                (int)chunk.attached_cmdbuf.status(), " waited_ms=", madeira_wait_ms);
        }
      }
    }
    if (stopped.load(std::memory_order_acquire))
      break;
    const bool device_error = missing_cmdbuf || chunk.attached_cmdbuf.status() == WMTCommandBufferStatusError;
    if (device_error) {
      if (!missing_cmdbuf)
        ERR("Device error at frame ", chunk.frame_, ": ", chunk.attached_cmdbuf.error().description().getUTF8String());
      MarkDeviceError();
    }
    if (!missing_cmdbuf) {
      if (auto logs = chunk.attached_cmdbuf.logs()) {
        for (auto &log : logs.elements()) {
          ERR("Frame ", chunk.frame_, ": ", log.description().getUTF8String());
        }
      }
      AccountGpuTime(chunk.attached_cmdbuf.handle, chunk.frame_);   /* ml1254 */
    }

    if (chunk.signal_frame_latency_fence_ != ~0ull)
      frame_latency_fence_.signal(chunk.signal_frame_latency_fence_);

    for (const auto &target : chunk.completion_targets)
      target->CompleteGpuWork(device_error ? GpuCompletionStatus::Failed : GpuCompletionStatus::Complete);

    chunk.reset();
    cpu_coherent.signal(internal_seq);
    chunk_ongoing.fetch_sub(1, std::memory_order_release);
    dxmt::atomic_notify_one(chunk_ongoing);

    staging_allocator.free_blocks(internal_seq);
    copy_temp_allocator.free_blocks(internal_seq);
    argbuf_allocator.free_blocks(internal_seq);

    internal_seq++;
  }
  TRACE("finishing thread gracefully terminates");
  return 0;
}

/* ml1254: see PerfCounters. Command buffers of one queue run in order, so the
 * busy time is the union of their [GPUStartTime, GPUEndTime] intervals. */
void
CommandQueue::AccountGpuTime(obj_handle_t cmdbuf, uint64_t frame) {
  struct { uint64_t cb; double start, end; } t = {(uint64_t)cmdbuf, 0.0, 0.0};
  struct madeira_ctl_args a = {};
  a.op = 3;
  a.ptr = (uint64_t)(uintptr_t)&t;
  MadeiraCtl(&a);
  if (!a.ret || t.end <= 0.0)
    return;
  if (perf_.window_frame == ~0ull) {
    perf_.window_frame = frame;
    perf_.window_start = t.start;
  }
  const double from = std::max(t.start, perf_.last_end);
  if (t.end > from)
    perf_.busy += t.end - from;
  perf_.last_end = std::max(perf_.last_end, t.end);
  perf_.cmdbufs++;
  if (frame < perf_.window_frame + 64)
    return;
  const double frames = double(frame - perf_.window_frame);
  const double wall_ms = (perf_.last_end - perf_.window_start) * 1000.0;
  const double busy_ms = perf_.busy * 1000.0;
  auto take = [](std::atomic<uint64_t> &c) { return double(c.exchange(0, std::memory_order_relaxed)); };
  const double waits = take(g_perf.map_waits), wait_ms = take(g_perf.map_wait_ns) / 1e6,
               unsubmitted = take(g_perf.map_waits_unsubmitted);
  const double pe = take(g_perf.polls_event), po = take(g_perf.polls_other);
  const double draws = take(g_perf.draws), tess = take(g_perf.draws_tess), gs = take(g_perf.draws_gs);
  const double dec_ms = take(g_perf.decode_ns) / 1e6, refill_ms = take(g_perf.refill_ns) / 1e6;
  const double rw_ms = take(g_perf.refill_worker_ns) / 1e6, rq = take(g_perf.refill_queued),
               ra = take(g_perf.refill_applied), rs = take(g_perf.refill_stale), ro = take(g_perf.refill_overbudget);
  char line[800];
  snprintf(line, sizeof(line),
           "[gpu-perf] ml1254 %.0f frames in %.0f ms (%.1f fps): GPU busy %.0f ms = %.0f%% (%.1f ms/frame), "
           "%.1f cmdbufs/frame | readback Map waits %.2f/frame, %.1f ms/frame (%.2f/frame on unsubmitted work) | "
           "not-ready polls/frame: event %.1f, other %.1f | draws/frame %.0f, tess %.0f, GS %.0f | "
           "game-thread BC decode %.2f ms/frame (%.0f ms per 64 frames), lost-level refill %.2f ms/frame | "
           "async refill: worker %.2f ms/frame, queued %.0f applied %.0f stale %.0f over-budget %.0f",
           frames, wall_ms, wall_ms > 0 ? frames * 1000.0 / wall_ms : 0.0, busy_ms,
           wall_ms > 0 ? busy_ms * 100.0 / wall_ms : 0.0, busy_ms / frames, perf_.cmdbufs / frames, waits / frames,
           wait_ms / frames, unsubmitted / frames, pe / frames, po / frames, draws / frames, tess / frames, gs / frames,
           dec_ms / frames, dec_ms, refill_ms / frames, rw_ms / frames, rq, ra, rs, ro);
  ERR(line);
  perf_.window_frame = frame;
  perf_.window_start = perf_.last_end;
  perf_.busy = 0.0;
  perf_.cmdbufs = 0;
}

void CommandQueue::Retain(uint64_t seq, Allocation* allocaiton) {
  auto &chunk = chunks[seq % kCommandChunkCount];
  auto &tracker = chunk.ref_tracker;
  constexpr size_t block_size = decltype(reftracker_storage_allocator)::block_size;
  while (unlikely(!tracker.track(allocaiton))) {
    auto [temp_buffer, _] = reftracker_storage_allocator.allocate(seq, cpu_coherent.signaledValue(), block_size, 1);
    tracker.addStorage(temp_buffer.ptr, block_size);
  }
};

// MADEIRA: see dxmt_command_queue.hpp for why a non-blocking poller needs
// this instead of WaitCPUFence.
bool
CommandQueue::WaitCPUFenceBounded(uint64_t seq, uint64_t timeout_ns) {
  if (cpu_coherent.signaledValue() >= seq)
    return true;
  // Phase 1: a short load-spin. The watermark is published by the finish
  // thread with a release store, so a re-check costs one acquire load; if the
  // command buffer is about to retire this catches it without entering the
  // scheduler at all.
  constexpr unsigned kSpinIterations = 64;
  for (unsigned i = 0; i < kSpinIterations; i++) {
    if (cpu_coherent.signaledValue() >= seq)
      return true;
  }
  // Phase 2: yield until the deadline. this_thread::yield is SwitchToThread on
  // the PE build, so the encode / finish threads (both TIME_CRITICAL) get the
  // core the poller would otherwise have burned. The clock is read once per
  // yield, because reading it is the expensive part of this loop.
  const auto deadline = clock::now() + std::chrono::nanoseconds(timeout_ns);
  do {
    this_thread::yield();
    if (cpu_coherent.signaledValue() >= seq)
      return true;
  } while (clock::now() < deadline);
  return cpu_coherent.signaledValue() >= seq;
}

void
CommandQueue::MarkDeviceError() {
  device_error_.store(true, std::memory_order_release);
}

} // namespace dxmt
