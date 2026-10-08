"""Run history: RunRecord, the SQLite HistoryStore, counters carried by execute(), the recorder."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine.context import FixedCanvas, RunContext
from ngoalong.engine.history import HistoryRecorder, HistoryStore, RunRecord
from ngoalong.engine.runner import RunResult, RunStatus, execute
from ngoalong.engine.task import Task

T0 = datetime(2026, 10, 6, 8, 0, 0)


def rec(task_id: str = "quay_ruong", status: str = "done", start: datetime = T0, secs: int = 30,
        **kw) -> RunRecord:
    return RunRecord(task_id=task_id, task_title=kw.pop("task_title", task_id), started_at=start,
                     ended_at=start + timedelta(seconds=secs), status=status, **kw)


def make_ctx(tmp_path: Path) -> RunContext:
    return RunContext(
        capturer=FileCapturer([np.zeros((700, 1200, 3), np.uint8)], loop=True),
        input_driver=RecordingInputDriver(),
        coords=CoordMap.load(PROFILE / "coords"),
        matcher=Matcher(TemplateStore(PROFILE / "templates")),
        canvas_source=FixedCanvas(),
        png_sink=PngSink(tmp_path / "run"),
    )


# --- RunRecord ----------------------------------------------------------------------------------
def test_record_from_result_carries_meta_counters_and_screenshot(tmp_path: Path) -> None:
    shot = tmp_path / "001-fail.png"
    started = time.time() - 12
    r = RunResult("tham_bao", RunStatus.FAILED, "Lỗi: x", tmp_path, shot, None, 12.0,
                  task_title="Thám Bảo", started_at=started, ended_at=started + 12,
                  counters={"clicks": 7, "progress": 3, "progress_total": 10},
                  meta={"routine_id": "r1", "routine_name": "Sáng", "attempt": 2})
    record = RunRecord.from_result(r, window="Sv 266")
    assert record.task_title == "Thám Bảo" and record.status == "failed"
    assert record.screenshots == [str(shot)] and record.run_dir == str(tmp_path)
    assert (record.routine_id, record.routine_name, record.attempt, record.window) == ("r1", "Sáng", 2, "Sv 266")
    assert 11 <= record.duration_s <= 13
    assert record.status_text == "Lỗi"
    assert record.counters_text() == "tiến độ 3/10, lượt nhấp 7"


def test_record_without_wall_times_uses_duration() -> None:
    r = RunResult("autoclick", RunStatus.DONE, "ok", duration_s=90.0)
    record = RunRecord.from_result(r)
    assert record.task_title == "autoclick" and 89 <= record.duration_s <= 91 and record.screenshots == []


# --- HistoryStore -------------------------------------------------------------------------------
def test_store_add_query_filters_and_persistence(tmp_path: Path) -> None:
    path = tmp_path / "data" / "history.sqlite3"
    store = HistoryStore(path)
    assert not path.exists()  # opened lazily
    a = store.add(rec("quay_ruong", "done", T0, counters={"clicks": 3}))
    store.add(rec("tham_bao", "failed", T0 + timedelta(hours=1), screenshots=["x.png"]))
    store.add(rec("tham_bao", "done", T0 + timedelta(hours=2), routine_id="r1", routine_name="Sáng"))
    assert a.id is not None and path.is_file()
    assert [r.task_id for r in store.query()] == ["tham_bao", "tham_bao", "quay_ruong"]  # newest first
    assert store.count() == 3 and store.count(task_id="tham_bao") == 2
    assert [r.status for r in store.query(task_id="tham_bao", status="failed")] == ["failed"]
    assert [r.routine_name for r in store.query(routine_id="r1")] == ["Sáng"]
    assert len(store.query(since=T0 + timedelta(minutes=30))) == 2
    assert len(store.query(until=T0 + timedelta(minutes=30))) == 1
    assert len(store.query(limit=1, offset=1)) == 1
    got = store.get(a.id)
    assert got is not None and got.counters == {"clicks": 3} and got.started_at == T0
    store.close()

    again = HistoryStore(path)  # survives a restart
    assert again.count() == 3 and again.query(status="failed")[0].screenshots == ["x.png"]
    assert again.delete([a.id]) == 1 and again.count() == 2
    assert again.clear() == 2 and again.count() == 0
    again.close()


def test_store_prunes_to_max_records_and_by_age(tmp_path: Path) -> None:
    store = HistoryStore(None, max_records=3)
    for i in range(5):
        store.add(rec(start=T0 + timedelta(minutes=i)))
    assert store.count() == 3
    assert min(r.started_at for r in store.query()) == T0 + timedelta(minutes=2)

    aged = HistoryStore(None)
    aged.add(rec(start=datetime.now() - timedelta(days=40)))
    aged.add(rec(start=datetime.now() - timedelta(days=1)))
    assert aged.prune(older_than_days=30) == 1 and aged.count() == 1


def test_store_summary_sums_counters(tmp_path: Path) -> None:
    store = HistoryStore(None)
    store.add(rec("quay_ruong", "done", counters={"clicks": 3, "spins": 5, "progress_total": 5}))
    store.add(rec("quay_ruong", "failed", counters={"clicks": 2, "spins": 1}))
    store.add(rec("tham_bao", "stopped", task_title="Thám Bảo"))
    summary = {s.task_id: s for s in store.summary()}
    q = summary["quay_ruong"]
    assert (q.runs, q.done, q.failed, q.stopped) == (2, 1, 1, 0)
    assert q.counters == {"clicks": 5, "spins": 6}
    assert summary["tham_bao"].stopped == 1 and summary["tham_bao"].task_title == "Thám Bảo"


def test_broken_database_is_kept_as_backup(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    path.write_bytes(b"this is not a sqlite database" * 100)
    store = HistoryStore(path)
    store.add(rec())
    assert store.count() == 1
    assert (tmp_path / "history.sqlite3.bak").is_file()
    store.close()


def test_store_is_thread_safe() -> None:
    store = HistoryStore(None)

    def worker(n: int) -> None:
        for i in range(25):
            store.add(rec(task_id=f"t{n}", start=T0 + timedelta(seconds=i)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert store.count() == 100


# --- execute() fills the history fields -----------------------------------------------------------
class _Counting(Task):
    id = "counting"
    title = "Đếm"

    def run(self, ctx: RunContext) -> str:
        ctx.count("spins")
        ctx.count("spins", 2)
        ctx.progress(2, 4)
        return "xong"


class _Failing(Task):
    id = "failing"
    title = "Hỏng"

    def run(self, ctx: RunContext) -> str:
        ctx.grab()
        raise RuntimeError("không thấy nút")


def test_execute_result_has_counters_meta_and_wall_times(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    before = time.time()
    r = execute(_Counting(), ctx, meta={"routine_id": "r9"})
    assert r.status is RunStatus.DONE and r.task_title == "Đếm"
    assert r.counters == {"clicks": 0, "spins": 3, "progress": 2, "progress_total": 4}
    assert r.meta == {"routine_id": "r9"}
    assert before <= r.started_at <= r.ended_at <= time.time()


def test_failed_run_record_keeps_the_failure_screenshot(tmp_path: Path) -> None:
    r = execute(_Failing(), make_ctx(tmp_path), tmp_path / "run")
    assert r.status is RunStatus.FAILED and r.screenshot is not None and r.screenshot.is_file()
    store = HistoryStore(None)
    saved = store.add(RunRecord.from_result(r))
    assert store.get(saved.id).screenshots == [str(r.screenshot)]  # type: ignore[arg-type, union-attr]


# --- recorder -----------------------------------------------------------------------------------
def test_recorder_records_runner_results(qapp, tmp_path: Path) -> None:
    from ngoalong.engine.runner import TaskRunner

    store = HistoryStore(None)
    recorder = HistoryRecorder(store)
    runner = TaskRunner(tmp_path / "logs")
    recorder.watch(runner, lambda: "Cửa sổ 1")
    seen: list[RunRecord] = []
    recorder.recorded.connect(seen.append)
    runner.start(_Counting(), lambda task, hooks: make_ctx(tmp_path), meta={"routine_name": "Tối"})
    assert runner.wait(5)
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert len(seen) == 1 and store.count() == 1
    r = store.query()[0]
    assert r.task_id == "counting" and r.window == "Cửa sổ 1" and r.routine_name == "Tối"
    assert r.counters["spins"] == 3 and r.run_dir.endswith("counting")


def test_recorder_survives_a_store_error(qapp) -> None:
    store = HistoryStore(None)
    store.close()

    class Broken(HistoryStore):
        def add(self, record: RunRecord) -> RunRecord:  # noqa: ARG002
            import sqlite3

            raise sqlite3.OperationalError("database is locked")

    recorder = HistoryRecorder(Broken(None))
    assert recorder.record(RunResult("x", RunStatus.DONE, "ok")) is None


@pytest.mark.parametrize("status", list(RunStatus))
def test_every_status_has_vietnamese_text(status: RunStatus) -> None:
    from ngoalong.engine.history import STATUS_TEXT

    assert STATUS_TEXT[status.value]
