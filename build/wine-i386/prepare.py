"""Prepare the i386 Wine source tree the 32-bit farm is built from.

The farm is built from `wine/`, which is a submodule, so a module that needs a
Madeira change is built from a prepared copy instead: every entry is a symlink
back into the submodule except the one module this script patches, which is
materialised as real files. Same shape as build/wine-wow64/prepare.py.
"""

import os
import sys
from pathlib import Path

PATCHED = Path("dlls/msadp32.acm")

OLD = """    aad->convert(adsi, adsh->pbSrc, &nsrc, adsh->pbDst, &ndst);
    adsh->cbSrcLengthUsed = nsrc;
    adsh->cbDstLengthUsed = ndst;
"""

NEW = r"""    aad->convert(adsi, adsh->pbSrc, &nsrc, adsh->pbDst, &ndst);
    TRACE("[adpcm] src=%lu dst=%lu used=%lu/%lu flags=%lx\n", adsh->cbSrcLength,
          adsh->cbDstLength, nsrc, ndst, adsh->fdwConvert);
    if (!nsrc && adsh->cbSrcLength)
    {
        /* Madeira-15.6: the converter only works in whole blocks, so a tail
         * shorter than nBlockAlign (or any input the caller's destination
         * cannot hold) converts nothing and reports zero bytes consumed. A
         * caller looping "while input remains" never advances: Galaxy Angel
         * sat in acmStreamSize/acmStreamConvert forever (2026-10-27 13:38,
         * 1.2 M trace lines). Consume the tail instead of reporting no
         * progress; the samples in it are dropped. */
        TRACE("[adpcm] %lu bytes produced no block: consumed without converting\n",
              adsh->cbSrcLength);
        nsrc = adsh->cbSrcLength;
    }
    adsh->cbSrcLengthUsed = nsrc;
    adsh->cbDstLengthUsed = ndst;
"""


def prepare(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for relative in (Path(), Path("dlls")):
        target = destination / relative
        target.mkdir(exist_ok=True)
        for entry in (source / relative).iterdir():
            if relative == Path() and (entry.name == ".git" or entry.name.startswith("build-")):
                continue
            if relative == Path("dlls") and entry.name == PATCHED.name:
                continue
            link = target / entry.name
            if not link.exists() and not link.is_symlink():
                os.symlink(entry.resolve(), link, target_is_directory=entry.is_dir())

    src_dir = source / PATCHED
    dst_dir = destination / PATCHED
    dst_dir.mkdir(parents=True, exist_ok=True)
    for entry in src_dir.iterdir():
        out = dst_dir / entry.name
        if entry.name == "msadp32.c":
            text = entry.read_text(encoding="utf-8")
            if text.count(OLD) != 1:
                raise SystemExit("msadp32.acm: ADPCM_StreamConvert pattern changed")
            out.write_text(text.replace(OLD, NEW), encoding="utf-8")
        elif not out.exists() and not out.is_symlink():
            os.symlink(entry.resolve(), out, target_is_directory=entry.is_dir())


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
    print("prepared i386 source tree (msadp32.acm patched)")
