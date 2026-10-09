"""Recovery ladder (docs/08 "Concepts" 5): what the loop does when an expectation is not met in
time, the same decision repeats on the same screen, or nothing moves for too long.

Each trouble climbs one rung; a met expectation of a feature rule puts the ladder back to 0:

1. close the top dialog by its ✕ (skipped when there is no dialog with a ✕)
2. ESC
3. ``Navigate(entry)``: walk the screen graph back to the feature's entry screen, ignoring the
   feature's rules, for at most ``NAV_BUDGET_S`` seconds
4. fail: the loop stops the run with a screenshot and the Snapshot dump
"""

from __future__ import annotations

import time

from ngoalong.core.input import VK_ESCAPE
from ngoalong.engine.actions import Action, Click, Fail, Key, Navigate
from ngoalong.engine.actor import close_point
from ngoalong.perception import Snapshot

NAV_BUDGET_S = 45.0
RUNG_NAMES = ("", "đóng hộp thoại", "ESC", "về màn hình bắt đầu", "dừng")


class Recovery:
    def __init__(self, entry: str) -> None:
        self.entry = entry
        self.level = 0
        self.reasons: list[str] = []
        self.navigating_until = 0.0

    @property
    def navigating(self) -> bool:
        return time.monotonic() < self.navigating_until

    def reset(self) -> None:
        self.level = 0
        self.reasons.clear()
        self.navigating_until = 0.0

    def next(self, snap: Snapshot, reason: str) -> tuple[str, Action]:
        """Climb one rung for ``reason``; returns (rung name, action)."""
        self.reasons.append(reason)
        self.navigating_until = 0.0
        self.level += 1
        if self.level == 1:
            p = close_point(snap)
            if p is not None:
                before = snap.stack
                return RUNG_NAMES[1], Click(p, label=f"✕ {snap.top}", expect=lambda s: s.stack != before)
            self.level = 2
        if self.level == 2:
            return RUNG_NAMES[2], Key(VK_ESCAPE, label="esc")
        if self.level == 3:
            self.navigating_until = time.monotonic() + NAV_BUDGET_S
            return RUNG_NAMES[3], Navigate(self.entry, label=self.entry)
        why = "; ".join(self.reasons[-4:])
        return RUNG_NAMES[4], Fail(f"không thoát được tình huống ({why})")
