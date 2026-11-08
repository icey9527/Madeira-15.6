#!/usr/bin/env python3
"""Rewrite tools/ci/upstream-compat.sha256 from the committed farm.

Run this after a merge that adds, replaces or removes Windows binaries, in the
same commit as the binaries themselves, then check the diff with
`git diff tools/ci/upstream-compat.sha256` before committing: every line that
changes is a shipped DLL changing.  verify-upstream-compat.py keeps failing the
build on any drift that this was not run for.

The files CI builds from source (xtajit/ntdll) and the whole i386 farm are left
out, exactly as the verifier leaves them out.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST = HERE / "upstream-compat.sha256"

DIRS = ("i386-windows", "aarch64-windows", "arm64ec-windows", "d3d12")
BUILD_OUTPUTS = {
    "aarch64-windows/xtajit.dll",
    "aarch64-windows/ntdll.dll",
    "arm64ec-windows/xtajit64.dll",
    "arm64ec-windows/ntdll.dll",
}
SKIP_DIRS = {"i386-windows"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    bundle = Path(argv[1]) if len(argv) > 1 else ROOT / "app/Madeira"
    entries: dict[str, str] = {}
    for name in DIRS:
        if name in SKIP_DIRS:
            continue
        base = bundle / name
        if not base.is_dir():
            raise SystemExit(f"missing compatibility directory: {base}")
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.relative_to(bundle).as_posix() in BUILD_OUTPUTS:
                continue
            entries[f"app/Madeira/{path.relative_to(bundle).as_posix()}"] = sha256(path)

    old = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, relative = line.split("  ", 1)
                old[relative] = digest

    added = sorted(set(entries) - set(old))
    removed = sorted(set(old) - set(entries))
    changed = sorted(k for k in entries if k in old and old[k] != entries[k])

    MANIFEST.write_text(
        "\n".join(f"{entries[k]}  {k}" for k in sorted(entries)) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(entries)} entries ({len(added)} added, {len(removed)} removed, {len(changed)} changed)")
    for label, paths in (("added", added), ("removed", removed), ("changed", changed)):
        for path in paths[:20]:
            print(f"  {label}: {path}")
        if len(paths) > 20:
            print(f"  ... and {len(paths) - 20} more {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
