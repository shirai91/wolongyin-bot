"""Draw a ``DebugFrame`` onto an image (docs/06 debug overlay table).

* game canvas: cyan rectangle (red when not reliable), label ``WxH @(x,y) xS``
* object found: green rectangle around the match, label ``name score``
* object expected but not found: red rectangle around the search region, ``name best < threshold``
* planned click: yellow crosshair; scene-object markers: small magenta crosses
* status line: text under the canvas (a band is added below the image when there is no room)

OpenCV's Hershey fonts are ASCII-only, so labels are transliterated (``Tháp`` -> ``Thap``).
"""

from __future__ import annotations

import unicodedata

import cv2
import numpy as np

from ngoalong.core.detection import Detection
from ngoalong.core.geometry import Point, Rect
from ngoalong.debug.model import DebugFrame

# BGR
CYAN = (255, 255, 0)
GREEN = (0, 200, 0)
RED = (0, 0, 255)
YELLOW = (0, 255, 255)
MAGENTA = (255, 0, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALE = 0.42
STATUS_BAND = 22


def ascii_label(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    norm = unicodedata.normalize("NFKD", text)
    out = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return out.encode("ascii", "replace").decode("ascii")


def detection_label(d: Detection) -> str:
    if d.found:
        return f"{d.name} {d.score:.2f}"
    return f"{d.name} {d.score:.2f} < {d.threshold:.2f}"


def _put_label(img: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    """Text with a dark background box, kept inside the image; (x, y) is the box's top-left."""
    text = ascii_label(text)
    (tw, th), base = cv2.getTextSize(text, FONT, FONT_SCALE, 1)
    h, w = img.shape[:2]
    x = max(0, min(x, w - tw - 4))
    y = max(0, min(y, h - th - base - 4))
    cv2.rectangle(img, (x, y), (x + tw + 4, y + th + base + 4), BLACK, -1)
    cv2.putText(img, text, (x + 2, y + th + 2), FONT, FONT_SCALE, color, 1, cv2.LINE_AA)


def _rect(img: np.ndarray, r: Rect, color: tuple[int, int, int], thickness: int = 1) -> None:
    cv2.rectangle(img, (r.x, r.y), (r.right - 1, r.bottom - 1), color, thickness)


def draw_crosshair(img: np.ndarray, p: Point, color: tuple[int, int, int] = YELLOW, size: int = 10) -> None:
    cv2.line(img, (p.x - size, p.y), (p.x + size, p.y), color, 2)
    cv2.line(img, (p.x, p.y - size), (p.x, p.y + size), color, 2)
    cv2.circle(img, (p.x, p.y), size // 2, color, 1)


def draw_detection(img: np.ndarray, d: Detection) -> None:
    color = GREEN if d.found else RED
    _rect(img, d.rect, color, 2 if d.found else 1)
    _put_label(img, detection_label(d), d.rect.x, d.rect.y - 16, color)


def render(frame: DebugFrame, image: np.ndarray | None = None) -> np.ndarray:
    """Return an annotated BGR copy of ``image`` (default: ``frame.image``)."""
    src = image if image is not None else frame.image
    if src is None:
        raise ValueError("DebugFrame has no image to draw on")
    img = src.copy() if src.ndim == 3 else cv2.cvtColor(src, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    status_y: int | None = None
    if frame.canvas is not None:
        c = frame.canvas
        color = CYAN if c.reliable else RED
        _rect(img, c.rect, color, 2)
        label = f"canvas {c.rect.w}x{c.rect.h} @({c.rect.x},{c.rect.y}) x{c.scale:.2f}"
        if not c.reliable:
            label += f" {c.confidence.value.upper()}" + (" (last good)" if c.stale else "")
        _put_label(img, label, c.rect.x, max(0, c.rect.y - 18), color)  # inside when cropped
        if c.rect.bottom + STATUS_BAND <= img.shape[0]:
            status_y = c.rect.bottom + 2

    for d in frame.detections:
        draw_detection(img, d)
    for m in frame.markers:
        cv2.drawMarker(img, (m.point.x, m.point.y), MAGENTA, cv2.MARKER_TILTED_CROSS, 8, 1)
        _put_label(img, m.name, m.point.x + 5, m.point.y + 3, MAGENTA)
    for p in frame.clicks:
        draw_crosshair(img, p)

    status = frame.status_line()
    if status_y is None:
        band = np.zeros((STATUS_BAND, img.shape[1], 3), np.uint8)
        img = np.vstack([img, band])
        status_y = img.shape[0] - STATUS_BAND
    x0 = frame.canvas.rect.x if frame.canvas is not None else 0
    _put_label(img, status, x0, status_y, WHITE)
    return img
