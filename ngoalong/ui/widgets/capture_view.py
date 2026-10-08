"""``CaptureView``: shows a capture and lets the user drag a rectangle on it (template tool).

Coordinates exchanged with the outside are FRAME pixels (the capture), never widget pixels. The
view fits the image to its size (``zoom=None``) or shows it at a fixed zoom (inside a scroll area);
small templates need 200-400 % to be cut precisely.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ngoalong.core.geometry import Point, Rect
from ngoalong.ui.widgets.debug_panel import bgr_to_qimage

CANVAS_COLOR = QColor(0, 200, 255)
SELECTION_COLOR = QColor(255, 220, 0)
FOUND_COLOR = QColor(0, 220, 0)
MISSED_COLOR = QColor(255, 60, 60)


class CaptureView(QWidget):
    selection_changed = Signal(object)  # Rect (frame px) | None, emitted when a drag ends

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._canvas: Rect | None = None
        self._selection: Rect | None = None
        self._marks: list[tuple[Rect, bool]] = []
        self._zoom: float | None = None
        self._drag_from: Point | None = None
        self.setMinimumSize(320, 200)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # --- state ----------------------------------------------------------------------------------
    @property
    def has_image(self) -> bool:
        return self._image is not None

    @property
    def selection(self) -> Rect | None:
        return self._selection

    @property
    def zoom(self) -> float | None:
        return self._zoom

    def set_frame(self, image: np.ndarray, canvas: Rect | None) -> None:
        self._image = bgr_to_qimage(image)
        self._canvas = canvas
        self._selection = None
        self._marks = []
        self._apply_size()
        self.update()

    def set_selection(self, rect: Rect | None) -> None:
        self._selection = rect if rect is not None and not rect.is_empty() else None
        self.update()

    def set_marks(self, marks: list[tuple[Rect, bool]]) -> None:
        """Rectangles to outline: (frame rect, found) -> green when found, red otherwise."""
        self._marks = list(marks)
        self.update()

    def set_zoom(self, zoom: float | None) -> None:
        self._zoom = zoom
        self._apply_size()
        self.update()

    def _apply_size(self) -> None:
        if self._zoom is not None and self._image is not None:
            self.setMinimumSize(round(self._image.width() * self._zoom), round(self._image.height() * self._zoom))
        else:
            self.setMinimumSize(320, 200)
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize() if self._zoom is not None else QSize(640, 400)

    # --- mapping --------------------------------------------------------------------------------
    def _transform(self) -> tuple[float, float, float]:
        """(k, ox, oy): widget = frame * k + (ox, oy)."""
        if self._image is None:
            return 1.0, 0.0, 0.0
        iw, ih = self._image.width(), self._image.height()
        if self._zoom is not None:
            return self._zoom, 0.0, 0.0
        k = min(self.width() / iw, self.height() / ih) if iw and ih else 1.0
        return k, (self.width() - iw * k) / 2, (self.height() - ih * k) / 2

    def to_frame(self, pos: QPointF) -> Point:
        k, ox, oy = self._transform()
        x = int((pos.x() - ox) / k)
        y = int((pos.y() - oy) / k)
        if self._image is not None:
            x = min(max(x, 0), self._image.width())
            y = min(max(y, 0), self._image.height())
        return Point(x, y)

    def to_widget(self, r: Rect) -> QRectF:
        k, ox, oy = self._transform()
        return QRectF(r.x * k + ox, r.y * k + oy, r.w * k, r.h * k)

    # --- mouse ----------------------------------------------------------------------------------
    def _drag_rect(self, p: Point) -> Rect:
        assert self._drag_from is not None
        a = self._drag_from
        return Rect.from_ltrb(min(a.x, p.x), min(a.y, p.y), max(a.x, p.x), max(a.y, p.y))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._image is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_from = self.to_frame(event.position())
        self._selection = None
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_from is None:
            return
        self._selection = self._drag_rect(self.to_frame(event.position()))
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_from is None or event.button() != Qt.MouseButton.LeftButton:
            return
        r = self._drag_rect(self.to_frame(event.position()))
        self._drag_from = None
        self.set_selection(r)
        self.selection_changed.emit(self._selection)

    # --- painting -------------------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(24, 24, 24))
            if self._image is None:
                p.setPen(QColor(200, 200, 200))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "Chưa có ảnh. Bấm 'Chụp từ game' hoặc 'Mở ảnh…'.")
                return
            k, ox, oy = self._transform()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, k < 1.0)
            p.drawImage(QRectF(ox, oy, self._image.width() * k, self._image.height() * k), self._image)
            if self._canvas is not None:
                p.setPen(QPen(CANVAS_COLOR, 1))
                p.drawRect(self.to_widget(self._canvas))
            for r, found in self._marks:
                p.setPen(QPen(FOUND_COLOR if found else MISSED_COLOR, 2))
                p.drawRect(self.to_widget(r))
            if self._selection is not None:
                p.setPen(QPen(SELECTION_COLOR, 1, Qt.PenStyle.DashLine))
                p.drawRect(self.to_widget(self._selection))
        finally:
            p.end()
