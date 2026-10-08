"""Debug data model: ``Detection`` (re-exported from core) and ``DebugFrame`` (docs/06 "Debug mode").

One ``DebugFrame`` holds everything the overlay draws for one step: the captured image, the canvas
fix, the scene, every search made (found or not), planned clicks and the status line fields. The
three sinks (live overlay, debug panel, PNG files) all render the same frame.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

import numpy as np

from ngoalong.core.detection import Detection
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix, canvas_crop

__all__ = ["DebugFrame", "Detection", "Marker"]


@dataclass(frozen=True, slots=True)
class Marker:
    """A labelled point that is not a click (e.g. a scene object's fixed offset)."""

    name: str
    point: Point


@dataclass(slots=True)
class DebugFrame:
    image: np.ndarray | None = None  # BGR frame the detections refer to (frame coordinates)
    canvas: CanvasFix | None = None
    scene: str | None = None
    scene_score: float | None = None
    detections: list[Detection] = field(default_factory=list)
    clicks: list[Point] = field(default_factory=list)  # planned clicks, frame coordinates
    markers: list[Marker] = field(default_factory=list)
    window: str = ""
    task: str = ""
    step: str = ""
    failed: bool = False  # failure frames are always saved, debug mode or not
    note: str = ""
    timestamp: float = field(default_factory=time.time)

    def add(self, detections: Iterable[Detection]) -> DebugFrame:
        self.detections.extend(detections)
        return self

    def cropped_to_canvas(self) -> DebugFrame:
        """A copy showing only the game canvas, every annotation moved with it (log PNGs, the debug
        panel). ``self`` when the canvas is not certain: the whole window is kept then."""
        crop = None if self.image is None else canvas_crop(self.image, self.canvas)
        if crop is None or self.canvas is None:
            return self
        dx, dy = -crop.x, -crop.y

        def move(r: Rect | None) -> Rect | None:
            return None if r is None else r.offset(dx, dy)

        return replace(
            self,
            image=self.image[crop.y:crop.bottom, crop.x:crop.right],  # type: ignore[index]
            canvas=replace(self.canvas, rect=move(self.canvas.rect),
                           candidates=(), detections=tuple(replace(d, rect=d.rect.offset(dx, dy),
                                                                   best_rect=move(d.best_rect), roi=move(d.roi))
                                                           for d in self.canvas.detections)),
            detections=[replace(d, rect=d.rect.offset(dx, dy), best_rect=move(d.best_rect), roi=move(d.roi))
                        for d in self.detections],
            clicks=[c.offset(dx, dy) for c in self.clicks],
            markers=[Marker(m.name, m.point.offset(dx, dy)) for m in self.markers],
        )

    @property
    def next_click(self) -> Point | None:
        return self.clicks[0] if self.clicks else None

    def status_line(self) -> str:
        parts: list[str] = []
        if self.scene is not None:
            score = f" {self.scene_score:.2f}" if self.scene_score is not None else ""
            parts.append(f"scene: {self.scene}{score}")
        if self.window:
            parts.append(f"window: {self.window}")
        if self.task:
            parts.append(f"task: {self.task}")
        if self.step:
            parts.append(f"step: {self.step}")
        nc = self.next_click
        parts.append(f"next click: ({nc.x}, {nc.y})" if nc else "next click: -")
        if self.failed:
            parts.append("FAILED")
        if self.note:
            parts.append(self.note)
        return " | ".join(parts)
