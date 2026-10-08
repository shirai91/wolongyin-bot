"""Chrome DevTools Protocol drivers for FlashBrowser on macOS/Linux: POST-MVP, not implemented.

The interfaces exist so a CDP driver can be added later without touching feature code (docs/06).
"""

from __future__ import annotations

import numpy as np

from ngoalong.core.geometry import Point
from ngoalong.core.input import MouseButton

_MSG = "CDP drivers are post-MVP (FlashBrowser support); the MVP supports Coowon on Windows only"


class CdpInputDriver:
    def __init__(self, endpoint: str = "http://127.0.0.1:9222") -> None:
        self.endpoint = endpoint

    def click(self, p: Point, button: MouseButton = "left", double: bool = False) -> None:
        raise NotImplementedError(_MSG)

    def key(self, vk: int) -> None:
        raise NotImplementedError(_MSG)

    def text(self, s: str) -> None:
        raise NotImplementedError(_MSG)


class CdpCapturer:
    def __init__(self, endpoint: str = "http://127.0.0.1:9222") -> None:
        self.endpoint = endpoint

    def grab(self) -> np.ndarray:
        raise NotImplementedError(_MSG)
