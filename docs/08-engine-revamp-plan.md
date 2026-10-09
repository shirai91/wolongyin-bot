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

### 2 · Structures (city and harbour)

- Click a building on the map → **building panel** (same panel in the city and the harbour: ✕ at 687,202, "Tăng cấp" at 373,325, "Tốn bạc N" and "Thời gian h:mm:ss" under it, plus building-specific buttons such as Tháp Thí Luyện / Luyện / Điểm Tướng / Võ Hồn or Ra Khơi Mậu Dịch).
- Each press of Tăng cấp upgrades one level **instantly** (cost and time grow: 30091 → 31727 over 5 presses). The red silver digits in the top-left bar flashed during the presses.
- **The gold "skip wait" prompt does not show on this account because the player already chose "use gold to skip" in the game, and that choice resets on every browser reload.** So the bot can't assume either state. It must handle both: no prompt, or the `gold_skip_wait` confirm (answered by the user's setting).
- Buildings carry a "lvN" label on the map; the harbour is reached with "Vào Hải Cảng" (city) and left with 主城. Which buildings to upgrade (and their map positions) is per-account; the map labels give the levels.

### 3 · Campaign (Phó Bản)

- Field map with the **light-blue beam** over the next army. Clicking any army opens `field.army_dialog` (small frame, ✕ 638,184): name, level, troops "N/30", checkbox **"Phát chiến đấu"** (play the battle; ticked label turns white), "Tấn công" (584,304).
- With "Phát chiến đấu" **off**, the fight resolves at once with no battle screen; the dialog stays open and the troop count drops (30/30 → 29/30). **With it on**, the battle screen opens (`第N回合` round label at the top, "Kết quả / Tải / Nhanh" buttons bottom right), then a **战胜 / 战败 box** with "Xem lại" and "Thoát" (540,408). Thoát returns to the field.
- Rule: keep "Phát chiến đấu" off for speed and read the outcome from the troop count and whether the army is still there. Use the battle path only when the result has to be known exactly.
- The same battle screen and result box appear in Thám Bảo, so "battle" is one reusable part.

### 4 · Trial tower (Tháp Thí Luyện)

- Building panel "Tháp Thí Luyện" → medium dialog with tabs Tháp / Luyện / Điểm tướng (it reopens on the last tab and page).
- **Floor list with pagination**: page N shows floors 10(N−1)+1 … 10N, and only floors the player has reached are listed (page 15 showed only "Tầng 141"). Page box at 247,476 with ◀ 206,476 and ▶ 291,476. **Typing works**: double-click the box, Backspace ×3, type the page, Enter (page 3 → Tầng 21–30). Read the page with `digits` and the list rows with row templates.
- **Climb ("Vượt Tháp", 367,469)** → confirm box (3 Thí luyện lệnh) → **floor map** (`Tầng N` at the top, kill log top-left, bottom-right "☑ Tự Vượt Tháp" checkbox at 741,563, "Phát chiến báo", "Thoát"). Nothing moves until a click: **ensure the "Tự Vượt Tháp" checkbox is ticked, then click the first enemy once**; the unit then fights every guard by itself. The floor maps differ (own unit at 268,480 on Tầng 141 and 378,440 on Tầng 21), so the first enemy must be found on the map, not clicked at a fixed offset.
- **Result**: win = 战胜 box with only "Thoát", which returns straight to the tower dialog. Loss = 战败 box with "Tiếp tục" and "Thoát"; leaving then asks "Chưa hoàn thành vượt Tháp, xác nhận thoát?" (确定 / 取消) and returns to the dialog.
- **Sweep ("Tự Vượt Tháp" button in the dialog, 538,469)**: a side panel "Hãy nhập số lần vào" with a count box (700,253), cost "Vàng*0", floor, reward preview, 确定 / 取消, then a confirm. It completes instantly (2 runs = 6 Thí luyện lệnh) with no battle. **Only floors already won can be swept**; this is the user's requirement for the "Tự vượt tháp" mode.

### 5 · Tinh Tú (chest wheel)

- Top icon "Tinh Tú" → large dialog (✕ 859,98). **State**: chest count "X N" (Mua at 783,143), coin count "X N" (Đổi), the scrolling reward log, and the left buttons Hướng dẫn / Đổi nhanh / **Tự mở**.
- "开始抽奖" (420,268) opens one chest, and a reward line appears in the log.
- "Mua" → quantity box (+ / − / Tối đa) "Dùng 240 vàng, mua 1 Rương may mắn" → 确定 (438,365).
- "Tự mở" → "Tự mở N Rương may mắn không?" with a quantity box → 确定. The button then reads **"Ngưng mở"** (button label = auto-open state) and opens about one chest every 4 s.
- **Fast-open trick** (user tip, verified): toggle Tự mở on and off quickly. 5 cycles of Tự mở → 确定 → Ngưng mở, 0.3 s apart, took 5.9 s and opened 4 chests: about 1.5 s per chest instead of 4 s. The loop stops when the chest count reads 0.

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
- **Not yet observed**: the 2-billion silver alert (the account has 959 million), running out of Quân lệnh (593 left, 23 per round), and a lost or timed-out boss fight. They need a long run, and the R2 pilot of this feature should capture them.

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
| `selected_tab` | colour probe on each tab (the highlighted one is brighter) | which tab of `tower.dialog` is open: Tháp / Luyện / Điểm tướng |
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
| `core/scenes.py` + `profiles/*/scenes/*.yaml` | **Replace** with `perception/` (`parts.py`, `screens.py`, `readers.py`, `snapshot.py`) + `profiles/*/parts.yaml`, `screens/*.yaml`, `nav.yaml`. Existing templates are reused as part detectors where they still hold. |
| `engine/context.py` (`RunContext` with `expect_scene` / `wait_for` / `click`) | **Replace** with `engine/loop.py` (tick, decide, act, verify), `engine/actions.py`, `engine/recovery.py`, `engine/navigate.py`. A thin `Actor` keeps click/key/text and the debug recording. |
| `engine/popups.py`, `engine/panels.py` | **Fold into** interrupt rules; their YAML stays. |
| `features/*.py` | **Rewrite** one by one as rule sets. The old modules are deleted as each one is ported. |
| `profiles/*/coords/*.yaml` | **Shrink.** Offsets stay only for things that have no recognisable part (a spot on the map). Buttons are found through their screen. |
| `tools/annotate.py` | **Extend** into `tools/perceive.py`: prints and draws the Snapshot for screenshots or the live window, and runs the benchmark below. |

## Phases and gates

Each phase ends at a gate checked on the real game, as in 06.

| Phase | Scope | Exit gate |
| --- | --- | --- |
| **R0 · Clean slate and corpus** | *Started 2026-10-08: tests and scenes removed; `tools/explore.py`; 71 labelled live frames.* Remove the tests and scene definitions (see "Tests" below). Turn `docs/game screenshots/live/` + failure PNGs from `logs/` into a **labelled corpus**: for each image, the expected screen stack and key state values (`corpus/labels.yaml`). Add a capture button to the debug tab that saves the frame and opens a label form. | ≥ 80 labelled frames covering every screen the 12 features touch, including the failure frames of 2026-10-08 |
| **R1 · Perception** | *Started 2026-10-09: 40 part templates (`part_crops.yaml` → `templates/part/`), `parts.yaml` with search regions, `tools/part_report.py`. All 40 separate cleanly over 293 frames, 1.75 ms per search. Digit reader with 8 number fields (`numbers.yaml`): 435 correct reads over the corpus, none wrong.* Parts, screen rules, readers (colour, template, `digits`), Snapshot, `tools/perceive.py --bench` (per-screen precision/recall, per-reader accuracy, ms per Snapshot, OCR calls). Cut title, icon and digit-glyph templates. Live "Kiểm tra" shows the Snapshot. | Screen stack correct on ≥ 98% of the corpus with **zero OCR calls**, no "unknown" on any frame a feature needs; parts ≤ 80 ms and full Snapshot with its readers ≤ 150 ms; the 3 failing frames above read correctly |
| **R2 · Decision loop** | `engine/loop.py`, actions with expectations, interrupts, recovery ladder, navigation graph, decision log, overlay of the Snapshot. One pilot feature: **Quay rương** (small), then **Tháp Thí Luyện** (dialog + battle + result). | Both pilots run 30 minutes live; starting each from city, world map, field or with a dialog open still reaches the goal; Stop ends within 1 s |
| **R3 · Port the features** | In order of failure pain: Nâng cấp công trình, Clear Phó Bản, Cày cấp chúa công, Nâng cấp khoa kỹ, Võ Khôi Tháp, Thám Bảo, Tri Kỷ, Mở thẻ bài, Chạy Thành Tựu. AutoClick stays a plain clicker. Delete each old module and its coords when its port passes. | Each feature passes a 30-minute live run with no stuck loop; every recovery is logged with its Snapshot |
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
- **Decision to confirm:** is the R1 accuracy gate (98%, no unknown on feature screens) the right bar, and which feature should be the second pilot?
