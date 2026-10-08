# 06 · Rewrite design (Python)

Working draft of the Python rewrite: target browsers, automatic screen detection, debug overlay, stack, modules, interfaces, old-to-new mapping, roadmap and decisions. Issue numbers (#n) refer to [05 Known issues](05-known-issues.md). Index of all parts: [README.md](README.md)

The proposal keeps v1.8.0's proven idea, background `PostMessage` clicks at `origin + offset`, and rebuilds everything around it. The new parts are a worker thread with real cancellation, OpenCV matching with a score threshold, a window binding that survives restarts, and offsets and flows stored as data instead of code.

## Target browsers

The MVP supports one host: **Coowon** on Windows. **FlashBrowser** (Windows, macOS, Linux) is out of scope for the MVP and comes after it. Both are Chromium-based, so FlashBrowser on Windows should later reuse the Win32 driver; macOS and Linux would need a driver built on the Chrome DevTools Protocol (CDP).

| Browser | Engine | Platforms | Window the bot targets | Input | Capture | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Coowon | Chromium-based with built-in Flash (v1.8.0's browser type 2) | Windows | The top-level `Chrome_WidgetWin_1` window itself. Verified live on 2026-10-06: this Coowon build (`%LOCALAPPDATA%\Coowon\Coowon\Application\chrome.exe`) has **no** `Chrome_RenderWidgetHostHWND` child | `PostMessage` mouse and key messages to the top-level window, client coordinates | `PrintWindow` with `PW_RENDERFULLCONTENT` on the top-level window | MVP; background capture and background click both verified live |
| [FlashBrowser](https://github.com/radubirsan/FlashBrowser) | Electron 9.4.4 plus the Pepper Flash plugin you supply | Windows, macOS, Linux | Windows: the same render widget. macOS and Linux: no window handle available | Windows: `PostMessage`. Any OS: CDP `Input.dispatchMouseEvent` | Windows: `PrintWindow`. Any OS: CDP `Page.captureScreenshot` | Post-MVP; CDP path not yet tested |

What this changes in the design:

- **The origin is never 0,0.** In both browsers the game canvas sits inside a web page, so the origin is the canvas's top-left corner inside the render widget. v1.8.0's browser type 1 shortcut is dropped, and the canvas locator finds the canvas automatically (next section).
- **The MVP ships only the Win32 drivers.** The `InputDriver` and `Capturer` interfaces stay, so a `Cdp` driver can be added after the MVP without touching feature code.
- **CDP is post-MVP and unproven.** Pepper Flash may ignore synthetic CDP input in Electron 9 (Chromium 83). If it does, macOS and Linux fall back to OS-level input and screen capture, which needs the game window in front.
- **Page zoom changes the scale.** Chromium page zoom resizes the canvas, so `CoordMap` computes its scale factor per browser and per zoom level.
- **Post-MVP: run FlashBrowser isolated.** Its README warns that Electron 9.4.4 and Flash are end-of-life. Bind the debugging port to localhost only.

## Finding the game screen automatically

The user never sets the root coordinate (*Tọa độ gốc*) or presses F9. The bot finds the Coowon window, then locates the 1000×600 game canvas inside it on every capture. The 7 reference screenshots show why this is needed: the canvas's top-left corner moved between (270, 106) and (273, 112) from one capture to the next (measurements in [07 Screen catalogue](07-screen-catalogue.md)).

1. **Find the window.** Enumerate visible top-level `Chrome_WidgetWin_1` windows whose process path contains `\Coowon\` (the executable is named `chrome.exe`) and whose title contains "Ngọa Long" (for example "Sv 266 Ngọa Long Việt Nam - Coowon Browser"). Target that top-level window directly: it has no render-widget child. With several matches, list them in the UI by title; pick-under-cursor stays as a manual fallback only. The live window measured 1349×1391 client px with the canvas at about (180, 110); `tools/live/livectl.exe` is a reference implementation of find + capture + click.
2. **Locate the canvas.** Run three detectors on the capture and combine them:
    - **Anchor templates** (primary): HUD pieces with fixed canvas positions. The chat input bar "K.Vực" at the bottom-left is visible in all 7 screenshots, and the top-right gear, sound and music icons in 6. `origin = match − anchor offset`; two anchors also give the scale.
    - **Black-border box**: the page background is pure black, so the bounding box of non-black rows and columns is the canvas on bright scenes. Accept it only when its size is 1000×600 × scale, ±2 px. It found the canvas on 6 of 7 screenshots and failed on the dark tower battle (668×598).
    - **Layout prior**: the canvas is centred horizontally and top-aligned in the page. Used to place search regions, never on its own.
3. **Validate.** Accept a rectangle when two detectors agree within 3 px. Otherwise keep the last good rectangle, mark it low-confidence, and retry on the next capture.
4. **Track.** Re-run on window move or resize (WinEvent `EVENT_OBJECT_LOCATIONCHANGE`), on scene change, and every 10 s. Cache the result between runs.
5. **Scale.** `scale = canvas width / 1000`. All offsets in `coords.yaml` and `scenes.yaml` are canvas-relative at 1000×600, so page zoom works without re-recording.

## Scene recognition

Each scene is declared in `profiles/ngoalongviet/scenes.yaml`: a name, signature anchors that must all match, and the objects it contains (template or fixed offset, search region, threshold). `SceneDetector` checks modal dialogs first, then full screens, and returns the scene name with a score. Features wait on scenes instead of blind sleeps: `ctx.expect_scene("tower.confirm", timeout_s=5)` replaces v1.8.0's `Sleep` + hope. The first 7 scenes (`city`, `field`, `world_map`, `tower.dialog`, `tower.confirm`, `tower.battle`, `chest.wheel`) and their objects are listed in [07 Screen catalogue](07-screen-catalogue.md).

## Pop-up guard

The game opens reward and notice pop-ups on its own, on top of whatever a feature is doing. On 2026-10-06 a "Chúc mừng ngài được … M.C+4500" reward modal opened over a tower battle, and the run waited for the battle result until its time limit. Since then, `RunContext.observe()`, which every `find`, `wait_for`, `expect_scene` and `detect_scene` call goes through, asks the `PopupGuard` (`ngoalong/engine/popups.py`) about each captured frame first:

1. The guard checks the pop-ups listed in `profiles/ngoalongviet/popups.yaml`. Each entry has a signature template (for example the title "Chúc mừng ngài được") and a close button (its 确定 or ✕), searched near where they were cut. The button region moves with the signature, so a taller box still works.
2. When one is shown, the context clicks the close button, logs "Tự đóng popup: …", counts it in the run history (`popups`), waits 0.6 s and captures again. It closes at most 3 pop-ups per capture, so the bot can't fall into a click loop.
3. The feature then sees the screen underneath. No feature code is involved.

**Only harmless pop-ups are listed**: boxes whose only button closes them. Dialogs a feature answers itself (`tower.confirm`, battle results, buy or exchange boxes) must never go in `popups.yaml`, and a test enforces this for the tower ones. Listed today: `reward` (title + 确定) and `toast` (the corner notice: "Xem ngay" + ✕). A new pop-up is added by cutting its title and close button as templates and adding an entry, with no code change.

**Pop-ups with a cost are answered by a setting.** An entry with a `policy` names an accept button (`dismiss`) and a decline button (`decline`), and the guard reads `Settings.game.<policy>` at the moment the question appears. On: it clicks accept and the run goes on, counting the answer in the run history. Off: it clicks decline and the run ends as **stopped** (`TaskStopped`) with the entry's `stop_message`. The only such entry today is `gold_skip_wait`: "Bỏ qua thời gian chờ tốn N Vàng. Xác nhận?". It appears after "Tăng cấp" and similar actions when the game's warning is enabled (the HUD gear setting), and the first line varies per task. It is recognised by the unchanging "☐ Lần sau không nhắc nhở" line plus both buttons. Setting: **Cài đặt → Trong game → "Dùng vàng bỏ qua thời gian chờ"**, off by default: 确定 (left) when on; 取消 (right) and stop when off. The bot never ticks "Lần sau không nhắc nhở", since that would let the game spend Vàng without asking.

## Debug mode and overlay

Debug mode draws what the bot sees: one rectangle per object it looked for, green if found and red if not. It is off by default. It is switched on by `debug = true` in `settings.toml`, a checkbox in the UI, or Ctrl+Alt+D.

![Debug overlay mock-up on tower-ui-enter-confirmation.png](images/debug-overlay-mockup.png)

*Illustrative mock-up, drawn on a real screenshot; scores and the window id are sample values.*

| Element | Drawn as | Label |
| --- | --- | --- |
| Game canvas | Cyan rectangle; red when low-confidence | size, origin, scale |
| Object found | Green rectangle around the match | `name score` |
| Object expected but not found | Red rectangle around the search region | `name best < threshold` |
| Planned click | Yellow crosshair | — |
| Status line | Text under the canvas | scene and score, window, task, step, next click |

How it works:

- **One data model, three outputs.** Every `Matcher.find`, `wait_for` and `expect_scene` call records a `Detection` (name, rectangle, score, threshold, found) into a `DebugFrame`, together with the canvas, the scene and planned clicks. The overlay is a by-product of normal detection, not separate code. Three sinks render the same `DebugFrame`:
    1. **Live overlay window**: a frameless, transparent, click-through, top-most Qt window (`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`) kept on top of the render widget's screen rectangle. Visible only while the game is on screen; the bot itself keeps working in the background.
    2. **Debug panel** in the app: the last capture with the same annotations, so debugging works while the game is behind other windows.
    3. **Annotated PNGs**: in debug mode every step is saved to `logs/run-<id>/NNN-<step>.png`. On any failure the last frame is always saved, debug mode or not.
- **Offline testing.** `FileCapturer` replays screenshots instead of a live window. `python -m ngoalong.tools.annotate "docs/game screenshots/*.png" --out build/annotated` runs the canvas locator and scene detector on stored screenshots and writes annotated copies. `pytest` golden tests assert the canvas rectangle and scene name for each reference screenshot, with expected values from [07](07-screen-catalogue.md).
- **No cost when off.** With debug mode off, detections are still recorded (they are cheap) but nothing is drawn or saved except failure frames.

## Stack

| Concern | Choice | Why |
| --- | --- | --- |
| Runtime | Python 3.12, 64-bit; Windows only for the MVP | The MVP targets Coowon, which is Windows-only, and the proven PostMessage path is Win32. |
| Win32 access | `pywin32`, plus `ctypes` for `PrintWindow` and `RegisterHotKey` | Direct equivalents of every AutoIt call used today |
| CDP (FlashBrowser, post-MVP) | A small raw DevTools Protocol client on websocket-client. FlashBrowser is launched with --remote-debugging-port bound to localhost. | Background input and screenshots on all three operating systems, if Pepper Flash accepts CDP input (post-MVP spike) |
| Capture | `PrintWindow(hwnd, PW_RENDERFULLCONTENT)` into a NumPy array | Works when the window is covered, and handles Chromium surfaces. DCs are freed in `finally` blocks, which fixes #1. |
| Vision | OpenCV `matchTemplate` (`TM_CCOEFF_NORMED`), per-template threshold, region of interest and mask, templates cached in memory | Tolerates colour drift. Returns the centre and a score. Fixes #3 and #14. |
| Debug overlay | Qt transparent click-through window for live view; OpenCV `rectangle`/`putText` for the panel and saved PNGs | Same `DebugFrame` drawn three ways; no extra dependency |
| UI | PySide6 (Qt 6), Vietnamese UI only | Threaded runs with signals for log, progress and state. Fixes #7. |
| Config | `pydantic` models saved as TOML (user settings); YAML for offsets and flows | Validated inputs (#15), readable files, and versioned profiles |
| Hotkeys | `RegisterHotKey` on a message thread, configurable, with a modifier by default (Ctrl+Alt+F10 = stop) | No more stolen F-keys (#8) |
| Packaging | PyInstaller one-folder build, later Nuitka | Simple distribution. The templates folder stays editable. |
| Quality | `pytest`, screenshot fixtures for vision, a fake input driver that records clicks | Flows can be tested without the game running |

## Module layout

```text
ngoalong/
  app.py                 # entry point: Qt app, DI wiring
  config/
    settings.py          # pydantic models, load/save settings.toml, migrate NgoaLong_data.ini
  core/
    window.py            # GameWindow: auto-discover Coowon render widget, bind by (pid, exe, class, title), re-find, liveness
    locator.py           # CanvasLocator: anchors + black-border box + layout prior -> canvas rect, scale, confidence
    scenes.py            # SceneDetector: loads scenes.yaml, returns current scene + score
    input.py             # InputDriver interface: click/double/right, key down+up, text
    capture.py           # Capturer interface, frame cache, debug dumps
    drivers/
      win32.py           # Win32InputDriver (PostMessage) + Win32Capturer (PrintWindow): Coowon (MVP); FlashBrowser on Windows later
      cdp.py             # CdpInputDriver + CdpCapturer over DevTools Protocol: FlashBrowser on macOS/Linux (post-MVP)
      file.py            # FileCapturer: replays screenshots for offline tests and the annotate tool
    vision.py            # Matcher: find / find_any / wait_for, threshold, ROI, mask, centre point
    coords.py            # CoordMap: origin + offset * scale; loads profiles/<name>/coords.yaml
  engine/
    context.py           # RunContext: click(key), find(name), wait_for(name, timeout), sleep(ms) - all cancellable
    task.py              # Task base class: params model, run(ctx), on_stop
    runner.py            # TaskRunner QThread: start/pause/stop, progress + log signals, crash screenshots
  features/              # one module per feature, registered by decorator
    quay_ruong.py  mo_the_bai.py  khoa_ky.py  cong_trinh.py  chua_cong.py
    thap_thi_luyen.py  clear_map.py  tham_bao.py  vo_khoi_thap.py  tri_ky.py  autoclick.py
  debug/
    model.py             # Detection, DebugFrame
    render.py            # draw a DebugFrame onto an image (green / red / cyan / crosshair / status line)
    overlay_window.py    # transparent click-through Qt window glued to the render widget
    sink.py              # DebugSink: live overlay, debug panel, PNG files
  tools/
    annotate.py          # CLI: run locator + scene detector on screenshots, write annotated PNGs
  ui/
    main_window.py  tabs/  widgets/log_view.py  widgets/debug_panel.py
  hotkeys.py
profiles/
  browsers.yaml          # per browser: window class rules, driver (win32 | cdp), launch flags, zoom
  ngoalongviet/
    coords.yaml          # canvas-relative offsets at 1000x600
    scenes.yaml          # scenes: signature anchors + objects (template | offset, region, threshold)
    templates/**         # cut from docs/game screenshots
tests/
  fixtures/screens/      # = docs/game screenshots, with expected canvas rect + scene per file
```

## Core interfaces

```python
class GameWindow:
    @classmethod
    def discover(cls) -> list["GameWindow"]: ...  # Coowon render widgets whose title contains "Ngoa Long"
    def pick_under_cursor(self) -> None: ...      # manual fallback only
    def rebind(self) -> bool: ...                 # find again by pid/exe/class/title
    def is_alive(self) -> bool: ...
    def client_size(self) -> tuple[int, int]: ...

class CanvasLocator:                               # replaces the root coordinate (Tọa độ gốc) and F9
    def locate(self, frame: np.ndarray) -> CanvasFix: ...   # rect, scale, confidence, detectors that agreed

class SceneDetector:
    def detect(self, frame: np.ndarray, canvas: CanvasFix) -> SceneMatch: ...  # name + score

@dataclass
class Detection:                                   # recorded by every find / wait_for / expect_scene
    name: str; rect: Rect; score: float; threshold: float; found: bool

class DebugSink(Protocol):                         # live overlay, debug panel, PNG files
    def publish(self, frame: DebugFrame) -> None: ...

class InputDriver(Protocol):                       # Win32InputDriver | CdpInputDriver
    def click(self, p: Point, button: str = "left", double: bool = False) -> None: ...
    def key(self, vk: int) -> None: ...            # key down + key up
    def text(self, s: str) -> None: ...           # WM_CHAR per character (Win32) / Input.insertText (CDP)

class Capturer(Protocol):                          # Win32Capturer | CdpCapturer
    def grab(self) -> np.ndarray: ...             # BGR frame of the render widget / page

class Matcher:
    def find(self, name: str, roi: Rect | None = None, threshold: float | None = None) -> Match | None: ...
    def find_any(self, names: list[str]) -> Match | None: ...

class RunContext:                                  # what every feature receives
    def click(self, target: str | Point | Match) -> None: ...   # "tower.open" resolves through CoordMap
    def wait_for(self, name: str, timeout_s: float) -> Match: ... # raises Timeout; checks cancel
    def expect_scene(self, scene: str, timeout_s: float) -> SceneMatch: ...
    def sleep(self, ms: int) -> None: ...                         # raises Cancelled when Stop is pressed
    def progress(self, done: int, total: int | None) -> None: ...
```

Each feature becomes a `Task` subclass with a pydantic `Params` model, so the UI form, validation and saved settings all come from one definition.

## Old to new mapping

| v1.8.0 | Python replacement | What changes |
| --- | --- | --- |
| `MOUSE_POS` (F9), `HWND_GAME`, `TITLE_ACTIVE` | `GameWindow.discover` and `rebind` | Finds the Coowon window automatically and binds by process and window class, not a raw handle. Never captures its own window. Fixes #5 and #6. |
| Root coordinate `TOADO_GOC` (*Tọa độ gốc*) | `CanvasLocator` | Found on every capture from anchors and the black border; no user step, follows window moves and zoom |
| `ImageSearchEx` results with no feedback | `Detection` records + `DebugSink` | Debug mode shows every search as a green or red rectangle, live and in saved PNGs |
| `ACP_GETPOS` (F2) | `autoclick` task with its own target | No longer overwrites the game window. Fixes #4. |
| `LVS_MOUSECLICK`, `ACP_BUTTON` | `InputDriver.click` | One code path. Click type honoured. Fixes #10. |
| `_LVS_SEND_TEXT` plus `ControlSend` | `InputDriver.key` and `InputDriver.text` | Proper key-down/up and `WM_CHAR`, no doubled keys. Fixes #9. |
| `IMAGESEARCHEX*` and `_BMPSEARCH` shellcode | `Capturer` plus `Matcher` | No leaks, a score threshold, centre point, caching. Fixes #1, #3, #14. |
| `While … And ON == True`, `Sleep` | `ctx.sleep` and `ctx.wait_for(timeout)` | Every wait can be stopped and has a time limit. Fixes #2. |
| Hard-coded `X_GOC + n` | `profiles/*/coords.yaml` via `CoordMap` | Editable without rebuilding. A scale factor handles zoom. |
| `[PRO5]` INI | `settings.toml` with a one-time INI importer | Typed, validated, saved continuously |
| GUIGetMsg loop and MsgBox at the end | Qt UI, `TaskRunner` thread, log panel, tray notification | UI stays responsive, with Start, Pause and Stop per run. Fixes #7. |
| IE ad banner, blog update scrape | Removed, or an optional GitHub Releases check | Fixes #11 |

## Roadmap

Build in five phases. Each phase ends at a gate that must be checked against the real game before the next one starts, so the parity work (P0–P2) finishes before new features (P3).

| Phase | Scope | Exit gate |
| --- | --- | --- |
| **P0 · Foundations (start here)** | GameWindow discovery, CanvasLocator, SceneDetector (7 known scenes), InputDriver, Capturer, Matcher, debug overlay + annotate tool, TaskRunner, settings + INI import. First working tasks: AutoClick and Quay rương on Coowon (Windows). | Canvas found within 2 px and scene named correctly on all 7 reference screenshots; 1-hour soak with a flat GDI-handle count; Stop ends any run within 1 s |
| P1 · Offset-only features | Mở thẻ bài, Khoa kỹ, Công trình, Chúa công, Quay rương, Tháp Thí Luyện. Offsets loaded from coords.yaml, with a zoom scale factor. | Parity checklist against v1.8.0 passed, feature by feature |
| P2 · Template-driven features | Clear Phó Bản, Thám Bảo, Võ Khôi Tháp, Tri Kỷ. Templates captured on ngoalongviet.com, thresholds tuned, every wait time-limited. | Each runs 30 minutes with no stuck loop; every miss logged with a screenshot |
| P3 · New features | Scheduler and routine chains, multiple windows, template capture tool, notifications, run history, FlashBrowser | Beta testers run their daily routines for one week |
| P4 · Release | PyInstaller build and installer, update check through GitHub Releases, user guide in Vietnamese | — |

P0 replaces the parts behind the high-severity bugs (#1–#4), so every later phase is built on fixed foundations.

## Implementation status (2026-10-06)

Built phase by phase; every phase has offline pytest coverage (stored screenshots through `FileCapturer`, clicks recorded by a fake `InputDriver`). Live checks are opt-in (`NGOALONG_LIVE=1`).

| Phase | Built | Where |
| --- | --- | --- |
| P0 · Foundations | Coowon window discovery and rebinding (top-level `Chrome_WidgetWin_1`), Win32 `PostMessage` input and `PrintWindow` capture with DCs freed in `finally`, `CanvasLocator` (anchors + black border + layout prior), `SceneDetector` (35 scenes, all golden screenshots pass), `Matcher`, `CoordMap`, debug model + overlay window + panel + PNG sink, annotate tool, `TaskRunner` with stop/pause, settings + one-time INI import, configurable hotkeys (Ctrl+Alt+F10 stop, Ctrl+Alt+D debug), Vietnamese Qt UI with forms generated from `Params`. Features: AutoClick, Quay rương | `ngoalong/core`, `debug`, `engine`, `ui`, `config`, `hotkeys.py`, `tools/annotate.py` |
| P1 · Offset-only features | Mở thẻ bài, Nâng cấp khoa kỹ, Nâng cấp công trình, Cày cấp chúa công, Tháp Thí Luyện (shared tower steps), each checking its screen before clicking | `ngoalong/features/*.py`, `profiles/ngoalongviet/coords/*.yaml` |
| P2 · Template-driven features | Clear Phó Bản, Thám Bảo, Võ Khôi Tháp, Tri Kỷ; templates cut reproducibly (`template_crops.yaml`, `tools/cut_templates.py`), every wait time-limited | `ngoalong/features`, `profiles/ngoalongviet/scenes`, `templates` |
| P3 · New features | Routine chains + scheduler, multiple windows, template capture tool, notifications (tray, Telegram, Discord), run history, Thám Bảo x2 reward | `engine/scheduler.py`, `engine/manager.py`, `engine/history.py`, `notify.py`, `tools/template_tool.py`, `ui/tabs/*` |
| P4 · Release | PyInstaller one-folder build (`AutoNgoaLong.spec`, `build.ps1`) with `profiles/` next to the exe; `--selftest` (profile, features, 15 bundled golden screenshots, offscreen main window; exit code 0/1); app paths for source and build (`ngoalong/paths.py`); Vietnamese user guide; developer README | `AutoNgoaLong.spec`, `build.ps1`, `ngoalong/selftest.py`, `docs/user-guide-vi.md`, `README.md` |

Deferred or still open:

- **FlashBrowser / CDP**: only the empty `CdpInputDriver` / `CdpCapturer` stub (`core/drivers/cdp.py`); no `profiles/browsers.yaml`; macOS and Linux unsupported.
- **P4 leftovers**: no installer (distribute the zipped `dist\AutoNgoaLong\` folder), no GitHub Releases update check, no code signing (SmartScreen warns), Nuitka not tried.
- **Not yet verified on ngoalongviet.com**: screens that still use v1.8.0 images or have no scene signature: Thám Bảo, Tri Kỷ portraits, the Võ Khôi Tháp fight screens after "Tấn công", the card-drawing screen (Mở thẻ bài) and the Hải cảng screen.
- **Live exit gates**: the roadmap's live gates (P0 1-hour soak, P1 parity checklist, P2 30-minute runs, P3 beta week) need the real game; the offline equivalents (GDI-leak regression, golden screenshots, stop latency, bounded loops) are in the test suite.
- **OCR** (added 2026-10-06 for Chạy Thành Tựu): `ngoalong/core/ocr.py`, RapidOCR (PaddleOCR PP-OCRv6 on ONNX Runtime). Cross-platform, pip only, models bundled in the wheel. `RunContext.read_text(region)` returns lines in canvas coordinates; names are compared with `normalize` + `similarity` because accented letters get dropped. Windows.Media.Ocr was tried and rejected because it is Windows-only.
- **Later ideas**: OCR of token and silver counts (the engine is in place); template profiles per server or website.

## New features to add after parity

- [x] **Routine chains and scheduler**: run a saved sequence such as Thám Bảo → Boss → Trial Tower at set times, with retries. (P3: "Chuỗi tác vụ" tab, `engine/scheduler.py`.)
- [x] **Multiple windows**: one `TaskRunner` per bound game window, so several accounts run in parallel. The target server allows bots. (P3: "Nhiều cửa sổ" tab, `engine/manager.py`.)
- [ ] **Template profiles per server or website**: delivers the selector that v1.8.0 promised but never implemented.
- [x] **Template capture tool**: drag a rectangle on a live capture to save a template, then test its match score on the spot. (P3: "Mẫu ảnh" tab, `ngoalong/tools/template_tool.py`; each save also writes a `template_crops.yaml` line and a scene entry.)
- [x] **Notifications**: tray alerts plus optional Telegram or Discord messages on finish, stop or error, including a screenshot. (P3: "Thông báo" tab, `notify.py`.)
- [x] **Run history**: logs, counters (spins, floors, bosses), and failure screenshots saved per run. (P3: "Lịch sử" tab, `engine/history.py`.)
- [x] **Thám Bảo x2 reward**: implement the option the checkbox already advertises, using `TB_QuaX2.bmp`. (P3: "Nhận quà x2", template `tham_bao.reward_x2`; still the v1.8.0 image until the screen is captured live.)
- [x] OCR engine (RapidOCR), used by Chạy Thành Tựu.
- [ ] **Later**: read token and silver counts with it, so runs stop on resources instead of fixed counts.
- [ ] **FlashBrowser support**: Windows first through the existing Win32 driver, then macOS and Linux through a CDP driver once a spike shows Pepper Flash accepts CDP input.

## Decisions (2026-10-06)

- **Platform:** Windows only for the MVP, on Coowon. FlashBrowser, macOS and Linux are out of scope for the MVP.
- **Server:** ngoalongviet.com, game page `http://www.ngoalongviet.com/UserCenter/mjwly.php`. Its templates still need capturing, before P2 starts.
- **UI language:** Vietnamese only.
- **Licence:** the original author was asked; the v1.8.0 code has no licence.
- **Bot policy:** the target is a private server of a legacy Flash game that allows bots, so multi-account features are acceptable.

No open questions remain.
