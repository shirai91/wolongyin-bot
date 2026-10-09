"""Main window (Vietnamese UI): game window picker, canvas/scene status, feature forms, run
controls, log view, and the extra tabs from the tab registry (docs/06)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QLabel, QMainWindow, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from ngoalong.core.locator import CanvasFix, Confidence
from ngoalong.core.scenes import SceneMatch
from ngoalong.engine.runner import RunResult, RunStatus
from ngoalong.ui.feature_page import FeaturePage
from ngoalong.ui.tabs import discover_tabs
from ngoalong.ui.widgets.log_view import LogView

APP_TITLE = "AutoNgoaLong v2 - Ngọa Long Việt"

CONFIDENCE_TEXT = {Confidence.HIGH: "tin cậy", Confidence.LOW: "chưa chắc chắn", Confidence.NONE: "không thấy"}


def canvas_text(fix: CanvasFix | None) -> str:
    if fix is None:
        return "Khung game: chưa kiểm tra"
    r = fix.rect
    return (f"Khung game: ({r.x}, {r.y}) {r.w}×{r.h} ×{fix.scale:.2f} - "
            f"{CONFIDENCE_TEXT.get(fix.confidence, fix.confidence.value)}")


def scene_text(scene: SceneMatch | str | None, services: Any = None) -> str:
    """The status line: a Snapshot description (decision loop, "Kiểm tra") or an old SceneMatch."""
    if scene is None:
        return "Màn hình: chưa kiểm tra"
    if isinstance(scene, str):
        return f"Màn hình: {scene}"
    if not scene.found:
        near = f" (gần nhất: {scene.candidate})" if scene.candidate else ""
        return f"Màn hình: không rõ{near}"
    desc = ""
    if services is not None:
        try:
            desc = next((d.description for d in services.scene_defs if d.name == scene.name), "")
        except Exception:
            desc = ""
    return f"Màn hình: {scene.name} ({scene.score:.2f})" + (f" - {desc}" if desc else "")


class _ProbeThread(QThread):
    done = Signal(object, object, str)  # CanvasFix | None, Snapshot description | None, error

    def __init__(self, services: Any) -> None:
        super().__init__()
        self.services = services

    def run(self) -> None:
        try:
            _frame, fix, scene = self.services.probe()
        except Exception as exc:
            self.done.emit(None, None, str(exc) or type(exc).__name__)
            return
        self.done.emit(fix, scene, "")


class MainWindow(QMainWindow):
    def __init__(self, services: Any, overlay: bool = True) -> None:
        super().__init__()
        self.services = services
        self.setWindowTitle(APP_TITLE)
        self.resize(820, 720)
        self._windows: list[Any] = []
        self._probe: _ProbeThread | None = None

        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        # --- game window picker and status ---------------------------------------------------
        box = QGroupBox("Cửa sổ game (Coowon)")
        grid = QGridLayout(box)
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(360)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        self.refresh_btn = QPushButton("Tìm lại")
        self.refresh_btn.clicked.connect(self.refresh_windows)
        self.check_btn = QPushButton("Kiểm tra")
        self.check_btn.clicked.connect(self.check_screen)
        self.canvas_label = QLabel(canvas_text(None))
        self.scene_label = QLabel(scene_text(None))
        self.scene_label.setWordWrap(True)
        grid.addWidget(self.window_combo, 0, 0)
        grid.addWidget(self.refresh_btn, 0, 1)
        grid.addWidget(self.check_btn, 0, 2)
        grid.addWidget(self.canvas_label, 1, 0, 1, 3)
        grid.addWidget(self.scene_label, 2, 0, 1, 3)
        grid.setColumnStretch(0, 1)
        root.addWidget(box)

        # --- tabs and log ----------------------------------------------------------------------
        split = QSplitter(Qt.Orientation.Vertical)
        self.tabs = QTabWidget()
        self.feature_page = FeaturePage(services)
        self.feature_page.message.connect(self.log_message)
        self.tabs.addTab(self.feature_page, "Tính năng")
        self.extra_tabs: dict[str, QWidget] = {}
        for spec in discover_tabs():
            try:
                w = spec.factory(services)
            except Exception as exc:
                self.log_message("error", f"Không tạo được thẻ {spec.title}: {exc}")
                continue
            self.extra_tabs[spec.id] = w
            self.tabs.addTab(w, spec.title)
        split.addWidget(self.tabs)
        log_box = QGroupBox("Nhật ký")
        log_lay = QVBoxLayout(log_box)
        self.log_view = LogView()
        log_lay.addWidget(self.log_view)
        split.addWidget(log_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        hk = services.settings.hotkeys
        hint = f"Dừng: {hk.stop}  ·  Gỡ lỗi: {hk.debug_toggle}" + (f"  ·  Tạm dừng: {hk.pause}" if hk.pause else "")
        self.statusBar().showMessage(hint)

        runner = services.runner
        runner.log.connect(self.log_message)
        runner.status.connect(self._on_status)
        runner.finished.connect(self._on_finished)
        runner.state_changed.connect(self._on_run_state)
        # Routine chains and the scheduler report into the same log view.
        for name in ("routine_executor", "scheduler"):
            source = getattr(services, name, None)
            if source is not None:
                source.log.connect(self.log_message)
        executor = getattr(services, "routine_executor", None)
        if executor is not None:  # the window stays bound for the whole routine (retry waits too)
            executor.routine_started.connect(self._on_routine_change)
            executor.routine_finished.connect(self._on_routine_change)

        self.overlay = None
        if overlay:
            from ngoalong.debug.overlay_window import OverlayWindow

            self.overlay = OverlayWindow(services.overlay_target)
            services.attach_overlay(self.overlay)

        self.refresh_windows()

    # --- log ------------------------------------------------------------------------------------
    def log_message(self, level: str, message: str) -> None:
        self.log_view.append_message(level, message)

    # --- window picker --------------------------------------------------------------------------
    def refresh_windows(self) -> None:
        windows = self.services.discover_windows()
        self._windows = windows
        blocked = self.window_combo.blockSignals(True)
        self.window_combo.clear()
        for w in windows:
            self.window_combo.addItem(f"{w.title}  (pid {w.pid})")
        self.window_combo.blockSignals(blocked)
        if not windows:
            self.window_combo.addItem("Không thấy cửa sổ Coowon đang mở Ngọa Long")
            self.window_combo.setEnabled(False)
            self.services.bind(None)
            self.check_btn.setEnabled(False)
            return
        self.window_combo.setEnabled(True)
        self.check_btn.setEnabled(True)
        idx = self.services.preferred_index(windows)
        self.window_combo.setCurrentIndex(idx)
        self._on_window_selected(idx)

    def _on_window_selected(self, index: int) -> None:
        if 0 <= index < len(self._windows):
            w = self._windows[index]
            self.services.bind(w)
            self.log_message("info", f"Đã chọn cửa sổ: {w.title}")

    # --- canvas / scene status ------------------------------------------------------------------
    def check_screen(self) -> None:
        if self._probe is not None and self._probe.isRunning():
            return
        self.check_btn.setEnabled(False)
        self._probe = _ProbeThread(self.services)
        self._probe.done.connect(self._on_probe_done)
        self._probe.start()

    def _on_probe_done(self, fix: CanvasFix | None, scene: SceneMatch | None, error: str) -> None:
        self.check_btn.setEnabled(bool(self._windows))
        if error:
            self.log_message("error", f"Không kiểm tra được màn hình: {error}")
            return
        self._on_status(fix, scene)

    def _on_status(self, fix: CanvasFix | None, scene: SceneMatch | None) -> None:
        if fix is not None:
            self.canvas_label.setText(canvas_text(fix))
        if scene is not None:
            self.scene_label.setText(scene_text(scene, self.services))

    def _on_routine_change(self, *_args: object) -> None:
        self._on_run_state(self.services.runner.state)

    def _on_run_state(self, state: str) -> None:
        """The bound window must not change under a running task or routine."""
        executor = getattr(self.services, "routine_executor", None)
        idle = state == "idle" and not (executor is not None and executor.is_active())
        self.window_combo.setEnabled(idle and bool(self._windows))
        self.refresh_btn.setEnabled(idle)

    def _on_finished(self, result: RunResult) -> None:
        level = {RunStatus.DONE: "info", RunStatus.STOPPED: "warning"}.get(result.status, "error")
        self.statusBar().showMessage(result.message, 10000)
        if result.run_dir is not None and result.run_dir.exists():
            self.log_message(level, f"Thư mục nhật ký: {result.run_dir}")

    # --- shutdown -------------------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self.services.runner.shutdown()
        if self._probe is not None:
            self._probe.wait(3000)
        refresher = getattr(self.services, "overlay_refresher", None)
        if refresher is not None:  # no live redraw may reopen the overlay after this
            refresher.stop()
        if self.overlay is not None:
            self.overlay.close()
        self.services.save()
        super().closeEvent(event)
