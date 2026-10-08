"""Multi-window: RunnerManager slots, parallel runs, one task per window, the "Nhiều cửa sổ" tab,
and notifications sent when a window's run ends. Offline: FileCapturer + recording input drivers."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from conftest import PROFILE, ROOT
from ngoalong.config.settings import DiscordSettings, NotifySettings, TelegramSettings
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.errors import WindowNotFoundError
from ngoalong.core.window import GameWindow, WindowInfo
from ngoalong.engine.context import RunContext
from ngoalong.engine.runner import RunStatus
from ngoalong.engine.task import Task

CHEST = ROOT / "docs" / "game screenshots" / "chest-rolling-ui.png"
EXE = r"C:\Users\x\AppData\Local\Coowon\Coowon\Application\chrome.exe"
TOKEN = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ"


def win(hwnd: int, title: str, pid: int = 4242) -> GameWindow:
    return GameWindow(WindowInfo(hwnd, pid, EXE, "Chrome_WidgetWin_1", title))


WIN_A = win(0x1234, "Sv 266 Ngọa Long Việt Nam - Coowon Browser")
WIN_B = win(0x5678, "Sv 300 Ngọa Long Việt Nam - Coowon Browser")


class _Loop(Task):
    """Sleeps in small cancellable steps until stopped."""

    id = "test_loop"
    title = "Vòng lặp thử"

    def run(self, ctx: RunContext) -> str | None:
        for _ in range(2000):
            ctx.sleep(10)
        return "hết vòng"


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kw: Any) -> Any:
        self.calls.append({"url": url, **kw})

        class R:
            status_code = 200

            @staticmethod
            def json() -> Any:
                return {"ok": True}

        return R()


@pytest.fixture
def env(qapp, tmp_path: Path):
    from ngoalong.app import AppServices

    windows = {"list": [WIN_A, WIN_B]}
    drivers: dict[int, RecordingInputDriver] = {}

    def input_factory(gw: GameWindow) -> RecordingInputDriver:
        return drivers.setdefault(gw.hwnd, RecordingInputDriver())

    s = AppServices(
        settings_path=tmp_path / "settings.toml",
        profile_dir=PROFILE,
        discover=lambda: list(windows["list"]),
        capturer_factory=lambda gw: FileCapturer([CHEST], loop=True),
        input_factory=input_factory,
        log_dir=tmp_path / "logs",
        rebind_windows=False,
    )
    # static frames cannot react to the panel clicks done before each run (tests/test_panels.py)
    s.settings.game.collapse_panels = False
    yield s, windows, drivers
    s.shutdown()


def wait_until(qapp, cond, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return cond()


def quay_ruong(spins: int = 2) -> Task:
    from ngoalong.features.quay_ruong import QuayRuong

    return QuayRuong({"spins": spins, "delay_ms": 0})


# --- manager ------------------------------------------------------------------------------------
def test_sync_adds_keeps_and_drops_slots(qapp, env) -> None:
    s, windows, _ = env
    slots = s.sync_windows()
    assert [x.title for x in slots] == [WIN_A.title, WIN_B.title]
    a, b = slots
    assert a.runner is not b.runner and a.locator is not b.locator
    assert a.window is not WIN_A  # private copy, rebinding one never moves another
    assert s.sync_windows()[0] is a  # same window -> same slot

    # B vanishes while running: kept (marked closed) until its run ends, then dropped
    s.manager.start(b.key, _Loop())
    windows["list"] = [WIN_A]
    slots = s.sync_windows()
    assert len(slots) == 2 and not b.present and a.present
    with pytest.raises(WindowNotFoundError):
        s.manager.start(b.key, _Loop())
    s.manager.stop(b.key)
    assert wait_until(qapp, lambda: not b.is_running())
    assert [x.key for x in s.sync_windows()] == [a.key]


def test_two_windows_run_in_parallel(qapp, env, tmp_path: Path) -> None:
    s, _, drivers = env
    a, b = s.sync_windows()
    finished: dict[str, Any] = {}
    s.manager.slot_finished.connect(lambda key, r: finished.__setitem__(key, r))
    s.manager.start(a.key, quay_ruong(2))
    s.manager.start(b.key, quay_ruong(3))
    assert wait_until(qapp, lambda: len(finished) == 2)
    assert finished[a.key].status is RunStatus.DONE and finished[b.key].status is RunStatus.DONE
    assert len(drivers[WIN_A.hwnd].clicks) == 6 and len(drivers[WIN_B.hwnd].clicks) == 9
    dirs = {finished[a.key].run_dir, finished[b.key].run_dir}
    assert len(dirs) == 2 and all(d is not None and d.parent == tmp_path / "logs" for d in dirs)
    assert a.locator.last_good is not None and b.locator.last_good is not None


def test_one_task_per_window(qapp, env) -> None:
    s, _, _ = env
    a, b = s.sync_windows()
    s.bind(WIN_A)
    # primary ("Tính năng") runner busy on A: the manager refuses A, accepts B
    s.runner.start(_Loop(), s.make_context)
    with pytest.raises(RuntimeError, match="thẻ Tính năng"):
        s.manager.start(a.key, _Loop())
    s.manager.start(b.key, _Loop())
    with pytest.raises(RuntimeError, match="tác vụ khác"):
        s.manager.start(b.key, _Loop())
    s.runner.stop()
    assert wait_until(qapp, lambda: not s.runner.is_running())
    # a slot busy on A: the primary runner refuses A
    s.manager.start(a.key, _Loop())
    with pytest.raises(RuntimeError, match="Nhiều cửa sổ"):
        s.runner.start(_Loop(), s.make_context)
    s.manager.stop_all()
    assert wait_until(qapp, lambda: not s.manager.any_running())


def test_stop_and_pause_hotkeys_reach_every_window(qapp, env) -> None:
    s, _, _ = env
    a, b = s.sync_windows()
    s.manager.start(a.key, _Loop())
    s.manager.start(b.key, _Loop())
    s.hotkey_bridge.pause_requested.emit()
    assert wait_until(qapp, lambda: a.state == "paused" and b.state == "paused")
    s.hotkey_bridge.pause_requested.emit()
    assert wait_until(qapp, lambda: a.state == "running" and b.state == "running")
    t0 = time.monotonic()
    s.hotkey_bridge.stop_requested.emit()
    assert wait_until(qapp, lambda: not s.manager.any_running(), timeout_s=3)
    assert time.monotonic() - t0 < 2.0
    assert a.runner.last_result.status is RunStatus.STOPPED


def test_slot_run_sends_a_notification(qapp, env) -> None:
    s, _, _ = env
    fake = FakeSession()
    s.notifier._session = fake
    s.settings.notify = NotifySettings(
        telegram=TelegramSettings(enabled=True, bot_token=TOKEN, chat_id="42"),
        discord=DiscordSettings(enabled=False),
    )
    tray: list[tuple[str, str, str]] = []
    s.notifier.set_tray(lambda *a: tray.append(a))
    _, b = s.sync_windows()
    s.manager.start(b.key, quay_ruong(1))
    assert wait_until(qapp, lambda: bool(fake.calls))
    call = fake.calls[0]
    assert call["url"].endswith("/sendPhoto")
    assert "Quay rương" in call["data"]["caption"] and WIN_B.title in call["data"]["caption"]
    assert call["files"]["photo"][1].startswith(b"\x89PNG")
    assert tray and "Hoàn thành" in tray[0][0]


def test_primary_run_notifies_and_respects_switches(qapp, env) -> None:
    s, _, _ = env
    fake = FakeSession()
    s.notifier._session = fake
    s.settings.notify = NotifySettings(
        on_stopped=False, screenshot=False,
        telegram=TelegramSettings(enabled=True, bot_token=TOKEN, chat_id="42"),
    )
    s.bind(WIN_A)
    s.runner.start(quay_ruong(1), s.make_context)
    assert wait_until(qapp, lambda: bool(fake.calls))
    assert fake.calls[0]["url"].endswith("/sendMessage") and WIN_A.title in fake.calls[0]["data"]["text"]

    fake.calls.clear()
    s.runner.start(_Loop(), s.make_context)  # stopped runs are switched off: nothing is sent
    s.runner.stop()
    assert wait_until(qapp, lambda: not s.runner.is_running())
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert fake.calls == []


# --- UI tab -------------------------------------------------------------------------------------
def test_windows_tab_assign_and_start_all(qapp, env) -> None:
    from ngoalong.ui.tabs.windows import WindowsTab

    s, windows, drivers = env
    tab = WindowsTab(s)
    assert tab.table.rowCount() == 2 and set(tab.rows) == {x.key for x in s.manager.slots()}
    a, b = s.manager.slots()
    assert not tab.start(a.key) and "Chưa chọn tính năng" in tab.log_view.toPlainText()

    s.settings.set_feature_params("quay_ruong", quay_ruong(2).params)
    tab.set_feature(a.key, "quay_ruong")
    tab.set_feature(b.key, "quay_ruong")
    assert s.settings.multi_window.assignments == {WIN_A.title: "quay_ruong", WIN_B.title: "quay_ruong"}
    assert tab.start_all() == 2
    assert not tab.rows[a.key].start_btn.isEnabled() and not tab.rows[a.key].combo.isEnabled()
    assert wait_until(qapp, lambda: not s.manager.any_running() and "Xong" in tab.rows[b.key].state.text())
    assert len(drivers[WIN_A.hwnd].clicks) == 6 and len(drivers[WIN_B.hwnd].clicks) == 6
    assert tab.rows[a.key].start_btn.isEnabled()
    text = tab.log_view.toPlainText()
    assert f"[{WIN_A.title}]" in text and f"[{WIN_B.title}]" in text and "Đã quay xong" in text

    # assignments survive a rediscovery (saved per window title)
    windows["list"] = [WIN_B]
    tab.refresh()
    assert tab.table.rowCount() == 1
    assert tab.selected_feature(s.manager.slots()[0].key) == "quay_ruong"


def test_windows_tab_registered_and_empty(qapp, tmp_path: Path) -> None:
    from ngoalong.app import AppServices
    from ngoalong.ui.main_window import MainWindow

    s = AppServices(settings_path=None, profile_dir=PROFILE, discover=lambda: [], log_dir=tmp_path,
                    rebind_windows=False)
    w = MainWindow(s, overlay=False)
    try:
        titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        assert "Nhiều cửa sổ" in titles and "Thông báo" in titles
        tab = w.extra_tabs["windows"]
        assert tab.table.rowCount() == 0 and "Không thấy cửa sổ" in tab.log_view.toPlainText()
        assert tab.start_all() == 0
    finally:
        w.close()
        s.shutdown()
