"""Live debug overlay: a transparent, click-through, top-most window glued to the game's client area.

docs/06 "Debug mode and overlay", sink 1. It implements ``OverlayPresenter`` (``show_frame`` /
``hide``) and is fed by ``OverlaySink``. Frames may arrive from the worker thread: they are handed
to the Qt thread through a signal. Every 250 ms the window follows the game window's client
rectangle; it hides itself while the game is minimised or its canvas is covered by another window
(the bot keeps working in the background either way).

Coordinates: ``DebugFrame`` rectangles are client pixels (physical). Qt widgets use logical pixels,
so painting divides by the screen's device pixel ratio.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ngoalong.core.geometry import Rect
from ngoalong.debug.model import DebugFrame
from ngoalong.debug.render import detection_label

# A function returning the game's client rectangle on screen (physical px), or None when the game
# is not visible (minimised, covered, gone).
TargetFn = Callable[[], Rect | None]

CYAN = QColor(0, 255, 255)
GREEN = QColor(0, 200, 0)
RED = QColor(255, 0, 0)
YELLOW = QColor(255, 255, 0)
MAGENTA = QColor(255, 0, 255)
LABEL_BG = QColor(0, 0, 0, 170)

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20


def physical_to_logical(r: Rect) -> tuple[QRect, float]:
    """Physical screen rectangle -> Qt logical rectangle, and the device pixel ratio used."""
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        nx, ny = geo.x(), geo.y()  # Qt keeps each screen's origin in native pixels
        nw, nh = round(geo.width() * dpr), round(geo.height() * dpr)
        if nx <= r.x < nx + nw and ny <= r.y < ny + nh:
            x = nx + (r.x - nx) / dpr
            y = ny + (r.y - ny) / dpr
            return QRect(round(x), round(y), max(1, round(r.w / dpr)), max(1, round(r.h / dpr))), dpr
    return QRect(r.x, r.y, max(1, r.w), max(1, r.h)), 1.0


class OverlayWindow(QWidget):
    _frame_received = Signal(object)
    _hide_requested = Signal()

    def __init__(self, target: TargetFn, follow_ms: int = 250) -> None:
        super().__init__(None)
        self.setWindowTitle("NgoaLong debug overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._target = target
        self._frame: DebugFrame | None = None
        self._active = False
        self._dpr = 1.0
        self._frame_received.connect(self._on_frame)
        self._hide_requested.connect(self._on_hide)
        self._timer = QTimer(self)
        self._timer.setInterval(follow_ms)
        self._timer.timeout.connect(self.follow)

    # --- OverlayPresenter (any thread) ----------------------------------------------------------
    def show_frame(self, frame: DebugFrame) -> None:
        self._frame_received.emit(frame)

    def hide(self) -> None:  # type: ignore[override]
        self._hide_requested.emit()

    # --- Qt thread ------------------------------------------------------------------------------
    @property
    def frame(self) -> DebugFrame | None:
        return self._frame

    def _on_frame(self, frame: DebugFrame) -> None:
        self._frame = frame
        if not self._active:
            self._active = True
            self._timer.start()
        self.follow()
        self.update()

    def _on_hide(self) -> None:
        self._active = False
        self._timer.stop()
        self._frame = None
        super().hide()

    def follow(self) -> None:
        """Stick to the game's client rectangle; hide while the game is not on screen."""
        if not self._active:
            return
        try:
            rect = self._target()
        except Exception:
            rect = None
        if rect is None or rect.is_empty():
            if self.isVisible():
                super().hide()
            return
        logical, self._dpr = physical_to_logical(rect)
        if self.geometry() != logical:
            self.setGeometry(logical)
        if not self.isVisible():
            self.show()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._apply_win32_styles()

    def _apply_win32_styles(self) -> None:
        if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
            return
        import ctypes

        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)

    # --- painting -------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        frame = self._frame
        if frame is None:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.scale(1.0 / self._dpr, 1.0 / self._dpr)  # paint in client (physical) pixels
            font = QFont(p.font())
            font.setPixelSize(13)
            p.setFont(font)
            if frame.canvas is not None:
                c = frame.canvas
                color = CYAN if c.reliable else RED
                self._rect(p, c.rect, color, 2)
                label = f"canvas {c.rect.w}x{c.rect.h} @({c.rect.x},{c.rect.y}) x{c.scale:.2f}"
                if not c.reliable:
                    label += f" {c.confidence.value.upper()}"
                self._label(p, label, c.rect.x, c.rect.y - 18, color)
            for d in frame.detections:
                color = GREEN if d.found else RED
                self._rect(p, d.rect, color, 2 if d.found else 1)
                self._label(p, detection_label(d), d.rect.x, d.rect.y - 18, color)
            for m in frame.markers:
                self._cross(p, m.point.x, m.point.y, MAGENTA, 5)
                self._label(p, m.name, m.point.x + 6, m.point.y + 3, MAGENTA)
            for c in frame.clicks:
                self._cross(p, c.x, c.y, YELLOW, 10)
            y = frame.canvas.rect.bottom + 2 if frame.canvas is not None else 2
            x = frame.canvas.rect.x if frame.canvas is not None else 2
            self._label(p, frame.status_line(), x, y, QColor(255, 255, 255))
        finally:
            p.end()

    @staticmethod
    def _rect(p: QPainter, r: Rect, color: QColor, width: int) -> None:
        p.setPen(QPen(color, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(r.x, r.y, max(0, r.w - 1), max(0, r.h - 1)))

    @staticmethod
    def _cross(p: QPainter, x: int, y: int, color: QColor, size: int) -> None:
        p.setPen(QPen(color, 2))
        p.drawLine(QPointF(x - size, y), QPointF(x + size, y))
        p.drawLine(QPointF(x, y - size), QPointF(x, y + size))

    @staticmethod
    def _label(p: QPainter, text: str, x: int, y: int, color: QColor) -> None:
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 6
        h = fm.height() + 2
        y = max(0, y)
        p.fillRect(QRectF(x, y, w, h), LABEL_BG)
        p.setPen(color)
        p.drawText(QPointF(x + 3, y + fm.ascent() + 1), text)
