"""Chạy Thành Tựu, offline: OCR helpers, reading the T.Tựu tab and army dialogs with the real OCR
engine (RapidOCR) on the user's screenshots (tests/fixtures/screens/thanh_tuu, stored at canvas size
1000x600 except the two army dialog crops), portrait matching, squad detection on a live dungeon
capture, the achievement store, the start guard and the "Phó bản" picker widget.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.coords import CoordMap
from ngoalong.core.geometry import Point, Rect
from ngoalong.core.ocr import (
    MATCH, OcrLine, OcrWord, army_name, fraction, normalize, prefix_similarity, similarity,
)
from ngoalong.engine import registry
from ngoalong.features import clear_map as cm
from ngoalong.features import thanh_tuu as tt

FIX = ROOT / "tests" / "fixtures" / "screens" / "thanh_tuu"
LIVE = ROOT / "docs" / "game screenshots" / "live"
COORDS = CoordMap.load(PROFILE / "coords")
MAPS = ["Khăn Vàng", "Đổng Trác", "Viên Thuật", "Công Tôn", "Lưu Chương", "Viên Thiệu", "Mạnh Hoạch",
        "Hàn Huyền", "Tây Khương"]


def c(key: str):
    return COORDS.raw(f"thanh_tuu.{key}")


@lru_cache(maxsize=None)
def ocr():
    return __import__("ngoalong.core.ocr", fromlist=["RapidOcr"]).RapidOcr()


@lru_cache(maxsize=None)
def img(name: str) -> np.ndarray:
    out = cv2.imread(str(FIX / name))
    assert out is not None, name
    return out


def read(image: np.ndarray, key: str) -> list[OcrLine]:
    r = Rect(*c(key))
    return [ln.moved(r.x, r.y) for ln in ocr().read(image[r.y:r.bottom, r.x:r.right])]


def line(text: str, x: int, y: int, w: int = 60, h: int = 14) -> OcrLine:
    r = Rect(x, y, w, h)
    return OcrLine(text, r, (OcrWord(text, r),))


# --- OCR helpers ----------------------------------------------------------------------------------
def test_normalize_drops_marks_and_symbols() -> None:
    assert normalize("Đoàn Viên Hạnh Phúc 1") == "doan vien hanh phuc 1"
    assert normalize("  Khǎn   Vàng! ") == "khan vang"
    assert normalize("Số lượng 48/50") == "so luong 48/50"


def test_names_match_through_ocr_dropouts() -> None:
    # what RapidOCR read on the screenshots vs the real names
    assert similarity("Đng Trác", "Đổng Trác") >= tt.NAME_MATCH
    assert similarity("Luru Chuong", "Lưu Chương") >= tt.NAME_MATCH
    assert similarity("Đng Trác", "Công Tôn") < 0.5
    assert similarity("Hàn Huyềr", "Hàn Huyền") >= MATCH  # user report: one letter misread
    assert similarity("Viên Thuật", "Viên Thiệu") < MATCH  # two different dungeons
    assert prefix_similarity("Đoan viên", "Đoàn Viên Vui Vẻ 2") >= tt.CARD_NAME_MATCH


def test_fraction_and_army_name() -> None:
    assert fraction("Số lượng 48/50") == (48, 50)
    assert fraction("0 / 20") == (0, 20)
    assert fraction("Chiến công 48") is None
    assert army_name("Đoàn Viên Vui Vě 2 (Đi-Tinh) Lv10") == "doan vien vui ve 2"


@pytest.mark.parametrize(("raw", "name"), [
    ("Đoàn Viên Hạnh Phúc 1S", "Đoàn Viên Hạnh Phúc 1"),
    ("Đoàn Viên Hnh Phúc S", "Đoàn Viên Hnh Phúc"),
    ("Đoàn Viên Hạnh Phúc ES", "Đoàn Viên Hạnh Phúc"),
    ("v Đoàn Viên Vui Vẻ 2S", "Đoàn Viên Vui Vẻ 2"),
    ("Đng", "Đng"),
])
def test_clean_rank_name(raw: str, name: str) -> None:
    assert tt.clean_rank_name(raw) == name


def test_parse_cards_splits_names_joined_across_cards() -> None:
    lines = [line("5Diêm", 375, 401, 42), line("5Diêm", 442, 403, 40),
             line("Đnc Đoan viên", 399, 445, 107), line("1/30", 393, 468, 27), line("12/30", 459, 468, 28)]
    cards = tt.parse_cards(lines, 369, 70, 66, 4)
    assert [(k.index, k.name, k.have, k.need) for k in cards] == [(0, "Đnc", 1, 30), (1, "Đoan viên", 12, 30)]


def test_parse_army_finds_count_and_tien_cong() -> None:
    lines = [line("董卓 (Đi-Tinh) Lv40", 120, 10, 150), line("Tn công", 250, 135), line("s lưng", 40, 158),
             line("0/20", 133, 160, 30), line("Tin công", 250, 170), line("Chin lurc", 250, 205)]
    title, count, tien = tt.parse_army(lines)
    assert title.startswith("董卓") and count == (0, 20)
    assert tien == Rect(250, 170, 60, 14).center


# --- real OCR on the user's screenshots -----------------------------------------------------------
def test_reads_the_thanh_tuu_tab() -> None:
    tab = img("tab-thanh-tuu-canvas.png")
    rows = [ln.text for ln in read(tab, "map_list")]
    assert len(rows) == 9
    for got, want in zip(rows, MAPS, strict=True):
        assert similarity(got, want) >= 0.6, (got, want)
    assert tt.parse_progress(read(tab, "progress")) == 100
    ranks = tt.parse_rank_lines(read(tab, "rank_tasks"))
    assert len(ranks) == 4
    cx, half, ratio = c("rank_checkbox_x"), c("rank_checkbox_half"), c("rank_checked_ratio")
    assert all(tt.is_checked(tab, Point(cx, ln.rect.center.y), half, ratio) for _n, ln in ranks)
    assert similarity(ranks[0][0], "Đoàn Viên Hạnh Phúc 1") >= tt.NAME_MATCH
    cards = tt.parse_cards(read(tab, "challenge_cards"), c("card_first_x"), c("card_pitch"),
                           c("card_width"), c("max_cards"))
    assert [(k.index, k.have, k.need) for k in cards] == [(0, 1, 30), (1, 1, 30)]
    assert tt.number(" ".join(ln.text for ln in read(tab, "score"))) == 44


def test_unticked_box_is_not_checked() -> None:
    tab = img("tab-thanh-tuu-canvas.png").copy()
    cx, half = c("rank_checkbox_x"), c("rank_checkbox_half")
    y = tt.parse_rank_lines(read(tab, "rank_tasks"))[0][1].rect.center.y
    tab[y - half:y + half + 1, cx - half:cx + half + 1] = (5, 5, 5)  # an empty (black) box
    assert not tt.is_checked(tab, Point(cx, y), half, c("rank_checked_ratio"))


@pytest.mark.parametrize(("name", "title", "count", "tien"), [
    ("army-dialog-count-48.png", "doan vien vui ve 2", (48, 50), False),
    ("army-dialog-count-0-tien-cong.png", "", (0, 20), True),  # 董卓: no Latin name
])
def test_reads_army_dialogs(name: str, title: str, count: tuple[int, int], tien: bool) -> None:
    t, n, button = tt.parse_army(ocr().read(img(name)))
    if title:
        assert similarity(army_name(t), title) >= tt.NAME_MATCH
    assert n == count
    assert (button is not None) == tien


def test_card_portraits_pick_the_right_army() -> None:
    tab = img("tab-thanh-tuu-canvas.png")
    px, py, pw, ph = c("card_portrait")
    y0 = c("challenge_cards")[1]

    def card(k: int) -> np.ndarray:
        x = c("card_first_x") + k * c("card_pitch") + px
        return tab[y0 + py:y0 + py + ph, x:x + pw]

    dong_trac, doan_vien = img("army-dialog-count-0-tien-cong.png"), img("army-dialog-count-48.png")
    assert tt.portrait_score(card(0), dong_trac) >= tt.PORTRAIT_MATCH > tt.portrait_score(card(0), doan_vien)
    assert tt.portrait_score(card(1), doan_vien) >= tt.PORTRAIT_MATCH > tt.portrait_score(card(1), dong_trac)


def test_reads_the_dungeon_name_bar() -> None:
    field = cv2.imread(str(LIVE / "08-dungeon-map-cong-ton.png"))[110:710, 180:1180]
    assert similarity(" ".join(ln.text for ln in read(field, "dungeon_name")), "Công Tôn") >= tt.NAME_MATCH


# --- squads on the dungeon map --------------------------------------------------------------------
def test_finds_squads_on_a_live_dungeon_map() -> None:
    field = cv2.imread(str(LIVE / "08-dungeon-map-cong-ton.png"))[110:710, 180:1180]
    regions = [Rect(*r) for r in COORDS.raw(cm.REGIONS)]
    flag = cv2.imread(str(PROFILE / "templates" / "field" / "enemy_flag.png"))
    pts = tt.find_armies(field, regions, flag, tuple(c("army_click_offset_from_tent")),
                         COORDS.offset(cm.ENEMY_OFF))
    assert len(pts) >= 20  # 08 shows about 30 squads, a few under the HUD and the quest list
    assert all(any(r.contains(p) for r in regions) for p in pts)
    for a in pts:  # no two clicks on the same squad
        assert all(a == b or abs(a.x - b.x) > tt.ARMY_DEDUPE_PX or abs(a.y - b.y) > tt.ARMY_DEDUPE_PX for b in pts)


# --- data, start guard ----------------------------------------------------------------------------
def _state() -> tt.AchState:
    rank = tt.AchTask(kind=tt.Kind.RANK, name="Đoàn Viên Hạnh Phúc 1", done=True)
    card = tt.AchTask(kind=tt.Kind.CHALLENGE, name="Đoàn Viên", done=False, have=1, need=30)
    return tt.AchState(synced_at=1.0, score=44, maps=[
        tt.AchMap(name="Khăn Vàng", progress=100, tasks=[rank]),
        tt.AchMap(name="Đổng Trác", progress=40, tasks=[rank, card]),
    ])


def test_store_round_trip_and_fuzzy_find(tmp_path: Path) -> None:
    store = tt.AchievementStore(tmp_path / "data" / "thanh_tuu.json")
    assert store.load().synced_at is None
    store.save(_state())
    again = tt.AchievementStore(store.path).load()
    assert [m.name for m in again.maps] == ["Khăn Vàng", "Đổng Trác"]
    assert again.find("Đng Trác").name == "Đổng Trác"  # type: ignore[union-attr]
    assert again.find("Tây Khương") is None
    m = again.find("Đổng Trác")
    assert m is not None and not m.done and m.pending()[0].kind is tt.Kind.CHALLENGE
    assert again.maps[0].done


def test_start_blocker_messages() -> None:
    store = tt.AchievementStore(None)
    p = tt.ThanhTuuParams
    assert "Đồng bộ" in (tt.start_blocker(p(map_name="Khăn Vàng"), store) or "")
    store.save(_state())
    assert "Chọn" in (tt.start_blocker(p(), store) or "")
    assert "hoàn thành" in (tt.start_blocker(p(map_name="Khăn Vàng"), store) or "")
    assert "Đồng bộ" in (tt.start_blocker(p(map_name="Tây Khương"), store) or "")
    assert tt.start_blocker(p(map_name="Đổng Trác"), store) is None
    assert tt.ChayThanhTuu.start_blocker(p(map_name="Đổng Trác"), SimpleNamespace(thanh_tuu_store=store)) is None


def test_feature_is_registered_with_a_sync_mode() -> None:
    registry.discover()
    cls = registry.get_feature("thanh_tuu")
    assert cls is tt.ChayThanhTuu and cls.title == "Chạy Thành Tựu"
    store = tt.AchievementStore(None)
    task = cls(store=store, mode="sync")
    assert task.mode == "sync" and task.title == "Đồng bộ Thành Tựu" and cls.title == "Chạy Thành Tựu"
    assert task.bind_services(SimpleNamespace(thanh_tuu_store=store)).store is store
    with pytest.raises(ValueError):
        cls(mode="fight")


# --- the "Phó bản" picker -------------------------------------------------------------------------
def test_picker_locks_the_form_until_synced(qapp) -> None:
    from ngoalong.ui.forms import ParamsForm

    store = tt.AchievementStore(None)
    form = ParamsForm(tt.ThanhTuuParams, tt.ThanhTuuParams(), SimpleNamespace(thanh_tuu_store=store))
    picker = form.editors["map_name"]
    others = [ed for name, ed in form.editors.items() if name != "map_name"]
    assert not picker.combo.isEnabled() and all(not ed.widget.isEnabled() for ed in others)
    assert "Chưa đồng bộ" in picker.status.text()

    store.save(_state())
    picker.refresh()
    assert picker.combo.isEnabled() and all(ed.widget.isEnabled() for ed in others)
    items = [picker.combo.itemText(i) for i in range(picker.combo.count())]
    assert items[0].startswith("✅ Khăn Vàng") and items[1].startswith("❌ Đổng Trác")
    assert picker.get() == "Đổng Trác"  # the first unfinished dungeon is preselected
    picker.combo.setCurrentIndex(0)
    assert form.value().map_name == "Khăn Vàng"
    assert "2 phó bản, 1 đã xong, điểm 44" in picker.status.text()


def test_feature_page_disables_start_with_a_reason(qapp, tmp_path: Path) -> None:
    from test_ui import CHEST, FAKE_WINDOW

    from ngoalong.app import AppServices
    from ngoalong.core.drivers.fake import RecordingInputDriver
    from ngoalong.core.drivers.file import FileCapturer
    from ngoalong.ui.main_window import MainWindow

    s = AppServices(
        settings_path=tmp_path / "settings.toml", profile_dir=PROFILE, discover=lambda: [FAKE_WINDOW],
        capturer_factory=lambda gw: FileCapturer([CHEST], loop=True),
        input_factory=lambda gw: RecordingInputDriver(), log_dir=tmp_path / "logs", rebind_windows=False,
    )
    w = MainWindow(s, overlay=False)
    try:
        assert s.thanh_tuu_store.path == tmp_path / "data" / "thanh_tuu.json"  # not the app's data/
        page = w.feature_page
        page.select_feature("thanh_tuu")
        label = page.blockers["thanh_tuu"]
        assert not page.start_btn.isEnabled() and "Đồng bộ" in label.text()
        assert not page.start_selected()  # refused, with the same reason

        s.thanh_tuu_store.save(_state())
        picker = page.forms["thanh_tuu"].editors["map_name"]
        picker.refresh()  # what a finished sync run triggers
        assert picker.get() == "Đổng Trác" and page.start_btn.isEnabled() and label.isHidden()
        picker.combo.setCurrentIndex(0)  # Khăn Vàng: done
        assert not page.start_btn.isEnabled() and "hoàn thành" in label.text()
        page.select_feature("autoclick")
        assert page.start_btn.isEnabled()  # other features are not affected
    finally:
        w.close()
        s.shutdown()
