#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 ft7t8drgwf-wq
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
#
# Applies this repository's DXMT changes (build/dxmt-patches/*.patch, in
# order) to the dxmt checkout before build/dxmt-ios/build.sh, build/wine-i386/build.sh and build/windows-runtime/build.sh build DXMT.
# The patches only touch the working tree, and a patch already applied is
# skipped, so every build script can call this. `apply.sh --revert` takes them
# out again. See build/dxmt-patches/README.md.
#
# In this fork dxmt is a vendored source tree, not a submodule: git would
# resolve the patches' src/ paths from the repository's top level, so the
# vendored tree is patched with patch(1) and a little fuzz (the fork's dxmt
# has drifted from the checkout the patches were written against).
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$R/dxmt"
if [ -e "$S/.git" ]; then
    is_applied() { git -C "$S" apply --reverse --check "$1" 2>/dev/null; }
    can_apply() { git -C "$S" apply --check "$1" 2>/dev/null; }
    do_apply() { git -C "$S" apply "$1"; }
    do_revert() { git -C "$S" apply --reverse "$1"; }
    dxmt_head() { git -C "$S" rev-parse --short HEAD; }
else
    # --no-backup-if-mismatch: an applied hunk must not leave .orig files in
    # the tree the app build reads.
    is_applied() { patch -p1 --reverse --dry-run --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1" >/dev/null 2>&1; }
    can_apply() { patch -p1 --dry-run --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1" >/dev/null 2>&1; }
    do_apply() { patch -p1 --forward --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1"; }
    do_revert() { patch -p1 --reverse --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1"; }
    dxmt_head() { git -C "$R" rev-parse --short HEAD; }
fi
shopt -s nullglob
patches=("$R"/build/dxmt-patches/*.patch)
# Nothing to do without patches (and bash 3.2 under set -u rejects "${patches[@]}" when empty).
[ ${#patches[@]} -gt 0 ] || exit 0
if [ "${1:-}" = --revert ]; then
    for ((i = ${#patches[@]} - 1; i >= 0; i--)); do
        p="${patches[$i]}"
        if is_applied "$p"; then
            do_revert "$p"
            echo "dxmt-patches: reverted $(basename "$p")"
        fi
    done
    exit 0
fi
for p in "${patches[@]}"; do
    if is_applied "$p"; then
        echo "dxmt-patches: $(basename "$p") already applied"
    elif can_apply "$p"; then
        do_apply "$p"
        echo "dxmt-patches: applied $(basename "$p")"
    else
        echo "dxmt-patches: $(basename "$p") does not apply on dxmt $(dxmt_head)" >&2
        exit 1
    fi
done
