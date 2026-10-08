# 05 · Known issues (v1.8.0)

Bugs and weaknesses found in the recovered source. Issue numbers #1–#17 are referenced from [06 Rewrite design](06-rewrite-design.md). Index of all parts: [README.md](README.md)

The code review found 17 problems. Four are severe enough to hang a run or slowly break it: a GDI handle leak in every image search, three stop-proof loops, exact-pixel templates, and the F2 key retargeting the game window.

| # | Severity | Problem | Where | Effect |
| --- | --- | --- | --- | --- |
| 1 | High | GDI leak. On a miss, the search returns before `ReleaseDC`, `DeleteDC`, `DeleteObject` and `GdiplusShutdown`. The bitmap loaded from file is never freed on any path. | `IMAGESEARCHEXAREA` | Every failed poll (up to 10 per second) leaks a DC and two bitmaps. Long runs reach the 10,000 GDI-object limit, and capture or the GUI starts failing. |
| 2 | High | Loops with no `ON` check: `While CUAHANGXUONG = 0`, `While CUAHANGRUONG = 0`, `While KETQUA == 0` | `BOSS_CHECKFULLBAC`, `CLEARMAP` | If a template never appears, the run hangs. F10 can't stop it, only F4 (quit). |
| 3 | High | Templates match only exact pixels, and the click lands on the template's top-left corner | `_BMPSEARCH` plus all callers | Any game re-skin, browser zoom, DPI change or anti-aliasing breaks detection. Clicks land on edges. |
| 4 | High | F2 (record a click point) assigns the window under the cursor to the global `HWND` | `ACP_GETPOS` | Recording a point outside the game silently sends every later feature to the wrong window. |
| 5 | Medium | `TITLE_ACTIVE` stores whichever window is active when F9 is pressed, which is usually the bot itself. Your INI holds the bot's own title. | `MOUSE_POS` | `WinActivate(TITLE_ACTIVE)` brings the bot to the front, not the game. |
| 6 | Medium | The window handle is saved as a number and restored on the next launch | `_LOADDATA` | After a restart the handle is either invalid or reused by another window, so clicks go to that window. |
| 7 | Medium | Each feature runs synchronously inside the GUI event loop | main `While` loop | The window freezes. There is no progress display, no pause, and no Stop button; only hotkeys work. |
| 8 | Medium | Hotkeys F1–F4 and F8–F10 are registered for the whole system | `HotKeySet` | They override those keys in every app. F1 erases the click list and F4 quits the bot. |
| 9 | Medium | Key injection sends `WM_KEYDOWN` with no key-up and no `WM_CHAR`. `{ESC}` sends only the key-up. Each key is often sent twice, by `ControlSend` as well. | `_LVS_SEND_TEXT` | Text and ESC handling work only by luck of Flash quirks. Typed quantities can be dropped or repeated. |
| 10 | Medium | The AutoClick click-type combo (`COMBO1`) is never read | `ACP_AUTOCLICK` | Every AutoClick is a left click, whatever the user chose. |
| 11 | Medium | Remote content runs inside the app: an ad page over plain HTTP in the legacy IE engine, plus a blog scrape for updates | startup | It slows startup and puts web content in the process. The update dialog blocks offline users. |
| 12 | Medium | The W button runs `NgoaLongBrowser.exe` from the working directory, but that file isn't shipped | main loop | The button does nothing. Any file with that name in the working directory would be executed. |
| 13 | Low | `WinActive(HWND)` is used where `WinActivate` was meant | 6 feature functions | It does nothing. |
| 14 | Low | Every search re-reads the BMP from disk, restarts GDI+, and captures the whole window | `IMAGESEARCHEXAREA` | It wastes CPU and makes polling slow. |
| 15 | Low | Inputs aren't validated: empty fields or placeholder text such as "Số lượng" become 0 | all buttons | A run ends instantly or does the wrong number of loops, with no error shown. |
| 16 | Low | The Thám Bảo *x2* checkbox is passed in but never used. The website/server choice is still promised in the notice, but `_GET_PATCH_IMAGES` is empty. | `THAMBAO`, notice | Features advertised in the UI don't exist. |
| 17 | Low | Tri Kỷ "attempts" counts companions found, not searches | `TRIKY` | If no selected portrait ever appears, it refreshes forever. |
