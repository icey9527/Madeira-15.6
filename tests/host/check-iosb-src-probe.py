#!/usr/bin/env python3
"""ml2112 [iosb-src]: the IOSB-cookie probe in prepare-file-write.py.

Pins: both anchors (server_read_file / server_write_file request) are unique in
the wine source; the generated file carries exactly two probes, each inside its
own function and immediately before server_async's SERVER_START_REQ; each probe
logs one return address and no caller-supplied status word; the NtWriteFile
server-path probe logs one return address; the probes are per-thread bounded
(<=32) and log addresses only - never buffer contents or paths. The wine
submodule must stay clean (the build script only writes a copy).

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
         'io_in_window=%d ptr_in_window=%d buf=%p caller=%p\\n"')
assert gen.count(PROBE) == 2, gen.count(PROBE)
assert gen.count("static __thread unsigned int ml2108_iosb_n") == 2
assert gen.count("if (ml2108_n <= 32)") == 2
assert "ios_wow_host_base()" in gen and "in_wow64_call()" in gen


def _err_calls(text):
    calls, i = [], 0
    while True:
        i = text.find('ERR( "', i)
        if i < 0:
            return calls
        j = text.index(");", i)
        calls.append(text[i:j + 2])
        i = j + 2


def _args_of(call):
    end = call.index('"', call.index('"') + 1)
    body = call[end + 1:].strip()
    assert body.endswith(");"), body
    body = body[:-2].strip()
    assert body.startswith(","), body
    body = body[1:]
    args, depth, cur, instr = [], 0, "", False
    for ch in body:
        if ch == '"':
            instr = not instr
        elif not instr and ch in "([{":
            depth += 1
        elif not instr and ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0 and not instr:
            args.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        args.append(cur)
    return args


def _conversions(fmt):
    n = i = 0
    while i < len(fmt):
        if fmt[i] != "%":
            i += 1
            continue
        if i + 1 < len(fmt) and fmt[i + 1] == "%":
            i += 2
            continue
        i += 1
        while i < len(fmt) and fmt[i] in "-+ #0123456789.":
            i += 1
        for p in ("I64", "I32", "ll", "l", "h", "z", "w"):
            if fmt.startswith(p, i):
                i += len(p)
                break
        i += 1
        n += 1
    return n


def _fmt_of(call):
    return call[call.index('"') + 1:call.index('"', call.index('"') + 1)]


iosb = [c for c in _err_calls(gen) if "[iosb-src]" in c]
assert len(iosb) == 2, len(iosb)
for call in iosb:
    fmt, args = _fmt_of(call), _args_of(call)
    assert _conversions(fmt) == len(args), (fmt, args)
    assert sum("__builtin_return_address( 0 )" in a for a in args) == 1, args
    assert all("io->Status" not in a and "memcpy" not in a for a in args), args

server_path = [c for c in _err_calls(gen) if "server-path #%d" in c]
assert len(server_path) == 1, len(server_path)
for call in server_path:
    fmt, args = _fmt_of(call), _args_of(call)
    assert _conversions(fmt) == len(args), (fmt, args)
    assert sum("__builtin_return_address( 0 )" in a for a in args) == 1, args
    assert "no-client-fd caller=%p" in fmt

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

print("iosb-src probe: anchors x1, probes x2 (read/write, bounded 32), "
      "caller x2 + server-path caller x1, wine clean")
