"""Optional checks against the real Coowon game window. Read-only: no clicks are sent.

Skipped unless NGOALONG_LIVE=1 and a Coowon "Ngọa Long" window is open.
"""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Win32 only"),
    pytest.mark.skipif(os.environ.get("NGOALONG_LIVE") != "1", reason="set NGOALONG_LIVE=1 to run"),
]


def test_live_discover_and_capture() -> None:
    from ngoalong.core.drivers.win32 import Win32Capturer
    from ngoalong.core.window import GameWindow

    windows = GameWindow.discover()
    if not windows:
        pytest.skip("no Coowon game window open")
    gw = windows[0]
    assert gw.is_alive()
    w, h = gw.client_size()
    frame = Win32Capturer(gw).grab()
    assert frame.shape == (h, w, 3)
    assert w >= 1000 and h >= 600
    assert frame.mean() > 5  # not an all-black capture
