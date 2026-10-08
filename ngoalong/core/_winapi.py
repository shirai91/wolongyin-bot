"""ctypes bindings for the few Win32 calls the core layer needs (user32, gdi32, kernel32).

Every function has explicit ``argtypes``/``restype`` so 64-bit handles are never truncated.
Importing this module on a non-Windows platform raises ``OSError``; callers import it lazily.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - MVP is Windows only
    raise OSError("ngoalong.core._winapi is only available on Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HWND = wintypes.HWND
HDC = wintypes.HDC
HBITMAP = wintypes.HBITMAP
HGDIOBJ = wintypes.HGDIOBJ
LPARAM = wintypes.LPARAM
WPARAM = wintypes.WPARAM

# --- messages and flags -------------------------------------------------------------------------
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2

BI_RGB = 0
DIB_RGB_COLORS = 0
GR_GDIOBJECTS = 0
GR_USEROBJECTS = 1
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAPVK_VK_TO_VSC = 0

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, LPARAM)


def _bind(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object) -> ctypes._CFuncPtr:
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


# --- user32 ---------------------------------------------------------------------------------------
EnumWindows = _bind(user32, "EnumWindows", wintypes.BOOL, WNDENUMPROC, LPARAM)
GetWindowTextLengthW = _bind(user32, "GetWindowTextLengthW", ctypes.c_int, HWND)
GetWindowTextW = _bind(user32, "GetWindowTextW", ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
GetClassNameW = _bind(user32, "GetClassNameW", ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
IsWindow = _bind(user32, "IsWindow", wintypes.BOOL, HWND)
IsWindowVisible = _bind(user32, "IsWindowVisible", wintypes.BOOL, HWND)
IsIconic = _bind(user32, "IsIconic", wintypes.BOOL, HWND)
GetWindowThreadProcessId = _bind(
    user32, "GetWindowThreadProcessId", wintypes.DWORD, HWND, ctypes.POINTER(wintypes.DWORD)
)
GetClientRect = _bind(user32, "GetClientRect", wintypes.BOOL, HWND, ctypes.POINTER(wintypes.RECT))
GetWindowRect = _bind(user32, "GetWindowRect", wintypes.BOOL, HWND, ctypes.POINTER(wintypes.RECT))
ClientToScreen = _bind(user32, "ClientToScreen", wintypes.BOOL, HWND, ctypes.POINTER(wintypes.POINT))
GetCursorPos = _bind(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
WindowFromPoint = _bind(user32, "WindowFromPoint", HWND, wintypes.POINT)
GetAncestor = _bind(user32, "GetAncestor", HWND, HWND, wintypes.UINT)
GetDC = _bind(user32, "GetDC", HDC, HWND)
ReleaseDC = _bind(user32, "ReleaseDC", ctypes.c_int, HWND, HDC)
PrintWindow = _bind(user32, "PrintWindow", wintypes.BOOL, HWND, HDC, wintypes.UINT)
PostMessageW = _bind(user32, "PostMessageW", wintypes.BOOL, HWND, wintypes.UINT, WPARAM, LPARAM)
MapVirtualKeyW = _bind(user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
GetGuiResources = _bind(user32, "GetGuiResources", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
try:
    SetThreadDpiAwarenessContext = _bind(
        user32, "SetThreadDpiAwarenessContext", ctypes.c_void_p, ctypes.c_void_p
    )
except AttributeError:  # pragma: no cover - Windows < 10 1607
    SetThreadDpiAwarenessContext = None

# --- gdi32 ----------------------------------------------------------------------------------------
CreateCompatibleDC = _bind(gdi32, "CreateCompatibleDC", HDC, HDC)
CreateCompatibleBitmap = _bind(gdi32, "CreateCompatibleBitmap", HBITMAP, HDC, ctypes.c_int, ctypes.c_int)
SelectObject = _bind(gdi32, "SelectObject", HGDIOBJ, HDC, HGDIOBJ)
DeleteObject = _bind(gdi32, "DeleteObject", wintypes.BOOL, HGDIOBJ)
DeleteDC = _bind(gdi32, "DeleteDC", wintypes.BOOL, HDC)
GetDIBits = _bind(
    gdi32,
    "GetDIBits",
    ctypes.c_int,
    HDC,
    HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
)

# --- kernel32 -------------------------------------------------------------------------------------
OpenProcess = _bind(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
CloseHandle = _bind(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
GetCurrentProcess = _bind(kernel32, "GetCurrentProcess", wintypes.HANDLE)
QueryFullProcessImageNameW = _bind(
    kernel32,
    "QueryFullProcessImageNameW",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)


# --- helpers --------------------------------------------------------------------------------------
@contextmanager
def dpi_aware() -> Iterator[None]:
    """Run the block per-monitor DPI aware (thread-local), so sizes are physical pixels.

    Without it, a DPI-unaware process on a 125-150 % display gets scaled client sizes that do not
    match PrintWindow output or the coordinates Chromium expects in mouse messages.
    """
    old = None
    if SetThreadDpiAwarenessContext is not None:
        old = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        yield
    finally:
        if old:
            SetThreadDpiAwarenessContext(old)


def window_text(hwnd: int) -> str:
    n = GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def process_image_path(pid: int) -> str:
    """Full executable path of ``pid``; empty string when it cannot be queried."""
    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        CloseHandle(handle)


def top_level_windows() -> list[int]:
    found: list[int] = []

    @WNDENUMPROC
    def _cb(hwnd: int, _l: int) -> bool:
        found.append(int(hwnd))
        return True

    EnumWindows(_cb, 0)
    return found


def gdi_object_count() -> int:
    """GDI objects held by this process (GetGuiResources GR_GDIOBJECTS)."""
    return int(GetGuiResources(GetCurrentProcess(), GR_GDIOBJECTS))


def make_lparam(x: int, y: int) -> int:
    """Pack client coordinates into an LPARAM (low word x, high word y, both signed 16-bit)."""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)
