"""Patch the pinned FEX tree the iOS build compiles.

FEX carries a Madeira-only guard (FEXCore/Source/Interface/Core/Core.cpp) that
refuses to compile a guest RIP below 0x10000 and returns 0 -- but the dispatcher
branches to whatever CompileBlock returns, so the process jumped to host pc 0
with no guest state left to report (GAML, 2026-10-27: a null function pointer
became an unattributable c0000005). Raise the guest fault instead, the way the
pool-rip fix a few lines below already does.
"""

import sys
from pathlib import Path

OLD = """  if (GuestRIP < 0x10000) {
    LogMan::Msg::IFmt("[iOS] CompileBlock: REFUSING low/invalid RIP={:#x}", GuestRIP);
    return 0;
  }
"""

NEW = """  if (GuestRIP < 0x10000) {
    /* Madeira-15.6: the dispatcher BRANCHES to what this returns, so returning
     * 0 jumped to host pc 0 and destroyed the guest state that names the bad
     * caller. Report the guest fault at the real RIP instead. */
    static std::atomic<uint32_t> LowRip {0};
    const uint32_t N = LowRip.fetch_add(1, std::memory_order_relaxed);
    if (N < 24) {
      LogMan::Msg::EFmt("[low-rip] #{} CompileBlock: guest RIP {:#x} is not code -- raising the "
                        "guest fault at that RIP instead of returning a branch target",
                        N + 1, GuestRIP);
    }
    Frame->State.rip = GuestRIP;
    return Frame->Pointers.GuestSignal_SIGSEGV;
  }
"""


def main(root):
    path = Path(root) / "FEXCore/Source/Interface/Core/Core.cpp"
    text = path.read_text(encoding="utf-8")
    if NEW.splitlines()[1] in text:
        print("FEX low-RIP guard already patched")
        return 0
    if text.count(OLD) != 1:
        raise SystemExit("FEX low-RIP guard pattern changed; review Core.cpp")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print("FEX low-RIP guard: guest fault raised at the bad RIP")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
