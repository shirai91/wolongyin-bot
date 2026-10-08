"""``Detection``: one record per template search, the data behind the debug overlay (docs/06).

It lives in core so the vision layer does not depend on the debug package;
``ngoalong.debug.model`` (debug phase) should re-export it next to ``DebugFrame``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ngoalong.core.geometry import Rect


@dataclass(frozen=True, slots=True)
class Detection:
    """Result of one search.

    ``rect`` is the matched rectangle when ``found``; otherwise the search region (ROI or the whole
    frame), which is what the overlay draws in red. ``best_rect`` is always the best-scoring
    location (``None`` when the template did not fit in the search region).
    """

    name: str
    rect: Rect
    score: float
    threshold: float
    found: bool
    best_rect: Rect | None = None
    roi: Rect | None = None
    timestamp: float = field(default_factory=time.time)
