"""Optional engine checks against the real Coowon game window. Read-only: no clicks are sent.

Skipped unless NGOALONG_LIVE=1 and a Coowon "Ngọa Long" window is open.
"""

from __future__ import annotations

import os
import sys

import pytest

from conftest import PROFILE

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Win32 only"),
    pytest.mark.skipif(os.environ.get("NGOALONG_LIVE") != "1", reason="set NGOALONG_LIVE=1 to run"),
]


def test_live_probe_and_context(qapp, tmp_path) -> None:
    from ngoalong.app import AppServices
    from ngoalong.core.drivers.fake import RecordingInputDriver
    from ngoalong.engine.control import RunControl
    from ngoalong.engine.runner import RunHooks
    from ngoalong.features.quay_ruong import QuayRuong

    drv = RecordingInputDriver()  # never the real driver: nothing is clicked
    s = AppServices(settings_path=None, profile_dir=PROFILE, input_factory=lambda gw: drv, log_dir=tmp_path)
    windows = s.discover_windows()
    if not windows:
        pytest.skip("no Coowon game window open")
    s.bind(windows[0])
    frame, fix, scene = s.probe()
    assert fix.reliable, fix
    print(f"\ncanvas {fix.rect} x{fix.scale} scene {scene.name} {scene.score:.2f}")
    hooks = RunHooks("live", tmp_path / "run-live", RunControl(), lambda *a: None, lambda *a: None, lambda *a: None)
    ctx = s.make_context(QuayRuong(), hooks)
    assert ctx.canvas().reliable
    assert ctx.detect_scene().name == scene.name
    assert drv.clicks == []
    s.shutdown()
