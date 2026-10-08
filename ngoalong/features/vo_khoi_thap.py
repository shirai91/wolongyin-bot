"""Võ Khôi Tháp: fight a tower boss again and again (port of v1.8.0 ``BOSS_VOKHOITHAP`` and its
helpers ``BOSS_CHECKHETLENH``, ``BOSS_QUAYRUONG``, ``BOSS_CHECKFULLBAC``; docs/03).

v1.8.0, kept click for click (offsets in ``coords/vo_khoi_thap.yaml``):

1. "Thế Giới" (950, 490), the Võ Khôi Tháp marker (235, 330); "lùi" x7, "tiến" x (boss - 1),
   "Thoát" (495, 575);
2. every round: out-of-tokens check, full-silver check, then marker, "Tấn công", "Lập đội" x2,
   "Đánh", confirm (456, 355), "Nhận thưởng";
3. out of tokens (``Boss_CheckHetLenh``) with "Auto quay rương": (490, 583) x2, the Tinh Tú HUD
   icon (``Boss_TimRuong``), optionally "Mua" + quantity + confirm (+ the purchase check), then
   ``quantity`` spins of ``quay_ruong.per_spin`` 50 ms apart, the full-silver check and ESC x5;
4. full silver (``VKT_FullTien``): policy 1 click (520, 328) twice; policy 2 Enter, open the shop
   tab, page until a "1 tỷ bạc" chest shows, click it and "Mua" twice, ESC; policy 3 stop.

Changes, on purpose:

* the run starts from the city or the world map, checked with the ``city`` / ``world_map`` scenes;
* the shop loops of policy 2 are bounded (they hung v1.8.0 forever, #2) and fail with a screenshot;
* out of tokens without "Auto quay rương" ends the run (v1.8.0 kept attacking with no tokens);
  the number of chest refills and of rounds can be limited; out of tokens again in the round right
  after a refill ends the run with an error (the spins gave nothing: no endless chest purchases);
* the Tinh Tú wheel is checked (``chest.wheel``) before buying and spinning; it is closed with ESC
  (at most 5 presses) and its ✕ as a fallback, instead of 5 blind ESC presses (#9);
* every click on the Võ Khôi Tháp marker waits for the boss screen (scene ``vo_khoi_thap``, it
  takes seconds to load) and "Thoát" waits for the world map; if the screen does not open the run
  ends with an error and a screenshot instead of clicking blind on the map;
* the full-silver template is a generic confirm-box corner (it also matches the tower confirm
  box): it is searched only in the centre of the canvas, where small confirm boxes appear;
* templates are matched with a score and clicked at their centre, not their corner (#3);
* Stop / Pause anywhere, progress per round, no message box.

The boss screen was captured live (2026-10-06, docs/07): the marker, the boss arrows, "Tấn công"
and "Thoát" offsets hit it. The fight screens after "Tấn công" and the legacy popup templates
(``template_crops.yaml``) are v1.8.0's and still unverified on ngoalongviet.com.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from typing import Any

from pydantic import Field

from ngoalong.core.errors import NgoaLongError, WaitTimeout
from ngoalong.core.geometry import Rect
from ngoalong.core.input import VK_ESCAPE, VK_RETURN
from ngoalong.engine.context import RunContext, SceneTimeout
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

KEY = "vo_khoi_thap"
SPIN_KEY = "quay_ruong.per_spin"  # the Tinh Tú spin, shared with Quay rương (v1.8.0 did the same)
QUANTITY_BOX = "common.quantity_box"  # (425, 288): double-click, then type

# --- scenes -------------------------------------------------------------------------------------
CITY = "city"
WORLD_MAP = "world_map"
VKT_SCREEN = "vo_khoi_thap"  # the boss screen (scenes/vo_khoi_thap.yaml)
WHEEL = "chest.wheel"

# --- templates (legacy v1.8.0 images, template_crops.yaml) and thresholds -----------------------
OUT_OF_TOKENS = f"{KEY}.out_of_tokens"  # Boss_CheckHetLenh
CHEST_ICON = f"{KEY}.chest_icon"  # Boss_TimRuong: a piece of the Tinh Tú HUD icon
BUY_CHECK = f"{KEY}.buy_check"  # VKT_MuaRuong_Check
FULL_SILVER = f"{KEY}.full_silver"  # VKT_FullTien
SHOP_SCROLL = f"{KEY}.shop.scroll_down"  # VKT_CuaHang_Xuong
SHOP_CHESTS = (f"{KEY}.shop.chest_1ty_1", f"{KEY}.shop.chest_1ty_2")  # VKT_CuaHang_Ruong1Ty_*
# Small legacy images: demand near-exact matches (best score on the 47 stored screens without the
# marker: 0.81 for the out-of-tokens marker).
SMALL_THRESHOLD = 0.95
SHOP_THRESHOLD = 0.9
# Where a small confirm box's top-left corner lies (canvas). Standard dialogs and their inner
# panels (corners at y 182) and the HUD stay outside, the tower confirm box (339, 250) inside.
FULL_SILVER_REGION = Rect(250, 200, 350, 200)

# --- v1.8.0 timings (ms) ------------------------------------------------------------------------
BOSS_PREV_CLICKS = 7  # "lùi" x7 shows boss 1
SPIN_GAP_MS = 50  # BOSS_QUAYRUONG: Sleep(50) between the clicks of a spin
POPUP_GAP_MS = 500  # BOSS_CHECKFULLBAC: Sleep(500)
BUY_CHECK_WAIT_S = 1.0  # IMAGEWAITSEARCHEX(..., 1)
SHOP_TAB_WAIT_S = 3.0  # IMAGEWAITSEARCHEX(..., 3)
ESC_PRESSES = 5  # BOSS_QUAYRUONG sent {ESC} five times

# --- bounds for the loops v1.8.0 never left (#2) ------------------------------------------------
SHOP_TAB_TRIES = 5
SHOP_PAGES = 20
SHOP_PAGE_WAIT_S = 1.0
MAX_QUANTITY = 100_000


class VoKhoiThapError(NgoaLongError):
    """The boss screen, the chest wheel or the shop did not behave as expected."""


class Boss(IntEnum):
    """The 7 bosses of v1.8.0's combo; the value is how many "tiến" clicks from boss 1, plus one."""

    THIEN_LANG = 1
    PHI_LIEM = 2
    BE_NGAN = 3
    AP_DU = 4
    BA_XA = 5
    CHU_YEM = 6
    DAO_NGOT = 7

    @property
    def label(self) -> str:
        return _BOSS_LABELS[self]


_BOSS_LABELS: dict[Boss, str] = {
    Boss.THIEN_LANG: "1. Thiên Lang (lv 100)",
    Boss.PHI_LIEM: "2. Phỉ Liêm (lv 120)",
    Boss.BE_NGAN: "3. Bệ Ngạn (lv 140)",
    Boss.AP_DU: "4. Áp Du (lv 160)",
    Boss.BA_XA: "5. Ba Xà (lv 165)",
    Boss.CHU_YEM: "6. Chu Yểm (lv 170)",
    Boss.DAO_NGOT: "7. Đào Ngột (lv 175)",
}


class FullSilverPolicy(IntEnum):
    """What to do when the "full silver" box shows (v1.8.0 ``BOSS_FULLBAC``)."""

    SKIP = 1
    BUY_CHEST = 2
    STOP = 3

    @property
    def label(self) -> str:
        return _POLICY_LABELS[self]


_POLICY_LABELS: dict[FullSilverPolicy, str] = {
    FullSilverPolicy.SKIP: "1. Bỏ qua và chạy tiếp",
    FullSilverPolicy.BUY_CHEST: "2. Mua rương bạc và chạy tiếp",
    FullSilverPolicy.STOP: "3. Dừng và thông báo",
}


class VoKhoiThapParams(TaskParams):
    boss: Boss = Field(Boss.PHI_LIEM, title="Boss")
    auto_spin: bool = Field(True, title="Auto quay rương khi hết lệnh")
    buy_chests: bool = Field(True, title="Mua rương trước khi quay")
    chest_quantity: int = Field(
        200, ge=1, le=MAX_QUANTITY, title="Số rương mua / quay",
        description="Số rương mua (khi chọn 'Mua rương') và số lượt quay mỗi lần hết lệnh",
    )
    delay_ms: int = Field(500, ge=0, le=60_000, title="Tốc độ: thời gian chờ giữa các bước (ms)")
    full_silver: FullSilverPolicy = Field(FullSilverPolicy.SKIP, title="Tùy chọn khi Full Bạc")
    rounds: int = Field(0, ge=0, le=1_000_000, title="Số lượt đánh (0 = đến khi bấm Dừng)")
    max_refills: int = Field(
        0, ge=0, le=100_000, title="Số lần mở rương tối đa (0 = không giới hạn)",
        description="Dừng khi đã hết lệnh và mở rương nhiều lần như vậy",
    )
    scene_timeout_s: float = Field(10.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")


@register
class VoKhoiThap(Task):
    id = "vo_khoi_thap"
    title = "Võ Khôi Tháp"
    description = (
        "Ở Thành chính hoặc bản đồ Thế Giới rồi bấm Bắt đầu: chọn boss và đánh liên tục; khi hết "
        "lệnh thì mở Tinh Tú mua và quay rương; xử lý Full Bạc theo tùy chọn"
    )
    order = 80
    Params = VoKhoiThapParams
    start_scene = WORLD_MAP

    params: VoKhoiThapParams
    ctx: RunContext  # the context of the current run (set by ``run``)

    def __init__(self, params: TaskParams | Mapping[str, Any] | None = None) -> None:
        super().__init__(params)
        self.rounds_done = 0
        self.refills = 0
        self.silver_events = 0
        self._refilled_last = False  # the previous round started with a chest refill

    @classmethod
    def required_templates(cls) -> set[str]:
        return {OUT_OF_TOKENS, CHEST_ICON, BUY_CHECK, FULL_SILVER, SHOP_SCROLL, *SHOP_CHESTS}

    # --- helpers --------------------------------------------------------------------------------
    def _k(self, name: str) -> str:
        return f"{KEY}.{name}"

    def _key(self, vk: int) -> None:
        self.ctx.checkpoint()
        self.ctx.input.key(vk)

    # --- run ------------------------------------------------------------------------------------
    def run(self, ctx: RunContext) -> str:
        self.ctx = ctx
        p = self.params
        self.rounds_done = self.refills = self.silver_events = 0
        self._refilled_last = False
        self._open_map()
        self._select_boss()
        total = p.rounds or None
        while p.rounds == 0 or self.rounds_done < p.rounds:
            n = self.rounds_done + 1
            ctx.step(f"lượt {n}")
            stop = self._check_tokens()
            if stop is None:
                stop = self._check_full_silver()
            if stop is not None:
                return stop
            self._attack_round()
            self.rounds_done = n
            ctx.count("bosses")
            ctx.progress(n, total)
        return self._summary(f"Đã đánh xong {self.rounds_done} lượt {p.boss.label}")

    def _summary(self, head: str) -> str:
        parts = [head]
        if self.refills:
            parts.append(f"mở rương {self.refills} lần")
        if self.silver_events:
            parts.append(f"full bạc {self.silver_events} lần")
        return ", ".join(parts)

    # --- navigation -----------------------------------------------------------------------------
    def _open_map(self) -> None:
        ctx, p = self.ctx, self.params
        ctx.step("mở bản đồ Thế Giới")
        m = ctx.detect_scene()
        if m.name == WORLD_MAP:
            return
        if m.name != CITY:
            shown = m.name if m.found else f"không rõ (gần nhất {m.candidate})"
            raise VoKhoiThapError(
                f"Hãy về Thành chính hoặc mở bản đồ Thế Giới rồi bấm Bắt đầu (màn hình hiện tại: {shown})"
            )
        ctx.click(self._k("world_map"), label="Thế Giới")
        ctx.expect_scene(WORLD_MAP, p.scene_timeout_s)
        ctx.sleep(p.delay_ms * 2.5)

    def _enter_tower(self) -> None:
        """Click the world-map marker and wait for the boss screen (it loads in several seconds)."""
        ctx, p = self.ctx, self.params
        ctx.click(self._k("vkt_entry"), label="Võ Khôi Tháp")
        try:
            ctx.expect_scene(VKT_SCREEN, p.scene_timeout_s)
        except SceneTimeout as exc:
            raise VoKhoiThapError(
                f"Không mở được Võ Khôi Tháp tại vo_khoi_thap.vkt_entry: {exc}. Hãy mở bản đồ Thế "
                "Giới (không có bảng nào che) và kiểm tra tọa độ vo_khoi_thap.vkt_entry"
            ) from exc

    def _select_boss(self) -> None:
        ctx, p = self.ctx, self.params
        d = p.delay_ms
        ctx.step(f"chọn boss {p.boss.label}")
        self._enter_tower()
        for _ in range(BOSS_PREV_CLICKS):
            ctx.click(self._k("boss_prev"), label="lùi")
            ctx.sleep(d / 10)
        ctx.sleep(d)
        for _ in range(int(p.boss) - 1):
            ctx.click(self._k("boss_next"), label="tiến")
            ctx.sleep(d / 10)
        ctx.click(self._k("exit"), label="Thoát")
        try:
            ctx.expect_scene(WORLD_MAP, p.scene_timeout_s)
        except SceneTimeout as exc:
            raise VoKhoiThapError(f"bấm Thoát nhưng không về bản đồ Thế Giới: {exc}") from exc

    def _attack_round(self) -> None:
        ctx, d = self.ctx, self.params.delay_ms
        self._enter_tower()
        # the fight screens after "Tấn công" are not catalogued yet (docs/07): v1.8.0's delays
        for name, label in (
            ("attack", "Tấn công"), ("form_team", "Lập đội"), ("form_team", "Lập đội"),
            ("fight", "Đánh"), ("fight_confirm", "xác nhận"), ("claim", "Nhận thưởng"),
        ):
            ctx.click(self._k(name), label=label)
            ctx.sleep(d)

    # --- out of tokens (BOSS_CHECKHETLENH) ------------------------------------------------------
    def _check_tokens(self) -> str | None:
        ctx, p = self.ctx, self.params
        ctx.step("kiểm tra lệnh")
        if ctx.find(OUT_OF_TOKENS, threshold=SMALL_THRESHOLD) is None:
            self._refilled_last = False
            return None
        if not p.auto_spin:
            ctx.warn("Đã hết lệnh Võ Khôi Tháp (không chọn auto quay rương)")
            return self._summary(f"Hết lệnh sau {self.rounds_done} lượt {p.boss.label}")
        if self._refilled_last:
            # the spins gave no tokens: refilling again would buy chests every round until Stop
            raise VoKhoiThapError(
                "hết lệnh ngay sau khi mở rương: quay rương không có tác dụng (đã dừng để không "
                f"mua rương lặp lại; đã mở rương {self.refills} lần)"
            )
        if p.max_refills and self.refills >= p.max_refills:
            ctx.warn(f"Đã hết lệnh và đã mở rương {self.refills} lần (tối đa {p.max_refills})")
            return self._summary(f"Hết lệnh sau {self.rounds_done} lượt {p.boss.label}")
        self.refills += 1
        ctx.count("chests")
        ctx.info(f"Hết lệnh: mở Tinh Tú quay rương (lần {self.refills})")
        stop = self._refill()
        self._refilled_last = True
        return stop

    def _refill(self) -> str | None:
        ctx, p = self.ctx, self.params
        gap = p.delay_ms * 2  # v1.8.0 used 1000 ms here, twice its default delay
        ctx.step("mở rương")
        for _ in range(2):
            ctx.click(self._k("out_of_tokens_open_chest"), label="mở rương")
            ctx.sleep(gap)
        try:
            icon = ctx.wait_for(CHEST_ICON, p.scene_timeout_s, threshold=SMALL_THRESHOLD)
        except WaitTimeout as exc:
            raise VoKhoiThapError(f"không thấy biểu tượng Tinh Tú để mở rương: {exc}") from exc
        ctx.click(icon, label="Tinh Tú")
        ctx.expect_scene(WHEEL, p.scene_timeout_s)
        ctx.sleep(gap)
        if p.buy_chests:
            self._buy(gap)
        ctx.sleep(gap)
        return self._spin()

    def _buy(self, gap: float) -> None:
        ctx, p = self.ctx, self.params
        ctx.step(f"mua {p.chest_quantity} rương")
        ctx.click(self._k("buy_chest_tab"), label="Mua")
        ctx.sleep(gap)
        ctx.click(QUANTITY_BOX, double=True, label="ô số lượng")
        ctx.sleep(gap)
        ctx.checkpoint()
        ctx.input.text(str(p.chest_quantity))
        ctx.sleep(gap)
        ctx.click(self._k("buy_confirm"), label="xác nhận mua")
        ctx.sleep(gap)
        try:
            ctx.wait_for(BUY_CHECK, BUY_CHECK_WAIT_S, threshold=SHOP_THRESHOLD)
        except WaitTimeout:
            return
        ctx.click(self._k("buy_check_ok"), label="OK")
        ctx.sleep(gap)
        ctx.click(self._k("fight_confirm"), label="đồng ý")  # v1.8.0 (455, 355)
        ctx.sleep(gap)

    def _spin(self) -> str | None:
        """BOSS_QUAYRUONG: ``chest_quantity`` spins, the full-silver check, then close the wheel."""
        ctx, p = self.ctx, self.params
        ctx.step("quay rương")
        ctx.expect_scene(WHEEL, p.scene_timeout_s)
        keys = [f"{SPIN_KEY}.{i}" for i in range(len(ctx.coords.offsets(SPIN_KEY)))]
        for spin in range(p.chest_quantity):
            for i, key in enumerate(keys):
                if i:
                    ctx.sleep(SPIN_GAP_MS)
                ctx.click(key, label=f"quay {spin + 1}")
            ctx.count("spins")
            ctx.sleep(SPIN_GAP_MS)
        ctx.sleep(p.delay_ms)
        stop = self._check_full_silver()
        if stop is not None:
            return stop
        ctx.sleep(p.delay_ms)
        self._close_wheel()
        return None

    def _close_wheel(self) -> None:
        ctx, p = self.ctx, self.params
        ctx.step("đóng Tinh Tú")
        for _ in range(ESC_PRESSES):
            if ctx.detect_scene().name != WHEEL:
                return
            self._key(VK_ESCAPE)
            ctx.sleep(p.delay_ms)
        if ctx.detect_scene().name != WHEEL:
            return
        ctx.click_object(WHEEL, "close")
        ctx.sleep(p.delay_ms)
        if ctx.detect_scene().name == WHEEL:
            raise VoKhoiThapError("không đóng được bảng Tinh Tú")

    # --- full silver (BOSS_CHECKFULLBAC) --------------------------------------------------------
    def _check_full_silver(self) -> str | None:
        ctx, p = self.ctx, self.params
        if ctx.find(FULL_SILVER, region=FULL_SILVER_REGION, threshold=SMALL_THRESHOLD) is None:
            return None
        self.silver_events += 1
        ctx.step("full bạc")
        ctx.warn(f"Full bạc: {p.full_silver.label}")
        if p.full_silver is FullSilverPolicy.SKIP:
            ctx.click(self._k("full_silver_dismiss"), label="bỏ qua")
            ctx.sleep(POPUP_GAP_MS)
            ctx.click(self._k("full_silver_dismiss"), label="bỏ qua")
            ctx.sleep(p.delay_ms)
            return None
        if p.full_silver is FullSilverPolicy.BUY_CHEST:
            self._buy_silver_chest()
            return None
        shot = ctx.save_failure("full bạc")
        ctx.warn("Đã dừng vì Full Bạc" + (f" (ảnh: {shot})" if shot else ""))
        return self._summary(f"Full bạc: đã dừng sau {self.rounds_done} lượt {p.boss.label}")

    def _buy_silver_chest(self) -> None:
        """Policy 2: spend the silver on a "1 tỷ bạc" chest in the shop (bounded loops, #2)."""
        ctx = self.ctx
        ctx.step("mua rương bạc")
        self._key(VK_RETURN)
        ctx.sleep(POPUP_GAP_MS)
        for _ in range(SHOP_TAB_TRIES):
            ctx.click(self._k("shop_tab"), label="Cửa hiệu")
            try:
                ctx.wait_for(SHOP_SCROLL, SHOP_TAB_WAIT_S, threshold=SHOP_THRESHOLD)
                break
            except WaitTimeout:
                continue
        else:
            raise VoKhoiThapError(f"không mở được Cửa hiệu sau {SHOP_TAB_TRIES} lần bấm (Full Bạc)")
        ctx.sleep(POPUP_GAP_MS)
        chest = None
        for _ in range(SHOP_PAGES):
            ctx.click(self._k("shop_next_page"), label="trang sau")
            try:
                chest = ctx.wait_for(SHOP_CHESTS, SHOP_PAGE_WAIT_S, threshold=SHOP_THRESHOLD)
                break
            except WaitTimeout:
                continue
        if chest is None:
            self._key(VK_ESCAPE)
            raise VoKhoiThapError(
                f"không thấy rương 1 tỷ bạc trong Cửa hiệu sau {SHOP_PAGES} trang (Full Bạc)"
            )
        ctx.sleep(POPUP_GAP_MS)
        ctx.click(chest, label="rương 1 tỷ bạc")
        ctx.sleep(POPUP_GAP_MS)
        ctx.click(self._k("shop_buy"), label="Mua")
        ctx.sleep(POPUP_GAP_MS)
        ctx.click(self._k("shop_buy"), label="Mua")
        ctx.sleep(POPUP_GAP_MS)
        self._key(VK_ESCAPE)
        ctx.sleep(self.params.delay_ms)

    # --- settings import ------------------------------------------------------------------------
    @classmethod
    def import_legacy(cls, ini: Mapping[str, str]) -> dict[str, Any]:
        """``BOSS_ID`` / ``BOSS_FULLBAC`` (combo labels "3. Bệ Ngạn ( lv 140 )"), ``BOSS_SLMUA``,
        ``BOSS_SLEEP`` (docs/04). Blank, placeholder or out-of-range values are dropped (#15)."""

        def number(key: str, label: bool = False) -> int | None:
            raw = ini.get(key, "").strip()
            if label:
                raw = raw.split(".", 1)[0].strip()
            try:
                return int(raw)
            except ValueError:
                return None

        out: dict[str, Any] = {}
        boss = number("BOSS_ID", label=True)
        if boss is not None and boss in Boss._value2member_map_:
            out["boss"] = boss
        policy = number("BOSS_FULLBAC", label=True)
        if policy is not None and policy in FullSilverPolicy._value2member_map_:
            out["full_silver"] = policy
        qty = number("BOSS_SLMUA")
        if qty is not None and qty >= 1:
            out["chest_quantity"] = min(qty, MAX_QUANTITY)
        delay = number("BOSS_SLEEP")
        if delay is not None and delay >= 0:
            out["delay_ms"] = min(delay, 60_000)
        return out
