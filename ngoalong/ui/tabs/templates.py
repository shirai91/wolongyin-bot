""""Mẫu ảnh" tab: template capture tool (docs/06 "New features").

Show a capture (live from the bound game window, or a screenshot file), drag a rectangle, save it as
a template with a scene entry, and test match scores on the current frame. The logic is in
``ngoalong.tools.template_tool``; this tab only collects the inputs and shows the results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ngoalong.core.geometry import Rect
from ngoalong.core.scenes import DEFAULT_MARGIN, load_scene_defs
from ngoalong.tools.template_tool import (
    CANVAS_H, CANVAS_W, Capture, SaveMode, SaveRequest, SaveResult, ScoreResult, TemplateTool,
)
from ngoalong.ui.tabs import register_tab
from ngoalong.ui.widgets.capture_view import CaptureView

log = logging.getLogger(__name__)

MODES = [
    (SaveMode.OBJECT, "Đối tượng của màn hình"),
    (SaveMode.SIGNATURE, "Dấu hiệu nhận diện màn hình"),
    (SaveMode.NEW_SCENE, "Màn hình mới"),
]
KINDS = [("screen", "Màn hình"), ("modal", "Hộp thoại"), ("overlay", "Lớp phủ (thông báo)")]
ZOOMS = [(None, "Vừa khung"), (1.0, "100%"), (2.0, "200%"), (4.0, "400%")]
CONFIDENCE_TEXT = {"high": "tin cậy", "low": "chưa chắc chắn", "none": "không thấy"}


class TemplatesTab(QWidget):
    def __init__(self, services: Any, tool: TemplateTool | None = None) -> None:
        super().__init__()
        self.services = services
        self.tool = tool or TemplateTool(services.profile_dir)
        self.capture: Capture | None = None
        self._syncing = False

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.capture_btn = QPushButton("Chụp từ game")
        self.capture_btn.clicked.connect(self.capture_live)
        self.open_btn = QPushButton("Mở ảnh…")
        self.open_btn.clicked.connect(self._choose_file)
        self.zoom_combo = QComboBox()
        for zoom, text in ZOOMS:
            self.zoom_combo.addItem(text, zoom)
        self.zoom_combo.currentIndexChanged.connect(lambda _i: self.view.set_zoom(self.zoom_combo.currentData()))
        bar.addWidget(self.capture_btn)
        bar.addWidget(self.open_btn)
        bar.addStretch(1)
        bar.addWidget(QLabel("Thu phóng:"))
        bar.addWidget(self.zoom_combo)
        root.addLayout(bar)
        self.info = QLabel("Chưa có ảnh")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.view = CaptureView()
        self.view.selection_changed.connect(self._on_view_selection)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.view)
        split.addWidget(self.scroll)
        split.addWidget(self._build_side())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        self.refresh_scenes()
        self._on_mode_changed()

    # --- layout ---------------------------------------------------------------------------------
    def _spin(self, maximum: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, maximum)
        s.valueChanged.connect(self._on_spin_changed)
        return s

    def _build_side(self) -> QWidget:
        side = QWidget()
        lay = QVBoxLayout(side)

        sel = QGroupBox(f"Vùng chọn (tọa độ khung game {CANVAS_W}×{CANVAS_H})")
        grid = QGridLayout(sel)
        self.sel_x, self.sel_y = self._spin(CANVAS_W - 1), self._spin(CANVAS_H - 1)
        self.sel_w, self.sel_h = self._spin(CANVAS_W), self._spin(CANVAS_H)
        for i, (text, spin) in enumerate((("x", self.sel_x), ("y", self.sel_y), ("rộng", self.sel_w),
                                          ("cao", self.sel_h))):
            grid.addWidget(QLabel(text), i // 2, (i % 2) * 2)
            grid.addWidget(spin, i // 2, (i % 2) * 2 + 1)
        self.test_sel_btn = QPushButton("Thử vùng chọn")
        self.test_sel_btn.setToolTip("Tìm vùng chọn trên cả khung game: chỗ khác càng thấp điểm càng tốt")
        self.test_sel_btn.clicked.connect(self.test_selection)
        grid.addWidget(self.test_sel_btn, 2, 0, 1, 4)
        lay.addWidget(sel)

        box = QGroupBox("Lưu mẫu")
        form = QFormLayout(box)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("vd: tham_bao.reward_x2")
        self.mode_combo = QComboBox()
        for mode, text in MODES:
            self.mode_combo.addItem(text, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.scene_combo = QComboBox()
        self.scene_combo.setEditable(True)
        self.object_edit = QLineEdit()
        self.object_edit.setPlaceholderText("mặc định: phần cuối của tên mẫu")
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("mặc định: <phần đầu tên mẫu>.yaml")
        self.kind_combo = QComboBox()
        for kind, text in KINDS:
            self.kind_combo.addItem(text, kind)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.5, 1.0)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(0.9)
        self.margin = QSpinBox()
        self.margin.setRange(0, 1000)
        self.margin.setValue(DEFAULT_MARGIN)
        self.margin.setToolTip("Tìm quanh vị trí đã cắt, xa tối đa bấy nhiêu px (1000 = cả khung game)")
        self.desc_edit = QLineEdit()
        self.overwrite = QCheckBox("Ghi đè nếu đã có")
        form.addRow("Tên mẫu", self.name_edit)
        form.addRow("Kiểu", self.mode_combo)
        form.addRow("Màn hình", self.scene_combo)
        form.addRow("Tên đối tượng", self.object_edit)
        form.addRow("Tệp màn hình mới", self.file_edit)
        form.addRow("Loại màn hình mới", self.kind_combo)
        form.addRow("Ngưỡng", self.threshold)
        form.addRow("Lề tìm (px)", self.margin)
        form.addRow("Mô tả", self.desc_edit)
        form.addRow("", self.overwrite)
        lay.addWidget(box)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Lưu mẫu")
        self.save_btn.clicked.connect(self.save_template)
        self.test_tpl_btn = QPushButton("Thử mẫu đã lưu")
        self.test_tpl_btn.setToolTip("Tìm mẫu có tên ở trên trong ảnh hiện tại (cả khung và chỗ màn hình dùng nó)")
        self.test_tpl_btn.clicked.connect(lambda: self.test_template())
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.test_tpl_btn)
        lay.addLayout(buttons)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.result)
        lay.addStretch(1)
        return side

    # --- helpers --------------------------------------------------------------------------------
    def refresh_scenes(self) -> None:
        current = self.scene_combo.currentText()
        try:
            names = self.tool.scene_names()
        except Exception as exc:
            names = []
            self._show(f"Lỗi đọc màn hình: {exc}")
        blocked = self.scene_combo.blockSignals(True)
        self.scene_combo.clear()
        self.scene_combo.addItems(names)
        self.scene_combo.setEditText(current)
        self.scene_combo.blockSignals(blocked)

    def _show(self, text: str) -> None:
        self.result.setText(text)

    def _mode(self) -> SaveMode:
        return SaveMode(self.mode_combo.currentData())  # Qt hands the enum back as a plain str

    def _on_mode_changed(self, *_: Any) -> None:
        new = self._mode() is SaveMode.NEW_SCENE
        self.file_edit.setEnabled(new)
        self.kind_combo.setEnabled(new)

    def _require_capture(self) -> Capture | None:
        if self.capture is None:
            self._show("Chưa có ảnh: bấm 'Chụp từ game' hoặc 'Mở ảnh…'")
        return self.capture

    # --- captures -------------------------------------------------------------------------------
    def set_capture(self, capture: Capture) -> None:
        self.capture = capture
        self.view.set_frame(capture.image, capture.canvas.rect)
        r, fix = capture.canvas.rect, capture.canvas
        conf = "cả ảnh là khung game" if not fix.reliable and capture.usable else \
            CONFIDENCE_TEXT.get(fix.confidence.value, fix.confidence.value)
        text = f"{capture.label}: khung game ({r.x}, {r.y}) {r.w}×{r.h} ×{fix.scale:.2f} - {conf}"
        try:
            scene = self.tool.detect_scene(capture)
        except Exception as exc:
            log.exception("scene detection failed")
            text += f"; không nhận được màn hình: {exc}"
        else:
            if scene.found:
                text += f"; màn hình: {scene.name} ({scene.score:.2f})"
                self.scene_combo.setEditText(scene.name)
            else:
                text += "; màn hình: không rõ" + (f" (gần nhất {scene.candidate})" if scene.candidate else "")
        if not capture.usable:
            text += ". Không tìm thấy khung game: không cắt mẫu được từ ảnh này."
        self.info.setText(text)
        self.set_selection_canvas(None)
        self._show("")

    def open_file(self, path: str | Path) -> bool:
        try:
            self.set_capture(self.tool.load_file(path))
        except Exception as exc:
            self._show(f"Lỗi mở ảnh: {exc}")
            return False
        return True

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Mở ảnh chụp game", str(self.tool.project_root),
                                              "Ảnh (*.png *.bmp *.jpg *.jpeg)")
        if path:
            self.open_file(path)

    def capture_live(self) -> bool:
        """One capture of the bound game window (works with the game in the background)."""
        try:
            gw = self.services.require_window()
            capture = self.tool.grab(self.services.capturer_factory(gw))
        except Exception as exc:
            self._show(f"Lỗi chụp: {exc}")
            return False
        self.set_capture(capture)
        return True

    # --- selection ------------------------------------------------------------------------------
    def selection_canvas(self) -> Rect | None:
        r = Rect(self.sel_x.value(), self.sel_y.value(), self.sel_w.value(), self.sel_h.value())
        return None if r.is_empty() else r

    def set_selection_canvas(self, rect: Rect | None) -> None:
        self._syncing = True
        try:
            r = rect or Rect(0, 0, 0, 0)
            self.sel_x.setValue(r.x)
            self.sel_y.setValue(r.y)
            self.sel_w.setValue(r.w)
            self.sel_h.setValue(r.h)
        finally:
            self._syncing = False
        self._sync_view()

    def _sync_view(self) -> None:
        r = self.selection_canvas()
        if self.capture is None or r is None:
            self.view.set_selection(None)
        else:
            self.view.set_selection(self.tool.frame_rect(self.capture, r))

    def _on_spin_changed(self, _value: int) -> None:
        if not self._syncing:
            self._sync_view()

    def _on_view_selection(self, frame_r: Rect | None) -> None:
        if self.capture is None or frame_r is None:
            self.set_selection_canvas(None)
            return
        r = self.tool.canvas_rect(self.capture, frame_r)
        self.set_selection_canvas(None if r.is_empty() else r)

    # --- actions --------------------------------------------------------------------------------
    def _mark(self, results: list[ScoreResult]) -> None:
        if self.capture is None:
            return
        marks = [(self.tool.frame_rect(self.capture, r.rect), r.found) for r in results if r.rect is not None]
        self.view.set_marks(marks)

    def test_selection(self) -> ScoreResult | None:
        cap = self._require_capture()
        rect = self.selection_canvas()
        if cap is None:
            return None
        if rect is None:
            self._show("Hãy kéo chuột trên ảnh để chọn một vùng")
            return None
        try:
            res = self.tool.score_selection(cap, rect, self.threshold.value())
        except Exception as exc:
            self._show(f"Lỗi: {exc}")
            return None
        self._mark([res])
        self._show(res.text())
        return res

    def build_request(self) -> SaveRequest | None:
        rect = self.selection_canvas()
        if rect is None:
            self._show("Hãy kéo chuột trên ảnh để chọn một vùng")
            return None
        mode = self._mode()
        return SaveRequest(
            name=self.name_edit.text().strip(),
            rect=rect,
            scene=self.scene_combo.currentText().strip(),
            mode=mode,
            object_name=self.object_edit.text().strip() or None,
            threshold=round(self.threshold.value(), 2),
            margin=self.margin.value(),
            description=self.desc_edit.text().strip(),
            scene_file=(self.file_edit.text().strip() or None) if mode is SaveMode.NEW_SCENE else None,
            kind=self.kind_combo.currentData(),
            overwrite=self.overwrite.isChecked(),
        )

    def save_template(self) -> SaveResult | None:
        cap = self._require_capture()
        if cap is None:
            return None
        req = self.build_request()
        if req is None:
            return None
        try:
            res = self.tool.save(cap, req)
        except Exception as exc:
            self._show(f"Lỗi: {exc}")
            return None
        self._reload_services()
        self.refresh_scenes()
        self._show(res.text())
        self.test_template(req.name, prefix=res.text() + "\n")
        return res

    def _reload_services(self) -> None:
        """New runs see the new template and scene entry without a restart."""
        s = self.services
        try:
            if getattr(s, "templates", None) is not None:
                s.templates.clear_cache()
            if getattr(s, "scene_defs", None) is not None and Path(s.profile_dir) == self.tool.profile_dir:
                s.scene_defs = load_scene_defs(self.tool.scenes_dir)
        except Exception:
            log.exception("could not reload the profile after saving a template")

    def test_template(self, name: str | None = None, prefix: str = "") -> list[ScoreResult]:
        cap = self._require_capture()
        name = (name or self.name_edit.text()).strip()
        if cap is None:
            return []
        if not name:
            self._show("Nhập tên mẫu cần thử")
            return []
        try:
            results = [self.tool.score(cap, name), *self.tool.score_references(cap, name)]
        except Exception as exc:
            self._show(f"{prefix}Lỗi: {exc}")
            return []
        self._mark(results)
        self._show(prefix + "\n".join(f"{name} | {r.text()}" for r in results))
        return results


@register_tab("templates", "Mẫu ảnh", order=30)
def build(services: Any) -> QWidget:
    return TemplatesTab(services)
