"""UI in offscreen Qt mode: main window, generated forms, a run started from the UI, debug views."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix, Confidence
from ngoalong.core.window import GameWindow, WindowInfo
from ngoalong.debug.model import DebugFrame
from ngoalong.engine.context import ClickType
from ngoalong.engine.runner import RunStatus

CHEST = ROOT / "docs" / "game screenshots" / "chest-rolling-ui.png"
FAKE_WINDOW = GameWindow(WindowInfo(
    hwnd=0x1234, pid=4242, exe_path=r"C:\Users\x\AppData\Local\Coowon\Coowon\Application\chrome.exe",
    class_name="Chrome_WidgetWin_1", title="Sv 266 Ngọa Long Việt Nam - Coowon Browser",
))


@pytest.fixture
def services(qapp, tmp_path: Path):
    from ngoalong.app import AppServices

    drv = RecordingInputDriver()
    s = AppServices(
        settings_path=tmp_path / "settings.toml",
        profile_dir=PROFILE,
        discover=lambda: [FAKE_WINDOW],
        capturer_factory=lambda gw: FileCapturer([CHEST], loop=True),
        input_factory=lambda gw: drv,
        log_dir=tmp_path / "logs",
        rebind_windows=False,
    )
    # static frames cannot react to the panel clicks done before each run (tests/test_panels.py)
    s.settings.game.collapse_panels = False
    s.test_driver = drv  # type: ignore[attr-defined]
    yield s
    s.shutdown()


def wait_until(qapp, cond, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return cond()


def test_main_window_constructs_offscreen(qapp, services) -> None:
    from ngoalong.ui.main_window import MainWindow

    w = MainWindow(services)
    try:
        titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        assert titles[0] == "Tính năng" and "Gỡ lỗi" in titles and "Cài đặt" in titles
        assert w.windowTitle().startswith("AutoNgoaLong")
        assert services.window is FAKE_WINDOW  # the only window was bound
        assert w.window_combo.count() == 1 and "Ngọa Long" in w.window_combo.itemText(0)
        assert {"autoclick", "quay_ruong"} <= set(w.feature_page.forms)
        assert w.feature_page.start_btn.isEnabled() and not w.feature_page.stop_btn.isEnabled()
    finally:
        w.close()


def test_forms_are_generated_from_params(qapp, services) -> None:
    from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox

    from ngoalong.ui.main_window import MainWindow
    from ngoalong.ui.widgets.points_editor import CanvasPointsEditor

    w = MainWindow(services, overlay=False)
    try:
        qr = w.feature_page.forms["quay_ruong"]
        assert isinstance(qr.editors["spins"].widget, QSpinBox)
        assert qr.editors["spins"].widget.minimum() == 1 and qr.editors["spins"].widget.maximum() == 100_000
        assert isinstance(qr.editors["verify_each_spin"].widget, QCheckBox)
        ac = w.feature_page.forms["autoclick"]
        assert isinstance(ac.editors["points"], CanvasPointsEditor)
        combo = ac.editors["click_type"].widget
        assert isinstance(combo, QComboBox) and combo.count() == 6 and combo.itemText(0) == "Chuột trái"
        ac.editors["points"].add_point(10, 20)
        ac.editors["click_type"].set(ClickType.MIDDLE)
        v = ac.value()
        assert v.points[0].x == 10 and v.click_type is ClickType.MIDDLE
        # edits are saved into the settings
        assert services.settings.features["autoclick"]["points"] == [{"x": 10, "y": 20}]
    finally:
        w.close()


def test_start_feature_from_the_ui(qapp, services, tmp_path: Path) -> None:
    from ngoalong.ui.main_window import MainWindow

    w = MainWindow(services, overlay=False)
    try:
        page = w.feature_page
        page.select_feature("quay_ruong")
        form = page.forms["quay_ruong"]
        form.editors["spins"].set(2)
        form.editors["delay_ms"].set(0)
        assert page.start_selected()
        assert wait_until(qapp, lambda: services.runner.last_result is not None and not services.runner.is_running())
        r = services.runner.last_result
        assert r.status is RunStatus.DONE, r.message
        assert len(services.test_driver.clicks) == 6
        assert wait_until(qapp, lambda: "Đã quay xong" in w.log_view.toPlainText())
        assert "chest.wheel" in w.scene_label.text() and "tin cậy" in w.canvas_label.text()
        assert page.start_btn.isEnabled()
        assert (tmp_path / "settings.toml").is_file()  # params saved on start
    finally:
        w.close()


def test_start_without_a_window_is_reported(qapp, tmp_path: Path) -> None:
    from ngoalong.app import AppServices
    from ngoalong.ui.main_window import MainWindow

    s = AppServices(settings_path=None, profile_dir=PROFILE, discover=lambda: [], log_dir=tmp_path,
                    rebind_windows=False)
    w = MainWindow(s, overlay=False)
    try:
        assert not w.window_combo.isEnabled()
        assert not w.feature_page.start_selected()
        assert "Chưa chọn cửa sổ game" in w.log_view.toPlainText()
    finally:
        w.close()
        s.shutdown()


def test_runs_share_one_locator_per_window(services, tmp_path: Path) -> None:
    """P0-R2: the canvas fix is cached between runs; re-binding forgets it."""
    from ngoalong.engine.control import RunControl
    from ngoalong.engine.runner import RunHooks
    from ngoalong.features.autoclick import AutoClick

    services.bind(FAKE_WINDOW)
    hooks = RunHooks("t", tmp_path / "run", RunControl(), lambda *a: None, lambda *a: None, lambda *a: None)
    a = services.make_context(AutoClick(), hooks)
    b = services.make_context(AutoClick(), hooks)
    assert a.canvas_source is services.locator and b.canvas_source is services.locator
    assert a.reliable_canvas().reliable
    assert services.locator.last_good is not None
    services.bind(FAKE_WINDOW)
    assert services.locator.last_good is None


def test_check_screen_probe(qapp, services) -> None:
    from ngoalong.ui.main_window import MainWindow

    w = MainWindow(services, overlay=False)
    try:
        w.check_screen()
        assert wait_until(qapp, lambda: "chest.wheel" in w.scene_label.text())
        assert "tin cậy" in w.canvas_label.text()
    finally:
        w.close()


def _debug_frame() -> DebugFrame:
    from ngoalong.core.detection import Detection

    img = np.full((700, 1300, 3), 40, np.uint8)
    canvas = CanvasFix(Rect(100, 50, 1000, 600), 1.0, Confidence.HIGH, ("fixed",))
    return DebugFrame(image=img, canvas=canvas, scene="chest.wheel", scene_score=0.97,
                      detections=[Detection("chest.wheel.title", Rect(548, 112, 100, 30), 0.97, 0.9, True)],
                      clicks=[Point(300, 470)], task="Quay rương", step="lượt 1")


def test_debug_toggle_and_panel(qapp, services) -> None:
    from ngoalong.ui.main_window import MainWindow

    w = MainWindow(services, overlay=False)
    try:
        tab = w.extra_tabs["debug"]
        assert not tab.checkbox.isChecked()
        services.debug.toggle()  # what Ctrl+Alt+D does
        assert tab.checkbox.isChecked() and services.settings.debug
        tab.checkbox.setChecked(False)
        assert not services.debug.enabled
        services.panel_sink.publish(_debug_frame())
        qapp.processEvents()
        tab.panel.refresh()
        assert tab.panel.image.pixmap() is not None and not tab.panel.image.pixmap().isNull()
        assert "chest.wheel" in tab.panel.status.text()
    finally:
        w.close()


def test_overlay_window_follows_target(qapp) -> None:
    from ngoalong.debug.overlay_window import OverlayWindow

    target = {"rect": Rect(200, 150, 1300, 700)}
    ov = OverlayWindow(lambda: target["rect"], follow_ms=20)
    try:
        assert not ov.isVisible()
        ov.show_frame(_debug_frame())
        qapp.processEvents()
        assert ov.isVisible() and ov.frame is not None
        ov.grab()  # paints without errors
        target["rect"] = None  # game minimised / covered
        ov.follow()
        assert not ov.isVisible()
        ov.hide()
        qapp.processEvents()
        assert ov.frame is None and not ov.isVisible()
    finally:
        ov.close()


def test_entry_point_main(qapp, tmp_path: Path, monkeypatch) -> None:
    """``python -m ngoalong`` path: imports the INI once, builds the window, exits cleanly."""
    from PySide6.QtCore import QTimer

    import ngoalong.app as app_mod

    monkeypatch.setattr(app_mod, "_discover", lambda: [])
    monkeypatch.setattr(app_mod.AppServices, "apply_hotkeys", lambda self: [])  # no global hotkeys in tests
    ini = tmp_path / "NgoaLong_data.ini"
    ini.write_text("[PRO5]\nQR_SOLUONG=33\nQR_TOCDO=7\n", encoding="utf-8")
    settings = tmp_path / "settings.toml"
    QTimer.singleShot(300, qapp.quit)
    rc = app_mod.main(["--settings", str(settings), "--ini", str(ini)])
    assert rc == 0
    from ngoalong.config.settings import load_settings
    from ngoalong.features.quay_ruong import QuayRuong

    s = load_settings(settings)
    assert s.feature_params(QuayRuong).spins == 33 and s.ini_imported_from == str(ini)


def test_overlay_sink_wiring(qapp, services) -> None:
    from ngoalong.ui.main_window import MainWindow

    w = MainWindow(services)
    try:
        assert services.overlay_sink is not None and not services.overlay_sink.enabled
        services.debug.set_enabled(True)
        assert services.overlay_sink.enabled
        services.debug.set_enabled(False)
        assert not services.overlay_sink.enabled
    finally:
        w.close()


def test_overlay_refresher_follows_manual_screen_changes(qapp, services) -> None:
    """Debug overlay re-captures on its own: a screen changed by hand (no bot run) is redrawn."""
    from ngoalong.core.drivers.file import FileCapturer
    from ngoalong.core.errors import WindowGoneError
    from ngoalong.ui.main_window import MainWindow

    shots = ROOT / "docs" / "game screenshots"
    screen = {"path": CHEST}
    services.capturer_factory = lambda gw: FileCapturer([screen["path"]], loop=True)
    w = MainWindow(services)
    try:
        ov, refresher = w.overlay, services.overlay_refresher
        assert services.set_overlay_refresh_ms(10) == 250  # clamped to the minimum
        assert not refresher.active  # debug mode is off
        services.debug.set_enabled(True)
        assert refresher.active
        assert wait_until(qapp, lambda: ov.frame is not None and ov.frame.scene == "chest.wheel")
        assert ov.frame.note == "live" and ov.frame.detections

        screen["path"] = shots / "worldmap-ui.png"  # the user opens the world map by hand
        assert wait_until(qapp, lambda: ov.frame is not None and ov.frame.scene == "world_map")

        bot = _debug_frame()  # a bot step's frame is not replaced right away
        services.overlay_sink.publish(bot)
        qapp.processEvents()
        time.sleep(0.6)
        qapp.processEvents()
        assert ov.frame is bot
        services.overlay_sink.last_publish = 0.0

        services.debug.set_enabled(False)
        assert not refresher.active
        services.debug.set_enabled(True)
        assert wait_until(qapp, lambda: ov.frame is not None and ov.frame.note == "live")

        def gone() -> None:
            raise WindowGoneError("window closed")

        services.overlay_snapshot = gone  # type: ignore[method-assign]
        assert wait_until(qapp, lambda: not refresher.active)
        assert ov.frame is None  # stale markers are cleared
    finally:
        w.close()
    assert not services.overlay_refresher.active
