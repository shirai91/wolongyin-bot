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

        self.debug_checkbox = QCheckBox(f"Bật chế độ gỡ lỗi ({hk})" if hk else "Bật chế độ gỡ lỗi")
        self.debug_checkbox.setToolTip(
            "Lưu ảnh từng bước vào logs/run-<id>/. Bật/tắt mục này sẽ tự động bật/tắt cả khung vẽ overlay."
        )
        self.debug_checkbox.setChecked(services.debug.enabled)
        self.debug_checkbox.toggled.connect(self._on_debug_toggled)
        services.debug.changed.connect(self._sync_debug)
        self.checkbox = self.debug_checkbox  # backward compatibility
        lay.addWidget(self.debug_checkbox)

        self.overlay_checkbox = QCheckBox("Vẽ khung gỡ lỗi (overlay)")
        self.overlay_checkbox.setToolTip(
            "Hiển thị khung nhận diện và click trực tiếp trên game mà không lưu ảnh vào thư mục logs. "
            "Thích hợp để quan sát khi chạy lâu dài mà không tốn dung lượng ổ đĩa."
        )
        self.overlay_checkbox.setChecked(services.draw_overlay.enabled)
        self.overlay_checkbox.toggled.connect(self._on_overlay_toggled)
        services.draw_overlay.changed.connect(self._sync_overlay)
        lay.addWidget(self.overlay_checkbox)

        row = QHBoxLayout()
        row.addWidget(QLabel("Làm mới khung gỡ lỗi trên game mỗi"))
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(OVERLAY_REFRESH_MIN_MS, OVERLAY_REFRESH_MAX_MS)
        self.refresh_spin.setSingleStep(50)
        self.refresh_spin.setSuffix(" ms")
        self.refresh_spin.setKeyboardTracking(False)  # save once the value is committed
        self.refresh_spin.setValue(services.settings.overlay_refresh_ms)
        self.refresh_spin.setToolTip(
            "Khi bật vẽ overlay, khung vẽ trên game được chụp và nhận diện lại theo chu kỳ này, "
            "kể cả khi bạn tự chuyển màn hình. Nhỏ hơn = cập nhật nhanh hơn nhưng tốn CPU hơn."
        )
        self.refresh_spin.valueChanged.connect(self._set_refresh)
        row.addWidget(self.refresh_spin)
        row.addStretch(1)
        lay.addLayout(row)

        info = QLabel(
            "Khung vẽ: xanh lam = khung game, xanh lá = tìm thấy, đỏ = không thấy, dấu vàng = điểm sắp click.\n"
            "• Vẽ overlay: chỉ hiển thị trên màn hình game và bảng xem trước, thích hợp chạy lâu dài mà không tốn dung lượng ổ đĩa.\n"
            "• Chế độ gỡ lỗi: lưu ảnh từng bước vào logs/run-<id>/ để phân tích chi tiết. Ảnh lỗi luôn được lưu."
        )
        info.setWordWrap(True)
        lay.addWidget(info)
        self.panel = DebugPanel(services.panel_sink)
        lay.addWidget(self.panel, 1)

    def _set_refresh(self, ms: int) -> None:
        self.services.set_overlay_refresh_ms(ms)

    def _on_debug_toggled(self, checked: bool) -> None:
        self.services.debug.set_enabled(checked)

    def _on_overlay_toggled(self, checked: bool) -> None:
        self.services.draw_overlay.set_enabled(checked)

    def _sync_debug(self, enabled: bool) -> None:
        if self.debug_checkbox.isChecked() != enabled:
            blocked = self.debug_checkbox.blockSignals(True)
            self.debug_checkbox.setChecked(enabled)
            self.debug_checkbox.blockSignals(blocked)

    def _sync_overlay(self, enabled: bool) -> None:
        if self.overlay_checkbox.isChecked() != enabled:
            blocked = self.overlay_checkbox.blockSignals(True)
            self.overlay_checkbox.setChecked(enabled)
            self.overlay_checkbox.blockSignals(blocked)

    def _sync(self, enabled: bool) -> None:
        self._sync_debug(enabled)


@register_tab("debug", "Gỡ lỗi", order=20)
def build(services: Any) -> QWidget:
    return DebugTab(services)
