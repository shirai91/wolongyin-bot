"""P2 Võ Khôi Tháp (BOSS_VOKHOITHAP, BOSS_CHECKHETLENH, BOSS_QUAYRUONG, BOSS_CHECKFULLBAC).

Offline: a small simulator replays live screenshots (city, world map, Tinh Tú, Trang bị/Cửa hiệu,
city info) through ``FileCapturer``, with the legacy templates pasted where a state needs them
(out of tokens, full silver, purchase check), and moves between states on the clicks and keys the
fake input driver records. Click positions are compared, in canvas coordinates, with a
transliteration of the AutoIt functions (v1.8.0 ``app_logic_only.au3``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from conftest import PROFILE, ROOT
from ngoalong.core.capture import read_image
from ngoalong.core.coords import CoordMap
from ngoalong.core.drivers.fake import InputEvent, RecordingInputDriver
from ngoalong.core.drivers.file import FileCapturer
from ngoalong.core.input import VK_ESCAPE, VK_RETURN
from ngoalong.core.locator import CanvasLocator
from ngoalong.core.scenes import SceneDetector
from ngoalong.core.templates import TemplateStore
from ngoalong.core.vision import Matcher
from ngoalong.debug.sink import PngSink
from ngoalong.engine import registry
from ngoalong.engine.context import RunContext
from ngoalong.engine.control import RunControl
from ngoalong.engine.runner import RunStatus, execute
from ngoalong.features import vo_khoi_thap as vkt
from ngoalong.features.vo_khoi_thap import (
    Boss, FullSilverPolicy, VoKhoiThap, VoKhoiThapParams,
)

SHOTS = ROOT / "docs" / "game screenshots"
LIVE = SHOTS / "live"
LIVE_ORIGIN = (180, 110)  # canvas origin of every live capture (template_crops.yaml)
STORE = TemplateStore(PROFILE / "templates")
COORDS = CoordMap.load(PROFILE / "coords")
SCENES = SceneDetector.load(PROFILE / "scenes", templates_dir=PROFILE / "templates")
LOCATOR = CanvasLocator.from_profile(PROFILE)

TOKENS_AT = (480, 570)  # where the simulator shows the out-of-tokens marker
SILVER_AT = (339, 250)  # a small confirm box corner (as on the tower confirm box)
BUY_CHECK_AT = (420, 300)
CHEST_ICON = (735, 108)  # v1.8.0 clicked the template's top-left corner (Tinh Tú HUD icon)
SHOP_CHEST = (203, 269, 56, 38)  # the "1 tỷ bạc" chest on 41-trang-bi.png (canvas x, y, w, h)
SHOP_CHEST_2 = (595, 213)  # a second one on the same screenshot


@pytest.fixture(autouse=True)
def _fast(monkeypatch) -> None:
    monkeypatch.setattr(vkt, "POPUP_GAP_MS", 0)
    monkeypatch.setattr(vkt, "SPIN_GAP_MS", 0)
    monkeypatch.setattr(vkt, "SHOP_TAB_WAIT_S", 0.3)
    monkeypatch.setattr(vkt, "SHOP_PAGE_WAIT_S", 0.05)
    monkeypatch.setattr(vkt, "BUY_CHECK_WAIT_S", 0.2)


# --- frames -------------------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _img(path: Path) -> np.ndarray:
    return read_image(path)


def _paste(base: np.ndarray, at: tuple[int, int], patch: np.ndarray) -> np.ndarray:
    out = base.copy()
    x, y = LIVE_ORIGIN[0] + at[0], LIVE_ORIGIN[1] + at[1]
    out[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
    return out


@lru_cache(maxsize=None)
def frames() -> dict[str, np.ndarray]:
    city = _img(LIVE / "01-city-main.png")
    world = _img(LIVE / "02-world-map.png")
    wheel = _img(LIVE / "19-tinh-tu.png")
    x, y, w, h = SHOP_CHEST
    noise = np.random.default_rng(3).integers(0, 255, (h, w, 3), dtype=np.uint8)
    # 41-trang-bi.png shows the chest twice; keep one so the click target is unambiguous
    shop = _paste(_img(LIVE / "41-trang-bi.png"), SHOP_CHEST_2, noise)
    silver = STORE.get(vkt.FULL_SILVER).image
    return {
        "city": city,
        "map": world,
        "boss": _img(LIVE / "44-vo-khoi-thap.png"),
        "fight": world,  # the screens after "Tấn công" are not catalogued (docs/07)
        "map_tokens": _paste(world, TOKENS_AT, STORE.get(vkt.OUT_OF_TOKENS).image),
        "map_silver": _paste(world, SILVER_AT, silver),
        "city_info": _img(LIVE / "29-world-city-click.png"),
        "wheel": wheel,
        "wheel_silver": _paste(wheel, SILVER_AT, silver),
        "buy_check": _paste(wheel, BUY_CHECK_AT, STORE.get(vkt.BUY_CHECK).image),
        "shop": shop,
        "shop_empty": _paste(shop, (x, y), noise),
        "tavern": _img(LIVE / "17-tuu-quan-tri-ky.png"),
    }


def _near(p: tuple[float, float], c: tuple[int, int], d: int = 3) -> bool:
    return abs(p[0] - c[0]) <= d and abs(p[1] - c[1]) <= d


# --- simulator ----------------------------------------------------------------------------------
class VktSim:
    """Capturer + input callback. The marker opens the boss screen (live capture); the screens after
    "Tấn công" are unknown (docs/07) and replayed with the world map until "Nhận thưởng"."""

    def __init__(
        self,
        start: str = "city",
        tokens_after: set[int] | None = None,
        silver_after: set[int] | None = None,
        silver_on_wheel: bool = False,
        buy_check: bool = True,
        chest_page: int | None = 2,
        esc_ignored: int = 0,
        entry_opens_city_info: bool = False,
        entry_dead: bool = False,
        stop_after_clicks: int | None = None,
    ) -> None:
        self.entry_dead = entry_dead
        self.state = start
        self.caps = {k: FileCapturer([v], loop=True) for k, v in frames().items()}
        self.tokens_after = tokens_after or set()
        self.silver_after = silver_after or set()
        self.silver_on_wheel = silver_on_wheel
        self.buy_check = buy_check
        self.chest_page = chest_page
        self.esc_ignored = esc_ignored
        self.entry_opens_city_info = entry_opens_city_info
        self.stop_after_clicks = stop_after_clicks
        self.rounds = 0
        self.page = 0
        self.dismiss = 0
        self.ctx: RunContext | None = None
        self.log: list[tuple[str, tuple[float, float], bool]] = []
        self.keys: list[tuple[str, int]] = []
        self.texts: list[str] = []

    def grab(self) -> np.ndarray:
        return self.caps[self.state].grab()

    def client_size(self) -> tuple[int, int]:
        return self.caps[self.state].client_size()

    def on_event(self, ev: InputEvent) -> None:
        if ev.kind == "text":
            self.texts.append(ev.text or "")
            return
        if ev.kind == "key":
            self._key(ev.vk or 0)
            return
        assert self.ctx is not None and self.ctx.last_canvas is not None
        p = self.ctx.last_canvas.to_canvas(ev.point)
        s = self.state
        self.log.append((s, p, ev.double))
        if self.stop_after_clicks is not None and len(self.log) >= self.stop_after_clicks:
            self.ctx.control.stop()
        if s == "city" and _near(p, (950, 490)):
            self.state = "map"
        elif s.startswith("map") and _near(p, (235, 330)) and self.entry_opens_city_info:
            self.state = "city_info"
        elif s == "map" and _near(p, (235, 330)) and not self.entry_dead:
            self.state = "boss"
        elif s == "boss" and _near(p, (495, 575)):  # "Thoát"
            self.state = "map"
        elif s == "boss" and _near(p, (475, 490)):  # "Tấn công"
            self.state = "fight"
        elif s == "fight" and _near(p, (495, 425)):  # "Nhận thưởng": a round ends
            self.state = "map"
            self.rounds += 1
            if self.rounds in self.tokens_after:
                self.state = "map_tokens"
            elif self.rounds in self.silver_after:
                self.state = "map_silver"
        elif s == "map_tokens" and _near(p, (738, 112), 5):  # the Tinh Tú HUD icon
            self.state = "wheel_silver" if self.silver_on_wheel else "wheel"
        elif s == "wheel" and _near(p, (432, 365)) and self.buy_check:
            self.state = "buy_check"
        elif s == "buy_check" and _near(p, (435, 314)):
            self.state = "wheel"
        elif s in ("map_silver", "wheel_silver") and _near(p, (520, 328)):
            self.dismiss += 1
            if self.dismiss % 2 == 0:
                self.state = "map" if s == "map_silver" else "wheel"
        elif s == "shop_empty" and _near(p, (652, 427)):
            self.page += 1
            if self.chest_page is not None and self.page >= self.chest_page:
                self.state = "shop"

    def _key(self, vk: int) -> None:
        s = self.state
        self.keys.append((s, vk))
        if vk == VK_ESCAPE and s in ("wheel", "wheel_silver"):
            if self.esc_ignored:
                self.esc_ignored -= 1
            else:
                self.state = "map"
        elif vk == VK_ESCAPE and s in ("shop", "shop_empty"):
            self.state = "map"
        elif vk == VK_RETURN and s in ("map_silver", "wheel_silver"):
            self.state = "shop" if self.chest_page == 0 else "shop_empty"

    def clicks(self, state: str | None = None) -> list[tuple[float, float]]:
        return [p for s, p, _d in self.log if state is None or s == state]


def make_ctx(sim: VktSim, png_dir: Path | None = None) -> RunContext:
    drv = RecordingInputDriver(on_event=sim.on_event)
    ctx = RunContext(
        capturer=sim, input_driver=drv, coords=COORDS, matcher=Matcher(STORE, history=64),
        canvas_source=CanvasLocator.from_profile(PROFILE), scenes=SCENES, control=RunControl(),
        png_sink=PngSink(png_dir) if png_dir is not None else None, poll_interval_s=0.02,
    )
    sim.ctx = ctx
    return ctx


def params(**kw) -> VoKhoiThapParams:
    base = dict(delay_ms=0, rounds=2, chest_quantity=3, scene_timeout_s=2.0)
    base.update(kw)
    return VoKhoiThapParams(**base)


# --- v1.8.0 transliteration ---------------------------------------------------------------------
Step = tuple[str, tuple[int, int]]


def v180_nav(boss: int) -> list[Step]:
    seq: list[Step] = [("world_map", (950, 490)), ("vkt_entry", (235, 330))]
    seq += [("boss_prev", (265, 305))] * 7
    seq += [("boss_next", (730, 305))] * (boss - 1)  # For I = 1 To (BOSSID + 4294967295)
    seq.append(("exit", (495, 575)))
    return seq


V180_ROUND: list[Step] = [
    ("vkt_entry", (235, 330)), ("attack", (475, 490)), ("form_team", (800, 504)),
    ("form_team", (800, 504)), ("fight", (245, 350)), ("fight_confirm", (456, 355)),
    ("claim", (495, 425)),
]


def v180_refill(qty: int, buy: bool = True, check: bool = True) -> list[Step]:
    seq: list[Step] = [("open_chest", (490, 583))] * 2 + [("chest_icon", CHEST_ICON)]
    if buy:
        seq += [("buy_tab", (783, 144)), ("quantity_box", (425, 288)), ("buy_confirm", (432, 365))]
        if check:
            seq += [("buy_check_ok", (435, 314)), ("buy_check_ok2", (455, 355))]
    seq += [("spin_a", (200, 420)), ("spin_b", (440, 360)), ("spin_a", (200, 420))] * qty
    return seq


V180_SILVER_SKIP: list[Step] = [("dismiss", (520, 328))] * 2

# The port clicks a template's centre: the Tinh Tú icon piece is 7x9 px (corner + (3, 4)); the
# purchase-check confirm is (456, 355) in coords/vo_khoi_thap.yaml, v1.8.0 typed (455, 355).
TOLERANCE: dict[str, tuple[int, int]] = {"chest_icon": (4, 5), "buy_check_ok2": (1, 0)}


def assert_parity(actual: list[tuple[float, float]], expected: list[Step]) -> None:
    assert len(actual) == len(expected), (
        f"{len(actual)} clicks, expected {len(expected)}: {[n for n, _ in expected]}"
    )
    for k, (p, (name, (ex, ey))) in enumerate(zip(actual, expected, strict=True)):
        dx, dy = TOLERANCE.get(name, (0, 0))
        assert abs(p[0] - ex) <= dx and abs(p[1] - ey) <= dy, f"click {k} ({name}): {p} vs v1.8.0 {(ex, ey)}"


# --- rounds -------------------------------------------------------------------------------------
@pytest.mark.parametrize("boss", [1, 4, 7])
def test_rounds_from_the_city_match_v180(boss: int) -> None:
    sim = VktSim("city")
    ctx = make_ctx(sim)
    progress: list[tuple[int, int | None]] = []
    ctx._on_progress = lambda d, t: progress.append((d, t))
    r = execute(VoKhoiThap(params(boss=boss, rounds=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert_parity(sim.clicks(), v180_nav(boss) + V180_ROUND * 3)
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert "3 lượt" in r.message and Boss(boss).label in r.message
    assert sim.keys == [] and sim.texts == []


def test_starts_on_the_world_map() -> None:
    sim = VktSim("map")
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(boss=2, rounds=1)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert_parity(sim.clicks(), v180_nav(2)[1:] + V180_ROUND)


def test_refuses_another_screen(tmp_path: Path) -> None:
    sim = VktSim("tavern")
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(VoKhoiThap(params()), ctx)
    assert r.status is RunStatus.FAILED and "tavern" in r.message
    assert sim.log == []


def test_city_dialog_on_the_map_stops_the_run(tmp_path: Path) -> None:
    """The v1.8.0 marker is missing on this map (docs/07): a city dialog means the clicks are blind."""
    sim = VktSim("map", entry_opens_city_info=True)
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(VoKhoiThap(params(rounds=5)), ctx)
    assert r.status is RunStatus.FAILED
    assert "vkt_entry" in r.message and r.screenshot is not None
    assert len(sim.clicks()) <= len(v180_nav(2)) + len(V180_ROUND)


def test_marker_that_opens_nothing_fails_within_one_round(tmp_path: Path) -> None:
    """vkt_entry on empty terrain: no blind boss/round clicks on an unchanged world map."""
    sim = VktSim("map", entry_dead=True)
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(VoKhoiThap(params(rounds=0, scene_timeout_s=0.5)), ctx)
    assert r.status is RunStatus.FAILED
    assert "Không mở được Võ Khôi Tháp" in r.message and "vkt_entry" in r.message
    assert r.screenshot is not None
    assert sim.clicks() == [(235, 330)]


def test_boss_screen_scene_and_offsets() -> None:
    """The live boss screen is recognised and v1.8.0's offsets hit its controls (docs/07)."""
    img = frames()["boss"]
    fix = LOCATOR.locate(img)
    assert SCENES.detect(img, fix).name == vkt.VKT_SCREEN
    assert SCENES.detect(frames()["map"], LOCATOR.locate(frames()["map"])).name == vkt.WORLD_MAP
    assert SCENES.find_object(img, fix, vkt.VKT_SCREEN, "exit").found
    assert COORDS.offset("vo_khoi_thap.exit") == (495, 575)
    assert COORDS.offset("vo_khoi_thap.attack") == (475, 490)
    assert COORDS.offset("vo_khoi_thap.boss_prev") == (265, 305)
    assert COORDS.offset("vo_khoi_thap.boss_next") == (730, 305)


def test_stop_ends_an_endless_run() -> None:
    sim = VktSim("map", stop_after_clicks=40)
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=0)), ctx)
    assert r.status is RunStatus.STOPPED
    assert len(sim.clicks()) == 40


# --- out of tokens ------------------------------------------------------------------------------
def test_out_of_tokens_buys_and_spins_like_v180() -> None:
    sim = VktSim("map", tokens_after={1})
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=2, chest_quantity=3)), ctx)
    assert r.status is RunStatus.DONE, r.message
    expected = v180_nav(2)[1:] + V180_ROUND + v180_refill(3) + V180_ROUND
    assert_parity(sim.clicks(), expected)
    # the quantity box is double-clicked and the quantity typed once (#9)
    doubles = [p for _s, p, d in sim.log if d]
    assert doubles == [(425, 288)]
    assert sim.texts == ["3"]
    # the wheel is closed with ESC (it closed at the first press: no blind extra presses)
    assert sim.keys == [("wheel", VK_ESCAPE)]
    assert "mở rương 1 lần" in r.message


def test_out_of_tokens_without_buying() -> None:
    sim = VktSim("map", tokens_after={1})
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=2, buy_chests=False, chest_quantity=2)), ctx)
    assert r.status is RunStatus.DONE, r.message
    expected = v180_nav(2)[1:] + V180_ROUND + v180_refill(2, buy=False) + V180_ROUND
    assert_parity(sim.clicks(), expected)
    assert sim.texts == []


def test_purchase_check_is_optional() -> None:
    sim = VktSim("map", tokens_after={1}, buy_check=False)
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=2, chest_quantity=1)), ctx)
    assert r.status is RunStatus.DONE, r.message
    expected = v180_nav(2)[1:] + V180_ROUND + v180_refill(1, check=False) + V180_ROUND
    assert_parity(sim.clicks(), expected)


def test_out_of_tokens_without_auto_spin_ends_the_run() -> None:
    """v1.8.0 kept attacking with no tokens; the port stops with a summary."""
    sim = VktSim("map", tokens_after={2})
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=10, auto_spin=False)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert "Hết lệnh sau 2 lượt" in r.message
    assert_parity(sim.clicks(), v180_nav(2)[1:] + V180_ROUND * 2)


def test_refill_limit() -> None:
    sim = VktSim("map", tokens_after={1, 3, 5})
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=10, max_refills=2, chest_quantity=1)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert "Hết lệnh sau 5 lượt" in r.message and "mở rương 2 lần" in r.message


def test_refill_that_gives_no_tokens_is_not_repeated(tmp_path: Path) -> None:
    """Out of tokens again right after a refill: stop instead of buying chests every round."""
    sim = VktSim("map", tokens_after={1, 2, 3, 4})
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(VoKhoiThap(params(rounds=0, chest_quantity=3)), ctx)
    assert r.status is RunStatus.FAILED
    assert "quay rương không có tác dụng" in r.message
    assert r.screenshot is not None
    assert sim.texts == ["3"]  # the purchase happened once
    assert sim.rounds == 2


def test_wheel_close_is_bounded_when_esc_is_ignored(tmp_path: Path) -> None:
    """At most 5 ESC presses (v1.8.0's count), then the wheel's ✕; still open: stop with an error."""
    sim = VktSim("map", tokens_after={1}, esc_ignored=99)
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(VoKhoiThap(params(rounds=2, chest_quantity=1)), ctx)
    assert r.status is RunStatus.FAILED and "Tinh Tú" in r.message
    assert [k for _s, k in sim.keys] == [VK_ESCAPE] * vkt.ESC_PRESSES
    assert _near(sim.clicks("wheel")[-1], (860, 98), 0)


def test_wheel_closed_after_a_few_esc_presses() -> None:
    sim = VktSim("map", tokens_after={1}, esc_ignored=2)
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=2, chest_quantity=1)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert [k for _s, k in sim.keys] == [VK_ESCAPE] * 3
    assert sim.clicks()[-7:] == [p for _n, p in V180_ROUND]


def test_missing_tinh_tu_icon_fails(tmp_path: Path) -> None:
    sim = VktSim("map", tokens_after={1})
    ctx = make_ctx(sim, png_dir=tmp_path)
    own = TemplateStore(PROFILE / "templates")
    own.register(vkt.CHEST_ICON, np.random.default_rng(5).integers(0, 255, (9, 7, 3), dtype=np.uint8))
    ctx.matcher = Matcher(own, history=64)
    r = execute(VoKhoiThap(params(rounds=2, scene_timeout_s=0.5)), ctx)
    assert r.status is RunStatus.FAILED and "Tinh Tú" in r.message
    assert r.screenshot is not None


# --- full silver --------------------------------------------------------------------------------
def test_full_silver_skip() -> None:
    sim = VktSim("map", silver_after={1})
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=2, full_silver=FullSilverPolicy.SKIP)), ctx)
    assert r.status is RunStatus.DONE, r.message
    assert_parity(sim.clicks(), v180_nav(2)[1:] + V180_ROUND + V180_SILVER_SKIP + V180_ROUND)
    assert "full bạc 1 lần" in r.message


def test_full_silver_after_the_spins() -> None:
    """BOSS_QUAYRUONG checks for full silver after spinning, before closing the wheel."""
    sim = VktSim("map", tokens_after={1}, silver_on_wheel=True)
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=2, chest_quantity=1, buy_chests=False)), ctx)
    assert r.status is RunStatus.DONE, r.message
    expected = (v180_nav(2)[1:] + V180_ROUND + v180_refill(1, buy=False) + V180_SILVER_SKIP
                + V180_ROUND)
    assert_parity(sim.clicks(), expected)


def test_full_silver_buys_a_silver_chest() -> None:
    sim = VktSim("map", silver_after={1}, chest_page=2)
    ctx = make_ctx(sim)
    r = execute(VoKhoiThap(params(rounds=2, full_silver=FullSilverPolicy.BUY_CHEST)), ctx)
    assert r.status is RunStatus.DONE, r.message
    shop = sim.clicks("shop_empty") + sim.clicks("shop")
    chest_centre = (SHOP_CHEST[0] + SHOP_CHEST[2] // 2, SHOP_CHEST[1] + SHOP_CHEST[3] // 2)
    assert shop[0] == (375, 155)  # the shop tab, once: the scroll button showed at once
    assert shop[1:3] == [(652, 427)] * 2  # next page until the chest shows (page 2)
    assert _near(shop[3], chest_centre, 1)
    assert shop[4:] == [(505, 460)] * 2
    assert sim.keys == [("map_silver", VK_RETURN), ("shop", VK_ESCAPE)]
    assert sim.clicks()[-7:] == [p for _n, p in V180_ROUND]  # round 2 after the purchase


def test_full_silver_shop_loop_is_bounded(monkeypatch, tmp_path: Path) -> None:
    """v1.8.0 paged the shop forever when the chest never showed (#2)."""
    monkeypatch.setattr(vkt, "SHOP_PAGES", 4)
    sim = VktSim("map", silver_after={1}, chest_page=None)
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(VoKhoiThap(params(rounds=3, full_silver=FullSilverPolicy.BUY_CHEST)), ctx)
    assert r.status is RunStatus.FAILED and "1 tỷ bạc" in r.message
    assert sim.clicks("shop_empty").count((652, 427)) == 4
    assert r.screenshot is not None


def test_full_silver_shop_tab_is_bounded(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(vkt, "SHOP_TAB_TRIES", 3)
    sim = VktSim("map", silver_after={1})
    ctx = make_ctx(sim, png_dir=tmp_path)
    own = TemplateStore(PROFILE / "templates")
    own.register(vkt.SHOP_SCROLL, np.random.default_rng(9).integers(0, 255, (25, 28, 3), dtype=np.uint8))
    ctx.matcher = Matcher(own, history=64)
    r = execute(VoKhoiThap(params(rounds=3, full_silver=FullSilverPolicy.BUY_CHEST)), ctx)
    assert r.status is RunStatus.FAILED and "Cửa hiệu" in r.message
    assert sim.clicks("shop_empty") == [(375, 155)] * 3


def test_full_silver_stop(tmp_path: Path) -> None:
    sim = VktSim("map", silver_after={1})
    ctx = make_ctx(sim, png_dir=tmp_path)
    r = execute(VoKhoiThap(params(rounds=5, full_silver=FullSilverPolicy.STOP)), ctx)
    assert r.status is RunStatus.DONE
    assert "Full bạc: đã dừng sau 1 lượt" in r.message
    assert_parity(sim.clicks(), v180_nav(2)[1:] + V180_ROUND)
    assert list(tmp_path.glob("*.png"))  # the full-silver screen is saved


# --- templates ----------------------------------------------------------------------------------
def _all_screens() -> list[Path]:
    return sorted(SHOTS.glob("*.png")) + sorted(LIVE.glob("*.png"))


def test_out_of_tokens_marker_on_no_stored_screen() -> None:
    m = Matcher(STORE)
    for path in _all_screens():
        img = _img(path)
        fix = LOCATOR.locate(img)
        d = m.search(vkt.OUT_OF_TOKENS, fix.rect, vkt.SMALL_THRESHOLD, frame=img, scale=fix.scale)
        assert not d.found, f"{path.name}: {d.score:.2f}"


def test_full_silver_only_on_small_confirm_boxes() -> None:
    """The legacy image is a confirm-box corner; standard dialogs and the HUD are outside the region."""
    m = Matcher(STORE)
    hits = set()
    for path in _all_screens():
        img = _img(path)
        fix = LOCATOR.locate(img)
        roi = fix.rect_to_frame(vkt.FULL_SILVER_REGION)
        if m.search(vkt.FULL_SILVER, roi, vkt.SMALL_THRESHOLD, frame=img, scale=fix.scale).found:
            hits.add(path.name)
    assert hits <= {"tower-ui-enter-confirmation.png", "05-pho-ban-list.png"}
    assert "tower-ui-enter-confirmation.png" in hits
    for clean in ("01-city-main.png", "02-world-map.png", "19-tinh-tu.png", "41-trang-bi.png"):
        assert clean not in hits


def test_tinh_tu_icon_piece_is_on_the_hud() -> None:
    m = Matcher(STORE)
    for name in ("01-city-main.png", "02-world-map.png"):
        img = _img(LIVE / name)
        fix = LOCATOR.locate(img)
        d = m.search(vkt.CHEST_ICON, fix.rect, vkt.SMALL_THRESHOLD, frame=img)
        assert d.found and fix.to_canvas(d.rect.top_left) == CHEST_ICON


def test_shop_templates_on_the_shop_page() -> None:
    m = Matcher(STORE)
    img = frames()["shop"]
    fix = LOCATOR.locate(img)
    assert m.search(vkt.SHOP_SCROLL, fix.rect, vkt.SHOP_THRESHOLD, frame=img).found
    d = m.search(vkt.SHOP_CHESTS[0], fix.rect, vkt.SHOP_THRESHOLD, frame=img)
    assert d.found and fix.to_canvas(d.rect.top_left) == SHOP_CHEST[:2]
    empty = frames()["shop_empty"]
    assert not any(m.search(n, fix.rect, vkt.SHOP_THRESHOLD, frame=empty).found for n in vkt.SHOP_CHESTS)


def test_coords_are_the_v180_values() -> None:
    assert COORDS.offset("vo_khoi_thap.world_map") == (950, 490)
    assert COORDS.offset("vo_khoi_thap.vkt_entry") == (235, 330)
    assert COORDS.offset("common.quantity_box") == (425, 288)
    assert COORDS.offsets("quay_ruong.per_spin") == [(200, 420), (440, 360), (200, 420)]


# --- params, settings, registry -----------------------------------------------------------------
def test_params_and_labels() -> None:
    p = VoKhoiThapParams()
    assert p.boss is Boss.PHI_LIEM and p.full_silver is FullSilverPolicy.SKIP  # v1.8.0 GUI defaults
    assert (p.auto_spin, p.buy_chests, p.chest_quantity, p.delay_ms) == (True, True, 200, 500)
    assert [b.label.split(".")[0] for b in Boss] == [str(i) for i in range(1, 8)]
    assert FullSilverPolicy.STOP.label == "3. Dừng và thông báo"
    for bad in ({"chest_quantity": 0}, {"delay_ms": -1}, {"boss": 8}, {"full_silver": 4}, {"rounds": -1}):
        with pytest.raises(ValueError):
            VoKhoiThapParams(**bad)


def test_legacy_import() -> None:
    ini = {"BOSS_ID": "3. Bệ Ngạn ( lv 140 )", "BOSS_SLMUA": "200", "BOSS_SLEEP": "500",
           "BOSS_FULLBAC": "1. Bỏ qua và chạy tiếp"}
    out = VoKhoiThap.import_legacy(ini)
    assert out == {"boss": 3, "chest_quantity": 200, "delay_ms": 500, "full_silver": 1}
    assert VoKhoiThapParams.model_validate(out).boss is Boss.BE_NGAN
    bad = {"BOSS_ID": "9. Không", "BOSS_SLMUA": "Số lượng", "BOSS_SLEEP": "", "BOSS_FULLBAC": "x"}
    assert VoKhoiThap.import_legacy(bad) == {}
    assert VoKhoiThap.import_legacy({"BOSS_SLMUA": "0"}) == {}


def test_registered() -> None:
    ids = {c.id for c in registry.discover()}
    assert "vo_khoi_thap" in ids
    assert registry.get_feature("vo_khoi_thap") is VoKhoiThap
    assert VoKhoiThap.start_scene == "world_map"


def test_form_is_generated(qapp) -> None:
    from ngoalong.ui.forms import ChoiceEditor, ParamsForm

    form = ParamsForm(VoKhoiThapParams)
    assert isinstance(form.editors["boss"], ChoiceEditor)
    form.editors["boss"].set(Boss.DAO_NGOT)
    form.editors["full_silver"].set(FullSilverPolicy.BUY_CHEST)
    v = form.value()
    assert v.boss is Boss.DAO_NGOT and v.full_silver is FullSilverPolicy.BUY_CHEST
