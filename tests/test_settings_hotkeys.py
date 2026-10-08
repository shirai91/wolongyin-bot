"""Settings (TOML round trip, one-time INI import) and hotkeys (parsing, registration)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ngoalong.config.settings import (
    Settings, load_or_import, load_settings, read_ini_section, save_settings,
)
from ngoalong.engine.task import Task, TaskParams
from ngoalong.features.autoclick import AutoClick
from ngoalong.features.quay_ruong import QuayRuong
from ngoalong.hotkeys import (
    MOD_ALT, MOD_CONTROL, HotkeyError, HotkeyManager, normalize_hotkey, parse_hotkey,
)

INI_TEXT = """[PRO5]
TOADO_GOC=17x82
TITLE_ACTIVE=Auto Game Ngọa Long Free v1.8.0
QR_TOCDO=5
QR_SOLUONG=200
CAPDO=10
ACP_INPUT_S=0
ACP_INPUT_MS=100
ACP_LABEL_X=504
ACP_LABEL_Y=416
ACP_LIST_TOADO=492x493|
HWND_GAME=0x0033099C
BOSS_ID=3. Bệ Ngạn ( lv 140 )
"""


# --- hotkeys ------------------------------------------------------------------------------------
def test_parse_hotkeys() -> None:
    hk = parse_hotkey("ctrl+alt+f10")
    assert hk.modifiers == MOD_CONTROL | MOD_ALT and hk.vk == 0x79 and hk.text == "Ctrl+Alt+F10"
    assert parse_hotkey("Alt + Ctrl + d").text == "Ctrl+Alt+D"
    assert parse_hotkey("Win+Shift+Pause").text == "Shift+Win+Pause"
    assert normalize_hotkey("") == ""


@pytest.mark.parametrize("bad", ["F10", "Shift+F1", "Ctrl+Alt", "Ctrl+A+B", "Ctrl++", "Ctrl+F25", "Ctrl+Ă"])
def test_reject_bad_or_bare_hotkeys(bad: str) -> None:
    with pytest.raises(HotkeyError):
        parse_hotkey(bad)


def test_hotkey_manager_reports_syntax_errors_without_registering() -> None:
    m = HotkeyManager()
    errors = m.set_bindings({"stop": ("F10", lambda: None), "none": ("", lambda: None)})
    assert len(errors) == 1 and "stop" in errors[0]
    assert not m.running


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Win32")
def test_hotkey_manager_registers_and_stops() -> None:
    m = HotkeyManager()
    errors = m.set_bindings({"test": ("Ctrl+Alt+Shift+F24", lambda: None)})
    try:
        if errors:
            pytest.skip(f"combination in use on this machine: {errors}")
        assert m.running and m.registered == ["test"]
    finally:
        m.stop()
    assert not m.running and m.registered == []


# --- settings -----------------------------------------------------------------------------------
def test_settings_defaults_and_round_trip(tmp_path: Path) -> None:
    s = Settings()
    assert s.debug is False and s.hotkeys.stop == "Ctrl+Alt+F10" and s.hotkeys.debug_toggle == "Ctrl+Alt+D"
    s.debug = True
    s.hotkeys.pause = "ctrl+alt+p"
    s.set_feature_params("autoclick", AutoClick.Params(points=[{"x": 1, "y": 2}], interval_ms=50))
    path = save_settings(s, tmp_path / "settings.toml")
    text = path.read_text(encoding="utf-8")
    assert "debug = true" in text and "Ctrl+Alt+P" in text
    s2 = load_settings(path)
    assert s2.debug and s2.hotkeys.pause == "Ctrl+Alt+P"
    p = s2.feature_params(AutoClick)
    assert p.points[0].x == 1 and p.interval_ms == 50


def test_invalid_hotkey_in_settings_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(hotkeys={"stop": "F10"})


def test_broken_settings_file_is_backed_up(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("this is = = not toml", encoding="utf-8")
    s = load_settings(path)
    assert s == Settings()
    assert (tmp_path / "settings.toml.bak").is_file() and not path.exists()


def test_invalid_feature_params_fall_back_to_defaults() -> None:
    s = Settings(features={"quay_ruong": {"spins": -5}})
    assert s.feature_params(QuayRuong) == QuayRuong.Params()


def test_read_ini_section_utf8_and_ansi(tmp_path: Path) -> None:
    p = tmp_path / "a.ini"
    p.write_text(INI_TEXT, encoding="utf-8")
    sec = read_ini_section(p)
    assert sec["BOSS_ID"] == "3. Bệ Ngạn ( lv 140 )" and sec["ACP_LIST_TOADO"] == "492x493|"
    p.write_bytes("[PRO5]\nBOSS_ID=3. B\xea\n".encode("latin-1"))
    assert read_ini_section(p)["BOSS_ID"].startswith("3. B")


def test_one_time_ini_import(tmp_path: Path) -> None:
    ini = tmp_path / "NgoaLong_data.ini"
    ini.write_text(INI_TEXT, encoding="utf-8")
    path = tmp_path / "settings.toml"
    s, imported = load_or_import(path, ini, [AutoClick, QuayRuong])
    assert imported and path.is_file()
    assert s.feature_params(QuayRuong).spins == 200 and s.feature_params(QuayRuong).delay_ms == 5
    ac = s.feature_params(AutoClick)
    assert [(p.x, p.y) for p in ac.points] == [(475, 411)] and ac.interval_ms == 100
    assert "HWND_GAME" not in s.legacy_ini and "TITLE_ACTIVE" not in s.legacy_ini  # #5, #6
    assert s.legacy_ini["CAPDO"] == "10"  # kept for features of later phases

    # Second start: settings.toml exists, so the INI is not imported again.
    ini.write_text(INI_TEXT.replace("QR_SOLUONG=200", "QR_SOLUONG=7"), encoding="utf-8")
    s2, imported2 = load_or_import(path, ini, [AutoClick, QuayRuong])
    assert not imported2 and s2.feature_params(QuayRuong).spins == 200


def test_later_features_import_their_legacy_keys_once() -> None:
    class LaterParams(TaskParams):
        level: int = 1

    class Later(Task):
        id = "later_feature"
        title = "Later"
        Params = LaterParams

        def run(self, ctx) -> None:
            return None

        @classmethod
        def import_legacy(cls, ini):
            return {"level": int(ini["CAPDO"])} if "CAPDO" in ini else {}

    s = Settings(legacy_ini={"CAPDO": "10"})
    assert s.feature_params(Later).level == 10
    assert "later_feature" in s.legacy_applied
    s.features = {}
    assert s.feature_params(Later).level == 1  # applied once only; user edits win afterwards


def test_no_ini_means_defaults(tmp_path: Path) -> None:
    s, imported = load_or_import(tmp_path / "settings.toml", tmp_path / "missing.ini")
    assert not imported and s == Settings()


def test_overlay_refresh_interval_is_clamped(tmp_path: Path) -> None:
    from ngoalong.config.settings import OVERLAY_REFRESH_MAX_MS, OVERLAY_REFRESH_MIN_MS

    s = Settings()
    assert OVERLAY_REFRESH_MIN_MS <= s.overlay_refresh_ms <= OVERLAY_REFRESH_MAX_MS
    s.overlay_refresh_ms = 1
    assert s.overlay_refresh_ms == OVERLAY_REFRESH_MIN_MS
    assert Settings.model_validate({"overlay_refresh_ms": 10**9}).overlay_refresh_ms == OVERLAY_REFRESH_MAX_MS
    s.overlay_refresh_ms = 1200
    path = save_settings(s, tmp_path / "settings.toml")
    assert load_settings(path).overlay_refresh_ms == 1200
