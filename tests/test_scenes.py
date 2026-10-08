"""SceneDetector: golden scene names, modal-first order, overlays, objects and YAML loading."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from conftest import PROFILE, ROOT, frame, golden_cases

from ngoalong.core.geometry import Point, Rect
from ngoalong.core.locator import CanvasFix, CanvasLocator, Confidence
from ngoalong.core.scenes import (
    UNKNOWN,
    SceneDetector,
    SceneError,
    load_scene_defs,
)
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher

SEVEN = {"city", "field", "world_map", "tower.dialog", "tower.confirm", "tower.battle", "chest.wheel"}


def _fix(rel: str) -> CanvasFix:
    return CanvasLocator.from_profile(PROFILE).locate(frame(rel))


@pytest.mark.parametrize("case", golden_cases("reference", "live"))
def test_golden_scene(scene_detector: SceneDetector, case: dict) -> None:
    img = frame(case["file"])
    canvas = _fix(case["file"])
    m = scene_detector.detect(img, canvas)
    assert m.name == case["scene"], f"got {m.name} ({m.score:.2f}), closest {m.candidate}"
    assert m.found and m.score >= 0.85
    assert set(case.get("overlays", [])) <= set(m.overlays)
    if not case.get("overlays"):
        assert m.overlays == ()
    assert all(d.found for d in m.detections)


def test_seven_reference_scenes_are_declared(scene_detector: SceneDetector) -> None:
    assert SEVEN <= set(scene_detector.names)


def test_modal_dialogs_are_checked_first(scene_detector: SceneDetector) -> None:
    order = scene_detector.check_order
    kinds = [scene_detector.get(n).kind for n in order]
    assert kinds == sorted(kinds, key=lambda k: {"modal": 0, "screen": 1}[k])
    assert order.index("tower.confirm") < order.index("tower.dialog")
    assert "overlay.toast" not in order  # overlays never win


def test_unknown_scene_reports_closest(scene_detector: SceneDetector) -> None:
    img = np.full((600, 1000, 3), 30, np.uint8)
    canvas = CanvasFix(Rect(0, 0, 1000, 600), 1.0, Confidence.HIGH)
    m = scene_detector.detect(img, canvas)
    assert m.name == UNKNOWN and not m.found
    assert m.candidate in scene_detector.names
    assert m.detections and not any(d.found for d in m.detections)
    assert set(m.scores) == set(scene_detector.check_order)


def test_check_single_scene(scene_detector: SceneDetector) -> None:
    rel = "docs/game screenshots/tower-ui-enter-confirmation.png"
    img, canvas = frame(rel), _fix(rel)
    assert scene_detector.check(img, canvas, "tower.dialog").found  # the dialog is still underneath
    miss = scene_detector.check(img, canvas, "chest.wheel")
    assert not miss.found and miss.candidate == "chest.wheel"
    with pytest.raises(SceneError):
        scene_detector.check(img, canvas, "no.such.scene")


def test_objects_template_and_offset(scene_detector: SceneDetector) -> None:
    rel = "docs/game screenshots/tower-ui-enter-confirmation.png"
    img, canvas = frame(rel), _fix(rel)
    ok = scene_detector.find_object(img, canvas, "tower.confirm", "ok")
    assert ok.detection is not None and ok.detection.found
    # match centre lands within 4 px of the docs/07 "确定" position (453, 331)
    exp = canvas.to_frame((453, 331))
    assert ok.point is not None and abs(ok.point.x - exp.x) <= 4 and abs(ok.point.y - exp.y) <= 4
    cancel = scene_detector.find_object(img, canvas, "tower.confirm", "cancel")
    assert cancel.detection is None and cancel.point == canvas.to_frame((546, 331))
    with pytest.raises(SceneError):
        scene_detector.find_object(img, canvas, "tower.confirm", "nope")
    assert {o.name for o in scene_detector.find_objects(img, canvas, "tower.confirm")} == {"ok", "cancel"}


def test_object_falls_back_to_offset_when_template_missing(scene_detector: SceneDetector) -> None:
    rel = "docs/game screenshots/main-game.png"
    img, canvas = frame(rel), _fix(rel)
    r = scene_detector.find_object(img, canvas, "tower.confirm", "ok")
    assert r.detection is not None and not r.detection.found
    assert r.point == canvas.to_frame((453, 331))


def test_scenes_follow_canvas_scale(scene_detector: SceneDetector) -> None:
    import cv2

    canvas_img = frame("docs/game screenshots/tower-ui.png")[106:706, 272:1272]
    zoomed = cv2.resize(canvas_img, (1250, 750), interpolation=cv2.INTER_LINEAR)
    fix = CanvasFix(Rect(0, 0, 1250, 750), 1.25, Confidence.HIGH)
    assert scene_detector.detect(zoomed, fix).name == "tower.dialog"


# --- profile consistency ------------------------------------------------------------------------
def _crops() -> dict[str, tuple[int, int, int, int]]:
    spec = importlib.util.spec_from_file_location("cut_templates_for_scenes", ROOT / "tools" / "cut_templates.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _sources, crops = mod.load_crops()
    return {c.name: c.rect for c in crops}


def test_every_referenced_template_exists(scene_detector: SceneDetector) -> None:
    store = TemplateStore(PROFILE / "templates")
    missing = sorted(t for t in scene_detector.templates() if t not in store)
    assert not missing


def test_signature_positions_match_crops() -> None:
    crops = _crops()
    for scene in load_scene_defs(PROFILE / "scenes"):
        refs = list(scene.signature) + [o for o in scene.objects.values() if o.template]
        for ref in refs:
            assert ref.template in crops, f"{scene.name}: {ref.template} is not cut by template_crops.yaml"
            assert tuple(ref.at) == crops[ref.template][:2], f"{scene.name}: {ref.template} at {ref.at}"


# --- loading ------------------------------------------------------------------------------------
def _write(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text, encoding="utf-8")


def test_duplicate_scene_across_files(tmp_path: Path) -> None:
    body = "a:\n  signature:\n    - {template: x, at: [0, 0]}\n"
    _write(tmp_path, "one.yaml", body)
    _write(tmp_path, "two.yaml", body)
    with pytest.raises(SceneError, match="defined in both"):
        load_scene_defs(tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        "a:\n  kind: screen\n  signature: []\n",
        "a:\n  kind: popup\n  signature:\n    - {template: x, at: [0, 0]}\n",
        "a:\n  signature:\n    - {template: x}\n",
        "a:\n  signature:\n    - {template: x, at: [0, 0]}\n  objects:\n    b: {description: nothing}\n",
        "a:\n  signature:\n    - {template: x, at: [0, 0]}\n  bogus: 1\n",
        "- just a list\n",
    ],
)
def test_invalid_scene_files(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "bad.yaml", body)
    with pytest.raises(SceneError):
        load_scene_defs(tmp_path)


def test_loads_every_file_and_priority(tmp_path: Path) -> None:
    import cv2

    rng = np.random.default_rng(3)
    tdir = tmp_path / "templates"
    tdir.mkdir()
    tpl = rng.integers(0, 255, (20, 30, 3), dtype=np.uint8)
    cv2.imwrite(str(tdir / "t.png"), tpl)
    sdir = tmp_path / "scenes"
    sdir.mkdir()
    _write(sdir, "a.yaml", "low:\n  kind: modal\n  signature:\n    - {template: t, at: [100, 50]}\n")
    _write(sdir, "b.yaml", "high:\n  kind: modal\n  priority: 5\n  signature:\n    - {template: t, at: [100, 50]}\n"
           "base:\n  kind: screen\n  signature:\n    - {template: t, at: [100, 50]}\n")
    det = SceneDetector.load(sdir, Matcher(TemplateStore(tdir)))
    assert det.check_order == ["high", "low", "base"]
    img = np.zeros((600, 1000, 3), np.uint8)
    img[52:72, 103:133] = tpl  # 3 px off: inside the default 10 px margin
    m = det.detect(img, CanvasFix(Rect(0, 0, 1000, 600), 1.0, Confidence.HIGH))
    assert m.name == "high" and m.kind == "modal"
    assert m.detections[0].rect == Rect(103, 52, 30, 20)
    assert Point(118, 62) == m.detections[0].rect.center
