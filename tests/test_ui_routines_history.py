"""The "Chuỗi tác vụ" and "Lịch sử" tabs (offscreen Qt) and the AppServices wiring of the run
history and the scheduler, using FileCapturer and a recording input driver (no live game)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.window import GameWindow, WindowInfo
from ngoalong.engine.history import RunRecord
from ngoalong.engine.scheduler import RoutineStatus, RoutineStore

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


@pytest.fixture
def window(qapp, services):
    from ngoalong.ui.main_window import MainWindow

    w = MainWindow(services, overlay=False)
    yield w
    w.close()


def wait_until(qapp, cond, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return cond()


def quick_spins(services) -> None:
    from ngoalong.features.quay_ruong import QuayRuong

    services.settings.set_feature_params("quay_ruong", QuayRuong.Params(spins=2, delay_ms=0))


def test_tabs_registered_and_data_next_to_settings(window, services, tmp_path: Path) -> None:
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Chuỗi tác vụ" in titles and "Lịch sử" in titles
    assert {"routines", "history"} <= set(window.extra_tabs)
    assert services.data_dir == tmp_path / "data"


def test_services_without_settings_file_keep_data_in_memory(qapp, tmp_path: Path) -> None:
    from ngoalong.app import AppServices

    s = AppServices(settings_path=None, profile_dir=PROFILE, discover=lambda: [], log_dir=tmp_path / "logs",
                    rebind_windows=False)
    try:
        assert s.data_dir is None and s.history.path is None and s.routine_store.path is None
    finally:
        s.shutdown()


def test_edit_and_save_a_routine(qapp, window, services, tmp_path: Path) -> None:
    tab = window.extra_tabs["routines"]
    r = tab.new_routine()
    assert tab.list.count() == 1 and tab.selected_id() == r.id
    tab.name_edit.setText("Buổi sáng")
    tab.sched_box.setChecked(True)
    tab.times_edit.setText("20:30, 8h00")
    tab.day_boxes[0].setChecked(True)  # T2
    tab.day_boxes[6].setChecked(True)  # CN
    tab.stop_on_error_box.setChecked(False)
    tab.remove_step()  # the default first step (nothing selected: the current row)
    tab.table.selectRow(0)
    tab.remove_step()
    assert tab.table.rowCount() == 0
    tab.add_step("tham_bao")
    tab.add_step("thap_thi_luyen")
    tab.add_step("chua_cong")
    tab.table.selectRow(2)
    tab.move_step(-1)  # Tháp <-> Chúa công
    tab.table.cellWidget(0, 1).setValue(2)  # Thám Bảo: 2 retries
    tab.table.cellWidget(0, 2).setValue(30)
    tab.table.cellWidget(2, 3).setChecked(False)
    saved = tab.save_current()
    assert saved is not None, tab.message.text()

    again = RoutineStore(tmp_path / "data" / "routines.json").get(r.id)
    assert again is not None and again.name == "Buổi sáng" and again.enabled and not again.stop_on_error
    assert again.schedule.times == ["08:00", "20:30"] and again.schedule.weekdays == [0, 6]
    assert [s.feature_id for s in again.steps] == ["tham_bao", "chua_cong", "thap_thi_luyen"]
    assert (again.steps[0].retries, again.steps[0].retry_delay_s) == (2, 30.0)
    assert [s.enabled for s in again.steps] == [True, True, False]
    assert "Buổi sáng" in tab.list.item(0).text()
    assert "Lần chạy tới: Buổi sáng" in tab.next_label.text()


def test_invalid_routine_is_not_saved(qapp, window, services) -> None:
    tab = window.extra_tabs["routines"]
    r = tab.new_routine()
    tab.times_edit.setText("25:00")
    assert tab.save_current() is None and "giờ không hợp lệ" in tab.message.text()
    tab.times_edit.setText("08:00")
    tab.name_edit.setText("   ")
    assert tab.save_current() is None and "tên" in tab.message.text()
    assert services.routine_store.get(r.id).name == r.name  # type: ignore[union-attr]


def test_delete_routine(qapp, window, services) -> None:
    tab = window.extra_tabs["routines"]
    tab.new_routine()
    tab.new_routine()
    assert tab.list.count() == 2
    assert tab.delete_selected(confirm=False)
    assert tab.list.count() == 1 and len(services.routine_store.routines()) == 1


def test_scheduler_switch_is_saved(qapp, window, services, tmp_path: Path) -> None:
    tab = window.extra_tabs["routines"]
    tab.enabled_box.setChecked(False)
    assert not RoutineStore(tmp_path / "data" / "routines.json").scheduler_enabled
    assert "tắt" in tab.next_label.text()
    tab.enabled_box.setChecked(True)
    assert services.routine_store.scheduler_enabled


def test_run_routine_from_the_tab_and_see_it_in_history(qapp, window, services, tmp_path: Path) -> None:
    quick_spins(services)
    tab = window.extra_tabs["routines"]
    tab.new_routine()
    tab.name_edit.setText("Quay nhanh")
    tab.table.cellWidget(0, 0).setCurrentIndex(tab.table.cellWidget(0, 0).findData("quay_ruong"))
    outcomes: list = []
    services.routine_executor.routine_finished.connect(outcomes.append)
    assert tab.run_selected() == "started"
    assert "Quay nhanh" in tab.state_label.text()
    assert wait_until(qapp, lambda: bool(outcomes))
    assert outcomes[0].status is RoutineStatus.DONE, outcomes[0].message
    assert len(services.test_driver.clicks) == 6
    assert "Hoàn thành" in tab.message.text()

    hist = window.extra_tabs["history"]
    assert wait_until(qapp, lambda: hist.table.rowCount() == 1)
    rec = hist.records[0]
    assert rec.task_id == "quay_ruong" and rec.routine_name == "Quay nhanh" and rec.status == "done"
    assert rec.counters["progress"] == 2 and rec.counters["progress_total"] == 2 and rec.counters["clicks"] == 6
    assert rec.window.startswith("Sv 266")
    assert hist.table.item(0, 3).text() == "Quay nhanh" and hist.table.item(0, 4).text() == "Hoàn thành"
    assert "tiến độ 2/2" in hist.table.item(0, 5).text()
    assert (tmp_path / "data" / "history.sqlite3").is_file()
    assert "Chuỗi Quay nhanh" in window.log_view.toPlainText()  # executor log reaches the log view


def test_manual_run_is_recorded(qapp, window, services) -> None:
    quick_spins(services)
    page = window.feature_page
    page.select_feature("quay_ruong")
    page.forms["quay_ruong"].editors["spins"].set(1)
    page.forms["quay_ruong"].editors["delay_ms"].set(0)
    assert page.start_selected()
    hist = window.extra_tabs["history"]
    assert wait_until(qapp, lambda: hist.table.rowCount() == 1)
    assert hist.records[0].routine_name == "" and hist.records[0].task_title == "Quay rương"


def test_history_filters_details_and_clear(qapp, window, services, tmp_path: Path) -> None:
    now = datetime.now().replace(microsecond=0)
    shot = tmp_path / "001-fail.png"
    shot.write_bytes(b"png")
    h = services.history
    h.add(RunRecord(task_id="tham_bao", task_title="Thám Bảo", started_at=now - timedelta(minutes=5),
                    ended_at=now - timedelta(minutes=4), status="failed", message="Lỗi: không thấy nút",
                    screenshots=[str(shot)], run_dir=str(tmp_path), counters={"clicks": 4}))
    h.add(RunRecord(task_id="quay_ruong", task_title="Quay rương", started_at=now - timedelta(minutes=2),
                    ended_at=now, status="done", counters={"progress": 5, "progress_total": 5}))
    h.add(RunRecord(task_id="quay_ruong", task_title="Quay rương", started_at=now - timedelta(days=10),
                    ended_at=now - timedelta(days=10), status="stopped"))
    hist = window.extra_tabs["history"]
    hist.refresh()
    assert hist.table.rowCount() == 2  # default period: 7 days
    assert "2 lượt chạy" in hist.summary.text()
    hist.period_combo.setCurrentIndex(3)  # all
    assert hist.table.rowCount() == 3
    hist.status_combo.setCurrentIndex(hist.status_combo.findData("failed"))
    assert hist.table.rowCount() == 1
    assert "Ảnh lỗi:" in hist.details.toPlainText() and str(shot) in hist.details.toPlainText()
    assert hist.shot_btn.isEnabled() and hist.dir_btn.isEnabled()
    hist.status_combo.setCurrentIndex(0)
    hist.task_combo.setCurrentIndex(hist.task_combo.findData("quay_ruong"))
    assert hist.table.rowCount() == 2 and hist.table.item(0, 1).text() == "2:00"
    assert hist.clear_history(confirm=False)
    assert hist.table.rowCount() == 0 and services.history.count() == 0
    assert not hist.shot_btn.isEnabled()


def test_format_helpers() -> None:
    from ngoalong.ui.tabs.history import format_duration, period_start

    assert format_duration(5) == "0:05" and format_duration(3725) == "1:02:05"
    now = datetime(2026, 10, 6, 15, 30)
    assert period_start(0, now) == datetime(2026, 10, 6) and period_start(None, now) is None
    assert period_start(7, now) == now - timedelta(days=7)


def test_step_time_limit_column_and_endless_warning(qapp, window, services, tmp_path: Path) -> None:
    from ngoalong.ui.tabs.routines import COL_DURATION

    tab = window.extra_tabs["routines"]
    r = tab.new_routine()
    tab.table.cellWidget(0, 0).setCurrentIndex(tab.table.cellWidget(0, 0).findData("tham_bao"))
    saved = tab.save_current()  # Thám Bảo defaults to max_rounds=0: loops until Stop
    assert saved is not None and "Cảnh báo" in tab.message.text() and "Thám Bảo" in tab.message.text()
    tab.table.cellWidget(0, COL_DURATION).setValue(15)
    saved = tab.save_current()
    assert saved is not None and "Cảnh báo" not in tab.message.text()
    again = RoutineStore(tmp_path / "data" / "routines.json").get(r.id)
    assert again is not None and again.steps[0].max_duration_s == 900.0
    assert tab.table.cellWidget(0, COL_DURATION).value() == 15


def test_feature_page_stays_busy_during_a_routine(qapp, window, services) -> None:
    """Step 2 of a routine starts before the "Tính năng" page sees step 1 finish: the page must not
    show itself idle (Start enabled) while the routine runs."""
    from ngoalong.engine.scheduler import Routine, RoutineStep

    quick_spins(services)
    r = services.routine_store.upsert(Routine(name="Hai bước", steps=[
        RoutineStep(feature_id="quay_ruong"),
        RoutineStep(feature_id="quay_ruong", params={"spins": 50, "delay_ms": 200}),
    ]))
    page = window.feature_page
    outcomes: list = []
    services.routine_executor.routine_finished.connect(outcomes.append)
    assert services.scheduler.run_now(r.id) == "started"
    ex = services.routine_executor
    assert wait_until(qapp, lambda: ex.current_step is not None and ex.current_step.index == 1
                      and services.runner.state == "running")
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.01)
    assert not page.start_btn.isEnabled() and page.stop_btn.isEnabled()
    assert page.state_label.text() == "Đang chạy"
    assert not window.window_combo.isEnabled()
    assert not page.start_selected()  # refused while the routine runs
    ex.stop()
    assert wait_until(qapp, lambda: bool(outcomes))
    assert wait_until(qapp, lambda: page.start_btn.isEnabled())
    assert window.window_combo.isEnabled()


def test_scheduled_routine_runs_through_the_app(qapp, window, services) -> None:
    """The scheduler service in AppServices starts a due routine on the app's runner."""
    from ngoalong.engine.scheduler import Routine, RoutineStep, Schedule

    quick_spins(services)
    slot = datetime.now().replace(second=0, microsecond=0)
    r = services.routine_store.upsert(
        Routine(name="Hẹn giờ", steps=[RoutineStep(feature_id="quay_ruong")], schedule=Schedule(times=[f"{slot:%H:%M}"])),
        now=slot - timedelta(minutes=1),
    )
    outcomes: list = []
    services.routine_executor.routine_finished.connect(outcomes.append)
    services.scheduler.tick(slot + timedelta(seconds=30))
    assert wait_until(qapp, lambda: bool(outcomes))
    assert outcomes[0].routine_id == r.id and outcomes[0].status is RoutineStatus.DONE
    assert services.routine_store.get(r.id).last_fired_at == slot  # type: ignore[union-attr]
