"""Win32 drivers for Coowon (MVP): background input by PostMessage, capture by PrintWindow.

Both target the Coowon TOP-LEVEL window in client coordinates (it has no render-widget child;
verified live 2026-10-06, reference ``tools/live/livectl.cs``). Neither needs the window in front.
"""

from __future__ import annotations

import ctypes
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np

from ngoalong.core import _winapi as w
from ngoalong.core.errors import CaptureError, WindowGoneError
from ngoalong.core.geometry import Point
from ngoalong.core.input import MouseButton


class HasHwnd(Protocol):
    @property
    def hwnd(self) -> int: ...


WindowTarget = int | HasHwnd


def _resolve_hwnd(target: WindowTarget) -> int:
    hwnd = target if isinstance(target, int) else target.hwnd
    if not hwnd:
        raise WindowGoneError("no window bound")
    return int(hwnd)


# Indirection points so tests can observe or fail the raw calls.
def _post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
    return bool(w.PostMessageW(hwnd, msg, wparam, lparam))


def _print_window(hwnd: int, hdc: int, flags: int) -> bool:
    return bool(w.PrintWindow(hwnd, hdc, flags))


_BUTTON_MESSAGES: dict[str, tuple[int, int, int, int]] = {
    # button: (down, up, double-click, MK_ flag)
    "left": (w.WM_LBUTTONDOWN, w.WM_LBUTTONUP, w.WM_LBUTTONDBLCLK, w.MK_LBUTTON),
    "right": (w.WM_RBUTTONDOWN, w.WM_RBUTTONUP, w.WM_RBUTTONDBLCLK, w.MK_RBUTTON),
    "middle": (w.WM_MBUTTONDOWN, w.WM_MBUTTONUP, w.WM_MBUTTONDBLCLK, w.MK_MBUTTON),
}


class Win32InputDriver:
    """Background mouse and keyboard input with ``PostMessage`` (fixes #9 and #10).

    The short gaps between messages (tens of ms) let Flash see separate events; they are part of
    one gesture, not waits for the game, so they are plain sleeps.
    """

    def __init__(
        self,
        target: WindowTarget,
        move_delay_s: float = 0.04,
        press_delay_s: float = 0.05,
        key_delay_s: float = 0.05,
        char_delay_s: float = 0.02,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._target = target
        self.move_delay_s = move_delay_s
        self.press_delay_s = press_delay_s
        self.key_delay_s = key_delay_s
        self.char_delay_s = char_delay_s
        self._sleep = sleep

    @property
    def hwnd(self) -> int:
        return _resolve_hwnd(self._target)

    def _post(self, hwnd: int, msg: int, wparam: int, lparam: int) -> None:
        if not _post_message(hwnd, msg, wparam, lparam):
            raise WindowGoneError(f"PostMessage 0x{msg:04X} to 0x{hwnd:X} failed")

    def click(self, p: Point, button: MouseButton = "left", double: bool = False) -> None:
        try:
            down, up, dbl, mk = _BUTTON_MESSAGES[button]
        except KeyError:
            raise ValueError(f"unknown mouse button {button!r}") from None
        hwnd = self.hwnd
        lp = w.make_lparam(p.x, p.y)
        self._post(hwnd, w.WM_MOUSEMOVE, 0, lp)
        self._sleep(self.move_delay_s)
        self._post(hwnd, down, mk, lp)
        self._sleep(self.press_delay_s)
        self._post(hwnd, up, 0, lp)
        if double:
            # Real Windows order: down, up, DBLCLK, up.
            self._sleep(self.press_delay_s)
            self._post(hwnd, dbl, mk, lp)
            self._sleep(self.press_delay_s)
            self._post(hwnd, up, 0, lp)

    def move(self, p: Point) -> None:
        """Move the game's idea of the mouse to ``p`` (no button): the last-clicked button stops
        glowing, a hovered list row stops reading as selected."""
        self._post(self.hwnd, w.WM_MOUSEMOVE, 0, w.make_lparam(p.x, p.y))

    def key(self, vk: int) -> None:
        hwnd = self.hwnd
        scan = int(w.MapVirtualKeyW(vk, w.MAPVK_VK_TO_VSC)) & 0xFF
        down_lp = 1 | (scan << 16)
        up_lp = 1 | (scan << 16) | (1 << 30) | (1 << 31)
        self._post(hwnd, w.WM_KEYDOWN, vk, down_lp)
        self._sleep(self.key_delay_s)
        self._post(hwnd, w.WM_KEYUP, vk, up_lp)

    def text(self, s: str) -> None:
        hwnd = self.hwnd
        data = s.encode("utf-16-le")
        units = [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]
        for i, unit in enumerate(units):
            if i:
                self._sleep(self.char_delay_s)
            self._post(hwnd, w.WM_CHAR, unit, 1)


class Win32Capturer:
    """Background capture of the client area with ``PrintWindow(PW_RENDERFULLCONTENT)``.

    The whole window is rendered (flag 2, verified live on Coowon), then the client area is cut
    out using the client origin relative to the window rectangle. Every DC and bitmap is released
    in ``finally`` blocks, whatever happens (fixes #1).
    """

    def __init__(self, target: WindowTarget, flags: int = w.PW_RENDERFULLCONTENT) -> None:
        self._target = target
        self.flags = flags

    @property
    def hwnd(self) -> int:
        return _resolve_hwnd(self._target)

    def _geometry(self, hwnd: int) -> tuple[int, int, int, int, int, int]:
        """(window w, window h, client x, client y, client w, client h) in window-DC coordinates."""
        from ctypes import wintypes

        wr = wintypes.RECT()
        cr = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not (
            w.GetWindowRect(hwnd, ctypes.byref(wr))
            and w.GetClientRect(hwnd, ctypes.byref(cr))
            and w.ClientToScreen(hwnd, ctypes.byref(origin))
        ):
            raise WindowGoneError(f"window 0x{hwnd:X} is gone")
        return (
            wr.right - wr.left,
            wr.bottom - wr.top,
            origin.x - wr.left,
            origin.y - wr.top,
            cr.right - cr.left,
            cr.bottom - cr.top,
        )

    def client_size(self) -> tuple[int, int]:
        """Current client (width, height) in physical pixels, read cheaply with GetClientRect."""
        from ctypes import wintypes

        hwnd = self.hwnd
        cr = wintypes.RECT()
        with w.dpi_aware():
            ok = w.GetClientRect(hwnd, ctypes.byref(cr))
        if not ok:
            raise WindowGoneError(f"window 0x{hwnd:X} is gone")
        return (cr.right - cr.left, cr.bottom - cr.top)

    def grab(self) -> np.ndarray:
        hwnd = self.hwnd
        if not w.IsWindow(hwnd):
            raise WindowGoneError(f"window 0x{hwnd:X} is gone")
        if w.IsIconic(hwnd):
            raise CaptureError("game window is minimised")
        with w.dpi_aware():
            win_w, win_h, cx, cy, cw, ch = self._geometry(hwnd)
            if cw <= 0 or ch <= 0 or win_w <= 0 or win_h <= 0:
                raise CaptureError(f"window has an empty client area ({cw}x{ch})")
            full = self._print(hwnd, win_w, win_h)
        x0, y0 = max(0, cx), max(0, cy)
        x1, y1 = min(win_w, cx + cw), min(win_h, cy + ch)
        if x1 <= x0 or y1 <= y0:
            raise CaptureError("client area outside the window rectangle")
        return np.ascontiguousarray(full[y0:y1, x0:x1, :3])

    def _print(self, hwnd: int, width: int, height: int) -> np.ndarray:
        """PrintWindow into a memory bitmap and copy it out as BGRA. Releases everything."""
        window_dc = w.GetDC(hwnd)
        if not window_dc:
            raise CaptureError("GetDC failed")
        mem_dc = None
        bitmap = None
        old_obj = None
        try:
            mem_dc = w.CreateCompatibleDC(window_dc)
            if not mem_dc:
                raise CaptureError("CreateCompatibleDC failed")
            bitmap = w.CreateCompatibleBitmap(window_dc, width, height)
            if not bitmap:
                raise CaptureError("CreateCompatibleBitmap failed")
            old_obj = w.SelectObject(mem_dc, bitmap)
            if not _print_window(hwnd, mem_dc, self.flags):
                raise CaptureError("PrintWindow failed")
            # Deselect before GetDIBits: the bitmap must not be selected into a DC.
            w.SelectObject(mem_dc, old_obj)
            old_obj = None
            info = w.BITMAPINFO()
            hdr = info.bmiHeader
            hdr.biSize = ctypes.sizeof(w.BITMAPINFOHEADER)
            hdr.biWidth = width
            hdr.biHeight = -height  # top-down rows
            hdr.biPlanes = 1
            hdr.biBitCount = 32
            hdr.biCompression = w.BI_RGB
            out = np.empty((height, width, 4), dtype=np.uint8)
            lines = w.GetDIBits(
                mem_dc,
                bitmap,
                0,
                height,
                out.ctypes.data_as(ctypes.c_void_p),
                ctypes.byref(info),
                w.DIB_RGB_COLORS,
            )
            if lines != height:
                raise CaptureError(f"GetDIBits copied {lines} of {height} lines")
            return out
        finally:
            if old_obj is not None and mem_dc:
                w.SelectObject(mem_dc, old_obj)
            if bitmap:
                w.DeleteObject(bitmap)
            if mem_dc:
                w.DeleteDC(mem_dc)
            w.ReleaseDC(hwnd, window_dc)
