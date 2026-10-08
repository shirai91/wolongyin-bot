"""Global hotkeys with ``RegisterHotKey`` on a dedicated message thread (fixes #8).

v1.8.0 grabbed bare F1-F4 and F8-F10 for the whole system. Here every hotkey needs Ctrl, Alt or
Win, the defaults are Ctrl+Alt+F10 (stop) and Ctrl+Alt+D (debug overlay), and all are configurable
in ``settings.toml``. A combination already taken by another program is reported, not fatal.

Callbacks run on the hotkey thread: UI code must hop to the Qt thread (emit a signal).
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
PM_NOREMOVE = 0x0000

DEFAULT_STOP = "Ctrl+Alt+F10"
DEFAULT_DEBUG_TOGGLE = "Ctrl+Alt+D"

_MODIFIERS: dict[str, int] = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "meta": MOD_WIN,
}
_MOD_ORDER = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))

_NAMED_KEYS: dict[str, tuple[int, str]] = {
    "space": (0x20, "Space"), "pageup": (0x21, "PageUp"), "pagedown": (0x22, "PageDown"),
    "end": (0x23, "End"), "home": (0x24, "Home"), "left": (0x25, "Left"), "up": (0x26, "Up"),
    "right": (0x27, "Right"), "down": (0x28, "Down"), "insert": (0x2D, "Insert"),
    "ins": (0x2D, "Insert"), "delete": (0x2E, "Delete"), "del": (0x2E, "Delete"),
    "pause": (0x13, "Pause"), "esc": (0x1B, "Esc"), "escape": (0x1B, "Esc"),
    "tab": (0x09, "Tab"), "enter": (0x0D, "Enter"), "return": (0x0D, "Enter"),
    "backspace": (0x08, "Backspace"),
}


class HotkeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Hotkey:
    modifiers: int
    vk: int
    key_name: str

    @property
    def text(self) -> str:
        mods = [name for flag, name in _MOD_ORDER if self.modifiers & flag]
        return "+".join([*mods, self.key_name])

    def __str__(self) -> str:
        return self.text


def _parse_key(token: str) -> tuple[int, str]:
    t = token.strip()
    low = t.lower()
    if len(t) == 1 and (t.isalpha() and t.isascii() or t.isdigit()):
        return ord(t.upper()), t.upper()
    if low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        n = int(low[1:])
        return 0x70 + n - 1, f"F{n}"
    if low in _NAMED_KEYS:
        return _NAMED_KEYS[low]
    raise HotkeyError(f"phím không hợp lệ: {token!r}")


def parse_hotkey(text: str) -> Hotkey:
    """Parse ``"Ctrl+Alt+F10"``. Needs exactly one key and at least Ctrl, Alt or Win."""
    tokens = [t.strip() for t in text.split("+")]
    if not text.strip() or any(not t for t in tokens):
        raise HotkeyError(f"tổ hợp phím không hợp lệ: {text!r}")
    mods = 0
    keys: list[tuple[int, str]] = []
    for t in tokens:
        flag = _MODIFIERS.get(t.lower())
        if flag is not None:
            mods |= flag
        else:
            keys.append(_parse_key(t))
    if len(keys) != 1:
        raise HotkeyError(f"cần đúng một phím chính: {text!r}")
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise HotkeyError(f"phím tắt phải có Ctrl, Alt hoặc Win (không chiếm phím F trần): {text!r}")
    vk, name = keys[0]
    return Hotkey(mods, vk, name)


def normalize_hotkey(text: str) -> str:
    """Canonical spelling, or ``""`` for an empty (disabled) hotkey."""
    return parse_hotkey(text).text if text.strip() else ""


# --- the message thread -------------------------------------------------------------------------
Binding = tuple[str, Callable[[], None]]  # (hotkey text, callback)


class HotkeyManager:
    """Registers hotkeys on its own thread; ``set_bindings`` replaces them all."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._errors: list[str] = []
        self._callbacks: dict[int, tuple[str, Callable[[], None]]] = {}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def registered(self) -> list[str]:
        with self._lock:
            return [name for name, _ in self._callbacks.values()]

    def set_bindings(self, bindings: Mapping[str, Binding], timeout_s: float = 2.0) -> list[str]:
        """(Re)register ``{name: (hotkey, callback)}``; empty hotkeys are skipped.

        Returns error messages (bad syntax, or the combination is used by another program).
        """
        self.stop()
        errors: list[str] = []
        parsed: list[tuple[str, Hotkey, Callable[[], None]]] = []
        for name, (text, cb) in bindings.items():
            if not text.strip():
                continue
            try:
                parsed.append((name, parse_hotkey(text), cb))
            except HotkeyError as exc:
                errors.append(f"{name}: {exc}")
        if sys.platform != "win32" or not parsed:
            return errors
        self._ready.clear()
        self._errors = []
        self._thread = threading.Thread(target=self._run, args=(parsed,), name="hotkeys", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout_s):
            errors.append("không khởi động được luồng phím tắt")
        return errors + list(self._errors)

    def stop(self, timeout_s: float = 2.0) -> None:
        t = self._thread
        if t is None:
            return
        if t.is_alive() and self._thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            t.join(timeout_s)
        self._thread = None
        self._thread_id = 0
        with self._lock:
            self._callbacks.clear()

    def _run(self, parsed: list[tuple[str, Hotkey, Callable[[], None]]]) -> None:  # hotkey thread
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
        ]
        user32.PeekMessageW.restype = wintypes.BOOL

        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)  # create the message queue
        self._thread_id = int(kernel32.GetCurrentThreadId())
        ids: list[int] = []
        try:
            for i, (name, hk, cb) in enumerate(parsed, start=1):
                if user32.RegisterHotKey(None, i, hk.modifiers | MOD_NOREPEAT, hk.vk):
                    ids.append(i)
                    with self._lock:
                        self._callbacks[i] = (name, cb)
                else:
                    self._errors.append(f"{name}: {hk.text} đã bị chương trình khác dùng")
            self._ready.set()
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:  # WM_QUIT or error
                    break
                if msg.message == WM_HOTKEY:
                    with self._lock:
                        entry = self._callbacks.get(int(msg.wParam))
                    if entry is not None:
                        try:
                            entry[1]()
                        except Exception:
                            log.exception("hotkey %s callback failed", entry[0])
        finally:
            for i in ids:
                user32.UnregisterHotKey(None, i)
            self._ready.set()
