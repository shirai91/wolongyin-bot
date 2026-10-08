"""Text recognition (OCR) of game text: names, counters, scores. Cross-platform.

The engine is RapidOCR (PaddleOCR models on ONNX Runtime; ``pip install rapidocr onnxruntime``, no
system install, same on Windows / macOS / Linux). Checked on the Thành tựu and army dialogs
(2026-10-06): it reads map, task and army names, Chinese names (董卓), "48/50" counts and the score
from a 2x upscale, but drops some Vietnamese accented letters ("Đổng" -> "Đng", "Tấn" -> "Tn"), so
names are compared with ``normalize`` + ``similarity``, never as exact strings.

``OcrLine`` / ``OcrWord`` rectangles are in the pixels of the image given to ``read`` (the upscale
and margin are undone). ``RunContext.read_text`` moves them to canvas coordinates. RapidOCR gives
whole lines: each line has a single word covering it.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np
from rapidfuzz import fuzz

from ngoalong.core.errors import NgoaLongError
from ngoalong.core.geometry import Rect

log = logging.getLogger(__name__)

UPSCALE = 2.0  # the game fonts are ~11 px high; read reliably from 2x
PAD = 10  # px of replicated border around an upscaled crop


class OcrUnavailable(NgoaLongError):
    """No OCR engine (``rapidocr`` / ``onnxruntime`` missing or broken)."""


@dataclass(frozen=True, slots=True)
class OcrWord:
    text: str
    rect: Rect


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    rect: Rect
    words: tuple[OcrWord, ...] = ()

    @property
    def norm(self) -> str:
        return normalize(self.text)

    def moved(self, dx: float, dy: float, scale: float = 1.0) -> OcrLine:
        """The line with every rectangle mapped by ``p * scale + (dx, dy)``."""
        def m(r: Rect) -> Rect:
            return Rect(round(r.x * scale + dx), round(r.y * scale + dy),
                        max(1, round(r.w * scale)), max(1, round(r.h * scale)))
        return OcrLine(self.text, m(self.rect), tuple(OcrWord(w.text, m(w.rect)) for w in self.words))


class OcrEngine(Protocol):
    def read(self, image: np.ndarray) -> list[OcrLine]:
        """Lines of text in a BGR image, top to bottom."""
        ...


# --- text helpers -----------------------------------------------------------------------------------
# OCR look-alikes of the Latin recognizer on the game font, applied after diacritics are removed.
_CONFUSABLE = str.maketrans({"đ": "d", "ð": "d", "|": "l", "$": "s"})
_KEEP = re.compile(r"[^a-z0-9/ ]+")
_FRACTION = re.compile(r"(\d+)\s*/\s*(\d+)")
_SUFFIX = re.compile(r"\(.*?\)|\blv\s*\d+\b")  # "(Đội-Tinh) Lv10" after an army name


def normalize(text: str) -> str:
    """Lower case, no diacritics (đ -> d), only ``a-z 0-9 /`` and single spaces."""
    s = unicodedata.normalize("NFKD", text.lower()).translate(_CONFUSABLE)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(_KEEP.sub(" ", s).split())


def army_name(title: str) -> str:
    """Normalised army name from an army dialog title (drops "(Đội-Tinh)" and "Lv10")."""
    return " ".join(_SUFFIX.sub(" ", normalize_keep_parens(title)).split())


def normalize_keep_parens(text: str) -> str:
    s = unicodedata.normalize("NFKD", text.lower()).translate(_CONFUSABLE)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9/() ]+", " ", s).split())


# Two OCR readings are the same name at this similarity or more (user's rule: "> 85%"). OCR misreads
# seen live score 87.5-100 ("Hàn Huyềr" / "Hàn Huyền" 87.5, "Đng Trác" / "Đổng Trác" 93.3), two
# different dungeons <= 78 ("Viên Thuật" / "Viên Thiệu" 77.8). Names that differ only by a digit
# ("... Phúc 1" / "... Phúc 2", 90.9) both pass: callers pick the BEST match, never the first.
MATCH = 0.85


def similarity(a: str, b: str) -> float:
    """0..1 likeness of two texts after ``normalize`` (spaces ignored): RapidFuzz ``fuzz.ratio``."""
    x, y = normalize(a).replace(" ", ""), normalize(b).replace(" ", "")
    if not x or not y:
        return 0.0
    return fuzz.ratio(x, y) / 100.0


def prefix_similarity(short: str, long: str) -> float:
    """Likeness of ``short`` to the start of ``long`` (a name cut off by a narrow card or badge)."""
    x, y = normalize(short).replace(" ", ""), normalize(long).replace(" ", "")
    if not x or not y:
        return 0.0
    return fuzz.ratio(x, y[:len(x)]) / 100.0


def best_match(text: str, choices: list[str], threshold: float = MATCH) -> str | None:
    """The choice most like ``text`` (``similarity``), or ``None`` under ``threshold``."""
    scored = sorted(((similarity(text, c), c) for c in choices), reverse=True)
    if scored and scored[0][0] >= threshold:
        return scored[0][1]
    return None


def fraction(text: str) -> tuple[int, int] | None:
    """``(have, need)`` from "48/50" (OCR spaces allowed), else ``None``."""
    m = _FRACTION.search(text.replace("O", "0").replace("o", "0"))
    return (int(m.group(1)), int(m.group(2))) if m else None


def number(text: str) -> int | None:
    m = re.search(r"\d+", text.replace("O", "0"))
    return int(m.group()) if m else None


# --- RapidOCR ---------------------------------------------------------------------------------------
class RapidOcr:
    """RapidOCR (PaddleOCR PP-OCRv6 models on ONNX Runtime): pip only, same on Windows/macOS/Linux.

    The default Chinese+Latin model ships inside the ``rapidocr`` wheel (no download) and reads both
    the Vietnamese texts (a few accented letters dropped: "Đổng" -> "Đng") and Chinese army names.
    """

    def __init__(self, upscale: float = UPSCALE, pad: int = PAD) -> None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise OcrUnavailable(f"thiếu thư viện OCR (rapidocr, onnxruntime): {exc}") from exc
        self._engine = RapidOCR()
        self.upscale = upscale
        self.pad = pad
        self._lock = threading.Lock()
        log.info("OCR: RapidOCR (ONNX Runtime)")

    def read(self, image: np.ndarray) -> list[OcrLine]:
        if image.size == 0:
            return []
        f, pad = self.upscale, self.pad
        img = image[:, :, :3] if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        big = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f != 1 else img
        # text touching the crop's edge is missed without a margin ("Điểm:: 44")
        big = cv2.copyMakeBorder(big, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        with self._lock:  # one engine shared by every run
            result = self._engine(big)
        boxes, txts = getattr(result, "boxes", None), getattr(result, "txts", None)
        if boxes is None or txts is None:
            return []
        lines: list[OcrLine] = []
        for poly, text in zip(boxes, txts, strict=False):
            xs = [(float(pt[0]) - pad) / f for pt in poly]
            ys = [(float(pt[1]) - pad) / f for pt in poly]
            rect = Rect.from_ltrb(round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys)))
            lines.append(OcrLine(str(text), rect, (OcrWord(str(text), rect),)))
        lines.sort(key=lambda ln: (ln.rect.y, ln.rect.x))
        return lines


_default: OcrEngine | None = None
_default_error: str | None = None
_default_lock = threading.Lock()


def default_ocr() -> OcrEngine | None:
    """The shared OCR engine, created on first use; ``None`` (logged once) if unavailable."""
    global _default, _default_error
    with _default_lock:
        if _default is None and _default_error is None:
            try:
                _default = RapidOcr()
            except OcrUnavailable as exc:
                _default_error = str(exc)
                log.warning("OCR unavailable: %s", exc)
            except Exception as exc:  # a broken onnxruntime install must not stop the app
                _default_error = str(exc)
                log.exception("OCR init failed")
        return _default


def default_ocr_error() -> str | None:
    return _default_error
