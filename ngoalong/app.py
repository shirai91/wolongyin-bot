"""Entry point and dependency wiring (docs/06 module layout: ``app.py``).

``AppServices`` owns the long-lived objects (settings, profile data, bound window, debug state,
sinks, runner, hotkeys) and builds a fresh ``Actor`` for every run. The drivers are factories so
the self-test can build the app without a game window.

Run with ``python -m ngoalong``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ngoalong.config.settings import (
    DEFAULT_INI_PATH, DEFAULT_SETTINGS_PATH, PROJECT_ROOT, Settings, load_or_import, save_settings,
)
from ngoalong.core.capture import CachedCapturer, Capturer
from ngoalong.core.errors import WindowGoneError, WindowNotFoundError
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.input import InputDriver
from ngoalong.core.locator import CanvasFix, CanvasLocator, Confidence, LocatorConfig, canvas_crop
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.core.window import GameWindow
from ngoalong.debug.model import DebugFrame
from ngoalong.debug.sink import FanoutSink, OverlaySink, PanelSink, PngSink
from ngoalong.engine import registry
from ngoalong.engine.actor import Actor, snapshot_frame
from ngoalong.engine.history import HistoryRecorder, HistoryStore
from ngoalong.engine.loop import load_profile
from ngoalong.engine.manager import RunnerManager, WindowSlot, same_window
from ngoalong.engine.interrupts import Panels, PopupGuard
from ngoalong.engine.scheduler import (
    RoutineExecutor, RoutineStep, RoutineStore, Scheduler, SchedulerService, build_step_task,
)
from ngoalong.engine.runner import RunHooks, RunResult, TaskRunner
from ngoalong.engine.task import Task
from ngoalong.hotkeys import HotkeyManager
from ngoalong.perception import Perceiver
from ngoalong.notify import Notifier, QtTray

log = logging.getLogger(__name__)

PROFILES_DIR = PROJECT_ROOT / "profiles"
APP_LOG_FILE = PROJECT_ROOT / "logs" / "app.log"

CapturerFactory = Callable[[GameWindow], Capturer]
InputFactory = Callable[[GameWindow], InputDriver]
DiscoverFn = Callable[[], list[GameWindow]]


def _win32_capturer(gw: GameWindow) -> Capturer:
    from ngoalong.core.drivers.win32 import Win32Capturer

    return Win32Capturer(gw)


def _win32_input(gw: GameWindow) -> InputDriver:
    from ngoalong.core.drivers.win32 import Win32InputDriver

    return Win32InputDriver(gw)


def _discover() -> list[GameWindow]:
    if sys.platform != "win32":
        return []
    return GameWindow.discover()


class DebugState(QObject):
    """The debug-mode switch (settings checkbox, UI checkbox, Ctrl+Alt+D)."""

    changed = Signal(bool)

    def __init__(self, enabled: bool = False) -> None:
        super().__init__()
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self._enabled:
            self._enabled = enabled
            self.changed.emit(enabled)

    def toggle(self) -> None:
        self.set_enabled(not self._enabled)


class HotkeyBridge(QObject):
    """Hotkey callbacks run on the hotkey thread; these signals hop to the Qt thread."""

    stop_requested = Signal()
    pause_requested = Signal()
    debug_toggle_requested = Signal()


class OverlayRefresher(QObject):
    """Keeps the live debug overlay in step with the game screen.

    Bot frames only arrive while a task runs, so without this the overlay kept showing the last
    bot frame after the user changed the screen by hand. While debug mode is on, every
    ``settings.overlay_refresh_ms`` it captures the bound window on a background thread, locates
    the canvas, perceives the Snapshot and redraws the overlay (``AppServices.overlay_snapshot``). A tick
    is skipped while the previous capture still runs, or while the bot is publishing its own
    frames. The timer stops when debug mode is turned off, the window is lost (until the next
    bind) and at shutdown.
    """

    BOT_HOLD_S = 2.0  # a bot frame stays on screen at least this long before a live one replaces it

    _done = Signal(object, str, float)  # DebugFrame | None, "ok" | "lost", tick start (monotonic)

    def __init__(self, services: AppServices) -> None:
        super().__init__()
        self._s = services
        self._busy = False
        self._closed = False
        self._thread: threading.Thread | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._done.connect(self._on_done)

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def sync(self) -> None:
        """Start or stop polling to match debug mode, the bound window and the interval setting."""
        sink = self._s.overlay_sink
        self._timer.setInterval(self._s.settings.overlay_refresh_ms)
        if self._closed or sink is None or not sink.enabled or self._s.window is None:
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start()
            QTimer.singleShot(0, self._tick)  # redraw now, not one interval later

    def stop(self) -> None:
        self._closed = True
        self._timer.stop()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(2.0)

    def _tick(self) -> None:
        sink = self._s.overlay_sink
        if self._closed or sink is None or not sink.enabled or self._s.window is None:
            self._timer.stop()
            return
        if self._busy:  # never overlap captures
            return
        hold = max(self._timer.interval() / 1000.0, self.BOT_HOLD_S)
        started = time.monotonic()
        if sink.last_publish and started - sink.last_publish < hold:
            return  # the running bot owns the overlay
        self._busy = True
        self._thread = threading.Thread(target=self._work, args=(started,), name="overlay-refresh",
                                        daemon=True)
        self._thread.start()

    def _work(self, started: float) -> None:
        try:
            frame, outcome = self._s.overlay_snapshot(), "ok"
        except (WindowNotFoundError, WindowGoneError):
            frame, outcome = None, "lost"
        except Exception as exc:  # capture glitch: clear the overlay, try again next tick
            log.debug("overlay refresh failed: %s", exc)
            frame, outcome = None, "ok"
        if not self._closed:
            self._done.emit(frame, outcome, started)

    def _on_done(self, frame: DebugFrame | None, outcome: str, started: float) -> None:
        self._busy = False
        sink = self._s.overlay_sink
        if self._closed or sink is None or not sink.enabled:
            return
        if sink.last_publish > started:
            return  # a bot frame arrived meanwhile and is newer
        sink.refresh(frame)  # None clears stale markers
        if outcome == "lost":
            self._timer.stop()


class _RunEvents(QObject):
    """Receives run results in the UI thread (queued from the workers) and sends notifications."""

    def __init__(self, services: AppServices) -> None:
        super().__init__()
        self._s = services

    @Slot(object)
    def on_primary_finished(self, result: RunResult) -> None:
        s = self._s
        s.notify_run(s.window, s.runner.current_task, result)

    @Slot(str, object)
    def on_slot_finished(self, key: str, result: RunResult) -> None:
        s = self._s
        slot = next((x for x in s.manager.slots() if x.key == key), None)
        s.notify_run(slot.window if slot else None, slot.runner.current_task if slot else None, result)


class AppServices:
    def __init__(
        self,
        settings: Settings | None = None,
        settings_path: str | Path | None = DEFAULT_SETTINGS_PATH,
        profile_dir: str | Path | None = None,
        discover: DiscoverFn | None = None,
        capturer_factory: CapturerFactory | None = None,
        input_factory: InputFactory | None = None,
        log_dir: str | Path | None = None,
        rebind_windows: bool = True,
        data_dir: str | Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.settings_path = Path(settings_path) if settings_path is not None else None
        self.profile_dir = Path(profile_dir) if profile_dir else PROFILES_DIR / self.settings.profile
        self._discover = discover if discover is not None else _discover
        self.capturer_factory = capturer_factory if capturer_factory is not None else _win32_capturer
        self.input_factory = input_factory if input_factory is not None else _win32_input
        self.rebind_windows = rebind_windows and sys.platform == "win32"

        self.features: list[type[Task]] = registry.discover()
        self.settings.forget_features({c.id for c in self.features})
        self.templates = TemplateStore(self.profile_dir / "templates")
        self.locator_config = LocatorConfig.load(self.profile_dir / "locator.yaml")
        self.popups = PopupGuard.load(self.profile_dir / "popups.yaml")
        self.panels = Panels.load(self.profile_dir / "panels.yaml")

        self.window: GameWindow | None = None
        # One locator per bound window: its last good fix is reused between runs (docs/06 Track).
        self.locator: CanvasLocator = self.new_locator()
        if self.settings.debug and not self.settings.draw_overlay:
            self.settings.draw_overlay = True
        self.debug = DebugState(self.settings.debug)
        self.debug.changed.connect(self._on_debug_changed)
        self.draw_overlay = DebugState(self.settings.draw_overlay)
        self.draw_overlay.changed.connect(self._on_draw_overlay_changed)
        self.panel_sink = PanelSink()
        self.live_sink = FanoutSink([self.panel_sink])
        self.overlay_sink: OverlaySink | None = None
        # Live overlay refresh between bot frames: its own locator (refresh thread).
        self._overlay_locator = self.new_locator()
        self.overlay_refresher = OverlayRefresher(self)
        base_log = Path(log_dir if log_dir is not None else self.settings.log_dir)
        self.runner = TaskRunner(base_log if base_log.is_absolute() else PROJECT_ROOT / base_log)
        self.hotkeys = HotkeyManager()
        self.hotkey_bridge = HotkeyBridge()
        self.hotkey_bridge.stop_requested.connect(self.runner.stop)
        self.hotkey_bridge.pause_requested.connect(self.runner.toggle_pause)
        self.hotkey_bridge.debug_toggle_requested.connect(self.debug.toggle)
        # Multi-window: one runner per extra game window ("Nhiều cửa sổ" tab); the stop and pause
        # hotkeys act on every runner.
        self.manager = RunnerManager(self.runner.log_dir, self._make_slot_actor, self.new_locator,
                                     self._primary_busy_on)
        self.hotkey_bridge.stop_requested.connect(self.manager.stop_all)
        self.hotkey_bridge.pause_requested.connect(self.manager.toggle_pause_all)
        # Notifications on finish / stop / error (tray, Telegram, Discord).
        self.notifier = Notifier(lambda: self.settings.notify)
        self.tray: QtTray | None = None
        self._run_events = _RunEvents(self)
        self.runner.finished.connect(self._run_events.on_primary_finished)
        self.manager.slot_finished.connect(self._run_events.on_slot_finished)
        # Run history and routine scheduler (docs/06 P3), persisted under data/.
        self._init_history_and_routines(data_dir)

    # --- settings -------------------------------------------------------------------------------
    def save(self) -> None:
        if self.settings_path is None:
            return
        try:
            save_settings(self.settings, self.settings_path)
        except OSError:
            log.exception("cannot save settings to %s", self.settings_path)

    def _on_debug_changed(self, enabled: bool) -> None:
        self.settings.debug = enabled
        self.draw_overlay.set_enabled(enabled)
        self.save()

    def _on_draw_overlay_changed(self, enabled: bool) -> None:
        self.settings.draw_overlay = enabled
        if self.overlay_sink is not None:
            self.overlay_sink.set_enabled(enabled)
        self.overlay_refresher.sync()
        self.save()

    def set_overlay_refresh_ms(self, ms: int) -> int:
        """Change the overlay refresh interval (clamped); returns the value kept."""
        self.settings.overlay_refresh_ms = ms
        self.overlay_refresher.sync()
        self.save()
        return self.settings.overlay_refresh_ms

    def attach_overlay(self, presenter: object) -> OverlaySink:
        """Connect the live overlay window (``OverlayPresenter``) to the debug frames."""
        sink = OverlaySink(presenter, enabled=self.draw_overlay.enabled)  # type: ignore[arg-type]
        self.overlay_sink = sink
        self.live_sink.add(sink)
        self.overlay_refresher.sync()
        return sink

    # --- hotkeys --------------------------------------------------------------------------------
    def apply_hotkeys(self) -> list[str]:
        hk = self.settings.hotkeys
        b = self.hotkey_bridge
        return self.hotkeys.set_bindings({
            "Dừng": (hk.stop, b.stop_requested.emit),
            "Gỡ lỗi": (hk.debug_toggle, b.debug_toggle_requested.emit),
            "Tạm dừng": (hk.pause, b.pause_requested.emit),
        })

    # --- window ---------------------------------------------------------------------------------
    def discover_windows(self) -> list[GameWindow]:
        try:
            return self._discover()
        except Exception:
            log.exception("window discovery failed")
            return []

    def bind(self, window: GameWindow | None) -> None:
        self.window = window
        self.locator.reset()  # another window: the old canvas position means nothing
        self._overlay_locator.reset()
        if window is not None:
            self.settings.window.title = window.title
            self.settings.window.exe_path = window.exe_path
        if self.overlay_sink is not None:
            self.overlay_sink.refresh(None)  # markers of the previous window are stale
        self.overlay_refresher.sync()

    def preferred_index(self, windows: Sequence[GameWindow]) -> int:
        """Index of the window matching the saved title (else 0); -1 for an empty list."""
        if not windows:
            return -1
        for i, w in enumerate(windows):
            if self.settings.window.title and w.title == self.settings.window.title:
                return i
        return 0

    def require_window(self) -> GameWindow:
        gw = self.window
        if gw is None:
            raise WindowNotFoundError("Chưa chọn cửa sổ game (Coowon đang mở Ngọa Long)")
        if self.rebind_windows and not gw.rebind():
            raise WindowNotFoundError("Cửa sổ game đã đóng; hãy mở lại game và bấm Tìm lại")
        return gw

    def overlay_target(self) -> Rect | None:
        """Client rect of the game on screen, or None when it is minimised/covered (overlay hides)."""
        gw = self.window
        if gw is None or sys.platform != "win32":
            return None
        try:
            if not gw.is_alive() or gw.is_minimized():
                return None
            rect = gw.client_rect_on_screen()
            return rect if _point_shows_window(rect.center, gw.hwnd) else None
        except Exception:
            return None

    # --- building blocks ------------------------------------------------------------------------
    def new_locator(self) -> CanvasLocator:
        return CanvasLocator(self.locator_config, Matcher(self.templates, history=64))

    def perceiver(self) -> Perceiver:
        """The profile's perception layer (loaded once, shared with the decision loop)."""
        return load_profile(str(self.profile_dir))[0]

    def probe(self) -> tuple[np.ndarray, CanvasFix, str]:
        """Capture once, locate the canvas and describe the Snapshot ("Kiểm tra"; point recording)."""
        gw = self.require_window()
        frame = self.capturer_factory(gw).grab()
        fix = self.new_locator().locate(frame)
        if not fix.reliable:
            return frame, fix, "không thấy khung game"
        return frame, fix, self.perceiver().perceive(Perceiver.canvas(frame, fix)).describe()

    def overlay_snapshot(self) -> DebugFrame | None:
        """A live overlay frame of the bound window: canvas and Snapshot, outside any task
        (``OverlayRefresher``, refresh thread). ``None`` clears the overlay: game minimised or no
        canvas found. Raises ``WindowNotFoundError`` / ``WindowGoneError`` when the window is lost."""
        gw = self.require_window()
        if self.rebind_windows and gw.is_minimized():
            return None
        image = self.capturer_factory(gw).grab()
        fix = self._overlay_locator.locate(image)
        if fix.confidence is Confidence.NONE:
            return None
        if not fix.reliable:
            return DebugFrame(canvas=fix, window=gw.title, note="live")
        snap = self.perceiver().perceive(Perceiver.canvas(image, fix))
        frame = snapshot_frame(image, fix, snap, window=gw.title, note="live")
        frame.image = None  # the overlay draws over the game itself
        return frame

    def cursor_canvas_point(self) -> Point:
        """The mouse position as a canvas point (1000x600); raises ValueError when outside."""
        import ctypes
        from ctypes import wintypes

        from ngoalong.core import _winapi as w

        gw = self.require_window()
        pt = wintypes.POINT()
        with w.dpi_aware():
            if not w.GetCursorPos(ctypes.byref(pt)):
                raise ValueError("không đọc được vị trí chuột")
        origin = gw.client_origin_on_screen()
        client = Point(pt.x - origin.x, pt.y - origin.y)
        _frame, fix, _scene = self.probe()
        if not fix.reliable:
            raise ValueError("không tìm thấy khung game để quy đổi tọa độ")
        x, y = fix.to_canvas(client)
        cx, cy = round(x), round(y)
        if not (0 <= cx < 1000 and 0 <= cy < 600):
            raise ValueError(f"con trỏ nằm ngoài khung game ({cx}, {cy})")
        return Point(cx, cy)

    def make_actor(self, task: Task, hooks: RunHooks) -> Actor:
        """A fresh actor for one run (``TaskRunner`` actor factory)."""
        gw = self.require_window()
        if self.manager.running_on(gw):
            raise RuntimeError("cửa sổ này đang chạy ở thẻ Nhiều cửa sổ")
        return self.build_actor(gw, self.locator, task, hooks, self.live_sink)

    def _make_slot_actor(self, slot: WindowSlot, task: Task, hooks: RunHooks) -> Actor:
        """Actor factory of the multi-window runners (``RunnerManager``)."""
        gw = slot.window
        if self.rebind_windows and not gw.rebind():
            raise WindowNotFoundError(f"Cửa sổ game đã đóng: {gw.title}")
        # Only the debug panel: the overlay window follows the primary window, not this one.
        return self.build_actor(gw, slot.locator, task, hooks, self.panel_sink)

    def _primary_busy_on(self, gw: GameWindow) -> bool:
        """The "Tính năng" runner drives ``gw``: a run, or a routine (also between its steps)."""
        executor = getattr(self, "routine_executor", None)
        busy = self.runner.is_running() or (executor is not None and executor.is_active())
        return busy and same_window(self.window, gw)

    def sync_windows(self) -> list[WindowSlot]:
        """Discover the game windows and give each one a runner slot."""
        return self.manager.sync(self.discover_windows())

    def feature_title(self, fid: str) -> str:
        return next((c.title for c in self.features if c.id == fid), fid)

    # --- history and routines -------------------------------------------------------------------
    def _init_history_and_routines(self, data_dir: str | Path | None) -> None:
        """``data/`` sits next to ``settings.toml`` unless given; no settings file = memory only."""
        if data_dir is not None:
            self.data_dir: Path | None = Path(data_dir)
        elif self.settings_path is not None:
            self.data_dir = self.settings_path.parent / "data"
        else:
            self.data_dir = None
        d = self.data_dir
        self.history = HistoryStore(d / "history.sqlite3" if d is not None else None)
        self.history_recorder = HistoryRecorder(self.history)
        self.history_recorder.watch(self.runner, lambda: self.window)
        self.history_recorder.watch_manager(self.manager)
        self.routine_store = RoutineStore(d / "routines.json" if d is not None else None)
        self.routine_store.disable_unknown_steps({c.id for c in self.features})
        self.routine_executor = RoutineExecutor(self.runner, self.build_routine_task, self.make_actor,
                                                self.history_recorder)
        self.scheduler = SchedulerService(Scheduler(self.routine_store), self.routine_executor, self.runner)
        # Stop also cancels a routine waiting to retry (the runner is idle then).
        self.hotkey_bridge.stop_requested.connect(self.routine_executor.stop)

    def build_routine_task(self, step: RoutineStep) -> Task:
        """A routine step's task: the params saved in the "Tính năng" tab plus the step's overrides."""
        return build_step_task(step, self.settings.feature_params).bind_services(self)

    # --- notifications --------------------------------------------------------------------------
    def attach_tray(self) -> QtTray:
        """Show tray balloons for run results (called by ``main`` once the UI exists)."""
        if self.tray is None:
            self.tray = QtTray()
            self.notifier.set_tray(self.tray.show)
        return self.tray

    def notify_run(self, gw: GameWindow | None, task: Task | None, result: RunResult) -> None:
        """Notify a finished run (UI thread). A fresh screenshot is captured on the sender thread
        unless the run already saved a failure screenshot."""
        title = getattr(result, "task_title", "") or (
            task.title if task is not None and task.id == result.task_id else self.feature_title(result.task_id))
        capture = None
        if gw is not None:
            win = GameWindow(gw._info())
            factory, locator = self.capturer_factory, self.new_locator()

            def capture() -> np.ndarray:
                frame = factory(win).grab()
                crop = canvas_crop(frame, locator.locate(frame))
                return frame if crop is None else frame[crop.y:crop.bottom, crop.x:crop.right]

        try:
            self.notifier.notify_result(result, title, gw.title if gw is not None else "", capture)
        except Exception:
            log.exception("notification failed")

    def build_actor(
        self, gw: GameWindow, locator: CanvasLocator, task: Task, hooks: RunHooks, debug_sink: object
    ) -> Actor:
        return Actor(
            CachedCapturer(self.capturer_factory(gw), max_age_s=0.05),
            self.input_factory(gw),
            locator,
            hooks.control,
            on_log=hooks.on_log,
            on_progress=hooks.on_progress,
            on_status=hooks.on_status,
            debug_sink=debug_sink,
            png_sink=PngSink(hooks.run_dir, enabled=self.debug.enabled),
            debug_enabled=lambda: self.debug.enabled,
            overlay_enabled=lambda: self.draw_overlay.enabled,
            policy=lambda name: bool(getattr(self.settings.game, name, False)),
            popups=self.popups,
            panels=self.panels if self.settings.game.collapse_panels else None,
            matcher=Matcher(self.templates, history=256),
            window=gw.title,
            task=task.title,
        )

    def shutdown(self) -> None:
        self.overlay_refresher.stop()
        self.scheduler.stop()
        self.routine_executor.stop()
        self.runner.shutdown()
        self.manager.shutdown()
        self.hotkeys.stop()
        self.notifier.shutdown()
        if self.tray is not None:
            self.tray.close()
        self.history.close()
        self.save()


def _point_shows_window(p: Point, hwnd: int) -> bool:
    """True when the top-level window under screen point ``p`` is ``hwnd`` (not covered)."""
    import ctypes
    from ctypes import wintypes

    from ngoalong.core import _winapi as w

    with w.dpi_aware():
        h = w.WindowFromPoint(wintypes.POINT(p.x, p.y))
    if not h:
        return False
    root = w.GetAncestor(h, w.GA_ROOT) or h
    return int(root) == int(hwnd)


def _setup_logging() -> None:
    """Console logging from source; the windowed build has no console, so it logs to
    ``logs/app.log`` next to the exe instead."""
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if sys.stderr is not None:
        logging.basicConfig(level=logging.INFO, format=fmt)
        return
    log_file = APP_LOG_FILE
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format=fmt, filename=str(log_file), encoding="utf-8")
    except OSError:
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=[logging.NullHandler()])


def _install_excepthooks() -> None:
    """Unhandled exceptions (Qt slots, worker threads) go to the log: the windowed build has no
    stderr, so the default hooks would lose them."""

    def hook(t: type[BaseException], v: BaseException, tb: object) -> None:
        log.critical("unhandled exception", exc_info=(t, v, tb))  # type: ignore[arg-type]

    def thread_hook(a: threading.ExceptHookArgs) -> None:
        if a.exc_type is SystemExit:
            return
        log.critical("unhandled exception in thread %s", getattr(a.thread, "name", "?"),
                     exc_info=(a.exc_type, a.exc_value, a.exc_traceback))  # type: ignore[arg-type]

    sys.excepthook = hook
    threading.excepthook = thread_hook


def _show_startup_error(message: str) -> None:
    """Modal Vietnamese error box shown when the app cannot start."""
    from PySide6.QtWidgets import QMessageBox

    try:
        QMessageBox.critical(None, "AutoNgoaLong - lỗi khởi động", message)
    except Exception:
        log.exception("cannot show the startup error box")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ngoalong", description="AutoNgoaLong v2")
    ap.add_argument("--settings", default=str(DEFAULT_SETTINGS_PATH), help="settings.toml path")
    ap.add_argument("--ini", default=str(DEFAULT_INI_PATH), help="v1.8.0 NgoaLong_data.ini to import once")
    ap.add_argument("--debug", action="store_true", help="start with debug mode on")
    ap.add_argument("--selftest", action="store_true",
                    help="run the offline checks (perception benchmark on the bundled corpus) and exit "
                         "(0 = passed, 1 = failed)")
    ap.add_argument("--corpus", default=None,
                    help="--selftest: labels.yaml of a corpus (default: the bundled one, else corpus/live)")
    ap.add_argument("--report", default=None, help="--selftest: also write the report to this file")
    ap.add_argument("--no-ui", action="store_true", help="--selftest: skip the offscreen main window check")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        from ngoalong import selftest

        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
        return selftest.main(args.corpus, args.report, ui=not args.no_ui)

    _setup_logging()
    _install_excepthooks()
    from PySide6.QtWidgets import QApplication

    from ngoalong.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    settings: Settings | None = None
    services: AppServices | None = None
    try:
        features = registry.discover()
        settings, imported = load_or_import(args.settings, args.ini, features)
        settings.forget_features({c.id for c in features})
        if args.debug:
            settings.debug = True
            settings.draw_overlay = True
        services = AppServices(settings, args.settings)
        window = MainWindow(services)
    except Exception as exc:
        log.exception("startup failed")
        if services is not None:
            try:
                services.shutdown()
            except Exception:
                log.exception("shutdown after a failed start failed")
        profile = PROFILES_DIR / (settings.profile if settings is not None else Settings().profile)
        _show_startup_error(
            "Không khởi động được AutoNgoaLong.\n\n"
            f"Lỗi: {type(exc).__name__}: {exc}\n\n"
            f"Tệp cài đặt: {args.settings}\n"
            f"Thư mục hồ sơ: {profile}\n\n"
            f"Xem chi tiết trong {APP_LOG_FILE}. Chạy \"AutoNgoaLong.exe --selftest\" để kiểm tra "
            "hồ sơ (ảnh mẫu, màn hình, nhận diện)."
        )
        return 1
    services.attach_tray()
    if imported:
        window.log_message("info", f"Đã nhập cài đặt cũ từ {args.ini}")
    for err in services.apply_hotkeys():
        window.log_message("warning", f"Phím tắt: {err}")
    window.show()
    services.scheduler.start()  # routines run at their set times while the app is open
    try:
        return int(app.exec())
    finally:
        services.shutdown()
