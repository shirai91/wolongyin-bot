"""AutoClick: background clicker on recorded canvas points.

* points are canvas-relative (1000x600), so they survive window moves and page zoom; recording a
  point never re-targets the game window;
* six click types (left / middle / right, single / double);
* a run can be limited to a number of rounds; Stop / Pause work at any time.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ngoalong.core.geometry import Point
from ngoalong.core.input import MouseButton
from ngoalong.engine.actor import Actor
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

CANVAS_W, CANVAS_H = 1000, 600


class ClickType(StrEnum):
    """Mouse button and single / double click."""

    LEFT = "left"
    LEFT_DOUBLE = "left_double"
    MIDDLE = "middle"
    MIDDLE_DOUBLE = "middle_double"
    RIGHT = "right"
    RIGHT_DOUBLE = "right_double"

    @property
    def button(self) -> MouseButton:
        return self.value.split("_")[0]  # type: ignore[return-value]

    @property
    def double(self) -> bool:
        return self.value.endswith("_double")

    @property
    def label(self) -> str:
        return _CLICK_LABELS[self]


_CLICK_LABELS: dict[ClickType, str] = {
    ClickType.LEFT: "Chuột trái",
    ClickType.LEFT_DOUBLE: "Chuột trái (nhấp đúp)",
    ClickType.MIDDLE: "Chuột giữa",
    ClickType.MIDDLE_DOUBLE: "Chuột giữa (nhấp đúp)",
    ClickType.RIGHT: "Chuột phải",
    ClickType.RIGHT_DOUBLE: "Chuột phải (nhấp đúp)",
}


class CanvasPoint(BaseModel):
    """A point on the game canvas at 1000x600."""

    model_config = ConfigDict(frozen=True)

    x: int = Field(ge=0, lt=CANVAS_W)
    y: int = Field(ge=0, lt=CANVAS_H)

    def to_point(self) -> Point:
        return Point(self.x, self.y)


class AutoClickParams(TaskParams):
    points: list[CanvasPoint] = Field(
        default_factory=list,
        title="Danh sách tọa độ",
        description="Các điểm trên khung game (1000x600) được click lần lượt",
        json_schema_extra={"widget": "canvas_points"},
    )
    click_type: ClickType = Field(ClickType.LEFT, title="Kiểu click")
    interval_ms: int = Field(1000, ge=10, le=3_600_000, title="Thời gian giữa 2 lần click (ms)")
    rounds: int = Field(0, ge=0, le=1_000_000, title="Số vòng", description="0 = chạy đến khi bấm Dừng")


@register
class AutoClick(Task):
    id = "autoclick"
    title = "AutoClick"
    description = "Click lần lượt các điểm đã ghi trên khung game, chạy ngầm"
    order = 900
    Params = AutoClickParams

    params: AutoClickParams

    def run(self, actor: Actor) -> str:
        """Click the points in turn. No screen recognition: the canvas is only located (again when
        it is a few seconds old or the window changed size) to turn canvas points into window points."""
        p = self.params
        if not p.points:
            raise ValueError("Chưa có tọa độ click: hãy ghi ít nhất một điểm")
        per_round = len(p.points)
        total = p.rounds * per_round if p.rounds else None
        done = 0
        rnd = 0
        while p.rounds == 0 or rnd < p.rounds:
            for pt in p.points:
                actor.canvas_fix()
                actor.click(pt.to_point(), p.click_type.double, p.click_type.button)
                done += 1
                actor.progress(done, total)
                if total is None or done < total:
                    actor.sleep(p.interval_ms / 1000)
            rnd += 1
        return f"Đã click {done} lần"
