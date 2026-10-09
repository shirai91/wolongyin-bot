"""The decision loop (docs/08 "Target loop"): capture → Snapshot → decide → act → verify, one tick
at a time, until the feature's goal is reached, the run is stopped, or it fails.

One tick:

1. Stop / Pause are checked (``RunControl``); the max run time is checked.
2. Capture the window, locate the canvas (again every few seconds), perceive the Snapshot.
3. Frozen-client watchdog: the canvas did not change for ``frozen_s`` while inputs were sent.
4. The action of the previous tick is verified: its expectation met → progress (and ``then``);
   still within its time → wait (interrupts may still act); too late → recovery ladder.
5. Interrupts (pop-ups, panels), the feature's goal, recovery navigation, the feature's rules,
   then navigation to the feature's entry screen; nothing applies → an idle tick.
6. Loop guard: an idle tick, a rule waiting longer than its patience, or the same decision on the
   same screen ``max_repeats`` times climbs the recovery ladder.
7. Act, and log the decision to run.log (one line per new decision)::

       city > tower.dialog{tab=Tháp, page=3, selected=25} | rule "vượt tháp" → click vượt tháp @367,469, expect confirm.ok_cancel ≤3s
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from ngoalong.core.errors import NgoaLongError, TaskStopped
from ngoalong.core.locator import DEFAULT_PROFILE_DIR, CanvasFix
from ngoalong.engine.actions import Click, Decision, Done, Fail, Key, Navigate, Text, Wait
from ngoalong.engine.actor import Actor
from ngoalong.engine.interrupts import Interrupts
from ngoalong.engine.navigate import Navigator
from ngoalong.engine.recovery import Recovery
from ngoalong.perception import Perceiver, Snapshot

if TYPE_CHECKING:
    from ngoalong.engine.context import RunContext
    from ngoalong.engine.feature import Feature


FROZEN_HINT_S = 8.0  # when recovery gives up on a canvas unchanged this long, say the client froze


class LoopFailed(NgoaLongError):
    """The loop gave up (recovery ladder exhausted, frozen client, no canvas, a rule's Fail)."""


@dataclass
class Limits:
    max_idle_ticks: int = 40  # ticks with nothing to do (no rule, no navigation) before recovery
    max_repeats: int = 8  # the same decision on the same screen this many times in a row
    frozen_s: float = 30.0  # no pixel changed while inputs were sent: the client is frozen
    frozen_inputs: int = 3  # inputs sent during that time
    no_canvas_s: float = 20.0  # no reliable canvas for this long


@dataclass
class Pending:
    """An action waiting for its expectation."""

    decision: Decision
    deadline: float
    before: Any = None


@functools.lru_cache(maxsize=4)
def load_profile(profile_dir: str) -> tuple[Perceiver, Navigator]:
    """Perception and navigation of a profile, loaded once per process."""
    p = Perceiver.load(profile_dir)
    nav = Navigator.load(Path(profile_dir) / "nav.yaml")
    bad = nav.check(set(p.book.by_name), set(p.parts.specs))
    if bad:
        raise ValueError(f"nav.yaml names what the profile does not have: {', '.join(bad)}")
    return p, nav


class Loop:
    def __init__(self, feature: Feature, actor: Actor, perceiver: Perceiver, nav: Navigator,
                 interrupts: Interrupts | None = None, limits: Limits | None = None) -> None:
        self.f = feature
        self.actor = actor
        self.perceiver = perceiver
        self.nav = nav
        self.interrupts = interrupts
        self.limits = limits or Limits()
        self.recovery = Recovery(feature.entry)
        self.rules = feature.rules()
        self.pending: Pending | None = None
        self.snap: Snapshot | None = None
        self.idle = 0
        self._repeat_key: tuple[str, ...] | None = None
        self._repeats = 0
        self._wait_rule = ""
        self._wait_since = 0.0
        self._last_line = ""
        self._last_stack = ""
        self._small: np.ndarray | None = None
        self._changed_at = time.monotonic()
        self._inputs_at_change = 0
        self._no_canvas_since: float | None = None
        self.started = time.monotonic()
        self.ticks = 0

    # --- the run --------------------------------------------------------------------------------
    def run(self) -> str:
        self.f.actor = self.actor
        self.f.begin()
        self.actor.log(f"Vòng quyết định: {self.f.title}, màn hình bắt đầu {self.f.entry}", "debug")
        try:
            while True:
                result = self.tick()
                if result is not None:
                    return result
        finally:
            self.actor.log(f"Bộ nhớ: {self.f.m.model_dump()} | {self.ticks} nhịp, "
                           f"{time.monotonic() - self.started:.0f}s", "debug")

    def tick(self) -> str | None:
        limit = self.f.max_run_s()
        if limit and time.monotonic() - self.started > limit:
            return f"{self.f.summary()} (hết thời gian chạy {limit / 60:g} phút)"
        frame, fix = self.actor.look()
        self.ticks += 1
        if not fix.reliable:
            return self._no_canvas()
        self._no_canvas_since = None
        canvas = self.actor.canvas(frame, fix)
        snap = self.perceiver.perceive(canvas)
        self.snap = self.f.snap = snap
        stack = snap.describe(read=False)
        if stack != self._last_stack:
            self._last_stack = stack
            self.actor.status(stack)
        self._watch(canvas, snap)
        d = self._decide(frame, fix, snap)
        d = self._guard(snap, d)
        self._log(snap, d)
        clicks = (d.action.at,) if isinstance(d.action, Click) else ()
        self.actor.show(snap, f"{d.rule} -> {d.action.what()}", clicks)
        return self._act(snap, d)

    # --- deciding -------------------------------------------------------------------------------
    def _decide(self, frame: np.ndarray, fix: CanvasFix, snap: Snapshot) -> Decision:
        now = time.monotonic()
        waiting: Decision | None = None
        if self.pending is not None:
            p = self.pending
            if self._met(p, snap):
                self._progress(p.decision)
            elif now < p.deadline:
                waiting = Decision(Wait(0.15, label=p.decision.action.label), f"chờ: {p.decision.rule}", "expect")
            else:
                self.pending = None
                reason = f"'{p.decision.rule}' không đạt ({p.decision.action.describe()})"
                return self._recover(snap, reason)
        if self.interrupts is not None:
            hit = self.interrupts.check(frame, fix, snap, self.f.answers)
            if hit is not None:
                return Decision(hit[1], f"interrupt: {hit[0]}", "interrupt")
        if waiting is not None:
            return waiting
        msg = self.f.goal(snap)
        if msg:
            return Decision(Done(msg), "goal", "goal")
        entry = self.f.entry
        if self.recovery.navigating:
            if snap.top != entry:
                a = self.nav.step(snap, entry)
                return Decision(a or Wait(0.5, label="không có đường"), f"recovery → {entry}", "nav")
            self.recovery.navigating_until = 0.0
        for r in self.rules:
            a = r.fire(snap)
            if a is not None:
                if isinstance(a, Navigate):
                    step = self.nav.step(snap, a.screen)
                    return Decision(step or Wait(0.5, label=f"không có đường tới {a.screen}"),
                                    f"{r.name} → {a.screen}", "nav")
                return Decision(a, r.name, "rule")
        if snap.top != entry:
            a = self.nav.step(snap, entry)
            if a is not None:
                return Decision(a, f"→ {entry}", "nav")
        return Decision(Wait(self.f.tick_s, label="không có luật nào"), "idle", "idle")

    def _met(self, p: Pending, snap: Snapshot) -> bool:
        a = p.decision.action
        try:
            ok = True
            if isinstance(a.expect, str):
                ok = snap.has(a.expect)
            elif a.expect is not None:
                ok = bool(a.expect(snap))
            if ok and a.change is not None:
                v = a.change(snap)
                ok = v is not None and v != p.before
            return ok
        except (KeyError, AttributeError):
            return False

    def _progress(self, d: Decision) -> None:
        self.pending = None
        self.idle = 0
        self._changed_at = time.monotonic()  # the game answered: not frozen
        self._inputs_at_change = self.actor.inputs
        if d.kind == "rule":
            self.recovery.reset()
        if d.action.then is not None:
            d.action.then()

    def _recover(self, snap: Snapshot, reason: str) -> Decision:
        rung, a = self.recovery.next(snap, reason)
        if isinstance(a, Fail) and self._frozen_for() >= FROZEN_HINT_S:
            a = Fail(self._frozen_message())
        self.actor.log(f"Gỡ rối ({self.recovery.level}: {rung}): {reason}", "warning")
        self.idle = 0
        self._repeats = 0
        self._wait_rule = ""
        if isinstance(a, Navigate):
            step = self.nav.step(snap, a.screen)
            return Decision(step or Wait(0.5, label="đã ở màn hình bắt đầu"), f"recovery: {rung}", "recovery")
        return Decision(a, f"recovery: {rung}", "recovery")

    def _guard(self, snap: Snapshot, d: Decision) -> Decision:
        """Loop guard: idle ticks, waits past their patience, the same decision repeated."""
        a = d.action
        now = time.monotonic()
        if d.kind == "idle":
            self.idle += 1
            if self.idle > self.limits.max_idle_ticks:
                return self._recover(snap, f"không có gì để làm sau {self.idle} nhịp")
            return d
        self.idle = 0
        if isinstance(a, Wait) and d.kind in ("rule", "nav"):
            if d.rule != self._wait_rule:
                self._wait_rule, self._wait_since = d.rule, now
            patience = a.patience or self.limits.max_idle_ticks * self.f.tick_s
            if now - self._wait_since > patience:
                return self._recover(snap, f"'{d.rule}' chờ quá {patience:g}s")
            return d
        if not isinstance(a, Wait):
            self._wait_rule = ""
        if isinstance(a, (Click, Key, Text)):
            key = (snap.top, d.rule, a.what())
            self._repeats = self._repeats + 1 if key == self._repeat_key else 1
            self._repeat_key = key
            if self._repeats > self.limits.max_repeats:
                self._repeat_key, self._repeats = None, 0
                return self._recover(snap, f"lặp lại '{d.rule}' {self.limits.max_repeats} lần trên {snap.top}")
        return d

    # --- acting ---------------------------------------------------------------------------------
    def _act(self, snap: Snapshot, d: Decision) -> str | None:
        a = d.action
        if isinstance(a, Done):
            return a.message
        if isinstance(a, Fail):
            self._fail(snap, a.message)
        if isinstance(a, Wait):
            self.actor.sleep(a.seconds)
            return None
        before = None
        if a.change is not None:
            try:
                before = a.change(snap)
            except (KeyError, AttributeError):
                before = None
        if isinstance(a, Click):
            self.actor.click(a.at, a.double)
        elif isinstance(a, Key):
            self.actor.key(a.vk)
        elif isinstance(a, Text):
            self.actor.type_into(a.text, a.at, a.clear, a.enter)
        if a.stop:
            raise TaskStopped(a.stop)
        if a.expects:
            self.pending = Pending(d, time.monotonic() + a.within + a.settle, before)
        elif a.then is not None:
            a.then()
        self.actor.sleep(a.settle)
        return None

    def _fail(self, snap: Snapshot | None, message: str) -> None:
        if snap is not None:
            try:
                dump = snap.describe(read=True)
            except Exception as exc:  # a reader that breaks must not hide the failure
                dump = f"{snap.describe(read=False)} (đọc lỗi: {exc})"
            self.actor.log(f"Snapshot lúc dừng: {dump}", "error")
        self.actor.hand_over_failure(snap, message)
        raise LoopFailed(message)

    def _no_canvas(self) -> None:
        now = time.monotonic()
        if self._no_canvas_since is None:
            self._no_canvas_since = now
        elif now - self._no_canvas_since > self.limits.no_canvas_s:
            self._fail(None, f"không tìm thấy khung game sau {self.limits.no_canvas_s:g}s")
        self.actor.sleep(0.5)
        self.actor.look(relocate=True)
        return None

    def _watch(self, canvas: np.ndarray, snap: Snapshot) -> None:
        """Frozen-client watchdog: identical frames while the bot's inputs get no response."""
        small = cv2.resize(cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY), (100, 60),
                           interpolation=cv2.INTER_AREA).astype(np.int16)
        now = time.monotonic()
        if self._small is None or float(np.abs(small - self._small).mean()) > 0.3:
            self._changed_at = now
            self._inputs_at_change = self.actor.inputs
        self._small = small
        if self._frozen_for() > self.limits.frozen_s:
            self._fail(snap, self._frozen_message())

    def _frozen_for(self) -> float:
        """Seconds the canvas has not changed while the bot's inputs were ignored (0 otherwise)."""
        if self.actor.inputs - self._inputs_at_change < self.limits.frozen_inputs:
            return 0.0
        return time.monotonic() - self._changed_at

    def _frozen_message(self) -> str:
        return (f"game không phản hồi: màn hình đứng yên {time.monotonic() - self._changed_at:.0f}s, "
                f"{self.actor.inputs - self._inputs_at_change} thao tác không có tác dụng. "
                "Hãy tải lại trình duyệt (F5) rồi chạy lại")

    # --- logging --------------------------------------------------------------------------------
    def _log(self, snap: Snapshot, d: Decision) -> None:
        if d.kind == "expect":
            return
        what = f'rule "{d.rule}"' if d.kind == "rule" else d.rule
        line = f"{snap.describe(read='seen')} | {what} → {d.action.describe()}"
        if line != self._last_line:
            self._last_line = line
            self.actor.log(line, "debug")


def run_feature(feature: Feature, ctx: RunContext) -> str:
    """Run a ``Feature`` on the decision loop with the window, switches and sinks of ``ctx``."""
    services = feature.services
    profile_dir = str(getattr(services, "profile_dir", DEFAULT_PROFILE_DIR))
    perceiver, nav = load_profile(profile_dir)
    interrupts = Interrupts(ctx.popups, ctx.panels if feature.collapse_panels else None, ctx.matcher, ctx.policy)
    return Loop(feature, Actor.from_context(ctx), perceiver, nav, interrupts).run()
