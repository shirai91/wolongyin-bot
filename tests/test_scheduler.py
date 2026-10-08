"""Routine chains and the scheduler: schedule math, JSON store, due slots, the routine state machine
(retries, stop-on-error, stop), and the Qt executor / service driving a real TaskRunner offline."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from conftest import PROFILE
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine.context import FixedCanvas, RunContext
from ngoalong.engine.control import RunControl
from ngoalong.engine.history import HistoryRecorder, HistoryStore
from ngoalong.engine.runner import RunHooks, RunStatus, TaskRunner
from ngoalong.engine.scheduler import (
    Routine, RoutineExecutor, RoutineRun, RoutineStatus, RoutineStep, RoutineStore, Schedule, Scheduler,
    SchedulerService, StartStep, StepError, build_step_task, parse_time, parse_times, run_routine,
)
from ngoalong.engine.task import Task, TaskParams

MON = datetime(2026, 10, 5, 0, 0)  # a Monday


def at(day: int, hh: int, mm: int, ss: int = 0) -> datetime:
    """``day`` 0 = Monday 5 Oct 2026."""
    return MON + timedelta(days=day, hours=hh, minutes=mm, seconds=ss)


def routine(name: str = "Sáng", steps: list[str] | None = None, times: list[str] | None = None,
            **kw) -> Routine:
    steps = steps if steps is not None else ["a", "b", "c"]
    return Routine(name=name, steps=[RoutineStep(feature_id=s, retry_delay_s=0) for s in steps],
                   schedule=Schedule(times=times or []), updated_at=kw.pop("updated_at", MON - timedelta(days=30)),
                   **kw)


# --- parsing and schedule math ------------------------------------------------------------------
def test_parse_times() -> None:
    assert parse_time("8:05") == "08:05" and parse_time(" 20h30 ") == "20:30" and parse_time("7.00") == "07:00"
    assert parse_times("20:30, 08:00 8:00;12:15") == ["08:00", "12:15", "20:30"]
    assert parse_times("  ") == []
    for bad in ("24:00", "8:5", "abc", "12:60"):
        with pytest.raises(ValueError):
            parse_time(bad)
    with pytest.raises(ValidationError):
        Schedule(weekdays=[7])


def test_schedule_next_and_latest_slots() -> None:
    s = Schedule(times=["20:30", "08:00"])
    assert s.times == ["08:00", "20:30"]
    assert s.next_after(at(0, 7, 0)) == at(0, 8, 0)
    assert s.next_after(at(0, 8, 0)) == at(0, 20, 30)  # strictly after
    assert s.next_after(at(0, 21, 0)) == at(1, 8, 0)
    assert s.latest_at_or_before(at(0, 8, 0)) == at(0, 8, 0)
    assert s.latest_at_or_before(at(0, 7, 59)) == at(-1, 20, 30)
    weekend = Schedule(times=["09:00"], weekdays=[5, 6])  # Saturday, Sunday
    assert weekend.next_after(at(0, 10, 0)) == at(5, 9, 0)
    assert weekend.latest_at_or_before(at(2, 10, 0)) == at(-1, 9, 0)
    assert Schedule().next_after(at(0, 0, 0)) is None and Schedule().latest_at_or_before(at(0, 0, 0)) is None
    assert weekend.describe() == "09:00 (T7, CN)" and Schedule().describe() == "không đặt giờ"


def test_routine_validation() -> None:
    with pytest.raises(ValidationError):
        Routine(name="  ")
    with pytest.raises(ValidationError):
        RoutineStep(feature_id="x", retries=11)
    with pytest.raises(ValidationError):
        RoutineStep(feature_id="x", retry_delay_s=-1)
    r = routine(steps=["a", "b"])
    r.steps[0].enabled = False
    assert [i for i, _s in r.active_steps] == [1]


# --- store --------------------------------------------------------------------------------------
def test_store_roundtrip_order_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "data" / "routines.json"
    store = RoutineStore(path)
    a = store.upsert(routine("A", times=["08:00"]), now=at(0, 1, 0))
    b = store.upsert(routine("B"), now=at(0, 1, 0))
    assert a.updated_at == at(0, 1, 0)
    store.upsert(a.model_copy(update={"name": "A2"}))  # replacing keeps the position
    assert [r.name for r in store.routines()] == ["A2", "B"]
    store.mark_fired(a.id, at(0, 8, 0))
    store.set_scheduler_enabled(False)

    again = RoutineStore(path)
    assert [r.name for r in again.routines()] == ["A2", "B"]
    assert again.get(a.id).last_fired_at == at(0, 8, 0)  # type: ignore[union-attr]
    assert again.get(a.id).schedule.times == ["08:00"]  # type: ignore[union-attr]
    assert not again.scheduler_enabled
    assert again.delete(b.id) and not again.delete(b.id)
    assert json.loads(path.read_text(encoding="utf-8"))["routines"][0]["name"] == "A2"


def test_store_returns_copies(tmp_path: Path) -> None:
    store = RoutineStore(None)
    r = store.upsert(routine("A"))
    got = store.get(r.id)
    got.name = "changed"  # type: ignore[union-attr]
    assert store.get(r.id).name == "A"  # type: ignore[union-attr]


def test_broken_store_file_is_kept_as_backup(tmp_path: Path) -> None:
    path = tmp_path / "routines.json"
    path.write_text("{not json", encoding="utf-8")
    store = RoutineStore(path)
    assert store.routines() == [] and (tmp_path / "routines.json.bak").is_file()


# --- due slots ----------------------------------------------------------------------------------
def test_scheduler_fires_each_slot_once_within_grace(tmp_path: Path) -> None:
    store = RoutineStore(tmp_path / "routines.json")
    r = store.upsert(routine("A", times=["08:00", "20:00"]), now=at(0, 1, 0))
    sched = Scheduler(store, grace_s=300)
    assert sched.due(at(0, 7, 59)) == []
    due = sched.due(at(0, 8, 0, 10))
    assert [(x.id, slot) for x, slot in due] == [(r.id, at(0, 8, 0))]
    assert sched.due(at(0, 8, 1)) == []  # same slot never twice
    assert Scheduler(RoutineStore(tmp_path / "routines.json")).due(at(0, 8, 2)) == []  # ...even after a restart
    assert sched.due(at(0, 20, 6)) == []  # missed by more than the grace window
    assert [slot for _x, slot in sched.due(at(1, 8, 4))] == [at(1, 8, 0)]


def test_scheduler_skips_disabled_unscheduled_and_fresh_edits() -> None:
    store = RoutineStore(None)
    store.upsert(routine("off", times=["08:00"], enabled=False), now=at(0, 1, 0))
    store.upsert(routine("no times"), now=at(0, 1, 0))
    store.upsert(routine("no steps", steps=[], times=["08:00"]), now=at(0, 1, 0))
    store.upsert(routine("edited late", times=["08:00"]), now=at(0, 8, 1))  # saved after the slot
    sched = Scheduler(store)
    assert sched.due(at(0, 8, 2)) == []
    ok = store.upsert(routine("ok", times=["08:00"]), now=at(0, 1, 0))
    store.set_scheduler_enabled(False)
    assert sched.due(at(0, 8, 2)) == [] and sched.upcoming(at(0, 8, 2)) == []
    store.set_scheduler_enabled(True)
    assert [x.id for x, _ in sched.due(at(0, 8, 2))] == [ok.id]
    upcoming = sched.upcoming(at(0, 8, 2))
    assert [(x.name, t) for x, t in upcoming] == [("edited late", at(1, 8, 0)), ("ok", at(1, 8, 0))]


# --- the routine state machine ------------------------------------------------------------------
def scripted(results: dict[str, list[RunStatus]]):
    """A step runner returning scripted results per feature (default DONE); records the calls."""
    calls: list[tuple[str, int]] = []

    def run_step(a: StartStep) -> tuple[RunStatus, str]:
        calls.append((a.step.feature_id, a.attempt))
        seq = results.get(a.step.feature_id, [])
        status = seq.pop(0) if seq else RunStatus.DONE
        return status, f"{a.step.feature_id}:{status.value}"

    return run_step, calls


def test_all_steps_done() -> None:
    run_step, calls = scripted({})
    out = run_routine(routine(), run_step)
    assert out.status is RoutineStatus.DONE and calls == [("a", 1), ("b", 1), ("c", 1)]
    assert [s.status for s in out.steps] == ["done"] * 3 and out.message == "Xong 3 bước"


def test_retry_then_success() -> None:
    r = routine()
    r.steps[1].retries = 2
    run_step, calls = scripted({"b": [RunStatus.FAILED, RunStatus.FAILED]})
    out = run_routine(r, run_step)
    assert out.status is RoutineStatus.DONE
    assert calls == [("a", 1), ("b", 1), ("b", 2), ("b", 3), ("c", 1)]
    assert out.steps[1].attempts == 3


def test_failure_with_stop_on_error_ends_the_routine() -> None:
    r = routine()
    r.steps[0].retries = 1
    run_step, calls = scripted({"a": [RunStatus.FAILED, RunStatus.FAILED]})
    out = run_routine(r, run_step)
    assert out.status is RoutineStatus.FAILED and calls == [("a", 1), ("a", 2)]
    assert "Bước 1" in out.message and "2 lần" in out.message


def test_failure_without_stop_on_error_continues() -> None:
    r = routine(stop_on_error=False)
    run_step, calls = scripted({"b": [RunStatus.FAILED]})
    out = run_routine(r, run_step)
    assert out.status is RoutineStatus.PARTIAL and [c[0] for c in calls] == ["a", "b", "c"]
    assert [s.status for s in out.steps] == ["done", "failed", "done"]


def test_stopped_step_ends_the_routine_without_retry() -> None:
    r = routine()
    r.steps[1].retries = 3
    run_step, calls = scripted({"b": [RunStatus.STOPPED]})
    out = run_routine(r, run_step)
    assert out.status is RoutineStatus.STOPPED and calls == [("a", 1), ("b", 1)]


def test_disabled_steps_are_skipped_and_empty_routine_is_done() -> None:
    r = routine()
    r.steps[0].enabled = False
    run_step, calls = scripted({})
    assert run_routine(r, run_step).status is RoutineStatus.DONE and [c[0] for c in calls] == ["b", "c"]
    assert run_routine(routine(steps=[]), run_step).status is RoutineStatus.DONE


def test_non_retryable_failure_is_not_retried() -> None:
    r = routine(steps=["a"])
    r.steps[0].retries = 5
    run = RoutineRun(r)
    first = run.first()
    assert isinstance(first, StartStep)
    action = run.report(RunStatus.FAILED, "không có tính năng", retryable=False)
    assert action.outcome.status is RoutineStatus.FAILED  # type: ignore[union-attr]
    assert run.finished and run.report(RunStatus.DONE).outcome is run.result  # type: ignore[union-attr]


def test_stop_during_retry_wait_is_prompt() -> None:
    r = routine(steps=["a", "b"])
    r.steps[0].retries = 1
    r.steps[0].retry_delay_s = 30
    control = RunControl()
    run_step, calls = scripted({"a": [RunStatus.FAILED]})
    threading.Timer(0.2, control.stop).start()
    t0 = time.monotonic()
    out = run_routine(r, run_step, control)
    assert out.status is RoutineStatus.STOPPED and calls == [("a", 1)]
    assert time.monotonic() - t0 < 2.0


# --- building a step's task ---------------------------------------------------------------------
class _P(TaskParams):
    count: int = 1
    label: str = "x"


class _T(Task):
    id = "t_param"
    title = "Thử"
    Params = _P

    def run(self, ctx: RunContext) -> str:
        return "ok"


def test_build_step_task_merges_saved_params_and_override() -> None:
    lookup = {"t_param": _T}.__getitem__
    saved = lambda cls: _P(count=5, label="saved")  # noqa: E731
    t = build_step_task(RoutineStep(feature_id="t_param"), saved, lookup)
    assert isinstance(t, _T) and (t.params.count, t.params.label) == (5, "saved")  # type: ignore[attr-defined]
    t = build_step_task(RoutineStep(feature_id="t_param", params={"count": 9}), saved, lookup)
    assert (t.params.count, t.params.label) == (9, "saved")  # type: ignore[attr-defined]
    with pytest.raises(StepError):
        build_step_task(RoutineStep(feature_id="t_param", params={"count": "nhiều"}), saved, lookup)
    with pytest.raises(StepError):
        build_step_task(RoutineStep(feature_id="missing"), saved, lookup)


def test_build_step_task_uses_the_registry() -> None:
    from ngoalong.engine import registry
    from ngoalong.features.quay_ruong import QuayRuong

    registry.discover()
    t = build_step_task(RoutineStep(feature_id="quay_ruong", params={"spins": 3}), lambda cls: cls.Params())
    assert isinstance(t, QuayRuong) and t.params.spins == 3  # type: ignore[attr-defined]


# --- Qt executor and scheduler service ----------------------------------------------------------
FLAKY: dict[str, int] = {}


class _Ok(Task):
    id = "q_ok"
    title = "Bước ổn"

    def run(self, ctx: RunContext) -> str:
        ctx.count("spins", 2)
        return "ok"


class _Flaky(Task):
    """Fails while ``FLAKY['q_flaky'] > 0``."""

    id = "q_flaky"
    title = "Bước chập chờn"

    def run(self, ctx: RunContext) -> str:
        if FLAKY.get(self.id, 0) > 0:
            FLAKY[self.id] -= 1
            raise RuntimeError("chưa được")
        return "ok"


class _Slow(Task):
    id = "q_slow"
    title = "Bước chậm"

    def run(self, ctx: RunContext) -> str:
        for _ in range(200):  # at most 10 s; Stop interrupts the sleep
            ctx.sleep(50)
        return "hết giờ"


class _Endless(Task):
    """Loops until Stop (like Thám Bảo with max_rounds=0); a 20 s safety bound for the tests."""

    id = "q_endless"
    title = "Bước vô hạn"

    def run(self, ctx: RunContext) -> str:
        for _ in range(400):
            ctx.sleep(50)
        return "không bao giờ tới đây"


TASKS = {c.id: c for c in (_Ok, _Flaky, _Slow, _Endless)}


def build(step: RoutineStep) -> Task:
    try:
        return TASKS[step.feature_id]()
    except KeyError:
        raise StepError(f"không có tính năng {step.feature_id!r}") from None


def ctx_factory(task: Task, hooks: RunHooks) -> RunContext:
    return RunContext(
        capturer=FileCapturer([np.zeros((700, 1200, 3), np.uint8)], loop=True),
        input_driver=RecordingInputDriver(),
        coords=CoordMap.load(PROFILE / "coords"),
        matcher=Matcher(TemplateStore(PROFILE / "templates")),
        canvas_source=FixedCanvas(),
        control=hooks.control,
        on_log=hooks.on_log,
        png_sink=PngSink(hooks.run_dir),
    )


def wait_until(qapp, cond, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return cond()


@pytest.fixture
def rig(qapp, tmp_path: Path):
    runner = TaskRunner(tmp_path / "logs")
    history = HistoryStore(None)
    recorder = HistoryRecorder(history)
    recorder.watch(runner, "Cửa sổ test")
    executor = RoutineExecutor(runner, build, ctx_factory, recorder)
    outcomes: list = []
    executor.routine_finished.connect(outcomes.append)
    FLAKY.clear()
    yield runner, executor, history, outcomes
    executor.stop()
    runner.shutdown()


def chain(steps: list[str], name: str = "Chuỗi thử", **kw) -> Routine:
    return Routine(name=name, steps=[RoutineStep(feature_id=s, retry_delay_s=0.05) for s in steps], **kw)


def test_executor_runs_a_chain_with_a_retry(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    r = chain(["q_ok", "q_flaky", "q_ok"])
    r.steps[1].retries = 2
    FLAKY["q_flaky"] = 1
    started: list = []
    executor.step_started.connect(lambda *a: started.append(a[1:]))
    assert executor.start(r)
    assert not executor.start(r)  # one routine at a time
    assert wait_until(qapp, lambda: bool(outcomes))
    out = outcomes[0]
    assert out.status is RoutineStatus.DONE, out.message
    assert started == [(0, "q_ok", 1), (1, "q_flaky", 1), (1, "q_flaky", 2), (2, "q_ok", 1)]
    assert wait_until(qapp, lambda: history.count() == 4)
    rows = history.query(routine_id=r.id)
    assert len(rows) == 4 and {x.routine_name for x in rows} == {"Chuỗi thử"}
    assert sorted((x.task_id, x.attempt, x.status) for x in rows) == [
        ("q_flaky", 1, "failed"), ("q_flaky", 2, "done"), ("q_ok", 1, "done"), ("q_ok", 1, "done")]
    failed = next(x for x in rows if x.status == "failed")
    assert failed.screenshots and Path(failed.screenshots[0]).is_file()  # failure screenshot kept
    assert not executor.is_active()


def test_executor_stop_on_error_and_unknown_feature(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    assert executor.start(chain(["missing", "q_ok"]))
    assert wait_until(qapp, lambda: bool(outcomes))
    assert outcomes[0].status is RoutineStatus.FAILED and len(outcomes[0].steps) == 1
    rec = history.query()[0]
    assert rec.task_id == "missing" and "Không bắt đầu được" in rec.message

    outcomes.clear()
    assert executor.start(chain(["missing", "q_ok"], stop_on_error=False))
    assert wait_until(qapp, lambda: bool(outcomes))
    assert outcomes[0].status is RoutineStatus.PARTIAL
    assert [s.status for s in outcomes[0].steps] == ["failed", "done"]


def test_executor_stop_while_a_step_runs(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    assert executor.start(chain(["q_slow", "q_ok"]))
    assert wait_until(qapp, lambda: runner.state == "running")
    t0 = time.monotonic()
    executor.stop()
    assert wait_until(qapp, lambda: bool(outcomes), 3.0)
    assert time.monotonic() - t0 < 1.5
    assert outcomes[0].status is RoutineStatus.STOPPED
    assert wait_until(qapp, lambda: history.count() == 1)
    assert history.query()[0].status == "stopped"


def test_executor_stop_cancels_a_pending_retry(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    r = chain(["q_flaky", "q_ok"])
    r.steps[0].retries = 3
    r.steps[0].retry_delay_s = 60
    FLAKY["q_flaky"] = 5
    assert executor.start(r)
    assert wait_until(qapp, lambda: executor.waiting_retry)
    executor.stop()
    assert outcomes and outcomes[0].status is RoutineStatus.STOPPED
    assert not executor.waiting_retry and not executor.is_active()


def test_executor_retries_when_the_run_cannot_start(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    calls = {"n": 0}

    def flaky_factory(task: Task, hooks: RunHooks) -> RunContext:
        calls["n"] += 1
        if calls["n"] == 1:
            from ngoalong.core.errors import WindowNotFoundError

            raise WindowNotFoundError("Chưa chọn cửa sổ game")
        return ctx_factory(task, hooks)

    executor.make_context = flaky_factory
    r = chain(["q_ok"])
    r.steps[0].retries = 1
    assert executor.start(r)
    assert wait_until(qapp, lambda: bool(outcomes))
    assert outcomes[0].status is RoutineStatus.DONE and outcomes[0].steps[0].attempts == 2
    assert wait_until(qapp, lambda: history.count() == 2)
    assert {x.status for x in history.query()} == {"failed", "done"}


def test_step_time_limit_stops_an_endless_step_and_the_chain_goes_on(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    r = chain(["q_endless", "q_ok"])
    r.steps[0].max_duration_s = 0.3
    t0 = time.monotonic()
    assert executor.start(r)
    assert wait_until(qapp, lambda: bool(outcomes), 8.0)
    out = outcomes[0]
    assert out.status is RoutineStatus.DONE, out.message
    assert [(s.feature_id, s.status) for s in out.steps] == [("q_endless", "done"), ("q_ok", "done")]
    assert "đủ thời gian" in out.steps[0].message
    assert time.monotonic() - t0 < 5.0
    assert not executor.is_active()


def test_user_stop_during_a_timed_step_still_stops_the_routine(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    r = chain(["q_endless", "q_ok"])
    r.steps[0].max_duration_s = 60
    assert executor.start(r)
    assert wait_until(qapp, lambda: runner.state == "running")
    executor.stop()
    assert wait_until(qapp, lambda: bool(outcomes), 3.0)
    assert outcomes[0].status is RoutineStatus.STOPPED and len(outcomes[0].steps) == 1


def test_pending_retry_waits_for_a_busy_runner_without_using_an_attempt(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    r = chain(["q_flaky", "q_ok"])
    r.steps[0].retries = 1
    r.steps[0].retry_delay_s = 0.3
    FLAKY["q_flaky"] = 1
    assert executor.start(r)
    assert wait_until(qapp, lambda: executor.waiting_retry)
    runner.start(_Slow(), ctx_factory)  # a manual run grabs the runner during the retry wait
    assert wait_until(qapp, lambda: runner.state == "running")
    time.sleep(0.5)
    qapp.processEvents()
    assert executor.is_active() and outcomes == []  # still waiting, not failed
    runner.stop()
    assert wait_until(qapp, lambda: bool(outcomes), 8.0)
    out = outcomes[0]
    assert out.status is RoutineStatus.DONE, out.message
    assert out.steps[0].attempts == 2  # the busy wait did not use up the last attempt


def test_step_time_limit_model_and_endless_params() -> None:
    from ngoalong.engine.scheduler import runs_until_stopped
    from ngoalong.features.tham_bao import ThamBaoParams

    assert RoutineStep(feature_id="x", max_duration_s=0).max_duration_s is None
    assert RoutineStep(feature_id="x", max_duration_s=90).max_duration_s == 90
    with pytest.raises(ValidationError):
        RoutineStep(feature_id="x", max_duration_s=-5)
    assert runs_until_stopped(ThamBaoParams()) and not runs_until_stopped(ThamBaoParams(max_rounds=3))
    assert runs_until_stopped({"rounds": 0}) and not runs_until_stopped({"spins": 0})


def test_executor_ignores_manual_runs(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    runner.start(_Ok(), ctx_factory)
    assert wait_until(qapp, lambda: not runner.is_running() and runner.last_result is not None)
    qapp.processEvents()
    assert outcomes == [] and not executor.is_active()
    assert wait_until(qapp, lambda: history.count() == 1) and history.query()[0].routine_id == ""


def test_service_runs_due_routines_and_queues_while_busy(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    store = RoutineStore(None)
    r = store.upsert(chain(["q_ok"], schedule=Schedule(times=["08:00"])), now=at(0, 1, 0))
    service = SchedulerService(Scheduler(store), executor, runner, tick_ms=60_000, clock=lambda: at(0, 8, 1))
    # busy with a manual run at 08:00: the routine waits in the queue
    runner.start(_Slow(), ctx_factory)
    assert wait_until(qapp, lambda: runner.state == "running")
    service.tick(at(0, 8, 0, 5))
    assert [q.routine_id for q in service.queued()] == [r.id] and not executor.is_active()
    runner.stop()
    # the queue drains when the runner becomes idle
    assert wait_until(qapp, lambda: bool(outcomes))
    assert outcomes[0].routine_id == r.id and outcomes[0].status is RoutineStatus.DONE
    assert service.queued() == []
    service.tick(at(0, 8, 1))  # the slot already fired
    qapp.processEvents()
    assert len(outcomes) == 1


def test_service_drops_routines_that_waited_too_long(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    store = RoutineStore(None)
    r = store.upsert(chain(["q_ok"], schedule=Schedule(times=["08:00"])), now=at(0, 1, 0))
    service = SchedulerService(Scheduler(store), executor, runner, max_wait_s=60)
    logs: list = []
    service.log.connect(lambda level, msg: logs.append((level, msg)))
    service.enqueue(r.id, "schedule", now=at(0, 8, 0))
    service.drain(at(0, 8, 5))
    assert service.queued() == [] and not executor.is_active()
    assert any(level == "warning" and "Bỏ lượt" in msg for level, msg in logs)


def test_service_run_now(qapp, rig) -> None:
    runner, executor, history, outcomes = rig
    store = RoutineStore(None)
    r = store.upsert(chain(["q_ok"]))
    service = SchedulerService(Scheduler(store), executor, runner)
    assert service.run_now("nope") == "missing"
    assert service.run_now(r.id) == "started"
    assert service.run_now(r.id) == "started" and service.queued() == []  # already running: not queued twice
    other = store.upsert(chain(["q_ok"], name="Khác"))
    assert service.run_now(other.id) == "queued"  # waits for the first routine
    assert wait_until(qapp, lambda: len(outcomes) == 2)
    assert [o.routine_id for o in outcomes] == [r.id, other.id]
