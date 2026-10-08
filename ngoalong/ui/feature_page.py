""""Tính năng" page: feature list, the form generated from each feature's Params, run controls."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QProgressBar, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ngoalong.core.errors import NgoaLongError
from ngoalong.engine.runner import RunResult, RunStatus
from ngoalong.engine.task import Task
from ngoalong.ui.forms import ParamsForm, format_validation_error

STATE_TEXT = {"idle": "Đang nghỉ", "running": "Đang chạy", "paused": "Tạm dừng", "stopping": "Đang dừng..."}


class FeaturePage(QWidget):
    message = Signal(str, str)  # level, text (for the log view)

    def __init__(self, services: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.features: list[type[Task]] = list(services.features)
        self.forms: dict[str, ParamsForm] = {}
        self.blockers: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        split.addWidget(self.list)
        self.stack = QStackedWidget()
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)

        for cls in self.features:
            self.list.addItem(cls.title)
            self.stack.addWidget(self._build_page(cls))
        self.list.currentRowChanged.connect(self._select)

        controls = QHBoxLayout()
        self.start_btn = QPushButton("Bắt đầu")
        self.pause_btn = QPushButton("Tạm dừng")
        self.stop_btn = QPushButton("Dừng")
        self.start_btn.clicked.connect(self.start_selected)
        self.pause_btn.clicked.connect(services.runner.toggle_pause)
        self.stop_btn.clicked.connect(services.runner.stop)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m")
        self.state_label = QLabel(STATE_TEXT["idle"])
        for w in (self.start_btn, self.pause_btn, self.stop_btn):
            controls.addWidget(w)
        controls.addWidget(self.progress, 1)
        controls.addWidget(self.state_label)
        root.addLayout(controls)

        runner = services.runner
        runner.state_changed.connect(self._on_state)
        runner.progress.connect(self._on_progress)
        runner.finished.connect(self._on_finished)
        # A routine owns the runner between its steps too (retry waits): no manual Start then.
        executor = getattr(services, "routine_executor", None)
        if executor is not None:
            executor.routine_started.connect(self._on_routine_change)
            executor.routine_finished.connect(self._on_routine_change)
        self._on_state(runner.state)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(services.save)

        last = services.settings.ui.last_feature
        ids = [c.id for c in self.features]
        self.list.setCurrentRow(ids.index(last) if last in ids else (0 if ids else -1))

    # --- pages ----------------------------------------------------------------------------------
    def _build_page(self, cls: type[Task]) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        title = QLabel(f"<b>{cls.title}</b>")
        lay.addWidget(title)
        if cls.description:
            d = QLabel(cls.description)
            d.setWordWrap(True)
            lay.addWidget(d)
        if cls.start_scene:
            s = QLabel(f"Màn hình bắt đầu: {cls.start_scene}")
            s.setStyleSheet("color: gray;")
            lay.addWidget(s)
        form = ParamsForm(cls.Params, self.services.settings.feature_params(cls), self.services)
        form.changed.connect(lambda c=cls: self._params_changed(c))
        self.forms[cls.id] = form
        blocker = QLabel()
        blocker.setWordWrap(True)
        blocker.setStyleSheet("color: #c0392b;")
        blocker.hide()
        self.blockers[cls.id] = blocker
        lay.addWidget(blocker)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        lay.addWidget(scroll, 1)
        return page

    def _select(self, row: int) -> None:
        if 0 <= row < len(self.features):
            self.stack.setCurrentIndex(row)
            self.services.settings.ui.last_feature = self.features[row].id
            self._on_state(self.services.runner.state)

    def current_feature(self) -> type[Task] | None:
        row = self.list.currentRow()
        return self.features[row] if 0 <= row < len(self.features) else None

    def select_feature(self, fid: str) -> None:
        for i, c in enumerate(self.features):
            if c.id == fid:
                self.list.setCurrentRow(i)
                return
        raise KeyError(fid)

    def _params_changed(self, cls: type[Task]) -> None:
        try:
            params = self.forms[cls.id].value()
        except ValidationError:
            return  # keep the last valid params saved; the error is shown on Start
        self.services.settings.set_feature_params(cls.id, params)
        self._save_timer.start()
        self._on_state(self.services.runner.state)

    def start_blocker(self, cls: type[Task]) -> str | None:
        """The feature's reason not to start now (shown under its form), or ``None``."""
        try:
            params = self.forms[cls.id].value()
        except ValidationError:
            return None  # invalid params are reported on Start
        try:
            return cls.start_blocker(params, self.services)
        except Exception as exc:  # a broken check must not lock the page
            return f"Không kiểm tra được điều kiện chạy: {exc}"

    def refresh_blocker(self) -> None:
        self._on_state(self.services.runner.state)

    # --- running --------------------------------------------------------------------------------
    def start_selected(self) -> bool:
        cls = self.current_feature()
        if cls is None:
            return False
        if self.routine_active():
            self.message.emit("warning", "Chuỗi tác vụ đang chạy; hãy dừng chuỗi trước khi chạy tính năng khác")
            return False
        blocker = self.start_blocker(cls)
        if blocker is not None:
            self.message.emit("warning", blocker)
            return False
        form = self.forms[cls.id]
        try:
            params = form.value()
        except ValidationError as exc:
            self.message.emit("error", f"Thông số không hợp lệ: {format_validation_error(exc, cls.Params)}")
            return False
        self.services.settings.set_feature_params(cls.id, params)
        self.services.save()
        self.progress.setRange(0, 0)
        try:
            self.services.runner.start(cls(params).bind_services(self.services), self.services.make_context)
        except (NgoaLongError, RuntimeError, OSError) as exc:
            self.progress.setRange(0, 1)
            self.message.emit("error", f"Không chạy được {cls.title}: {exc}")
            return False
        return True

    def _on_routine_change(self, *_args: object) -> None:
        self._on_state(self.services.runner.state)

    def routine_active(self) -> bool:
        executor = getattr(self.services, "routine_executor", None)
        return executor is not None and executor.is_active()

    def _on_state(self, state: str) -> None:
        running = state != "idle" or self.routine_active()
        cls = self.current_feature()
        blocker = self.start_blocker(cls) if cls is not None else None
        if cls is not None:
            label = self.blockers[cls.id]
            label.setText(blocker or "")
            label.setVisible(blocker is not None)
        self.start_btn.setEnabled(not running and blocker is None)
        self.pause_btn.setEnabled(state in ("running", "paused"))
        self.pause_btn.setText("Tiếp tục" if state == "paused" else "Tạm dừng")
        self.stop_btn.setEnabled(state in ("running", "paused"))
        self.list.setEnabled(not running)
        text = STATE_TEXT.get(state, state)
        if state == "idle" and self.routine_active():
            text = "Chuỗi tác vụ đang chạy"
        self.state_label.setText(text)

    def _on_progress(self, done: int, total: int) -> None:
        if total < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(min(done, total))

    def _on_finished(self, result: RunResult) -> None:
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if result.status is RunStatus.DONE else 0)
        # A routine may already have started its next step on this runner (it is connected first).
        self._on_state(self.services.runner.state)
