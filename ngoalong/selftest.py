"""Offline self-test: ``AutoNgoaLong.exe --selftest`` (or ``python -m ngoalong --selftest``).

Checks, without the game and without clicking anything:

1. **profile**: ``profiles/<name>/`` exists next to the app; ``locator.yaml``, the perception data
   (``parts.yaml``, ``probes.yaml``, ``numbers.yaml``, ``screens/*.yaml``, the digit glyphs),
   ``nav.yaml``, ``popups.yaml`` and ``panels.yaml`` load and agree with each other; every template
   they search and every crop of ``template_crops.yaml`` / ``part_crops.yaml`` exists.
2. **features**: exactly the seven features are registered; each ``Params`` builds with defaults,
   each feature's rules build and its entry screen exists.
3. **driver**: the Win32 window driver imports (capture and input).
4. **perception**: the perception benchmark (``ngoalong/perception/bench.py``, the same as
   ``tools/perceive.py --bench``) on the corpus bundled into the build: every frame's screen stack
   and labelled reader values must be right, with no OCR. Timing is reported, not judged. When no
   corpus is bundled the check says so and is skipped (the build bundles one when the corpus is on
   the build machine, see ``write_bundle``).
5. **ui** (optional): the main window builds offscreen (catches a broken Qt bundle).

Returns 0 when no check failed, 1 otherwise. In the windowed build there is no console, so the
report is also written to ``selftest-report.txt`` next to the exe.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ngoalong.paths import APP_ROOT, BUNDLE_ROOT, is_frozen

REPORT_NAME = "selftest-report.txt"
SOURCE_LABELS = Path("corpus") / "live" / "labels.yaml"
BUNDLE_DIR_NAME = "selftest"

EXPECTED_FEATURES = frozenset({
    "autoclick", "quay_ruong", "khoa_ky", "cong_trinh", "thap_thi_luyen", "clear_map", "vo_khoi_thap",
})

# Frames of corpus/live bundled into the build (frame numbers of labels.yaml). Chosen by a greedy
# cover over the labels so that each one is seen at least once: every screen of the six features
# (33, with the overlays), every distinct feature screen stack (47), every labelled reader value
# that a reader checks (41 readers), and the 9 kinds of other screens (unknown dialogs, full-screen
# locations, HUD maps), plus the field beams the campaign must find (576: a beam taller than usual on
# a bright court). 58 frames, about 35 MB as lossless WebP.
BUNDLE_FRAMES = (
    2, 3, 6, 10, 23, 30, 32, 33, 38, 42, 58, 62, 86, 88, 100, 103, 104, 114, 119, 124, 125, 134, 135,
    155, 191, 205, 208, 215, 217, 258, 260, 266, 273, 298, 306, 360, 362, 367, 371, 387, 393, 410,
    426, 427, 430, 435, 442, 468, 470, 477, 481, 482, 522, 538, 540, 541, 564, 576,
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    group: str
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False

    def line(self) -> str:
        tag = "SKIP" if self.skipped else "OK  " if self.ok else "FAIL"
        return f"{tag} [{self.group}] {self.name}" + (f": {self.detail}" if self.detail else "")


# --- the bundled corpus ---------------------------------------------------------------------------
def default_labels() -> Path | None:
    """The corpus bundled in the build, else the local corpus of the source tree."""
    for p in (BUNDLE_ROOT / BUNDLE_DIR_NAME / "labels.yaml", APP_ROOT / SOURCE_LABELS):
        if p.is_file():
            return p
    return None


def write_bundle(dest: str | Path, labels: str | Path | None = None,
                 frames: Iterable[int] = BUNDLE_FRAMES) -> tuple[Path, list[str]]:
    """Copy the chosen corpus frames to ``dest`` as lossless WebP and write ``dest/labels.yaml`` with
    their labels (ranges written out per frame). Used by the PyInstaller spec at build time.

    Returns ``(dest, missing)``: frames that are not on this machine are left out and listed (the
    corpus PNGs are not in git). ``dest`` is left empty when the corpus is missing altogether; the
    self-test then reports that no corpus is bundled."""
    import cv2

    from ngoalong.core.capture import read_image
    from ngoalong.perception.bench import frame_number

    src = Path(labels) if labels is not None else APP_ROOT / SOURCE_LABELS
    out = Path(dest)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    wanted = list(frames)
    if not src.is_file():
        return out, [f"{n:03d}" for n in wanted]
    raw: dict[str, Any] = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    by_number: dict[int, Any] = {}
    for key, label in raw.items():
        for n in frame_number(key):
            by_number[n] = label
    files = {int(p.name[:3]): p for p in src.parent.glob("[0-9][0-9][0-9]-*.png")}
    kept: dict[str, Any] = {}
    missing: list[str] = []
    for n in wanted:
        p = files.get(n)
        if p is None or n not in by_number:
            missing.append(f"{n:03d}")
            continue
        target = out / f"{p.stem}.webp"
        ok, data = cv2.imencode(".webp", read_image(p), [cv2.IMWRITE_WEBP_QUALITY, 101])  # 101 = lossless
        if not ok:
            raise RuntimeError(f"cannot encode {p}")
        data.tofile(str(target))
        kept[target.name] = by_number[n]
    if kept:
        (out / "labels.yaml").write_text(
            "# Generated by ngoalong.selftest.write_bundle (build) from corpus/live/labels.yaml; do not edit.\n"
            + yaml.safe_dump(kept, allow_unicode=True, sort_keys=False, width=200),
            encoding="utf-8",
        )
    return out, missing


# --- checks ---------------------------------------------------------------------------------------
def _guard(group: str, name: str, fn: Callable[[], str]) -> CheckResult:
    """Run one check: a returned string is the OK detail, an exception the failure detail."""
    try:
        return CheckResult(group, name, True, fn())
    except Exception as exc:  # every failure becomes a FAIL line, never a crash
        return CheckResult(group, name, False, f"{type(exc).__name__}: {exc}")


def check_profile(profile_dir: Path) -> list[CheckResult]:
    from ngoalong.core.locator import LocatorConfig
    from ngoalong.core.templates import TemplateStore
    from ngoalong.engine.interrupts import Panels, PopupGuard, templates_used
    from ngoalong.engine.loop import load_profile

    results = [CheckResult("profile", "folder", profile_dir.is_dir(), str(profile_dir))]
    if not profile_dir.is_dir():
        return results

    def perception() -> str:
        p, nav = load_profile(str(profile_dir))  # checks nav.yaml against the screens and parts
        book = p.book
        return (f"{len(p.parts.specs)} phần giao diện, {len(p.probes)} đầu dò màu, {len(p.numbers)} ô số, "
                f"{len(book.by_name)} màn hình, {len(nav.edges)} đường đi")

    def templates() -> str:
        store = TemplateStore(profile_dir / "templates")
        names = {a.template for a in LocatorConfig.load(profile_dir / "locator.yaml").anchors}
        names |= templates_used(PopupGuard.load(profile_dir / "popups.yaml"), Panels.load(profile_dir / "panels.yaml"))
        for crops_name in ("template_crops.yaml", "part_crops.yaml"):
            crops_file = profile_dir / crops_name
            if crops_file.is_file():
                crops = (yaml.safe_load(crops_file.read_text(encoding="utf-8")) or {}).get("crops") or []
                names.update(str(c["name"]) for c in crops if isinstance(c, dict) and c.get("name"))
        missing = sorted(n for n in names if n not in store)
        if missing:
            raise FileNotFoundError(f"thiếu mẫu ảnh: {', '.join(missing)}")
        return f"{len(names)} mẫu ảnh (mốc khung, popup, bảng, danh sách cắt), đủ cả"

    results.append(_guard("profile", "locator",
                          lambda: f"{len(LocatorConfig.load(profile_dir / 'locator.yaml').anchors)} mốc"))
    results.append(_guard("profile", "perception", perception))
    results.append(_guard("profile", "templates", templates))
    return results


def check_features(profile_dir: Path) -> list[CheckResult]:
    from ngoalong.engine import registry
    from ngoalong.engine.feature import Feature
    from ngoalong.engine.loop import load_profile

    try:
        features = registry.discover()
    except Exception as exc:
        return [CheckResult("features", "discover", False, f"{type(exc).__name__}: {exc}")]
    ids = {c.id for c in features}
    missing, extra = EXPECTED_FEATURES - ids, ids - EXPECTED_FEATURES
    problems = ([f"thiếu: {', '.join(sorted(missing))}"] if missing else []) + \
               ([f"thừa: {', '.join(sorted(extra))}"] if extra else [])
    results = [CheckResult("features", "registry", not problems,
                           "; ".join(problems) if problems else f"{len(ids)} tính năng")]

    def one(cls: Any) -> str:
        params = cls.Params()
        if not issubclass(cls, Feature):
            return f"{cls.title}: {len(params.model_dump())} thông số"
        screens = load_profile(str(profile_dir))[0].book.by_name
        if cls.entry not in screens:
            raise ValueError(f"màn hình bắt đầu {cls.entry!r} không có trong screens/*.yaml")
        rules = cls(params).rules()
        return f"{cls.title}: {len(params.model_dump())} thông số, {len(rules)} luật, bắt đầu ở {cls.entry}"

    for cls in features:
        results.append(_guard("features", cls.id, lambda cls=cls: one(cls)))
    return results


def check_driver() -> list[CheckResult]:
    def run() -> str:
        if sys.platform != "win32":
            return "không phải Windows: bỏ qua"
        from ngoalong.core.drivers.win32 import Win32Capturer, Win32InputDriver
        from ngoalong.core.window import GameWindow

        names = [Win32Capturer.__name__, Win32InputDriver.__name__, GameWindow.__name__]
        return f"{', '.join(names)} nạp được"

    return [_guard("driver", "win32", run)]


def check_perception(labels: Path | None, profile_dir: Path) -> list[CheckResult]:
    if labels is None or not labels.is_file():
        where = labels or (BUNDLE_ROOT / BUNDLE_DIR_NAME if is_frozen() else APP_ROOT / SOURCE_LABELS)
        return [CheckResult("perception", "corpus", True,
                            f"KHÔNG có bộ ảnh mẫu (corpus) đi kèm ({where}): bỏ qua bài kiểm tra nhận diện",
                            skipped=True)]

    def run() -> str:
        from ngoalong.engine.loop import load_profile
        from ngoalong.perception import bench

        perceiver = load_profile(str(profile_dir))[0]
        res = bench.run(perceiver, labels)
        if not res.rows:
            raise AssertionError(f"{labels}: không có ảnh nào khớp nhãn")
        lines = bench.report(res, per_screen=False)
        feat = res.feature_rows
        ok = res.gate(timing=False) and all(r.ok for r in res.rows) and res.wrong_readers == 0
        detail = (f"{sum(r.ok for r in feat)}/{len(feat)} ảnh của 6 tính năng và "
                  f"{sum(r.ok for r in res.other_rows)}/{len(res.other_rows)} ảnh khác đúng màn hình; "
                  f"{sum(c['ok'] for c in res.readers.values())} giá trị đọc đúng, {res.wrong_readers} sai; "
                  f"Snapshot tối đa {res.max_ms('ms_all'):.0f} ms; OCR: {res.ocr_modules or 'không'}")
        if not ok:
            raise AssertionError(detail + "\n" + "\n".join(lines))
        return detail

    return [_guard("perception", f"benchmark ({labels.parent.name})", run)]


def check_ui(profile_dir: Path) -> list[CheckResult]:
    """Build the main window offscreen with no game window (nothing is shown or clicked)."""

    def run() -> str:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from ngoalong.app import AppServices
        from ngoalong.core.drivers.fake import RecordingInputDriver
        from ngoalong.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication(["ngoalong-selftest"])
        with tempfile.TemporaryDirectory(prefix="ngoalong-selftest-") as tmp:
            services = AppServices(
                settings_path=None, profile_dir=profile_dir, discover=lambda: [],
                capturer_factory=_no_capture, input_factory=lambda gw: RecordingInputDriver(),
                log_dir=Path(tmp) / "logs", rebind_windows=False,
            )
            try:
                window = MainWindow(services, overlay=False)
                try:
                    app.processEvents()
                    tabs = [window.tabs.tabText(i) for i in range(window.tabs.count())]
                    forms = len(window.feature_page.forms)
                finally:
                    window.close()
                    window.deleteLater()
                    app.processEvents()
            finally:
                services.shutdown()
        if forms != len(EXPECTED_FEATURES):
            raise AssertionError(f"có {forms} biểu mẫu tính năng, cần {len(EXPECTED_FEATURES)}")
        return f"{len(tabs)} thẻ ({', '.join(tabs)}), {forms} biểu mẫu tính năng"

    return [_guard("ui", "main_window", run)]


def _no_capture(_gw: Any) -> Any:
    raise RuntimeError("self-test: no game capture")


# --- entry point ----------------------------------------------------------------------------------
def run_selftest(
    labels: str | Path | None = None,
    profile_dir: str | Path | None = None,
    ui: bool = True,
    out: Callable[[str], None] = print,
) -> int:
    """Run every check, write one line per check through ``out``; 0 = none failed, 1 = a failure."""
    started = time.monotonic()
    profile = Path(profile_dir) if profile_dir is not None else APP_ROOT / "profiles" / "ngoalongviet"
    out(f"AutoNgoaLong self-test ({'bản build' if is_frozen() else 'mã nguồn'}), thư mục: {APP_ROOT}")
    results: list[CheckResult] = []
    results += check_profile(profile)
    results += check_features(profile)
    results += check_driver()
    labels_path = Path(labels) if labels is not None else default_labels()
    if profile.is_dir():
        results += check_perception(labels_path, profile)
    if ui:
        results += check_ui(profile)
    for r in results:
        out(r.line())
    failed = [r for r in results if not r.ok]
    skipped = [r for r in results if r.skipped]
    out(f"{len(results) - len(failed) - len(skipped)}/{len(results)} đạt, {len(failed)} lỗi"
        + (f", {len(skipped)} bỏ qua" if skipped else "") + f", {time.monotonic() - started:.1f} giây")
    return 1 if failed else 0


def main(labels: str | None, report: str | None, ui: bool) -> int:
    """``--selftest`` entry: print to the console when there is one, and write the report file
    (always in the windowed build, which has no console)."""
    lines: list[str] = []
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:  # a pipe in a non-UTF-8 code page must not fail on Vietnamese text
            sys.stdout.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass

    def out(line: str) -> None:
        lines.append(line)
        stream = sys.stdout
        if stream is not None:
            try:
                stream.write(line + "\n")
                stream.flush()
            except (OSError, UnicodeEncodeError, ValueError):
                pass

    try:
        code = run_selftest(labels, ui=ui, out=out)
    except Exception as exc:  # never let the windowed build show a crash dialog
        out(f"FAIL [selftest] {type(exc).__name__}: {exc}")
        code = 1
    report_path = Path(report) if report else (APP_ROOT / REPORT_NAME if sys.stdout is None or is_frozen() else None)
    if report_path is not None:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass  # the exit code still tells the result
    return code
