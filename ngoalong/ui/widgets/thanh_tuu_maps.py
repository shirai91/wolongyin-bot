"""``thanh_tuu_maps`` editor: Chạy Thành Tựu's dungeon picker with its "Đồng bộ" button.

Until a sync has been saved only "Đồng bộ" is enabled (the picker and the other fields of the form
are off; "Bắt đầu" is off through ``ChayThanhTuu.start_blocker``). "Đồng bộ" runs the task in
``mode="sync"`` on the "Tính năng" runner; when it finishes the picker lists every dungeon with ✅
(achievements done) or ❌ and its task count. ``get()`` is the chosen dungeon's name.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError
from pydantic.fields import FieldInfo
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ngoalong.core.errors import NgoaLongError
from ngoalong.ui.forms import FieldEditor, register_widget


class ThanhTuuMapsEditor(FieldEditor):
    def __init__(self, name: str, info: FieldInfo, services: Any = None) -> None:
        super().__init__(name, info, services)
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        # "Đồng bộ" first, on its own row: beside the picker it was pushed out of a narrow window.
        top = QHBoxLayout()
        self.sync_btn = QPushButton("Đồng bộ")
        self.sync_btn.setToolTip("Mở Hoạt Động > T.Tựu trong game và đọc thành tựu của mọi phó bản")
        self.sync_btn.clicked.connect(self.start_sync)
        top.addWidget(self.sync_btn)
        top.addStretch(1)
        lay.addLayout(top)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: gray;")
        lay.addWidget(self.status)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(120)
        # long items ("❌ Đổng Trác (1/6 nhiệm vụ)") must not widen the form past the window
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(12)
        self.combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        lay.addWidget(self.combo)
        self.widget = root
        self._wanted = ""
        self._synced = False
        runner = getattr(services, "runner", None)
        if runner is not None:
            runner.state_changed.connect(self._on_state)
            runner.finished.connect(lambda _r: self.refresh())
            self._on_state(runner.state)

    # --- FieldEditor ----------------------------------------------------------------------------
    def get(self) -> str:
        data = self.combo.currentData()
        return data if isinstance(data, str) else self._wanted

    def set(self, value: Any) -> None:
        self._wanted = "" if value is None else str(value)
        self.refresh()

    # --- internals ------------------------------------------------------------------------------
    def _store(self) -> Any:
        from ngoalong.features.thanh_tuu import store_of

        return store_of(self.services)

    def refresh(self) -> None:
        """Reload the synced dungeons into the picker (keeps the current choice when it is listed)."""
        from ngoalong.core.ocr import MATCH, similarity

        state = self._store().load()
        current = self.get() or self._wanted
        blocked = self.combo.blockSignals(True)
        try:
            self.combo.clear()
            for m in state.maps:
                self.combo.addItem(f"{'✅' if m.done else '❌'} {m.name} ({m.summary})", m.name)
            pick = next((i for i, m in enumerate(state.maps) if current and similarity(current, m.name) >= MATCH), -1)
            if pick < 0:
                pick = next((i for i, m in enumerate(state.maps) if not m.done), 0 if state.maps else -1)
            self.combo.setCurrentIndex(pick)
        finally:
            self.combo.blockSignals(blocked)
        self._synced = state.synced_at is not None
        if self._synced:
            done = sum(m.done for m in state.maps)
            when = time.strftime("%H:%M %d/%m", time.localtime(state.synced_at))
            score = f", điểm {state.score}" if state.score is not None else ""
            self.status.setText(f"Đồng bộ lúc {when}: {len(state.maps)} phó bản, {done} đã xong{score}")
        else:
            self.status.setText("Chưa đồng bộ: mở game (Thành chính hoặc bản đồ Phó Bản) rồi bấm Đồng bộ")
        self.combo.setEnabled(self._synced)
        if self.form is not None:
            self.form.set_others_enabled(self.name, self._synced)
        if self.get() != (self._wanted or ""):
            self._wanted = self.get()
            self.changed.emit()

    def _on_state(self, state: str) -> None:
        self.sync_btn.setEnabled(state == "idle" and self.services is not None)

    def start_sync(self) -> None:
        from ngoalong.features.thanh_tuu import ChayThanhTuu

        s = self.services
        if s is None:
            return
        executor = getattr(s, "routine_executor", None)
        if executor is not None and executor.is_active():
            self.status.setText("Chuỗi tác vụ đang chạy; hãy dừng chuỗi trước khi đồng bộ")
            return
        try:
            params = self.form.value() if self.form is not None else ChayThanhTuu.Params()
        except ValidationError:
            params = ChayThanhTuu.Params()
        task = ChayThanhTuu(params, store=self._store(), mode="sync")
        try:
            s.runner.start(task, s.make_context)
        except (NgoaLongError, RuntimeError, OSError) as exc:
            self.status.setText(f"Không đồng bộ được: {exc}")
            return
        self.status.setText("Đang đồng bộ... (xem nhật ký)")


@register_widget("thanh_tuu_maps")
def _factory(name: str, info: FieldInfo, services: Any = None) -> FieldEditor:
    return ThanhTuuMapsEditor(name, info, services)
