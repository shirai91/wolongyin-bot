"""Offline self-test: ``AutoNgoaLong.exe --selftest`` (or ``python -m ngoalong --selftest``).

Checks, without the game and without clicking anything:

1. **profile**: ``profiles/<name>/`` exists next to the app, and its ``coords/*.yaml``,
   ``scenes/*.yaml`` and ``locator.yaml`` load; every template they reference, every template a
   feature loads by name (``Task.required_templates``) and every ``template_crops.yaml`` crop exists.
2. **features**: every v1.8.0 feature is registered and its ``Params`` model builds with defaults.
3. **golden**: the canvas locator finds the 1000x600 canvas within 2 px (high confidence) and the
   scene detector names the expected scene on stored screenshots (the docs/07 golden set: the
   same expectations as ``tests/fixtures/screens/expected.yaml``).
4. **ui** (optional): the main window builds offscreen (catches a broken Qt bundle).

Returns 0 when every check passes, 1 otherwise. In the windowed build there is no console, so the
report is also written to ``selftest-report.txt`` next to the exe.

The build bundles a subset of the golden screenshots (``write_bundle``) as ``selftest/`` inside
the app; from source the full ``tests/fixtures/screens/expected.yaml`` set is used.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ngoalong.paths import APP_ROOT, BUNDLE_ROOT, is_frozen

TOLERANCE_PX = 2  # docs/06 P0 exit gate: canvas within 2 px
MIN_SCENE_SCORE = 0.85
REPORT_NAME = "selftest-report.txt"
SOURCE_GOLDEN = Path("tests") / "fixtures" / "screens" / "expected.yaml"
BUNDLE_DIR_NAME = "selftest"

# Every feature of v1.8.0 (docs/03) must be registered in the build.
EXPECTED_FEATURES = frozenset({
    "autoclick", "quay_ruong", "mo_the_bai", "khoa_ky", "cong_trinh", "chua_cong",
    "thap_thi_luyen", "clear_map", "tham_bao", "vo_khoi_thap", "tri_ky",
})

# Golden screenshots bundled into the build (file stems): the 7 reference captures plus live
# captures covering the main feature screens and an anchors-only case. About 18 MB.
BUNDLE_STEMS = frozenset({
    "main-game", "dungeon", "worldmap-ui", "tower-ui", "tower-ui-enter-confirmation",
    "chest-rolling-ui", "tower-in-dungeon",
    "01-city-main", "05-pho-ban-list", "09-dungeon-army-dialog", "16-khoa-ky",
    "17-tuu-quan-tri-ky", "26-tower-dialog", "27-loi-dai", "44-vo-khoi-thap",
})


@dataclass(frozen=True, slots=True)
class CheckResult:
    group: str
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return f"{'OK  ' if self.ok else 'FAIL'} [{self.group}] {self.name}" + (f": {self.detail}" if self.detail else "")


@dataclass(frozen=True, slots=True)
class GoldenCase:
    path: Path
    canvas: tuple[int, int]
    scene: str
    overlays: tuple[str, ...] = ()
    via_anchors: bool = False

    @property
    def name(self) -> str:
        return self.path.stem


# --- golden data ----------------------------------------------------------------------------------
def default_golden() -> Path | None:
    """The bundled golden set in the build, else the test fixtures of the source tree."""
    for p in (BUNDLE_ROOT / BUNDLE_DIR_NAME / "expected.yaml", APP_ROOT / SOURCE_GOLDEN):
        if p.is_file():
            return p
    return None


def load_cases(golden: str | Path) -> list[GoldenCase]:
    """Cases of every list in the YAML file. ``root:`` (relative to the YAML file) is where the
    screenshot paths start; without it they start at the project root (``APP_ROOT``)."""
    gp = Path(golden)
    data = yaml.safe_load(gp.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{gp}: top level must be a mapping")
    root_value = data.get("root")
    root = (gp.parent / str(root_value)).resolve() if root_value is not None else APP_ROOT
    cases: list[GoldenCase] = []
    for key, items in data.items():
        if key == "root" or not isinstance(items, list):
            continue
        for item in items:
            x, y = item["canvas"]
            cases.append(GoldenCase(
                path=root / item["file"],
                canvas=(int(x), int(y)),
                scene=str(item["scene"]),
                overlays=tuple(item.get("overlays") or ()),
                via_anchors=bool(item.get("via_anchors", False)),
            ))
    return cases


def write_bundle(
    dest: str | Path,
    source: str | Path | None = None,
    stems: Iterable[str] = BUNDLE_STEMS,
) -> Path:
    """Copy the selected golden screenshots to ``dest/screens`` and write ``dest/expected.yaml``
    (``root: .``). Used by the PyInstaller spec; raises if a selected screenshot is missing."""
    src = Path(source) if source is not None else APP_ROOT / SOURCE_GOLDEN
    wanted = set(stems)
    out = Path(dest)
    if out.exists():
        shutil.rmtree(out)
    (out / "screens").mkdir(parents=True)
    data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    result: dict[str, Any] = {"root": "."}
    found: set[str] = set()
    for group, items in data.items():
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            p = APP_ROOT / item["file"]
            if p.stem not in wanted:
                continue
            if not p.is_file():
                raise FileNotFoundError(f"golden screenshot missing: {p}")
            shutil.copy2(p, out / "screens" / p.name)
            kept.append({**item, "file": f"screens/{p.name}"})
            found.add(p.stem)
        if kept:
            result[group] = kept
    missing = wanted - found
    if missing:
        raise ValueError(f"golden stems not in {src}: {sorted(missing)}")
    target = out / "expected.yaml"
    target.write_text(
        "# Generated by ngoalong.selftest.write_bundle (build); do not edit.\n"
        + yaml.safe_dump(result, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return target


# --- checks ---------------------------------------------------------------------------------------
def _guard(group: str, name: str, fn: Callable[[], str]) -> CheckResult:
    """Run one check: a returned string is the OK detail, an exception the failure detail."""
    try:
        return CheckResult(group, name, True, fn())
    except Exception as exc:  # every failure becomes a FAIL line, never a crash
        return CheckResult(group, name, False, f"{type(exc).__name__}: {exc}")


def check_profile(profile_dir: Path) -> list[CheckResult]:
    from ngoalong.core.coords import CoordMap
    from ngoalong.core.locator import LocatorConfig
    from ngoalong.core.scenes import load_scene_defs
    from ngoalong.core.templates import TemplateStore
    from ngoalong.engine import registry

    results = [CheckResult("profile", "folder", profile_dir.is_dir(), str(profile_dir))]
    if not profile_dir.is_dir():
        return results

    def coords() -> str:
        cm = CoordMap.load(profile_dir / "coords")
        return f"{len(cm.features)} nhóm tọa độ, {sum(1 for _ in cm.point_keys())} điểm"

    def templates() -> str:
        store = TemplateStore(profile_dir / "templates")
        names: set[str] = set()
        for d in load_scene_defs(profile_dir / "scenes"):
            names.update(r.template for r in d.signature)
            names.update(o.template for o in d.objects.values() if o.template)
        names.update(a.template for a in LocatorConfig.load(profile_dir / "locator.yaml").anchors)
        # Templates the features load by name, and every crop of the reproducible crop list.
        for cls in registry.discover():
            names.update(cls.required_templates())
        crops_file = profile_dir / "template_crops.yaml"
        if crops_file.is_file():
            crops = (yaml.safe_load(crops_file.read_text(encoding="utf-8")) or {}).get("crops") or []
            names.update(str(c["name"]) for c in crops if isinstance(c, dict) and c.get("name"))
        missing = sorted(n for n in names if n not in store)
        if missing:
            raise FileNotFoundError(f"thiếu mẫu ảnh: {', '.join(missing)}")
        return f"{len(names)} mẫu ảnh được dùng, đủ cả"

    results.append(_guard("profile", "coords", coords))
    results.append(_guard("profile", "scenes",
                          lambda: f"{len(load_scene_defs(profile_dir / 'scenes'))} màn hình"))
    results.append(_guard("profile", "locator",
                          lambda: f"{len(LocatorConfig.load(profile_dir / 'locator.yaml').anchors)} mốc"))
    results.append(_guard("profile", "templates", templates))
    return results


def check_features() -> list[CheckResult]:
    from ngoalong.engine import registry

    results: list[CheckResult] = []
    try:
        features = registry.discover()
    except Exception as exc:
        return [CheckResult("features", "discover", False, f"{type(exc).__name__}: {exc}")]
    ids = {c.id for c in features}
    missing = EXPECTED_FEATURES - ids
    results.append(CheckResult("features", "registry", not missing,
                               f"thiếu: {', '.join(sorted(missing))}" if missing else f"{len(ids)} tính năng"))
    for cls in features:
        results.append(_guard("features", cls.id, lambda cls=cls: f"{cls.title}: {len(cls.Params().model_dump())} thông số"))
    return results


def check_golden(cases: Sequence[GoldenCase], profile_dir: Path) -> list[CheckResult]:
    from ngoalong.core.capture import read_image
    from ngoalong.core.locator import BORDER, CanvasLocator, Confidence
    from ngoalong.core.scenes import SceneDetector

    if not cases:
        return [CheckResult("golden", "cases", False, "không có ảnh mẫu nào")]
    detector = SceneDetector.load(profile_dir / "scenes", templates_dir=profile_dir / "templates")
    results: list[CheckResult] = []
    for case in cases:
        def run(case: GoldenCase = case) -> str:
            frame = read_image(case.path)
            fix = CanvasLocator.from_profile(profile_dir).locate(frame)  # fresh: no last-good reuse
            r = fix.rect
            problems: list[str] = []
            if fix.confidence is not Confidence.HIGH:
                problems.append(f"độ tin cậy {fix.confidence.value}")
            ex, ey = case.canvas
            if abs(r.x - ex) > TOLERANCE_PX or abs(r.y - ey) > TOLERANCE_PX \
                    or abs(r.w - 1000) > TOLERANCE_PX or abs(r.h - 600) > TOLERANCE_PX:
                problems.append(f"khung ({r.x}, {r.y}) {r.w}x{r.h}, cần ({ex}, {ey}) 1000x600")
            if case.via_anchors and BORDER in fix.detectors:
                problems.append("viền đen không được dùng cho ảnh này")
            scene = detector.detect(frame, fix)
            if scene.name != case.scene or scene.score < MIN_SCENE_SCORE:
                problems.append(f"màn hình {scene.name} ({scene.score:.2f}), cần {case.scene}")
            missing = set(case.overlays) - set(scene.overlays)
            if missing:
                problems.append(f"thiếu lớp phủ {', '.join(sorted(missing))}")
            if problems:
                raise AssertionError("; ".join(problems))
            return f"khung ({r.x}, {r.y}), {scene.name} {scene.score:.2f}"

        results.append(_guard("golden", case.name, run))
    return results


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
        if forms < len(EXPECTED_FEATURES):
            raise AssertionError(f"chỉ có {forms} biểu mẫu tính năng")
        return f"{len(tabs)} thẻ, {forms} biểu mẫu tính năng"

    return [_guard("ui", "main_window", run)]


def _no_capture(_gw: Any) -> Any:
    raise RuntimeError("self-test: no game capture")


# --- entry point ----------------------------------------------------------------------------------
def run_selftest(
    golden: str | Path | None = None,
    profile_dir: str | Path | None = None,
    ui: bool = True,
    out: Callable[[str], None] = print,
) -> int:
    """Run every check, write one line per check through ``out``; 0 = all passed, 1 = a failure."""
    started = time.monotonic()
    profile = Path(profile_dir) if profile_dir is not None else APP_ROOT / "profiles" / "ngoalongviet"
    out(f"AutoNgoaLong self-test ({'bản build' if is_frozen() else 'mã nguồn'}), thư mục: {APP_ROOT}")
    results: list[CheckResult] = []
    results += check_profile(profile)
    results += check_features()
    golden_path = Path(golden) if golden is not None else default_golden()
    if golden_path is None or not golden_path.is_file():
        results.append(CheckResult("golden", "expected.yaml", False, f"không thấy tệp ảnh mẫu: {golden_path}"))
    elif profile.is_dir():
        try:
            cases = load_cases(golden_path)
        except Exception as exc:
            results.append(CheckResult("golden", "expected.yaml", False, f"{type(exc).__name__}: {exc}"))
        else:
            results += check_golden(cases, profile)
    if ui:
        results += check_ui(profile)
    for r in results:
        out(r.line())
    failed = [r for r in results if not r.ok]
    out(f"{len(results) - len(failed)}/{len(results)} đạt, {len(failed)} lỗi, "
        f"{time.monotonic() - started:.1f} giây")
    return 1 if failed else 0


def main(golden: str | None, report: str | None, ui: bool) -> int:
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
        code = run_selftest(golden, ui=ui, out=out)
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
