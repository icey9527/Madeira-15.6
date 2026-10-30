#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 125hz
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
#
# ml2108: a build-time copy of wine/dlls/ntdll/unix/file.c with NtWriteFile
# source probes, in the same pattern as prepare-sync.py: the wine submodule is
# never modified, only $OBJ_DIR/file.c is written.
#
# Why: on device a 32-bit game's main thread was reported by [srv-stuck] as
# waiting for more than two minutes on an *async* object (h[0], NO-WAIT) with
# the PC at invoke_system_apc and the stack at NtWriteFile+0x7b0. That is the
# wait handle returned by server_write_file()'s server `write` request and
# waited on by wait_async(); the missing fact is which handle it is and what
# kind of object it names, because each answer points at a different fix.
# ml955 already does exactly this for NtReadFile.
#
# Three probes, each bounded by a per-thread __thread counter (Wine's clang
# supports __thread; a new game thread therefore gets its own 64-line budget
# instead of sharing one global counter already drained by Steam startup) and
# capped at 64 lines per thread. No dedup tables: a static "seen" array raced
# across threads and a per-link cap could miss the game's handle entirely. Every line
# carries the wine tid and the handle, so a wait-begin without a wait-end is
# the pending write.
#
# Privacy: handle/fd numbers, the server fd type enum, options, length, offset
# and status only. Never buffer contents, account names, Steam IDs or paths.
#
# Anchors are required exactly once. Any drift is a hard error.

import pathlib
import sys

source_root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
relative = 'dlls/ntdll/unix/file.c'

text = (source_root / relative).read_text(encoding='utf-8')

# ---------------------------------------------------------------- anchor 1
# NtWriteFile only (FILE_WRITE_DATA). Logs handles the server classified so
# fd/type/options are real. A BAD_DEVICE_TYPE handle has no client fd and no
# valid type/options (server_ios.c stores the error with FD_TYPE_INVALID), so
# it is reported by anchor 2 instead of printing uninitialised values.
anchor1 = (
    '    status = server_get_unix_fd( handle, FILE_WRITE_DATA, &unix_handle, &needs_close, &type, &options );\n'
)

replacement1 = (
    '    status = server_get_unix_fd( handle, FILE_WRITE_DATA, &unix_handle, &needs_close, &type, &options );\n'
    '#ifdef WINE_IOS\n'
    '    /* ml2108 [write-src]: successful classification. enum server_fd_type\n'
    '     * (include/wine/server_protocol.h) really has INVALID/FILE/DIR/\n'
    '     * SOCKET/SERIAL/CHAR/DEVICE/NB_TYPES; no names are invented here. */\n'
    '    {\n'
    '        /* ml2108: 线程局部预算。每次调用自增，每个线程各自有 64 行上限；\n'
    '         * 之前是全局 InterlockedIncrement，Steam 启动就把额度耗光了。 */\n'
    '        static __thread unsigned int ml2108_fd_n;\n'
    '        unsigned int ml2108_n = ++ml2108_fd_n;\n'
    '\n'
    '        if (!status && ml2108_n <= 64)\n'
    '        {\n'
    '            /* One-time const table in the real enum order (server_protocol.h):\n'
    '             * INVALID, FILE, DIR, SOCKET, SERIAL, CHAR, DEVICE, NB_TYPES.\n'
    '             * No per-call writes, so no data race. */\n'
    '            static const char * const ml2108_types[FD_TYPE_NB_TYPES] =\n'
    '            {\n'
    '                [FD_TYPE_INVALID] = "INVALID",\n'
    '                [FD_TYPE_FILE]    = "FILE",\n'
    '                [FD_TYPE_DIR]     = "DIR",\n'
    '                [FD_TYPE_SOCKET]  = "SOCKET",\n'
    '                [FD_TYPE_SERIAL]  = "SERIAL",\n'
    '                [FD_TYPE_CHAR]    = "CHAR",\n'
    '                [FD_TYPE_DEVICE]  = "DEVICE",\n'
    '            };\n'
    '            ERR( "ml2108 [write-src] fd #%d tid=%04x handle=%p fd=%d type=%s(%d) options=%08x len=%u async=%d\\n",\n'
    '                 (int)ml2108_n, (unsigned)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread, handle, unix_handle,\n'
    '                 ((int)type >= 0 && (int)type < FD_TYPE_NB_TYPES) ? ml2108_types[(int)type] : "?",\n'
    '                 (int)type, options, length,\n'
    '                 !(options & (FILE_SYNCHRONOUS_IO_ALERT | FILE_SYNCHRONOUS_IO_NONALERT)) );\n'
    '        }\n'
    '    }\n'
    '#endif\n'
)

# ---------------------------------------------------------------- anchor 2
# The only NtWriteFile path that calls server_write_file() and thus wait_async().
# status == STATUS_BAD_DEVICE_TYPE means the server object has no client fd
# (server_ios.c caches it as FD_TYPE_INVALID + the status), so type/options are
# deliberately not read here.
anchor2 = (
    '    if (status == STATUS_BAD_DEVICE_TYPE)\n'
    '        return server_write_file( handle, event, apc, apc_user, io, buffer, length, offset, key );\n'
)

replacement2 = (
    '    if (status == STATUS_BAD_DEVICE_TYPE)\n'
    '    {\n'
    '#ifdef WINE_IOS\n'
    '        /* ml2108: 线程局部预算，新游戏线程不再受 Steam 已消耗的全局计数影响。 */\n'
    '        static __thread unsigned int ml2108_server_n;\n'
    '        unsigned int ml2108_n = ++ml2108_server_n;\n'
    '\n'
    '        if (ml2108_n <= 64)\n'
    '            ERR( "ml2108 [write-src] server-path #%d tid=%04x handle=%p len=%u offset=%s%I64x no-client-fd caller=%p\\n",\n'
    '                 (int)ml2108_n, (unsigned)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread, handle, length,\n'
    '                 offset ? "" : "(none) ", offset ? offset->QuadPart : 0,\n'
    '                 (void *)__builtin_return_address( 0 ) );\n'
    '#endif\n'
    '        return server_write_file( handle, event, apc, apc_user, io, buffer, length, offset, key );\n'
    '    }\n'
)

# ---------------------------------------------------------------- anchor 3
# server_write_file()'s request + wait tail. SERVER_START_REQ( write ) makes
# this block unique (the read/ioctl tails are otherwise identical). wait-begin
# without wait-end is a write whose async completion never arrived.
anchor3 = (
    '    SERVER_START_REQ( write )\n'
    '    {\n'
    '        req->async = server_async( handle, &async->io, event, apc, apc_context, iosb_client_ptr(io) );\n'
    '        req->pos   = offset ? offset->QuadPart : 0;\n'
    '        wine_server_add_data( req, buffer, size );\n'
    '        status = wine_server_call( req );\n'
    '        wait_handle = wine_server_ptr_handle( reply->wait );\n'
    '        options     = reply->options;\n'
    '        if (wait_handle && status != STATUS_PENDING)\n'
    '            set_sync_iosb( io, status, reply->size, options );\n'
    '    }\n'
    '    SERVER_END_REQ;\n'
    '\n'
    '    if (status != STATUS_PENDING) free( async );\n'
    '\n'
    '    if (wait_handle) status = wait_async( wait_handle, (options & FILE_SYNCHRONOUS_IO_ALERT) );\n'
    '    return status;\n'
    '}\n'
)

replacement3 = (
    '    SERVER_START_REQ( write )\n'
    '    {\n'
    '        req->async = server_async( handle, &async->io, event, apc, apc_context, iosb_client_ptr(io) );\n'
    '        req->pos   = offset ? offset->QuadPart : 0;\n'
    '        wine_server_add_data( req, buffer, size );\n'
    '        status = wine_server_call( req );\n'
    '        wait_handle = wine_server_ptr_handle( reply->wait );\n'
    '        options     = reply->options;\n'
    '        if (wait_handle && status != STATUS_PENDING)\n'
    '            set_sync_iosb( io, status, reply->size, options );\n'
    '    }\n'
    '    SERVER_END_REQ;\n'
    '\n'
    '    if (status != STATUS_PENDING) free( async );\n'
    '\n'
    '    if (wait_handle)\n'
    '    {\n'
    '        /* ml2108 [write-src]: the wait point. 线程局部预算，每个线程\n'
    '         * 各 64 行，避免 Steam 启动耗尽全局额度后游戏线程没有探针。\n'
    '         * 无去重；每行都带 wine tid 和 handle。 */\n'
    '        static __thread unsigned int ml2108_wait_n;\n'
    '        unsigned int ml2108_n = ++ml2108_wait_n;\n'
    '\n'
    '        if (ml2108_n <= 64)\n'
    '            ERR( "ml2108 [write-src] wait-begin #%d tid=%04x handle=%p wait=%p size=%u options=%08x alertable=%d\\n",\n'
    '                 (int)ml2108_n, (unsigned)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread, handle, wait_handle, size, options,\n'
    '                 !!(options & FILE_SYNCHRONOUS_IO_ALERT) );\n'
    '        status = wait_async( wait_handle, (options & FILE_SYNCHRONOUS_IO_ALERT) );\n'
    '        if (ml2108_n <= 64)\n'
    '            ERR( "ml2108 [write-src] wait-end #%d tid=%04x handle=%p status=%08x\\n",\n'
    '                 (int)ml2108_n, (unsigned)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread, handle, status );\n'
    '    }\n'
    '    return status;\n'
    '}\n'
)

# ---------------------------------------------------------------- anchors 4/5
# ml2112 [iosb-src]: the IOSB cookie the caller hands to server_async(), in
# both server_read_file() and server_write_file().  On device a 32-bit write
# faulted in set_async_iosb writing the IO_STATUS_BLOCK32 at x19=0x10000; this
# pins whether the cookie is already wrong at REQUEST time (host cookies are
# wow_guest_base + raw, per wow64_private.h), or only on the completion side.
# Bounded per thread (32 lines); no buffer contents, no paths.
iosb_probe = (
    '#ifdef WINE_IOS\n'
    '        {\n'
    '            static __thread unsigned int ml2108_iosb_n;\n'
    '            unsigned int ml2108_n = ++ml2108_iosb_n;\n'
    '\n'
    '            if (ml2108_n <= 32)\n'
    '            {\n'
    '                ULONG_PTR ml2108_base = ios_wow_host_base();\n'
    '                ULONG_PTR ml2108_ptr  = (ULONG_PTR)iosb_client_ptr( io );\n'
    '\n'
    '                ERR( "[iosb-src] tid=%04x handle=%p io=%p ptr=%p wowcall=%d hostbase=%p io_in_window=%d ptr_in_window=%d buf=%p caller=%p\\n",\n'
    '                     (unsigned)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread, handle, (void *)io, (void *)ml2108_ptr,\n'
    '                     (int)in_wow64_call(), (void *)ml2108_base,\n'
    '                     ml2108_base && (ULONG_PTR)io >= ml2108_base && (ULONG_PTR)io < ml2108_base + 0x100000000ULL,\n'
    '                     ml2108_base && ml2108_ptr >= ml2108_base && ml2108_ptr < ml2108_base + 0x100000000ULL,\n'
    '                     (void *)buffer,\n'
    '                     (void *)__builtin_return_address( 0 ) );\n'
    '            }\n'
    '        }\n'
    '#endif\n'
)

anchor4 = '    SERVER_START_REQ( read )\n'
replacement4 = iosb_probe + anchor4

anchor5 = '    SERVER_START_REQ( write )\n'
replacement5 = iosb_probe + anchor5

for name, anchor in (('NtWriteFile get_unix_fd', anchor1),
                     ('NtWriteFile server path', anchor2),
                     ('server_write_file tail', anchor3),
                     ('server_read_file request', anchor4),
                     ('server_write_file request', anchor5)):
    count = text.count(anchor)
    if count != 1:
        raise SystemExit(
            f'file.c anchor drift: expected 1 occurrence of {name}, found {count}')

patched = text.replace(anchor1, replacement1, 1)
patched = patched.replace(anchor2, replacement2, 1)
patched = patched.replace(anchor3, replacement3, 1)
patched = patched.replace(anchor4, replacement4, 1)
patched = patched.replace(anchor5, replacement5, 1)

# Source validation: each probe exists exactly once and the patch changed the
# text. __thread and NtCurrentTeb are already used in this tree.
checks = (
    ('ml2108 [write-src] fd #%d tid=%04x handle=%p fd=%d', 1),
    ('ml2108 [write-src] server-path #%d tid=%04x handle=%p', 1),
    ('ml2108 [write-src] wait-begin #%d tid=%04x handle=%p', 1),
    ('ml2108 [write-src] wait-end #%d tid=%04x handle=%p', 1),
    ('[iosb-src] tid=%04x handle=%p io=%p ptr=%p', 2),
)
for needle, expected in checks:
    found = patched.count(needle)
    if found != expected:
        raise SystemExit(f'source validation failed: {needle!r} x{found}, expected {expected}')
if patched == text:
    raise SystemExit('source validation failed: patch produced no change')
if 'ml2108_seen' in patched or 'ml2108_wait_handles' in patched:
    raise SystemExit('source validation failed: dedup table still present')

output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(patched, encoding='utf-8')
print(f'NtWriteFile source probes applied to {relative}: 5 anchors, {len(patched) - len(text):+d} bytes')
