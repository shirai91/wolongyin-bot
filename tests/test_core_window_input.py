"""GameWindow discovery/rebind rules, Win32InputDriver message sequences, CDP stub."""

from __future__ import annotations

import sys

import pytest

from ngoalong.core.drivers.cdp import CdpCapturer, CdpInputDriver
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.geometry import Point
from ngoalong.core.input import VK_RETURN, InputDriver
from ngoalong.core.window import GameWindow, WindowInfo, fold_text, is_game_window

COOWON = r"C:\Users\me\AppData\Local\Coowon\Coowon\Application\chrome.exe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
TITLE = "Sv 266 Ngọa Long Việt Nam - Coowon Browser"


def win(hwnd: int, pid: int = 100, exe: str = COOWON, cls: str = "Chrome_WidgetWin_1",
        title: str = TITLE, visible: bool = True) -> WindowInfo:
    return WindowInfo(hwnd, pid, exe, cls, title, visible)


# --- discovery rules ------------------------------------------------------------------------------
def test_fold_text_strips_vietnamese_diacritics() -> None:
    assert fold_text("Ngọa Long Việt Nam Đồng ý") == "ngoa long viet nam dong y"


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (win(1), True),
        (win(1, title="Ngoa Long - Coowon"), True),  # without diacritics
        (win(1, visible=False), False),
        (win(1, cls="Chrome_RenderWidgetHostHWND"), False),
        (win(1, title="Google - Coowon Browser"), False),  # Coowon, but not the game
        (win(1, exe=CHROME, title="Ngọa Long - Google Chrome"), False),  # game in another browser
        (win(1, exe="", title=TITLE), True),  # path unreadable: title says Coowon
        (win(1, exe="", title="Ngọa Long"), False),
    ],
)
def test_is_game_window(info: WindowInfo, expected: bool) -> None:
    assert is_game_window(info) is expected


def test_discover_filters_and_skips_own_process() -> None:
    windows = [win(1, pid=100), win(2, pid=200, exe=CHROME), win(3, pid=999, exe="", title=TITLE + " (bot)"),
               win(4, pid=101, title="Sv 1 Ngọa Long - Coowon Browser")]
    found = GameWindow.discover(windows=windows, own_pid=999)
    assert [g.hwnd for g in found] == [1, 4]
    assert found[0].pid == 100 and found[0].exe_path == COOWON


def test_rebind_after_browser_restart_prefers_same_exe_and_title() -> None:
    gw = GameWindow(win(1, pid=100))
    after_restart = [win(7, pid=300, title="Other Ngọa Long tab - Coowon"), win(8, pid=300)]
    assert gw.rebind(windows=after_restart, own_pid=1) is True
    assert (gw.hwnd, gw.pid) == (8, 300)


def test_rebind_keeps_same_window_when_present() -> None:
    gw = GameWindow(win(5, pid=100))
    assert gw.rebind(windows=[win(4, pid=100), win(5, pid=100)], own_pid=1)
    assert gw.hwnd == 5


def test_rebind_fails_without_candidates() -> None:
    gw = GameWindow(win(5))
    assert gw.rebind(windows=[win(9, exe=CHROME, title="Ngọa Long - Chrome")], own_pid=1) is False
    assert gw.hwnd == 5


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_dead_window_is_not_alive() -> None:
    gw = GameWindow(win(0x7FFFFFF0))
    assert gw.is_alive() is False


# --- Win32InputDriver -----------------------------------------------------------------------------
@pytest.fixture()
def posted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, int, int]]:
    from ngoalong.core.drivers import win32

    log: list[tuple[int, int, int, int]] = []

    def fake_post(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
        log.append((hwnd, msg, wparam, lparam))
        return True

    monkeypatch.setattr(win32, "_post_message", fake_post)
    return log


def _driver():  # noqa: ANN202
    from ngoalong.core.drivers.win32 import Win32InputDriver

    return Win32InputDriver(0x1234, sleep=lambda _s: None)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_left_click_sequence(posted: list) -> None:
    _driver().click(Point(705, 433))
    lp = (433 << 16) | 705
    assert posted == [(0x1234, 0x0200, 0, lp), (0x1234, 0x0201, 1, lp), (0x1234, 0x0202, 0, lp)]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_double_and_right_click_honour_type(posted: list) -> None:
    d = _driver()
    d.click(Point(1, 2), double=True)
    assert [m[1] for m in posted] == [0x0200, 0x0201, 0x0202, 0x0203, 0x0202]
    posted.clear()
    d.click(Point(1, 2), button="right")  # click type honoured (#10)
    assert [m[1] for m in posted] == [0x0200, 0x0204, 0x0205]
    assert posted[1][2] == 0x0002  # MK_RBUTTON
    with pytest.raises(ValueError):
        d.click(Point(1, 2), button="x1")  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_key_sends_down_then_up(posted: list) -> None:
    _driver().key(VK_RETURN)
    assert [(m[1], m[2]) for m in posted] == [(0x0100, 0x0D), (0x0101, 0x0D)]  # one down, one up (#9)
    down_lp, up_lp = posted[0][3], posted[1][3]
    assert down_lp & 0xFFFF == 1 and not down_lp & (1 << 31)
    assert up_lp & (1 << 31) and up_lp & (1 << 30)
    assert (down_lp >> 16) & 0xFF == 0x1C  # scan code of Enter


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_text_sends_one_wm_char_per_character(posted: list) -> None:
    _driver().text("12ệ")
    assert [(m[1], m[2]) for m in posted] == [(0x0102, ord("1")), (0x0102, ord("2")), (0x0102, ord("ệ"))]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_post_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from ngoalong.core.drivers import win32
    from ngoalong.core.errors import WindowGoneError

    monkeypatch.setattr(win32, "_post_message", lambda *a: False)
    with pytest.raises(WindowGoneError):
        _driver().click(Point(1, 1))


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_driver_follows_rebound_window(posted: list) -> None:
    from ngoalong.core.drivers.win32 import Win32InputDriver

    gw = GameWindow(win(0x10))
    d = Win32InputDriver(gw, sleep=lambda _s: None)
    d.key(VK_RETURN)
    gw.hwnd = 0x20  # as after rebind()
    d.key(VK_RETURN)
    assert {m[0] for m in posted} == {0x10, 0x20}


# --- interfaces and stubs -------------------------------------------------------------------------
def test_recording_driver_satisfies_interface() -> None:
    d = RecordingInputDriver()
    assert isinstance(d, InputDriver)
    d.click(Point(1, 2))
    d.click(Point(3, 4), button="right", double=True)
    d.key(VK_RETURN)
    d.text("5")
    assert d.clicks == [Point(1, 2), Point(3, 4)]
    assert [e.kind for e in d.events] == ["click", "click", "key", "text"]


def test_cdp_is_a_post_mvp_stub() -> None:
    with pytest.raises(NotImplementedError):
        CdpInputDriver().click(Point(0, 0))
    with pytest.raises(NotImplementedError):
        CdpCapturer().grab()
