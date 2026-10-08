"""``RunControl``: the stop and pause switches shared by a running task and the UI.

Stop sets a cancel flag that every wait checks (``Cancelled`` is raised within one wait slice).
Pause blocks the task at its next check point (a click, a poll, a sleep slice) until resumed or
stopped. Both are plain ``threading.Event`` objects, so any thread may flip them.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from ngoalong.core.cancel import check_cancel
from ngoalong.core.errors import Cancelled

SLICE_S = 0.05  # longest a paused or sleeping task waits before re-checking the switches


class RunControl:
    def __init__(self) -> None:
        self.cancel = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()

    # --- switches -------------------------------------------------------------------------------
    def stop(self) -> None:
        self.cancel.set()
        self._resumed.set()  # wake a paused task so it sees the stop

    def pause(self) -> None:
        if not self.cancel.is_set():
            self._resumed.clear()

    def resume(self) -> None:
        self._resumed.set()

    @property
    def stopped(self) -> bool:
        return self.cancel.is_set()

    @property
    def paused(self) -> bool:
        return not self._resumed.is_set() and not self.cancel.is_set()

    # --- check points ---------------------------------------------------------------------------
    def checkpoint(self) -> None:
        """Raise ``Cancelled`` when stopped; block while paused (stop still wins)."""
        check_cancel(self.cancel)
        while not self._resumed.is_set():
            self._resumed.wait(SLICE_S)
            check_cancel(self.cancel)
        check_cancel(self.cancel)

    def sleep(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        """Sleep ``seconds`` of RUNNING time: paused time does not count; stop interrupts."""
        self.checkpoint()
        remaining = max(0.0, seconds)
        while remaining > 0:
            start = clock()
            if self.cancel.wait(min(SLICE_S, remaining)):
                raise Cancelled("stopped by user")
            remaining -= clock() - start
            if not self._resumed.is_set():
                self.checkpoint()
        self.checkpoint()
