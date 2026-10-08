"""P4 release: app paths (source vs PyInstaller build), the ``--selftest`` golden checks and the
build files. Offline: stored screenshots only, no game window."""

from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

import pytest
import yaml
from conftest import EXPECTED_FILE, PROFILE, ROOT

from ngoalong import paths, selftest
from ngoalong.selftest import BUNDLE_STEMS, CheckResult, load_cases, run_selftest, write_bundle

SMALL = ("chest-rolling-ui", "01-city-main", "27-loi-dai")  # border, live and anchors-only cases


# --- paths ----------------------------------------------------------------------------------------
def test_paths_from_source() -> None:
    assert not paths.is_frozen()
    assert paths.APP_ROOT == ROOT
    assert paths.BUNDLE_ROOT == ROOT
    assert paths.PROFILES_DIR == ROOT / "profiles"


def test_paths_in_the_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "AutoNgoaLong" / "AutoNgoaLong.exe"
    internal = tmp_path / "AutoNgoaLong" / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    assert paths.is_frozen()
    assert paths.app_root() == exe.parent.resolve()  # profiles/, settings.toml, logs/ next to the exe
    assert paths.bundle_root() == internal


def test_default_paths_use_the_app_root() -> None:
    from ngoalong.config import settings
    from ngoalong.core import coords, locator, scenes

    assert settings.DEFAULT_SETTINGS_PATH == paths.APP_ROOT / "settings.toml"
    assert coords.DEFAULT_COORDS_DIR == paths.PROFILES_DIR / "ngoalongviet" / "coords"
    assert locator.DEFAULT_PROFILE_DIR == scenes.DEFAULT_PROFILE_DIR == paths.PROFILES_DIR / "ngoalongviet"


# --- golden data ----------------------------------------------------------------------------------
def test_source_golden_is_the_test_fixture() -> None:
    golden = selftest.default_golden()
    assert golden == EXPECTED_FILE
    data = yaml.safe_load(EXPECTED_FILE.read_text(encoding="utf-8"))
    cases = load_cases(golden)
    assert len(cases) == sum(len(v) for v in data.values())
    assert all(c.path.is_file() for c in cases)


def test_write_bundle_copies_the_selected_screens(tmp_path: Path) -> None:
    target = write_bundle(tmp_path / "bundle")
    assert target == tmp_path / "bundle" / "expected.yaml"
    cases = load_cases(target)
    assert {c.name for c in cases} == set(BUNDLE_STEMS)
    assert all(c.path.parent == (tmp_path / "bundle" / "screens").resolve() for c in cases)
    assert all(c.path.is_file() for c in cases)
    assert any(c.via_anchors for c in cases)  # the anchors-only path is covered in the build


def test_write_bundle_rejects_unknown_stems(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no-such-shot"):
        write_bundle(tmp_path / "bundle", stems=["01-city-main", "no-such-shot"])


# --- checks ---------------------------------------------------------------------------------------
def test_selftest_passes_on_a_bundle(tmp_path: Path) -> None:
    golden = write_bundle(tmp_path / "bundle", stems=SMALL)
    lines: list[str] = []
    assert run_selftest(golden, profile_dir=PROFILE, ui=False, out=lines.append) == 0
    assert not [ln for ln in lines if ln.startswith("FAIL")]
    assert sum(ln.startswith("OK   [golden]") for ln in lines) == len(SMALL)
    assert any("[features] registry: 12" in ln for ln in lines)


def test_selftest_fails_on_a_wrong_expectation(tmp_path: Path) -> None:
    golden = write_bundle(tmp_path / "bundle", stems=SMALL[:1])
    data = yaml.safe_load(golden.read_text(encoding="utf-8"))
    data["reference"][0]["scene"] = "tower.dialog"
    data["reference"][0]["canvas"] = [0, 0]
    golden.write_text(yaml.safe_dump(data), encoding="utf-8")
    lines: list[str] = []
    assert run_selftest(golden, profile_dir=PROFILE, ui=False, out=lines.append) == 1
    fail = next(ln for ln in lines if ln.startswith("FAIL [golden]"))
    assert "chest.wheel" in fail and "tower.dialog" in fail and "khung" in fail


def test_selftest_fails_without_golden_file(tmp_path: Path) -> None:
    lines: list[str] = []
    assert run_selftest(tmp_path / "missing.yaml", profile_dir=PROFILE, ui=False, out=lines.append) == 1
    assert any(ln.startswith("FAIL [golden] expected.yaml") for ln in lines)


def test_profile_check_reports_a_missing_template(tmp_path: Path) -> None:
    profile = tmp_path / "ngoalongviet"
    shutil.copytree(PROFILE, profile)
    (profile / "templates" / "tower" / "confirm" / "ok.png").unlink()
    results = {r.name: r for r in selftest.check_profile(profile)}
    assert results["coords"].ok and results["scenes"].ok and results["locator"].ok
    assert not results["templates"].ok and "tower.confirm.ok" in results["templates"].detail


def test_profile_check_reports_a_missing_feature_template(tmp_path: Path) -> None:
    profile = tmp_path / "ngoalongviet"
    shutil.copytree(PROFILE, profile)
    (profile / "templates" / "tri_ky" / "cam" / "0.png").unlink()
    results = {r.name: r for r in selftest.check_profile(profile)}
    assert not results["templates"].ok and "tri_ky.cam.0" in results["templates"].detail


def test_features_declare_their_direct_templates() -> None:
    from ngoalong.features.tri_ky import TriKy
    from ngoalong.features.vo_khoi_thap import VoKhoiThap

    assert len(TriKy.required_templates()) == 19 + 21
    assert "vo_khoi_thap.shop.chest_1ty_2" in VoKhoiThap.required_templates()
    [ok] = [r for r in selftest.check_profile(PROFILE) if r.name == "templates"]
    assert ok.ok, ok.detail


def test_main_reports_a_broken_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                       caplog: pytest.LogCaptureFixture, qapp) -> None:
    from ngoalong import app

    profiles = tmp_path / "profiles"
    shutil.copytree(PROFILE, profiles / "ngoalongviet")
    (profiles / "ngoalongviet" / "locator.yaml").write_text("anchors: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(app, "PROFILES_DIR", profiles)
    shown: list[str] = []
    monkeypatch.setattr(app, "_show_startup_error", shown.append)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # main() installs its own: restore it
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    with caplog.at_level("ERROR", logger="ngoalong.app"):
        code = app.main(["--settings", str(tmp_path / "settings.toml"), "--ini", str(tmp_path / "none.ini")])
    assert code == 1
    assert any(r.message == "startup failed" and r.exc_info for r in caplog.records)
    assert len(shown) == 1
    assert str(profiles / "ngoalongviet") in shown[0]
    assert "app.log" in shown[0] and "--selftest" in shown[0]


def test_profile_check_reports_a_missing_folder(tmp_path: Path) -> None:
    results = selftest.check_profile(tmp_path / "nothing")
    assert results == [CheckResult("profile", "folder", False, str(tmp_path / "nothing"))]


def test_ui_check_builds_the_main_window(qapp) -> None:
    [result] = selftest.check_ui(PROFILE)
    assert result.ok, result.detail
    assert "12 biểu mẫu" in result.detail


def test_main_flag_writes_the_report(tmp_path: Path) -> None:
    from ngoalong.app import main

    golden = write_bundle(tmp_path / "bundle", stems=SMALL[:1])
    report = tmp_path / "out" / "report.txt"
    code = main(["--selftest", "--no-ui", "--golden", str(golden), "--report", str(report)])
    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "OK   [golden] chest-rolling-ui" in text and "0 lỗi" in text


def test_main_flag_exits_non_zero_on_failure(tmp_path: Path) -> None:
    from ngoalong.app import main

    report = tmp_path / "report.txt"
    assert main(["--selftest", "--no-ui", "--golden", str(tmp_path / "x.yaml"), "--report", str(report)]) == 1
    assert "FAIL" in report.read_text(encoding="utf-8")


# --- build files ----------------------------------------------------------------------------------
def test_spec_builds_one_folder_with_editable_profiles() -> None:
    spec = (ROOT / "AutoNgoaLong.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("ngoalong")' in spec  # pkgutil-discovered features and tabs
    assert "exclude_binaries=True" in spec and "COLLECT(" in spec  # one-folder
    assert 'ROOT / "profiles", profiles_dst' in spec  # profiles/ next to the exe
    assert "write_bundle(" in spec and '"selftest"' in spec


def test_release_docs_are_indexed() -> None:
    guide = (ROOT / "docs" / "user-guide-vi.md").read_text(encoding="utf-8")
    for heading in ("Cài đặt", "Lần chạy đầu tiên", "Nhận cửa sổ game", "Chế độ gỡ lỗi",
                    "Chuỗi tác vụ", "Xử lý sự cố"):
        assert heading in guide
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "user-guide-vi.md" in index and "../README.md" in index
    assert "## Implementation status" in (ROOT / "docs" / "06-rewrite-design.md").read_text(encoding="utf-8")
    assert "--selftest" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_build_script_runs_the_selftest() -> None:
    script = (ROOT / "build.ps1").read_text(encoding="utf-8")
    assert "AutoNgoaLong.spec" in script and "--selftest" in script
    assert "ExitCode" in script and "LASTEXITCODE" in script
    assert "WaitForExit($SelftestTimeoutSec" in script and "-Wait " not in script  # bounded wait
    assert script.isascii()  # Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page
