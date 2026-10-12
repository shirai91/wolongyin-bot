# AutoNgoaLong v2

A bot for the Flash webgame **Ngọa Long** on the private server ngoalongviet.com, played in the **Coowon** browser on Windows. It clicks and captures the game **in the background**, so you can keep using the computer while it runs. It finds the game window and the 1000×600 game area by itself, looks at the screen before every click, and checks that each click worked.

Features: Nâng cấp khoa kỹ, Nâng cấp công trình (city and harbour), Clear Phó Bản, Tháp Thí Luyện, Quay Tinh Tú, Võ Khôi Tháp, plus AutoClick.

The full user guide is in Vietnamese: [docs/user-guide-vi.md](docs/user-guide-vi.md). It also ships inside the package as `HUONG-DAN.md`. To work on the code, read the [development guide](development-guide.md).

## What you need

| Requirement | Notes |
| --- | --- |
| Windows 10 or 11, 64-bit | The bot uses Windows APIs; it does not run on macOS or Linux. |
| Coowon browser | The game is Flash, which normal browsers no longer run. Coowon still does. The bot only works with Coowon (not Chrome or Edge). |
| A Ngọa Long account on ngoalongviet.com | Log in and enter your server in Coowon before starting the bot. |

Nothing else to install: Python, Qt, OpenCV and the Visual C++ runtime are all inside the package. The bot does not need administrator rights.

## Install

1. Open the [Releases page](../../releases/latest) and download `AutoNgoaLong-v<version>-win64.zip`.
2. Before extracting, right-click the zip → **Properties** → tick **Unblock** (if shown) → **OK**. This stops Windows from warning on every file inside (see below).
3. Extract the `AutoNgoaLong` folder somewhere you can write to, for example `D:\AutoNgoaLong`. Do not use `C:\Program Files`: the bot saves its settings, logs and history in its own folder.
4. Run `AutoNgoaLong.exe`.

The package contains:

| File / folder | What it is |
| --- | --- |
| `AutoNgoaLong.exe` | The bot |
| `_internal\` | Libraries the bot needs. Do not change or delete. |
| `profiles\ngoalongviet\` | How the bot recognises the game (screens, buttons, image templates). Editable with a text editor; no rebuild needed. |
| `HUONG-DAN.md` | The Vietnamese user guide |
| `VERSION.txt` | The version |

These are created on first run, next to the exe: `settings.toml` (your settings), `logs\` (a log and screenshots for each run) and `data\` (run history and task chains).

## Why Windows warns about it

When you first run `AutoNgoaLong.exe`, Windows may show **"Windows protected your PC"** (SmartScreen), or Microsoft Defender may block or quarantine the file. This is expected for this kind of program:

- **The exe is not code-signed.** Signing needs a paid certificate. SmartScreen warns about any unsigned program that few people have downloaded yet ("unknown publisher").
- **It is packed with PyInstaller.** The exe unpacks a bundled Python runtime. Some malware is packed the same way, so antivirus heuristics sometimes flag PyInstaller apps in general.
- **It controls another program's window.** The bot sends mouse clicks and keys to the Coowon window (`PostMessage`), takes pictures of it (`PrintWindow`) and registers global hotkeys (Stop = `Ctrl+Alt+F10`). This is what a game bot has to do, but it also matches what heuristic scanners look for.

What the bot does **not** do: it does not need admin rights, does not install anything, does not change system settings, and does not connect to the internet unless you turn on Telegram or Discord notifications in the **Thông báo** tab.

If you trust where you downloaded it from:

- **SmartScreen:** click **More info** → **Run anyway**. Windows remembers this for that file.
- **Defender blocks or deletes the exe:** open **Windows Security** → **Virus & threat protection** → **Protection history**, choose the item and **Allow on device**. To stop it coming back after updates, add the bot's folder under **Virus & threat protection settings** → **Exclusions** → **Add an exclusion** → **Folder**.

## First run

1. Open **Coowon** and enter the game. If Coowon shows "Adobe Flash Player was blocked", click **Run this time**.
2. Keep the page zoom at 100% (`Ctrl+0`). Other zoom levels work, but 100% is the best tested.
3. Start `AutoNgoaLong.exe` and press **Kiểm tra**. When the status shows `Khung game: … 1000×600 ×1.00 - tin cậy` and a `Màn hình: …` line, the bot can see the game.
4. Pick a feature in the **Tính năng** tab and press **Bắt đầu**. Press **Dừng** or `Ctrl+Alt+F10` to stop.

Keep the game window **not minimized and at least partly visible**: Windows does not draw a minimized window, so the bot cannot see it. Other windows may cover part of it.

## Upgrading

- **From v1.8.0:** copy your old `NgoaLong_data.ini` next to `AutoNgoaLong.exe` **before the first run**. The bot imports the settings it still uses once and saves them to `settings.toml`.
- **From an older v2 release:** extract the new version to a new folder and copy `settings.toml` and `data\` from the old folder into it. If you edited anything in `profiles\`, copy those changes over by hand.

## Problems

| What you see | What to do |
| --- | --- |
| "Không thấy cửa sổ Coowon đang mở Ngọa Long" | Open the game in Coowon (not another browser), wait until the tab title contains "Ngọa Long", then press **Tìm lại**. |
| `Khung game: … không thấy` or `chưa chắc chắn` | The game window is minimized, still loading, or covered by a dialog. Restore it, set zoom to 100%, press **Kiểm tra**. |
| The bot clicks in the wrong place | Set the page zoom to 100% (`Ctrl+0`). |
| The bot does not start or shows no window | Read `logs\app.log`. Check that Defender did not quarantine files in `_internal\`; if it did, allow them and extract the zip again. |

More cases are in section 10 of the [user guide](docs/user-guide-vi.md). When you report a bug, include the summary line from the **Nhật ký** panel and the `logs\run-<id>\` folder of that run (it has `run.log` and screenshots).

## Licence and policy

The original v1.8.0 code has no licence (the author was asked). The target server allows bots. Use the bot on your own account and at your own risk.
