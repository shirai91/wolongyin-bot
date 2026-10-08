""""Chuỗi tác vụ" tab: saved routine chains (e.g. Thám Bảo -> Chúa công -> Tháp Thí Luyện), their
schedule, per-step retries, stop-on-error, and Run now / Stop (docs/06 P3).

Each step runs with the params saved in the "Tính năng" tab (a step's own ``params`` override,
when present in ``data/routines.json``, is kept untouched by this editor). A step can have a time
limit ("Tối đa (phút)"): when it is up the step is stopped and the chain goes on. Saving warns about
a step that would loop until Stop (0 rounds) without a time limit.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QVBoxLayout, QWidget,
)

from ngoalong.engine.scheduler import (
    MAX_RETRIES, MAX_RETRY_DELAY_S, MAX_STEP_DURATION_S, ROUTINE_STATUS_TEXT, WEEKDAY_TEXT, Routine,
    RoutineOutcome, RoutineStep, Schedule, parse_times, runs_until_stopped,
)
from ngoalong.ui.tabs import register_tab

COL_FEATURE, COL_RETRIES, COL_DELAY, COL_ENABLED, COL_DURATION = range(5)


def _duration_minutes(step: RoutineStep) -> int:
    return math.ceil(step.max_duration_s / 60) if step.max_duration_s else 0


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
    return str(exc)


class RoutinesTab(QWidget):
    def __init__(self, services: Any) -> None:
        super().__init__()
        self.services = services
        self.store = services.routine_store
        self.scheduler = services.scheduler
        self.executor = services.routine_executor
        self.features = list(services.features)
        self._steps: list[RoutineStep] = []  # the steps shown in the table (params kept per row)
        self._current_id: str | None = None

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.enabled_box = QCheckBox("Bật lịch tự động")
        self.enabled_box.setChecked(self.store.scheduler_enabled)
        self.enabled_box.toggled.connect(self._on_scheduler_toggled)
        self.next_label = QLabel("")
        top.addWidget(self.enabled_box)
        top.addWidget(self.next_label, 1)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # --- routine list ---------------------------------------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        ll.addWidget(self.list, 1)
        lb = QHBoxLayout()
        self.add_btn = QPushButton("Thêm chuỗi")
        self.del_btn = QPushButton("Xóa")
        self.add_btn.clicked.connect(self.new_routine)
        self.del_btn.clicked.connect(lambda: self.delete_selected())
        lb.addWidget(self.add_btn)
        lb.addWidget(self.del_btn)
        ll.addLayout(lb)
        left.setMaximumWidth(240)
        split.addWidget(left)

        # --- editor ---------------------------------------------------------------------------
        self.editor = QWidget()
        el = QVBoxLayout(self.editor)
        el.setContentsMargins(0, 0, 0, 0)
        form_box = QGroupBox("Chuỗi tác vụ")
        form = QFormLayout(form_box)
        self.name_edit = QLineEdit()
        form.addRow("Tên", self.name_edit)
        self.sched_box = QCheckBox("Chạy theo lịch")
        form.addRow(self.sched_box)
        self.times_edit = QLineEdit()
        self.times_edit.setPlaceholderText("ví dụ: 08:00, 20:30")
        form.addRow("Giờ chạy", self.times_edit)
        days = QHBoxLayout()
        self.day_boxes: list[QCheckBox] = []
        for text in WEEKDAY_TEXT:
            cb = QCheckBox(text)
            self.day_boxes.append(cb)
            days.addWidget(cb)
        days.addStretch(1)
        form.addRow("Ngày (bỏ trống = mỗi ngày)", days)
        self.stop_on_error_box = QCheckBox("Dừng cả chuỗi khi một bước vẫn lỗi sau khi thử lại")
        form.addRow(self.stop_on_error_box)
        el.addWidget(form_box)

        steps_box = QGroupBox("Các bước (chạy lần lượt; thông số lấy từ thẻ Tính năng)")
        sl = QVBoxLayout(steps_box)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Tính năng", "Thử lại", "Chờ thử lại (giây)", "Bật",
                                              "Tối đa (phút)"])
        header_tip = self.table.horizontalHeaderItem(COL_DURATION)
        if header_tip is not None:
            header_tip.setToolTip("Hết thời gian thì dừng bước và chạy bước sau (0 = không giới hạn). "
                                  "Cần cho tính năng chạy đến khi bấm Dừng (số lượt = 0).")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_FEATURE, QHeaderView.ResizeMode.Stretch)
        for c in (COL_RETRIES, COL_DELAY, COL_ENABLED, COL_DURATION):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        sl.addWidget(self.table, 1)
        sb = QHBoxLayout()
        self.add_step_btn = QPushButton("Thêm bước")
        self.del_step_btn = QPushButton("Xóa bước")
        self.up_btn = QPushButton("Lên")
        self.down_btn = QPushButton("Xuống")
        self.add_step_btn.clicked.connect(lambda: self.add_step())
        self.del_step_btn.clicked.connect(self.remove_step)
        self.up_btn.clicked.connect(lambda: self.move_step(-1))
        self.down_btn.clicked.connect(lambda: self.move_step(1))
        for b in (self.add_step_btn, self.del_step_btn, self.up_btn, self.down_btn):
            sb.addWidget(b)
        sb.addStretch(1)
        sl.addLayout(sb)
        el.addWidget(steps_box, 1)

        actions = QHBoxLayout()
        self.save_btn = QPushButton("Lưu")
        self.run_btn = QPushButton("Chạy ngay")
        self.stop_btn = QPushButton("Dừng chuỗi")
        self.save_btn.clicked.connect(self.save_current)
        self.run_btn.clicked.connect(self.run_selected)
        self.stop_btn.clicked.connect(self.executor.stop)
        for b in (self.save_btn, self.run_btn, self.stop_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        el.addLayout(actions)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        el.addWidget(self.message)
        split.addWidget(self.editor)
        split.setStretchFactor(1, 1)

        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)
        root.addWidget(self.state_label)

        self.executor.routine_started.connect(lambda *_: self.refresh_state())
        self.executor.step_started.connect(lambda *_: self.refresh_state())
        self.executor.routine_finished.connect(self._on_routine_finished)
        self.scheduler.queue_changed.connect(self.refresh_state)
        self.services.runner.state_changed.connect(lambda *_: self.refresh_state())
        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.refresh_next)
        self._timer.start()

        self.reload()

    # --- helpers --------------------------------------------------------------------------------
    def feature_title(self, fid: str) -> str:
        return next((c.title for c in self.features if c.id == fid), fid)

    def routine_name(self, rid: str) -> str:
        r = self.store.get(rid)
        return r.name if r is not None else rid

    def _say(self, text: str, error: bool = False) -> None:
        self.message.setStyleSheet("color: #c0392b;" if error else "")
        self.message.setText(text)

    # --- list -----------------------------------------------------------------------------------
    def reload(self, select_id: str | None = None) -> None:
        routines = self.store.routines()
        keep = select_id or self._current_id
        blocked = self.list.blockSignals(True)
        self.list.clear()
        row = -1
        for i, r in enumerate(routines):
            item = QListWidgetItem(r.name + ("" if r.enabled and r.schedule.active else "  (không lịch)"))
            item.setData(Qt.ItemDataRole.UserRole, r.id)
            item.setToolTip(r.schedule.describe())
            self.list.addItem(item)
            if r.id == keep:
                row = i
        self.list.blockSignals(blocked)
        if row < 0 and routines:
            row = 0
        self.list.setCurrentRow(row)
        self._on_select(row)
        self.refresh_next()
        self.refresh_state()

    def selected_id(self) -> str | None:
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _on_select(self, row: int) -> None:
        item = self.list.item(row) if row >= 0 else None
        routine = self.store.get(str(item.data(Qt.ItemDataRole.UserRole))) if item is not None else None
        self.editor.setEnabled(routine is not None)
        self.del_btn.setEnabled(routine is not None)
        if routine is None:
            self._current_id = None
            self._load(Routine(name="-"))
            return
        self._current_id = routine.id
        self._load(routine)

    def new_routine(self) -> Routine:
        names = {r.name for r in self.store.routines()}
        n = 1
        while f"Chuỗi mới {n}" in names:
            n += 1
        steps = [RoutineStep(feature_id=self.features[0].id)] if self.features else []
        r = self.store.upsert(Routine(name=f"Chuỗi mới {n}", steps=steps, enabled=False))
        self.reload(r.id)
        self._say(f"Đã thêm {r.name}. Chọn các bước và giờ chạy rồi bấm Lưu.")
        return r

    def delete_selected(self, confirm: bool = True) -> bool:
        rid = self.selected_id()
        if rid is None:
            return False
        name = self.routine_name(rid)
        if confirm and QMessageBox.question(self, "Xóa chuỗi", f"Xóa chuỗi {name}?") \
                != QMessageBox.StandardButton.Yes:
            return False
        cur = self.executor.routine
        if cur is not None and cur.id == rid:
            self._say("Chuỗi đang chạy; hãy dừng trước khi xóa.", error=True)
            return False
        self.scheduler.remove_queued(rid)
        self.store.delete(rid)
        self._current_id = None
        self.reload()
        self._say(f"Đã xóa {name}.")
        return True

    # --- editor ---------------------------------------------------------------------------------
    def _load(self, r: Routine) -> None:
        self.name_edit.setText(r.name)
        self.sched_box.setChecked(r.enabled)
        self.times_edit.setText(", ".join(r.schedule.times))
        for i, cb in enumerate(self.day_boxes):
            cb.setChecked(i in r.schedule.weekdays)
        self.stop_on_error_box.setChecked(r.stop_on_error)
        self._steps = [s.model_copy(deep=True) for s in r.steps]
        self._render_steps()

    def _render_steps(self, select: int | None = None) -> None:
        self.table.setRowCount(0)
        for step in self._steps:
            row = self.table.rowCount()
            self.table.insertRow(row)
            combo = QComboBox()
            for c in self.features:
                combo.addItem(c.title, c.id)
            idx = combo.findData(step.feature_id)
            if idx < 0:  # a feature that no longer exists: keep it visible
                combo.addItem(f"(không có) {step.feature_id}", step.feature_id)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
            self.table.setCellWidget(row, COL_FEATURE, combo)
            retries = QSpinBox()
            retries.setRange(0, MAX_RETRIES)
            retries.setValue(step.retries)
            self.table.setCellWidget(row, COL_RETRIES, retries)
            delay = QSpinBox()
            delay.setRange(0, int(MAX_RETRY_DELAY_S))
            delay.setValue(int(step.retry_delay_s))
            self.table.setCellWidget(row, COL_DELAY, delay)
            on = QCheckBox()
            on.setChecked(step.enabled)
            self.table.setCellWidget(row, COL_ENABLED, on)
            limit = QSpinBox()
            limit.setRange(0, int(MAX_STEP_DURATION_S // 60))
            limit.setSpecialValueText("không")
            limit.setValue(_duration_minutes(step))
            self.table.setCellWidget(row, COL_DURATION, limit)
        if select is not None and 0 <= select < len(self._steps):
            self.table.selectRow(select)

    def _collect_steps(self) -> list[RoutineStep]:
        """Read the table back into ``self._steps`` (keeping each step's params override)."""
        out: list[RoutineStep] = []
        for row, old in enumerate(self._steps):
            combo = self.table.cellWidget(row, COL_FEATURE)
            retries = self.table.cellWidget(row, COL_RETRIES)
            delay = self.table.cellWidget(row, COL_DELAY)
            on = self.table.cellWidget(row, COL_ENABLED)
            limit = self.table.cellWidget(row, COL_DURATION)
            fid = combo.currentData() if isinstance(combo, QComboBox) else old.feature_id
            duration = old.max_duration_s
            if isinstance(limit, QSpinBox) and limit.value() != _duration_minutes(old):
                duration = limit.value() * 60.0 or None  # changed in the editor (minutes)
            out.append(RoutineStep(
                feature_id=str(fid),
                params=old.params if fid == old.feature_id else None,
                retries=retries.value() if isinstance(retries, QSpinBox) else old.retries,
                retry_delay_s=float(delay.value()) if isinstance(delay, QSpinBox) else old.retry_delay_s,
                max_duration_s=duration,
                enabled=on.isChecked() if isinstance(on, QCheckBox) else old.enabled,
            ))
        self._steps = out
        return out

    def _selected_step(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else self.table.currentRow()

    def add_step(self, feature_id: str | None = None) -> None:
        self._collect_steps()
        fid = feature_id or (self.features[0].id if self.features else "")
        if not fid:
            return
        self._steps.append(RoutineStep(feature_id=fid))
        self._render_steps(len(self._steps) - 1)

    def remove_step(self) -> None:
        row = self._selected_step()
        if not 0 <= row < len(self._steps):
            return
        self._collect_steps()
        del self._steps[row]
        self._render_steps(min(row, len(self._steps) - 1))

    def move_step(self, delta: int) -> None:
        row = self._selected_step()
        dst = row + delta
        if not (0 <= row < len(self._steps) and 0 <= dst < len(self._steps)):
            return
        self._collect_steps()
        self._steps[row], self._steps[dst] = self._steps[dst], self._steps[row]
        self._render_steps(dst)

    def editor_routine(self) -> Routine:
        """The routine as edited (raises ``ValueError`` / ``ValidationError`` when invalid)."""
        if self._current_id is None:
            raise ValueError("chưa chọn chuỗi")
        base = self.store.get(self._current_id)
        if base is None:
            raise ValueError("chuỗi đã bị xóa")
        times = parse_times(self.times_edit.text())
        days = [i for i, cb in enumerate(self.day_boxes) if cb.isChecked()]
        return Routine(
            id=base.id, name=self.name_edit.text(), steps=self._collect_steps(),
            schedule=Schedule(times=times, weekdays=days), enabled=self.sched_box.isChecked(),
            stop_on_error=self.stop_on_error_box.isChecked(), last_fired_at=base.last_fired_at,
        )

    def save_current(self) -> Routine | None:
        try:
            r = self.editor_routine()
        except (ValueError, ValidationError) as exc:
            self._say(f"Không lưu được: {_error_text(exc)}", error=True)
            return None
        if r.enabled and r.schedule.active and not r.active_steps:
            self._say("Không lưu được: chuỗi chạy theo lịch cần ít nhất một bước được bật", error=True)
            return None
        saved = self.store.upsert(r)
        self.reload(saved.id)
        warning = self.endless_steps_warning(saved)
        self._say(f"Đã lưu {saved.name}: {saved.schedule.describe()}" + (f". {warning}" if warning else ""),
                  error=bool(warning))
        return saved

    def endless_steps_warning(self, routine: Routine) -> str:
        """A warning naming the enabled steps that loop until Stop and have no time limit."""
        build = getattr(self.services, "build_routine_task", None)
        if build is None:
            return ""
        endless: list[str] = []
        for idx, step in routine.active_steps:
            if step.max_duration_s:
                continue
            try:
                params = build(step).params
            except Exception:  # an invalid step is reported when it runs
                continue
            if runs_until_stopped(params):
                endless.append(f"bước {idx + 1} ({self.feature_title(step.feature_id)})")
        if not endless:
            return ""
        return ("Cảnh báo: " + ", ".join(endless) + " chạy đến khi bấm Dừng (số lượt = 0) nên các bước "
                "sau sẽ không chạy; hãy đặt cột Tối đa (phút) hoặc số lượt trong thẻ Tính năng")

    # --- running --------------------------------------------------------------------------------
    def run_selected(self) -> str:
        saved = self.save_current()
        if saved is None:
            return "invalid"
        if not saved.active_steps:
            self._say("Chuỗi chưa có bước nào được bật", error=True)
            return "empty"
        warning = self.endless_steps_warning(saved)
        result = self.scheduler.run_now(saved.id)
        if result == "queued":
            self._say(f"{saved.name} đang chờ: một tác vụ khác đang chạy" + (f". {warning}" if warning else ""),
                      error=bool(warning))
        elif result == "started":
            self._say(f"Đang chạy {saved.name}" + (f". {warning}" if warning else ""), error=bool(warning))
        self.refresh_state()
        return result

    def _on_scheduler_toggled(self, on: bool) -> None:
        self.store.set_scheduler_enabled(on)
        self.refresh_next()

    def _on_routine_finished(self, outcome: RoutineOutcome) -> None:
        self._say(f"{outcome.routine_name}: {ROUTINE_STATUS_TEXT[outcome.status.value]} - {outcome.message}",
                  error=outcome.status.value in ("failed", "partial"))
        self.refresh_state()

    def refresh_next(self) -> None:
        if not self.store.scheduler_enabled:
            self.next_label.setText("Lịch tự động đang tắt")
            return
        upcoming = self.scheduler.scheduler.upcoming(datetime.now())
        if not upcoming:
            self.next_label.setText("Chưa có chuỗi nào được hẹn giờ")
            return
        r, when = upcoming[0]
        day = "hôm nay" if when.date() == datetime.now().date() else f"{when:%d/%m}"
        self.next_label.setText(f"Lần chạy tới: {r.name} lúc {when:%H:%M} {day}")

    def refresh_state(self) -> None:
        ex = self.executor
        r = ex.routine
        if r is not None:
            cur = ex.current_step
            if cur is not None:
                n = len(r.active_steps)
                pos = next((i for i, (idx, _s) in enumerate(r.active_steps) if idx == cur.index), 0) + 1
                wait = " - đang chờ để thử lại" if ex.waiting_retry else ""
                text = (f"Đang chạy chuỗi {r.name}: bước {pos}/{n} {self.feature_title(cur.step.feature_id)}"
                        + (f" (lần {cur.attempt})" if cur.attempt > 1 else "") + wait)
            else:
                text = f"Đang chạy chuỗi {r.name}"
        else:
            text = "Không có chuỗi nào đang chạy"
        queued = self.scheduler.queued()
        if queued:
            text += " · Đang chờ: " + ", ".join(self.routine_name(q.routine_id) for q in queued)
        self.state_label.setText(text)
        self.stop_btn.setEnabled(r is not None)


@register_tab("routines", "Chuỗi tác vụ", order=15)
def build(services: Any) -> QWidget:
    return RoutinesTab(services)
