"""annotate CLI: runs locator + scene detector on stored screenshots and writes annotated PNGs.

The golden run writes to build/annotated/ (docs/06) so the images can be reviewed by hand.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from conftest import ROOT, load_expected

from ngoalong.core.capture import read_image
from ngoalong.tools.annotate import Annotator, main

BUILD_OUT = ROOT / "build" / "annotated"


def test_cli_writes_build_annotated(capsys) -> None:
    rc = main([
        str(ROOT / "docs" / "game screenshots" / "*.png"),
        str(ROOT / "docs" / "game screenshots" / "live" / "*.png"),
        "--out", str(BUILD_OUT), "--objects", "--strict",
    ])
    out = capsys.readouterr().out
    assert rc == 0, out
    expected = load_expected()
    for case in expected["reference"] + expected["live"]:
        stem = Path(case["file"]).stem
        p = BUILD_OUT / f"{stem}.png"
        assert p.is_file(), p
        assert f"{stem}.png: canvas" in out
        assert f"scene {case['scene']} " in out.split(f"{stem}.png: canvas", 1)[1].splitlines()[0]
    assert "0 with problems" in out


def test_annotator_marks_failures(tmp_path: Path) -> None:
    black = tmp_path / "black.png"
    import cv2

    cv2.imwrite(str(black), np.zeros((400, 700, 3), np.uint8))
    r = Annotator().annotate_file(black, tmp_path / "out")
    assert r.frame.failed
    assert not r.canvas.reliable and not r.scene.found
    assert r.output is not None and r.output.is_file()
    assert read_image(r.output).shape[1] == 700
    assert "unknown" in r.summary()
    rc = main([str(black), "--out", str(tmp_path / "out2"), "--strict"])
    assert rc == 1


def test_cli_no_inputs(tmp_path: Path) -> None:
    assert main([str(tmp_path / "*.png"), "--out", str(tmp_path)]) == 2
