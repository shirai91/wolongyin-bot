# 07 · Screen catalogue (ngoalongviet.com, Coowon)

Measured canvas positions, scenes and on-screen objects from the reference screenshots in [`game screenshots/`](game%20screenshots/). Used by the canvas locator, the scene detector and the debug overlay in [06 Rewrite design](06-rewrite-design.md). Index of all parts: [README.md](README.md)

> **Historical (2026-10-10).** The canvas measurements still hold (the locator uses them). The scenes and object offsets below were the first engine's; the scene detector and the coords files were removed in R4. Screens are now recognised from UI parts and labelled live frames: see [08](08-engine-revamp-plan.md) "Perception", `profiles/ngoalongviet/screens/*.yaml` and `corpus/live/labels.yaml`.

All 6 bright screenshots show the game canvas at exactly **1000×600 px**, but its top-left corner moves by up to 6 px between captures. The dark tower-battle screen defeats a simple black-border search. Most v1.8.0 offsets still land on the right buttons, which confirms they are canvas-relative and still valid on this server.

## Canvas measurements

Screenshot coordinates are pixels in the full Coowon window capture. Measured by the bounding box of non-black rows and columns below the browser toolbar.

| Screenshot | Image size | Canvas top-left | Canvas size | Black-border method |
| --- | --- | --- | --- | --- |
| `main-game.png` | 1536×852 | (273, 109) | 999×600 | OK |
| `dungeon.png` | 1540×857 | (272, 110) | 1000×600 | OK |
| `worldmap-ui.png` | 1541×852 | (270, 107) | 1000×600 | OK |
| `tower-ui.png` | 1542×849 | (272, 106) | 999×600 | OK |
| `tower-ui-enter-confirmation.png` | 1539×854 | (270, 110) | 999×600 | OK |
| `chest-rolling-ui.png` | 1544×861 | (271, 112) | 999×600 | OK |
| `tower-in-dungeon.png` | 1545×855 | about (272, 110) | 668×598 found | **Fails**: the scene's left side is near-black |

The canvas is horizontally centred in the page and top-aligned under the toolbar. "999" widths are the 1-px anti-aliased edge; treat ±2 px as equal.

## Scenes

Each scene is identified by signature anchors (templates that must all match) and owns a list of objects. Canvas coordinates below are approximate centres (±3 px), read from the screenshots; templates still need cutting from these images.

| Scene | Screenshot | Signature (proposed) | Key objects, canvas (x, y) |
| --- | --- | --- | --- |
| `city` | `main-game.png` | HUD + main-city button bottom-right, no modal dialog | settings gear (762, 16); "Thế Giới" world button (944, 493); "Phó Bản" (899, 549); main-city "主城" (959, 549) |
| `field` | `dungeon.png` | HUD + "Lưu Chương" bar bottom-centre | "Lưu Chương" bar (500, 578); right-side arrow (983, 178) |
| `world_map` | `worldmap-ui.png` | HUD + city-name labels, no "Lưu Chương" bar | city markers (many); same bottom-right buttons as `city` |
| `tower.dialog` | `tower-ui.png` | "Tháp Thí Luyện" title bar (499, 111) | floor slots "Tầng 1–5" x 247, y 218 + 25·(n−1); page ◀ (206, 476) ▶ (291, 476); "Vượt Tháp" (367, 469); "Chiến lược" (453, 469); "Tự Vượt Tháp" (538, 469); close ✕ (803, 119) |
| `tower.confirm` | `tower-ui-enter-confirmation.png` | `tower.dialog` + confirm box text "Mỗi lần Vượt tháp mất 3 Thí luyện lệnh" | "确定" OK (453, 331); "取消" cancel (546, 331) |
| `tower.battle` | `tower-in-dungeon.png` | "Tầng N" label top-centre (500, 17) + "Thoát" button | "Thoát" exit (960, 563); "Tự Vượt Tháp" checkbox (741, 563) |
| `chest.wheel` | `chest-rolling-ui.png` | "Tinh Tú" title (501, 72) + wheel | start "开始抽奖" (422, 271); "Tự mở" auto-open (200, 416); "Đổi nhanh" (200, 386); "Mua" buy (782, 143); "Đổi" exchange (782, 398); close ✕ (860, 98) |

**Always-visible anchors** for the canvas locator: the chat input bar "K.Vực" at the bottom-left (canvas ≈ 3..275, 574..596) appears in all 7 screenshots. The top-right gear, sound and music icons (canvas ≈ (762, 16), (792, 16), (822, 16)) appear in 6 of 7; the tower battle hides them.

## v1.8.0 offsets checked against the screenshots

| v1.8.0 offset (`coordinates.yaml`) | Value | Measured object | Measured | Verdict |
| --- | --- | --- | --- | --- |
| `thap.floor_list_x`, `floor_list_y0` | 245, 215 | "Tầng 1" slot | (247, 218) | matches |
| `thap.page_back`, `page_next` | (208, 475), (290, 475) | page ◀ ▶ arrows | (206, 476), (291, 476) | matches |
| `common.confirm_dialog_ok` | (455, 325) | "确定" | (453, 331) | matches (inside button) |
| `thap.exit_tower` | (960, 565) | "Thoát" | (960, 563) | matches |
| `thap.challenge_button`, `sweep_button` | (338, 467), (395, 467) | "Vượt Tháp" button (spans ≈ 331..406) | (367, 469) | both inside the button |
| `quay_ruong.per_spin[0]` | (200, 420) | "Tự mở" | (200, 416) | matches |
| `vo_khoi_thap.buy_chest_tab` | (783, 144) | "Mua" | (782, 143) | matches |
| `vo_khoi_thap.world_map` | (950, 490) | "Thế Giới" | (944, 493) | matches |
| `clear_map.close_recruit` | (803, 120) | tower dialog close ✕ | (803, 119) | same position; dialogs share a close-button slot |

Next: cut anchor and object templates from these screenshots into `profiles/ngoalongviet/templates/`, and add screenshots of the scenes not yet covered (Thám Bảo, Võ Khôi Tháp, Tri Kỷ, card opening, building and research screens).

## Live recon (2026-10-06)

Captured from the live game (Sv 266, account level 80, main city level 100) with `tools/live/livectl.exe` in the background: PostMessage clicks and PrintWindow captures of the Coowon top-level window. Files are in [`game screenshots/live/`](game%20screenshots/live/). Each file is the whole Coowon client area (1349×1391).

**Canvas origin:** client (180, 110) in **every** live capture, and the canvas is exactly 1000×600. The black-border method worked on all of them. For a client width of 1349 the canvas is centred (`(1349 − 1000) / 2 ≈ 175`, plus the window frame) and sits 110 px under the top of the client area. All coordinates below are canvas coordinates: approximate centres, ±3 px.

### Shared HUD (all "map" scenes: city, world map, field, region, resources)

| Element | Canvas (x, y) | Notes |
| --- | --- | --- |
| Avatar, name, level badge | (55, 45); level badge (20, 108) | |
| Resource rows Bạc / Lúa / Lính | box (105..345, 28..90) | text; useful for OCR of silver and gold |
| "Tổng LC" bar, "Mua" | (265, 110), (180, 112) | |
| Top icon row 1 (labels at y ≈ 73) | Bộ Doanh (390, 52), Đấu Trường (445, 52), Ban thưởng (500, 52), Tửu Quán (560, 52), Lôi đài (615, 52), Đập Binh (672, 52), Ngao du (727, 52), Báo danh (785, 52) | **new, not in v1.8.0** |
| Top icon row 2 (labels at y ≈ 130) | Quà mỗi ngày (560, 112), VIP/Rương (615, 112), Hoạt động (672, 112), Tinh Tú (727, 112), Phó Bản event (785, 112) | the row of event icons changes with the events running |
| HUD toggle `>>` / `<<` | (830, 77) | hides both icon rows (`06-field-hud-collapsed.png`) |
| Gear, sound, music | (762, 16), (792, 16), (822, 16) | always visible |
| Vertical tabs Thư / Hạng / Tư / Động / Giúp, "Quốc chiến" | x 865, 895, 925, 955, 985 at y ≈ 60; (873, 103) | |
| Quest tracker | collapse `▶` (818, 178), "Chủ tuyến" (887, 178), "N.vụ ngày" (962, 178); links at (825, 213..265) | collapsing it leaves only `◀` (983, 178) |
| Left notification list | (25..175, 137..222) | text changes all the time; not an anchor |
| "Sắp mở" box, "Đoạt Thành Chiến" | (90, 275..340), (90, 367) | city, world map and region only |
| Chat log overlay | (0..275, 385..545) | **covers buttons that dialogs place in that area** (Tinh Tú "Tự mở") |
| Chat bar | tabs Chung/Nước/Đoàn/K.Vực/Mật Trận doanh (40..245, 559); input "K.Vực" (3..275, 574..596) | always visible: the best locator anchor |
| Bottom bar | Quốc Chính (620, 578), Q.Đoàn (672, 578), Khoa kỹ (717, 578), Trang Bị (765, 578), Tướng (810, 578), N.Vụ (855, 578) | "Quốc Chính" is missing on the field and world map |
| Bottom-right cluster | Thế Giới (944, 493), Tài Nguyên (985, 495), Khu Vực (912, 510), Phó Bản (900, 548), main city 主城 (958, 555) | |
| Toast popup (e.g. "Bảo vật") | box (858..998, 305..450), ✕ (987, 314), "Xem ngay" (927, 435) | shows up on its own; dismiss with ✕ (`00-start-city-with-baovat-popup.png`). Closed automatically by the pop-up guard (`popups.yaml: toast`) |
| Gold question "Bỏ qua thời gian chờ tốn N Vàng. Xác nhận?" | box (≈ 405..595, 225..375); first text line varies per task; "☐ Lần sau không nhắc nhở" (424..592, 310..330); 确定 (418..492, 340..360), centre (455, 350); 取消 (509..583, 340..360), centre (546, 350) | after "Tăng cấp" and similar actions when the in-game warning is on (`51-gold-skip-wait-confirm.png`, canvas at client (45, 110) in that smaller window). Answered by the pop-up guard from the "Dùng vàng bỏ qua thời gian chờ" setting (`popups.yaml: gold_skip_wait`) |
| Reward modal "Chúc mừng ngài được" | box (361..630, 222..377), title (436..556, 229..244), 确定 (458..532, 341..363), centre (495, 352) | opens on its own over any screen (seen over a tower battle, 2026-10-06, `tests/fixtures/screens/popups/`); closed automatically by the pop-up guard (`popups.yaml: reward`) |

Standard dialog frame (Tướng, Khoa kỹ, Q.Đoàn, Trang bị, T.Giúp, Hoạt Động/Báo danh, Tháp Thí Luyện): title plate at (500, 111), "?" (778, 116), **close ✕ (803, 118)**, tab row at y 159 with tabs at x ≈ 222, 298, 375, 452, 530, 605, 683. The large event dialogs (Hoạt động, Bộ Doanh) use a second frame: title (500, 87), close ✕ (855, 85). The small building panel uses a third: ✕ (688, 202).

### Scenes

| Scene | Screenshot | How to reach | Signature elements | Key objects (canvas) |
| --- | --- | --- | --- | --- |
| `city` | `01-city-main.png`, `34-city-panel-collapsed.png` | start, or 主城 (958, 555) | HUD + "Quốc Chính" in the bottom bar + "lv80"/"lv60" building labels + "Sắp mở" box | Dân cư house (160, 300); Tháp thí luyện pagoda (460, 320) with label "lv80" (458, 358); other labelled buildings "lv80" at (425, 105), (512, 270), (677, 333), (772, 398), (900, 465), "lv60" (262, 182), (568, 432), (642, 480) |
| `city.building_panel` | `14-building-panel-dan-cu.png`, `25-tower-building-panel.png` | click a building | panel (300..700, 185..410), name in "< … >" at (490, 208), ✕ (688, 202) | "Tăng cấp" (375, 323) **(do not press)**; Dân cư: "Tuần Tra" (521, 321); Tháp: "Tháp Thí Luyện" (521, 321), "Luyện" (621, 321), "Điểm Tướng" (521, 366); cost "Tốn bạc" (365, 365), time (380, 383) |
| `tower.dialog` | `26-tower-dialog.png` | city → pagoda (460, 320) → "Tháp Thí Luyện" (521, 321) | title "Tháp Thí Luyện" (500, 111); tabs Tháp (222, 159), Luyện (297, 159), Điểm tướng (375, 159); "Top 10" table (700, 189..455) | as in the table above. The list shows **only unlocked floors**: Tầng 1–5 here, Tầng 1–6 after the next floor was cleared (`26c-tower-dialog-6-floors.png`, 2026-10-06). It has room for 10 slots per page (y 218..443), as v1.8.0 assumed; empty slots are flat dark panel (grey std < 1). Pager: ◀ (200..212, 467..485), page box inner (220..276, 468..484), ▶ (285..297, 467..485); templates `tower.pager.*`. The page number is pure grey, drawn ~166 or ~255 bright depending on the box state, same shape; the chat overlay can run over ◀ and the box (its text is coloured, so it is masked out). The ▶ is only on the "Tháp" tab: the Luyện / Điểm tướng tabs share the dialog title (`26d-tower-diem-tuong-tab.png`) — never type there (Enter can press "Điểm tướng", which spends Hồn Tướng). Page box "1" (247, 476). "Thí luyện lệnh: N" (390, 382) can be read as the token count. ✕ (803, 118) |
| `world_map` | `02-world-map.png` | "Thế Giới" (944, 493) | HUD, no "Quốc Chính" in the bottom bar, Chinese city labels | cities: 汉中 (275, 285), 天水 (305, 175), 长安 (445, 210), 洛阳 (560, 250), 许昌 (585, 310), 濮阳 (685, 235), 下邳 (715, 300), 宛城 (578, 370), 梓潼 (405, 350), 成都 (350, 415), 永安 (462, 440), 江陵 (562, 455), 襄阳 (645, 445), 武陵 (518, 515), 长沙 (635, 510), 江夏 (725, 495), 柴桑 (780, 440), 建业 (870, 420), 寿春 (745, 365); farms 董卓农场 (497, 280), 刘璋农场 (497, 390). Võ Khôi Tháp marker (pagoda labelled "Võ Khôi Tháp") at about (233, 340): it was missing in the morning capture `02-world-map.png` but shown on 2026-10-06 afternoon (verify-p2). The map ignores background drag messages. |
| `vo_khoi_thap` (Võ Khôi Tháp) | `44-vo-khoi-thap.png` | world map → marker (235, 330); the screen takes **more than 4 s** to load | full screen, HUD hidden; title "Võ Khôi Tháp" (500, 12), "Thoát" (494, 577) | boss arrows "<" (265, 308) and ">" (730, 308), boss name + level (500, 424), "Tấn công" (502, 500; button 462..543 × 482..519), "Thoát" (494, 577) returns to the world map |
| `world_map.city_info` | `29-world-city-click.png` | click a city marker | dialog titled with the city's name (500, 160), table "Thuộc / Đặc điểm / …" | "Vào thành" (660, 205), "Dời đến" (660, 241), "Mở rộng" (660, 277), ✕ (690, 158) |
| `region` (Khu Vực) | `12-khu-vuc.png` | "Khu Vực" (912, 510) | own castle with the player's name (530, 345); bottom bar "Tài nguyên / Khu trước / Khu sau" | page box (500, 548), "Đi đến" (558, 548) |
| `resources` (Tài Nguyên) | `31-tai-nguyen.png` | "Tài Nguyên" (985, 495) | rice fields and mines; bottom "Về khu vực" (398, 578) | "Khu trước" (478, 578), "Khu sau" (565, 578) |
| `field` (Phó Bản) | `04-pho-ban.png`, `08-dungeon-map-cong-ton.png` | "Phó Bản" (900, 548) | dungeon-name bar (500, 578) with « (423, 578) and » (578, 578); no "Quốc Chính" | army squads (≈ 40×40 px blocks, red = enemy, yellow = cleared); » appears only when a next dungeon exists |
| `field.dungeon_list` | `05-pho-ban-list.png` | click the name bar (500, 578) | popup titled "Phó bản" (500, 372) | entries at x 490, y 397 + 25·k: Khăn Vàng, Thí nghiệm 1, Thí nghiệm 2, Đổng Trác, Viên Thuật, Công Tôn, Lưu Chương; scroll ▲ (574, 392) ▼ (574, 552) |
| `field.switch_banner` | `07-dungeon-switch-banner.png` | « or » on the name bar | transient banner "<Name> 100%" (500, 300) | disappears after ≈ 2 s |
| `field.army_dialog` | `09-dungeon-army-dialog.png` | click an army | title "<army> Lv56" (515, 185), portrait (410, 248) | "Tấn công" (584, 304), "Chiến lược" (584, 374), checkbox "Phát chiến đấu" (550, 412), ✕ (638, 185) |
| `event_dungeon` | `13-event-dungeon-from-top-pho-ban.png` | top icon "Phó Bản" (785, 112) | dark river map, progress label 活动副本 (500, 300) | separate event dungeon; v1.8.0 does not know about it |
| `chest.wheel` (Tinh Tú) | `19-tinh-tu.png` | top icon "Tinh Tú" (727, 112) | title "Tinh Tú" (500, 72), wheel centre 开始抽奖 (422, 262) | "Mua" (782, 143), "Đổi" (782, 398), "Hướng dẫn" (200, 356), "Đổi nhanh" (200, 386), "Tự mở" (200, 416) **under the chat overlay**, chest tiers (290..740, 478), ✕ (860, 98), timer (425, 102) |
| `research` (Khoa kỹ) | `16-khoa-ky.png` | bottom bar "Khoa kỹ" (717, 578) | title "Phủ Sách Lược" (500, 111), tabs "Khoa kỹ" (222, 159), "Chiêu binh" (297, 159), "Danh Sách Nghiên Cứu" (425, 189) | grid of 6 columns at x 238, 310, 382, 455, 527, 598 (step ≈ 72) and rows at y 248, 322, 395, with more rows behind a scroll bar ▲ (646, 228) ▼ (646, 422); info panel (740, 255..440); "Tăng cấp" (615, 472) |
| `tavern` (Tửu Quán / Tri Kỷ) | `17-tuu-quan-tri-ky.png` | top icon "Tửu Quán" (560, 52) | full-screen tavern, city name plate (498, 15), "Lần hội đàm: N" (455, 534) | visitor cards at (92, 115) and (225, 115) (box ≈ 105×125, name at y 165); "Mời thần" (715, 492), progress bar "Điểm thần tiên" (515, 492), "Thành khác" (443, 563), "Mời rượu" (585, 563), "Mời nhanh" (728, 563), "Thoát" (938, 570); "Miễn phí" timers (y 590) |
| `tavern.talk_result` | `18-tuu-quan-visitor.png` | **clicking a visitor card talks to them at once** | box (225..775, 215..385), visitor's name "< … >" (425, 250) | reward line (425, 327), single button 确定 (680, 355). ESC does not close it. |
| `generals` (Tướng) | `22-tuong.png`, `23-tuong-chieu-mo.png` | bottom bar "Tướng" (810, 578) | title "Tướng" (500, 111) | tabs Tướng (222, 159), Huấn Luyện (298), Trang Bị (375), Trận (452), Chiêu Mộ (533), Quân Ấn (605); Chiêu Mộ page ◀ (452, 464) ▶ (525, 464), "Trọng dụng" (610, 465) |
| `inventory` (Trang bị) | `41-trang-bi.png` | bottom bar "Trang Bị" (765, 578) | title "Trang bị" (500, 111), "Vật phẩm kho" (425, 189) | tabs Kho, Tài Nguyên, Cửa hiệu, Bảo vật, Cường hóa, Phái, Tướng khí (x 222..683, y 159); 8×N item grid from (222, 230), step 54 px |
| `legion` (Q.Đoàn) | `38-q-doan.png` | bottom bar "Q.Đoàn" (672, 578) | title "QuânĐoàn" (500, 111) | ✕ (803, 118) |
| `help` (T.Giúp) | `39-giup.png`, `40-giup-maincity.png` | vertical tab "Giúp" (985, 60) | title "T.Giúp" (500, 111), Chinese topic tree (245, 200..470) | in-game help; the 主城 node lists 征收 / 官职 / 市场 / 作坊 |
| `palace` (Quốc Chính) | `35-quoc-chinh.png` | bottom bar "Quốc Chính" (620, 578) | full-screen throne room, ruler's name (500, 128) | Nội Chính (95, 43), Quân Sự (95, 93), Chức (95, 143), Quốc Khố (95, 193), "Thoát" (513, 581) |
| `events` (Hoạt động) | `10-hoat-dong-events.png` | top icon "Hoạt động" (672, 112) | big frame, title "Hoạt động" (500, 87) | event list on the left (215, 145..405); ✕ (855, 85) |
| `daily` (Báo danh) | `42-bao-danh.png`, `43-hoat-dong-tab.png` | top icon "Báo danh" (785, 52) | title "Hoạt Động" (500, 111), tabs T.Tựu (222, 159), N.Động (297), Hạng (375), Hoạt Động (452) | "Năng động hiện có" (625, 193); "Báo danh" (757, 367) claims a reward; exchange rows "Đổi" (733, 241 + 70·k) |
| `daily`, tab T.Tựu (Thành tựu) | `tests/fixtures/screens/thanh_tuu/tab-thanh-tuu-canvas.png` (user screenshot, scaled to 1000x600) | vertical "T.Tựu" (923, 65) on the city and the field, then tab T.Tựu (222, 159) | same frame as `daily` | dungeon list (192..332, 205..417), ▲ (337, 213), ▼ (337, 471); "Tiến độ" (362, 183); Đánh giá lines (367..645, 243..343), ✓ box x 380; Khiêu chiến cards from x 369, pitch 70; "Điểm" (680..800, 440..470); "Nhận thưởng" (734, 478). All in `coords/thanh_tuu.yaml` |
| `bo_doanh` | `21-bo-doanh.png` | top icon "Bộ Doanh" (390, 52) | big frame, title "Bộ Doanh" (500, 87) | 开始挑战 (660, 315) costs 50000 Vàng; ✕ (855, 85) |
| `dap_binh` | `20-dap-binh.png` | top icon "Đập Binh" (672, 52) | 4×4 grid of jars (420..685, 255..520) | hammer (845, 300), bomb (845, 445), "Rời khỏi" (940, 575) |
| `ngao_du` | `11-ngao-du.png` | top icon "Ngao du" (727, 52) | dice board, dice "Chuyển" (472, 515) and "Cưỡng chế" (588, 515) | "Hắc điểm" (750, 565), "Hướng dẫn" (842, 565), "Thoát" (930, 565), "Còn: N/36" (518, 592) |
| `loi_dai` (Lôi đài) | `27-loi-dai.png` | top icon "Lôi đài" (615, 52) | 擂台赛 / 等待开赛 title (560, 70), bracket | 报名 (393, 553), 助威 (512, 553), 说明 (823, 550), 退出 (912, 550) |
| `dau_truong` (Đấu Trường) | `28-dau-truong.png` | top icon "Đấu Trường" (445, 52) | 竞技场 title (500, 32), three opponent cards | "Khiêu chiến" (353 / 495 / 637, 325), "Thay thế đối thủ" (498, 386), "Mô tả" (830, 560), "Thoát" (937, 560) |

**Not reached** with allowed navigation:

- **Võ Khôi Tháp fight screens**: the boss screen is catalogued above (scene `vo_khoi_thap`). In the morning recon the marker was missing (`03-world-map-after-v180-vkt-click.png` shows nothing changed); by the afternoon it was on the map at about (233, 340) and `vkt_entry` opened the boss screen. The screens after "Tấn công" (Lập đội, Đánh, rewards) and the out-of-tokens / full-silver popups were not reached.
- **Thám Bảo**: no HUD icon, bottom-bar button or dialog tab is labelled Thám Bảo. "Ngao du" is a dice board, not v1.8.0's Thám Bảo flow.
- **Card opening (thẻ bài)**: not found under the HUD icons, Trang bị or Tướng.
- **Hải cảng (harbour)**: the city view does not scroll (drag ignored), and nothing in the HUD switches to a harbour.

### v1.8.0 offsets checked live

| v1.8.0 offset | Value | Live object | Live (canvas) | Verdict |
| --- | --- | --- | --- | --- |
| `vo_khoi_thap.world_map` | (950, 490) | "Thế Giới" | (944, 493) | **confirmed**: opens the world map |
| `vo_khoi_thap.vkt_entry` | (235, 330) | "Võ Khôi Tháp" marker | (233, 340) | **confirmed** (2026-10-06, verify-p2): opens the boss screen after more than 4 s. The morning recon found empty terrain here (marker not shown then): the bot waits for the `vo_khoi_thap` scene and stops if it does not open |
| `vo_khoi_thap.boss_prev`, `boss_next` | (265, 305), (730, 305) | "<", ">" | (265, 308), (730, 308) | confirmed (position) |
| `vo_khoi_thap.attack` | (475, 490) | "Tấn công" | (502, 500) | inside the button (not pressed live) |
| `vo_khoi_thap.exit` | (495, 575) | "Thoát" | (494, 577) | **confirmed**: back to the world map |
| `vo_khoi_thap.buy_chest_tab` | (783, 144) | Tinh Tú "Mua" | (782, 143) | confirmed (not clicked) |
| `thap.open_tower` (clicked twice) | (525, 320) | 1st click: empty city ground; 2nd click: panel button "Tháp Thí Luyện" | building (460, 320); button (521, 321) | **moved**: the building is at (460, 320). The second click only works once the panel is open. |
| `thap.floor_list_x`, `floor_list_y0` | 245, 215 | "Tầng 1" | (247, 218) | confirmed; 10 slots per page, only unlocked floors filled |
| `thap.page_back`, `page_next` | (208, 475), (290, 475) | ◀ ▶ | (206, 476), (291, 476) | position confirmed; effect not verified (the game froze, see below) |
| `thap.ttl_send_box` | (255, 477) | page-number box | (247, 476) | confirmed |
| `thap.challenge_button` / `sweep_button` | (338, 467) / (395, 467) | "Vượt Tháp" (331..406) | (367, 469) | both inside "Vượt Tháp". "Tự Vượt Tháp" is (538, 469). |
| `nang_cap_cong_trinh.upgrade`, `close` | (375, 325), (690, 204) | "Tăng cấp", ✕ | (375, 323), (688, 202) | confirmed (upgrade not pressed) |
| `nang_cap_cong_trinh.thanh_chinh_buildings` [0], [1] | (160, 300), (230, 320) | Dân cư panel | – | confirmed |
| … [5] | (455, 300) | Tháp thí luyện panel | – | confirmed |
| … [4], [2], [6], [7], [8], [9], [10] | (144, 258), (310, 360), (520, 340), (550, 465), (640, 420), (675, 500), (770, 470) | no panel opens | – | **missing**: the city layout differs from v1.8.0's |
| … [11], [12], [13], [14], [15] | (770, 120), (545, 72), (660, 123), (540, 131), (883, 243) | now **HUD elements**: Tinh Tú icon, Tửu Quán label, Hoạt động icon, Quà mỗi ngày icon, quest tracker | – | **dangerous**: clicking them would open HUD dialogs; not tested |
| … [3], [16], [17] | (310, 260), (800, 300), (900, 350) | – | – | not tested |
| `nang_cap_cong_trinh.hai_cang_buildings` | 6 points | harbour not found | – | not verified |
| `nang_cap_cong_trinh.city_level_region` | [4, 96, 36, 34] | city level badge under the avatar ("80", "110") | – | OCR reads it on 01, 34 (2026-10-06). The Thành chính upgrade now reads this and every "lvN" building label instead of the fixed points above |
| `nang_cap_cong_trinh.label_click_offsets` | (0, -14), (0, 0) | from a "lvN" label centre | – | v1.8.0 points that opened a panel sit 28-38 px above their label; not yet clicked live |
| `nang_cap_khoa_ky.open_building` | (536, 170) | city ground | – | **moved**: use the bottom-bar "Khoa kỹ" (717, 578), which opens the research dialog directly |
| `nang_cap_khoa_ky.open_tech_panel` | (520, 320) | not needed | – | **obsolete** |
| `nang_cap_khoa_ky.rows_first_item`, `item_step_x` | (240, 250 / 320 / 400), step 75 | first icons | (238, 248 / 322 / 395), step ≈ 72 | confirmed with drift: column 6 lands at 615 against an icon centre of 598 (icon 568..628), so still inside. Use step 72. |
| `nang_cap_khoa_ky.upgrade` | (615, 470) | "Tăng cấp" | (615, 472) | confirmed (not pressed) |
| `quay_ruong.per_spin[0]` / `[2]` | (200, 420) | "Tự mở" | (200, 416) | position confirmed, but **the chat overlay draws over it**; whether the click reaches the button is untested |
| `quay_ruong.per_spin[1]` | (440, 360) | a wheel item (bag) | start button is (422, 262) | **moved/unknown**: probably a result popup v1.8.0 expected |
| `tri_ky.refresh_purple`, `refresh_orange` | (570, 475), (670, 475) | "Điểm thần tiên" bar / near "Mời thần" | buttons are "Mời rượu" (585, 563), "Mời nhanh" (728, 563), "Mời thần" (715, 492) | **moved**: the tavern layout changed |
| `tri_ky.talk`, `talk_confirm`, `leave` | (480, 480), (600, 370), (735, 480) | – | clicking a visitor card talks at once; result 确定 (680, 355) | **flow changed**: no separate talk/leave buttons |
| `clear_map.close_recruit` | (803, 120) | standard dialog ✕ | (803, 118) | confirmed for every standard dialog |
| `clear_map.next_map` | (580, 580) | » on the dungeon bar | (578, 578) | confirmed (only shown when a next dungeon exists) |
| `mo_the_bai.*`, `tham_bao.*`, `vo_khoi_thap` boss/attack/shop | – | screens not reached | – | not verified |

**Things v1.8.0 does not know about**: the HUD toggle `>>` (830, 77) and the collapsible quest tracker, both of which change what is on screen; toast popups such as "Bảo vật"; a separate event dungeon (top "Phó Bản" icon); the chat overlay that covers the lower-left of dialogs; Tri Kỷ talking on a card click; a trial-tower floor list that shows only unlocked floors (10 slots per page, as v1.8.0); and the many HUD dialogs (Bộ Doanh, Đập Binh, Ngao du, Lôi đài, Đấu Trường, Báo danh, Hoạt động). Features should check for the `city` scene first and close toasts and dialogs before they start.

**Incidents during recon**:

1. Clicking the first Tri Kỷ visitor card (92, 110) did not open a menu: it **used one talk** ("Lần hội đàm" 6 → 5, reward "Dân tâm*8"). The result box was closed with its only button, 确定.
2. After the trial-tower dialog was reopened at the end, **the game stopped rendering and stopped accepting input**. PrintWindow and a screen copy both show the same frozen frame for more than 5 minutes, while the Flash process still uses CPU. The page was not refreshed, as the policy requires. The last capture is `zz-final-frozen-tower-dialog.png`, and the clean city before that is `01-city-main.png` / `34-city-panel-collapsed.png`. The rewrite should detect a frozen canvas (identical frames over N seconds) and stop with a clear message.
