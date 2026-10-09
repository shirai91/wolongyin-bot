# 08 · Engine revamp plan: perceive → read state → decide

Draft of 2026-10-08. Replaces the "Scene recognition" and feature-flow parts of [06](06-rewrite-design.md); window binding, capture, input, canvas location, the runner, the UI shell, routines, history and notifications stay.

## Why the bot gets lost

Today every feature is a script that assumes where it is. It calls `ctx.expect_scene("city", 3)` for the screen it expects next. If that one check fails, the run fails or skips a step. The bot never asks "what screen is this?" while it works.

Evidence from the runs of 2026-10-08 (`dist/AutoNgoaLong/logs/`) and the 46 live captures (`docs/game screenshots/live/`):

| Finding | Where | Cause |
| --- | --- | --- |
| Thành chính reported as "unknown (closest tham_bao)" | `run-20261008-215050-cong_trinh`, `run-…-205826`, `run-…-210019` | `city` is recognised by **one** template ("Quốc Chính") at **one** position ±10 px in the bottom bar. In this account's bar a "Dinh Phủ" label now covers it. The bar's content changes as the player levels up. |
| Same army attacked 3 times, "Trận kết thúc nhưng không thấy bảng kết quả" ×3 | `run-20261008-222038-clear_map` | The battle result was never read, so the bot could not tell won / lost / still fighting and kept repeating its script. |
| The debug status line says `field.army_dialog 1.00` on a frame that shows the open map | same run, `001-tim-dao-quan.png` | `RunContext._scene` is only updated by `expect_scene`; the bot's idea of the current screen is stale. |
| `tower.dialog` matches both the "Tháp" and "Điểm tướng" tabs; `generals` matches both its tabs; `daily` matches "Báo danh" and "Hoạt động" | annotate run over `live/*.png` | A scene is a flat name. There is no state inside a screen (tab, page, selected item, button enabled, counts, result). |
| 44 of 46 live captures are named correctly offline | annotate run | Canvas location (three anchors + black border) is reliable and stays. The weak part is per-screen recognition and the absence of state. |

So the fix is not "more templates". The bot needs (1) screen recognition that tolerates UI that moves and changes, (2) a readable state for each screen, and (3) a loop that decides from what it sees, not from where the script thinks it is.

## Live exploration (2026-10-08)

The live game was explored with `python -m ngoalong.tools.explore` (capture, click and key in canvas coordinates; frames saved to `corpus/live/`). The pass covered 71 frames and about 35 distinct screens, using navigation only: nothing that attacks, buys, spends or upgrades was clicked. Labels: [`corpus/live/labels.yaml`](../corpus/live/labels.yaml).

### Screen families

| Family | Examples | Parts that identify it |
| --- | --- | --- |
| **Map with HUD** | city, harbour, dungeon map (field), event dungeon, world map, region, resources | Top-left player panel, top-right gear/sound/music, bottom-right 主城 cluster, right-aligned bottom bar. They differ in **one or two parts**: city = bottom bar with Dinh Phủ + Quốc Chính and the map label "Vào Hải Cảng"; harbour = the same bottom bar, map label "Hải Chiến" instead; field = dungeon name bar + "»" at the bottom centre; region = bottom nav bar starting "Tài nguyên"; resources = the same bar starting "Về khu vực"; world map = city markers + Võ Khôi Tháp; event dungeon = map HUD with none of these |
| **Full-screen location** | tavern, palace (Quốc Chính), Dinh Phủ, Lôi đài, Đấu Trường, Đáp Binh, Ngao du, city-siege lobby, Hồn Thiên | HUD hidden (except chat, and in Ngao du the player panel). Exit button in the bottom strip, but its label and position vary: Thoát at the bottom right or bottom centre, Rời khỏi, 退出, Về thành chính. Some have a title plaque at the top centre |
| **Dialog over a map** | Báo danh/Hoạt Động, Quân Đoàn, Phủ Sách Lược, Trang bị, Tướng, Nhiệm Vụ, Tháp Thí Luyện, Tinh Tú, events, Bộ Doanh, Tìm lại, settings, army dialog, building panel, dungeon list | Four frame styles: **medium** (✕ at 802,119, title at 500,111, tab row at y≈159), **large** (✕ at ≈855,85, title at 500,87), **small** (title bar + ✕ at varying places), **panel** (no title, ✕ top-right) |
| **Box** | 确定/取消 confirm (leave arena), single-确定 notice ("cấp 130") | No title and no ✕; buttons centred. Meaning comes from **what the bot just did** |
| **Overlay** | corner toast, "Trong Thành"/location banner, loading bar, expanded chat | Drawn over any family; the loading bar ("…加载中… N%") appears the first time a module opens |

### Measured on the 71 frames

- **One ✕ template finds every dialog.** Cut once from Quân Đoàn and searched over the whole canvas, it scored 1.00 on all 18 frames with a dialog, toast, panel or full-screen ✕ (8 different frame styles), and found nothing above 0.7 on the 53 frames without one. A full-canvas search takes 45 ms; restricted to likely regions it costs a few ms. This is the anchor for the generic "dialog frame" part.
- **The selected tab is a hue, not a brightness.** Selected tabs are gold, the others teal. Mean (R − B) over the tab without its white label: selected **+22 to +33**, unselected **−11 to −18**. That's 8/8 correct over 5 dialogs (Hoạt Động ×4, Tháp, Tướng, Phủ Sách Lược, Trang bị). A plain brightness probe got it wrong, because longer labels are brighter.
- **Canvas location is solid**: HIGH on all 71 frames.

### Behaviours the engine must handle

- **The bot's own cursor makes buttons glow.** PostMessage clicks leave the game's mouse position on the last button clicked, and its hover glow lowers that button's template score (Trang Bị icon 0.85, "Tự mở" 0.82, against ≥ 0.92 normally). Parts that are clicked repeatedly use a 0.80 threshold. The other option is to move the mouse to a neutral spot after each click.

- **Dialogs remember their last state.** Tướng reopened on "Huấn Luyện" and the tower dialog on page 15. Read the state; never assume the first tab or page.
- **Exits do not always go back.** Leaving the arena (opened from the event map) landed in the city. After every exit, re-perceive.
- **ESC does nothing** in full-screen locations, so recovery must use the exit button or ✕.
- **Clicks can land on the wrong layer.** A top-row icon click inside a full-screen location hits nothing, and an exit can raise a confirm box first. Every action needs an expectation.
- **First-open loading** takes several seconds; "loading" is a state to wait on, not "unknown".
- **Account-dependent layout.** The top icon rows and the bottom bar are right-aligned and their content depends on level and progress. Search them by icon within the strip.
- **Danger buttons** sit next to safe ones (Đáp Binh costs Vàng per click, Bộ Doanh 50,000 Vàng, Tăng cấp, Mua). Rules must name buttons by part, never by a blind offset.

## Supported features (scope)

The game has many activities; the bot supports only these six. Thám Bảo, Tri Kỷ, card drawing, Chạy Thành Tựu and the other activities are out of scope.

1. Auto upgrade Khoa kỹ
2. Auto upgrade structures, in the main city and the harbour (Hải Cảng)
3. Auto clear campaign (Phó Bản)
4. Auto clear trial tower (Tháp Thí Luyện), with "Tự vượt tháp"
5. Auto roll chest (Quay Tinh Tú)
6. Auto farm Võ Khôi Tháp: a long loop with boss-fight retry, silver-chest purchase above 2 billion silver, and opening Tinh Tú chests when Quân lệnh runs out

## Feature flows, observed live (session 2, frames 072–263)

Frames and labels: `corpus/live/` (`labels.yaml`, second section). All flows were run end to end on the test account.

### 1 · Khoa kỹ (research)

- City bottom bar "Khoa kỹ" → medium dialog **Phủ Sách Lược**, tab "Khoa kỹ" (tab probe: gold = selected).
- **State**: a 6×3 grid of research icons (scrolls with ▲▼ at 645,225 / 645,422); the **selected** slot has a light frame; the "Thông tin" panel shows Tên, **Cấp** (green digits), Y/C (requirement), Phí.
- **Action**: select a slot, press "Tăng cấp" (613,471).
- **Result**: no box. Success raises Cấp; a failure shows a **red floating line** over the grid, e.g. "Cấp Phủ sách lược không đủ, không thể thăng cấp!" (fades after ~2 s). On this account every slot was capped by that requirement, so the rule is: after Tăng cấp, if Cấp did not change and the red line appeared, mark the slot "capped" and move on. Read Cấp with `digits`; detect the red line with a colour probe (pure red text in the grid region).
- *R1 live check (2026-10-09, frames 298–302)*: the dialog reopens on the slot selected last; the selected slot is read from its 1 px yellow frame (`research.slot`, 0–17 row by row). Cấp is unreadable on the Chiêu binh tab, as it should be.
- *R3 (frames 458–463, 476)*: there are exactly 18 slots (▼ does nothing). The dotted tiles are formations (Ngư Lân Trận, Hổ Thao Trận …), not locked slots. One press = +1 Cấp at once (37 → 70 in 33 presses once the cap had risen). The red line takes ~0.4 s to appear and ~3.5 s to fade, so a line still showing from the previous slot is not this slot's answer. A success line crossing other slots lit their frame probe at 60+ px (the real frame is 240–255): `slot.selected` now needs 200.

### 2 · Structures (city and harbour)

- Click a building on the map → **building panel** (same panel in the city and the harbour: ✕ at 687,202, "Tăng cấp" at 373,325, "Tốn bạc N" and "Thời gian h:mm:ss" under it, plus building-specific buttons such as Tháp Thí Luyện / Luyện / Điểm Tướng / Võ Hồn or Ra Khơi Mậu Dịch).
- Each press of Tăng cấp upgrades one level **instantly** (cost and time grow: 30091 → 31727 over 5 presses). The red silver digits in the top-left bar flashed during the presses.
- **The gold "skip wait" prompt does not show on this account because the player already chose "use gold to skip" in the game, and that choice resets on every browser reload.** So the bot can't assume either state. It must handle both: no prompt, or the `gold_skip_wait` confirm (answered by the user's setting).
- Buildings carry a "lvN" label on the map; the harbour is reached with "Vào Hải Cảng" (city) and left with 主城. Which buildings to upgrade (and their map positions) is per-account; the map labels give the levels.
- *R1 live check (2026-10-09)*: a harbour building upgraded at once (silver −7,218, cost 7,218 → 7,404, frames 332–333). The city's Tháp thí luyện building did **nothing** on Tăng cấp (no cost, no level change, no message, frames 304–305), probably a cap. So the rule must check that the cost (`city.building_panel.cost`) or silver changed, and give up on that building otherwise. The harbour map now shows summer colours (no snow): map art changes with the season, so identification never relies on it.
- *R3 (frames 452–477)*: the gold box is about **builders**: the notice list shows "Kiến Trúc Nhàn *N" (idle builders). While one is free a press upgrades at once with no box; when none is, the press asks "Bỏ qua thời gian chờ tốn N Vàng. Xác nhận?" (3 Vàng for a 3:35 wait) and the box covers Tăng cấp (the panel is then known by its ✕ at 687,202). A building at its maximum shows a yellow "Kiến trúc đã đạt cấp cao nhất" and nothing changes (Thương xã and two others stop at lv110). The city cap is the Chúa công level in the badge under the avatar (140 here). "lvN" labels are centred on their building, so the "lv" moves ~3 px left per added digit. The harbour has 5 buildings (v1.8.0 had 6 points).

### 3 · Campaign (Phó Bản)

- Field map with the **light-blue beam** over the next army. Clicking any army opens `field.army_dialog` (small frame, ✕ 638,184): name, level, troops "N/30", checkbox **"Phát chiến đấu"** (play the battle; ticked label turns white), "Tấn công" (584,304).
- With "Phát chiến đấu" **off**, the fight resolves at once with no battle screen; the dialog stays open and the troop count drops (30/30 → 29/30). **With it on**, the battle screen opens (`第N回合` round label at the top, "Kết quả / Tải / Nhanh" buttons bottom right), then a **战胜 / 战败 box** with "Xem lại" and "Thoát" (540,408). Thoát returns to the field.
- Rule: keep "Phát chiến đấu" off for speed and read the outcome from the troop count and whether the army is still there. Use the battle path only when the result has to be known exactly.
- *Correction (R1, frames 075–083 and 385)*: with "Phát chiến đấu" off the army dialog **closes** right after Tấn công and a green floating line shows the result; it does not stay open. Every attack costs **1 Quân lệnh** (123 → 122). The checkbox keeps its last state between armies.
- *Group armies (R1 live check, Đổng Trác, frames 360–373)*: an army titled "(Đoàn-Tinh)" has no "Phát chiến đấu" box (`field.army_dialog.group`). Tấn công opens the **Đoàn chiến** dialog (same team list as Võ Khôi Tháp): Lập đội → team room (no ✕) → Khai chiến → a battle screen with the VS bar (loading first), which ends with a big **攻方胜利** ("the attacker won") over the field and Xem lại / Thoát at the bottom right. The loss text (presumably 守方胜利) has not been seen.
- *The beam* was seen on Đổng Trác (frame 359: 15 × 47 px at 826,94, the army 90 px below its top). It flickers (frames 361–373 show it on and off), so a rule needs it on 2 ticks. The last dungeon (Tư Mã) has « but no » under its name bar.
- The same battle screen and result box appear in Thám Bảo, so "battle" is one reusable part.

### 4 · Trial tower (Tháp Thí Luyện)

- Building panel "Tháp Thí Luyện" → medium dialog with tabs Tháp / Luyện / Điểm tướng (it reopens on the last tab and page).
- **Floor list with pagination**: page N shows floors 10(N−1)+1 … 10N, and only floors the player has reached are listed (page 15 showed only "Tầng 141"). Page box at 247,476 with ◀ 206,476 and ▶ 291,476. **Typing works**: double-click the box, Backspace ×3, type the page, Enter (page 3 → Tầng 21–30). Read the page with `digits` and the list rows with row templates.
- **Climb ("Vượt Tháp", 367,469)** → confirm box (3 Thí luyện lệnh) → **floor map** (`Tầng N` at the top, kill log top-left, bottom-right "☑ Tự Vượt Tháp" checkbox at 741,563, "Phát chiến báo", "Thoát"). Nothing moves until a click: **ensure the "Tự Vượt Tháp" checkbox is ticked, then click the first enemy once**; the unit then fights every guard by itself. The floor maps differ (own unit at 268,480 on Tầng 141 and 378,440 on Tầng 21), so the first enemy must be found on the map, not clicked at a fixed offset.
- *R1 live check (frames 313–328)*: the guard to click is the one under a **light-blue crystal** (the same colour as the campaign beam; `tower.floor.marker`, ~50 px above the guard). A click on the next guard along did nothing (316–322); the click under the crystal started the climb, and floor 21 was won in a few seconds. Paging with ◀ ▶ selects the first row of the new page; a selected row has a yellow frame, a hovered one a teal frame (both read as selected).
- **Result**: win = 战胜 box with only "Thoát", which returns straight to the tower dialog. Loss = 战败 box with "Tiếp tục" and "Thoát"; leaving then asks "Chưa hoàn thành vượt Tháp, xác nhận thoát?" (确定 / 取消) and returns to the dialog.
- **Sweep ("Tự Vượt Tháp" button in the dialog, 538,469)**: a side panel "Hãy nhập số lần vào" with a count box (700,253), cost "Vàng*0", floor, reward preview, 确定 / 取消, then a confirm. It completes instantly (2 runs = 6 Thí luyện lệnh) with no battle. **Only floors already won can be swept**; this is the user's requirement for the "Tự vượt tháp" mode.

### 5 · Tinh Tú (chest wheel)

- Top icon "Tinh Tú" → large dialog (✕ 859,98). **State**: chest count "X N" (Mua at 783,143), coin count "X N" (Đổi), the scrolling reward log, and the left buttons Hướng dẫn / Đổi nhanh / **Tự mở**.
- "开始抽奖" (420,268) opens one chest, and a reward line appears in the log.
- "Mua" → quantity box (+ / − / Tối đa) "Dùng 240 vàng, mua 1 Rương may mắn" → 确定 (438,365).
- "Tự mở" → "Tự mở N Rương may mắn không?" with a quantity box → 确定. The button then reads **"Ngưng mở"** (button label = auto-open state) and opens about one chest every 4 s.
- **Fast-open trick** (user tip, verified): toggle Tự mở on and off quickly. 5 cycles of Tự mở → 确定 → Ngưng mở, 0.3 s apart, took 5.9 s and opened 4 chests: about 1.5 s per chest instead of 4 s. The loop stops when the chest count reads 0.
- *R1 live check (frames 335–343, 387)*: Mua costs 240 vàng per chest (3 bought). The button already reads "Ngưng mở" while the "Tự mở N" confirm box is open, and auto-open stops by itself at 0 chests. A chest can give Quân lệnh (+20 seen), which is why the Võ Khôi Tháp loop comes here.

### 6 · Võ Khôi Tháp

- World map "Võ Khôi Tháp" → **lobby** (full screen): boss name and level ("Hoả Lv.100"), "Lần này tiêu hao Quân Lệnh 23", "Số đội hiện tại N", ‹ › to switch boss (265,308 / 728,308), "Tấn công" (502,500), "Thoát" (493,577).
- Tấn công → **team list** (page 1/1, "Hiện không có nhóm", "Lập đội" 768,505) → **team room** ("Bella 1/8", Mời / Giải tán / Khai trừ / **Khai chiến** 805,510).
- Khai chiến → **arena**: boss HP bar at the top ("Hoả LV100 50000000/50000000"), "Đếm ngược mm:ss", the player standing apart and **three glowing magic circles** on the floor. Clicking the boss does nothing, and the countdown runs down without a fight. **Clicking a magic circle joins the fight** ("Ngài gia nhập thành công…"); the boss died within a few seconds.
- **Victory**: "胜利" box listing Bạc / U.D / C.C / Long Ngọc / Khắc Văn / Trang Bị / Thí Luyện Lệnh rewards, 确定 at 494,424. It returns to the **world map** with Bạc +90,000,000 and Lệnh −23.
- **Loop rule**: lobby → Tấn công → Lập đội → Khai chiến → click a circle (retry on another circle while the countdown runs and the boss HP stays full) → 确定 → back to the lobby. Before each round, check Quân lệnh ≥ cost (top-left "Lện N/100" and the lobby text). When it is too low, go to Tinh Tú and open chests. When silver passes 2,000,000,000 the game shows an alert, and the bot buys the silver chest.
- **Buying a silver chest** (session 3, frames 264–292):
  1. City bottom bar "Trang Bị" (760,556) → medium dialog **Trang bị**. It reopens on its last tab; tabs Kho / Tài Nguyên / **Cửa hiệu** (375,159) / Bảo vật / Cường hóa / Phái / Tướng khí, read with the tab probe.
  2. Cửa hiệu is a scrolling item grid with ▲ (651,212) and ▼ (651,425). **It keeps its scroll position between opens**, so press ▼ until the chest row shows at the bottom and the grid stops changing. That took 6 presses from the top; the scroll moves in partial rows.
  3. The bottom row holds five identical chest icons at y≈410, x = 335 / 390 / 445 / 500 / 553: **5,000,000 / 10,000,000 / 50,000,000 / 100,000,000 / 1,000,000,000** silver ("5 trăm vạn Bạc" … "1 tỷ bạc"). The icons can't be told apart, so the bot picks by position and **verifies the "Bạc cần" number** (`digits`, at 360–460, 463) before buying.
  4. "Mua" (502,463) buys **at once, with no confirm**: a green floating line "Mua[1 trăm triệu Bạc] thành công". Silver drops by the price (958,983,797 → 858,983,797), and the chest goes into Kho as an item (60 → 61/500 slots). The silver cap shown in the top bar is 2,425,000,000.
  5. Close with ✕ (802,119). Every press buys one chest, so the bot must read silver after each buy.
- **Rule (user decision, 2026-10-08)**: when the silver alert appears (silver above 2,000,000,000), buy **two 1,000,000,000 chests** (the last slot, x=553), checking "Bạc cần" = 1000000000 and that silver drops by 1,000,000,000 after each Mua. Then close the dialog and return to the Võ Khôi Tháp loop.
- **Not yet observed**: the 2-billion silver alert (the account has 959 million), running out of Quân lệnh (593 left, 23 per round). They need a long run, and the R2 pilot of this feature should capture them.
- *A lost round (R1 live check, 2026-10-09, frames 390–423)*: the lobby reopened on another boss (**Mộc Lv.140, 70,000,000 HP, cost Quân Lệnh 1**: the cost depends on the boss, read it per boss). Clicks on the 2nd and 3rd magic circle (not the one next to the player) did not join; a little later the countdown and the player vanished and the boss kept full HP. Thoát asks "Bạn muốn thoát khỏi Võ Khôi Tháp?" and leads to the world map (Quân lệnh −1); clicking the world-map tower then went **straight back into the arena**, and Thoát there showed the **失败 box** (every reward 0, the HP bar empty as after a win). After that the game client **froze**: no pixel changed for minutes and clicks and keys were ignored. So: `vkt.result.result` (win / lose) decides the outcome, never the HP bar; `vkt.arena.counting` tells a running round from an expired one; and R2 needs a watchdog for a frozen client (identical frames for N seconds while actions get no response).

## Target loop

```text
          ┌──────────────────────────────────────────────────────────────┐
          │                         one tick (~250–500 ms)               │
          ▼                                                              │
  1 Capture + canvas        (kept: Win32Capturer, CanvasLocator)         │
          ▼                                                              │
  2 Recognise UI parts      HUD bars, dialog frames + ✕, buttons,        │
                            title images, battle chrome, toasts (no OCR) │
          ▼                                                              │
  3 Identify the screen     base screen + stack of dialogs + overlays    │
          ▼                                                              │
  4 Read the screen state   tab, page, list rows, counts, result, ...    │
          ▼                                                              │
  5 Decide                  compare Snapshot with BotState (goal +       │
                            memory) → one Action + what it expects       │
          ▼                                                              │
  6 Act                     click / key / text / wait / done / fail      │
          ▼                                                              │
  7 Verify                  next Snapshot meets the expectation? update  │
                            BotState; else count a miss → recovery ──────┘
```

Each tick produces one `Snapshot`, which is also what the debug overlay, the "Kiểm tra" button and the logs show. There is a single source of truth for "where am I".

## Concepts

### 1. UI parts (recognise pieces, not whole screens)

A **part** is a piece of game UI that appears on many screens: the top-left resource bar, the top-right gear/sound/music icons, the bottom-right 主城 / Thế Giới cluster, the bottom button bar, the chat bar, a dialog frame with its ✕ close button, a 确定 / 取消 pair, the battle "第N回合" round label, the "Thoát" button, a toast.

**UI parts are the primary signal for both screen and state.** Parts are found with cheap pixel detectors only. OCR is not used for recognition (see "When OCR is allowed" below).

Rules for part detectors:

- **Search a region, not a point.** Each part has a search region (for example "bottom 60 px of the canvas"), not an `at` ± 10 px.
- **Cheap detector kinds only**, cheapest first:
  - **colour or shape probe**: a few pixels or a small histogram, for example the light-blue beam, a highlighted tab, a greyed button, the black battle background;
  - **icon template anywhere in the region**: the bottom bar's buttons are recognised by their **icons**, not their labels, so a label covered by another button (the "Dinh Phủ" case) does not matter;
  - **dialog frame**: find ✕ buttons anywhere, derive the frame from each, then match the **title as an image** (a template cut once from the title strip) only inside that strip.
- **Gate expensive checks behind cheap ones.** A screen's detectors run only when its required parts are present. For example, tower templates are never searched when no dialog frame was found.
- **Parts return positions.** A dialog part returns its rectangle, its ✕ point and its title id, so later steps never need fixed offsets for that dialog.
- **Fast enough to run every tick.** Target: all parts of one Snapshot in ≤ 80 ms on a 1000×600 canvas, measured by the R1 benchmark.

### 2. Screen identity (rules over parts)

A screen is declared by which parts must be present, which may be, and which must not be:

```yaml
# profiles/ngoalongviet/screens/city.yaml
city:
  layer: base
  require: [hud.top_left, hud.home_cluster, bottom_bar]
  any:     [{icon: bottom_bar.quoc_chinh, in: bottom_bar}, {icon: bottom_bar.dinh_phu, in: bottom_bar}]
  forbid:  [field.name_bar, battle.round_label]

tower.dialog:
  layer: dialog
  require: [{dialog: {title: title.thap_thi_luyen}}]   # title template, matched only in the frame's title strip
```

The result is layered, because the game stacks things:

```text
Snapshot
  base:     city
  dialogs:  [tower.dialog {tab: "Tháp", page: 3, ...}, tower.confirm]   # top last
  overlays: [toast]
  hud:      {level: 140, silver: 712694233, ...}                         # read when asked
  unknown:  []                                                          # parts nobody claimed
```

"Unknown" is no longer a dead end: it lists the parts that were seen (for example "a dialog frame with ✕ at (803, 118), title matches no known title") so recovery can still close it with its ✕, and the failure PNG shows what it was.

### 3. Screen state (readers per screen)

Each screen declares **readers** that turn pixels into typed values. State is read from UI parts too:

| Reader | How | Example |
| --- | --- | --- |
| `selected_tab` | colour probe on each tab: mean(R − B) without the label (gold = selected) | which tab of `tower.dialog` is open: Tháp / Luyện / Điểm tướng |
| `enabled` | colour probe (grey vs active) | is "Nâng cấp" clickable |
| `present` | template or colour in a small region | battle result 战胜 / 战败 shown, beam visible, "»" shown |
| `rows` | row-slot templates or highlight probe per slot | the floor list: which slots are filled, which one is selected |
| `find_all` | template anywhere in a region | all enemy flags on the field, with the one under the beam marked |
| `digits` | the game draws numbers in fixed bitmap fonts: match 0–9 glyph templates in a small box | page box "3", "Thí luyện lệnh: 628", building level |
| `ocr_text` | **last resort**, see below | a name the bot cannot know in advance |

Readers run lazily: a decision that only needs `tab` reads nothing else. Values are cached per Snapshot.

### Digits (built 2026-10-09)

`ngoalong/core/digits.py` reads numbers without OCR. Glyphs are in `templates/digits/game/`, learned by `tools/cut_digits.py` from `digit_samples.yaml`; the fields are in `numbers.yaml`; `tools/number_report.py` checks them.

- **One font for every number** the six features read: 9 px tall, proportional (a "1" is 4 px wide, a "2" 7 px). Only the colour changes (white, yellow, green, red). The reader turns a region into an **ink mask** (max(B, G, R) > 150), which keeps the digits and drops box borders and backgrounds for every colour. It splits the mask at blank columns and matches each character against 13 glyph images (0–9, "/", and second variants of 0 and 2).
- **Touching digits** (the font kerns, e.g. "74", "342", "22") are split by dynamic programming: the glyph sequence that best covers the run, allowing 1 px overlap.
- **Text caret** in the page box: an inked column taller than 11 px is removed.
- **Safety**: a character that matches no glyph reads "?", and a read whose ink touches the region's left or right edge is **clipped**. Either one rejects the read, because a region starting inside a "1" read it as a "4" with a 0.89 score. Numbers that run into an icon can't be read; the HUD gold count touches 充, and no supported feature needs it.
- **Centred labels** ("Tầng 141" / "Tầng 21") use `last_int`: a wide region, keeping the characters after the last word gap (≥ 4 px).
- **Result over the corpus**: 435 reads across 8 fields, all consistent with the session.
  - Silver: +90M from the boss, then upgrades, then −100M for the chest.
  - Quân lệnh: 619 → 618 → 617 → 594 (−23 Võ Khôi Tháp) → 593.
  - Chests: 21 → 20 → 21 → 20 … 11 → 7 → 3.
  - Thí luyện lệnh, tower page and floor, research Cấp, and all five shop prices also read correctly.
  - 28 frames were rejected and none were misread. The rejections were silver flashing **red** while it is being spent (read again next tick), and Quân lệnh covered by the Tinh Tú or events dialog.
- *R1 end*: 12 fields (added Tinh Tú coins, sweep runs, army troops "80/80", building cost "Tốn bạc: 33047" via `last_int`): 688 reads over 425 frames, none wrong; 46 rejected (red silver, covered Quân lệnh, Cấp on the Chiêu binh tab, armies without a troop count).

### Perception (built 2026-10-09, R1)

The package `ngoalong/perception/` turns a 1000×600 canvas into a `Snapshot` without OCR. Profile data: `parts.yaml` (56 parts, templates cut by `part_crops.yaml`), `probes.yaml` (colour probes), `numbers.yaml` (digit fields), `screens/*.yaml` (screen rules and readers).

| Module | Does |
| --- | --- |
| `parts.py` | `PartLibrary` (specs + templates, loaded once) and `PartScan` (one frame): `find(name)` searches the part's region once and caches the hits; `multi` parts return every copy (non-maximum suppression) |
| `probes.py` | colour probes of 4 kinds: `mean` (e.g. R − B of a tab without its label), `count` (pixels of a colour, optionally only on a border ring, or *at most* N for "not drawn"), `blobs` (connected colour regions with size/aspect, e.g. the beam), `fill` (bar fill fraction) |
| `screens.py` | `ScreenBook`: rules `require` (all of; a nested list = one of), `forbid`, `over` (only over these screens), `z` (stacking), `close` (which ✕ it owns), `claims`. Bases are tried in order (first match), every dialog / overlay that holds is kept. A ✕ nobody claims becomes an `unknown` dialog with its position. YAML note: the key is `over`, because YAML reads a bare `on` as *true*. |
| `readers.py` | `Senses` (parts + probes + digits of one frame, cached) and `ScreenState` (a screen's readers, lazy, cached). Reader kinds: `tabs`, `present`/`enabled`, `value`, `digits`, `point`, `find_all`, `count`, `blobs`, `first_of`, `grid` (rows / slots with filled / selected probes and a number per row), `from` (+ `pick`) |
| `snapshot.py` | `Perceiver.load()`, `Perceiver.canvas(frame, fix)`, `perceive(canvas) -> Snapshot` with `base`, `dialogs` (top last), `overlays`, `unknown`, `stack`, `top`, `has()`, `snap["tower.dialog"].page`, `snap.hud.silver`, `point(part)`, `describe()` |

Screens declared for the six features: bases `battle`, `tower.floor`, `vkt.arena`, `vkt.lobby`, `field`, `harbour`, `city`, `world_map`, `map` (any other HUD map); dialogs `research`, `city.building_panel`, `field.army_dialog`, `field.dungeon_list`, `field.group_list`, `field.group_room`, `battle.result`, `battle.group_result`, `tower.dialog`, `tower.sweep`, `tower.result`, `chest.wheel`, `chest.buy`, `chest.auto_open_confirm`, `inventory`, `vkt.team_list`, `vkt.team_room`, `vkt.result`, `confirm.ok_cancel`, `notice.ok`; overlays `floating_error`, `floating_success`, `toast`; the `hud` (silver, Quân lệnh).

Findings while building it:

- **Which map is under a big dialog.** Tinh Tú covers the map labels and the bottom-bar icons. The bar's *label strip* ("DinhPhủ") still shows under it, and the sea in the left margin tells the harbour from the city (mean B − R: harbour +76..+93, city −9..−24); the right margin tells the world map (+26).
- **One ✕ search region** (x ≥ 560, y ≤ 340) holds every ✕ seen (9 ms instead of 37 ms on the whole canvas).
- **The battle screen** is recognised by its VS bar (also on the loading screen and group battles) or the Nhanh button.
- The old engine's `field.enemy_flag` template no longer matches this account's army sprites; the campaign port needs new army templates (or the beam alone).
- `tools/perceive.py --bench` over `corpus/live/labels.yaml` (labels may carry `expect: "base > dialog + overlay"` where the human names differ): **364/364 feature frames and 31/31 other frames** have the right stack, every reader value in the labels matches (31 readers, 0 wrong), **0 OCR calls**, parts 21 ms mean / 33 ms max, full Snapshot with every reader 35 ms mean / 81 ms max (the tower dialog's 10 row reads are the dearest). The two failure frames of 2026-10-08 that were kept (`logs/run-…-215050`, `run-…-222038`) read as `city` and `field{beam=True}`.
- **Live check** (`perceive.py --live`, or `explore` + `perceive.py` on the saved frame): frames 294–423, every screen of the six features, see `labels.yaml` session 4.

### Decision loop (built 2026-10-09, R2)

| Module | Does |
| --- | --- |
| `engine/loop.py` | `Loop.tick()`: Stop / Pause and max run time → capture, canvas (located again every 5 s), Snapshot → frozen-client watchdog → verify the previous action's expectation → interrupts → the feature's `goal` → recovery navigation → the feature's rules → `Navigate(entry)` → act. Loop guard (`Limits`): 40 idle ticks, a `Wait` past its `patience`, or the same decision on the same screen 8 times in a row climbs the recovery ladder. `run_feature(feature, ctx)` is what `Feature.run` calls. |
| `engine/actions.py` | `Click(at)`, `Key(vk)`, `Text(text, at=, clear=, enter=)`, `Wait(seconds, patience=)`, `Done(message)`, `Fail(message)`, `Navigate(screen)`. Each carries `expect=` (a screen name or a predicate on the next Snapshot), `change=` (a reader whose value must change), `within=` (seconds), `settle=`, `then=` (runs once the expectation is met: update the memory) and `stop=` (end the run as stopped). |
| `engine/feature.py` | `Feature(Task)` with `entry`, a pydantic `Memory`, `rules()`, `goal(snap)`, `begin()`, `summary()`, `max_run_s()`, `answers` (boxes the feature answers itself). `Rule(name, on=screen, when=, do=)`: the first rule whose `on` is the top of the stack and whose `when` holds fires; `when` / `do` get that screen's `ScreenState`, `self.snap` is the Snapshot, `self.m` the memory. A missing reader (KeyError / AttributeError) or a `Click` on a part that is not shown means "does not apply". |
| `engine/navigate.py` + `nav.yaml` | The screen graph: edges `click: part` / `at: [x, y]` / `key` / `close: true`, with `reveal` (click this first when the part is hidden: the "<<" of the top icon rows), `over` and `within`; `close:` entries for dialogs not closed by their ✕ (boxes answer 取消, the sweep panel). `Navigator.step(snap, target)`: cost = dialogs to close + shortest path from each screen of the stack; only the first edge is checked against the frame. A dialog not on the way (also an `unknown` one) is closed by its ✕, else ESC. |
| `engine/interrupts.py` | `popups.yaml` (reward, toast, `gold_skip_wait` by its setting) and `panels.yaml` (quest tracker, Sắp mở, chat: at most 2 clicks each per run, only on a map with no dialog), checked every tick before the feature's rules. |
| `engine/recovery.py` | Ladder, one rung per trouble, back to 0 when a feature rule's expectation is met: 1 close the top dialog by its ✕ → 2 ESC → 3 `Navigate(entry)` for up to 45 s, ignoring the feature's rules → 4 fail with the screenshot and the Snapshot dump in run.log. |
| `engine/actor.py` | `Actor`: capture, canvas fix, clicks / keys / typing in canvas points, the mouse moved to (600, 2) after every click (no hover glow), run counters and progress, the debug frame of a Snapshot (`snapshot_frame`: parts found green, unclaimed ✕ red, the stack as the status line). Built from the old `RunContext` for now (`Actor.from_context`). |
| `tools/run_feature.py` | `python -m ngoalong.tools.run_feature <id> --set k=v [--debug] [--stop-after S]`: one feature through `AppServices` → `TaskRunner` → `Feature.run`, the same path as the "Bắt đầu" button, with the run log on the console. |

The app's "Kiểm tra" button and the live overlay now show the Snapshot (`AppServices.probe`, `overlay_snapshot`), and the status line follows the stack while a feature runs. run.log has one line per new decision, the values the rules looked at (`Snapshot.describe(read="seen")`) and the action with its expectation:

```text
09:34:52 [DEBUG] field | → tower.dialog → click field → city: hud.home @959,571, expect state ≤8s
09:34:52 [DEBUG] city > city.building_panel | → tower.dialog → click city.building_panel → tower.dialog: btn.panel_tower @522,322, expect state ≤8s
09:34:53 [DEBUG] city > tower.dialog{tab=Tháp, page=15, tokens=21379} | rule "trang" → type '3' trang 3, expect state ≤3s
```

**Pilots** (`features/quay_ruong.py` "Quay Tinh Tú", `features/thap_thi_luyen.py`), replacing the old scripts; their forms come from the new `Params` (old saved keys are ignored):

- *Quay Tinh Tú*: `opens` (0 = all), `buy` (chests to buy first, 240 Vàng each), `max_minutes`. Buy: Mua → type the number → 确定 → the spend-Vàng box 确定; then the fast trick (Tự mở → 确定 → Ngưng mở) while 2 or more are left, 开始抽奖 for the last one. Every drop of the chest count is a chest opened.
- *Tháp Thí Luyện*: `mode` climb / sweep, `start_floor`, `end_floor`, `sweep_runs`, `battle_timeout_s`, `max_minutes`. Climb: tab Tháp → type the page → select the floor → Vượt Tháp → 确定 → tick Tự Vượt Tháp if needed → click under the crystal (seen on 2 ticks) once → 战胜: next floor / 战败: Thoát → 确定, the run ends. Sweep: only when the next floor is listed (else it fails: "Tầng N chưa thắng"), Tự Vượt Tháp → type the runs → 确定 → 确定; the runs are counted from the Thí luyện lệnh spent.
- `features/_tower.py` and `coords/thap.yaml` stay: `chua_cong` (Cày cấp chúa công) still uses them; it is the same flow as the new sweep mode, so R3 can drop it. `coords/quay_ruong.yaml` stays for `vo_khoi_thap` (its spin). No coords became unused.

**Live gate** (2026-10-09, the test account, `run_feature` through the runner): see the table in "Phases and gates". Findings:

- **The browser reload reset the game's "don't remind" choices.** Buying a Tinh Tú chest now asks "Dùng 240 vàng, mua N Rương may mắn ☐ Lần sau không nhắc nhở" (frame 435). The old pop-up guard took it for `gold_skip_wait` (same "Lần sau không nhắc nhở" line) and declined, stopping the run. It is now the screen `confirm.gold`; a feature that expects it lists it in `answers` and the guard leaves it alone; navigation answers 取消 to it. The box is never ticked.
- **Quantity boxes reset to 1 when emptied** (frames 430–432): double-click selects the number, Backspace empties it, the game puts "1" back with the caret in front, so typing gave "61". Type over the selection (`Text(clear=0)`). The page box takes Backspace as R1 found; `Text` sends Backspace and Delete so the caret side does not matter.
- **A mouse move outside the canvas never reaches the game**: the last clicked button kept its hover glow, "Ngưng mở" scored 0.88 against 0.90 and the fast trick stalled until the idle guard closed and reopened the dialog. The mouse now rests at canvas (600, 2) and `tinh_tu.auto_on` uses 0.80 like `auto_off`. With the rest point no tower row read as hovered.
- **The top icon rows change with the account** (after the reload "Quà mỗi ngày" and "VIP Đông" appeared and Tinh Tú moved from x≈500 to x≈558): `icon.tinh_tu` is searched along both rows; the "<<" of hidden rows is `hud.icons_hidden`, clicked first (`reveal`).
- **Speeds**: the fast trick opened 60 chests in 60 s (~1.0 s per chest at a 0.3 s tick). A tower floor of 21–40 took ~27 s (the crystal is found in about 1 s, the fight ~23 s). The building panel and the tower dialog open in under 0.5 s once loaded.
- **ESC on the city map does nothing** (frame 429), so the ESC rung is harmless on maps.
- **Ice floors (61+) have blue torches and orbs** of the crystal's colour (frame 449: 18×63 at 813,188 and 9×37 at 672,96). The first run clicked the biggest blob, far from any guard; the crystal is now picked by size (6–15 × 26–44 px) and, of several, the lowest one (the path starts at the bottom). Floors 61–66 then went through on the first click.
- **The frozen-client watchdog** was checked offline (a static frame, a feature typing into it): the ladder gave up after 12.5 s and reported "game không phản hồi … Hãy tải lại trình duyệt (F5)"; on a live client that keeps changing pixels it never fired. Long waits (fights) are covered by `Wait(patience=battle_timeout_s)`.

Gate runs (run folders under `logs/`, frames in `corpus/live/labels.yaml` session 5):

| Pilot | Start | Run | Result |
| --- | --- | --- | --- |
| Quay Tinh Tú | city, panels + toast open after the reload | `092020`, `092203`, `092355` | toast closed, 3 panels collapsed, wheel opened (0 chests: done); `092203` stopped on the spend-Vàng box (finding below); after the fix 6 bought and opened in 14 s |
| Quay Tinh Tú | world map | `093129` (63 s) | bought 60, opened 60. The runs before the hover fix (`092812`, `093011`) stalled on the glowing "Ngưng mở"; the idle guard closed and reopened the wheel 4 times and they still finished |
| Quay Tinh Tú | campaign field | `093249` | bought 50, opened exactly the 40 asked for |
| Quay Tinh Tú | field with Quân Đoàn open (unknown dialog) | `093348` | ✕ closed it; bought 40, opened 50 |
| Quay Tinh Tú | city | `095624` + `095700` | bought 150, Stop after 30 s (latency 0.01 s, 26 opened), the next run opened the other 123 |
| Tháp Thí Luyện | chest wheel open over the city | `092429` | climbed 22–24 |
| Tháp Thí Luyện | campaign field | `093450` | 主城 → city → panel → dialog; climbed 25–30 |
| Tháp Thí Luyện | world map | `093817` | climbed 31–40 (276 s, no recovery) |
| Tháp Thí Luyện | city with Quân Đoàn open | `094312` | climbed 41–60 across a page change (551 s, no recovery) |
| Tháp Thí Luyện | city | `095235` | sweep floor 50 ×60 (page 6 checked for floor 51, then page 5); `092626`: floor 23 ×5 |
| Tháp Thí Luyện | tower dialog | `092638`, `092652` | sweep of floor 141 refused ("chưa thắng"); climb 141 lost → Thoát → 确定 → summary |
| Tháp Thí Luyện | tower dialog / inside floor 61 | `095246` + `095327` | Stop after 12 s mid-floor (latency 0.02 s); the next run started on the floor map and climbed 61–66 |

### Structures and Khoa kỹ (built 2026-10-09, R3)

**Perception added**: `map.lv`, a part matched on its **white ink** (`ink: 170` in `parts.yaml`: region and template are both reduced to "every channel above 170" before matching, so map art, season and animations under the label do not count; 24 ms over the map); the `tags` reader (`city.labels` / `harbour.labels`: every "lv" with the number right after it, its own pixels removed because the font kerns, pieces lower than a digit dropped, a 4 px gap ends the number, a number running off its box reads None) and `levels` (the numbers); `hud.level` (the Chúa công badge; its digits need ink level 170, so `numbers.yaml` fields take an optional `level`); `hud.icons_shown` (">>"), `hud.notice_open` (the left notice list's "<<"); `city.building_panel` is also recognised by its ✕ alone (under the gold box). Real labels score 0.93–1.00, nothing else above 0.89 (threshold 0.91). Bench: 443/443 feature and 37/37 other frames, 0 OCR, Snapshot with every reader ≤ 110 ms.

**Engine added**: `or_after(pred, s)` (an expectation met by `pred` or by `s` seconds passing: for presses that may rightly do nothing; the action's `then` reads the answer from the Snapshot and the recovery ladder is not involved), `Actor.policy` (the user's answers to cost questions, for features that answer a box themselves), a feature may change `entry` during a run (the loop and the recovery ladder read it each time), `run_feature --gold yes|no` (the "Dùng vàng bỏ qua thời gian chờ" setting for one run, not saved). Navigation: city / harbour → `research` by the bottom bar "Khoa kỹ".

**Nâng cấp công trình** (`features/cong_trinh.py`; settings `mode` Thành chính / Hải cảng / Cả hai, `max_level` 0 = Chúa công level, `upgrades` presses per visit, `max_minutes`; old saved keys are ignored):

- On the area's bare map: collapse the notice list, hide the top icon rows (they cover the labels near the top; shown again at the end), read the labels. A building is remembered by where its "lv" sits (5 px, following it as it widens); a level counts when two reads in a row agree (a fire sprite turned the tower's lv140 into "145" on one frame). An area is done after 3 scans with labels when no building is below the cap.
- Pick the lowest building below the cap, click 12 px above its label (then on the label; 3 tries), press Tăng cấp up to `upgrades` times, ✕, read the map again. **Every new "Tốn bạc" on the open panel is one level**, whatever answered the press; a press that changes nothing within 3 s, twice in a row, means the building is at its maximum and it is left for the run.
- The gold box: setting on → 确定 (that press is not judged: the Vàng may only free a builder); off → 取消 and the run ends with that reason (every building would ask the same until a builder is free). "Cả hai" does the city, then walks to the harbour ("Vào Hải Cảng") with the harbour as the new entry.

**Nâng cấp khoa kỹ** (`features/khoa_ky.py`; settings `upgrades_per_tech`, `slots` e.g. "1-8" (empty = all 18), `max_minutes`): tab Khoa kỹ → each slot in turn: select (expect `research.slot`), press while Cấp rises, up to `upgrades_per_tech`; a new red line, or no new Cấp within 1.5 s, = capped (skip). The summary points to the Phủ Sách Lược building when every slot is capped. The old modules' v1.8.0 points and grid offsets (`coords/nang_cap_*.yaml`) are deleted.

**Live gate** (2026-10-09, test account; run folders under `logs/`, frames in `labels.yaml` session 6). The upgradeable work ran out during the gate: the city went from lv1 / lv60 / lv80 / lv110 to lv140 (three buildings stop at their lv110 maximum), the harbour's lv62 to lv140, and every Khoa kỹ slot is capped by Phủ Sách Lược. About 975 building levels and 34 research levels; ~18 min of structure runs and ~8 min of research runs in all, then the client froze (findings).

| Feature | Start | Run (`run-20261009-…`) | Result |
| --- | --- | --- | --- |
| Công trình | city | `101753` | first try: the gold box hid the panel (stack `unknown > confirm.gold`); box and panel took turns (navigation answered 取消, the rule pressed again) for 2 min, stopped by hand. Fixed: the panel is known by its ✕, the gold rule only needs a visit in progress |
| Công trình | panel open, gold off | `102227` | ✕ the stray panel, press → gold box → 取消, the run ends with the reason (3.5 s) |
| Công trình | city, gold on, max lv12 | `102240` | 43 levels (5 buildings to lv12) in 30 s |
| Công trình | world map, Cả hai, 6 min | `102542` | 540 levels; one transient "panel did not open" (now 3 tries) |
| Công trình | Tài Vụ panel open, Cả hai | `103239` | 366 levels, city then harbour (lv62 → lv140); a gold box that came 2–3 s after a press was taken for "no effect" and closed by the ESC rung (the one recovery; now 3 s and 2 misses) |
| Công trình | harbour → city | `103854` | 25 levels (lv115 → lv140); three lv110 buildings at their maximum |
| Công trình | research dialog open, Cả hai | `104239` | ✕, nothing left in the city or the harbour (12 s) |
| Công trình | field, Hải cảng | `104424` | 主城 → Vào Hải Cảng → done (2.7 s) |
| Công trình | research over the harbour | `104512` | Stop after 5 s: latency 0.00 s |
| Khoa kỹ | city | `102353` | 1 level (ô 7 36 → 37), 17 capped (72 s: it waited out each red line; now 1.5–2 s per capped slot) |
| Khoa kỹ | building panel open | `103945` | 33 levels (ô 7 37 → 70); the success line lit other slots' frames, `chọn ô` missed 17 times (rung 1 each: close and reopen the dialog) and still finished. Fixed with `slot.selected` ≥ 200 |
| Khoa kỹ | harbour / world map / Quân Đoàn open / Chiêu binh tab | `104307`, `104355`, `104448`, `104505` | all capped, goal reached; Stop after 8 s: latency 0.06 s |
| both | soak, alternating: world map, Quân Đoàn over the harbour, field | `104543` … `105049` (15 runs) | every run reached its goal, no recovery |

Findings:

- **The client froze again** after the last soak run (10:51): identical frames for minutes, clicks ignored, while Windows does not report the window as hung (frames 498–510). It was not reloaded. The last three fixes (`slot.selected` ≥ 200, 3 s / 2 misses, `map.lv` threshold 0.91) are checked on the corpus only.
- `Tổng LC` in the player panel dropped from 161,656 to 133,074 during the structure runs; unexplained, probably unrelated (troops).
- The old templates `field.hud.icons_toggle` and `hud.notice_collapse` (`template_crops.yaml`) are no longer used by these features; R4 removes them with the old engine.

### When OCR is allowed

OCR (RapidOCR) costs hundreds of milliseconds, against a few milliseconds for a template or colour probe. It is used only:

1. **For text the bot cannot enumerate ahead of time**: names that change per player or per day, for example Chạy Thành Tựu's achievement names or Tri Kỷ guest names if their portraits are not enough.
2. **When a feature asks for it explicitly** (`ocr_text` reader), on a small region, at most once per Snapshot, cached. It is never part of screen identification.
3. **For diagnostics**: when a frame stays unknown, the run log may OCR the top dialog's title strip once, so the log says what the screen was. This doesn't affect decisions.
4. **Offline, in the labelling tool**, to suggest labels and template names. This costs nothing at run time.

A number is read with OCR only if its font can't be read with `digits`, and the benchmark reports every reader that falls back to OCR.

### 4. Bot state and decisions

A feature stops being a script. It becomes a **goal** with memory and a list of **rules**:

```python
@register
class ThapThiLuyen(Feature):
    class Memory(BaseModel):          # BotState for this feature, saved in the run history
        floor: int                    # next floor to climb
        battles: int = 0

    def rules(self) -> list[Rule]:
        return [
            Rule("chọn tầng",   on="tower.dialog", when=lambda s, m: s.tab == "Tháp" and m.floor in s.floors,
                 do=lambda s, m: Click(s.floor_button(m.floor)), expect=Selected("floor", m.floor)),
            Rule("vượt tháp",   on="tower.dialog", when=selected_is_target, do=Click("challenge"), expect="tower.confirm"),
            Rule("xác nhận",    on="tower.confirm", do=Click("ok"), expect="tower.battle"),
            Rule("thắng",       on="tower.battle", when=result_is("win"), do=[Click("result_ok"), Advance("floor")]),
            Rule("thua",        on="tower.battle", when=result_is("lose"), do=Finish("thua ở tầng {m.floor}")),
            Rule("đang đánh",   on="tower.battle", do=Wait(1.0)),            # nothing to do yet
        ]
    goal = Done(when=lambda m, p: m.floor > p.end_floor)
    entry = "tower.dialog"            # Navigate there first if not on it
```

*As built in R2 (see "Decision loop"): `Rule(name, on=, when=, do=)` with `when` / `do` taking the top screen's state, `goal(snap)` returning the summary, `entry` a screen name; memory updates go in an action's `then=`.*

The engine picks the first rule whose screen and condition match the Snapshot. Every action carries an **expectation** (a screen, or a state change) and a time limit, checked on the following ticks.

### 5. Built-in behaviour every feature gets

- **Navigation.** A screen graph in YAML (`city --click hud.world--> world_map`, `city --click building tower--> city.building_panel --click "Vào"--> tower.dialog`, any dialog `--✕--> what is under it`). `Navigate("tower.dialog")` finds a path from the current Snapshot, so features stop scripting menus. The current `start_scene` becomes `entry`.
- **Interrupts.** The pop-up guard and panel tidier become rules that run before the feature's rules: harmless pop-ups are closed, cost pop-ups follow their setting, covering panels are collapsed. Same YAML data as today, now inside the loop.
- **Recovery.** Unknown screen, an unmet expectation, or the same Snapshot + Action repeated N times → recovery ladder: close the top dialog with its ✕ → ESC → `Navigate(entry)` → fail with a screenshot and the Snapshot dump. This replaces the per-feature counters ("same army 3 times", "4 misses") with one mechanism.
- **Loop guard and limits.** Max ticks without progress, max run time, Stop/Pause at every tick (the existing `RunControl`).

### 6. What the debug views show

The overlay, the debug panel and the PNGs draw the Snapshot: each part's box, the screen stack, the state values, the rule that fired, the action and its expectation. `run.log` gets one line per decision, for example:

```text
22:20:51 city > tower.dialog{tab=Tháp, page=3, sel=25} | rule "vượt tháp" → click challenge, expect tower.confirm ≤3s
```

## Code layout

| Area | Fate |
| --- | --- |
| `core/window.py`, `core/drivers/*`, `core/capture.py`, `core/input.py`, `core/geometry.py`, `core/templates.py`, `core/vision.py` (Matcher), `core/ocr.py`, `core/locator.py` | **Keep.** They work: the canvas was found HIGH on 44/46 live captures. |
| `engine/control.py`, `engine/runner.py` (`TaskRunner`, `execute`), `engine/manager.py`, `engine/scheduler.py`, `engine/history.py`, `engine/registry.py`, `notify.py`, `hotkeys.py`, `config/`, `ui/` shell, `debug/` sinks | **Keep**, with small adapters (a run's counters come from the feature's Memory). |
| `core/scenes.py` + `profiles/*/scenes/*.yaml` | **Replace** with `perception/` (`parts.py`, `probes.py`, `screens.py`, `readers.py`, `snapshot.py`) + `profiles/*/parts.yaml`, `probes.yaml`, `screens/*.yaml`, `nav.yaml`. Existing templates are reused as part detectors where they still hold. *Perception built in R1; `nav.yaml` is R2.* |
| `engine/context.py` (`RunContext` with `expect_scene` / `wait_for` / `click`) | **Replace** with `engine/loop.py` (tick, decide, act, verify), `engine/actions.py`, `engine/recovery.py`, `engine/navigate.py`. A thin `Actor` keeps click/key/text and the debug recording. *Built in R2 (see "Decision loop"); `RunContext` stays until R4 for the features not ported yet, and the new loop takes its window, switches and sinks through `Actor.from_context`.* |
| `engine/popups.py`, `engine/panels.py` | **Fold into** interrupt rules; their YAML stays. *R2: `engine/interrupts.py` runs them inside the loop; the old `RunContext` path still uses them directly until R4.* |
| `features/*.py` | **Rewrite** one by one as rule sets. The old modules are deleted as each one is ported. |
| `profiles/*/coords/*.yaml` | **Shrink.** Offsets stay only for things that have no recognisable part (a spot on the map). Buttons are found through their screen. |
| `tools/annotate.py` | **Extend** into `tools/perceive.py`: prints and draws the Snapshot for screenshots or the live window, and runs the benchmark below. *Done in R1 (`tools/perceive.py`); `annotate.py` stays until R4.* |

## Phases and gates

Each phase ends at a gate checked on the real game, as in 06.

| Phase | Scope | Exit gate |
| --- | --- | --- |
| **R0 · Clean slate and corpus** | *Started 2026-10-08: tests and scenes removed; `tools/explore.py`; 71 labelled live frames.* Remove the tests and scene definitions (see "Tests" below). Turn `docs/game screenshots/live/` + failure PNGs from `logs/` into a **labelled corpus**: for each image, the expected screen stack and key state values (`corpus/labels.yaml`). Add a capture button to the debug tab that saves the frame and opens a label form. | ≥ 80 labelled frames covering every screen the 12 features touch, including the failure frames of 2026-10-08 |
| **R1 · Perception** | *Started 2026-10-09: 40 part templates (`part_crops.yaml` → `templates/part/`), `parts.yaml` with search regions, `tools/part_report.py`. All 40 separate cleanly over 293 frames, 1.75 ms per search. Digit reader with 8 number fields (`numbers.yaml`): 435 correct reads over the corpus, none wrong. **Gate passed 2026-10-09** (see "Perception"): 56 parts, 17 colour probes, 33 screens (9 bases, 20 dialogs, 3 overlays, the HUD), 12 number fields; stack correct on 364/364 feature frames and 31/31 other frames of 395 labelled, 0 OCR calls, parts ≤ 33 ms, Snapshot with every reader ≤ 81 ms; both kept failure frames read correctly (the third run left no PNG); live check over every screen of the six features (frames 294–423). The live "Kiểm tra" button is `tools/perceive.py --live` for now; wiring it into the UI is left to R2.* Parts, screen rules, readers (colour, template, `digits`), Snapshot, `tools/perceive.py --bench` (per-screen precision/recall, per-reader accuracy, ms per Snapshot, OCR calls). Cut title, icon and digit-glyph templates. Live "Kiểm tra" shows the Snapshot. | Screen stack correct on ≥ 98% of the corpus with **zero OCR calls**, no "unknown" on any frame a feature needs; parts ≤ 80 ms and full Snapshot with its readers ≤ 150 ms; the 3 failing frames above read correctly |
| **R2 · Decision loop** | *Gate passed 2026-10-09 (see "Decision loop"): both pilots ran ~30 min in all on the live game through `TaskRunner` (`tools/run_feature.py`), every start screen reached the goal, Stop ended in 0.01–0.02 s; table below.* `engine/loop.py`, actions with expectations, interrupts, recovery ladder, navigation graph, decision log, overlay of the Snapshot. One pilot feature: **Quay rương** (small), then **Tháp Thí Luyện** (dialog + battle + result). | Both pilots run 30 minutes live; starting each from city, world map, field or with a dialog open still reaches the goal; Stop ends within 1 s |
| **R3 · Port the features** | Only the four remaining supported features, one at a time: Nâng cấp công trình (city and harbour), Nâng cấp khoa kỹ, Clear Phó Bản, Võ Khôi Tháp (long loop). *User decision 2026-10-09: the game's own auto-play covers everything else, so Thám Bảo, Tri Kỷ, Mở thẻ bài, Chạy Thành Tựu and Cày cấp chúa công are not ported; they are deleted in R4. AutoClick stays a plain clicker.* Delete each old module and its coords when its port passes. *2026-10-09: Nâng cấp công trình and Nâng cấp khoa kỹ are ported and were run live (see "Structures and Khoa kỹ"); the account ran out of upgradeable levels, so their gate time is ~18 and ~8 minutes, then the client froze. Clear Phó Bản and Võ Khôi Tháp are next.* | Each feature passes a 30-minute live run with no stuck loop; every recovery is logged with its Snapshot |
| **R4 · Remove the old engine** | Delete `core/scenes.py`, `RunContext`, `scenes/*.yaml`, unused coords; update `selftest.py`, `build.ps1`, docs 06 and the user guide | `--selftest` runs the perception benchmark on the bundled corpus; build passes |

## Tests

**Done 2026-10-08:** the whole `tests/` folder (with its fixture screenshots) and every scene definition (`profiles/ngoalongviet/scenes/*.yaml`) were removed, so no old assumption constrains the new design. They can be restored from commit `39cdd96` if a piece is needed for reference.

Consequences until R1/R4 replace them:

- Features that call `expect_scene` fail with "unknown scene" (no scenes are defined). The app still starts, since the `scenes/` folder is kept empty.
- `build.ps1` runs pytest, and `AutoNgoaLong.spec` / `selftest.py` bundle golden screenshots from `tests/fixtures/`. The build fails until R4 points the self-test at the new corpus.
- `tools/annotate.py` still locates the canvas but names no scene.

**New tests are corpus-driven**: the perception benchmark (labelled frames in `corpus/` → Snapshot), and rule tests that feed a feature a sequence of labelled Snapshots and check the actions it picks. No test asserts click coordinates of a flow.

## Risks and open questions

- **OCR cost.** Handled by design: recognition uses UI parts only, numbers use `digits`, and OCR is limited to the four cases in "When OCR is allowed". The benchmark counts OCR calls so they can't creep back in.
- **Template count.** Recognising by icons and title images means more small templates (titles, icons, digit glyphs per font). The capture-and-label tool cuts them, and `template_crops.yaml` keeps every cut reproducible.
- **Fonts for `digits`.** The game uses a few bitmap fonts (resource bar, page box, levels). Each needs its own 0–9 glyph set; R1 lists which numbers the features actually need, so only those fonts are cut.
- **Animated Flash content.** Some parts flicker or animate (beam, battle). Readers that need stability take the value seen on 2 consecutive ticks.
- **Labelling effort.** The corpus is the main cost of R0. The capture-and-label button keeps it to a few seconds per frame.
- **Transition period.** Old features keep running on `RunContext` until their port passes, so the app stays usable. R4 removes the old path.
- **Decision to confirm:** is the R1 accuracy gate (98%, no unknown on feature screens) the right bar, and which feature should be the second pilot? *R2: the pilots were Quay Tinh Tú and Tháp Thí Luyện.*
