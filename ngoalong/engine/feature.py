"""``Feature``: a feature as a goal, a memory and a list of rules (docs/08 "Concepts" 4).

A feature module defines ``Params`` (the UI form, as for every ``Task``), a ``Memory`` model and
the rules; the decision loop (``engine/loop.py``) does the rest::

    @register
    class QuayRuong(Feature):
        id, title, Params = "quay_ruong", "Quay Tinh Tú", QuayRuongParams
        entry = "chest.wheel"                     # the loop navigates here when no rule applies

        class Memory(BaseModel):
            opened: int = 0

        def rules(self) -> list[Rule]:
            return [
                Rule("quay", on="chest.wheel", when=lambda s: s.chests > 0,
                     do=lambda s: Click(s.spin, label="quay", change=lambda n: n.value("chest.wheel", "chests"))),
            ]

        def goal(self, snap) -> str | None:       # the run ends when this returns a summary
            return "Hết rương" if snap.value("chest.wheel", "chests") == 0 else None

Each tick the loop takes the first rule whose ``on`` screen is the top of the stack (top dialog,
else the base) and whose ``when`` holds; ``when`` and ``do`` get that screen's ``ScreenState``
(``s.tab``, ``s.page``, ``s.spin`` ...). ``self.snap`` is the whole Snapshot of the tick,
``self.m`` the memory, ``self.params`` the settings. A reader that is not there (KeyError,
AttributeError) makes the rule not apply. Interrupts (pop-ups, panels) run before the rules,
navigation and recovery are built in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from ngoalong.engine.actions import Action, Click
from ngoalong.engine.task import Task
from ngoalong.perception import Snapshot
from ngoalong.perception.readers import ScreenState
from ngoalong.perception.screens import UNKNOWN

if TYPE_CHECKING:
    from ngoalong.engine.actor import Actor


@dataclass
class Rule:
    name: str
    on: str | tuple[str, ...]
    do: Action | Callable[[ScreenState], Action | None]
    when: Callable[[ScreenState], bool] | None = None

    def fire(self, snap: Snapshot) -> Action | None:
        top = snap.top
        if top == UNKNOWN or top not in ((self.on,) if isinstance(self.on, str) else self.on):
            return None
        s = snap[top]
        try:
            if self.when is not None and not self.when(s):
                return None
            a = self.do(s) if callable(self.do) else self.do
        except (KeyError, AttributeError):
            return None
        if isinstance(a, Click) and a.at is None:  # the button it needs is not on this frame
            return None
        return a


class Feature(Task):
    """A Task that runs on the decision loop instead of a script."""

    entry: str  # the screen the work starts from (a feature may set it per run, in begin())
    tick_s: ClassVar[float] = 0.3  # pause of an idle tick
    collapse_panels: ClassVar[bool] = True  # interrupts collapse the HUD panels (panels.yaml)
    # Boxes the feature answers itself, so the pop-up guard's cost questions leave them alone
    # (e.g. "confirm.gold" after the feature's own "buy").
    answers: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "entry", None):
            cls.start_scene = cls.entry  # shown on the feature page

    class Memory(BaseModel):
        pass

    def __init__(self, params: Any = None) -> None:
        super().__init__(params)
        self.m = self.Memory()
        self.snap: Snapshot | None = None
        self.services: Any = None
        self.actor: Any = None  # the run's Actor (count, progress, log), set by the loop

    def bind_services(self, services: Any) -> Feature:
        self.services = services
        return self

    # --- the feature's part -----------------------------------------------------------------------
    def begin(self) -> None:
        """Called once when the run starts: set the memory from the params."""
        self.m = self.Memory()

    def rules(self) -> list[Rule]:
        raise NotImplementedError

    def goal(self, snap: Snapshot) -> str | None:
        """The run's summary once the goal is reached, else None."""
        return None

    def max_run_s(self) -> float:
        """Longest run in seconds (0 = no limit); features with a time setting override it."""
        return 0.0

    def summary(self) -> str:
        """What was done so far (the run's message when it stops on a time limit)."""
        return "Đã dừng"

    def run(self, actor: Actor) -> str:
        from ngoalong.engine.loop import run_feature

        return run_feature(self, actor)
