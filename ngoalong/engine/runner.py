"""``TaskRunner``: runs one ``Task`` on a ``QThread`` with Start / Pause / Stop.

* ``execute(task, actor)`` is the Qt-free core: runs the task, maps the outcome to a ``RunResult``
  and ALWAYS saves a failure screenshot under ``logs/run-<id>/`` when the task fails.
* ``TaskRunner`` owns the worker thread and turns log / progress / status callbacks into Qt
  signals (emitted from the worker; Qt queues them to receivers in the UI thread).
* Each run gets ``logs/run-<id>/`` with ``run.log`` plus debug and failure PNGs.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ngoalong.core.errors import Cancelled, TaskStopped
from ngoalong.core.locator import CanvasFix
from ngoalong.debug.sink import new_run_dir
from ngoalong.engine.actor import Actor
from ngoalong.engine.control import RunControl
from ngoalong.engine.task import Task

log = logging.getLogger(__name__)


class RunStatus(StrEnum):
    DONE = "done"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RunResult:
    task_id: str
    status: RunStatus
    message: str
    run_dir: Path | None = None
    screenshot: Path | None = None
    error: BaseException | None = None
    duration_s: float = 0.0
    task_title: str = ""
    started_at: float = 0.0  # wall clock (time.time()) for the run history
    ended_at: float = 0.0
    counters: dict[str, int] = field(default_factory=dict)
    # Caller tags passed to ``TaskRunner.start(meta=...)`` (routine id, attempt...), returned as is.
    meta: dict[str, object] = field(default_factory=dict)


def _counters(actor: Actor) -> dict[str, int]:
    try:
        snap = actor.counter_snapshot()
    except Exception:  # broken counters must not break the result
        log.exception("could not read the run counters")
        return {}
    return {str(k): int(v) for k, v in snap.items()}


def execute(task: Task, actor: Actor, run_dir: Path | None = None,
            meta: dict[str, object] | None = None) -> RunResult:
    """Run ``task`` to completion, stop or failure. Never raises (except KeyboardInterrupt)."""
    started = time.monotonic()
    started_wall = time.time()
    actor.info(f"Bắt đầu: {task.title}")
    shot: Path | None = None
    error: BaseException | None = None
    try:
        message = task.run(actor) or "Hoàn thành"
        status = RunStatus.DONE
        actor.info(f"Xong: {message}")
    except Cancelled:
        status, message = RunStatus.STOPPED, "Đã dừng theo yêu cầu"
        actor.warn(message)
    except TaskStopped as exc:
        status, message = RunStatus.STOPPED, f"Đã dừng: {exc}"
        actor.warn(message)
    except Exception as exc:
        status, error = RunStatus.FAILED, exc
        message = f"Lỗi: {exc}" if str(exc) else f"Lỗi: {type(exc).__name__}"
        log.error("task %s failed\n%s", task.id, "".join(traceback.format_exception(exc)))
        try:
            shot = actor.save_failure(f"{type(exc).__name__}: {exc}")
        except Exception:
            log.exception("could not save the failure screenshot")
        actor.log(message + (f" (ảnh: {shot})" if shot else ""), "error")
    finally:
        try:
            task.on_stop(actor)
        except Exception:
            log.exception("on_stop of %s failed", task.id)
    return RunResult(
        task.id, status, message, run_dir, shot, error, time.monotonic() - started,
        task_title=getattr(task, "title", ""), started_at=started_wall, ended_at=time.time(),
        counters=_counters(actor), meta=dict(meta or {}),
    )


@dataclass
class RunHooks:
    """What the runner gives the actor factory: switches, run folder and callbacks."""

    run_id: str
    run_dir: Path
    control: RunControl
    on_log: Callable[[str, str], None]
    on_progress: Callable[[int, int | None], None]
    on_status: Callable[[CanvasFix | None, str | None], None]  # canvas, screen stack
    extra: dict[str, object] = field(default_factory=dict)


ActorFactory = Callable[[Task, RunHooks], Actor]


class _RunLog:
    """Appends ``run.log`` lines; the folder is created on the first line."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, level: str, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [{level.upper()}] {message}\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                log.exception("cannot write %s", self.path)


class _Worker(QThread):
    def __init__(self, runner: TaskRunner, task: Task, actor: Actor, run_dir: Path,
                 meta: dict[str, object] | None = None) -> None:
        super().__init__()
        self._runner = runner
        self._task = task
        self._actor = actor
        self._run_dir = run_dir
        self._meta = meta
        self.result: RunResult | None = None

    def run(self) -> None:  # worker thread
        self.result = execute(self._task, self._actor, self._run_dir, self._meta)
        self._runner._on_worker_done(self.result)


class TaskRunner(QObject):
    started = Signal(str)  # task id
    log = Signal(str, str)  # level, message
    progress = Signal(int, int)  # done, total (-1 = unknown)
    status = Signal(object, object)  # CanvasFix | None, str | None (the screen stack)
    state_changed = Signal(str)  # idle | running | paused | stopping
    finished = Signal(object)  # RunResult

    def __init__(self, log_dir: str | Path = "logs", parent: QObject | None = None, run_tag: str = "") -> None:
        super().__init__(parent)
        self.log_dir = Path(log_dir)
        # Part of every run id; several runners sharing ``log_dir`` (one per game window) use
        # different tags so two runs started in the same second never share a folder.
        self.run_tag = run_tag
        self._worker: _Worker | None = None
        self._control: RunControl | None = None
        self._state = "idle"
        self.last_result: RunResult | None = None
        self.current_task: Task | None = None

    # --- state ----------------------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def is_running(self) -> bool:
        return self._worker is not None and not self._worker.isFinished() and self._state != "idle"

    def _new_run_dir(self, task: Task) -> tuple[str, Path]:
        tag = f"{self.run_tag}-" if self.run_tag else ""
        base = f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}{task.id}"
        run_id, n = base, 1
        while new_run_dir(self.log_dir, run_id).exists():
            n += 1
            run_id = f"{base}-{n}"
        return run_id, new_run_dir(self.log_dir, run_id)

    # --- control --------------------------------------------------------------------------------
    def start(self, task: Task, make_actor: ActorFactory,
              meta: dict[str, object] | None = None) -> RunHooks:
        """Build the actor (in this thread) and start the task on a worker thread.

        ``meta`` (routine id, attempt...) is handed back unchanged in ``RunResult.meta``.
        """
        if self.is_running():
            raise RuntimeError("một tác vụ khác đang chạy")
        if self._worker is not None:
            self._worker.wait(2000)
        run_id, run_dir = self._new_run_dir(task)
        control = RunControl()
        run_log = _RunLog(run_dir / "run.log")

        def on_log(level: str, message: str) -> None:
            run_log.write(level, message)
            self.log.emit(level, message)

        def on_progress(done: int, total: int | None) -> None:
            self.progress.emit(int(done), -1 if total is None else int(total))

        def on_status(canvas: CanvasFix | None, screen: str | None) -> None:
            self.status.emit(canvas, screen)

        hooks = RunHooks(run_id, run_dir, control, on_log, on_progress, on_status, dict(meta or {}))
        actor = make_actor(task, hooks)
        self._control = control
        self.current_task = task
        self._worker = _Worker(self, task, actor, run_dir, dict(meta or {}))
        self._set_state("running")
        self.started.emit(task.id)
        self._worker.start()
        return hooks

    def pause(self) -> None:
        if self._control is not None and self.is_running():
            self._control.pause()
            self._set_state("paused")

    def resume(self) -> None:
        if self._control is not None and self.is_running():
            self._control.resume()
            self._set_state("running")

    def toggle_pause(self) -> None:
        if self._state == "paused":
            self.resume()
        else:
            self.pause()

    def stop(self) -> None:
        if self._control is not None and self.is_running():
            self._control.stop()
            self._set_state("stopping")

    def wait(self, timeout_s: float = 5.0) -> bool:
        """Block until the worker ends (tests and shutdown); True when it ended in time."""
        if self._worker is None:
            return True
        return bool(self._worker.wait(max(0, int(timeout_s * 1000))))

    def shutdown(self, timeout_s: float = 2.0) -> None:
        self.stop()
        self.wait(timeout_s)

    # --- worker callback ------------------------------------------------------------------------
    def _on_worker_done(self, result: RunResult) -> None:  # worker thread
        self.last_result = result
        self._set_state("idle")
        self.finished.emit(result)
