#!/usr/bin/env python3
"""ml2112 [iosb-src]: the IOSB-cookie probe in prepare-file-write.py.

Pins: both anchors (server_read_file / server_write_file request) are unique in
the wine source; the generated file carries exactly two probes, each inside its
own function and immediately before server_async's SERVER_START_REQ; the probes
are per-thread bounded (<=32) and log addresses only - never buffer contents or
paths. The wine submodule must stay clean (the build script only writes a copy).

Run: python -I tests/host/check-iosb-src-probe.py
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "build/ntdll-unix/prepare-file-write.py"
WINE = ROOT / "wine"
FILE_C = WINE / "dlls/ntdll/unix/file.c"

src = FILE_C.read_text(encoding="utf-8")
for anchor in ("    SERVER_START_REQ( read )\n", "    SERVER_START_REQ( write )\n"):
    n = src.count(anchor)
    assert n == 1, f"anchor {anchor!r} appears {n} times in file.c"

with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "file.c"
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), str(WINE), str(out)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    gen = out.read_text(encoding="utf-8")

PROBE = ('"[iosb-src] tid=%04x handle=%p io=%p ptr=%p wowcall=%d hostbase=%p '
         'io_in_window=%d ptr_in_window=%d buf=%p\\n"')
assert gen.count(PROBE) == 2, gen.count(PROBE)
assert gen.count("static __thread unsigned int ml2108_iosb_n") == 2
assert gen.count("if (ml2108_n <= 32)") == 2
assert "ios_wow_host_base()" in gen and "in_wow64_call()" in gen

for fn, req in (("static unsigned int server_read_file", "    SERVER_START_REQ( read )\n"),
                ("static unsigned int server_write_file", "    SERVER_START_REQ( write )\n")):
    start = gen.index(fn)
    probe = gen.index(PROBE, start)
    block = gen.index(req, start)
    assert start < probe < block, f"probe not before {req!r} in {fn}"

assert "ml2108_seen" not in gen and "ml2108_wait_handles" not in gen

status = subprocess.run(["git", "-C", str(WINE), "status", "--porcelain"],
                        capture_output=True, text=True)
assert status.returncode == 0 and status.stdout.strip() == "", status.stdout

print("iosb-src probe: anchors x1, probes x2 (read/write, bounded 32), wine clean")
