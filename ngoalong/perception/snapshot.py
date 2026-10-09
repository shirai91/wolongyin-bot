"""``Snapshot``: one per frame, the single answer to "where am I and what do I see" (docs/08
"Target loop", "Screen identity").

    perceiver = Perceiver.load()                  # profile: parts, probes, numbers, screens
    snap = perceiver.perceive(canvas)             # canvas = 1000x600 BGR (Perceiver.canvas crops a frame)
    snap.base, snap.dialogs, snap.overlays        # "city", ("tower.dialog", "confirm.ok_cancel"), ()
    snap.top                                      # top dialog, else the base
    snap.has("tower.dialog")                      # in the stack or the overlays
    snap["tower.dialog"].page                     # readers, read lazily and cached
    snap.hud.silver                               # HUD readers (None when the HUD is hidden)
    snap.point("btn.vuot_thap")                   # where a part is, to click it
    snap.unknown                                  # generic parts nobody claimed (✕ of an unknown dialog)
    print(snap.describe())                        # city > tower.dialog{tab=Tháp, page=3, ...}

Identification runs when the Snapshot is made; readers run only when asked. Nothing here uses OCR.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from ngoalong.core.digits import GlyphSet
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import DEFAULT_PROFILE_DIR, CanvasFix
from ngoalong.perception.parts import Hit, PartLibrary
from ngoalong.perception.probes import ProbeResult, ProbeSpec, load_probes
from ngoalong.perception.readers import NumberField, ScreenState, Senses
from ngoalong.perception.screens import UNKNOWN, Identity, ScreenBook

CANVAS_W, CANVAS_H = 1000, 600


@dataclass
class Snapshot:
    identity: Identity
    senses: Senses
    book: ScreenBook
    ms_identify: float = 0.0
    _senses_ms_at_identify: float = 0.0
    _states: dict[str, ScreenState] = field(default_factory=dict)

    # --- the stack --------------------------------------------------------------------------------
    @property
    def base(self) -> str:
        return self.identity.base

    @property
    def dialogs(self) -> tuple[str, ...]:
        return self.identity.dialogs

    @property
    def overlays(self) -> tuple[str, ...]:
        return self.identity.overlays

    @property
    def unknown(self) -> tuple[Hit, ...]:
        return self.identity.unknown

    @property
    def stack(self) -> tuple[str, ...]:
        """Base first, top dialog last."""
        return (self.base, *self.dialogs)

    @property
    def top(self) -> str:
        return self.dialogs[-1] if self.dialogs else self.base

    def has(self, screen: str) -> bool:
        return screen in self.stack or screen in self.overlays

    @property
    def is_unknown(self) -> bool:
        """Something on screen that no rule explains: an unknown base or an unclaimed dialog."""
        return self.base == UNKNOWN or UNKNOWN in self.dialogs

    # --- state ------------------------------------------------------------------------------------
    def state(self, screen: str) -> ScreenState:
        """Readers of a screen in this Snapshot (KeyError when it is not on screen)."""
        if screen == "hud":
            if not self.identity.hud:
                raise KeyError("the HUD is not on screen")
        elif not self.has(screen):
            raise KeyError(f"{screen} is not on screen ({self.describe(read=False)})")
        if screen not in self._states:
            self._states[screen] = ScreenState(self.book.by_name[screen], self.senses)
        return self._states[screen]

    def __getitem__(self, screen: str) -> ScreenState:
        return self.state(screen)

    def value(self, screen: str, key: str, default: Any = None) -> Any:
        """One reader value, or ``default`` when the screen is not on screen."""
        try:
            return self.state(screen).get(key)
        except KeyError:
            return default

    @property
    def hud(self) -> ScreenState | None:
        return self.state("hud") if self.identity.hud else None

    # --- parts and probes -------------------------------------------------------------------------
    def parts(self, name: str) -> tuple[Hit, ...]:
        return self.senses.hits(name)

    def part(self, name: str) -> Hit | None:
        hs = self.senses.hits(name)
        return hs[0] if hs else None

    def point(self, name: str) -> Point | None:
        h = self.part(name)
        return h.center if h else None

    def probe(self, name: str, rect: Rect | None = None) -> ProbeResult:
        return self.senses.probe(name, rect)

    # --- timing and output ------------------------------------------------------------------------
    @property
    def ms_parts(self) -> float:
        return self.senses.scan.ms

    @property
    def ms(self) -> float:
        """Total perception time so far: identification plus every reader read since."""
        return self.ms_identify + self.senses.ms - self._senses_ms_at_identify

    def read_all(self) -> dict[str, dict[str, Any]]:
        """Every reader of every screen on screen (for logs, the debug view and the benchmark)."""
        out: dict[str, dict[str, Any]] = {}
        names = [*self.stack, *self.overlays] + (["hud"] if self.identity.hud else [])
        for n in names:
            if n != UNKNOWN and self.book.by_name[n].read:
                out[n] = self.state(n).values()
        return out

    def describe(self, read: bool | str = True) -> str:
        """One line: ``city > tower.dialog{tab=Tháp, page=3} + floating_error ? close@802,119``.

        ``read``: True reads every reader, False none, ``"seen"`` shows only the values read so far
        (the decision log: what the rules looked at, at no extra cost)."""
        def fmt(v: Any) -> str:
            if isinstance(v, Point):
                return f"{v.x},{v.y}"
            if isinstance(v, (list, tuple)):
                return "[" + " ".join(fmt(x) for x in v) + "]"
            if isinstance(v, Rect):
                return f"{v.x},{v.y},{v.w}x{v.h}"
            return str(v)

        def one(n: str) -> str:
            if not read or n == UNKNOWN or not self.book.by_name[n].read:
                return n
            if read == "seen":
                st = self._states.get(n)
                vals = st.seen() if st is not None else {}
                if not vals:
                    return n
            else:
                vals = self.state(n).values()
            return n + "{" + ", ".join(f"{k}={fmt(v)}" for k, v in vals.items()) + "}"

        s = " > ".join(one(n) for n in self.stack)
        if self.overlays:
            s += " + " + " + ".join(one(n) for n in self.overlays)
        if self.identity.hud and read and (read != "seen" or "hud" in self._states):
            s += " | " + one("hud")
        if self.unknown:
            s += " ? " + " ".join(str(h) for h in self.unknown)
        return s


class Perceiver:
    """The perception layer of one game profile (loaded once, used every tick)."""

    def __init__(self, parts: PartLibrary, probes: dict[str, ProbeSpec], numbers: dict[str, NumberField],
                 glyphs: GlyphSet, book: ScreenBook) -> None:
        self.parts = parts
        self.probes = probes
        self.numbers = numbers
        self.glyphs = glyphs
        self.book = book
        clash = set(parts.specs) & set(probes)
        if clash:
            raise ValueError(f"names used by both a part and a probe: {sorted(clash)}")
        missing = {n for n in book.signals() if n not in parts.specs and n not in probes}
        missing |= {f.when for f in numbers.values() if f.when not in parts.specs}
        if missing:
            raise ValueError(f"screens/numbers use unknown parts or probes: {sorted(missing)}")

    @classmethod
    def load(cls, profile_dir: str | Path = DEFAULT_PROFILE_DIR) -> Perceiver:
        d = Path(profile_dir)
        raw = yaml.safe_load((d / "numbers.yaml").read_text(encoding="utf-8")) or {}
        numbers = {n: NumberField(n, Rect(*v["rect"]), v["when"], v.get("parse", "int")) for n, v in raw.items()}
        return cls(PartLibrary.load(d), load_probes(d / "probes.yaml"), numbers,
                   GlyphSet.load(d / "templates" / "digits" / "game"), ScreenBook.load(d / "screens"))

    @staticmethod
    def canvas(frame: np.ndarray, fix: CanvasFix) -> np.ndarray:
        """The canvas of a window capture, at 1000x600 (rescaled when the game is zoomed)."""
        r = fix.rect
        img = frame[max(0, r.y):max(0, r.bottom), max(0, r.x):max(0, r.right)]
        if img.shape[1] != CANVAS_W or img.shape[0] != CANVAS_H:
            interp = cv2.INTER_AREA if img.shape[1] > CANVAS_W else cv2.INTER_LINEAR
            img = cv2.resize(img, (CANVAS_W, CANVAS_H), interpolation=interp)
        return img

    def perceive(self, canvas: np.ndarray) -> Snapshot:
        if canvas.shape[:2] != (CANVAS_H, CANVAS_W):
            raise ValueError(f"canvas must be {CANVAS_W}x{CANVAS_H}, got {canvas.shape[1]}x{canvas.shape[0]}")
        if canvas.ndim == 3 and canvas.shape[2] == 4:
            canvas = cv2.cvtColor(canvas, cv2.COLOR_BGRA2BGR)
        t0 = time.perf_counter()
        senses = Senses(np.ascontiguousarray(canvas), self.parts, self.probes, self.numbers, self.glyphs)
        identity = self.book.identify(senses.on, senses.hits)
        return Snapshot(identity, senses, self.book, (time.perf_counter() - t0) * 1000, senses.ms)
