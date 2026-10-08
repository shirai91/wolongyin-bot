"""Panel tidier (``panels.yaml``): collapse the HUD panels before every run, offline.

A small fake game shows the live city capture (01: quest tracker, "Sắp mở" box and chat all open,
plus the top icon rows, which stay open) and paints a panel's button away when a click lands on it, as the game hides the panel.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.engine.context import FixedCanvas, RunContext
from ngoalong.engine.panels import PanelTidier
from ngoalong.engine.runner import RunStatus, execute
from ngoalong.engine.task import Task

LIVE = ROOT / "docs" / "game screenshots" / "live"
STORE = TemplateStore(PROFILE / "templates")
TIDIER = PanelTidier.load(PROFILE / "panels.yaml")
O = Point(180, 110)  # canvas origin in the live captures


class FakeGame:
    """Frames of a live capture; a click on an open panel's button hides that button."""

    def __init__(self, name: str, stuck: tuple[str, ...] = ()) -> None:
        self.frame = read_image(LIVE / name).copy()
        self.stuck = stuck  # panels whose button does nothing
        self.hidden: list[str] = []
        self.driver = RecordingInputDriver(self.on_event)

    def grab(self) -> np.ndarray:
        return self.frame.copy()

    def on_event(self, ev: InputEvent) -> None:
        if ev.kind != "click" or ev.point is None:
            return
        for p in TIDIER.panels:
            w, h = STORE.get(p.open.template).size
            r = Rect(O.x + p.open.at[0], O.y + p.open.at[1], w, h)
            if r.contains(ev.point) and p.name not in self.stuck:
                self.frame[r.y:r.bottom, r.x:r.right] = (20, 30, 25)
                self.hidden.append(p.name)


def ctx_for(game: FakeGame, panels: PanelTidier | None = TIDIER) -> RunContext:
    return RunContext(game, game.driver, CoordMap.load(PROFILE / "coords"), Matcher(STORE),
                      FixedCanvas(O), panels=panels, poll_interval_s=0.01)


def fast(t: PanelTidier) -> PanelTidier:
    return PanelTidier(t.panels, settle_s=0.0, max_clicks=t.max_clicks)


def test_profile_lists_the_four_panels() -> None:
    assert [p.name for p in TIDIER.panels] == ["quest_tracker", "sap_mo", "chat"]
    # the top icon rows stay open: features open their screens from them (Quay Tinh Tú, ...)
    assert all(p.open.template != "field.hud.icons_toggle" for p in TIDIER.panels)
    for p in TIDIER.panels:
        STORE.get(p.open.template)  # every template exists


def test_collapses_every_open_panel_once() -> None:
    game = FakeGame("01-city-main.png")
    ctx = ctx_for(game, fast(TIDIER))
    closed = ctx.tidy_panels()
    assert game.hidden == ["quest_tracker", "sap_mo", "chat"]
    assert len(closed) == 3 and len(game.driver.clicks) == 3
    assert ctx.tidy_panels() == [] and len(game.driver.clicks) == 3  # nothing left to click


def test_screen_without_panels_gets_no_clicks() -> None:
    game = FakeGame("28-dau-truong.png")  # full-screen activity: none of the panels shown
    assert ctx_for(game, fast(TIDIER)).tidy_panels() == []
    assert game.driver.clicks == []


def test_a_panel_that_will_not_close_is_reported_not_fatal() -> None:
    game = FakeGame("01-city-main.png", stuck=("sap_mo",))
    logs: list[tuple[str, str]] = []
    ctx = ctx_for(game, fast(TIDIER))
    ctx._on_log = lambda level, msg: logs.append((level, msg))
    closed = ctx.tidy_panels()
    assert "khung Sắp mở" not in closed and len(closed) == 2
    assert len(game.driver.clicks) == 2 + TIDIER.max_clicks
    assert any(level == "warning" and "Sắp mở" in msg for level, msg in logs)


class _Probe(Task):
    id = "probe"
    title = "Probe"

    def run(self, ctx: RunContext) -> str:
        return f"{len(ctx.input.clicks)} clicks before run"  # type: ignore[attr-defined]


class _NoTidy(_Probe):
    tidy_panels = False


@pytest.mark.parametrize(("task", "panels", "before"), [
    (_Probe(), True, 3), (_NoTidy(), True, 0), (_Probe(), False, 0),
])
def test_runner_tidies_before_run(task: Task, panels: bool, before: int) -> None:
    game = FakeGame("01-city-main.png")
    result = execute(task, ctx_for(game, fast(TIDIER) if panels else None))
    assert result.status is RunStatus.DONE and result.message == f"{before} clicks before run"


def test_missing_file_means_no_panels(tmp_path: Path) -> None:
    assert PanelTidier.load(tmp_path / "panels.yaml").panels == []
