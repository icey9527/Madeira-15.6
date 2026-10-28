/* SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Keep the Dock host off the guest desktop.
 *
 * dockhost.exe is a console program (the build links it without -mwindows), so
 * Wine gives it a console window titled with its own path. That window is a
 * real top-level window on the virtual desktop, and it stayed visible next to
 * the game: BaldrSky's 800x600 desktop had a 1545x434 console window laid over
 * it, so the strip to the right of the picture showed the Dock's console
 * instead of the game (2026-10-28, "右边有个显示").
 *
 * Hiding it at startup is enough -- stdout/stderr are pipes the app reads for
 * the [steam-host] report, and hiding a window does not touch them. A
 * constructor runs before main, so the console never flashes on screen.
 */
#include <windows.h>

__attribute__((constructor)) static void madeira_hide_console(void)
{
    HWND console = GetConsoleWindow();
    if (console) ShowWindow(console, SW_HIDE);
}
