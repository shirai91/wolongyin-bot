"""``RecordingInputDriver``: a fake ``InputDriver`` that records every action, for offline tests."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from ngoalong.core.geometry import Point
from ngoalong.core.input import MouseButton


@dataclass(frozen=True, slots=True)
class InputEvent:
    kind: str  # "click" | "key" | "text"
    point: Point | None = None
    button: MouseButton = "left"
    double: bool = False
    vk: int | None = None
    text: str | None = None


class RecordingInputDriver:
    """Records clicks, keys and text. ``on_event`` lets a test advance a FileCapturer per click."""

    def __init__(self, on_event: Callable[[InputEvent], None] | None = None) -> None:
        self.events: list[InputEvent] = []
        self._on_event = on_event
        self._lock = threading.Lock()

    def _record(self, ev: InputEvent) -> None:
        with self._lock:
            self.events.append(ev)
        if self._on_event is not None:
            self._on_event(ev)

    def click(self, p: Point, button: MouseButton = "left", double: bool = False) -> None:
        self._record(InputEvent("click", point=p, button=button, double=double))

    def key(self, vk: int) -> None:
        self._record(InputEvent("key", vk=vk))

    def text(self, s: str) -> None:
        self._record(InputEvent("text", text=s))

    @property
    def clicks(self) -> list[Point]:
        return [e.point for e in self.events if e.kind == "click" and e.point is not None]

    def clear(self) -> None:
        with self._lock:
            self.events.clear()
