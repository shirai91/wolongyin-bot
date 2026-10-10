"""``RunnerManager``: one ``TaskRunner`` per discovered game window (docs/06 P3 "Multiple windows").

Each Coowon window that shows Ngọa Long gets a ``WindowSlot`` with its own runner and its own
``CanvasLocator`` (the canvas fix of one window means nothing for another), so several accounts
run in parallel. The manager is Qt-thread owned: ``sync``/``start``/``stop`` are called from the UI
thread; the per-slot signals re-emit the runner signals with the slot key and are delivered to UI
receivers in the UI thread.

A window that disappears while its slot is running is kept (marked ``present=False``) until the
run ends, then dropped on the next ``sync``. ``is_busy_elsewhere`` lets the app refuse a run on a
window that the single-window "Tính năng" runner is already driving (one task per window).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ngoalong.core.errors import WindowNotFoundError
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.window import GameWindow
from ngoalong.engine.actor import Actor
from ngoalong.engine.runner import RunHooks, RunResult, TaskRunner
from ngoalong.engine.task import Task

log = logging.getLogger(__name__)


def same_window(a: GameWindow | None, b: GameWindow | None) -> bool:
    return a is not None and b is not None and a.hwnd == b.hwnd and a.pid == b.pid


@dataclass(eq=False)
class WindowSlot:
    """One bound game window, its runner and its canvas locator."""

    key: str
    window: GameWindow
    runner: TaskRunner
    locator: CanvasLocator
    present: bool = True  # False when the last discovery no longer saw the window

    @property
    def title(self) -> str:
        return self.window.title

    @property
    def state(self) -> str:
        return self.runner.state

    def is_running(self) -> bool:
        return self.runner.is_running()


SlotActorFactory = Callable[[WindowSlot, Task, RunHooks], Actor]
LocatorFactory = Callable[[], CanvasLocator]
BusyCheck = Callable[[GameWindow], bool]


class _SlotRelay(QObject):
    """Lives in the UI thread; re-emits one runner's signals with the slot key."""

    def __init__(self, manager: RunnerManager, key: str) -> None:
        super().__init__(manager)
        self._manager = manager
        self._key = key

    @Slot(str, str)
    def on_log(self, level: str, message: str) -> None:
        self._manager.slot_log.emit(self._key, level, message)

    @Slot(int, int)
    def on_progress(self, done: int, total: int) -> None:
        self._manager.slot_progress.emit(self._key, done, total)

    @Slot(str)
    def on_state(self, state: str) -> None:
        self._manager.slot_state.emit(self._key, state)

    @Slot(object)
    def on_finished(self, result: RunResult) -> None:
        self._manager.slot_finished.emit(self._key, result)


class RunnerManager(QObject):
    slots_changed = Signal()
    slot_state = Signal(str, str)  # key, idle | running | paused | stopping
    slot_log = Signal(str, str, str)  # key, level, message
    slot_progress = Signal(str, int, int)  # key, done, total (-1 = unknown)
    slot_finished = Signal(str, object)  # key, RunResult

    def __init__(
        self,
        log_dir: str | Path,
        make_actor: SlotActorFactory,
        new_locator: LocatorFactory,
        is_busy_elsewhere: BusyCheck | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.log_dir = Path(log_dir)
        self._make_actor = make_actor
        self._new_locator = new_locator
        self._busy_elsewhere = is_busy_elsewhere or (lambda _gw: False)
        self._slots: dict[str, WindowSlot] = {}
        self._relays: dict[str, _SlotRelay] = {}

    # --- slots ----------------------------------------------------------------------------------
    def slots(self) -> list[WindowSlot]:
        return list(self._slots.values())

    def slot(self, key: str) -> WindowSlot:
        try:
            return self._slots[key]
        except KeyError:
            raise WindowNotFoundError(f"không còn cửa sổ {key}") from None

    def slot_for(self, window: GameWindow) -> WindowSlot | None:
        return next((s for s in self._slots.values() if same_window(s.window, window)), None)

    def sync(self, windows: Iterable[GameWindow]) -> list[WindowSlot]:
        """Match the discovered windows to slots: add new ones, drop idle vanished ones."""
        seen: set[str] = set()
        for gw in windows:
            slot = self.slot_for(gw)
            if slot is None:
                slot = self._add(gw)
            else:
                slot.window.title = gw.title  # the tab title can change (server / account)
                slot.present = True
            seen.add(slot.key)
        for key, slot in list(self._slots.items()):
            if key in seen:
                continue
            if slot.is_running():
                slot.present = False  # keep it until the run ends; it fails or stops on its own
            else:
                self._remove(key)
        self.slots_changed.emit()
        return self.slots()

    def _add(self, gw: GameWindow) -> WindowSlot:
        key = f"{gw.pid}:{gw.hwnd:X}"
        n = 1
        while key in self._slots:
            n += 1
            key = f"{gw.pid}:{gw.hwnd:X}#{n}"
        runner = TaskRunner(self.log_dir, parent=self, run_tag=f"w{gw.hwnd:X}")
        relay = _SlotRelay(self, key)
        runner.log.connect(relay.on_log)
        runner.progress.connect(relay.on_progress)
        runner.state_changed.connect(relay.on_state)
        runner.finished.connect(relay.on_finished)
        # A private copy: ``rebind()`` during a run must not move another runner's window.
        slot = WindowSlot(key, GameWindow(gw._info()), runner, self._new_locator())
        self._slots[key] = slot
        self._relays[key] = relay
        return slot

    def _remove(self, key: str) -> None:
        slot = self._slots.pop(key)
        relay = self._relays.pop(key, None)
        slot.runner.wait(1.0)
        for obj in (slot.runner, relay):
            if obj is not None:
                obj.deleteLater()

    # --- control --------------------------------------------------------------------------------
    def running_on(self, window: GameWindow | None) -> bool:
        slot = self.slot_for(window) if window is not None else None
        return slot is not None and slot.is_running()

    def any_running(self) -> bool:
        return any(s.is_running() for s in self._slots.values())

    def start(self, key: str, task: Task) -> RunHooks:
        """Start ``task`` on the slot's window (raises when the window is gone or busy)."""
        slot = self.slot(key)
        if not slot.present:
            raise WindowNotFoundError(f"cửa sổ đã đóng: {slot.title}")
        if slot.is_running():
            raise RuntimeError(f"cửa sổ đang chạy một tác vụ khác: {slot.title}")
        if self._busy_elsewhere(slot.window):
            raise RuntimeError(f"cửa sổ đang chạy ở thẻ Tính năng: {slot.title}")
        return slot.runner.start(task, lambda t, hooks: self._make_actor(slot, t, hooks))

    def stop(self, key: str) -> None:
        self.slot(key).runner.stop()

    def toggle_pause(self, key: str) -> None:
        self.slot(key).runner.toggle_pause()

    @Slot()
    def stop_all(self) -> None:
        for s in self._slots.values():
            s.runner.stop()

    @Slot()
    def toggle_pause_all(self) -> None:
        """Pause every running slot; when none is running but some are paused, resume them."""
        active = [s for s in self._slots.values() if s.is_running()]
        if any(s.state == "running" for s in active):
            for s in active:
                s.runner.pause()
        else:
            for s in active:
                s.runner.resume()

    def wait_all(self, timeout_s: float = 5.0) -> bool:
        return all([s.runner.wait(timeout_s) for s in self._slots.values()])

    def shutdown(self, timeout_s: float = 2.0) -> None:
        self.stop_all()
        self.wait_all(timeout_s)
