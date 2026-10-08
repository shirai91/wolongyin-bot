""""Thông báo" tab: when to notify (finish / stop / error), tray balloon, Telegram bot and
Discord webhook, with a "Gửi thử" (send a test message) button."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ngoalong.config.settings import DiscordSettings, NotifySettings, TelegramSettings
from ngoalong.notify import SendReport
from ngoalong.ui.tabs import register_tab

CHANNEL_TEXT = {"telegram": "Telegram", "discord": "Discord"}


def report_text(report: SendReport) -> str:
    if not report.results:
        return "Không có kênh nào được gửi"
    parts = [f"{CHANNEL_TEXT.get(k, k)}: {'đã gửi' if v is None else 'lỗi - ' + v}" for k, v in report.results.items()]
    return "; ".join(parts)


class NotifyTab(QWidget):
    test_done = Signal(object)  # SendReport | str (error)

    def __init__(self, services: Any) -> None:
        super().__init__()
        self.services = services
        s: NotifySettings = services.settings.notify
        lay = QVBoxLayout(self)

        when = QGroupBox("Gửi thông báo khi")
        wl = QHBoxLayout(when)
        self.on_done = QCheckBox("Hoàn thành")
        self.on_stopped = QCheckBox("Bị dừng")
        self.on_failed = QCheckBox("Gặp lỗi")
        for cb, v in ((self.on_done, s.on_done), (self.on_stopped, s.on_stopped), (self.on_failed, s.on_failed)):
            cb.setChecked(v)
            wl.addWidget(cb)
        wl.addStretch(1)
        lay.addWidget(when)

        general = QGroupBox("Chung")
        gl = QVBoxLayout(general)
        self.tray = QCheckBox("Hiện thông báo ở khay hệ thống (góc màn hình)")
        self.tray.setChecked(s.tray)
        self.screenshot = QCheckBox("Đính kèm ảnh chụp màn hình game (Telegram / Discord)")
        self.screenshot.setChecked(s.screenshot)
        gl.addWidget(self.tray)
        gl.addWidget(self.screenshot)
        lay.addWidget(general)

        tg = QGroupBox("Telegram")
        tf = QFormLayout(tg)
        self.tg_enabled = QCheckBox("Bật")
        self.tg_enabled.setChecked(s.telegram.enabled)
        self.tg_token = QLineEdit(s.telegram.bot_token)
        self.tg_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.tg_token.setPlaceholderText("123456789:AA... (lấy từ @BotFather)")
        self.tg_chat = QLineEdit(s.telegram.chat_id)
        self.tg_chat.setPlaceholderText("số chat id hoặc @tên_kênh")
        tf.addRow(self.tg_enabled)
        tf.addRow("Mã bot", self.tg_token)
        tf.addRow("Chat id", self.tg_chat)
        lay.addWidget(tg)

        dc = QGroupBox("Discord")
        df = QFormLayout(dc)
        self.dc_enabled = QCheckBox("Bật")
        self.dc_enabled.setChecked(s.discord.enabled)
        self.dc_url = QLineEdit(s.discord.webhook_url)
        self.dc_url.setEchoMode(QLineEdit.EchoMode.Password)
        self.dc_url.setPlaceholderText("https://discord.com/api/webhooks/...")
        df.addRow(self.dc_enabled)
        df.addRow("Webhook", self.dc_url)
        lay.addWidget(dc)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Lưu")
        self.test_btn = QPushButton("Gửi thử")
        self.save_btn.clicked.connect(self.apply)
        self.test_btn.clicked.connect(self.send_test)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        note = QLabel("Mã bot và webhook được lưu trong settings.toml trên máy này; đừng chia sẻ tệp đó.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        lay.addStretch(1)
        self.test_done.connect(self._on_test_done)

    # --- settings -------------------------------------------------------------------------------
    def read_form(self) -> NotifySettings:
        """The settings as typed; raises ``ValidationError`` with Vietnamese messages."""
        return NotifySettings(
            tray=self.tray.isChecked(),
            on_done=self.on_done.isChecked(),
            on_stopped=self.on_stopped.isChecked(),
            on_failed=self.on_failed.isChecked(),
            screenshot=self.screenshot.isChecked(),
            timeout_s=self.services.settings.notify.timeout_s,
            telegram=TelegramSettings(enabled=self.tg_enabled.isChecked(), bot_token=self.tg_token.text(),
                                      chat_id=self.tg_chat.text()),
            discord=DiscordSettings(enabled=self.dc_enabled.isChecked(), webhook_url=self.dc_url.text()),
        )

    @staticmethod
    def _errors(exc: ValidationError) -> str:
        return "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())

    def apply(self) -> bool:
        try:
            s = self.read_form()
        except ValidationError as exc:
            self.result.setText("Không hợp lệ: " + self._errors(exc))
            return False
        if s.telegram.enabled and not s.telegram.ready:
            self.result.setText("Telegram: cần nhập cả mã bot và chat id")
            return False
        if s.discord.enabled and not s.discord.ready:
            self.result.setText("Discord: cần nhập địa chỉ webhook")
            return False
        self.services.settings.notify = s
        self.services.save()
        self.result.setText("Đã lưu cài đặt thông báo")
        return True

    # --- test -----------------------------------------------------------------------------------
    def send_test(self) -> Future[SendReport] | None:
        if not self.apply():
            return None
        fut = self.services.notifier.send_test()
        if fut is None:
            self.result.setText("Chưa bật kênh Telegram hoặc Discord nào")
            return None
        self.test_btn.setEnabled(False)
        self.result.setText("Đang gửi thử...")
        fut.add_done_callback(self._future_done)  # runs on the sender thread
        return fut

    def _future_done(self, fut: Future[SendReport]) -> None:
        try:
            payload: object = fut.result()
        except Exception as exc:  # cancelled at shutdown, or a bug
            payload = type(exc).__name__
        self.test_done.emit(payload)  # queued to the UI thread

    def _on_test_done(self, payload: object) -> None:
        self.test_btn.setEnabled(True)
        if isinstance(payload, SendReport):
            self.result.setText(report_text(payload))
        else:
            self.result.setText(f"Gửi thử thất bại: {payload}")


@register_tab("notify", "Thông báo", order=80)
def build(services: Any) -> QWidget:
    return NotifyTab(services)
