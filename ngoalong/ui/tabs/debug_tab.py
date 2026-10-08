""""Gỡ lỗi" tab: debug-mode switch and the debug panel (last capture with annotations)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from ngoalong.config.settings import OVERLAY_REFRESH_MAX_MS, OVERLAY_REFRESH_MIN_MS
from ngoalong.ui.tabs import register_tab
from ngoalong.ui.widgets.debug_panel import DebugPanel


class DebugTab(QWidget):
    def __init__(self, services: Any) -> None:
        super().__init__()
        self.services = services
        lay = QVBoxLayout(self)
        hk = services.settings.hotkeys.debug_toggle
        self.checkbox = QCheckBox(f"Bật chế độ gỡ lỗi ({hk})" if hk else "Bật chế độ gỡ lỗi")
        self.checkbox.setChecked(services.debug.enabled)
        self.checkbox.toggled.connect(services.debug.set_enabled)
        services.debug.changed.connect(self._sync)
        lay.addWidget(self.checkbox)
        row = QHBoxLayout()
        row.addWidget(QLabel("Làm mới khung gỡ lỗi trên game mỗi"))
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(OVERLAY_REFRESH_MIN_MS, OVERLAY_REFRESH_MAX_MS)
        self.refresh_spin.setSingleStep(50)
        self.refresh_spin.setSuffix(" ms")
        self.refresh_spin.setKeyboardTracking(False)  # save once the value is committed
        self.refresh_spin.setValue(services.settings.overlay_refresh_ms)
        self.refresh_spin.setToolTip(
            "Khi bật gỡ lỗi, khung vẽ trên game được chụp và nhận diện lại theo chu kỳ này, "
            "kể cả khi bạn tự chuyển màn hình. Nhỏ hơn = cập nhật nhanh hơn nhưng tốn CPU hơn."
        )
        self.refresh_spin.valueChanged.connect(self._set_refresh)
        row.addWidget(self.refresh_spin)
        row.addStretch(1)
        lay.addLayout(row)
        info = QLabel(
            "Khi bật: khung xanh lam = khung game, xanh lá = tìm thấy, đỏ = không thấy, "
            "dấu vàng = điểm sắp click. Mỗi bước được lưu thành ảnh trong logs/run-<id>/. "
            "Ảnh lỗi luôn được lưu, kể cả khi tắt."
        )
        info.setWordWrap(True)
        lay.addWidget(info)
        self.panel = DebugPanel(services.panel_sink)
        lay.addWidget(self.panel, 1)

    def _set_refresh(self, ms: int) -> None:
        self.services.set_overlay_refresh_ms(ms)

    def _sync(self, enabled: bool) -> None:
        if self.checkbox.isChecked() != enabled:
            blocked = self.checkbox.blockSignals(True)
            self.checkbox.setChecked(enabled)
            self.checkbox.blockSignals(blocked)


@register_tab("debug", "Gỡ lỗi", order=20)
def build(services: Any) -> QWidget:
    return DebugTab(services)
