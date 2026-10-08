"""Explore the live game by hand or by an agent: capture the canvas, click and press keys in canvas
coordinates (1000x600), and save every frame to a corpus folder (docs/08, phase R0).

    python -m ngoalong.tools.explore shot city-main
    python -m ngoalong.tools.explore click 944 493 --name world-map
    python -m ngoalong.tools.explore key esc --name after-esc
    python -m ngoalong.tools.explore move 500 300 --name hover-tooltip

Every command finds the Coowon "Ngọa Long" window, locates the canvas (a click needs a HIGH fix),
acts, waits ``--wait`` seconds and saves ``corpus/live/NNN-<name>.png`` (the canvas only). With
``--grid DIR`` a copy with a 50 px coordinate grid is also written there, to pick the next click.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from ngoalong.core.capture import save_png
from ngoalong.core.geometry import Point
from ngoalong.core.input import VK_BACK, VK_ESCAPE, VK_RETURN, VK_SPACE, VK_TAB
from ngoalong.core.locator import CanvasFix, CanvasLocator
from ngoalong.core.window import GameWindow
from ngoalong.paths import APP_ROOT

DEFAULT_OUT = APP_ROOT / "corpus" / "live"
KEYS = {"esc": VK_ESCAPE, "enter": VK_RETURN, "space": VK_SPACE, "tab": VK_TAB, "back": VK_BACK}
WM_MOUSEMOVE = 0x0200


def _window(index: int) -> GameWindow:
    windows = GameWindow.discover()
    if not windows:
        raise SystemExit("no Coowon 'Ngọa Long' window found")
    if index >= len(windows):
        raise SystemExit(f"only {len(windows)} game window(s) found")
    return windows[index]


def _next_path(out: Path, name: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    nums = [int(p.name[:3]) for p in out.glob("[0-9][0-9][0-9]-*.png") if p.name[:3].isdigit()]
    return out / f"{max(nums, default=0) + 1:03d}-{name}.png"


def _grid(canvas: np.ndarray) -> np.ndarray:
    img = canvas.copy()
    h, w = img.shape[:2]
    for x in range(0, w, 50):
        cv2.line(img, (x, 0), (x, h), (0, 255, 255) if x % 100 == 0 else (0, 140, 140), 1)
        if x % 100 == 0:
            cv2.putText(img, str(x), (x + 2, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (0, 255, 255), 1)
    for y in range(0, h, 50):
        cv2.line(img, (0, y), (w, y), (0, 255, 255) if y % 100 == 0 else (0, 140, 140), 1)
        if y % 100 == 0:
            cv2.putText(img, str(y), (2, y + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (0, 255, 255), 1)
    return img


def _capture(gw: GameWindow, locator: CanvasLocator) -> tuple[np.ndarray, CanvasFix]:
    from ngoalong.core.drivers.win32 import Win32Capturer

    frame = Win32Capturer(gw).grab()
    return frame, locator.locate(frame)


def _crop(frame: np.ndarray, fix: CanvasFix) -> np.ndarray:
    r = fix.rect
    return frame[max(0, r.y):max(0, r.bottom), max(0, r.x):max(0, r.right)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ngoalong.tools.explore", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["shot", "click", "dclick", "rclick", "move", "key"])
    ap.add_argument("args", nargs="*", help="x y (canvas, 1000x600) for click/move; key name for key; name for shot")
    ap.add_argument("--name", default=None, help="frame name (default: the action)")
    ap.add_argument("--wait", type=float, default=1.0, help="seconds to wait before the capture")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--grid", type=Path, default=None, help="also write a gridded copy into this folder")
    ap.add_argument("--window", type=int, default=0, help="index of the game window when several are open")
    args = ap.parse_args(argv)

    from ngoalong.core.drivers.win32 import Win32InputDriver
    from ngoalong.core import _winapi as w

    gw = _window(args.window)
    locator = CanvasLocator.from_profile()
    name = args.name
    if args.action == "shot":
        name = name or (args.args[0] if args.args else "shot")
    else:
        frame, fix = _capture(gw, locator)
        if not fix.reliable:
            raise SystemExit(f"canvas not reliable ({fix.confidence.value}); nothing sent")
        driver = Win32InputDriver(gw)
        if args.action == "key":
            if len(args.args) != 1 or args.args[0] not in KEYS:
                raise SystemExit(f"key needs one of {sorted(KEYS)}")
            driver.key(KEYS[args.args[0]])
            name = name or f"key-{args.args[0]}"
        else:
            if len(args.args) != 2:
                raise SystemExit(f"{args.action} needs x y in canvas coordinates")
            p = fix.to_frame(Point(int(args.args[0]), int(args.args[1])))
            if args.action == "move":
                w.PostMessageW(driver.hwnd, WM_MOUSEMOVE, 0, w.make_lparam(p.x, p.y))
            else:
                button = "right" if args.action == "rclick" else "left"
                driver.click(p, button, args.action == "dclick")
            name = name or f"{args.action}-{args.args[0]}-{args.args[1]}"
        time.sleep(max(0.0, args.wait))

    frame, fix = _capture(gw, locator)
    canvas = _crop(frame, fix) if fix.reliable else frame
    path = save_png(_next_path(args.out, name), canvas)
    if args.grid is not None:
        args.grid.mkdir(parents=True, exist_ok=True)
        save_png(args.grid / path.name, _grid(canvas))
    r = fix.rect
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(f"{path.name}: canvas ({r.x}, {r.y}) {r.w}x{r.h} x{fix.scale:.2f} {fix.confidence.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
