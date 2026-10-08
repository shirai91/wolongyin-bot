"""``GameWindow``: find and bind the Coowon window that shows Ngọa Long (docs/06, step 1).

Live findings (2026-10-06): the game window is a visible top-level ``Chrome_WidgetWin_1`` whose
title contains "Ngọa Long" and "Coowon" (process ``chrome.exe`` under ``%LOCALAPPDATA%\\Coowon\\``).
It has NO ``Chrome_RenderWidgetHostHWND`` child, so input and capture target the top-level window
in client coordinates.

The binding is by (pid, exe, class, title rule), never a raw handle saved across restarts (#6),
and the bot's own windows are never selected (#5).
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from ngoalong.core.errors import WindowGoneError
from ngoalong.core.geometry import Point, Rect

GAME_WINDOW_CLASS = "Chrome_WidgetWin_1"
GAME_TITLE_KEY = "ngoa long"  # compared without diacritics, case-insensitive
BROWSER_PATH_KEY = "\\coowon\\"
BROWSER_TITLE_KEY = "coowon"


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A snapshot of one top-level window, as returned by enumeration."""

    hwnd: int
    pid: int
    exe_path: str
    class_name: str
    title: str
    visible: bool = True


def fold_text(s: str) -> str:
    """Lower-case and strip Vietnamese diacritics: "Ngọa Long Việt" -> "ngoa long viet"."""
    s = s.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def is_game_title(title: str) -> bool:
    return GAME_TITLE_KEY in fold_text(title)


def is_coowon(info: WindowInfo) -> bool:
    """Coowon by process path; by window title only when the path cannot be read."""
    if info.exe_path:
        return BROWSER_PATH_KEY in info.exe_path.casefold()
    return BROWSER_TITLE_KEY in fold_text(info.title)


def is_game_window(info: WindowInfo) -> bool:
    return (
        info.visible
        and info.class_name == GAME_WINDOW_CLASS
        and is_game_title(info.title)
        and is_coowon(info)
    )


def enumerate_windows() -> list[WindowInfo]:
    """Snapshot of all top-level windows (Win32)."""
    from ngoalong.core import _winapi as w

    infos: list[WindowInfo] = []
    exe_cache: dict[int, str] = {}
    for hwnd in w.top_level_windows():
        pid = w.window_pid(hwnd)
        cls = w.class_name(hwnd)
        visible = bool(w.IsWindowVisible(hwnd))
        title = w.window_text(hwnd) if visible else ""
        exe = ""
        if visible and cls == GAME_WINDOW_CLASS:
            if pid not in exe_cache:
                exe_cache[pid] = w.process_image_path(pid)
            exe = exe_cache[pid]
        infos.append(WindowInfo(hwnd, pid, exe, cls, title, visible))
    return infos


def _window_info(hwnd: int) -> WindowInfo:
    from ngoalong.core import _winapi as w

    pid = w.window_pid(hwnd)
    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        exe_path=w.process_image_path(pid),
        class_name=w.class_name(hwnd),
        title=w.window_text(hwnd),
        visible=bool(w.IsWindowVisible(hwnd)),
    )


class GameWindow:
    """A bound game window. ``hwnd`` may change after ``rebind()`` (browser restart)."""

    def __init__(self, info: WindowInfo) -> None:
        self.hwnd = info.hwnd
        self.pid = info.pid
        self.exe_path = info.exe_path
        self.class_name = info.class_name
        self.title = info.title

    def __repr__(self) -> str:
        return f"GameWindow(hwnd=0x{self.hwnd:X}, pid={self.pid}, title={self.title!r})"

    # --- discovery ------------------------------------------------------------------------------
    @classmethod
    def discover(
        cls, windows: Iterable[WindowInfo] | None = None, own_pid: int | None = None
    ) -> list[GameWindow]:
        """All Coowon windows showing Ngọa Long, excluding this process's own windows."""
        own = os.getpid() if own_pid is None else own_pid
        source = enumerate_windows() if windows is None else windows
        return [cls(info) for info in source if info.pid != own and is_game_window(info)]

    @classmethod
    def pick_under_cursor(cls, own_pid: int | None = None) -> GameWindow | None:
        """Manual fallback: the top-level window under the mouse cursor (not the bot's own)."""
        import ctypes
        from ctypes import wintypes

        from ngoalong.core import _winapi as w

        pt = wintypes.POINT()
        if not w.GetCursorPos(ctypes.byref(pt)):
            return None
        hwnd = w.WindowFromPoint(pt)
        if not hwnd:
            return None
        root = w.GetAncestor(hwnd, w.GA_ROOT) or hwnd
        info = _window_info(int(root))
        if info.pid == (os.getpid() if own_pid is None else own_pid):
            return None
        return cls(info)

    # --- liveness and rebinding -----------------------------------------------------------------
    def is_alive(self) -> bool:
        from ngoalong.core import _winapi as w

        if not self.hwnd or not w.IsWindow(self.hwnd):
            return False
        return w.window_pid(self.hwnd) == self.pid and w.class_name(self.hwnd) == self.class_name

    def rebind(self, windows: Iterable[WindowInfo] | None = None, own_pid: int | None = None) -> bool:
        """Re-find this window after it closed or the browser restarted.

        Keeps the binding when it is still alive. Otherwise prefers a game window from the same
        process, then the same executable and title, then any game window of the same executable.
        """
        if windows is None and self.is_alive():
            return True
        candidates = [
            gw._info() for gw in GameWindow.discover(windows=windows, own_pid=own_pid)
        ]
        same_exe = [c for c in candidates if c.exe_path.casefold() == self.exe_path.casefold()]
        ranked = (
            [c for c in candidates if c.pid == self.pid and c.hwnd == self.hwnd]
            + [c for c in candidates if c.pid == self.pid]
            + [c for c in same_exe if c.title == self.title]
            + same_exe
        )
        if not ranked:
            return False
        best = ranked[0]
        self.hwnd, self.pid, self.exe_path = best.hwnd, best.pid, best.exe_path
        self.class_name, self.title = best.class_name, best.title
        return True

    def _info(self) -> WindowInfo:
        return WindowInfo(self.hwnd, self.pid, self.exe_path, self.class_name, self.title)

    # --- geometry -------------------------------------------------------------------------------
    def client_size(self) -> tuple[int, int]:
        """Client area (width, height) in physical pixels."""
        import ctypes
        from ctypes import wintypes

        from ngoalong.core import _winapi as w

        rect = wintypes.RECT()
        with w.dpi_aware():
            ok = w.GetClientRect(self.hwnd, ctypes.byref(rect))
        if not ok:
            raise WindowGoneError(f"window 0x{self.hwnd:X} is gone")
        return (rect.right - rect.left, rect.bottom - rect.top)

    def client_origin_on_screen(self) -> Point:
        """Screen position of the client area's top-left corner (physical pixels)."""
        import ctypes
        from ctypes import wintypes

        from ngoalong.core import _winapi as w

        pt = wintypes.POINT(0, 0)
        with w.dpi_aware():
            ok = w.ClientToScreen(self.hwnd, ctypes.byref(pt))
        if not ok:
            raise WindowGoneError(f"window 0x{self.hwnd:X} is gone")
        return Point(pt.x, pt.y)

    def client_rect_on_screen(self) -> Rect:
        origin = self.client_origin_on_screen()
        w, h = self.client_size()
        return Rect(origin.x, origin.y, w, h)

    def is_minimized(self) -> bool:
        from ngoalong.core import _winapi as w

        return bool(w.IsIconic(self.hwnd))
