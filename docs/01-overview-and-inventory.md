# 01 · Overview and package inventory

What AutoNgoaLong Free v1.8.0 is, how it was reverse engineered, and what ships in the package. Index of all parts: [README.md](README.md)

## Overview

AutoNgoaLong Free v1.8.0 is a compiled **AutoIt v3** script that plays the Flash webgame *Ngọa Long* by sending fake mouse and keyboard window messages to the game window. It finds those click points in two ways: fixed pixel offsets from a user-picked origin, and exact-match BMP template search.

**How it was reverse engineered.** Static analysis only. The `.exe` was never run.

1. Fingerprinted the binary: AutoIt v3 runtime with an `EA06` script resource. No UPX, .NET, or other packer.
2. Located the script blob at file offset `0xD6710` (878352). Its magic header `A3484BBE…AU3!EA06` is followed by 16 bytes of padding, then encrypted `FILE` records.
3. Decrypted the records with AutoIt's LAME stream cipher, decompressed them (EA06 LZ), and de-tokenised the bytecode back into source. This used a C# port of the open-source [AutoIt-Ripper](https://github.com/nazywam/AutoIt-Ripper), compiled with the `csc.exe` that ships with Windows.
4. Result: **18,119 lines** of source with a valid checksum. About 16,200 lines are bundled standard UDFs (WinAPI, GDI+, Array, Inet, GUI). About 1,900 lines (lines 16,206–18,119) are the application itself.

The embedded build path shows the original author compiled it on a machine user `PHANTOM0803`. The status bar credits *Lê Văn Sơn*.

## Package inventory

The package is one 1.2 MB executable, one INI file, and 79 template images. No DLLs or other code ship with it.

| Item | Size | What it is |
| --- | --- | --- |
| `AutoNgoaLong-V1.8.0.exe` | 1,198,080 B | AutoIt v3.3.x runtime (32-bit) plus the encrypted EA06 script resource |
| `NgoaLong_data.ini` | 661 B | All saved settings, in a single `[PRO5]` section (see [04](04-data-and-configuration.md)) |
| `Server\default\All\Images\**` | 79 files | Template images for on-screen detection, mostly 32 bpp and some 24 bpp |
| `NgoaLongBrowser.exe` | not shipped | Launched by the **W** button. It is missing from this package, so that button fails silently. |

**Template folders and which code uses them**

| Folder | Files | Used by | Unused files |
| --- | --- | --- | --- |
| `VoKhoiThap` | 8 | Boss (Võ Khôi Tháp) | `VKT_CuaHang.bmp` |
| `Boss` | 4 | nothing (duplicates of `VoKhoiThap`) | all 4 |
| `ClearMap` | 12 | Clear Phó Bản | `TatChuaCongLenCap.bmp` |
| `ThamBao` | 9 | Thám Bảo | `TB_NhanThuong`, `TB_QuaX2`, `TB_ThemLenh` |
| `ThapThiLuyen` | 5 | Tháp Thí Luyện | none. `TTL_Thang2.bmp` is really a PNG renamed to `.bmp`, which GDI+ still loads |
| `TriKy\Tim`, `TriKy\Cam` | 19 + 21 | Tri Kỷ search. The file name is the checkbox index (`0.bmp` to `20.bmp`). | none |
| `ThuongNhan` | 0 | nothing (empty) | n/a |
| `LayGoc.bmp` | 1 | nothing | 1 |

**Runtime dependencies inside the script:** `user32` (PostMessage, PrintWindow, WindowFromPoint), `gdi32`, `gdiplus` (loading BMP files), `crypt32` (decoding the embedded search shellcode), the IE `Shell.Explorer.2` ActiveX control (ad banner), and WinINet (the update check).

## Recovered files (archived)

The original v1.8.0 package and the recovered files were removed from the project after the rewrite. They are archived in `AutoNgoaLong-v1.8.0-legacy.zip`, next to the project folder, under `reverse/`:

- `AutoNgoaLong-v1.8.0.decompiled.au3`: the full recovered source, 18,119 lines.
- `app_logic_only.au3`: only the app logic and the BmpSearch core, about 2,000 lines.
- `coordinates.yaml`: every `(dx, dy)` offset, grouped by feature. The same offsets now live in `../profiles/ngoalongviet/coords/`.
- `au3_ea06_extractor.cs`: the extractor used, so the next version can be unpacked the same way.
