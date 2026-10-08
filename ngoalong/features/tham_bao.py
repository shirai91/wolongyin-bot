"""Thám Bảo: run treasure-hunt rounds ("Càn quét") (port of v1.8.0 ``THAMBAO``, docs/03).

v1.8.0 looped until F10. Each round it clicked the "Càn quét" button twice when found
(``TB_CanQuet`` / ``TB_CanQuet2``), then (690, 345), and then repeated, with no time limit, until the
button showed again:

* with "Chiến" checked: an opponent box (``TB_DoiThu``) -> "fight" (590, 380), wait up to 6 s for
  ``TB_KetQua`` -> (822, 578), 1 s, OK (538, 409); no opponent -> (680, 380);
* a "mất thất" popup (``TB_MatThat``) -> (440, 390);
* (780, 510), then (685, 440);
* "reward claimed" (``TB_DaNhanThuong``) -> (702, 486).

The port keeps the order, the offsets (``coords/tham_bao.yaml``) and the v1.8.0 images
(``scenes/tham_bao.yaml``; the screen was not reached on ngoalongviet.com, docs/07), and:

* checks that a Thám Bảo marker is on screen before the first click (no blind clicking elsewhere);
* every round has a time limit (``round_timeout_s``) and Stop works everywhere (#2, #7);
* optional number of rounds (0 = until Stop, like v1.8.0);
* the "x2" option now works (#16: v1.8.0 read its checkbox and never used it; P3 "Thám Bảo x2
  reward", docs/06): after the round's two steps, if the x2 reward bag (``TB_QuaX2``, template
  ``tham_bao.reward_x2``) shows, it is clicked at the match centre, at most once per round so a
  missed screen change can never claim (and pay for) the x2 reward twice. Off by default;
* no message box; a summary at the end and a screenshot on failure.
"""

from __future__ import annotations

import time

from pydantic import Field

from ngoalong.core.errors import NgoaLongError, WaitTimeout
from ngoalong.core.scenes import ObjectResult
from ngoalong.engine.context import RunContext
from ngoalong.engine.registry import register
from ngoalong.engine.task import Task, TaskParams

SCENE = "tham_bao"
SWEEP_OBJECTS = ("sweep", "sweep_alt")  # TB_CanQuet, TB_CanQuet2
RESULT_BOXES = ("win_box", "lose_box")  # live 战胜 / 战败 box, "Thoát" = RESULT_OK
REWARD_X2 = "reward_x2"  # TB_QuaX2: the x2 reward bag
MARKERS = (*SWEEP_OBJECTS, "opponent", "result", "lost", "claimed", REWARD_X2)

KEY = "tham_bao"
START = f"{KEY}.start_after_sweep"
FIGHT = f"{KEY}.fight"
NO_OPPONENT = f"{KEY}.skip"  # clicked when "Chiến" is on and no opponent box shows
FIGHT_RESULT_CLOSE = f"{KEY}.fight_result_close"
LOST_OK = f"{KEY}.lost_popup_ok"
STEP_A = f"{KEY}.step_a"
STEP_B = f"{KEY}.step_b"
CLAIMED_OK = f"{KEY}.claimed_ok"
RESULT_OK = "common.close_result_ok"  # (538, 409)

RESULT_WAIT_S = 6.0  # v1.8.0: IMAGEWAITSEARCHEX(TB_KetQua, 6)
RESULT_CLOSE_PAUSE_MS = 1000  # v1.8.0: Sleep(1000) between the two result clicks


class ThamBaoError(NgoaLongError):
    """Not on the Thám Bảo screen, or a round never finished."""


class ThamBaoParams(TaskParams):
    delay_ms: int = Field(200, ge=0, le=60_000, title="Thời gian chờ giữa các bước (ms)")
    fight: bool = Field(
        True, title="Chiến",
        description="Gặp đối thủ thì đánh (như ô 'Chiến' của bản cũ)",
    )
    reward_x2: bool = Field(
        False, title="Nhận quà x2",
        description="Thấy túi quà x2 thì bấm để nhận thưởng gấp đôi (tối đa 1 lần mỗi lượt)",
    )
    max_rounds: int = Field(0, ge=0, le=100_000, title="Số lượt Càn quét (0 = chạy đến khi bấm Dừng)")
    round_timeout_s: float = Field(
        300.0, ge=10.0, le=3600.0, title="Thời gian tối đa mỗi lượt (giây)",
        description="Không thấy lại nút Càn quét sau thời gian này thì dừng và chụp màn hình",
    )
    scene_timeout_s: float = Field(10.0, ge=0.5, le=120.0, title="Thời gian chờ màn hình (giây)")


@register
class ThamBao(Task):
    id = "tham_bao"
    title = "Thám Bảo"
    description = "Mở màn hình Thám Bảo rồi bấm Bắt đầu: Càn quét liên tục, gặp đối thủ thì đánh (tùy chọn)"
    order = 85
    Params = ThamBaoParams
    start_scene = SCENE

    params: ThamBaoParams
    fights: int = 0  # fights started in the current run (reset by run)
    x2_claims: int = 0  # x2 reward bags clicked in the current run (reset by run)

    # --- helpers ----------------------------------------------------------------------------------
    def _seen(self, ctx: RunContext, obj: str) -> ObjectResult | None:
        r = ctx.find_object(SCENE, obj)
        return r if r.found else None

    def _sweep(self, ctx: RunContext) -> ObjectResult | None:
        for obj in SWEEP_OBJECTS:
            r = self._seen(ctx, obj)
            if r is not None:
                return r
        return None

    def _check_screen(self, ctx: RunContext) -> None:
        """Wait until any Thám Bảo marker shows; never click on another screen."""
        ctx.step("kiểm tra màn hình Thám Bảo")
        deadline = time.monotonic() + self.params.scene_timeout_s
        while True:
            for obj in MARKERS:
                if self._seen(ctx, obj) is not None:
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                m = ctx.detect_scene()
                shown = m.name if m.found else f"không rõ (gần nhất {m.candidate})"
                raise ThamBaoError(f"Hãy mở màn hình Thám Bảo rồi bấm Bắt đầu (màn hình hiện tại: {shown})")
            ctx.sleep_s(min(ctx.poll_interval_s, remaining))

    # --- one round --------------------------------------------------------------------------------
    def _fight(self, ctx: RunContext) -> None:
        p = self.params
        if self._seen(ctx, "opponent") is None:
            ctx.click(NO_OPPONENT, label="không có đối thủ")
            return
        ctx.step("đánh đối thủ")
        ctx.sleep(p.delay_ms)
        ctx.click(FIGHT, label="chiến")
        self.fights += 1
        ctx.count("battles")
        if not self._wait_result(ctx):
            ctx.info("Không thấy kết quả trận sau 6 giây")
            return
        ctx.click(FIGHT_RESULT_CLOSE, label="đóng kết quả")
        ctx.sleep(max(p.delay_ms, RESULT_CLOSE_PAUSE_MS))
        ctx.click(RESULT_OK, label="OK kết quả")

    def _close_result_box(self, ctx: RunContext) -> None:
        """A 战胜 / 战败 box left open (new: v1.8.0 clicked "Thoát" only right after a fight)."""
        for obj in RESULT_BOXES:
            if self._seen(ctx, obj) is not None:
                ctx.sleep(self.params.delay_ms)
                ctx.click(RESULT_OK, label="Thoát kết quả")
                return

    def _wait_result(self, ctx: RunContext) -> bool:
        deadline = time.monotonic() + RESULT_WAIT_S
        while True:
            if self._seen(ctx, "result") is not None:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            ctx.sleep_s(min(ctx.poll_interval_s, remaining))

    def _claim_x2(self, ctx: RunContext) -> bool:
        """Click the x2 reward bag when it shows; True when clicked."""
        bag = self._seen(ctx, REWARD_X2)
        if bag is None:
            return False
        ctx.click(bag, label="quà x2")
        self.x2_claims += 1
        ctx.count("x2")
        ctx.sleep(self.params.delay_ms)
        return True

    def _play_round(self, ctx: RunContext, n: int) -> None:
        """Step through the round until "Càn quét" shows again (bounded by ``round_timeout_s``)."""
        p = self.params
        deadline = time.monotonic() + p.round_timeout_s
        x2_done = False  # at most one x2 click per round
        while True:
            ctx.checkpoint()
            ctx.step(f"lượt {n}")
            self._close_result_box(ctx)
            if p.fight:
                self._fight(ctx)
            ctx.sleep(p.delay_ms)
            if self._seen(ctx, "lost") is not None:
                ctx.sleep(p.delay_ms)
                ctx.click(LOST_OK, label="OK mất thất")
            ctx.sleep(p.delay_ms)
            ctx.click(STEP_A, label="bước 1")
            ctx.sleep(p.delay_ms)
            ctx.click(STEP_B, label="bước 2")
            ctx.sleep(p.delay_ms)
            if p.reward_x2 and not x2_done:
                x2_done = self._claim_x2(ctx)
            if self._seen(ctx, "claimed") is not None:
                ctx.sleep(p.delay_ms)
                ctx.click(CLAIMED_OK, label="OK nhận thưởng")
            if self._sweep(ctx) is not None:
                return
            if time.monotonic() >= deadline:
                raise WaitTimeout(f"lượt {n}: không thấy lại nút Càn quét sau {p.round_timeout_s:.0f}s")

    # --- main loop --------------------------------------------------------------------------------
    def run(self, ctx: RunContext) -> str:
        p = self.params
        self.fights = 0
        self.x2_claims = 0
        self._check_screen(ctx)
        rounds = 0
        while not p.max_rounds or rounds < p.max_rounds:
            ctx.checkpoint()
            n = rounds + 1
            ctx.step(f"bắt đầu lượt {n}")
            ctx.sleep(p.delay_ms * 2)
            sweep = self._sweep(ctx)
            if sweep is not None:
                ctx.click(sweep, label="Càn quét")
                ctx.sleep(p.delay_ms)
                ctx.click(sweep, label="Càn quét")
            else:
                ctx.info(f"Lượt {n}: không thấy nút Càn quét, tiếp tục lượt đang dở")
            ctx.sleep(p.delay_ms)
            ctx.click(START, label="bắt đầu")
            self._play_round(ctx, n)
            rounds = n
            ctx.count("rounds")
            ctx.progress(rounds, p.max_rounds or None)
        x2 = f", nhận quà x2 {self.x2_claims} lần" if p.reward_x2 else ""
        return f"Đã chạy {rounds} lượt Thám Bảo (đánh {self.fights} trận{x2})"
