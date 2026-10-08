""""Cài đặt" tab: configurable hotkeys and where the settings live."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ngoalong.config.settings import HotkeySettings
from ngoalong.ui.tabs import register_tab


class SettingsTab(QWidget):
    def __init__(self, services: Any) -> None:
        super().__init__()
        self.services = services
        lay = QVBoxLayout(self)

        box = QGroupBox("Phím tắt (phải có Ctrl, Alt hoặc Win)")
        form = QFormLayout(box)
        hk = services.settings.hotkeys
        self.stop_edit = QLineEdit(hk.stop)
        self.debug_edit = QLineEdit(hk.debug_toggle)
        self.pause_edit = QLineEdit(hk.pause)
        self.pause_edit.setPlaceholderText("để trống = không dùng")
        form.addRow("Dừng", self.stop_edit)
        form.addRow("Bật/tắt gỡ lỗi", self.debug_edit)
        form.addRow("Tạm dừng / tiếp tục", self.pause_edit)
        self.apply_btn = QPushButton("Áp dụng")
        self.apply_btn.clicked.connect(self.apply)
        form.addRow(self.apply_btn)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        form.addRow(self.result)
        lay.addWidget(box)

        game = QGroupBox("Trong game")
        gl = QVBoxLayout(game)
        self.gold_box = QCheckBox("Dùng vàng bỏ qua thời gian chờ")
        self.gold_box.setChecked(services.settings.game.spend_gold_skip_wait)
        self.gold_box.setToolTip(
            "Khi game hỏi \"Bỏ qua thời gian chờ tốn … Vàng. Xác nhận?\":\n"
            "bật = bấm 确定 (tốn Vàng, không phải chờ);\n"
            "tắt = bấm 取消 và dừng tính năng đang chạy."
        )
        self.gold_box.toggled.connect(self._set_gold)
        gl.addWidget(self.gold_box)
        gold_note = QLabel(
            "Tắt (mặc định): bot bấm 取消 và dừng. Bật: bot bấm 确定 mỗi lần game hỏi, có thể tốn nhiều "
            "Vàng khi nâng cấp nhiều lần. Bot không bao giờ tích ô \"Lần sau không nhắc nhở\"."
        )
        gold_note.setWordWrap(True)
        gl.addWidget(gold_note)
        self.panels_box = QCheckBox("Thu gọn các bảng che game trước khi chạy")
        self.panels_box.setChecked(services.settings.game.collapse_panels)
        self.panels_box.setToolTip(
            "Trước mỗi tính năng: thu gọn bảng nhiệm vụ bên phải, "
            "khung \"Sắp mở\" và khung chat, để chúng không che nhà và đạo quân bot cần bấm."
        )
        self.panels_box.toggled.connect(self._set_panels)
        gl.addWidget(self.panels_box)
        lay.addWidget(game)

        s = services.settings
        path = services.settings_path or "(không lưu)"
        info = f"Tệp cài đặt: {path}"
        if s.ini_imported_from:
            info += f"\nĐã nhập cài đặt cũ (v1.8.0) từ: {s.ini_imported_from}"
        lbl = QLabel(info)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        lay.addStretch(1)

    def _set_gold(self, on: bool) -> None:
        """Takes effect at once, also for a run in progress (the guard reads the setting each time)."""
        self.services.settings.game.spend_gold_skip_wait = bool(on)
        self.services.save()

    def _set_panels(self, on: bool) -> None:
        """Used by the next run (the context is built when a run starts)."""
        self.services.settings.game.collapse_panels = bool(on)
        self.services.save()

    def apply(self) -> None:
        try:
            hk = HotkeySettings(
                stop=self.stop_edit.text(), debug_toggle=self.debug_edit.text(), pause=self.pause_edit.text(),
            )
        except ValidationError as exc:
            self.result.setText("Không hợp lệ: " + "; ".join(str(e.get("msg", "")) for e in exc.errors()))
            return
        self.services.settings.hotkeys = hk
        self.stop_edit.setText(hk.stop)
        self.debug_edit.setText(hk.debug_toggle)
        self.pause_edit.setText(hk.pause)
        self.services.save()
        errors = self.services.apply_hotkeys()
        self.result.setText("Lỗi: " + "; ".join(errors) if errors else "Đã áp dụng phím tắt")


@register_tab("settings", "Cài đặt", order=90)
def build(services: Any) -> QWidget:
    return SettingsTab(services)
