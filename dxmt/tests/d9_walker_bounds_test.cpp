// Host test for the ml1972 arrival-order walker check.
// Reuses the production pure helper dxmt::d9WalkRefIndexValid from
// d3d9_batch_budget.hpp (no second copy of the algorithm) and mirrors only the
// walker's control flow: an invalid ref terminates the op stream, the tail
// still runs, and no payload is indexed before the helper accepts it.
#include "d3d9_batch_budget.hpp"

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>

namespace {

enum Kind : uint8_t { Draw = 0, Blit = 1, SetRef = 2 };

struct Ref {
  Kind kind;
  uint32_t index;
};

struct Walk {
  bool terminated = false;
  bool tail_ran = false;
  unsigned device_errors = 0;
  std::vector<int> executed; // payload tags actually touched, in order
};

// Same control flow as FlushDrawBatch: check -> payload -> continue; on failure
// latch, set the sticky queue error, break; tail (end_current_pass) always runs.
Walk
run_walker(const std::vector<Ref> &ops, const std::vector<int> &draws, const std::vector<int> &blits,
           size_t ref_ops_size) {
  Walk w;
  for (const auto &ref : ops) {
    size_t bound = 0;
    bool known = false;
    if (!dxmt::d9WalkRefIndexValid(static_cast<uint8_t>(ref.kind), ref.index, draws.size(),
                                   blits.size(), ref_ops_size, bound, known)) {
      assert(!known || ref.index >= bound);
      w.terminated = true;
      ++w.device_errors; // m_dxmtQueue->MarkDeviceError()
      break;             // terminate this op stream; later ops must not run
    }
    switch (ref.kind) {
    case Draw:
      w.executed.push_back(draws[ref.index]); // only reached when in bounds
      break;
    case Blit:
      w.executed.push_back(blits[ref.index]);
      break;
    case SetRef:
      w.executed.push_back(1000 + static_cast<int>(ref.index));
      break;
    }
  }
  w.tail_ran = true; // end_current_pass(); completion target added by caller
  return w;
}

} // namespace

int
main() {
  // 1) Helper mapping: each kind is bounded by its own container.
  {
    size_t bound = 0;
    bool known = false;
    assert(dxmt::d9WalkRefIndexValid(0, 1, 2, 0, 0, bound, known) && known && bound == 2);
    assert(!dxmt::d9WalkRefIndexValid(0, 2, 2, 0, 0, bound, known));
    assert(dxmt::d9WalkRefIndexValid(1, 0, 0, 1, 0, bound, known) && known && bound == 1);
    assert(!dxmt::d9WalkRefIndexValid(1, 1, 0, 1, 0, bound, known));
    assert(dxmt::d9WalkRefIndexValid(2, 0, 0, 0, 1, bound, known) && known && bound == 1);
    assert(!dxmt::d9WalkRefIndexValid(2, 1, 0, 0, 1, bound, known));
    // Unknown kind is invalid and reported as unknown, never treated as blit.
    known = true;
    assert(!dxmt::d9WalkRefIndexValid(3, 0, 0, 64, 0, bound, known) && !known);
    known = true;
    assert(!dxmt::d9WalkRefIndexValid(255, 0, 0, 64, 0, bound, known) && !known);
  }

  const std::vector<int> draws{11, 22};
  const std::vector<int> blits{31, 32};

  // 2) SetRef out of bounds first: the following legal Draw must NOT execute,
  //    the stream terminates, the tail still runs.
  {
    const Walk w = run_walker({{SetRef, 5}, {Draw, 0}, {Blit, 0}}, draws, blits, 1);
    assert(w.terminated);
    assert(w.device_errors == 1);
    assert(w.executed.empty());
    assert(w.tail_ran);
  }

  // 3) Blit out of bounds after a legal Draw: Draw ran, Blit did not, tail ran.
  {
    const Walk w = run_walker({{Draw, 1}, {Blit, 7}}, draws, blits, 4);
    assert(w.terminated && w.tail_ran && w.device_errors == 1);
    assert((w.executed == std::vector<int>{22}));
  }

  // 4) Unknown kind terminates before any payload access.
  {
    const Walk w = run_walker({{static_cast<Kind>(3), 0}, {Draw, 0}}, draws, blits, 4);
    assert(w.terminated && w.executed.empty() && w.tail_ran);
  }

  // 5) All-valid stream keeps arrival order and never latches.
  {
    const Walk w = run_walker({{SetRef, 0}, {Draw, 1}, {Blit, 0}}, draws, blits, 1);
    assert(!w.terminated && w.device_errors == 0 && w.tail_ran);
    assert((w.executed == std::vector<int>{1000, 22, 31}));
  }

  // 6) Compaction keeps indices meaningful (property the walk relies on).
  {
    std::vector<int> batch;
    batch.reserve(64);
    for (int i = 0; i < 8; ++i)
      batch.push_back(100 + i);
    std::vector<Ref> ops;
    for (uint32_t i = 0; i < batch.size(); ++i)
      ops.push_back({Blit, i});
    const size_t before = batch.size();
    dxmt::d9CompactBatch(batch, true, true);
    assert(batch.size() == before);
    for (uint64_t i = 0; i < batch.size(); ++i) {
      assert(batch[i] == 100 + static_cast<int>(i));
      assert(ops[i].index < batch.size());
    }
  }

  std::printf("d9_walker_bounds_test: OK\n");
  return 0;
}
