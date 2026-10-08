"""``canvas_points`` editor: a list of canvas points (1000x600) for AutoClick.

Points are added by typing X/Y, or recorded from the mouse: press "Ghi từ con trỏ", move the mouse
over the game within 3 seconds; the position is converted to canvas coordinates with the canvas
locator. Recording never changes the bound game window (#4).
"""

from __future__ import annotations

from typing import Any

from pydantic.fields import FieldInfo
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ngoalong.ui.forms import FieldEditor, register_widget

RECORD_DELAY_S = 3


class CanvasPointsEditor(FieldEditor):
    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        self._points: list[tuple[int, int]] = []
        self._countdown = 0
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)

        self.list = QListWidget()
        self.list.setMinimumHeight(90)
        lay.addWidget(self.list)

        row = QHBoxLayout()
        self.x = QSpinBox()
        self.x.setRange(0, 999)
        self.x.setPrefix("X ")
        self.y = QSpinBox()
        self.y.setRange(0, 599)
        self.y.setPrefix("Y ")
        self.add_btn = QPushButton("Thêm")
        self.add_btn.clicked.connect(self._add_typed)
        row.addWidget(self.x)
        row.addWidget(self.y)
        row.addWidget(self.add_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.record_btn = QPushButton(f"Ghi từ con trỏ ({RECORD_DELAY_S} giây)")
        self.record_btn.clicked.connect(self._start_record)
        self.remove_btn = QPushButton("Xóa điểm")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn = QPushButton("Xóa hết")
        self.clear_btn.clicked.connect(self.clear)
        row2.addWidget(self.record_btn)
        row2.addWidget(self.remove_btn)
        row2.addWidget(self.clear_btn)
        lay.addLayout(row2)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self._timer = QTimer(root)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.widget = root

    # --- FieldEditor ----------------------------------------------------------------------------
    def get(self) -> list[dict[str, int]]:
        return [{"x": x, "y": y} for x, y in self._points]

    def set(self, value: Any) -> None:
        pts: list[tuple[int, int]] = []
        for v in value or []:
            if isinstance(v, dict):
                pts.append((int(v["x"]), int(v["y"])))
            else:
                pts.append((int(v.x), int(v.y)))
        self._points = pts
        self._refresh()

    # --- editing --------------------------------------------------------------------------------
    def add_point(self, x: int, y: int) -> None:
        self._points.append((int(x), int(y)))
        self._refresh()
        self.changed.emit()

    def clear(self) -> None:
        self._points = []
        self._refresh()
        self.changed.emit()

    def _add_typed(self) -> None:
        self.add_point(self.x.value(), self.y.value())

    def _remove_selected(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._points):
            del self._points[row]
            self._refresh()
            self.changed.emit()

    def _refresh(self) -> None:
        self.list.clear()
        for i, (x, y) in enumerate(self._points, start=1):
            self.list.addItem(f"{i}. ({x}, {y})")

    # --- recording from the mouse ---------------------------------------------------------------
    def _start_record(self) -> None:
        if self.services is None:
            self.status.setText("Không ghi được: ứng dụng chưa sẵn sàng")
            return
        self._countdown = RECORD_DELAY_S
        self.record_btn.setEnabled(False)
        self.status.setText(f"Đưa chuột vào điểm cần click trong game... {self._countdown}")
        self._timer.start()

    def _tick(self) -> None:
        self._countdown -= 1
        if self._countdown > 0:
            self.status.setText(f"Đưa chuột vào điểm cần click trong game... {self._countdown}")
            return
        self._timer.stop()
        self.record_btn.setEnabled(True)
        try:
            p = self.services.cursor_canvas_point()
        except Exception as exc:
            self.status.setText(f"Không ghi được: {exc}")
            return
        self.add_point(p.x, p.y)
        self.status.setText(f"Đã ghi điểm ({p.x}, {p.y})")


@register_widget("canvas_points")
def _factory(name: str, info: FieldInfo, services: Any = None) -> FieldEditor:
    return CanvasPointsEditor(name, info, services)
