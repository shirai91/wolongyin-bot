"""Actions: what one tick of the decision loop does (docs/08 "Concepts" 4-5).

A rule returns one action. Every action that touches the game says what it **expects** on the
following ticks and how long that may take; the loop checks it on each new Snapshot and calls the
recovery ladder when it is not met in time::

    Click(snap.point("btn.vuot_thap"), label="vượt tháp", expect="confirm.ok_cancel")
    Click(p, label="tầng 25", expect=lambda s: s.value("tower.dialog", "selected") == 25)
    Click(spin, label="quay", change=lambda s: s.value("chest.wheel", "chests"), within=6)
    Text("3", at=PAGE_BOX, label="trang 3", expect=lambda s: s.value("tower.dialog", "page") == 3)
    Wait(1.0, label="đang đánh", patience=300)    # nothing to do yet; stuck after 300 s
    Done("Đã mở 40 rương")                        # the goal is reached
    Fail("Tầng 300 chưa mở")                      # stop the run as failed (screenshot + Snapshot dump)
    Navigate("tower.dialog")                      # walk the screen graph (nav.yaml) one step

Expectations: ``expect`` is a screen name (shown anywhere in the stack or the overlays) or a
predicate on the next Snapshot; ``change`` is a reader function whose value must differ from its
value when the action was taken (and not be None). ``then`` runs once when the expectation is met
(update the feature's memory: next floor, chests bought, ...). Points are canvas points (1000x600).
The common fields are keyword-only (``label=``, ``expect=``, ...).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ngoalong.core.geometry import Point

if TYPE_CHECKING:
    from ngoalong.perception import Snapshot

Expectation = str | Callable[["Snapshot"], bool] | None

DEFAULT_WITHIN_S = 3.0
DEFAULT_SETTLE_S = 0.25


@dataclass(kw_only=True)
class Action:
    label: str = ""
    expect: Expectation = None
    change: Callable[[Snapshot], Any] | None = None
    within: float = DEFAULT_WITHIN_S  # seconds the expectation may take
    settle: float = DEFAULT_SETTLE_S  # pause after acting, before the next capture
    then: Callable[[], None] | None = None  # runs once when the expectation is met
    stop: str = ""  # after acting, stop the run (TaskStopped) with this message

    @property
    def expects(self) -> bool:
        return self.expect is not None or self.change is not None

    def what(self) -> str:
        return f"{type(self).__name__.lower()} {self.label}".rstrip()

    def describe(self) -> str:
        s = self.what()
        if self.expects:
            what = self.expect if isinstance(self.expect, str) else "state" if self.expect else "change"
            s += f", expect {what} ≤{self.within:g}s"
        return s


@dataclass
class Click(Action):
    at: Point
    double: bool = False

    def what(self) -> str:
        return f"{'dclick' if self.double else 'click'} {self.label} @{self.at.x},{self.at.y}"


@dataclass
class Key(Action):
    vk: int

    def what(self) -> str:
        return f"key {self.label or hex(self.vk)}"


@dataclass
class Text(Action):
    """Type into a text box: double-click ``at``, Backspace and Delete ``clear`` times each, type
    ``text``, Enter (when ``enter``)."""

    text: str
    at: Point | None = None
    clear: int = 3
    enter: bool = True

    def what(self) -> str:
        return f"type {self.text!r} {self.label}".rstrip()


@dataclass
class Wait(Action):
    """Nothing to do this tick. ``patience``: how long the same rule may keep waiting before the
    loop counts it as stuck (0 = the loop's idle limit)."""

    seconds: float = 0.5
    patience: float = 0.0

    def what(self) -> str:
        return f"wait {self.seconds:g}s {self.label}".rstrip()


@dataclass
class Done(Action):
    message: str = "Hoàn thành"

    def what(self) -> str:
        return f"done: {self.message}"


@dataclass
class Fail(Action):
    message: str = "Thất bại"

    def what(self) -> str:
        return f"fail: {self.message}"


@dataclass
class Navigate(Action):
    screen: str = ""

    def what(self) -> str:
        return f"navigate {self.screen}"


@dataclass
class Decision:
    """What the loop decided on one tick, and why (one run.log line)."""

    action: Action
    rule: str  # rule name, "interrupt: ...", "nav → X", "recovery: ..."
    kind: str = "rule"  # rule | interrupt | nav | recovery
    extra: dict[str, Any] = field(default_factory=dict)
