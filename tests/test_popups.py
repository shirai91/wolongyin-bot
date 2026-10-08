"""Popup guard: harmless pop-ups are closed by RunContext before a feature looks at the screen.

User report (2026-10-06): a "Chúc mừng ngài được … M.C+4500" reward modal opened over a tower
battle and the run waited until its time limit. Fixture: the debug-mode frame of that run
(``tests/fixtures/screens/popups/reward-modal-chuc-mung-debug-frame.png``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.core.errors import TaskStopped
from ngoalong.engine.context import RunContext, SceneTimeout
from ngoalong.engine.popups import PopupGuard, parse_popups

SHOTS = ROOT / "docs" / "game screenshots"
LIVE = SHOTS / "live"
FIXTURES = ROOT / "tests" / "fixtures" / "screens"
STORE = TemplateStore(PROFILE / "templates")
LOCATOR = CanvasLocator.from_profile(PROFILE)
GUARD = PopupGuard.load(PROFILE / "popups.yaml")

REWARD = FIXTURES / "popups" / "reward-modal-chuc-mung-debug-frame.png"
TOASTS = {
    SHOTS / "main-game.png",
    LIVE / "00-start-city-with-baovat-popup.png",
    FIXTURES / "clear_map" / "live-field-manh-hoach-levelup-notice.png",
}
GOLD = LIVE / "51-gold-skip-wait-confirm.png"  # "Bỏ qua thời gian chờ tốn 13 Vàng. Xác nhận?"
GOLD_ORIGIN = (45, 110)  # canvas origin in that capture (a smaller Coowon window)
GOLD_OK, GOLD_CANCEL = (455, 350), (546, 350)  # canvas centres of 确定 / 取消
REWARD_OK = (495, 352)  # canvas centre of the reward modal's 确定
TOAST_CLOSE = (986, 314)  # canvas centre of the toast ✕
REWARD_CANVAS_ORIGIN = (180, 110)  # the fixture is a live client capture
MODAL = (358, 220, 276, 165)  # canvas x, y, w, h of the reward modal on the fixture


@lru_cache(maxsize=None)
def _img(path: Path) -> np.ndarray:
    return read_image(path)


def _canvas_point(path: Path, p) -> tuple[float, float]:
    fix = LOCATOR.locate(_img(path))
    return fix.to_canvas(p)


def _all_screens() -> list[Path]:
    return sorted(SHOTS.glob("*.png")) + sorted(LIVE.glob("*.png")) + sorted(FIXTURES.rglob("*.png"))


def reward_over(base: np.ndarray, origin: tuple[int, int]) -> np.ndarray:
    """``base`` with the live reward modal pasted at its canvas position."""
    src = _img(REWARD)
    x, y, w, h = MODAL
    sx, sy = REWARD_CANVAS_ORIGIN
    out = base.copy()
    out[origin[1] + y:origin[1] + y + h, origin[0] + x:origin[0] + x + w] = src[sy + y:sy + y + h, sx + x:sx + x + w]
    return out


# --- detection on stored screens ----------------------------------------------------------------
def test_profile_lists_only_the_harmless_popups() -> None:
    assert [p.name for p in GUARD.popups] == ["gold_skip_wait", "reward", "toast"]
    # never a dialog that a feature answers itself
    banned = {"tower.confirm.ok", "tower.result.win", "tower.result.win_zh", "tower.result.lose"}
    for p in GUARD.popups:
        assert p.signature.template not in banned
        assert p.dismiss is None or p.dismiss.template not in banned
        if p.policy is None:  # plain entries just close a notice
            assert p.decline is None
        else:  # a cost question: both answers known, and a reason to show when declined
            assert p.dismiss is not None and p.decline is not None and p.stop_message


def test_a_policy_popup_needs_both_buttons() -> None:
    with pytest.raises(ValueError, match="decline"):
        parse_popups({"x": {"policy": "p", "signature": {"template": "a", "at": [1, 2]},
                            "dismiss": {"template": "b", "at": [1, 2]}}})


def test_reward_modal_found_on_the_user_capture() -> None:
    img = _img(REWARD)
    hit = GUARD.find(img, LOCATOR.locate(img), Matcher(STORE))
    assert hit is not None and hit.popup.name == "reward"
    # the fixture is a debug-mode frame: its drawn boxes throw the locator off by a few px, so use
    # the known client origin of the live window for the check (the hit itself is in frame pixels)
    x, y = hit.point.x - REWARD_CANVAS_ORIGIN[0], hit.point.y - REWARD_CANVAS_ORIGIN[1]
    assert abs(x - REWARD_OK[0]) <= 3 and abs(y - REWARD_OK[1]) <= 3


@pytest.mark.parametrize("path", sorted(TOASTS), ids=lambda p: p.name)
def test_toast_found(path: Path) -> None:
    img = _img(path)
    hit = GUARD.find(img, LOCATOR.locate(img), Matcher(STORE))
    assert hit is not None and hit.popup.name == "toast"
    x, y = _canvas_point(path, hit.point)
    assert abs(x - TOAST_CLOSE[0]) <= 3 and abs(y - TOAST_CLOSE[1]) <= 3


def test_no_popup_on_any_other_screen() -> None:
    m = Matcher(STORE)
    for path in _all_screens():
        if path in (REWARD, GOLD) or path in TOASTS:
            continue
        img = _img(path)
        hit = GUARD.find(img, LOCATOR.locate(img), m)
        assert hit is None, f"{hit.popup.name} on {path.name}"


def test_reward_found_when_the_modal_sits_lower() -> None:
    """The modal can be taller (longer reward text): the button region follows the title."""
    base = _img(SHOTS / "tower-in-dungeon.png")
    img = reward_over(base, (272, 108 + 18))  # 18 px lower than on the fixture
    hit = GUARD.find(img, LOCATOR.locate(img), Matcher(STORE))
    assert hit is not None and hit.popup.name == "reward"


def test_parse_rejects_bad_entries() -> None:
    with pytest.raises(ValueError):
        parse_popups({"x": {"signature": {"template": "a"}}})  # no "at"
    defs = parse_popups({"x": {"signature": {"template": "a", "at": [1, 2]}}})
    assert defs[0].dismiss is None  # default: click the signature itself
    assert not PopupGuard.load(ROOT / "no-such-file.yaml")


# --- RunContext integration ---------------------------------------------------------------------
class PopupScreen:
    """Shows ``covered`` until a click lands on the popup button, then ``clear``."""

    def __init__(self, covered: np.ndarray, clear: np.ndarray, button: tuple[int, int], origin: tuple[int, int]):
        self.covered, self.clear, self.button, self.origin = covered, clear, button, origin
        self.shown = True
        self.clicks: list[tuple[int, int]] = []

    def grab(self) -> np.ndarray:
        return self.covered if self.shown else self.clear

    def client_size(self) -> tuple[int, int]:
        f = self.grab()
        return (int(f.shape[1]), int(f.shape[0]))

    def on_event(self, ev: InputEvent) -> None:
        if ev.kind == "click" and ev.point is not None:
            c = (ev.point.x - self.origin[0], ev.point.y - self.origin[1])
            self.clicks.append(c)
            if abs(c[0] - self.button[0]) <= 12 and abs(c[1] - self.button[1]) <= 8:
                self.shown = False


def _ctx(screen: PopupScreen, popups: PopupGuard | None = GUARD) -> tuple[RunContext, RecordingInputDriver]:
    drv = RecordingInputDriver(on_event=screen.on_event)
    matcher = Matcher(STORE)
    ctx = RunContext(
        capturer=screen, input_driver=drv, coords=CoordMap.load(PROFILE / "coords"), matcher=matcher,
        canvas_source=CanvasLocator.from_profile(PROFILE),
        scenes=SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates"),
        poll_interval_s=0.05, popups=popups,
    )
    return ctx, drv


def _battle_screens() -> PopupScreen:
    base = _img(SHOTS / "tower-in-dungeon.png")
    origin = (272, 108)
    return PopupScreen(reward_over(base, origin), base, REWARD_OK, origin)


def test_expect_scene_closes_the_reward_modal_first() -> None:
    screen = _battle_screens()
    ctx, _ = _ctx(screen)
    logs: list[str] = []
    ctx._on_log = lambda level, msg: logs.append(msg)
    m = ctx.expect_scene("tower.battle", 3)
    assert m.found and not screen.shown
    assert len(screen.clicks) == 1
    assert ctx.counters["popups"] == 1
    assert any("Tự đóng popup" in s for s in logs)


def test_wait_for_sees_through_the_popup() -> None:
    screen = _battle_screens()
    ctx, _ = _ctx(screen)
    match = ctx.wait_for("tower.battle.exit", 3)
    assert match.name == "tower.battle.exit" and not screen.shown


def test_without_a_guard_the_popup_stays(monkeypatch) -> None:
    screen = _battle_screens()
    ctx, drv = _ctx(screen, popups=None)
    ctx.detect_scene()
    assert screen.shown and drv.clicks == []


def test_cost_dialogs_are_never_answered() -> None:
    """The tower's "Mỗi lần Vượt tháp mất 3 Thí luyện lệnh" box is left to the feature."""
    img = _img(SHOTS / "tower-ui-enter-confirmation.png")
    screen = PopupScreen(img, img, (0, 0), (270, 110))
    ctx, drv = _ctx(screen)
    assert ctx.detect_scene().name == "tower.confirm"
    assert drv.clicks == []


# --- the gold question ("Bỏ qua thời gian chờ tốn N Vàng") --------------------------------------
def test_gold_question_found_with_both_answers() -> None:
    img = _img(GOLD)
    fix = LOCATOR.locate(img)
    hit = GUARD.find(img, fix, Matcher(STORE))
    assert hit is not None and hit.popup.name == "gold_skip_wait" and hit.popup.policy == "spend_gold_skip_wait"
    ok, cancel = fix.to_canvas(hit.point), fix.to_canvas(hit.decline_point)
    assert abs(ok[0] - GOLD_OK[0]) <= 3 and abs(ok[1] - GOLD_OK[1]) <= 3  # left button = accept
    assert abs(cancel[0] - GOLD_CANCEL[0]) <= 3 and abs(cancel[1] - GOLD_CANCEL[1]) <= 3


def test_the_tower_cost_box_is_not_the_gold_question() -> None:
    """Same 确定 / 取消 buttons, but no "Lần sau không nhắc nhở" line: left to the tower feature."""
    img = _img(SHOTS / "tower-ui-enter-confirmation.png")
    assert GUARD.find(img, LOCATOR.locate(img), Matcher(STORE)) is None


def _gold_screens() -> PopupScreen:
    """The gold question; after an answer, the city (pasted into the same smaller window)."""
    covered = _img(GOLD)
    city = _img(LIVE / "01-city-main.png")
    clear = covered.copy()
    gx, gy = GOLD_ORIGIN
    clear[gy:gy + 600, gx:gx + 1000] = city[110:710, 180:1180]
    return PopupScreen(covered, clear, GOLD_OK, GOLD_ORIGIN)


def test_gold_question_accepted_when_the_setting_is_on() -> None:
    screen = _gold_screens()
    ctx, _ = _ctx(screen)
    ctx.policy = lambda name: name == "spend_gold_skip_wait"
    assert ctx.expect_scene("city", 3).found
    assert screen.clicks == [GOLD_OK] or (len(screen.clicks) == 1 and _close(screen.clicks[0], GOLD_OK))
    assert ctx.counters["gold_skip_wait"] == 1


def test_gold_question_declined_and_task_stopped_when_off() -> None:
    screen = _gold_screens()
    screen.button = GOLD_CANCEL  # 取消 also closes the box
    ctx, _ = _ctx(screen)  # default policy: decline
    with pytest.raises(TaskStopped, match="Vàng"):
        ctx.detect_scene()
    assert len(screen.clicks) == 1 and _close(screen.clicks[0], GOLD_CANCEL)


def test_declining_stops_the_run_with_its_reason() -> None:
    from ngoalong.engine.runner import RunStatus, execute
    from ngoalong.engine.task import Task, TaskParams

    class Look(Task):
        id = "look"
        title = "Look"
        Params = TaskParams

        def run(self, ctx: RunContext) -> str:
            ctx.detect_scene()
            return "saw it"

    screen = _gold_screens()
    screen.button = GOLD_CANCEL
    ctx, _ = _ctx(screen)
    r = execute(Look(TaskParams()), ctx)
    assert r.status is RunStatus.STOPPED
    assert "Vàng" in r.message and "Cài đặt" in r.message


def test_the_setting_is_read_at_the_moment_of_the_question() -> None:
    """Switching the checkbox during a run applies to the next question."""
    allowed = {"spend_gold_skip_wait": False}
    screen = _gold_screens()
    ctx, _ = _ctx(screen)
    ctx.policy = lambda name: allowed.get(name, False)
    allowed["spend_gold_skip_wait"] = True
    assert ctx.detect_scene().name == "city"
    assert _close(screen.clicks[0], GOLD_OK)


def test_setting_defaults_to_never_spending_gold() -> None:
    from ngoalong.config.settings import Settings

    assert Settings().game.spend_gold_skip_wait is False
    s = Settings.model_validate({"game": {"spend_gold_skip_wait": True}})
    assert s.game.spend_gold_skip_wait is True


def _close(p: tuple[int, int], c: tuple[int, int], d: int = 3) -> bool:
    return abs(p[0] - c[0]) <= d and abs(p[1] - c[1]) <= d


def test_a_popup_that_never_closes_is_left_after_a_few_tries() -> None:
    screen = _battle_screens()
    screen.button = (-100, -100)  # clicks never close it
    ctx, _ = _ctx(screen)
    GUARD_FAST = PopupGuard(GUARD.popups, max_rounds=2, settle_s=0.0)
    ctx.popups = GUARD_FAST
    with pytest.raises(SceneTimeout):
        ctx.expect_scene("city", 0.2)
    assert 2 <= len(screen.clicks) <= 2 * 10  # bounded per capture, never an endless click loop
