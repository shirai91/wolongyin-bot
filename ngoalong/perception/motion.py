"""Motion across a few frames: what a single-frame probe cannot see (docs/08 "UI parts").

The campaign field is a still picture: between two captures only animations change. The next army
has a pale-blue ring pulsing around its feet (one cycle every 2.0 s live, 2026-10-11, Gia Cát-Thọ
Xuân), so the pixels it touches turn bluer and back while everything else stays the same, apart
from a few small animations (fountain, torches) and the chat. That finds the army even when its
beam is hidden by the HUD::

    rings = find_rings([canvas_t0, canvas_t1, canvas_t2, ...], area=Rect(0, 0, 1000, 525))

Live: the moving pixels of one cycle cover an 88 x 51 px ellipse (fill 0.48; 0.10 when only the
thin outline at full size moved) centred on the army's feet; the fountain's blobs are <= 16 px, the
visible end of a beam is tall and thin. The ring is invisible for part of the cycle: 5 frames
0.3 s apart found it in every window of a 4 s burst, 3 frames missed 6 of 34.
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from ngoalong.core.geometry import Rect

BLUE_DELTA = 12  # |change of B - R| that counts, with B changing more than R (the ring adds cyan)
RING_W = (45, 120)
RING_H = (22, 70)
RING_ASPECT = (1.3, 2.4)  # width / height (seen at a slant)
RING_FILL = (0.05, 0.9)  # moving pixels / bounding box: 0.10 for the thin outline at full size, 0.48 swept
RING_MIN_AREA = 300


def bluish_change(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pixels whose blue-over-red changed between two frames (either way), red staying steadier."""
    d = b.astype(np.int16) - a.astype(np.int16)
    db, dr = d[..., 0], d[..., 2]
    return (np.abs(db - dr) > BLUE_DELTA) & (np.abs(dr) < np.abs(db))


def find_rings(frames: Sequence[np.ndarray], area: Rect) -> list[Rect]:
    """Ellipse-shaped regions of bluish change over consecutive ``frames`` inside ``area``, largest
    first (canvas pixels). Needs at least 2 frames; a stretch of ~1 s of a 2 s cycle shows the ring."""
    if len(frames) < 2:
        return []
    crop = [f[area.y:area.bottom, area.x:area.right] for f in frames]
    moved = np.zeros(crop[0].shape[:2], dtype=bool)
    for a, b in zip(crop, crop[1:], strict=False):
        moved |= bluish_change(a, b)
    mask = cv2.morphologyEx(moved.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _lab, st, _c = cv2.connectedComponentsWithStats(mask)
    rings = []
    for i in range(1, n):
        x, y, w, h, px = (int(v) for v in st[i])
        if not (RING_W[0] <= w <= RING_W[1] and RING_H[0] <= h <= RING_H[1] and px >= RING_MIN_AREA):
            continue
        if RING_ASPECT[0] <= w / h <= RING_ASPECT[1] and RING_FILL[0] <= px / (w * h) <= RING_FILL[1]:
            rings.append((px, Rect(area.x + x, area.y + y, w, h)))
    return [r for _px, r in sorted(rings, key=lambda t: -t[0])]
