#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 ft7t8drgwf-wq
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
#
# Applies this repository's Madeira Dock changes (build/dock-patches/*.patch, in
# order) to the madeira-dock checkout before build/madeira-dock/build.sh builds dockhost.exe.
# The patches only touch the working tree, and a patch already applied is
# skipped, so every build script can call this. `apply.sh --revert` takes them
# out again. See build/dock-patches/README.md.
#
# In this fork madeira-dock is a vendored source tree, not a submodule: git
# would resolve the patches' paths from the repository's top level, so the
# vendored tree is patched with patch(1) and a little fuzz (the fork's
# madeira-dock has drifted from the checkout the patches were written against).
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$R/madeira-dock"
if [ -e "$S/.git" ]; then
    is_applied() { git -C "$S" apply --reverse --check "$1" 2>/dev/null; }
    can_apply() { git -C "$S" apply --check "$1" 2>/dev/null; }
    do_apply() { git -C "$S" apply "$1"; }
    do_revert() { git -C "$S" apply --reverse "$1"; }
    dock_head() { git -C "$S" rev-parse --short HEAD; }
else
    # --no-backup-if-mismatch: an applied hunk must not leave .orig files in
    # the tree the app build reads.
    is_applied() { patch -p1 --reverse --dry-run --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1" >/dev/null 2>&1; }
    can_apply() { patch -p1 --dry-run --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1" >/dev/null 2>&1; }
    do_apply() { patch -p1 --forward --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1"; }
    do_revert() { patch -p1 --reverse --fuzz=3 --no-backup-if-mismatch -d "$S" -i "$1"; }
    dock_head() { git -C "$R" rev-parse --short HEAD; }
fi
shopt -s nullglob
patches=("$R"/build/dock-patches/*.patch)
# Nothing to do without patches (and bash 3.2 under set -u rejects "${patches[@]}" when empty).
[ ${#patches[@]} -gt 0 ] || exit 0
if [ "${1:-}" = --revert ]; then
    for ((i = ${#patches[@]} - 1; i >= 0; i--)); do
        p="${patches[$i]}"
        if is_applied "$p"; then
            do_revert "$p"
            echo "dock-patches: reverted $(basename "$p")"
        fi
    done
    exit 0
fi
for p in "${patches[@]}"; do
    if is_applied "$p"; then
        echo "dock-patches: $(basename "$p") already applied"
    elif can_apply "$p"; then
        do_apply "$p"
        echo "dock-patches: applied $(basename "$p")"
    else
        echo "dock-patches: $(basename "$p") does not apply on madeira-dock $(dock_head)" >&2
        exit 1
    fi
done
