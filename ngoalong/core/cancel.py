"""Cancellation primitives: every wait in the bot goes through ``sleep_cancellable``.

A ``CancelFlag`` is anything with ``is_set()`` and ``wait(timeout)``; ``threading.Event`` fits.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from ngoalong.core.errors import Cancelled


class CancelFlag(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


def check_cancel(cancel: CancelFlag | None) -> None:
    """Raise ``Cancelled`` when the flag is set."""
    if cancel is not None and cancel.is_set():
        raise Cancelled("stopped by user")


def sleep_cancellable(seconds: float, cancel: CancelFlag | None = None) -> None:
    """Sleep up to ``seconds``; raise ``Cancelled`` as soon as ``cancel`` is set."""
    check_cancel(cancel)
    if seconds <= 0:
        return
    if cancel is None:
        time.sleep(seconds)
        return
    if cancel.wait(seconds):
        raise Cancelled("stopped by user")


def new_cancel_flag() -> threading.Event:
    return threading.Event()
