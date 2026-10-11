# Hướng dẫn sử dụng AutoNgoaLong v2

AutoNgoaLong v2 là bản viết lại bằng Python của AutoNgoaLong v1.8.0, dùng cho game Ngọa Long (máy chủ ngoalongviet.com) chạy trong trình duyệt **Coowon** trên Windows. Bot click và chụp màn hình **ở chế độ nền**: bạn vẫn làm việc khác trên máy được, chỉ cần cửa sổ game **còn lộ ra một phần** (xem mục 3).

Điểm khác lớn nhất so với v1.8.0:

- **Không còn bấm F9 và nhập "Tọa độ gốc".** Bot tự tìm cửa sổ Coowon và tự tìm khung game 1000×600 trong cửa sổ trên mỗi lần chụp, kể cả khi bạn kéo cửa sổ hoặc phóng to/thu nhỏ trang.
- **Bot nhìn rồi mới làm.** Mỗi nhịp (khoảng 0,3 giây) bot chụp game, nhận ra màn hình đang hiện (bản đồ, các bảng đang mở chồng lên nhau, các thông báo) và đọc các con số cần thiết (số rương, Bạc, Quân lệnh, tầng, cấp...), rồi mới quyết định click gì. Vì vậy **bắt đầu ở màn hình nào cũng được**: bot tự đi tới màn hình cần, tự đóng bảng lạ đang che.
- **Mỗi click đều được kiểm tra.** Sau mỗi thao tác bot chờ kết quả mong đợi (bảng mở ra, con số thay đổi...). Không thấy thì bot tự gỡ rối (đóng bảng, ESC, đi lại từ đầu); vẫn không được thì dừng, ghi lý do và chụp ảnh.
- **Mọi lần chờ đều có giới hạn thời gian** và nút Dừng có tác dụng ngay (dưới 1 giây).
- **Phím tắt không chiếm phím F trần.** Mặc định Dừng = `Ctrl+Alt+F10`, bật/tắt gỡ lỗi = `Ctrl+Alt+D`.
- **Không còn hộp thoại (MsgBox) khi chạy xong.** Kết quả ghi ở khung Nhật ký, thẻ Lịch sử và (tùy chọn) thông báo khay hệ thống, Telegram, Discord.

Bot có **6 tính năng** cho game và AutoClick (mục 4). Các hoạt động khác (Thám Bảo, Tri Kỷ, Mở thẻ bài, Chạy Thành Tựu, Cày cấp chúa công) đã được bỏ: chế độ tự chơi của game làm được việc đó.

## 1. Cài đặt

1. Giải nén thư mục `AutoNgoaLong` vào nơi bạn có quyền ghi, ví dụ `D:\AutoNgoaLong`. Không nên để trong `C:\Program Files`, vì bot ghi cài đặt, nhật ký và lịch sử ngay trong thư mục này.
2. Trong thư mục có:

    | Tệp / thư mục | Dùng để |
    | --- | --- |
    | `AutoNgoaLong.exe` | Chạy bot |
    | `_internal\` | Thư viện của chương trình và bộ ảnh mẫu cho `--selftest`. Không sửa, không xóa. |
    | `profiles\ngoalongviet\` | Dữ liệu nhận diện game: các phần giao diện (`parts.yaml`), màn hình (`screens\*.yaml`), đường đi giữa các màn hình (`nav.yaml`), popup tự đóng (`popups.yaml`), ảnh mẫu (`templates\`). Sửa được bằng Notepad, không cần build lại. |
    | `HUONG-DAN.md` | Tài liệu này |
    | `settings.toml` | Cài đặt của bạn (tự tạo khi chạy lần đầu) |
    | `logs\` | Nhật ký và ảnh chụp mỗi lần chạy (tự tạo) |
    | `data\` | Lịch sử chạy và chuỗi tác vụ (tự tạo) |

3. Muốn giữ cài đặt của bản cũ: chép tệp `NgoaLong_data.ini` của v1.8.0 vào cùng thư mục với `AutoNgoaLong.exe` **trước lần chạy đầu tiên**. Bot nhập các giá trị còn dùng được (số rương, tầng tháp, boss...) một lần duy nhất rồi lưu sang `settings.toml`. Cài đặt của các tính năng đã bỏ được bot tự xóa khi mở; bước của chuỗi tác vụ dùng tính năng đã bỏ được tắt (vẫn thấy trong chuỗi).
4. Kiểm tra bản cài (không cần mở game): mở PowerShell trong thư mục bot và chạy

    ```powershell
    Start-Process .\AutoNgoaLong.exe -ArgumentList "--selftest" -Wait
    Get-Content .\selftest-report.txt -Encoding UTF8
    ```

    Bot kiểm tra hồ sơ `profiles\`, 7 tính năng, trình điều khiển cửa sổ, giao diện, và **cho bot nhận diện lại 57 ảnh game đi kèm** (mỗi ảnh phải ra đúng màn hình và đúng các con số đã ghi). Mỗi dòng `OK` là một phép kiểm tra đạt; dòng cuối cho biết tổng số lỗi. Dòng `SKIP [perception]` nghĩa là bản build không kèm bộ ảnh, bỏ qua phần nhận diện. Nếu có dòng `FAIL`, xem mục Xử lý sự cố.

Windows Defender hoặc SmartScreen có thể cảnh báo vì tệp exe chưa được ký số. Chọn **More info → Run anyway** nếu bạn tải bot từ nguồn bạn tin cậy.

## 2. Lần chạy đầu tiên

1. Mở **Coowon**, vào game Ngọa Long trên ngoalongviet.com. Nếu Coowon báo "Adobe Flash Player was blocked", bấm **Run this time**.
2. Để trang game ở mức thu phóng 100% (Ctrl+0). Bot vẫn chạy được ở mức khác, nhưng 100% là mức đã được kiểm tra kỹ nhất.
3. Chạy `AutoNgoaLong.exe`. Cửa sổ chính gồm:
    - Khung **Cửa sổ game (Coowon)**: danh sách cửa sổ game, nút **Tìm lại**, nút **Kiểm tra**, và hai dòng trạng thái *Khung game* và *Màn hình*.
    - Các thẻ: **Tính năng**, **Nhiều cửa sổ**, **Chuỗi tác vụ**, **Gỡ lỗi**, **Lịch sử**, **Thông báo**, **Cài đặt**.
    - Khung **Nhật ký** ở dưới cùng.
    - Thanh trạng thái nhắc phím tắt Dừng và Gỡ lỗi.
4. Bấm **Kiểm tra**. Nếu thấy `Khung game: (…) 1000×600 ×1.00 - tin cậy` và một dòng *Màn hình* như `Màn hình: city{…} | hud{silver=…, quan_lenh=…, level=140}` là bot đã nhìn thấy game. Bạn đã sẵn sàng chạy tính năng.

Dòng *Màn hình* là cái bot "thấy": màn hình nền (ví dụ `city` = Thành chính, `harbour` = Hải cảng, `field` = bản đồ Phó Bản, `world_map` = Bản đồ thế giới), rồi các bảng đang mở chồng lên (dấu `>`, bảng trên cùng ở cuối, ví dụ `city > chest.wheel{chests=99}` = bảng Tinh Tú có 99 rương), các thông báo (dấu `+`), và các con số trên thanh trên cùng (`hud`). Một bảng bot không biết ghi là `unknown`; bot vẫn đóng được nó bằng nút ✕.

## 3. Nhận cửa sổ game

- Bot tự liệt kê các cửa sổ Coowon có tiêu đề chứa "Ngọa Long" (ví dụ `Sv 266 Ngọa Long Việt Nam - Coowon Browser`). Không cần F9, không cần chọn bằng chuột.
- Có nhiều cửa sổ: chọn đúng cửa sổ trong danh sách. Lần sau bot tự chọn lại cửa sổ có cùng tiêu đề.
- Mở game sau khi đã mở bot, hoặc tải lại trang: bấm **Tìm lại**.
- Bot không bao giờ lưu "số hiệu cửa sổ" (HWND) như v1.8.0, nên đóng/mở lại Coowon không làm bot click nhầm cửa sổ khác. Nếu cửa sổ đã đóng khi đang chạy, lần chạy dừng với thông báo "Cửa sổ game đã đóng".
- Trong lúc một tính năng hay một chuỗi tác vụ đang chạy, danh sách cửa sổ bị khóa để không đổi cửa sổ giữa chừng.
- **Khung game** cho biết vị trí khung 1000×600 trong cửa sổ:
    - `tin cậy`: hai cách dò khác nhau cho cùng một kết quả, bot được phép click.
    - `chưa chắc chắn` hoặc `không thấy`: bot không click. Thường do game đang tải, bị thu nhỏ xuống thanh tác vụ, hoặc trang đang ở mức thu phóng quá lạ. Đưa game về màn hình chính rồi bấm **Kiểm tra** lại.
- Cửa sổ game **không được thu nhỏ (minimize)** và **nên để lộ ít nhất một phần**: Windows không vẽ cửa sổ đã thu nhỏ, và đã có lần ảnh chụp đứng yên khi cửa sổ bị che kín. Khi ảnh chụp đứng yên mà cửa sổ bị che kín, Nhật ký ghi "cửa sổ game bị che, hãy để lộ một phần cửa sổ".

## 4. Chạy một tính năng

1. Mở thẻ **Tính năng**, chọn tính năng ở danh sách bên trái.
2. Điền thông số ở biểu mẫu bên phải. Rê chuột lên tên thông số để xem giải thích. Giá trị sai bị báo đỏ ngay, bot không chạy với giá trị sai như v1.8.0.
3. Bấm **Bắt đầu**, ở **bất kỳ màn hình nào** của game: bot tự đi tới màn hình bắt đầu (ghi ở dòng xám *Màn hình bắt đầu* dưới tên tính năng) và tự đóng bảng đang che đường.
4. **Tạm dừng** để dừng tạm thời, bấm lại để chạy tiếp. **Dừng** (hoặc `Ctrl+Alt+F10`) để kết thúc.
5. Khi xong, dòng tóm tắt hiện ở Nhật ký và thanh trạng thái, kèm đường dẫn thư mục nhật ký của lần chạy. Lần chạy lỗi luôn có ảnh chụp màn hình cuối cùng trong thư mục đó.

Thông số của mỗi tính năng được lưu tự động và dùng lại ở lần sau (và ở Chuỗi tác vụ). Mọi tính năng có **Thời gian chạy tối đa (phút)**: 0 = không giới hạn; hết giờ thì bot dừng và tóm tắt việc đã làm.

**Bot tự lo những việc chung**, ở mọi tính năng:

- **Đóng popup vô hại** khi chúng hiện lên (phần thưởng "Chúc mừng ngài được", thông báo góc phải, hộp cốt truyện "bấm để tiếp tục").
- **Thu gọn các bảng che game** trên bản đồ (bảng nhiệm vụ, "Sắp mở", chat), nếu bật trong thẻ Cài đặt.
- **Trả lời câu hỏi tốn Vàng** "Bỏ qua thời gian chờ…" theo cài đặt **Dùng vàng bỏ qua thời gian chờ** (thẻ Cài đặt).
- **Gỡ rối** khi một thao tác không có kết quả: đóng bảng trên cùng bằng ✕ → ESC → đi lại tới màn hình bắt đầu → dừng và chụp ảnh. Mỗi lần gỡ rối có một dòng "Gỡ rối (…)" trong Nhật ký.
- **Game đứng hình**: màn hình không đổi chút nào trong 30 giây dù bot đã click → bot dừng với lời nhắn "game không phản hồi … Hãy tải lại trình duyệt (F5)".

| Tính năng | Màn hình bắt đầu (bot tự tới) | Thông số chính |
| --- | --- | --- |
| Nâng cấp công trình | Thành chính (`city`), Hải cảng khi chọn | Khu vực, cấp tối đa, số lần bấm Tăng cấp mỗi lần mở |
| Quay Tinh Tú | Bảng Tinh Tú (`chest.wheel`) | Số rương cần mở, số rương mua trước, cỡ đợt mở nhanh |
| Nâng cấp khoa kỹ | Bảng Phủ Sách Lược, thẻ Khoa kỹ (`research`) | Số cấp mỗi ô, các ô cần nâng |
| Tháp Thí Luyện | Bảng Tháp Thí Luyện (`tower.dialog`) | Chế độ (Vượt tháp / Tự vượt tháp), tầng, số lần quét |
| Clear Phó Bản | Bản đồ Phó Bản (`field`) | Sang phó bản kế tiếp, số trận, số trận thua liên tiếp |
| Võ Khôi Tháp | Bản đồ thế giới (`world_map`) | Boss, số lượt, khi bạc vượt 2 tỷ, mở rương khi hết Quân lệnh |
| AutoClick | Bất kỳ | Danh sách tọa độ, kiểu click, thời gian giữa 2 click, số vòng |

### 4.1 Nâng cấp công trình

Bot đọc nhãn "lvN" trên từng công trình của bản đồ và nâng công trình thấp nhất trước, đến cấp Chúa công (số trong vòng tròn dưới ảnh đại diện) hoặc **Cấp tối đa** bạn đặt.

1. Trên bản đồ: thu gọn danh sách thông báo bên trái và hàng biểu tượng trên cùng (để không che nhãn; hàng biểu tượng được mở lại khi xong), đọc cấp mọi công trình. Một cấp chỉ được tin khi hai lần đọc liên tiếp giống nhau.
2. Mở công trình thấp nhất, bấm **Tăng cấp** tối đa **Số lần bấm Tăng cấp mỗi lần mở**, đóng bảng, đọc lại bản đồ. Mỗi lần "Tốn bạc" đổi là một cấp.
3. Bấm hai lần liền mà không có gì đổi (công trình ở cấp cao nhất của nó, ví dụ lv110) thì bỏ công trình đó đến hết lần chạy.
4. Hết thợ (game hỏi "Bỏ qua thời gian chờ tốn N Vàng"): **Dùng vàng bỏ qua thời gian chờ** bật thì bấm 确定 và chạy tiếp; tắt thì bấm 取消 và dừng, ghi lý do.
5. Xong khi không còn công trình nào thấp hơn cấp đích.

- **Khu vực**: Thành chính, Hải cảng, hoặc Cả hai (Thành chính trước, rồi bot tự vào Hải cảng bằng "Vào Hải Cảng").
- **Cấp tối đa**: 0 = cấp Chúa công.

### 4.2 Quay Tinh Tú

Bot tự mở bảng **Tinh Tú** (biểu tượng trên cùng; mở hàng biểu tượng nếu đang ẩn), mua rương nếu được yêu cầu, rồi mở rương theo **đợt**: mỗi đợt bấm liền Tự mở → 确定 → Ngưng mở nhiều lần (mỗi lần mở 1 rương), rồi đọc lại số rương. Khoảng 20–25 rương mỗi giây; 200 rương trong khoảng 9 giây.

- **Số rương cần mở**: 0 = mở hết.
- **Mua thêm rương trước khi mở**: 240 Vàng mỗi rương; 0 = không mua. Bot gõ số lượng vào ô, **đọc lại ô** và chỉ bấm 确定 khi ô đúng số; sai thì gõ lại. Hộp "Dùng 240 vàng…" được bấm 确定 (ô "Lần sau không nhắc nhở" không bao giờ được tích).
- **Số rương mỗi đợt mở nhanh** (mặc định 50), **Khoảng cách giữa các click khi mở nhanh (ms)** (mặc định 5).
- Mỗi đợt ghi một dòng như `Mở nhanh 50 lượt: rương 200 → 150 (mở 50, 2.2s)`.

### 4.3 Nâng cấp khoa kỹ

Bot mở **Phủ Sách Lược** (nút "Khoa kỹ" ở thanh dưới Thành chính), chọn thẻ **Khoa kỹ**, rồi đi lần lượt các ô: chọn ô, bấm **Tăng cấp** khi cấp còn tăng, tối đa **Số cấp nâng tối đa mỗi ô khoa kỹ**. Ô hiện dòng đỏ "Cấp Phủ sách lược không đủ…" (hoặc bấm mà cấp không đổi) là đã tối đa và được bỏ qua.

- **Các ô khoa kỹ**: để trống = cả 18 ô, đánh số theo hàng từ trái sang (ví dụ `1-8`).
- Mọi ô đều tối đa thì dòng tóm tắt nhắc bạn nâng công trình Phủ Sách Lược.

### 4.4 Tháp Thí Luyện

Bot tự mở bảng Tháp Thí Luyện (Thành chính → công trình Tháp → nút Tháp Thí Luyện), chọn thẻ **Tháp**, gõ số trang vào ô trang (đọc lại để chắc chắn) và chọn tầng.

- **Chế độ Vượt tháp**: đánh từ **Tầng bắt đầu** đến **Tầng kết thúc**. Mỗi tầng: Vượt Tháp → 确定 (3 Thí luyện lệnh) → tích ô "Tự Vượt Tháp" nếu chưa tích → bấm **một lần** vào lính đứng dưới viên pha lê xanh; quân mình tự đánh hết lính. Thắng thì lên tầng sau; thua thì Thoát → 确定 và dừng.
- **Chế độ Tự vượt tháp**: quét **Số lần tự vượt tháp** lần tầng **Tầng bắt đầu** (phải là tầng đã thắng; chưa thắng thì bot dừng và báo "chưa thắng"). Quét xong ngay, không có trận.
- **Thời gian tối đa mỗi tầng (giây)**: không thấy Thắng/Bại sau thời gian này thì bot gỡ rối rồi dừng.

### 4.5 Clear Phó Bản

Bot mở **Phó Bản** (nút ở góc dưới bên phải), rồi đánh đạo quân có **tia sáng xanh** (đạo quân kế tiếp của phó bản):

1. Thu gọn danh sách thông báo và hàng biểu tượng trên cùng (không che đạo quân; mở lại khi xong).
2. Tia sáng bị giao diện che (đạo quân ngay dưới bảng nhân vật): bot nhận ra đạo quân nhờ vòng sáng xanh nhấp nháy quanh chân đạo quân (so sánh khung hình trong ~1-2 giây).
3. Click đạo quân dưới tia sáng, bỏ tích "Phát chiến đấu" (đánh nhanh, không xem trận), bấm **Tấn công**. Dòng xanh "Thắng Trận" = thắng; tia sáng chuyển sang đạo quân khác cũng là thắng.
4. Đoàn chiến ("(Đoàn-Tinh)"): Tấn công → Lập đội → Khai chiến → vào trận → Thoát. Đạo quân boss: Tấn công, vào trận, Thoát. Trong màn trận, nút **Kết quả** (góc dưới bên phải) hiện ra thì bot bấm ngay để bỏ qua trận.
5. Không còn tia sáng (và vòng sáng) 4 giây: phó bản đã xong → sang phó bản kế tiếp (nút »). Phó bản mới không có tia sáng thì dừng (chưa mở khoá hoặc đã xong), như v1.8.0.

- **Hết đạo quân thì sang phó bản kế tiếp**, **Số phó bản được chuyển sang tối đa**, **Số trận tối đa** (0 = không giới hạn).
- **Dừng khi thua liên tiếp bấy nhiêu trận** (mặc định 3), **Thời gian tối đa mỗi trận (giây)**. Hết Quân lệnh cũng dừng.

### 4.6 Võ Khôi Tháp

Vòng lặp dài: đánh boss Võ Khôi Tháp liên tục, tự xử lý bạc đầy và hết Quân lệnh.

1. Từ Bản đồ thế giới, bot bấm vào tháp Võ Khôi → sảnh (đọc **Quân lệnh cần cho lượt này**: lượt 1 tốn 1, lượt 2 tốn 2…, về 1 mỗi ngày mới) → Tấn công → Lập đội → Khai chiến.
2. Trong đấu trường: bấm **một** vòng phép phát sáng; thấy dòng "Ngài gia nhập thành công" là đã vào trận (nhân vật tự đi tới boss, 5–15 giây). Không vào được thì thử vòng khác. Hết giờ đếm ngược mà boss còn đầy máu thì Thoát.
3. Kết quả 胜利 / 失败 → 确定 → lượt sau. Thua thì đánh lại.

- **Boss**: 0 = boss đang hiện trong sảnh; N = boss thứ N.
- **Số lượt đánh**: 0 = đến khi bấm Dừng.
- **Khi bạc vượt 2 tỷ**: bạc tối đa là 2.000.000.000, phần vượt bị mất. *Mua rương 1 tỷ bạc rồi chạy tiếp* (mặc định): trước khi tràn (hoặc khi game báo "Bạc đã đạt tối đa", bot bấm **Hủy bỏ**, không bao giờ bấm Cường hóa), bot mở Trang bị → Cửa hiệu, cuộn xuống hàng rương, kiểm tra "Bạc cần" = 1.000.000.000 rồi bấm Mua **Số rương 1 tỷ bạc mua mỗi lần** lần (mặc định 2), kiểm tra bạc giảm đúng sau mỗi lần. Hai lựa chọn khác: *Bỏ qua, chạy tiếp* và *Dừng*.
- **Hết Quân lệnh thì mở rương Tinh Tú**: mở hết rương Tinh Tú (rương có thể cho Quân lệnh), rồi đánh tiếp. **Hết rương Tinh Tú thì mua thêm (tốn Vàng)** với **Số rương Tinh Tú mua mỗi lần** (240 Vàng mỗi rương). Không mua được hoặc mở rương mà không thêm Quân lệnh thì dừng. **Số lần mở rương tối đa**: 0 = không giới hạn.
- **Số rương Tinh Tú mỗi đợt mở nhanh**, **Khoảng cách giữa các click khi mở rương (ms)**: như Quay Tinh Tú.
- Mỗi hộp lạ hiện ra lần đầu được lưu thành ảnh `event-NN-….png` trong thư mục nhật ký của lần chạy.

### 4.7 AutoClick

Click lần lượt các điểm bạn ghi, ở chế độ nền. AutoClick không nhận diện màn hình: nó chỉ tìm khung game để đổi tọa độ.

- **Danh sách tọa độ**: điểm trên khung game 1000×600 (không phải tọa độ màn hình), nên vẫn đúng khi bạn kéo cửa sổ. Thêm điểm bằng nút **Thêm** (nhập tay) hoặc **Ghi từ con trỏ (3 giây)**: bấm nút, trong 3 giây đưa chuột tới chỗ cần click trên game và giữ yên.
- **Kiểu click**: chuột trái, giữa hoặc phải, click đơn hoặc click đúp (6 kiểu như bản cũ; bản cũ bỏ qua lựa chọn này, bản mới làm đúng).
- **Thời gian giữa 2 lần click (ms)**, **Số vòng** (0 = đến khi bấm Dừng).
- Sau mỗi click, con trỏ của game được đưa về mép trên khung game (600, 2), để nút vừa bấm không còn sáng.

## 5. Chế độ gỡ lỗi

Chế độ gỡ lỗi cho bạn thấy bot đang "nhìn" gì. Mặc định tắt. Bật bằng một trong ba cách:

- ô **Bật chế độ gỡ lỗi** ở thẻ **Gỡ lỗi**;
- phím tắt `Ctrl+Alt+D` (đổi được ở thẻ Cài đặt);
- `debug = true` trong `settings.toml`, hoặc chạy `AutoNgoaLong.exe --debug`.

Khi bật:

- **Lớp phủ trực tiếp** vẽ ngay trên cửa sổ game (không chặn chuột, chỉ hiện khi game đang hiện trên màn hình), cả khi không có tính năng nào chạy:
    - khung **xanh lơ** = khung game bot tìm được (đỏ nếu chưa chắc chắn), kèm kích thước, gốc tọa độ và tỉ lệ;
    - khung **xanh lá** = phần giao diện bot nhận ra (nút, tiêu đề bảng, ✕...), kèm tên và điểm khớp;
    - khung **đỏ** = nút ✕ của một bảng bot không biết;
    - **dấu cộng vàng** = chỗ bot sắp click;
    - dòng trạng thái: các màn hình bot thấy (như dòng *Màn hình*), cửa sổ, tính năng, luật vừa chọn và thao tác.
- **Thẻ Gỡ lỗi** hiện ảnh chụp cuối cùng với cùng các khung đó, dùng được cả khi game bị che.
- **Ảnh PNG từng nhịp** được lưu vào `logs\run-<mã>\NNN-<bước>.png`.

Khi tắt, bot vẫn lưu ảnh màn hình cuối cùng của mọi lần chạy bị lỗi. `run.log` trong thư mục đó có một dòng cho mỗi quyết định (màn hình bot thấy | luật → thao tác, kết quả mong đợi). Gửi kèm thư mục `logs\run-<mã>\` khi báo lỗi.

## 6. Chuỗi tác vụ và lịch chạy

Thẻ **Chuỗi tác vụ** cho phép chạy nhiều tính năng nối tiếp nhau, bằng tay hoặc theo giờ (ví dụ 08:00 mỗi ngày: Nâng cấp khoa kỹ → Nâng cấp công trình → Tháp Thí Luyện → Võ Khôi Tháp).

1. Bấm **Thêm chuỗi**, đặt **Tên**.
2. **Các bước**: bấm **Thêm bước**, chọn tính năng. Mỗi bước có:
    - **Thử lại**: số lần chạy lại khi bước lỗi; **Chờ thử lại (giây)**: thời gian chờ trước khi chạy lại;
    - **Bật**: bỏ dấu để tạm bỏ qua bước;
    - **Tối đa (phút)**: hết thời gian thì dừng bước và chuyển sang bước sau (0 = không giới hạn). **Bắt buộc đặt** cho tính năng chạy đến khi bấm Dừng (Võ Khôi Tháp, AutoClick có số lượt/số vòng = 0), nếu không chuỗi sẽ không bao giờ sang bước sau.
    - Thông số của mỗi bước lấy từ thẻ **Tính năng** (giá trị đã lưu lúc bước bắt đầu).
    - Dùng **Lên**/**Xuống** để đổi thứ tự.
3. **Dừng cả chuỗi khi một bước vẫn lỗi sau khi thử lại**: bỏ dấu để chuỗi bỏ qua bước lỗi và chạy tiếp.
4. **Chạy theo lịch**: nhập **Giờ chạy** (ví dụ `08:00, 20:30`) và chọn **Ngày** (bỏ trống = mỗi ngày). Ô **Bật lịch tự động** ở trên cùng bật/tắt lịch cho mọi chuỗi; dòng bên cạnh cho biết lần chạy kế tiếp.
5. Bấm **Lưu**. **Chạy ngay** để chạy thử; **Dừng chuỗi** (hoặc phím Dừng) để dừng.

Lưu ý:

- Lịch chỉ chạy khi AutoNgoaLong đang mở. Nếu bot vừa mở sau giờ hẹn vài phút, chuỗi vẫn được chạy; trễ quá lâu thì bỏ qua lần đó.
- Nếu một tính năng khác đang chạy tới giờ hẹn, chuỗi xếp hàng chờ; chờ quá lâu thì bị hủy và ghi vào Nhật ký.
- Vì mỗi tính năng tự đi tới màn hình của nó, các bước nối tiếp nhau không cần đưa game về màn hình nào trước.
- Chuỗi được lưu trong `data\routines.json`.

## 7. Nhiều cửa sổ (nhiều tài khoản)

Thẻ **Nhiều cửa sổ** chạy song song một tính năng trên mỗi cửa sổ game (máy chủ này cho phép dùng bot).

1. Mở mỗi tài khoản trong một cửa sổ Coowon riêng, rồi bấm **Tìm cửa sổ**.
2. Với mỗi cửa sổ, chọn tính năng muốn chạy. Thông số lấy từ thẻ **Tính năng**.
3. Bấm **Bắt đầu** / **Tạm dừng** / **Dừng** ở từng dòng, hoặc **Bắt đầu tất cả** / **Dừng tất cả**. Phím Dừng dừng mọi cửa sổ.

Một cửa sổ đang chạy ở thẻ Tính năng (hoặc trong một chuỗi tác vụ) thì không chạy được ở thẻ Nhiều cửa sổ, và ngược lại. Lớp phủ gỡ lỗi chỉ đi theo cửa sổ chọn ở trên cùng; các cửa sổ khác xem ở thẻ Gỡ lỗi.

## 8. Các thẻ khác

- **Lịch sử**: mọi lần chạy (tính năng, cửa sổ, giờ, kết quả, tóm tắt, bộ đếm như số rương, số tầng, số lượt). **Mở ảnh lỗi** mở ảnh chụp lúc lỗi; **Mở thư mục nhật ký** mở thư mục của lần chạy; **Xóa lịch sử** xóa danh sách. Lưu trong `data\history.sqlite3`.
- **Thông báo**: chọn gửi khi **Hoàn thành**, **Bị dừng**, **Gặp lỗi**; thông báo ở khay hệ thống; Telegram (mã bot + chat id) và Discord (webhook `https://discord.com/api/webhooks/...`), có thể đính kèm ảnh chụp game. Bấm **Lưu**, rồi **Gửi thử**. Mã bot và webhook nằm trong `settings.toml`: đừng chia sẻ tệp đó.
- **Cài đặt**: đổi phím tắt **Dừng**, **Bật/tắt gỡ lỗi**, **Tạm dừng / tiếp tục** (phải có Ctrl, Alt hoặc Win; để trống ô Tạm dừng = không dùng), rồi bấm **Áp dụng**. Thẻ cũng cho biết đường dẫn tệp cài đặt và tệp INI cũ đã nhập.
    - **Trong game → Dùng vàng bỏ qua thời gian chờ** (mặc định tắt). Khi game hỏi *"Bỏ qua thời gian chờ tốn … Vàng. Xác nhận?"* (ví dụ sau khi bấm Tăng cấp lúc hết thợ): **bật** thì bot bấm **确定** (tốn Vàng, không phải chờ) rồi chạy tiếp; **tắt** thì bot bấm **取消** và dừng tính năng đang chạy, ghi lý do vào Nhật ký. Thay đổi có tác dụng ngay, kể cả khi đang chạy. Bot không bao giờ tích ô "Lần sau không nhắc nhở". Lưu ý: lựa chọn "dùng vàng" trong game bị xóa mỗi khi tải lại trình duyệt.
    - **Trong game → Thu gọn các bảng che game khi chạy** (mặc định bật). Trên bản đồ không có bảng nào mở, bot thu gọn bảng nhiệm vụ bên phải, khung "Sắp mở" và khung chat (tối đa 2 lần bấm mỗi bảng mỗi lần chạy), để chúng không che nhà và đạo quân. Hàng biểu tượng trên cùng chỉ được ẩn bởi tính năng cần (Công trình, Clear Phó Bản) và được mở lại khi xong. Áp dụng từ lần chạy sau.

## 9. Sửa dữ liệu nhận diện không cần build lại

Mọi thứ bot dùng để nhận ra game nằm trong `profiles\ngoalongviet\`:

- `parts.yaml`: các phần giao diện (nút, tiêu đề bảng, ✕...), mỗi phần là một ảnh mẫu và vùng tìm kiếm trên khung 1000×600;
- `probes.yaml`: các đầu dò màu (thẻ đang chọn, ô đã tích, tia sáng xanh, dòng chữ đỏ/xanh...);
- `numbers.yaml`: vị trí các con số bot đọc (Bạc, Quân lệnh, số rương, tầng, cấp...);
- `screens\*.yaml`: mỗi màn hình là một quy tắc trên các phần giao diện, kèm các giá trị bot đọc trên màn hình đó;
- `nav.yaml`: đường đi giữa các màn hình (bấm gì để từ màn hình này sang màn hình kia);
- `popups.yaml`, `panels.yaml`: popup tự đóng, bảng tự thu gọn;
- `templates\**\*.png`: ảnh mẫu (`templates\digits\game\` là các chữ số của game);
- `locator.yaml`: các mốc dùng để tìm khung game.

Sửa bằng Notepad (lưu dạng UTF-8), khởi động lại bot. Sao lưu thư mục `profiles` trước khi sửa. Sau khi sửa, chạy `--selftest` để chắc chắn các tệp vẫn đọc được, đủ ảnh mẫu, và 57 ảnh mẫu vẫn được nhận diện đúng.

## 10. Xử lý sự cố

| Hiện tượng | Cách xử lý |
| --- | --- |
| Danh sách cửa sổ ghi "Không thấy cửa sổ Coowon đang mở Ngọa Long" | Mở game trong **Coowon** (không phải Chrome/Edge), chờ tiêu đề tab có "Ngọa Long", rồi bấm **Tìm lại**. |
| Coowon báo "Adobe Flash Player was blocked" | Bấm **Run this time** mỗi lần tải lại trang. |
| `Khung game: … không thấy` hoặc `chưa chắc chắn` | Cửa sổ game bị thu nhỏ, game đang tải, hoặc có hộp thoại che khung. Mở lại cửa sổ, đặt thu phóng 100% (Ctrl+0), bấm **Kiểm tra**. Khi chạy, bot dừng nếu không thấy khung game 20 giây liền. |
| Dòng *Màn hình* có `unknown` | Một bảng bot không biết đang mở. Khi chạy, bot tự đóng nó bằng ✕ (hoặc ESC); không cần làm gì. |
| Nhật ký có nhiều dòng "Gỡ rối (…)" | Một thao tác không ra kết quả mong đợi; bot đã tự đóng bảng / ESC / đi lại. Vài dòng là bình thường (game chậm). Nếu lần chạy dừng, xem ảnh lỗi và dòng "Snapshot lúc dừng" trong `run.log`. |
| "game không phản hồi: màn hình đứng yên …" | Game (Flash) bị treo: hình không đổi dù bot đã click. Tải lại trang game (F5) rồi chạy lại. |
| "cửa sổ game bị che, hãy để lộ một phần cửa sổ" | Ảnh chụp đứng yên trong lúc cửa sổ game bị che kín hoặc thu nhỏ. Kéo cửa sổ game ra để lộ một phần, rồi chạy lại. |
| Bot click lệch | Đặt thu phóng trang về 100%. Bật gỡ lỗi: dấu cộng vàng là chỗ click. |
| Phím tắt không chạy | Một chương trình khác đã giữ tổ hợp đó: Nhật ký ghi "Phím tắt: …". Đổi sang tổ hợp khác ở thẻ Cài đặt. |
| Telegram/Discord không nhận được tin | Kiểm tra mã bot, chat id, webhook; bấm **Gửi thử** và đọc dòng kết quả. |
| `settings.toml` bị hỏng | Bot đổi tên tệp hỏng thành `settings.toml.bak` và chạy với cài đặt mặc định. |
| Bot không mở được, không hiện cửa sổ | Xem `logs\app.log`. Chạy `--selftest` (mục 1) và gửi `selftest-report.txt` khi báo lỗi. |
| `--selftest` báo `FAIL [profile] …` hoặc `FAIL [perception] …` | Có tệp trong `profiles\` bị sửa hỏng, xóa hoặc đổi tên. Lấy lại thư mục `profiles` từ bản tải về (hoặc hoàn tác thay đổi của bạn). |
| Windows Defender chặn hoặc xóa tệp | Tệp exe chưa ký số. Thêm thư mục bot vào danh sách loại trừ nếu bạn tin nguồn tải. |

Khi báo lỗi, gửi: dòng tóm tắt ở Nhật ký, thư mục `logs\run-<mã>\` của lần chạy (có ảnh lỗi và `run.log`), và nếu có thể, một lần chạy lại với chế độ gỡ lỗi bật.
