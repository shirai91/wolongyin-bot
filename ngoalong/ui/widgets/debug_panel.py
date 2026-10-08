"""Debug panel: the last capture with the debug annotations (docs/06, sink 2).

Works while the game is behind other windows. Frames come from ``PanelSink`` (any thread) and are
handed to the Qt thread by a signal; rendering is lazy and throttled to 5 per second.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from ngoalong.debug.model import DebugFrame
from ngoalong.debug.sink import PanelSink


def bgr_to_qimage(img: np.ndarray) -> QImage:
    img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    q = QImage(img.data, w, h, int(img.strides[0]), QImage.Format.Format_BGR888)
    return q.copy()  # detach from the numpy buffer


class DebugPanel(QWidget):
    _frame_arrived = Signal(object)

    def __init__(self, sink: PanelSink, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sink = sink
        self._pixmap: QPixmap | None = None
        self._dirty = False
        lay = QVBoxLayout(self)
        self.image = QLabel("Chưa có ảnh gỡ lỗi. Bật chế độ gỡ lỗi rồi chạy một tính năng.")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(320, 200)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        lay.addWidget(self.image, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._render_if_dirty)
        self._timer.start()
        self._frame_arrived.connect(self._on_frame)
        unsubscribe = sink.subscribe(self._frame_arrived.emit)
        self._unsubscribe = unsubscribe
        self.destroyed.connect(lambda *_: unsubscribe())

    def _on_frame(self, frame: DebugFrame) -> None:
        self._dirty = True
        self.status.setText(frame.status_line())

    def _render_if_dirty(self) -> None:
        if not self._dirty or not self.isVisible():
            return
        self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        img = self.sink.latest_image()
        if img is None:
            return
        self._pixmap = QPixmap.fromImage(bgr_to_qimage(img))
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is None:
            return
        self.image.setPixmap(self._pixmap.scaled(
            self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._rescale()
