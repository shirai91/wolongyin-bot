# 04 · Data and configuration (v1.8.0)

The INI settings file, the template images, and the coordinate model. Index of all parts: [README.md](README.md)

All state lives in one flat INI section, plus about 110 hard-coded click offsets in the source. The offsets are written out per feature in `coordinates.yaml` (archived; see 01), now split per feature into `../profiles/ngoalongviet/coords/`.

## `NgoaLong_data.ini`, section `[PRO5]`

The app reads these keys at startup and writes them all back on window close. F2, F9 and the start-up notice also write a few of them immediately.

| Key | Bound to | Meaning | Your current value |
| --- | --- | --- | --- |
| `TOADO_GOC` | origin label | Game origin `XxY` in client pixels | `17x82` |
| `HWND_GAME` | global `HWND` | Raw window-handle number. Invalid after a restart. | `0x0033099C` |
| `TITLE_ACTIVE` | Tab Click field | Window title used by `WinActivate` (UTF-8 stored through ANSI) | v1.8.0's own title |
| `TRINHDUYET` | browser combo | `1` = Tango/NgoaLongBrowser (origin forced to 0,0), `2` = Chrome family | `1. Tango…` |
| `QR_SOLUONG`, `QR_TOCDO` | Chest spin | Number of spins, delay in ms | 200, 5 |
| `CAPDO`, `NCCT_SLEEP` | Buildings | Upgrades per building, delay in ms | 10, 500 |
| `CCCC_QUANLENH`, `CCCC_SLEEP`, `CCCC_TANGTHAP` | Lord XP farm | Tokens, delay in ms, tower floor | 1000, 500, 6 |
| `MTB_SOLUONGTHE` | Open cards | Quantity | 100 |
| `TTL_INPUT_BATDAU`, `TTL_INPUT_KETTHUC` | Trial Tower | Start and end floor | 1, 5 |
| `CPB_INPUT_MS`, `CPB_INPUT_S` | Dungeon clear | Click delay in ms, template wait in s | 200, 3 |
| `BOSS_ID`, `BOSS_SLMUA`, `BOSS_SLEEP`, `BOSS_FULLBAC` | Tower boss | Boss label, chests to buy, delay in ms, full-silver policy | `3. Bệ Ngạn`, 200, 500, `1. Bỏ qua…` |
| `ACP_LIST_TOADO` | AutoClick list | Points as `XxY\|XxY\|`. The trailing `\|` is load-bearing. | `492x493\|` |
| `ACP_INPUT_S`, `ACP_INPUT_MS`, `ACP_LABEL_X`, `ACP_LABEL_Y` | AutoClick | Interval, single point | 0, 100, 504, 416 |
| `SHOW_BEGIN_POPUP` | first-run notice | `1` hides it | 1 |
| `ACP_LIST_TOADO_DEM`, `WEBSITE_SELECT`, `SERVER_SELECT` | none | Leftovers from older versions. Cleared or ignored. | S16, NgoaLongViet.Com |

Combo values are stored as their full display label (for example `3. Bệ Ngạn ( lv 140 )`). The code later recovers the number by splitting on `.`.

## Templates

- Format: BMP files (one is a PNG), 5×9 px to 140×40 px. Each is loaded through GDI+ and compared as raw 32-bit pixels.
- Matching is **exact**: no colour tolerance, no scaling, and alpha bytes must match too. The 24 bpp files only work because GDI+ widens them to 32 bpp with an opaque alpha.
- The result is the top-left corner of the first match, in window coordinates. Callers click that corner or add hand-tuned offsets, such as `(-50, +20)` for *Tấn công*.
- Tri Kỷ depends on file names: `TriKy/Tim/<i>.bmp` maps to purple checkbox *i*, and `TriKy/Cam/<i>.bmp` to orange checkbox *i*.
- Which folder each feature uses, and which files are unused: see [01 Overview and inventory](01-overview-and-inventory.md).

## Coordinate model

`click_point = origin + offset`. Offsets assume the Flash canvas at 100% zoom, about 1000×600 (the largest offset used is `(960, 583)`). Window messages carry client coordinates, so any page zoom, Windows DPI scaling or canvas resize moves every target.
