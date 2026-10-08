"""Notifications on finish / stop / error (docs/06 P3): tray balloon plus optional Telegram bot
and Discord webhook messages with a screenshot.

* ``Notifier.notify`` is called from the UI thread. The tray balloon is shown right away; the
  network sends run on one background thread with a timeout, so the UI never waits for them.
* A failing channel is logged and reported in the returned ``SendReport``; it never raises into
  the caller and never stops the other channels.
* Secrets (bot token, webhook URL) are removed from every logged error text.
* HTTP goes through an injectable ``requests``-like session (tests use a fake one).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from ngoalong.config.settings import NotifySettings
from ngoalong.engine.runner import RunResult, RunStatus

log = logging.getLogger(__name__)

APP_NAME = "AutoNgoaLong"
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_CAPTION_MAX = 1024
TELEGRAM_TEXT_MAX = 4096
DISCORD_CONTENT_MAX = 2000
SCREENSHOT_MAX_WIDTH = 1280

STATUS_TEXT = {RunStatus.DONE: "Hoàn thành", RunStatus.STOPPED: "Đã dừng", RunStatus.FAILED: "Lỗi"}
STATUS_LEVEL = {RunStatus.DONE: "info", RunStatus.STOPPED: "warning", RunStatus.FAILED: "error"}

FrameSource = Callable[[], np.ndarray]
TrayShow = Callable[[str, str, str], None]  # title, message, level


class NotifyError(RuntimeError):
    pass


class HttpSession(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Notification:
    title: str
    message: str
    level: str = "info"  # info | warning | error
    screenshot: Path | None = None  # an existing PNG (the failure screenshot)
    capture: FrameSource | None = None  # else a fresh capture, taken on the sender thread

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.message}"


@dataclass
class SendReport:
    """Per-channel outcome of one notification: ``None`` = sent, else the error text."""

    results: dict[str, str | None] = field(default_factory=dict)
    image_attached: bool = False

    @property
    def ok(self) -> bool:
        return all(v is None for v in self.results.values())

    def errors(self) -> dict[str, str]:
        return {k: v for k, v in self.results.items() if v is not None}


def should_notify(settings: NotifySettings, status: RunStatus) -> bool:
    return {
        RunStatus.DONE: settings.on_done,
        RunStatus.STOPPED: settings.on_stopped,
        RunStatus.FAILED: settings.on_failed,
    }.get(status, False)


def notification_for(
    result: RunResult, task_title: str, window_label: str = "", capture: FrameSource | None = None
) -> Notification:
    status = STATUS_TEXT.get(result.status, str(result.status))
    title = f"{APP_NAME} - {task_title}: {status}"
    lines = [result.message]
    if window_label:
        lines.append(f"Cửa sổ: {window_label}")
    if result.duration_s:
        m, s = divmod(int(result.duration_s), 60)
        lines.append(f"Thời gian chạy: {m} phút {s} giây" if m else f"Thời gian chạy: {s} giây")
    shot = result.screenshot if result.screenshot is not None and result.screenshot.is_file() else None
    return Notification(title, "\n".join(lines), STATUS_LEVEL.get(result.status, "info"), shot, capture)


def encode_png(frame: np.ndarray, max_width: int = SCREENSHOT_MAX_WIDTH) -> bytes:
    """PNG bytes of a BGR frame, scaled down to ``max_width`` when wider."""
    h, w = frame.shape[:2]
    if w > max_width:
        frame = cv2.resize(frame, (max_width, max(1, round(h * max_width / w))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise NotifyError("không mã hoá được ảnh PNG")
    return buf.tobytes()


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- channels -----------------------------------------------------------------------------------
class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, session: HttpSession, timeout_s: float = 15.0) -> None:
        self.token = token
        self.chat_id = chat_id
        self.session = session
        self.timeout_s = timeout_s

    def _post(self, method: str, **kwargs: Any) -> None:
        url = f"{TELEGRAM_API}/bot{self.token}/{method}"
        try:
            resp = self.session.post(url, timeout=self.timeout_s, **kwargs)
        except Exception as exc:  # requests.RequestException and friends
            raise NotifyError(_redact(f"không gửi được tới Telegram: {exc}", (self.token,))) from None
        status = getattr(resp, "status_code", 0)
        try:
            body = resp.json()
        except Exception:
            body = {}
        if status != 200 or not (isinstance(body, dict) and body.get("ok", False)):
            desc = body.get("description", "") if isinstance(body, dict) else ""
            raise NotifyError(_redact(f"Telegram trả lỗi {status}: {desc}".rstrip(": "), (self.token,)))

    def send(self, text: str, image_png: bytes | None) -> None:
        if image_png is None:
            self._post("sendMessage", data={"chat_id": self.chat_id, "text": _clip(text, TELEGRAM_TEXT_MAX)})
            return
        caption = text
        if len(text) > TELEGRAM_CAPTION_MAX:  # a long text goes first as a message
            self._post("sendMessage", data={"chat_id": self.chat_id, "text": _clip(text, TELEGRAM_TEXT_MAX)})
            caption = ""
        data = {"chat_id": self.chat_id}
        if caption:
            data["caption"] = caption
        self._post("sendPhoto", data=data, files={"photo": ("screenshot.png", image_png, "image/png")})


class DiscordChannel:
    name = "discord"

    def __init__(self, webhook_url: str, session: HttpSession, timeout_s: float = 15.0) -> None:
        self.webhook_url = webhook_url
        self.session = session
        self.timeout_s = timeout_s

    def send(self, text: str, image_png: bytes | None) -> None:
        payload = {"content": _clip(text, DISCORD_CONTENT_MAX), "username": APP_NAME}
        kwargs: dict[str, Any]
        if image_png is None:
            kwargs = {"json": payload}
        else:
            kwargs = {
                "data": {"payload_json": json.dumps(payload, ensure_ascii=False)},
                "files": {"files[0]": ("screenshot.png", image_png, "image/png")},
            }
        try:
            resp = self.session.post(self.webhook_url, timeout=self.timeout_s, **kwargs)
        except Exception as exc:
            raise NotifyError(_redact(f"không gửi được tới Discord: {exc}", (self.webhook_url,))) from None
        status = getattr(resp, "status_code", 0)
        if status not in (200, 204):
            detail = ""
            try:
                body = resp.json()
                detail = str(body.get("message", "")) if isinstance(body, dict) else ""
            except Exception:
                pass
            raise NotifyError(_redact(f"Discord trả lỗi {status}: {detail}".rstrip(": "), (self.webhook_url,)))


Channel = TelegramChannel | DiscordChannel


def remote_channels(settings: NotifySettings, session: HttpSession) -> list[Channel]:
    chans: list[Channel] = []
    if settings.telegram.ready:
        chans.append(TelegramChannel(settings.telegram.bot_token, settings.telegram.chat_id, session,
                                     settings.timeout_s))
    if settings.discord.ready:
        chans.append(DiscordChannel(settings.discord.webhook_url, session, settings.timeout_s))
    return chans


# --- tray ---------------------------------------------------------------------------------------
class QtTray:
    """A lazily created ``QSystemTrayIcon``; call ``show`` from the UI thread only."""

    def __init__(self) -> None:
        self._icon: Any = None

    def available(self) -> bool:
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        return QApplication.instance() is not None and QSystemTrayIcon.isSystemTrayAvailable()

    def show(self, title: str, message: str, level: str = "info") -> None:
        from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

        if not self.available():
            log.info("tray not available: %s - %s", title, message)
            return
        if self._icon is None:
            app = QApplication.instance()
            icon = app.windowIcon()  # type: ignore[union-attr]
            if icon.isNull():
                icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)  # type: ignore[union-attr]
            self._icon = QSystemTrayIcon(icon)
            self._icon.setToolTip(APP_NAME)
            self._icon.show()
        kind = {"error": QSystemTrayIcon.MessageIcon.Critical,
                "warning": QSystemTrayIcon.MessageIcon.Warning}.get(level, QSystemTrayIcon.MessageIcon.Information)
        self._icon.showMessage(title, message, kind, 8000)

    def close(self) -> None:
        if self._icon is not None:
            self._icon.hide()
            self._icon.deleteLater()
            self._icon = None


# --- notifier -----------------------------------------------------------------------------------
class Notifier:
    def __init__(
        self,
        settings: Callable[[], NotifySettings],
        session: HttpSession | None = None,
        tray: TrayShow | None = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._tray = tray
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._closed = False

    def set_tray(self, tray: TrayShow | None) -> None:
        self._tray = tray

    @property
    def session(self) -> HttpSession:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _submit(self, fn: Callable[..., SendReport], *args: Any) -> Future[SendReport] | None:
        with self._lock:
            if self._closed:
                return None
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notify")
            return self._executor.submit(fn, *args)

    # --- public ---------------------------------------------------------------------------------
    def notify(self, n: Notification) -> Future[SendReport] | None:
        """Tray now; Telegram / Discord in the background. Returns the background future (or None)."""
        s = self._settings().model_copy(deep=True)
        if s.tray and self._tray is not None:
            try:
                self._tray(n.title, n.message, n.level)
            except Exception:
                log.exception("tray notification failed")
        chans = remote_channels(s, self.session) if (s.telegram.ready or s.discord.ready) else []
        if not chans:
            return None
        return self._submit(self._send, n, chans, s.screenshot)

    def notify_result(
        self, result: RunResult, task_title: str, window_label: str = "", capture: FrameSource | None = None
    ) -> Future[SendReport] | None:
        if not should_notify(self._settings(), result.status):
            return None
        return self.notify(notification_for(result, task_title, window_label, capture))

    def send_test(self, settings: NotifySettings | None = None) -> Future[SendReport] | None:
        """"Gửi thử": a text-only message through the configured remote channels."""
        s = (settings or self._settings()).model_copy(deep=True)
        chans = remote_channels(s, self.session)
        if not chans:
            return None
        n = Notification(f"{APP_NAME} - Gửi thử", "Thông báo đã được cấu hình đúng.")
        return self._submit(self._send, n, chans, False)

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
            ex, self._executor = self._executor, None
        if ex is not None:
            ex.shutdown(wait=wait, cancel_futures=True)

    # --- sender thread --------------------------------------------------------------------------
    @staticmethod
    def _image(n: Notification) -> bytes | None:
        try:
            if n.screenshot is not None and n.screenshot.is_file():
                from ngoalong.core.capture import read_image

                return encode_png(read_image(n.screenshot))
            if n.capture is not None:
                return encode_png(n.capture())
        except Exception as exc:
            log.warning("notification screenshot unavailable: %s", exc)
        return None

    def _send(self, n: Notification, chans: list[Channel], with_image: bool) -> SendReport:
        report = SendReport()
        image = self._image(n) if with_image else None
        report.image_attached = image is not None
        for ch in chans:
            try:
                ch.send(n.text, image)
                report.results[ch.name] = None
            except NotifyError as exc:
                report.results[ch.name] = str(exc)
                log.warning("notification via %s failed: %s", ch.name, exc)
            except Exception as exc:  # a bug in a channel must not stop the others
                report.results[ch.name] = f"{type(exc).__name__}"
                # no traceback: its frames could show the token / webhook URL
                log.warning("notification via %s crashed: %s", ch.name, type(exc).__name__)
        return report
