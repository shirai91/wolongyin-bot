"""Read-only log view with a time stamp and a colour per level."""

from __future__ import annotations

import html
import time

from PySide6.QtWidgets import QPlainTextEdit, QWidget

_COLORS = {"error": "#d32f2f", "warning": "#ef6c00", "debug": "#808080"}


class LogView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None, max_lines: int = 5000) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setPlaceholderText("Nhật ký hoạt động")

    def append_message(self, level: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        text = html.escape(f"{stamp}  {message}")
        color = _COLORS.get(level.lower())
        self.appendHtml(f'<span style="color:{color}">{text}</span>' if color else text)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
