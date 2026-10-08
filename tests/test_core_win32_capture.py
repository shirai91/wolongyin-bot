"""Win32Capturer against a real (off-screen) Qt window: correctness and GDI-leak regression (#1)."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")

FILL_RGB = (10, 200, 30)


@pytest.fixture(scope="module")
def qt_window() -> Iterator[int]:
    """A framed top-level Qt window, shown far off-screen without activation; yields its HWND."""
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.setWindowTitle("ngoalong capture test")
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlag(Qt.WindowType.Tool, True)
    pal = w.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(*FILL_RGB))
    w.setPalette(pal)
    w.setAutoFillBackground(True)
    w.resize(320, 200)
    w.move(-6000, -6000)
    w.show()
    # Pump events until the window has painted its fill (bounded wait, no fixed sleep).
    from ngoalong.core.drivers.win32 import Win32Capturer

    cap = Win32Capturer(int(w.winId()))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        app.processEvents()
        f = cap.grab()
        if tuple(int(c) for c in f[f.shape[0] // 2, f.shape[1] // 2]) == FILL_RGB[::-1]:
            break
        time.sleep(0.01)
    try:
        yield int(w.winId())
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_grab_returns_client_area_bgr(qt_window: int) -> None:
    from ngoalong.core.drivers.win32 import Win32Capturer
    from ngoalong.core.window import GameWindow, WindowInfo

    frame = Win32Capturer(qt_window).grab()
    gw = GameWindow(WindowInfo(qt_window, os.getpid(), "", "", ""))
    w, h = gw.client_size()
    assert frame.shape == (h, w, 3) and frame.dtype == np.uint8
    assert frame.flags["C_CONTIGUOUS"]
    expected_bgr = FILL_RGB[::-1]
    # Client-area corners are the widget fill, not the window frame or title bar.
    for y, x in [(0, 0), (h - 1, w - 1), (h // 2, w // 2)]:
        assert tuple(int(c) for c in frame[y, x]) == expected_bgr


def test_no_gdi_leak_over_300_captures(qt_window: int) -> None:
    from ngoalong.core import _winapi as w
    from ngoalong.core.drivers.win32 import Win32Capturer

    cap = Win32Capturer(qt_window)
    for _ in range(5):  # warm-up: lazy GDI state in the process
        cap.grab()
    before = w.gdi_object_count()
    for _ in range(300):
        cap.grab()
    after = w.gdi_object_count()
    assert after - before <= 2, f"GDI objects grew from {before} to {after}"


def test_no_gdi_leak_when_printwindow_fails(qt_window: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from ngoalong.core import _winapi as w
    from ngoalong.core.drivers import win32
    from ngoalong.core.errors import CaptureError

    cap = win32.Win32Capturer(qt_window)
    cap.grab()
    monkeypatch.setattr(win32, "_print_window", lambda *a: False)
    before = w.gdi_object_count()
    for _ in range(300):
        with pytest.raises(CaptureError):
            cap.grab()
    assert w.gdi_object_count() - before <= 2


def test_grab_on_closed_window_raises() -> None:
    from ngoalong.core.drivers.win32 import Win32Capturer
    from ngoalong.core.errors import WindowGoneError

    with pytest.raises(WindowGoneError):
        Win32Capturer(0x7FFFFFF0).grab()
    with pytest.raises(WindowGoneError):
        Win32Capturer(0).grab()
