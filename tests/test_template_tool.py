"""P3 template capture tool: ``ngoalong.tools.template_tool`` and the "Mẫu ảnh" tab (offscreen Qt).

Every test that saves works on a copy of the profile inside ``tmp_path`` (the project root of the
copy is ``tmp_path / "proj"``), so the real profile is never written.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import cv2
import numpy as np
import pytest
import yaml

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.scenes import SceneDetector, load_scene_defs
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.tools import template_tool as tt
from ngoalong.tools.template_tool import (
    SaveMode, SaveRequest, TemplateTool, TemplateToolError, add_source_line, add_to_scene, set_crop_line,
)

TOWER_REL = "docs/game screenshots/tower-ui.png"  # canvas at (272, 106), scene tower.dialog
TITLE = Rect(448, 101, 104, 20)  # "Tháp Thí Luyện" title plate (= tower.dialog.title)
BADGE = Rect(900, 550, 60, 30)  # a distinctive corner of the tower dialog screen
NOW = datetime(2026, 10, 6, 12, 30, 0)


def _load_cut_templates() -> ModuleType:
    name = "cut_templates_for_template_tool"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "cut_templates.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


ct = _load_cut_templates()


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(PROFILE, root / "profiles" / "ngoalongviet", ignore=shutil.ignore_patterns("captures"))
    dst = root / TOWER_REL
    dst.parent.mkdir(parents=True)
    shutil.copy(ROOT / TOWER_REL, dst)
    return root


@pytest.fixture
def tool(proj: Path) -> TemplateTool:
    return TemplateTool(proj / "profiles" / "ngoalongviet", project_root=proj, clock=lambda: NOW)


def snapshot(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


def recut(tool: TemplateTool, name: str) -> np.ndarray:
    """What tools/cut_templates.py produces for ``name`` from the (copied) crop list."""
    sources, crops = ct.load_crops(tool.crops_path, root=tool.project_root)
    crop = next(c for c in crops if c.name == name)
    image, _mask = ct.cut(crop, sources)
    return image


def tower(tool: TemplateTool):
    return tool.load_file(tool.project_root / TOWER_REL)


# --- captures and coordinates -------------------------------------------------------------------
def test_load_file_locates_the_canvas(tool: TemplateTool) -> None:
    cap = tower(tool)
    assert cap.usable and cap.canvas.reliable
    assert cap.canvas.rect == Rect(272, 106, 1000, 600) and cap.canvas.scale == 1.0
    assert cap.label == "tower-ui.png"
    fr = tool.frame_rect(cap, TITLE)
    assert fr == Rect(720, 207, 104, 20)
    assert tool.canvas_rect(cap, fr) == TITLE
    assert tool.canvas_rect(cap, Rect(0, 0, 400, 200)) == Rect(0, 0, 128, 94)  # clipped to the canvas
    assert tool.detect_scene(cap).name == "tower.dialog"


def test_whole_image_and_unusable_captures(tool: TemplateTool) -> None:
    noise = np.random.default_rng(3).integers(0, 256, (600, 1000, 3), dtype=np.uint8)
    cap = tool.load_frame(noise)
    assert cap.usable and cap.canvas.rect == Rect(0, 0, 1000, 600)
    assert np.array_equal(tool.cut(cap, Rect(10, 20, 30, 40)), noise[20:60, 10:40])
    bad = tool.load_frame(np.random.default_rng(3).integers(0, 256, (500, 800, 3), dtype=np.uint8))
    assert not bad.usable
    with pytest.raises(TemplateToolError, match="khung game"):
        tool.save(bad, SaveRequest("tower.x", Rect(0, 0, 10, 10), "tower.dialog"))


def test_cut_rejects_bad_rectangles(tool: TemplateTool) -> None:
    cap = tower(tool)
    for r in (Rect(990, 10, 20, 20), Rect(10, 10, 3, 20), Rect(10, 590, 10, 11)):
        with pytest.raises(TemplateToolError, match="Vùng chọn"):
            tool.cut(cap, r)


# --- scoring ------------------------------------------------------------------------------------
def test_score_selection_reports_distinctiveness(tool: TemplateTool) -> None:
    cap = tower(tool)
    good = tool.score_selection(cap, BADGE)
    assert good.found and good.score > 0.99 and good.rect == BADGE
    assert good.runner_up is not None and good.runner_up < 0.7
    assert good.suggested_threshold is not None and 0.8 <= good.suggested_threshold <= 0.98
    assert "ngưỡng gợi ý" in good.text()
    flat = tool.score_selection(cap, Rect(300, 300, 30, 20))  # plain dialog background
    assert flat.runner_up is not None and flat.runner_up > 0.95 and flat.suggested_threshold is None
    assert "CHÚ Ý" in flat.text()


def test_score_saved_template_and_its_scene_references(tool: TemplateTool) -> None:
    cap = tower(tool)
    r = tool.score(cap, "tower.dialog.title")
    assert r.found and r.rect == TITLE and r.label == "khung game"
    refs = tool.score_references(cap, "tower.dialog.title")
    assert [x.label for x in refs] == ["tower.dialog (dấu hiệu)"]
    assert refs[0].found and refs[0].threshold == 0.9
    miss = tool.score(tool.load_file(ROOT / "docs" / "game screenshots" / "main-game.png"), "tower.dialog.title",
                      threshold=0.95)
    assert not miss.found
    with pytest.raises(TemplateToolError, match="Không có mẫu"):
        tool.score(cap, "tower.nothing_here")


def test_scaled_capture(tool: TemplateTool) -> None:
    """Zoomed game (x1.25): the template is stored at canvas scale 1.0 and still matches."""
    img = read_image(ROOT / TOWER_REL)[106:706, 272:1272]
    big = cv2.resize(img, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_LINEAR)
    frame = np.zeros((big.shape[0] + 150, big.shape[1] + 300, 3), np.uint8)
    frame[100:100 + big.shape[0], 150:150 + big.shape[1]] = big
    cap = tool.load_frame(frame)
    assert cap.canvas.reliable and abs(cap.canvas.scale - 1.25) < 0.01
    sel = tool.score_selection(cap, BADGE)
    assert sel.score > 0.99 and sel.rect is not None and abs(sel.rect.x - BADGE.x) <= 1
    res = tool.save(cap, SaveRequest("tower.zoomed_badge", BADGE, "tower.dialog"))
    assert res.capture_file is not None  # not 1:1: the canvas is saved and becomes the source
    assert tool.store.get("tower.zoomed_badge").size == (60, 30)
    assert tool.score(cap, "tower.zoomed_badge").score > 0.9
    assert np.array_equal(recut(tool, "tower.zoomed_badge"), read_image(res.template_path))


# --- saving -------------------------------------------------------------------------------------
def test_save_object_from_a_project_screenshot(tool: TemplateTool) -> None:
    cap = tower(tool)
    scene_file = tool.scenes_dir / "tower.yaml"
    before = scene_file.read_text(encoding="utf-8")
    crops_before = tool.crops_path.read_text(encoding="utf-8")
    res = tool.save(cap, SaveRequest("tower.dialog.badge", BADGE, "tower.dialog", description="góc dưới"))
    # template at canvas scale, reproducible by tools/cut_templates.py from the screenshot itself
    assert res.template_path == tool.templates_dir / "tower" / "dialog" / "badge.png"
    assert np.array_equal(read_image(res.template_path), tool.cut(cap, BADGE))
    assert np.array_equal(recut(tool, "tower.dialog.badge"), read_image(res.template_path))
    assert res.crop_source == "tower_ui" and res.capture_file is None  # existing source re-used
    crops = tool.crops_path.read_text(encoding="utf-8")
    assert crops.startswith(crops_before.rstrip("\n").split("\n")[0])
    assert "- {name: tower.dialog.badge, source: tower_ui, rect: [900, 550, 60, 30]}  # template tool 2026-10-06" \
        in crops
    # scene entry: comments kept, object found by the detector
    after = scene_file.read_text(encoding="utf-8")
    assert res.scene_file == scene_file and after.startswith(before.split("\n")[0])
    assert sum(1 for line in before.splitlines() if line.lstrip().startswith("#")) == \
        sum(1 for line in after.splitlines() if line.lstrip().startswith("#"))
    assert res.entry == ("badge: {template: tower.dialog.badge, at: [900, 550], threshold: 0.9, "
                         "description: góc dưới}")
    obj = {d.name: d for d in load_scene_defs(tool.scenes_dir)}["tower.dialog"].objects["badge"]
    assert obj.template == "tower.dialog.badge" and obj.at == (900, 550) and obj.margin == 10
    det = SceneDetector.load(tool.scenes_dir, templates_dir=tool.templates_dir)
    found = det.find_object(cap.image, cap.canvas, "tower.dialog", "badge")
    assert found.found and found.point == cap.canvas.to_frame((930, 565))
    assert "Đã lưu mẫu tower.dialog.badge" in res.text()


def test_save_signature_and_a_new_scene(tool: TemplateTool) -> None:
    cap = tower(tool)
    tool.save(cap, SaveRequest("tower.dialog.corner", BADGE, "tower.dialog", mode=SaveMode.SIGNATURE,
                               threshold=0.92, margin=4))
    sdef = {d.name: d for d in load_scene_defs(tool.scenes_dir)}["tower.dialog"]
    assert [r.template for r in sdef.signature] == ["tower.dialog.title", "tower.dialog.corner"]
    assert sdef.signature[1].margin == 4 and sdef.signature[1].threshold == 0.92
    res = tool.save(cap, SaveRequest("tool_test.title", TITLE, "tool_test.screen", mode=SaveMode.NEW_SCENE,
                                     object_name="title", kind="modal", description="Màn hình thử"))
    assert res.scene_file == tool.scenes_dir / "tool_test.yaml"
    text = res.scene_file.read_text(encoding="utf-8")
    assert text.startswith("# Scenes added by the template tool")
    assert '  description: "Màn hình thử"' in text
    defs = {d.name: d for d in load_scene_defs(tool.scenes_dir)}
    new = defs["tool_test.screen"]
    assert new.kind == "modal" and new.signature[0].at == (448, 101) and "title" in new.objects
    det = SceneDetector.load(tool.scenes_dir, templates_dir=tool.templates_dir)
    assert det.check(cap.image, cap.canvas, "tool_test.screen").found
    assert det.detect(cap.image, cap.canvas).name in ("tower.dialog", "tool_test.screen")


def test_live_capture_saves_the_canvas_once(tool: TemplateTool) -> None:
    frame = read_image(ROOT / TOWER_REL)
    cap = tool.grab(FileCapturer([frame]))
    assert cap.source is None and cap.canvas.reliable
    a = tool.save(cap, SaveRequest("tower.live_badge", BADGE, "tower.dialog"))
    assert a.capture_file == tool.captures_dir / "20261006-123000-tower_live_badge.png"
    assert read_image(a.capture_file).shape == (600, 1000, 3)
    data = yaml.safe_load(tool.crops_path.read_text(encoding="utf-8"))
    src = data["sources"][a.crop_source]
    assert a.crop_source.startswith("tool_") and src == {
        "path": "profiles/ngoalongviet/captures/20261006-123000-tower_live_badge.png", "origin": [0, 0]}
    text = tool.crops_path.read_text(encoding="utf-8")
    assert text.index(f"  {a.crop_source}:") < text.index("\ncrops:")  # inside the sources block
    b = tool.save(cap, SaveRequest("tower.live_title", TITLE, "tower.dialog"))
    assert b.capture_file is None and b.crop_source == a.crop_source  # same capture, same source
    assert len(list(tool.captures_dir.glob("*.png"))) == 1
    for name in ("tower.live_badge", "tower.live_title"):
        assert np.array_equal(recut(tool, name), read_image(tool.template_path(name)))


def test_live_capture_is_cropped_to_the_canvas(tool: TemplateTool) -> None:
    frame = read_image(ROOT / TOWER_REL)
    full = tool.grab(FileCapturer([frame]), crop=False)
    cap = tool.grab(FileCapturer([frame]))
    assert cap.image.shape == (600, 1000, 3) and cap.canvas.rect == Rect(0, 0, 1000, 600)
    assert cap.canvas.reliable and cap.canvas.scale == 1.0
    assert np.array_equal(cap.image, full.canvas_image())
    assert np.array_equal(tool.cut(cap, BADGE), tool.cut(full, BADGE))


def test_overwrite_replaces_lines_instead_of_adding(tool: TemplateTool) -> None:
    cap = tower(tool)
    tool.save(cap, SaveRequest("tower.dialog.badge", BADGE, "tower.dialog"))
    mask = tool.template_path("tower.dialog.badge").with_name("badge.mask.png")
    mask.write_bytes(b"stale")  # a mask left from an older crop is removed with the overwrite
    other = Rect(880, 540, 70, 40)
    with pytest.raises(TemplateToolError, match="Ghi đè"):
        tool.save(cap, SaveRequest("tower.dialog.badge", other, "tower.dialog"))
    tool.save(cap, SaveRequest("tower.dialog.badge", other, "tower.dialog", overwrite=True))
    crops = tool.crops_path.read_text(encoding="utf-8")
    assert crops.count("name: tower.dialog.badge,") == 1 and "rect: [880, 540, 70, 40]" in crops
    scene = (tool.scenes_dir / "tower.yaml").read_text(encoding="utf-8")
    assert scene.count("badge: {template: tower.dialog.badge") == 1 and "at: [880, 540]" in scene
    assert not mask.exists()
    assert read_image(tool.template_path("tower.dialog.badge")).shape == (40, 70, 3)
    # the signature of the scene can be replaced too
    tool.save(cap, SaveRequest("tower.dialog.title", TITLE, "tower.dialog", mode=SaveMode.SIGNATURE,
                               threshold=0.95, overwrite=True))
    sdef = {d.name: d for d in load_scene_defs(tool.scenes_dir)}["tower.dialog"]
    assert len(sdef.signature) == 1 and sdef.signature[0].threshold == 0.95


@pytest.mark.parametrize("req, msg", [
    (SaveRequest("Tower.Bad Name", BADGE, "tower.dialog"), "Tên mẫu"),
    (SaveRequest("single", BADGE, "tower.dialog"), "Tên mẫu"),
    (SaveRequest("tower.x", BADGE, "tower.nope"), "Không có màn hình"),
    (SaveRequest("tower.x", BADGE, "tower.dialog", mode=SaveMode.NEW_SCENE), "đã có"),
    (SaveRequest("tower.x", BADGE, "tower.new", mode=SaveMode.NEW_SCENE, scene_file="../x.yaml"), "tệp"),
    (SaveRequest("tower.dialog.title", BADGE, "tower.dialog", object_name="t"), "đã có"),
    (SaveRequest("tower.x", BADGE, "tower.dialog", object_name="close"), "đối tượng close"),
    (SaveRequest("tower.x", BADGE, "tower.dialog", object_name="Bad-Name"), "đối tượng"),
    (SaveRequest("tower.x", BADGE, "tower.dialog", threshold=1.5), "Ngưỡng"),
    (SaveRequest("tower.x", Rect(995, 0, 10, 10), "tower.dialog"), "Vùng chọn"),
])
def test_refusals_change_nothing(tool: TemplateTool, req: SaveRequest, msg: str) -> None:
    before = snapshot(tool.project_root)
    with pytest.raises(TemplateToolError, match=msg):
        tool.save(tower(tool), req)
    assert snapshot(tool.project_root) == before


def test_failure_after_writing_rolls_everything_back(tool: TemplateTool, monkeypatch: pytest.MonkeyPatch) -> None:
    cap = tool.grab(FileCapturer([read_image(ROOT / TOWER_REL)]))
    before = snapshot(tool.project_root)

    def boom(*_a: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(TemplateTool, "_verify", boom)
    with pytest.raises(TemplateToolError, match="đã hoàn tác"):
        tool.save(cap, SaveRequest("brand_new_area.deep.badge", BADGE, "tower.dialog"))
    assert snapshot(tool.project_root) == before
    assert not (tool.templates_dir / "brand_new_area").exists() and not tool.captures_dir.exists()
    assert cap.saved_as is None


def test_one_line_objects_are_not_edited(tool: TemplateTool) -> None:
    (tool.scenes_dir / "zz_inline.yaml").write_text(
        "zz.inline:\n  signature:\n    - {template: tower.dialog.title, at: [448, 101]}\n"
        "  objects: {close: {offset: [803, 118]}}\n", encoding="utf-8")
    before = snapshot(tool.project_root)
    with pytest.raises(TemplateToolError, match="sửa tệp bằng tay"):
        tool.save(tower(tool), SaveRequest("tower.x", BADGE, "zz.inline"))
    assert snapshot(tool.project_root) == before


# --- text helpers -------------------------------------------------------------------------------
def test_add_to_scene_variants() -> None:
    base = ("# head\na:\n  kind: screen\n  signature:\n  - {template: t.a, at: [1, 2]}\n"
            "  objects:\n    # note\n    x: {offset: [1, 1]}\n\n# between\nb:\n  signature:\n"
            "    - {template: t.b, at: [3, 4]}\n  objects: {}\n")
    out = add_to_scene(base, "a", "objects", "y: {offset: [2, 2]}")
    assert "    x: {offset: [1, 1]}\n    y: {offset: [2, 2]}\n\n# between" in out
    out = add_to_scene(out, "a", "signature", "- {template: t.c, at: [5, 6]}")
    assert "  - {template: t.a, at: [1, 2]}\n  - {template: t.c, at: [5, 6]}\n  objects:" in out
    out = add_to_scene(out, "b", "objects", "z: {offset: [3, 3]}")
    assert out.endswith("  objects:\n    z: {offset: [3, 3]}\n")
    out = add_to_scene(out, "a", "objects", "x: {offset: [9, 9]}", replace=re.compile(r"^x\s*:"))
    assert "x: {offset: [9, 9]}" in out and "x: {offset: [1, 1]}" not in out
    data = yaml.safe_load(out)
    assert set(data["a"]["objects"]) == {"x", "y"} and len(data["a"]["signature"]) == 2
    assert data["b"]["objects"] == {"z": {"offset": [3, 3]}}
    no_objects = add_to_scene("c:\n  signature:\n    - {template: t, at: [0, 0]}\n", "c", "objects", "o: {offset: [1, 2]}")
    assert yaml.safe_load(no_objects)["c"]["objects"] == {"o": {"offset": [1, 2]}}
    with pytest.raises(TemplateToolError):
        add_to_scene(base, "missing", "objects", "q: {offset: [0, 0]}")


def test_crop_list_helpers() -> None:
    text = "sources:\n  s: {path: a.png, origin: [0, 0]}\n\ncrops:\n  - {name: a.b, source: s, rect: [0, 0, 5, 5]}\n"
    out = add_source_line(text, "  t: {path: b.png, origin: [1, 1]}")
    assert out.startswith("sources:\n  s: {path: a.png, origin: [0, 0]}\n  t: {path: b.png, origin: [1, 1]}\n\ncrops:")
    out = set_crop_line(out, "a.c", "  - {name: a.c, source: t, rect: [1, 1, 5, 5]}")
    assert "# --- added by the template tool" in out and out.endswith("  - {name: a.c, source: t, rect: [1, 1, 5, 5]}\n")
    out2 = set_crop_line(out, "a.b", "  - {name: a.b, source: t, rect: [2, 2, 5, 5]}")
    assert out2.count("name: a.b,") == 1 and "rect: [2, 2, 5, 5]" in out2
    data = yaml.safe_load(out2)
    assert set(data["sources"]) == {"s", "t"} and [c["name"] for c in data["crops"]] == ["a.b", "a.c"]
    with pytest.raises(TemplateToolError):
        add_source_line("crops:\n", "  t: {path: b.png, origin: [1, 1]}")


def test_request_defaults_and_plain_string_modes() -> None:
    r = SaveRequest("tham_bao.reward_x2", BADGE, "tham_bao", mode="signature")  # type: ignore[arg-type]
    assert r.mode is SaveMode.SIGNATURE and r.object_key() is None and r.scene_file_name() == "tham_bao.yaml"
    assert SaveRequest("tham_bao.reward_x2", BADGE, "tham_bao").object_key() == "reward_x2"
    with pytest.raises(ValueError):
        SaveRequest("a.b", BADGE, "s", mode="nope")  # type: ignore[arg-type]


def test_command_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert tt.main([str(ROOT / TOWER_REL), "tower.dialog.title"]) == 0
    out = capsys.readouterr().out
    assert "canvas (272, 106) 1000x600" in out and "tower.dialog (dấu hiệu): THẤY" in out
    assert tt.main([str(ROOT / TOWER_REL), "tower.none"]) == 1


def test_real_profile_is_consistent_with_the_tool() -> None:
    """Every scene template exists, and names follow the tool's naming rule."""
    tool = TemplateTool(PROFILE)
    store = TemplateStore(PROFILE / "templates")
    det = SceneDetector(tool.scene_defs(), Matcher(store))
    for name in det.templates():
        assert name in store, name
    assert tt.NAME_RE.match("tham_bao.reward_x2")


# --- the "Mẫu ảnh" tab (offscreen) --------------------------------------------------------------
def _services(fail: bool = False) -> SimpleNamespace:
    def require_window() -> object:
        if fail:
            raise RuntimeError("Chưa chọn cửa sổ game")
        return object()

    return SimpleNamespace(profile_dir=PROFILE, require_window=require_window,
                           capturer_factory=lambda gw: FileCapturer([ROOT / TOWER_REL]),
                           templates=TemplateStore(PROFILE / "templates"), scene_defs=None)


def test_tab_is_registered(qapp) -> None:
    from ngoalong.ui.tabs import discover_tabs

    spec = next(t for t in discover_tabs() if t.id == "templates")
    assert spec.title == "Mẫu ảnh"
    w = spec.factory(_services())
    try:
        assert w.tool.profile_dir == PROFILE and w.scene_combo.count() > 5
    finally:
        w.close()


def test_tab_capture_drag_test_and_save(qapp, tool: TemplateTool) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from ngoalong.ui.tabs.templates import TemplatesTab

    services = _services()
    tab = TemplatesTab(services, tool=tool)
    try:
        tab.resize(1400, 900)
        tab.show()
        qapp.processEvents()
        assert tab.test_selection() is None and "Chưa có ảnh" in tab.result.text()
        assert tab.capture_live()
        assert "tower.dialog" in tab.info.text() and "tin cậy" in tab.info.text()
        assert tab.scene_combo.currentText() == "tower.dialog"
        # drag a rectangle on the view: widget -> frame -> canvas coordinates
        view = tab.view
        target = tool.frame_rect(tab.capture, BADGE)
        a = view.to_widget(Rect(target.x, target.y, 0, 0))
        b = view.to_widget(Rect(target.right, target.bottom, 0, 0))
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(round(a.x()) + 1, round(a.y()) + 1))
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(round(b.x()) + 1, round(b.y()) + 1))
        sel = tab.selection_canvas()
        assert sel is not None and abs(sel.x - BADGE.x) <= 3 and abs(sel.w - BADGE.w) <= 4
        assert view.selection is not None
        # exact values through the spin boxes (what a user fine-tunes)
        tab.set_selection_canvas(BADGE)
        assert tab.selection_canvas() == BADGE and view.selection == target
        res = tab.test_selection()
        assert res is not None and res.found and "vùng chọn" in tab.result.text()
        tab.zoom_combo.setCurrentIndex(3)  # 400 %
        assert view.zoom == 4.0 and view.minimumWidth() == tab.capture.image.shape[1] * 4
        view.grab()  # paints at zoom without errors
        tab.zoom_combo.setCurrentIndex(0)
        tab.name_edit.setText("tower.dialog.ui_badge")
        saved = tab.save_template()
        assert saved is not None and saved.template_path.is_file()
        assert "Đã lưu mẫu tower.dialog.ui_badge" in tab.result.text()
        assert "khung game: THẤY" in tab.result.text() and "tower.dialog.ui_badge: THẤY" in tab.result.text()
        assert view._marks and all(found for _r, found in view._marks)
        assert tab.save_template() is None and "Ghi đè" in tab.result.text()
        tab.overwrite.setChecked(True)
        assert tab.save_template() is not None
        # a new scene from the UI
        tab.mode_combo.setCurrentIndex(2)
        assert tab.file_edit.isEnabled() and tab.kind_combo.isEnabled()
        tab.name_edit.setText("ui_test.title")
        tab.scene_combo.setEditText("ui_test.screen")
        tab.set_selection_canvas(TITLE)
        assert tab.save_template() is not None
        assert tab.scene_combo.findText("ui_test.screen") >= 0
        results = tab.test_template("ui_test.title")
        assert any(r.label == "ui_test.screen (dấu hiệu)" and r.found for r in results)
    finally:
        tab.close()


def test_tab_errors_are_shown(qapp, tool: TemplateTool, tmp_path: Path) -> None:
    from PySide6.QtCore import QPointF

    from ngoalong.ui.tabs.templates import TemplatesTab

    tab = TemplatesTab(_services(fail=True), tool=tool)
    try:
        assert not tab.capture_live() and "Lỗi chụp" in tab.result.text()
        assert not tab.open_file(tmp_path / "missing.png") and "Lỗi mở ảnh" in tab.result.text()
        assert tab.open_file(tool.project_root / TOWER_REL)
        assert tab.save_template() is None and "kéo chuột" in tab.result.text()
        tab.set_selection_canvas(BADGE)
        tab.name_edit.setText("")
        assert tab.save_template() is None and "Tên mẫu" in tab.result.text()
        assert tab.test_template("") == [] and "Nhập tên mẫu" in tab.result.text()
        noise = np.random.default_rng(5).integers(0, 256, (500, 800, 3), dtype=np.uint8)
        tab.set_capture(tool.load_frame(noise))
        assert "Không tìm thấy khung game" in tab.info.text()
        assert tab.view.to_frame(QPointF(-50, -50)) == Point(0, 0)  # clamped to the image
    finally:
        tab.close()
