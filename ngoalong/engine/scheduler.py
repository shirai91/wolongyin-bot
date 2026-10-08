"""Routine chains and the scheduler (docs/06 P3 "Routine chains and scheduler").

A ``Routine`` is a saved sequence of feature steps (e.g. Thám Bảo -> Cày cấp chúa công -> Tháp Thí
Luyện) with a schedule (times of day, optional weekdays), per-step retries and a stop-on-error
option. Routines are saved in ``data/routines.json``.

Layers (the first three are Qt-free and unit-tested without the game):

* ``Schedule``: the next / latest time slot of a schedule.
* ``RoutineStore``: JSON persistence (atomic writes; a broken file is kept as ``.bak``).
* ``Scheduler``: which routines are due now (each slot fires once, within a grace window).
* ``RoutineRun``: the decision state machine of one routine run: next step, retry (after a
  delay), skip on error or stop. ``run_routine`` drives it synchronously (tests, scripts).
* ``RoutineExecutor`` (Qt): drives a ``RoutineRun`` on the app's ``TaskRunner``; retry delays are
  ``QTimer`` single shots, so nothing blocks the UI and Stop cancels a pending retry at once.
* ``SchedulerService`` (Qt): ticks a ``QTimer``, queues due routines and starts them when the
  runner is idle (a queued routine that waits too long is dropped and reported).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time as _time_mod
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ngoalong.core.errors import Cancelled
from ngoalong.engine.control import RunControl
from ngoalong.engine.history import RunRecord
from ngoalong.engine.registry import get_feature
from ngoalong.engine.runner import ContextFactory, RunResult, RunStatus
from ngoalong.engine.task import Task, TaskParams

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3])\s*[:hH.]\s*([0-5]\d)\s*$")
WEEKDAY_TEXT = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")  # Monday = 0 (datetime.weekday)
MAX_RETRIES = 10
MAX_RETRY_DELAY_S = 3600.0
MAX_STEP_DURATION_S = 24 * 3600.0  # a step time limit (``RoutineStep.max_duration_s``) up to a day
# Params fields where 0 means "loop until Stop" (Thám Bảo, Võ Khôi Tháp, AutoClick): such a step
# never ends on its own, so in a routine it needs a time limit.
UNTIL_STOP_FIELDS = ("max_rounds", "rounds")
BUSY_POLL_MS = 1000  # a step whose runner is busy (a manual run) re-checks this often...
RUNNER_BUSY_MAX_WAIT_S = 1800.0  # ...for at most this long, then the step fails
DEFAULT_GRACE_S = 300.0  # a slot missed by less than this (app just started, runner busy) still runs
DEFAULT_MAX_WAIT_S = 1800.0  # a queued routine waiting longer than this is dropped
DEFAULT_TICK_MS = 15_000


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse_time(text: str) -> str:
    """Normalise a time of day: ``"8:05"``, ``"08:05"``, ``"8h05"`` -> ``"08:05"``; raises ``ValueError``
    otherwise (minutes need two digits, so ``"8:5"`` is rejected)."""
    m = _TIME_RE.match(text)
    if not m:
        raise ValueError(f"giờ không hợp lệ: {text!r} (dạng HH:MM, ví dụ 08:30)")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_times(text: str) -> list[str]:
    """A comma/space separated list of times, normalised, sorted and without duplicates."""
    parts = [p for p in re.split(r"[,;\s]+", text.strip()) if p]
    return sorted({parse_time(p) for p in parts})


# --- models ---------------------------------------------------------------------------------------
class Schedule(BaseModel):
    """Times of day (``HH:MM``, local) on the given weekdays (0 = Monday; empty = every day)."""

    model_config = ConfigDict(extra="ignore")

    times: list[str] = Field(default_factory=list)
    weekdays: list[int] = Field(default_factory=list)

    @field_validator("times")
    @classmethod
    def _times(cls, v: list[str]) -> list[str]:
        return sorted({parse_time(t) for t in v})

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        bad = [d for d in v if not 0 <= int(d) <= 6]
        if bad:
            raise ValueError(f"thứ không hợp lệ: {bad} (0 = thứ Hai ... 6 = Chủ nhật)")
        return sorted({int(d) for d in v})

    @property
    def active(self) -> bool:
        return bool(self.times)

    def _clock_times(self) -> list[time]:
        return [time(int(t[:2]), int(t[3:])) for t in self.times]

    def _runs_on(self, d: date) -> bool:
        return not self.weekdays or d.weekday() in self.weekdays

    def next_after(self, after: datetime) -> datetime | None:
        """The first slot strictly after ``after`` (``None`` without times)."""
        clock = self._clock_times()
        if not clock:
            return None
        for i in range(8):
            d = after.date() + timedelta(days=i)
            if not self._runs_on(d):
                continue
            for t in clock:
                slot = datetime.combine(d, t)
                if slot > after:
                    return slot
        return None

    def latest_at_or_before(self, at: datetime) -> datetime | None:
        """The last slot at or before ``at`` (``None`` without times)."""
        clock = self._clock_times()
        if not clock:
            return None
        for i in range(8):
            d = at.date() - timedelta(days=i)
            if not self._runs_on(d):
                continue
            for t in reversed(clock):
                slot = datetime.combine(d, t)
                if slot <= at:
                    return slot
        return None

    def describe(self) -> str:
        if not self.times:
            return "không đặt giờ"
        days = "mỗi ngày" if not self.weekdays or len(self.weekdays) == 7 else \
            ", ".join(WEEKDAY_TEXT[d] for d in self.weekdays)
        return f"{', '.join(self.times)} ({days})"


class RoutineStep(BaseModel):
    """One feature run of a routine. ``params`` overrides the feature's saved params (``None`` =
    use the params saved in the "Tính năng" tab at the time the step starts).

    ``max_duration_s`` (``None`` / 0 = no limit) stops the step when its time is up and the routine
    goes on with the next step (the step counts as done): this is how a feature that loops until
    Stop (Thám Bảo, Võ Khôi Tháp, AutoClick with 0 rounds) takes part in a chain."""

    model_config = ConfigDict(extra="ignore")

    feature_id: str
    params: dict[str, Any] | None = None
    retries: int = Field(0, ge=0, le=MAX_RETRIES)
    retry_delay_s: float = Field(10.0, ge=0.0, le=MAX_RETRY_DELAY_S)
    max_duration_s: float | None = Field(None, gt=0.0, le=MAX_STEP_DURATION_S)
    enabled: bool = True

    @field_validator("feature_id")
    @classmethod
    def _fid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("bước thiếu tính năng")
        return v

    @field_validator("max_duration_s", mode="before")
    @classmethod
    def _no_limit(cls, v: Any) -> Any:
        return None if v in (None, 0, 0.0, "") else v


def runs_until_stopped(params: TaskParams | Mapping[str, Any]) -> bool:
    """True when ``params`` make the feature loop until Stop (a 0 in ``UNTIL_STOP_FIELDS``)."""
    data = params.model_dump() if isinstance(params, BaseModel) else dict(params)
    return any(f in data and data[f] == 0 for f in UNTIL_STOP_FIELDS)


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    if s % 60 == 0:
        return f"{s // 60} phút"
    return f"{s} giây" if s < 60 else f"{s // 60} phút {s % 60} giây"


def new_routine_id() -> str:
    return uuid.uuid4().hex[:12]


class Routine(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    id: str = Field(default_factory=new_routine_id)
    name: str
    steps: list[RoutineStep] = Field(default_factory=list)
    schedule: Schedule = Field(default_factory=Schedule)
    enabled: bool = True  # the schedule is active (Run now works either way)
    stop_on_error: bool = True  # a step that fails after its retries ends the routine
    updated_at: datetime = Field(default_factory=_now)  # slots before this never fire
    last_fired_at: datetime | None = None  # the last schedule slot that started this routine

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("chuỗi tác vụ cần có tên")
        return v

    @property
    def active_steps(self) -> list[tuple[int, RoutineStep]]:
        return [(i, s) for i, s in enumerate(self.steps) if s.enabled]


class RoutineFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    scheduler_enabled: bool = True
    routines: list[Routine] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> RoutineFile:
        seen: set[str] = set()
        for r in self.routines:
            if r.id in seen:
                r.id = new_routine_id()
            seen.add(r.id)
        return self


# --- persistence ----------------------------------------------------------------------------------
class RoutineStore:
    """Routines in a JSON file (``None`` path = memory only). Thread-safe; returns copies."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> RoutineFile:
        p = self.path
        if p is None or not p.is_file():
            return RoutineFile()
        try:
            return RoutineFile.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            backup = p.with_name(p.name + ".bak")
            log.error("routine file %s is invalid (%s); kept as %s", p, exc, backup)
            try:
                os.replace(p, backup)
            except OSError:
                pass
            return RoutineFile()

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(self._data.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()

    # --- scheduler switch -----------------------------------------------------------------------
    @property
    def scheduler_enabled(self) -> bool:
        with self._lock:
            return self._data.scheduler_enabled

    def set_scheduler_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._data.scheduler_enabled = bool(enabled)
            self._save()

    # --- routines -------------------------------------------------------------------------------
    def routines(self) -> list[Routine]:
        with self._lock:
            return [r.model_copy(deep=True) for r in self._data.routines]

    def get(self, routine_id: str) -> Routine | None:
        with self._lock:
            r = next((r for r in self._data.routines if r.id == routine_id), None)
            return r.model_copy(deep=True) if r is not None else None

    def upsert(self, routine: Routine, now: datetime | None = None) -> Routine:
        """Add or replace a routine. Its ``updated_at`` becomes ``now``, so a slot that is already
        past when the routine is saved does not fire right away."""
        with self._lock:
            old = next((r for r in self._data.routines if r.id == routine.id), None)
            new = Routine.model_validate(routine.model_dump())
            new.updated_at = now or _now()
            if old is not None and new.last_fired_at is None:
                new.last_fired_at = old.last_fired_at
            routines = [r for r in self._data.routines if r.id != routine.id]
            if old is not None:
                idx = self._data.routines.index(old)
                routines.insert(idx, new)
            else:
                routines.append(new)
            self._data.routines = routines
            self._save()
            return new.model_copy(deep=True)

    def delete(self, routine_id: str) -> bool:
        with self._lock:
            before = len(self._data.routines)
            self._data.routines = [r for r in self._data.routines if r.id != routine_id]
            if len(self._data.routines) == before:
                return False
            self._save()
            return True

    def mark_fired(self, routine_id: str, slot: datetime) -> None:
        with self._lock:
            for r in self._data.routines:
                if r.id == routine_id:
                    r.last_fired_at = slot
                    self._save()
                    return


# --- scheduling -----------------------------------------------------------------------------------
class Scheduler:
    """Decides which routines are due. Each slot fires at most once (persisted ``last_fired_at``)."""

    def __init__(self, store: RoutineStore, grace_s: float = DEFAULT_GRACE_S) -> None:
        self.store = store
        self.grace = timedelta(seconds=max(0.0, grace_s))

    def due(self, now: datetime | None = None) -> list[tuple[Routine, datetime]]:
        """Routines whose latest slot is in ``(now - grace, now]`` and has not fired; marks them."""
        now = now or _now()
        if not self.store.scheduler_enabled:
            return []
        out: list[tuple[Routine, datetime]] = []
        for r in self.store.routines():
            if not (r.enabled and r.active_steps and r.schedule.active):
                continue
            slot = r.schedule.latest_at_or_before(now)
            if slot is None or now - slot > self.grace or slot <= r.updated_at:
                continue
            if r.last_fired_at is not None and slot <= r.last_fired_at:
                continue
            self.store.mark_fired(r.id, slot)
            out.append((r, slot))
        return out

    def upcoming(self, now: datetime | None = None) -> list[tuple[Routine, datetime]]:
        """The next slot of every scheduled routine, soonest first."""
        now = now or _now()
        if not self.store.scheduler_enabled:
            return []
        nxt = [(r, r.schedule.next_after(now)) for r in self.store.routines()
               if r.enabled and r.active_steps and r.schedule.active]
        return sorted(((r, t) for r, t in nxt if t is not None), key=lambda x: x[1])


# --- one routine run (state machine) --------------------------------------------------------------
class RoutineStatus(StrEnum):
    DONE = "done"  # every step succeeded
    PARTIAL = "partial"  # finished, but some step failed (stop_on_error off)
    FAILED = "failed"  # a step failed and stop_on_error ended the routine
    STOPPED = "stopped"  # stopped by the user


ROUTINE_STATUS_TEXT: dict[str, str] = {
    RoutineStatus.DONE.value: "Hoàn thành",
    RoutineStatus.PARTIAL.value: "Xong (có bước lỗi)",
    RoutineStatus.FAILED.value: "Lỗi",
    RoutineStatus.STOPPED.value: "Đã dừng",
}


@dataclass(slots=True)
class StepOutcome:
    index: int
    feature_id: str
    status: str  # RunStatus value
    attempts: int
    message: str


@dataclass(slots=True)
class RoutineOutcome:
    routine_id: str
    routine_name: str
    status: RoutineStatus
    message: str
    started_at: datetime
    ended_at: datetime
    steps: list[StepOutcome] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StartStep:
    index: int  # index in ``Routine.steps``
    step: RoutineStep
    attempt: int  # 1 = first try
    delay_s: float = 0.0  # wait this long first (a retry)


@dataclass(frozen=True, slots=True)
class Finish:
    outcome: RoutineOutcome


Action = StartStep | Finish


class RoutineRun:
    """What to do next in one routine run, given each step's result."""

    def __init__(self, routine: Routine, clock: Callable[[], datetime] = _now) -> None:
        self.routine = routine
        self._clock = clock
        self.started_at = clock()
        self._queue = routine.active_steps
        self._pos = 0
        self._attempt = 0
        self.outcomes: list[StepOutcome] = []
        self._failed_steps = 0
        self.result: RoutineOutcome | None = None

    @property
    def finished(self) -> bool:
        return self.result is not None

    @property
    def current(self) -> StartStep | None:
        if self.finished or self._pos >= len(self._queue):
            return None
        idx, step = self._queue[self._pos]
        return StartStep(idx, step, max(1, self._attempt))

    def _finish(self, status: RoutineStatus, message: str) -> Finish:
        if self.result is None:
            self.result = RoutineOutcome(self.routine.id, self.routine.name, status, message,
                                         self.started_at, self._clock(), list(self.outcomes))
        return Finish(self.result)

    def _next_step(self) -> Action:
        if self._pos >= len(self._queue):
            if not self._queue:
                return self._finish(RoutineStatus.DONE, "Chuỗi không có bước nào được bật")
            if self._failed_steps:
                return self._finish(RoutineStatus.PARTIAL,
                                    f"Xong {len(self._queue)} bước, {self._failed_steps} bước lỗi")
            return self._finish(RoutineStatus.DONE, f"Xong {len(self._queue)} bước")
        idx, step = self._queue[self._pos]
        self._attempt = 1
        return StartStep(idx, step, 1)

    def first(self) -> Action:
        if self.finished:
            return Finish(self.result)  # type: ignore[arg-type]
        return self._next_step()

    def report(self, status: RunStatus | str, message: str = "", retryable: bool = True) -> Action:
        """The current step ended with ``status``; returns what to do next."""
        if self.finished:
            return Finish(self.result)  # type: ignore[arg-type]
        cur = self.current
        if cur is None:
            return self._next_step()
        status = RunStatus(status)
        if status is RunStatus.STOPPED:
            self.outcomes.append(StepOutcome(cur.index, cur.step.feature_id, status.value, cur.attempt, message))
            return self._finish(RoutineStatus.STOPPED, "Đã dừng theo yêu cầu")
        if status is RunStatus.FAILED and retryable and cur.attempt <= cur.step.retries:
            self._attempt = cur.attempt + 1
            return StartStep(cur.index, cur.step, self._attempt, cur.step.retry_delay_s)
        self.outcomes.append(StepOutcome(cur.index, cur.step.feature_id, status.value, cur.attempt, message))
        self._pos += 1
        if status is RunStatus.FAILED:
            self._failed_steps += 1
            if self.routine.stop_on_error:
                return self._finish(
                    RoutineStatus.FAILED,
                    f"Bước {cur.index + 1} ({cur.step.feature_id}) lỗi sau {cur.attempt} lần: {message}",
                )
        return self._next_step()

    def stop(self) -> Finish:
        """Stopped between steps (e.g. while waiting to retry)."""
        return self._finish(RoutineStatus.STOPPED, "Đã dừng theo yêu cầu")


StepRunner = Callable[[StartStep], tuple[RunStatus, str]]


def run_routine(routine: Routine, run_step: StepRunner, control: RunControl | None = None,
                clock: Callable[[], datetime] = _now) -> RoutineOutcome:
    """Run ``routine`` synchronously; ``run_step`` runs one attempt (retry waits are cancellable)."""
    control = control or RunControl()
    run = RoutineRun(routine, clock)
    action = run.first()
    while isinstance(action, StartStep):
        try:
            if action.delay_s > 0:
                control.sleep(action.delay_s)
            control.checkpoint()
        except Cancelled:
            return run.stop().outcome
        try:
            status, message = run_step(action)
        except Cancelled:
            status, message = RunStatus.STOPPED, "Đã dừng theo yêu cầu"
        action = run.report(status, message)
    return action.outcome


# --- building a step's task -----------------------------------------------------------------------
class StepError(ValueError):
    """A step cannot be started (unknown feature, invalid params); retrying would not help."""


FeatureLookup = Callable[[str], type[Task]]
SavedParams = Callable[[type[Task]], TaskParams]


def build_step_task(step: RoutineStep, saved_params: SavedParams,
                    lookup: FeatureLookup = get_feature) -> Task:
    """The ``Task`` for a step: the saved params of the feature, updated with ``step.params``."""
    try:
        cls = lookup(step.feature_id)
    except (KeyError, ValueError) as exc:
        raise StepError(f"không có tính năng {step.feature_id!r}") from exc
    base = saved_params(cls).model_dump()
    if step.params:
        base.update(step.params)
    try:
        return cls(cls.Params.model_validate(base))
    except ValidationError as exc:
        raise StepError(f"thông số của {cls.title} không hợp lệ: {exc.errors()[0].get('msg', exc)}") from exc


# --- Qt: executor and scheduler service -----------------------------------------------------------
TaskBuilder = Callable[[RoutineStep], Task]


class RoutineExecutor(QObject):
    """Runs one routine at a time on a ``TaskRunner`` (UI thread object)."""

    routine_started = Signal(str, str)  # routine id, name
    step_started = Signal(str, int, str, int)  # routine id, step index, feature id, attempt
    routine_finished = Signal(object)  # RoutineOutcome
    log = Signal(str, str)  # level, message

    def __init__(self, runner: Any, build_task: TaskBuilder, make_context: ContextFactory,
                 recorder: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.build_task = build_task
        self.make_context = make_context
        self.recorder = recorder  # HistoryRecorder: records steps that could not start
        self._run: RoutineRun | None = None
        self._token = ""
        self._pending: StartStep | None = None
        self._stop_requested = False
        self.last_outcome: RoutineOutcome | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_pending)
        # The time limit of the running step (``RoutineStep.max_duration_s``).
        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.timeout.connect(self._on_step_timeout)
        self._step_timed_out = False
        self._busy_since: float | None = None  # monotonic time a step started waiting for the runner
        runner.finished.connect(self._on_run_finished)

    # --- state ----------------------------------------------------------------------------------
    def is_active(self) -> bool:
        return self._run is not None

    @property
    def routine(self) -> Routine | None:
        return self._run.routine if self._run is not None else None

    @property
    def current_step(self) -> StartStep | None:
        return self._run.current if self._run is not None else None

    @property
    def waiting_retry(self) -> bool:
        return self._timer.isActive()

    # --- control --------------------------------------------------------------------------------
    def start(self, routine: Routine) -> bool:
        """Start ``routine``; ``False`` when a routine or a task is already running."""
        if self._run is not None or self.runner.is_running():
            return False
        self._run = RoutineRun(routine)
        self._token = uuid.uuid4().hex
        self._stop_requested = False
        self.log.emit("info", f"Bắt đầu chuỗi: {routine.name}")
        self.routine_started.emit(routine.id, routine.name)
        self._dispatch(self._run.first())
        return True

    @Slot()
    def stop(self) -> None:
        """Stop the routine: the running step is stopped, a pending retry is cancelled."""
        run = self._run
        if run is None:
            return
        self._stop_requested = True
        if self._timer.isActive() or not self.runner.is_running():
            self._timer.stop()
            self._pending = None
            self._finish(run.stop().outcome)
        else:
            self.runner.stop()  # the STOPPED result ends the routine in _on_run_finished

    # --- internals ------------------------------------------------------------------------------
    def _dispatch(self, action: Action) -> None:
        # Loop rather than recurse: steps that fail to start go straight to the next action.
        while self._run is not None:
            if isinstance(action, Finish):
                self._finish(action.outcome)
                return
            if self._stop_requested:
                self._finish(self._run.stop().outcome)
                return
            if action.delay_s > 0:
                self._pending = action
                self.log.emit("warning", f"Thử lại bước {action.index + 1} ({action.step.feature_id}) "
                                         f"lần {action.attempt} sau {action.delay_s:.0f}s")
                self._timer.start(max(0, int(action.delay_s * 1000)))
                return
            nxt = self._start_step(action)
            if nxt is None:
                return  # running; the result arrives through runner.finished
            action = nxt

    @Slot()
    def _start_pending(self) -> None:
        action, self._pending = self._pending, None
        if action is None or self._run is None:
            return
        self._dispatch(StartStep(action.index, action.step, action.attempt))

    def _meta(self, a: StartStep) -> dict[str, object]:
        r = self._run.routine if self._run is not None else None
        return {"routine_token": self._token, "routine_id": r.id if r else "",
                "routine_name": r.name if r else "", "step_index": a.index, "attempt": a.attempt}

    def _start_step(self, a: StartStep) -> Action | None:
        """Start one attempt; returns the next action when it could not start."""
        run = self._run
        assert run is not None
        if self.runner.is_running():
            # Another run (e.g. a manual one) holds the runner: wait for it without using up an
            # attempt, bounded by RUNNER_BUSY_MAX_WAIT_S.
            now = _time_mod.monotonic()
            if self._busy_since is None:
                self._busy_since = now
                self.log.emit("warning", f"Chuỗi {run.routine.name}: bước {a.index + 1} chờ tác vụ "
                                         "đang chạy kết thúc")
            if now - self._busy_since < RUNNER_BUSY_MAX_WAIT_S:
                self._pending = StartStep(a.index, a.step, a.attempt)
                self._timer.start(BUSY_POLL_MS)
                return None
            self._busy_since = None
            msg = f"một tác vụ khác vẫn đang chạy sau {format_duration(RUNNER_BUSY_MAX_WAIT_S)}"
            self._record_start_failure(a, msg)
            return run.report(RunStatus.FAILED, msg, retryable=False)
        self._busy_since = None
        self.step_started.emit(run.routine.id, a.index, a.step.feature_id, a.attempt)
        try:
            task = self.build_task(a.step)
        except StepError as exc:
            self._record_start_failure(a, str(exc))
            return run.report(RunStatus.FAILED, str(exc), retryable=False)
        except Exception as exc:  # a broken builder must not leave the routine hanging
            log.exception("building step %s failed", a.step.feature_id)
            self._record_start_failure(a, str(exc) or type(exc).__name__)
            return run.report(RunStatus.FAILED, str(exc) or type(exc).__name__, retryable=False)
        self.log.emit("info", f"Chuỗi {run.routine.name}: bước {a.index + 1} - {task.title}"
                              + (f" (lần {a.attempt})" if a.attempt > 1 else ""))
        try:
            self.runner.start(task, self.make_context, meta=self._meta(a))
        except Exception as exc:  # no window, runner busy...: a retry may succeed
            msg = str(exc) or type(exc).__name__
            self.log.emit("error", f"Không chạy được {task.title}: {msg}")
            self._record_start_failure(a, msg, task.title)
            return run.report(RunStatus.FAILED, msg)
        self._step_timed_out = False
        if a.step.max_duration_s:
            self._step_timer.start(max(1, int(a.step.max_duration_s * 1000)))
        return None

    @Slot()
    def _on_step_timeout(self) -> None:
        """The running step reached its time limit: stop it; the routine goes on (DONE)."""
        if self._run is None or not self.runner.is_running():
            return
        cur = self._run.current
        self._step_timed_out = True
        if cur is not None and cur.step.max_duration_s:
            self.log.emit("info", f"Chuỗi {self._run.routine.name}: bước {cur.index + 1} đã đủ thời gian "
                                  f"({format_duration(cur.step.max_duration_s)}), sang bước sau")
        self.runner.stop()

    def _record_start_failure(self, a: StartStep, message: str, title: str = "") -> None:
        if self.recorder is None or self._run is None:
            return
        now = _now()
        self.recorder.add(RunRecord(
            task_id=a.step.feature_id, task_title=title or a.step.feature_id, started_at=now, ended_at=now,
            status=RunStatus.FAILED.value, message=f"Không bắt đầu được: {message}",
            routine_id=self._run.routine.id, routine_name=self._run.routine.name, attempt=a.attempt,
        ))

    @Slot(object)
    def _on_run_finished(self, result: RunResult) -> None:
        if self._run is None or (result.meta or {}).get("routine_token") != self._token:
            return  # a manual run, or a run of an earlier routine
        self._step_timer.stop()
        timed_out, self._step_timed_out = self._step_timed_out, False
        status, message = RunStatus(result.status), result.message
        if timed_out and status is RunStatus.STOPPED and not self._stop_requested:
            status, message = RunStatus.DONE, "Đã đủ thời gian của bước"
        self._dispatch(self._run.report(status, message))

    def _finish(self, outcome: RoutineOutcome) -> None:
        self._timer.stop()
        self._step_timer.stop()
        self._step_timed_out = False
        self._busy_since = None
        self._pending = None
        self._run = None
        self._token = ""
        self._stop_requested = False
        self.last_outcome = outcome
        level = {RoutineStatus.DONE: "info", RoutineStatus.STOPPED: "warning"}.get(outcome.status, "error")
        self.log.emit(level, f"Chuỗi {outcome.routine_name}: "
                             f"{ROUTINE_STATUS_TEXT[outcome.status.value]} - {outcome.message}")
        self.routine_finished.emit(outcome)


@dataclass(slots=True)
class QueuedRoutine:
    routine_id: str
    queued_at: datetime
    reason: str  # "schedule" | "manual"


class SchedulerService(QObject):
    """Starts due routines on the executor; queues them while the runner is busy."""

    queue_changed = Signal()
    log = Signal(str, str)  # level, message

    def __init__(self, scheduler: Scheduler, executor: RoutineExecutor, runner: Any,
                 tick_ms: int = DEFAULT_TICK_MS, max_wait_s: float = DEFAULT_MAX_WAIT_S,
                 clock: Callable[[], datetime] = _now, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.executor = executor
        self.runner = runner
        self.max_wait = timedelta(seconds=max(0.0, max_wait_s))
        self._clock = clock
        self._queue: list[QueuedRoutine] = []
        self._timer = QTimer(self)
        self._timer.setInterval(max(100, int(tick_ms)))
        self._timer.timeout.connect(self.tick)
        executor.routine_finished.connect(self._on_idle)
        runner.state_changed.connect(self._on_runner_state)

    @property
    def store(self) -> RoutineStore:
        return self.scheduler.store

    # --- timer ----------------------------------------------------------------------------------
    def start(self) -> None:
        self._timer.start()
        self.tick()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    # --- queue ----------------------------------------------------------------------------------
    def queued(self) -> list[QueuedRoutine]:
        return list(self._queue)

    def busy(self) -> bool:
        return self.executor.is_active() or self.runner.is_running()

    def enqueue(self, routine_id: str, reason: str = "schedule", now: datetime | None = None) -> None:
        if any(q.routine_id == routine_id for q in self._queue):
            return
        cur = self.executor.routine
        if cur is not None and cur.id == routine_id:
            return  # already running
        self._queue.append(QueuedRoutine(routine_id, now or self._clock(), reason))
        self.queue_changed.emit()

    def run_now(self, routine_id: str) -> str:
        """``"started"``, ``"queued"`` (something is running) or ``"missing"``."""
        if self.store.get(routine_id) is None:
            return "missing"
        self.enqueue(routine_id, "manual")
        self.drain()
        cur = self.executor.routine
        return "started" if cur is not None and cur.id == routine_id else "queued"

    def remove_queued(self, routine_id: str) -> None:
        self._queue = [q for q in self._queue if q.routine_id != routine_id]
        self.queue_changed.emit()

    @Slot()
    def tick(self, now: datetime | None = None) -> None:
        now = now or self._clock()
        for routine, slot in self.scheduler.due(now):
            self.log.emit("info", f"Đến giờ chạy chuỗi {routine.name} ({slot:%H:%M})")
            self.enqueue(routine.id, "schedule", now)
        self.drain(now)

    def drain(self, now: datetime | None = None) -> None:
        """Start the first queued routine when nothing runs; drop routines that waited too long."""
        now = now or self._clock()
        changed = False
        while self._queue and not self.busy():
            q = self._queue.pop(0)
            changed = True
            routine = self.store.get(q.routine_id)
            if routine is None:
                continue
            if q.reason == "schedule" and now - q.queued_at > self.max_wait:
                self.log.emit("warning", f"Bỏ lượt chuỗi {routine.name}: chờ quá "
                                         f"{self.max_wait.total_seconds() / 60:.0f} phút vì đang bận")
                continue
            if self.executor.start(routine):
                break
            self._queue.insert(0, q)  # could not start after all: keep it
            changed = False
            break
        if changed:
            self.queue_changed.emit()

    @Slot(object)
    def _on_idle(self, _outcome: object = None) -> None:
        QTimer.singleShot(0, self.drain)

    @Slot(str)
    def _on_runner_state(self, state: str) -> None:
        if state == "idle":
            QTimer.singleShot(0, self.drain)


def saved_params_from(settings: Any) -> SavedParams:
    """``SavedParams`` reading the params saved in ``Settings`` (the "Tính năng" forms)."""
    return lambda cls: settings.feature_params(cls)


def feature_titles(features: Mapping[str, type[Task]] | list[type[Task]]) -> dict[str, str]:
    items = features.values() if isinstance(features, Mapping) else features
    return {c.id: c.title for c in items}
