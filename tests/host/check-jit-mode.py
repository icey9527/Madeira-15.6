#!/usr/bin/env python3
"""Host check for JIT memory mode resolution."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "app/Madeira/JITAllocator.c").read_text(encoding="utf-8")


def extract_function(text, signature):
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    if "__builtin_available(iOS 16" in source:
        raise AssertionError("the iOS 16 TrollStore gate is still present")

    resolver = extract_function(source, "static int jit_resolve_mode(")
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        raise RuntimeError("host clang++ or g++ is required")

    harness = """#include <cassert>

typedef enum {
    JIT_MODE_AUTOMATIC = 0,
    JIT_MODE_TROLLSTORE = 1,
    JIT_MODE_STIKDEBUG = 2,
} jit_memory_mode_t;

""" + resolver + """

int main() {
    assert(jit_resolve_mode(0, true) == 1);
    assert(jit_resolve_mode(0, false) == 2);
    assert(jit_resolve_mode(1, false) == 1);
    assert(jit_resolve_mode(1, true) == 1);
    assert(jit_resolve_mode(2, true) == 2);
    assert(jit_resolve_mode(2, false) == 2);
}
"""

    with tempfile.TemporaryDirectory(prefix="madeira-jit-mode-") as directory:
        harness_path = Path(directory) / "jit_mode_harness.cpp"
        binary_path = Path(directory) / "jit_mode_harness"
        harness_path.write_text(harness)
        subprocess.run(
            [compiler, "-std=c++11", str(harness_path), "-o", str(binary_path)],
            check=True,
        )
        subprocess.run([str(binary_path)], check=True)

    print("check-jit-mode: OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"check-jit-mode: FAIL: {error}", file=sys.stderr)
        sys.exit(1)
