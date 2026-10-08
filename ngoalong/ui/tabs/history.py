""""Lịch sử" tab: every run with its start/end, task, routine, result, counters and failure
screenshots (docs/06 P3 "Run history"), filtered by task, result and period."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ngoalong.engine.history import STATUS_TEXT, RunRecord
from ngoalong.ui.tabs import register_tab

MAX_ROWS = 500
COLUMNS = ["Bắt đầu", "Thời lượng", "Tác vụ", "Chuỗi", "Kết quả", "Bộ đếm", "Ghi chú"]
PERIODS: list[tuple[str, int | None]] = [("Hôm nay", 0), ("7 ngày", 7), ("30 ngày", 30), ("Tất cả", None)]
STATUS_COLORS = {"failed": "#c0392b", "stopped": "#b9770e"}


def format_duration(seconds: float) -> str:
    s = int(round(max(0.0, seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def period_start(days: int | None, now: datetime | None = None) -> datetime | None:
    """``0`` = since midnight today, ``n`` = the last ``n`` days, ``None`` = everything."""
    if days is None:
        return None
    now = now or datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight if days == 0 else now - timedelta(days=days)


class HistoryTab(QWidget):
    def __init__(self, services: Any) -> None:
        super().__init__()
        self.services = services
        self.store = services.history
        self.records: list[RunRecord] = []

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.task_combo = QComboBox()
        self.task_combo.addItem("Tất cả tác vụ", "")
        for c in services.features:
            self.task_combo.addItem(c.title, c.id)
        self.status_combo = QComboBox()
        self.status_combo.addItem("Mọi kết quả", "")
        for value, text in STATUS_TEXT.items():
            self.status_combo.addItem(text, value)
        self.period_combo = QComboBox()
        for text, days in PERIODS:
            self.period_combo.addItem(text, days)
        self.period_combo.setCurrentIndex(1)
        self.refresh_btn = QPushButton("Làm mới")
        self.clear_btn = QPushButton("Xóa lịch sử")
        for w in (self.task_combo, self.status_combo, self.period_combo):
            w.currentIndexChanged.connect(lambda *_: self.refresh())
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.refresh_btn)
        bar.addWidget(self.clear_btn)
        self.refresh_btn.clicked.connect(lambda: self.refresh())
        self.clear_btn.clicked.connect(lambda: self.clear_history())
        root.addLayout(bar)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        for c in range(len(COLUMNS) - 1):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(lambda *_: self._show_details())
        split.addWidget(self.table)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        dl.addWidget(self.details, 1)
        db = QHBoxLayout()
        self.shot_btn = QPushButton("Mở ảnh lỗi")
        self.dir_btn = QPushButton("Mở thư mục nhật ký")
        self.shot_btn.clicked.connect(self.open_screenshot)
        self.dir_btn.clicked.connect(self.open_run_dir)
        db.addWidget(self.shot_btn)
        db.addWidget(self.dir_btn)
        db.addStretch(1)
        dl.addLayout(db)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # New runs arrive from the recorder; refresh once per burst.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(200)
        self._refresh_timer.timeout.connect(self.refresh)
        services.history_recorder.recorded.connect(lambda *_: self._refresh_timer.start())

        self.refresh()

    # --- data -----------------------------------------------------------------------------------
    def feature_title(self, fid: str) -> str:
        return next((c.title for c in self.services.features if c.id == fid), fid)

    def filters(self) -> dict[str, Any]:
        return {
            "task_id": self.task_combo.currentData() or None,
            "status": self.status_combo.currentData() or None,
            "since": period_start(self.period_combo.currentData()),
        }

    def refresh(self) -> None:
        f = self.filters()
        try:
            self.records = self.store.query(limit=MAX_ROWS, **f)
            total = self.store.count(**f)
        except Exception as exc:  # a locked or broken database must not break the UI
            self.records, total = [], 0
            self.summary.setText(f"Không đọc được lịch sử: {exc}")
        else:
            self._summarise(total)
        self._fill()

    def _summarise(self, total: int) -> None:
        by: dict[str, int] = {}
        for r in self.records:
            by[r.status] = by.get(r.status, 0) + 1
        parts = [f"{n} {STATUS_TEXT.get(s, s).lower()}" for s, n in sorted(by.items())]
        shown = f" (hiện {len(self.records)} lượt mới nhất)" if total > len(self.records) else ""
        self.summary.setText(f"{total} lượt chạy" + (": " + ", ".join(parts) if parts else "") + shown)

    def _fill(self) -> None:
        self.table.setRowCount(len(self.records))
        for row, r in enumerate(self.records):
            title = r.task_title or self.feature_title(r.task_id)
            routine = r.routine_name + (f" (lần {r.attempt})" if r.routine_name and r.attempt > 1 else "")
            cells = [f"{r.started_at:%d/%m %H:%M:%S}", format_duration(r.duration_s), title, routine,
                     r.status_text, r.counters_text(), r.message]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r.id)
                color = STATUS_COLORS.get(r.status)
                if col == 4 and color:
                    item.setForeground(QColor(color))
                self.table.setItem(row, col, item)
        if self.records:
            self.table.selectRow(0)
        self._show_details()

    def selected(self) -> RunRecord | None:
        row = self.table.currentRow()
        return self.records[row] if 0 <= row < len(self.records) else None

    def _show_details(self) -> None:
        r = self.selected()
        self.shot_btn.setEnabled(bool(r and r.screenshots))
        self.dir_btn.setEnabled(bool(r and r.run_dir))
        if r is None:
            self.details.setPlainText("")
            return
        lines = [
            f"Tác vụ: {r.task_title or self.feature_title(r.task_id)}",
            f"Bắt đầu: {r.started_at:%d/%m/%Y %H:%M:%S} - Kết thúc: {r.ended_at:%H:%M:%S} "
            f"({format_duration(r.duration_s)})",
            f"Kết quả: {r.status_text} - {r.message}",
        ]
        if r.routine_name:
            lines.append(f"Chuỗi: {r.routine_name} (lần thử {r.attempt})")
        if r.window:
            lines.append(f"Cửa sổ: {r.window}")
        if r.counters:
            lines.append(f"Bộ đếm: {r.counters_text()}")
        lines += [f"Ảnh lỗi: {p}" for p in r.screenshots]
        if r.run_dir:
            lines.append(f"Thư mục nhật ký: {r.run_dir}")
        self.details.setPlainText("\n".join(lines))

    # --- actions --------------------------------------------------------------------------------
    def clear_history(self, confirm: bool = True) -> bool:
        if confirm and QMessageBox.question(
            self, "Xóa lịch sử", "Xóa toàn bộ lịch sử chạy? (ảnh và nhật ký trong logs/ được giữ lại)"
        ) != QMessageBox.StandardButton.Yes:
            return False
        self.store.clear()
        self.refresh()
        return True

    @staticmethod
    def _open(path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.resolve())))

    def open_screenshot(self) -> bool:
        r = self.selected()
        return bool(r and r.screenshots) and self._open(r.screenshots[-1])  # type: ignore[union-attr]

    def open_run_dir(self) -> bool:
        r = self.selected()
        return bool(r and r.run_dir) and self._open(r.run_dir)  # type: ignore[union-attr]


@register_tab("history", "Lịch sử", order=40)
def build(services: Any) -> QWidget:
    return HistoryTab(services)
