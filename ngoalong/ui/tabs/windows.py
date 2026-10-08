""""Nhiều cửa sổ" tab: one runner per game window; pick which feature each window runs.

Each discovered Coowon window is a row with a feature picker, its state and Start / Pause / Stop
buttons. The chosen feature is saved per window title (``settings.multi_window.assignments``);
the feature's parameters are the ones edited in the "Tính năng" tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ngoalong.core.errors import NgoaLongError
from ngoalong.engine.manager import same_window
from ngoalong.engine.runner import RunResult, RunStatus
from ngoalong.ui.tabs import register_tab
from ngoalong.ui.widgets.log_view import LogView

NO_FEATURE = ""
STATE_TEXT = {"idle": "Đang nghỉ", "running": "Đang chạy", "paused": "Tạm dừng", "stopping": "Đang dừng..."}
RESULT_TEXT = {RunStatus.DONE: "Xong", RunStatus.STOPPED: "Đã dừng", RunStatus.FAILED: "Lỗi"}
COL_WINDOW, COL_FEATURE, COL_STATE, COL_ACTIONS = range(4)


@dataclass
class _Row:
    key: str
    combo: QComboBox
    state: QLabel
    start_btn: QPushButton
    pause_btn: QPushButton
    stop_btn: QPushButton


class WindowsTab(QWidget):
    def __init__(self, services: Any) -> None:
        super().__init__()
        self.services = services
        self.manager = services.manager
        self.rows: dict[str, _Row] = {}
        self._last_result: dict[str, str] = {}

        lay = QVBoxLayout(self)
        info = QLabel(
            "Mỗi cửa sổ Coowon đang mở Ngọa Long chạy một tính năng riêng, song song với nhau. "
            "Thông số của tính năng lấy từ thẻ Tính năng. Một cửa sổ chỉ chạy một việc một lúc."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        bar = QHBoxLayout()
        self.refresh_btn = QPushButton("Tìm cửa sổ")
        self.start_all_btn = QPushButton("Bắt đầu tất cả")
        self.stop_all_btn = QPushButton("Dừng tất cả")
        self.refresh_btn.clicked.connect(self.refresh)
        self.start_all_btn.clicked.connect(self.start_all)
        self.stop_all_btn.clicked.connect(self.manager.stop_all)
        self.summary = QLabel("")
        for w in (self.refresh_btn, self.start_all_btn, self.stop_all_btn):
            bar.addWidget(w)
        bar.addWidget(self.summary, 1)
        lay.addLayout(bar)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Cửa sổ", "Tính năng", "Trạng thái", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_WINDOW, QHeaderView.ResizeMode.Stretch)
        for col in (COL_FEATURE, COL_STATE, COL_ACTIONS):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        split.addWidget(self.table)
        self.log_view = LogView()
        split.addWidget(self.log_view)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self.manager.slots_changed.connect(self._rebuild)
        self.manager.slot_state.connect(self._on_state)
        self.manager.slot_log.connect(self._on_log)
        self.manager.slot_finished.connect(self._on_finished)
        executor = getattr(services, "routine_executor", None)
        if executor is not None:  # a routine owns the primary window until it ends
            executor.routine_started.connect(self._on_routine_change)
            executor.routine_finished.connect(self._on_routine_change)
        self.refresh()

    # --- rows -----------------------------------------------------------------------------------
    def refresh(self) -> None:
        slots = self.services.sync_windows()  # emits slots_changed -> _rebuild
        if not slots:
            self.log("warning", "Không thấy cửa sổ Coowon nào đang mở Ngọa Long")

    def _rebuild(self) -> None:
        slots = self.manager.slots()
        self.rows.clear()
        self.table.setRowCount(len(slots))
        for i, slot in enumerate(slots):
            title = slot.title if slot.present else f"{slot.title} (đã đóng)"
            item = QTableWidgetItem(title)
            item.setToolTip(f"pid {slot.window.pid} · cửa sổ 0x{slot.window.hwnd:X}")
            self.table.setItem(i, COL_WINDOW, item)

            combo = QComboBox()
            combo.addItem("(không chạy)", NO_FEATURE)
            for cls in self.services.features:
                combo.addItem(cls.title, cls.id)
            saved = self.services.settings.multi_window.assignments.get(slot.title, NO_FEATURE)
            idx = combo.findData(saved)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.currentIndexChanged.connect(lambda _i, k=slot.key: self._assignment_changed(k))
            self.table.setCellWidget(i, COL_FEATURE, combo)

            state = QLabel()
            self.table.setCellWidget(i, COL_STATE, state)

            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 0, 2, 0)
            start_btn, pause_btn, stop_btn = QPushButton("Bắt đầu"), QPushButton("Tạm dừng"), QPushButton("Dừng")
            start_btn.clicked.connect(lambda _c=False, k=slot.key: self.start(k))
            pause_btn.clicked.connect(lambda _c=False, k=slot.key: self.manager.toggle_pause(k))
            stop_btn.clicked.connect(lambda _c=False, k=slot.key: self.manager.stop(k))
            for b in (start_btn, pause_btn, stop_btn):
                al.addWidget(b)
            self.table.setCellWidget(i, COL_ACTIONS, actions)

            self.rows[slot.key] = _Row(slot.key, combo, state, start_btn, pause_btn, stop_btn)
            self._on_state(slot.key, slot.state)
        self._update_summary()

    def _assignment_changed(self, key: str) -> None:
        row = self.rows.get(key)
        slot = self._slot(key)
        if row is None or slot is None:
            return
        fid = row.combo.currentData() or NO_FEATURE
        assignments = dict(self.services.settings.multi_window.assignments)
        if fid:
            assignments[slot.title] = fid
        else:
            assignments.pop(slot.title, None)
        self.services.settings.multi_window.assignments = assignments
        self.services.save()

    def _slot(self, key: str) -> Any:
        return next((s for s in self.manager.slots() if s.key == key), None)

    # --- running --------------------------------------------------------------------------------
    def selected_feature(self, key: str) -> str:
        row = self.rows.get(key)
        return (row.combo.currentData() or NO_FEATURE) if row is not None else NO_FEATURE

    def set_feature(self, key: str, fid: str) -> None:
        row = self.rows[key]
        idx = row.combo.findData(fid)
        if idx < 0:
            raise KeyError(fid)
        row.combo.setCurrentIndex(idx)

    def _routine_owns(self, slot: Any) -> bool:
        """True when a routine of the "Chuỗi tác vụ" tab runs on this slot's window."""
        executor = getattr(self.services, "routine_executor", None)
        if executor is None or not executor.is_active():
            return False
        primary = getattr(self.services, "window", None)
        return primary is not None and same_window(primary, slot.window)

    def _on_routine_change(self, *_args: object) -> None:
        for slot in self.manager.slots():
            self._on_state(slot.key, slot.state)

    def start(self, key: str) -> bool:
        slot = self._slot(key)
        if slot is None:
            return False
        if self._routine_owns(slot):
            self.log("warning", f"[{slot.title}] Cửa sổ đang chạy chuỗi tác vụ; hãy dừng chuỗi trước")
            return False
        fid = self.selected_feature(key)
        if not fid:
            self.log("warning", f"[{slot.title}] Chưa chọn tính năng")
            return False
        cls = next((c for c in self.services.features if c.id == fid), None)
        if cls is None:
            self.log("error", f"[{slot.title}] Không có tính năng {fid}")
            return False
        params = self.services.settings.feature_params(cls)
        try:
            self.manager.start(key, cls(params).bind_services(self.services))
        except (NgoaLongError, RuntimeError, OSError) as exc:
            self.log("error", f"[{slot.title}] Không chạy được {cls.title}: {exc}")
            return False
        return True

    def start_all(self) -> int:
        """Start every idle window that has a feature; returns how many started."""
        started = 0
        for slot in self.manager.slots():
            if slot.present and not slot.is_running() and self.selected_feature(slot.key):
                started += int(self.start(slot.key))
        if not started:
            self.log("warning", "Không có cửa sổ nào được bắt đầu (chọn tính năng cho từng cửa sổ)")
        return started

    # --- signals --------------------------------------------------------------------------------
    def log(self, level: str, message: str) -> None:
        self.log_view.append_message(level, message)

    def _title(self, key: str) -> str:
        slot = self._slot(key)
        return slot.title if slot is not None else key

    def _on_log(self, key: str, level: str, message: str) -> None:
        self.log(level, f"[{self._title(key)}] {message}")

    def _on_state(self, key: str, state: str) -> None:
        row = self.rows.get(key)
        if row is None:
            return
        slot = self._slot(key)
        running = state != "idle"
        text = STATE_TEXT.get(state, state)
        if not running and key in self._last_result:
            text += f" · {self._last_result[key]}"
        row.state.setText(text)
        row.start_btn.setEnabled(not running and slot is not None and slot.present
                                 and not self._routine_owns(slot))
        row.pause_btn.setEnabled(state in ("running", "paused"))
        row.pause_btn.setText("Tiếp tục" if state == "paused" else "Tạm dừng")
        row.stop_btn.setEnabled(state in ("running", "paused"))
        row.combo.setEnabled(not running)
        self._update_summary()

    def _on_finished(self, key: str, result: RunResult) -> None:
        self._last_result[key] = RESULT_TEXT.get(result.status, str(result.status))
        slot = self._slot(key)
        self._on_state(key, slot.state if slot is not None else "idle")

    def _update_summary(self) -> None:
        slots = self.manager.slots()
        running = sum(1 for s in slots if s.is_running())
        self.summary.setText(f"{len(slots)} cửa sổ · {running} đang chạy")
        self.refresh_btn.setEnabled(True)


@register_tab("windows", "Nhiều cửa sổ", order=10)
def build(services: Any) -> QWidget:
    return WindowsTab(services)
