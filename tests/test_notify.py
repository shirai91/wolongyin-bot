"""Notifications: Telegram / Discord over a fake HTTP session, tray callback, settings and the tab."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from conftest import PROFILE
from ngoalong.config.settings import (
    DiscordSettings, NotifySettings, Settings, TelegramSettings, load_settings, save_settings,
)
from ngoalong.core.capture import save_png
from ngoalong.engine.runner import RunResult, RunStatus
from ngoalong.notify import (
    DiscordChannel, Notification, Notifier, NotifyError, TelegramChannel, notification_for, should_notify,
)

TOKEN = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ"
WEBHOOK = "https://discord.com/api/webhooks/111222333/abcDEF-ghiJKL"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records every POST; answers from a per-host table (default: success)."""

    def __init__(self, telegram: FakeResponse | Exception | None = None,
                 discord: FakeResponse | Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.telegram = telegram or FakeResponse(200, {"ok": True})
        self.discord = discord or FakeResponse(204)

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        answer = self.telegram if "api.telegram.org" in url else self.discord
        if isinstance(answer, Exception):
            raise answer
        return answer

    def urls(self) -> list[str]:
        return [c["url"] for c in self.calls]


def enabled_settings(**kw: Any) -> NotifySettings:
    return NotifySettings(
        telegram=TelegramSettings(enabled=True, bot_token=TOKEN, chat_id="-100123"),
        discord=DiscordSettings(enabled=True, webhook_url=WEBHOOK),
        **kw,
    )


def frame() -> np.ndarray:
    img = np.zeros((600, 1000, 3), np.uint8)
    img[100:200, 100:300] = (0, 0, 255)
    return img


def result(status: RunStatus = RunStatus.DONE, **kw: Any) -> RunResult:
    return RunResult("quay_ruong", status, kw.pop("message", "Đã quay xong 5 lượt"), duration_s=75.0, **kw)


# --- channels -----------------------------------------------------------------------------------
def test_telegram_photo_with_caption() -> None:
    s = FakeSession()
    TelegramChannel(TOKEN, "-100123", s, timeout_s=7).send("Xong\nCửa sổ: A", b"PNGDATA")
    (call,) = s.calls
    assert call["url"] == f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    assert call["data"] == {"chat_id": "-100123", "caption": "Xong\nCửa sổ: A"}
    assert call["files"]["photo"] == ("screenshot.png", b"PNGDATA", "image/png")
    assert call["timeout"] == 7


def test_telegram_text_only_and_long_caption() -> None:
    s = FakeSession()
    ch = TelegramChannel(TOKEN, "@kenh_cua_toi", s)
    ch.send("chỉ chữ", None)
    assert s.calls[0]["url"].endswith("/sendMessage") and s.calls[0]["data"]["text"] == "chỉ chữ"
    s.calls.clear()
    ch.send("x" * 2000, b"IMG")  # caption limit 1024: text first, then the photo
    assert [u.rsplit("/", 1)[1] for u in s.urls()] == ["sendMessage", "sendPhoto"]
    assert "caption" not in s.calls[1]["data"]


def test_telegram_errors_never_leak_the_token() -> None:
    s = FakeSession(telegram=FakeResponse(401, {"ok": False, "description": "Unauthorized"}))
    with pytest.raises(NotifyError) as e:
        TelegramChannel(TOKEN, "1", s).send("a", None)
    assert "401" in str(e.value) and "Unauthorized" in str(e.value) and TOKEN not in str(e.value)

    boom = ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: /bot{TOKEN}/sendMessage")
    with pytest.raises(NotifyError) as e:
        TelegramChannel(TOKEN, "1", FakeSession(telegram=boom)).send("a", None)
    assert TOKEN not in str(e.value) and "***" in str(e.value)

    # HTTP 200 with ok=false is still an error
    with pytest.raises(NotifyError):
        TelegramChannel(TOKEN, "1", FakeSession(telegram=FakeResponse(200, {"ok": False}))).send("a", None)


def test_discord_webhook_with_and_without_image() -> None:
    s = FakeSession()
    ch = DiscordChannel(WEBHOOK, s, timeout_s=5)
    ch.send("Lỗi: hết thời gian", b"IMG")
    call = s.calls[0]
    assert call["url"] == WEBHOOK and call["timeout"] == 5
    payload = json.loads(call["data"]["payload_json"])
    assert payload["content"] == "Lỗi: hết thời gian"
    assert call["files"]["files[0]"] == ("screenshot.png", b"IMG", "image/png")
    ch.send("y" * 3000, None)
    assert s.calls[1]["json"]["content"].endswith("…") and len(s.calls[1]["json"]["content"]) == 2000


def test_discord_error_hides_the_webhook() -> None:
    s = FakeSession(discord=FakeResponse(404, {"message": "Unknown Webhook"}))
    with pytest.raises(NotifyError) as e:
        DiscordChannel(WEBHOOK, s).send("a", None)
    assert "404" in str(e.value) and "Unknown Webhook" in str(e.value)
    with pytest.raises(NotifyError) as e:
        DiscordChannel(WEBHOOK, FakeSession(discord=OSError(f"cannot reach {WEBHOOK}"))).send("a", None)
    assert WEBHOOK not in str(e.value)


# --- notifier -----------------------------------------------------------------------------------
def test_notification_text() -> None:
    n = notification_for(result(), "Quay rương", "Sv 266 Ngọa Long Việt Nam")
    assert n.title == "AutoNgoaLong - Quay rương: Hoàn thành" and n.level == "info"
    assert "Đã quay xong 5 lượt" in n.message and "Cửa sổ: Sv 266" in n.message
    assert "1 phút 15 giây" in n.message
    assert notification_for(result(RunStatus.FAILED), "X").level == "error"
    assert notification_for(result(RunStatus.STOPPED), "X").title.endswith("Đã dừng")


def test_should_notify_follows_the_event_switches() -> None:
    s = NotifySettings(on_done=False, on_stopped=True, on_failed=True)
    assert not should_notify(s, RunStatus.DONE)
    assert should_notify(s, RunStatus.STOPPED) and should_notify(s, RunStatus.FAILED)


def test_notify_result_sends_both_channels_with_a_capture() -> None:
    s = FakeSession()
    tray: list[tuple[str, str, str]] = []
    captured: list[int] = []

    def capture() -> np.ndarray:
        captured.append(1)
        return frame()

    n = Notifier(lambda: enabled_settings(), session=s, tray=lambda *a: tray.append(a))
    try:
        fut = n.notify_result(result(), "Quay rương", "Cửa sổ A", capture)
        assert fut is not None
        report = fut.result(timeout=5)
    finally:
        n.shutdown(wait=True)
    assert report.ok and report.image_attached and set(report.results) == {"telegram", "discord"}
    assert captured == [1]  # one capture shared by both channels
    assert tray and tray[0][0].endswith("Hoàn thành") and tray[0][2] == "info"
    tg = next(c for c in s.calls if "telegram" in c["url"])
    assert tg["files"]["photo"][1].startswith(PNG_MAGIC)
    dc = next(c for c in s.calls if c["url"] == WEBHOOK)
    assert dc["files"]["files[0]"][1].startswith(PNG_MAGIC)


def test_failure_screenshot_file_is_preferred(tmp_path: Path) -> None:
    shot = save_png(tmp_path / "fail.png", frame())
    s = FakeSession()
    n = Notifier(lambda: enabled_settings(), session=s)
    called: list[int] = []
    try:
        fut = n.notify_result(result(RunStatus.FAILED, screenshot=shot, message="Lỗi: hết giờ"), "T", "",
                              lambda: called.append(1) or frame())
        report = fut.result(timeout=5)  # type: ignore[union-attr]
    finally:
        n.shutdown(wait=True)
    assert report.image_attached and not called


def test_one_failing_channel_does_not_stop_the_other() -> None:
    s = FakeSession(telegram=FakeResponse(500, {"ok": False, "description": "boom"}))
    n = Notifier(lambda: enabled_settings(screenshot=False), session=s)
    try:
        report = n.notify(Notification("t", "m")).result(timeout=5)  # type: ignore[union-attr]
    finally:
        n.shutdown(wait=True)
    assert not report.ok and "500" in report.errors()["telegram"] and report.results["discord"] is None
    assert not report.image_attached and "files" not in s.calls[1]


def test_capture_error_still_sends_text() -> None:
    def bad_capture() -> np.ndarray:
        raise RuntimeError("cửa sổ đã đóng")

    s = FakeSession()
    n = Notifier(lambda: enabled_settings(), session=s)
    try:
        report = n.notify(Notification("t", "m", capture=bad_capture)).result(timeout=5)  # type: ignore[union-attr]
    finally:
        n.shutdown(wait=True)
    assert report.ok and not report.image_attached
    assert all("files" not in c for c in s.calls)


def test_disabled_events_and_channels_send_nothing() -> None:
    s = FakeSession()
    tray: list[Any] = []
    n = Notifier(lambda: NotifySettings(tray=False), session=s, tray=lambda *a: tray.append(a))
    assert n.notify_result(result(), "T") is None  # no remote channel configured
    n2 = Notifier(lambda: enabled_settings(on_done=False), session=s, tray=lambda *a: tray.append(a))
    assert n2.notify_result(result(), "T") is None
    assert not s.calls and not tray
    n.shutdown()
    n2.shutdown()


def test_send_test_and_shutdown() -> None:
    s = FakeSession()
    n = Notifier(lambda: enabled_settings(), session=s)
    report = n.send_test().result(timeout=5)  # type: ignore[union-attr]
    assert report.ok and not report.image_attached and len(s.calls) == 2
    n.shutdown(wait=True)
    assert n.notify(Notification("t", "m")) is None  # closed: no new background work


# --- settings -----------------------------------------------------------------------------------
def test_settings_validation() -> None:
    with pytest.raises(ValidationError):
        TelegramSettings(bot_token="not-a-token")
    with pytest.raises(ValidationError):
        TelegramSettings(chat_id="abc")
    with pytest.raises(ValidationError):
        DiscordSettings(webhook_url="https://evil.example.com/api/webhooks/1/x")
    with pytest.raises(ValidationError):
        DiscordSettings(webhook_url="http://discord.com/api/webhooks/1/x")
    assert DiscordSettings(webhook_url=f"  {WEBHOOK} ").webhook_url == WEBHOOK
    assert not TelegramSettings(enabled=True, bot_token=TOKEN).ready  # chat id missing


def test_settings_roundtrip_and_broken_section(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    s = Settings()
    s.notify = enabled_settings(on_stopped=False)
    s.multi_window.assignments = {"Sv 266 Ngọa Long": "quay_ruong"}
    save_settings(s, p)
    back = load_settings(p)
    assert back.notify.telegram.bot_token == TOKEN and not back.notify.on_stopped
    assert back.multi_window.assignments == {"Sv 266 Ngọa Long": "quay_ruong"}

    text = p.read_text(encoding="utf-8").replace(WEBHOOK, "https://evil.example.com/x")
    p.write_text(text, encoding="utf-8")
    broken = load_settings(p)  # only the broken section is reset; the file is not discarded
    assert broken.notify == NotifySettings()
    assert broken.multi_window.assignments == {"Sv 266 Ngọa Long": "quay_ruong"}
    assert p.is_file()


# --- UI tab -------------------------------------------------------------------------------------
@pytest.fixture
def services(qapp, tmp_path: Path):
    from ngoalong.app import AppServices

    s = AppServices(settings_path=tmp_path / "settings.toml", profile_dir=PROFILE, discover=lambda: [],
                    log_dir=tmp_path / "logs", rebind_windows=False)
    yield s
    s.shutdown()


def test_notify_tab_saves_and_sends_a_test(qapp, services) -> None:
    from ngoalong.ui.tabs.notify_tab import NotifyTab

    fake = FakeSession()
    services.notifier._session = fake
    tab = NotifyTab(services)
    tab.tg_enabled.setChecked(True)
    tab.tg_token.setText("sai")
    assert not tab.apply() and "Không hợp lệ" in tab.result.text()
    tab.tg_token.setText(TOKEN)
    tab.tg_chat.setText("")
    assert not tab.apply() and "chat id" in tab.result.text()
    tab.tg_chat.setText("987654")
    tab.on_stopped.setChecked(False)
    assert tab.apply()
    assert services.settings.notify.telegram.ready and not services.settings.notify.on_stopped
    assert load_settings(services.settings_path).notify.telegram.chat_id == "987654"

    assert tab.send_test() is not None
    deadline = time.monotonic() + 5
    while "Telegram: đã gửi" not in tab.result.text() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert tab.result.text() == "Telegram: đã gửi" and tab.test_btn.isEnabled()
    assert fake.calls[0]["data"]["chat_id"] == "987654"


def test_notify_tab_without_channels(qapp, services) -> None:
    from ngoalong.ui.tabs.notify_tab import NotifyTab

    tab = NotifyTab(services)
    assert tab.send_test() is None and "Chưa bật kênh" in tab.result.text()
