# 03 · Workflows (v1.8.0)

Startup, window capture, the shared run pattern, and each feature's behaviour. Index of all parts: [README.md](README.md)

Every feature runs the same way. The user captures the game window and an origin once. Then a button starts a blocking routine that posts clicks at `origin + offset` and polls templates, until a counter runs out or **F10** clears the global `ON` flag.

## Startup

1. Build the 420×628 Koda GUI: a settings strip, four tabs, and a status bar.
2. `_LOADDATA` reads `NgoaLong_data.ini` into the inputs. It restores `HWND` from the saved number `HWND_GAME`, which is stale after any restart.
3. Embed an IE ActiveX ad banner from `http://tuyentapphim.net/NgoaLongADS/index.html`.
4. Register hotkeys: F1 clear click list · F2 record click point · F3 start/stop AutoClick · F4 quit · F8 hide/show game · F9 capture origin · F10 start/stop.
5. `_CHECK_UPDATE` scrapes `autongoalongfree.blogspot.com` for `<span id="version">`. On a mismatch or a network failure it shows a modal dialog.
6. Show the first-run notice unless `SHOW_BEGIN_POPUP=1`. Run `_CHECK_HWND_LIVE`, then enter the `GUIGetMsg` event loop.

## Capturing the game (F9, `MOUSE_POS`)

1. The user picks a browser type in the combo box, then hovers over the game.
2. `WindowFromPoint(cursor)` becomes the global `HWND`, and `ScreenToClient` gives the origin `(x, y)`.
3. If the browser type is **1 (Tango / NgoaLongBrowser)**, the origin is forced to `0x0`. In that browser the hovered window is the Flash plugin itself. For Chrome-family browsers, the origin is the canvas's top-left corner inside the render widget.
4. The routine saves `TOADO_GOC`, `HWND_GAME` and `TITLE_ACTIVE` (the active window title, used later by `WinActivate`).

## Running a feature (shared pattern)

1. Check that the origin label is not `Null`, and that the inputs needed are filled in (only some features check this).
2. Split `"XxY"`, set `ON = True`, check `IsHWnd(HWND)`, and call `WinActivate(TITLE_ACTIVE)`.
3. Call the feature function **synchronously** inside the GUI loop. The GUI freezes, and only hotkeys still work.
4. Clicks go through `LVS_MOUSECLICK`. It posts `WM_LBUTTONDOWN`/`UP` (0x201/0x202) with `lParam = y<<16 | x` to `HWND`. The real cursor never moves, so the game can sit in the background.
5. Keys go through `_LVS_SEND_TEXT`. It posts `WM_KEYDOWN` with `VK_*` codes, often doubled by `ControlSend`.
6. Vision goes through `ImageSearchEx`. It captures the window with `PrintWindow`, runs an exact 32-bit pixel match in embedded x86 shellcode (Beege's *BmpSearch*), and returns the **top-left** of the first hit.
7. The routine ends with a `MsgBox` ("Đã chạy xong", meaning "Finished").

## Feature catalogue

| Tab | Feature | Function | User inputs | How it finds targets | Stops when |
| --- | --- | --- | --- | --- | --- |
| Thành Chính | Open cards (Mở thẻ bài) | `MOTHEBAI` | card type 1–3, quantity, buy-first checkbox | offsets: back ×3, next ×(type−1), draw loop | count reached or F10 |
| Thành Chính | Research (Nâng cấp khoa kỹ) | `NANGCAPKHOAKY` | upgrades per tech | offsets: 3 rows × 6 techs, 75 px apart | all 18 done |
| Thành Chính | Buildings (Nâng cấp công trình) | `NCCT_THANHCHINH` / `NCCT_HAICANG` | upgrades, delay | offsets: 18 city or 6 harbour buildings | all buildings done |
| Thành Chính | Lord XP farm (Cày cấp chúa công) | `CAYCAPCHUACONG` | tokens, delay, tower floor | offsets: page to the floor, then sweep × ⌊tokens/3⌋ | loop count reached |
| Thành Chính | Chest spin (Quay rương) | `QUAYRUONG` | count, delay | offsets: 3 clicks per spin | count reached |
| Thành Chính | Trial Tower (Tháp Thí Luyện) | `THAPTHILUYEN` | start and end floor | offsets plus templates `TTL_Linh`, `TTL_Thang*`, `TTL_Thua*` | end floor reached, or a loss |
| Phó Bản | Dungeon clear (Clear Phó Bản) | `CLEARMAP` | click delay (ms), wait time (s) | templates: popups, `DaoQuan_1–3`, `Button_TanCong`, `Button_ChieuBinh*`, `Button_KetQua` | 4 misses in a row, or 5 failed recruits |
| Phó Bản | Treasure hunt (Thám Bảo) | `THAMBAO` | fight checkbox, x2 checkbox (ignored), delay | templates `TB_CanQuet*`, `TB_DoiThu`, `TB_KetQua`, `TB_MatThat`, `TB_DaNhanThuong` | F10 only |
| Phó Bản | Tower boss (Võ Khôi Tháp) | `BOSS_VOKHOITHAP` | boss 1–7, delay, chest auto-spin and buy, full-silver policy | offsets for navigation and attack, plus templates for out-of-tokens, chest, full silver and shop | F10, or policy 3 on full silver |
| Tri Kỷ | Companion search (Tri Kỷ) | `TRIKY` | 19 purple and 21 orange checkboxes, attempts | templates: one portrait per checkbox; talk, leave, then refresh | attempts reached |
| AutoClick Pro | Generic background clicker | `ACP_RUN` | recorded points, interval (s and ms), one or many points | points recorded with F2 | F3 toggle |

Brown buttons (colour `0x660000`) mark the template-based features. The first-run notice tells users to pick the matching website and server so those templates work. **v1.8.0 never reads `WEBSITE_SELECT` or `SERVER_SELECT`**: every lookup goes to `Server\default\All`, and the hook for switching template sets (`_GET_PATCH_IMAGES`) is an empty function.

Exact click offsets per feature: `coordinates.yaml` (archived; see 01). The rewrite keeps them in `../profiles/ngoalongviet/coords/`.
