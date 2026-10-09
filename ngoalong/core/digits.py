"""Read numbers drawn in the game's bitmap font, without OCR (docs/08 "Screen state", ``digits``).

Every number the supported features need (silver, Quân lệnh, Thí luyện lệnh, chest count, page,
research level, shop price, floor) uses the same small font; only its colour changes (white, yellow,
green). So a region is turned into an **ink mask** (max(B, G, R) > ``level``: light text on the dark
boxes, independent of hue), split into characters at blank columns, and each character is matched
against glyph masks learned from labelled samples (``tools/cut_digits.py``).

Glyphs live in ``templates/digits/<font>/<char>.png`` (``slash`` for "/"); anti-aliasing varies a
little with the background, so a character may have variants ``<char>_2.png``, ``<char>_3.png``. A character that matches
no glyph well enough reads as ``?`` and the whole read reports it, so a caller never acts on a
half-read number.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ngoalong.core.capture import read_image

DEFAULT_LEVEL = 150  # ink threshold: digits stay, box borders and backgrounds drop out
MIN_SCORE = 0.70  # glyph match needed for a character
FILE_CHARS = {"slash": "/"}  # file stem -> character (characters that can't be file names)
CHAR_FILES = {v: k for k, v in FILE_CHARS.items()}


GLYPH_HEIGHT = 9  # digits are 9 px tall at canvas scale 1.0
WORD_GAP = 4  # blank columns between words; digits of one number are 0-2 px apart
CARET_MIN_ROWS = 12  # an inked column taller than this is a text caret ("3|" in an input box)


@dataclass(frozen=True, slots=True)
class Glyph:
    char: str
    mask: np.ndarray  # uint8 0/255, tight box


@dataclass(frozen=True, slots=True)
class DigitRead:
    text: str  # characters left to right; "?" where no glyph matched
    scores: tuple[float, ...]  # one per character
    boxes: tuple[tuple[int, int, int, int], ...]  # x, y, w, h in the region
    clipped_left: bool = False  # ink touches the region's left edge: a digit may be cut
    clipped_right: bool = False  # ink touches the right edge

    @property
    def clipped(self) -> bool:
        return self.clipped_left or self.clipped_right

    @property
    def ok(self) -> bool:
        """Every character read, and nothing cut off. A region that starts inside a digit can read
        it as another digit with a high score, so a clipped read is never ok."""
        return bool(self.text) and "?" not in self.text and not self.clipped

    def value(self) -> int | None:
        """The number as an int (only digits read), else None."""
        return int(self.text) if self.ok and self.text.isdigit() else None

    def fraction(self) -> tuple[int, int] | None:
        """``a/b`` read as (a, b), else None (e.g. "593/100")."""
        if not self.ok or self.text.count("/") != 1:
            return None
        a, b = self.text.split("/")
        return (int(a), int(b)) if a.isdigit() and b.isdigit() else None

    def before_slash(self) -> int | None:
        """The number before "/" ("858983797/2425000…" -> 858983797). The part after the slash may
        be cut off by its box (the HUD shows the cap only partly), so only the left edge must be
        clean."""
        if self.clipped_left or "/" not in self.text:
            return None
        a = self.text.split("/", 1)[0]
        return int(a) if a.isdigit() else None

    def last_group(self, min_gap: int = WORD_GAP) -> DigitRead:
        """The characters after the last word gap ("Tầng 141" -> "141"): for centred labels whose
        number moves with its length, read a wide region and keep the number at its end."""
        start = 0
        for i in range(1, len(self.boxes)):
            prev, cur = self.boxes[i - 1], self.boxes[i]
            if cur[0] - (prev[0] + prev[2]) >= min_gap:
                start = i
        return DigitRead(self.text[start:], self.scores[start:], self.boxes[start:], False, self.clipped_right)

    def parse(self, kind: str) -> int | tuple[int, int] | None:
        """``int`` | ``fraction`` | ``before_slash`` | ``last_int`` (numbers.yaml ``parse``)."""
        if kind == "last_int":
            return self.last_group().value()
        if kind == "int":
            return self.value()
        if kind == "fraction":
            return self.fraction()
        if kind == "before_slash":
            return self.before_slash()
        raise ValueError(f"unknown parse kind {kind!r}")




def ink_mask(bgr: np.ndarray, level: int = DEFAULT_LEVEL) -> np.ndarray:
    """uint8 0/255 mask of light text pixels; text carets (full-height lines) removed."""
    img = bgr if bgr.ndim == 2 else bgr[:, :, :3].max(axis=2)
    mask = np.where(img > level, 255, 0).astype(np.uint8)
    mask[:, (mask > 0).sum(axis=0) >= CARET_MIN_ROWS] = 0
    return mask


def segment(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Character boxes (x, y, w, h): runs of inked columns, each trimmed to its inked rows."""
    cols = mask.any(axis=0)
    boxes: list[tuple[int, int, int, int]] = []
    x, w = 0, len(cols)
    while x < w:
        if not cols[x]:
            x += 1
            continue
        x0 = x
        while x < w and cols[x]:
            x += 1
        rows = np.flatnonzero(mask[:, x0:x].any(axis=1))
        boxes.append((x0, int(rows[0]), x - x0, int(rows[-1]) - int(rows[0]) + 1))
    return boxes


def _score(piece: np.ndarray, glyph: np.ndarray) -> float:
    """Best normalised correlation of ``glyph`` over ``piece`` padded by 2 px (sizes may differ by
    a pixel where anti-aliasing crosses the ink level)."""
    pad = 2
    big = cv2.copyMakeBorder(piece, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    small = glyph
    if small.shape[0] > big.shape[0] or small.shape[1] > big.shape[1]:
        big, small = cv2.copyMakeBorder(glyph, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0), piece
        if small.shape[0] > big.shape[0] or small.shape[1] > big.shape[1]:
            return -1.0
    res = cv2.matchTemplate(big.astype(np.float32), small.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    res = np.where(np.isfinite(res), res, -1.0)
    return float(res.max())


class GlyphSet:
    def __init__(self, glyphs: list[Glyph], level: int = DEFAULT_LEVEL, min_score: float = MIN_SCORE) -> None:
        if not glyphs:
            raise ValueError("empty glyph set")
        self.glyphs = glyphs
        self.level = level
        self.min_score = min_score
        self.max_width = max(g.mask.shape[1] for g in glyphs)

    @classmethod
    def load(cls, directory: str | Path, level: int = DEFAULT_LEVEL, min_score: float = MIN_SCORE) -> GlyphSet:
        d = Path(directory)
        glyphs = []
        for p in sorted(d.glob("*.png")):
            stem = p.stem.split("_")[0]  # "0_2.png" = second variant of "0"
            char = FILE_CHARS.get(stem, stem)
            img = read_image(p, cv2.IMREAD_GRAYSCALE)
            glyphs.append(Glyph(char, np.where(img > 127, 255, 0).astype(np.uint8)))
        return cls(glyphs, level, min_score)

    def classify(self, piece: np.ndarray) -> tuple[str, float]:
        best = max(((g.char, _score(piece, g.mask)) for g in self.glyphs), key=lambda cs: cs[1])
        return best if best[1] >= self.min_score else ("?", best[1])

    def _split(self, mask: np.ndarray, box: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
        """A run wider than any glyph is two or more touching characters. Find the glyph sequence
        that covers it best (dynamic programming over start columns; neighbours may overlap by
        one column where the font kerns), and return one box per glyph."""
        x0, y, w, h = box
        run = mask[y:y + h, x0:x0 + w]
        widths = sorted({g.mask.shape[1] for g in self.glyphs})
        # best[x] = (total score, count, previous x, width) for a cover of columns [0, x)
        best: dict[int, tuple[float, int, int, int]] = {0: (0.0, 0, -1, 0)}
        for x in range(w):
            if x not in best:
                continue
            total, n, _, _ = best[x]
            for gw in widths:
                if x + gw > w + 1:
                    continue
                piece = run[:, x:min(w, x + gw)]
                s = max(_score(piece, g.mask) for g in self.glyphs if g.mask.shape[1] == gw)
                for nxt in (x + gw - 1, x + gw):  # kerning overlap, or touching
                    nxt = min(nxt, w)
                    if nxt <= x:
                        continue
                    cand = (total + s, n + 1, x, gw)
                    old = best.get(nxt)
                    if old is None or cand[0] / cand[1] > old[0] / old[1]:
                        best[nxt] = cand
        end = max((k for k in best if k >= w - 1), default=None)
        if end is None or end == 0:
            return [box]
        out: list[tuple[int, int, int, int]] = []
        x = end
        while x > 0:
            _, _, prev, gw = best[x]
            out.append((x0 + prev, y, min(gw, w - prev), h))
            x = prev
        return out[::-1]

    def read(self, bgr: np.ndarray) -> DigitRead:
        mask = ink_mask(bgr, self.level)
        boxes: list[tuple[int, int, int, int]] = []
        for b in segment(mask):
            boxes.extend(self._split(mask, b) if b[2] > self.max_width + 1 else [b])
        chars, scores = [], []
        for x, y, w, h in boxes:
            c, s = self.classify(mask[y:y + h, x:x + w])
            chars.append(c)
            scores.append(s)
        return DigitRead("".join(chars), tuple(scores), tuple(boxes),
                         bool(mask[:, 0].any()), bool(mask[:, -1].any()))
