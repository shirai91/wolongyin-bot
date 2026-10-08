"""Debug sinks: where ``DebugFrame`` objects go (docs/06 "One data model, three outputs").

* ``PngSink``: annotated PNGs ``logs/run-<id>/NNN-<step>.png``; every frame in debug mode, and
  failure frames always. Cropped to the game canvas when its position is certain.
* ``PanelSink``: keeps the latest frame (and its rendering) for the in-app debug panel and notifies
  listeners; the Qt panel subscribes later.
* ``OverlaySink``: forwards frames to a live overlay presenter (the transparent Qt window); between
  bot frames ``OverlayRefresher`` (app.py) redraws it from fresh captures.
* ``FanoutSink``: publishes to several sinks; one failing sink never breaks the bot.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from ngoalong.core.capture import save_png
from ngoalong.debug.model import DebugFrame
from ngoalong.debug.render import ascii_label, render

log = logging.getLogger(__name__)


@runtime_checkable
class DebugSink(Protocol):
    def publish(self, frame: DebugFrame) -> None: ...


def slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_label(text)).strip("-").lower()
    return s[:max_len] or "step"


def new_run_dir(base: str | Path = "logs", run_id: str | None = None) -> Path:
    """``<base>/run-<id>``; the id defaults to the local time ``YYYYmmdd-HHMMSS``."""
    rid = run_id or time.strftime("%Y%m%d-%H%M%S")
    return Path(base) / f"run-{rid}"


class PngSink:
    def __init__(self, directory: str | Path, enabled: bool = False, start_index: int = 1) -> None:
        self.directory = Path(directory)
        self.enabled = enabled  # debug mode: save every frame; otherwise failures only
        self._index = start_index
        self._lock = threading.Lock()
        self.saved: list[Path] = []

    def publish(self, frame: DebugFrame) -> None:
        self.save(frame)

    def save(self, frame: DebugFrame, name: str | None = None) -> Path | None:
        """Render and write ``frame``; returns the path, or ``None`` when it is skipped."""
        if not (self.enabled or frame.failed) or frame.image is None:
            return None
        with self._lock:
            n = self._index
            self._index += 1
        path = self.directory / f"{n:03d}-{slug(name or frame.step or frame.scene or 'frame')}.png"
        save_png(path, render(frame.cropped_to_canvas()))
        with self._lock:
            self.saved.append(path)
        return path


class PanelSink:
    """Latest frame for the debug panel; rendering happens lazily, on request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: DebugFrame | None = None
        self._rendered: np.ndarray | None = None
        self._listeners: list[Callable[[DebugFrame], None]] = []

    def subscribe(self, listener: Callable[[DebugFrame], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def publish(self, frame: DebugFrame) -> None:
        with self._lock:
            self._frame = frame
            self._rendered = None
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(frame)
            except Exception:  # a broken listener must not stop the bot
                log.exception("debug panel listener failed")

    @property
    def latest(self) -> DebugFrame | None:
        return self._frame

    def latest_image(self) -> np.ndarray | None:
        with self._lock:
            frame, cached = self._frame, self._rendered
        if frame is None or frame.image is None:
            return None
        if cached is None:
            cached = render(frame.cropped_to_canvas())
            with self._lock:
                if self._frame is frame:
                    self._rendered = cached
        return cached


class OverlayPresenter(Protocol):
    """The live overlay window (transparent, click-through, top-most); implemented by the UI."""

    def show_frame(self, frame: DebugFrame) -> None: ...

    def hide(self) -> None: ...


class OverlaySink:
    def __init__(self, presenter: OverlayPresenter, enabled: bool = False) -> None:
        self.presenter = presenter
        self._enabled = enabled
        self.last_publish = 0.0  # time.monotonic() of the last bot frame (0 = none yet)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self.presenter.hide()

    def publish(self, frame: DebugFrame) -> None:
        """A bot step's frame."""
        if self._enabled:
            self.last_publish = time.monotonic()
            self.presenter.show_frame(frame)

    def refresh(self, frame: DebugFrame | None) -> None:
        """A live frame from the overlay refresher (``None`` clears the overlay); not a bot frame."""
        if not self._enabled:
            return
        if frame is None:
            self.presenter.hide()
        else:
            self.presenter.show_frame(frame)


class CallbackSink:
    def __init__(self, fn: Callable[[DebugFrame], None]) -> None:
        self.fn = fn

    def publish(self, frame: DebugFrame) -> None:
        self.fn(frame)


class FanoutSink:
    def __init__(self, sinks: Iterable[DebugSink] = ()) -> None:
        self._sinks: list[DebugSink] = list(sinks)
        self._lock = threading.Lock()

    def add(self, sink: DebugSink) -> None:
        with self._lock:
            self._sinks.append(sink)

    def remove(self, sink: DebugSink) -> None:
        with self._lock:
            if sink in self._sinks:
                self._sinks.remove(sink)

    def publish(self, frame: DebugFrame) -> None:
        with self._lock:
            sinks = list(self._sinks)
        for s in sinks:
            try:
                s.publish(frame)
            except Exception:
                log.exception("debug sink %r failed", s)
