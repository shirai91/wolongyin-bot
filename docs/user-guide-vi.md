# Hướng dẫn sử dụng AutoNgoaLong v2

AutoNgoaLong v2 là bản viết lại bằng Python của AutoNgoaLong v1.8.0, dùng cho game Ngọa Long (máy chủ ngoalongviet.com) chạy trong trình duyệt **Coowon** trên Windows. Bot click và chụp màn hình **ở chế độ nền**: cửa sổ game có thể bị che bởi cửa sổ khác, bạn vẫn làm việc khác trên máy được.

Điểm khác lớn nhất so với v1.8.0:

- **Không còn bấm F9 và nhập "Tọa độ gốc".** Bot tự tìm cửa sổ Coowon và tự tìm khung game 1000×600 trong cửa sổ trên mỗi lần chụp, kể cả khi bạn kéo cửa sổ hoặc phóng to/thu nhỏ trang.
- **Bot nhìn màn hình trước khi click.** Mỗi tính năng kiểm tra đúng màn hình (ví dụ bảng Tháp Thí Luyện, vòng quay Tinh Tú) rồi mới click. Sai màn hình thì dừng, ghi lý do và chụp ảnh, không click mù.
- **Mọi lần chờ đều có giới hạn thời gian** và nút Dừng có tác dụng ngay (dưới 1 giây).
- **Phím tắt không chiếm phím F trần.** Mặc định Dừng = `Ctrl+Alt+F10`, bật/tắt gỡ lỗi = `Ctrl+Alt+D`.
- **Không còn hộp thoại (MsgBox) khi chạy xong.** Kết quả ghi ở khung Nhật ký, thẻ Lịch sử và (tùy chọn) thông báo khay hệ thống, Telegram, Discord.

## 1. Cài đặt

1. Giải nén thư mục `AutoNgoaLong` vào nơi bạn có quyền ghi, ví dụ `D:\AutoNgoaLong`. Không nên để trong `C:\Program Files`, vì bot ghi cài đặt, nhật ký và lịch sử ngay trong thư mục này.
2. Trong thư mục có:

    | Tệp / thư mục | Dùng để |
    | --- | --- |
    | `AutoNgoaLong.exe` | Chạy bot |
    | `_internal\` | Thư viện của chương trình. Không sửa, không xóa. |
    | `profiles\ngoalongviet\` | Tọa độ click (`coords\*.yaml`), định nghĩa màn hình (`scenes\*.yaml`), ảnh mẫu (`templates\`). Sửa được bằng Notepad, không cần build lại. |
    | `HUONG-DAN.md` | Tài liệu này |
    | `settings.toml` | Cài đặt của bạn (tự tạo khi chạy lần đầu) |
    | `logs\` | Nhật ký và ảnh chụp mỗi lần chạy (tự tạo) |
    | `data\` | Lịch sử chạy và chuỗi tác vụ (tự tạo) |

3. Muốn giữ cài đặt của bản cũ: chép tệp `NgoaLong_data.ini` của v1.8.0 vào cùng thư mục với `AutoNgoaLong.exe` **trước lần chạy đầu tiên**. Bot nhập các giá trị (số lượt quay, tầng tháp, boss, độ trễ...) một lần duy nhất rồi lưu sang `settings.toml`. Các khóa vô dụng như `HWND_GAME`, `TITLE_ACTIVE` bị bỏ qua.
4. Kiểm tra bản cài (không cần mở game): mở PowerShell trong thư mục bot và chạy

    ```powershell
    Start-Process .\AutoNgoaLong.exe -ArgumentList "--selftest" -Wait
    Get-Content .\selftest-report.txt -Encoding UTF8
    ```

    Mỗi dòng `OK` là một phép kiểm tra đạt; dòng cuối cho biết tổng số lỗi. Nếu có dòng `FAIL`, xem mục Xử lý sự cố.

Windows Defender hoặc SmartScreen có thể cảnh báo vì tệp exe chưa được ký số. Chọn **More info → Run anyway** nếu bạn tải bot từ nguồn bạn tin cậy.

## 2. Lần chạy đầu tiên

1. Mở **Coowon**, vào game Ngọa Long trên ngoalongviet.com. Nếu Coowon báo "Adobe Flash Player was blocked", bấm **Run this time**.
2. Để trang game ở mức thu phóng 100% (Ctrl+0). Bot vẫn chạy được ở mức khác, nhưng 100% là mức đã được kiểm tra kỹ nhất.
3. Chạy `AutoNgoaLong.exe`. Cửa sổ chính gồm:
    - Khung **Cửa sổ game (Coowon)**: danh sách cửa sổ game, nút **Tìm lại**, nút **Kiểm tra**, và hai dòng trạng thái *Khung game* và *Màn hình*.
    - Các thẻ: **Tính năng**, **Nhiều cửa sổ**, **Chuỗi tác vụ**, **Gỡ lỗi**, **Mẫu ảnh**, **Lịch sử**, **Thông báo**, **Cài đặt**.
    - Khung **Nhật ký** ở dưới cùng.
    - Thanh trạng thái nhắc phím tắt Dừng và Gỡ lỗi.
4. Bấm **Kiểm tra**. Nếu thấy `Khung game: (…) 1000×600 ×1.00 - tin cậy` và `Màn hình: city (1.00) …` là bot đã nhìn thấy game. Bạn đã sẵn sàng chạy tính năng.

## 3. Nhận cửa sổ game

- Bot tự liệt kê các cửa sổ Coowon có tiêu đề chứa "Ngọa Long" (ví dụ `Sv 266 Ngọa Long Việt Nam - Coowon Browser`). Không cần F9, không cần chọn bằng chuột.
- Có nhiều cửa sổ: chọn đúng cửa sổ trong danh sách. Lần sau bot tự chọn lại cửa sổ có cùng tiêu đề.
- Mở game sau khi đã mở bot, hoặc tải lại trang: bấm **Tìm lại**.
- Bot không bao giờ lưu "số hiệu cửa sổ" (HWND) như v1.8.0, nên đóng/mở lại Coowon không làm bot click nhầm cửa sổ khác. Nếu cửa sổ đã đóng khi đang chạy, lần chạy dừng với thông báo "Cửa sổ game đã đóng".
- Trong lúc một tính năng hay một chuỗi tác vụ đang chạy, danh sách cửa sổ bị khóa để không đổi cửa sổ giữa chừng.
- **Khung game** cho biết vị trí khung 1000×600 trong cửa sổ:
    - `tin cậy`: hai cách dò khác nhau cho cùng một kết quả, bot được phép click.
    - `chưa chắc chắn` hoặc `không thấy`: bot không click. Thường do game đang tải, bị thu nhỏ xuống thanh tác vụ, hoặc trang đang ở mức thu phóng quá lạ. Đưa game về màn hình chính rồi bấm **Kiểm tra** lại.
- Cửa sổ game được phép bị che, nhưng **không được thu nhỏ (minimize)**: Windows không vẽ cửa sổ đã thu nhỏ nên bot không chụp được.

## 4. Chạy một tính năng

1. Mở thẻ **Tính năng**, chọn tính năng ở danh sách bên trái.
2. Điền thông số ở biểu mẫu bên phải. Rê chuột lên tên thông số để xem giải thích. Giá trị sai (ví dụ số lượng để trống hoặc bằng 0) bị báo đỏ ngay, bot không chạy với giá trị sai như v1.8.0.
3. Đưa game tới đúng màn hình bắt đầu (bảng dưới), rồi bấm **Bắt đầu**.
4. **Tạm dừng** để dừng tạm thời, bấm lại để chạy tiếp. **Dừng** (hoặc `Ctrl+Alt+F10`) để kết thúc.
5. Khi xong, dòng tóm tắt hiện ở Nhật ký và thanh trạng thái, kèm đường dẫn thư mục nhật ký của lần chạy. Lần chạy lỗi luôn có ảnh chụp màn hình cuối cùng trong thư mục đó.

Thông số của mỗi tính năng được lưu tự động và dùng lại ở lần sau (và ở Chuỗi tác vụ). Thông số "Thời gian chờ màn hình (giây)" có ở nhiều tính năng: đó là thời gian tối đa bot chờ một màn hình xuất hiện trước khi báo lỗi; máy chậm hoặc mạng chậm thì tăng lên.

| Tính năng | Bắt đầu ở màn hình | Thông số chính |
| --- | --- | --- |
| Nâng cấp công trình | Thành chính (hoặc Hải cảng) | Khu vực (Thành chính tự đọc cấp bằng OCR), số lần bấm Tăng cấp |
| Quay rương | Vòng quay Tinh Tú đang mở | Số lượt quay, thời gian chờ giữa các click |
| Mở thẻ bài | Màn hình rút thẻ bài đang mở | Loại thẻ, số lượng, mua thẻ trước |
| Nâng cấp khoa kỹ | Bảng Khoa kỹ, hoặc Thành chính / bản đồ | Số lần nâng cấp mỗi khoa kỹ |
| Cày cấp chúa công | Thành chính, hoặc bảng Tháp Thí Luyện đang mở | Số Thí luyện lệnh, tầng tháp |
| Tháp Thí Luyện | Thành chính, hoặc bảng Tháp Thí Luyện đang mở | Tầng bắt đầu, tầng kết thúc |
| Clear Phó Bản | Bản đồ Phó Bản (hoặc màn hình bản đồ có nút Phó Bản) | Số trận tối đa, chuyển phó bản kế tiếp |
| Võ Khôi Tháp | Thành chính hoặc Bản đồ thế giới | Boss, tự quay rương khi hết lệnh, tùy chọn khi Full Bạc |
| Thám Bảo | Màn hình Thám Bảo đang mở | Chiến, nhận quà x2, số lượt Càn quét |
| Tri Kỷ | Tửu Quán đang mở | Danh sách tri kỷ tím / cam, số lần tìm |
| AutoClick | Bất kỳ | Danh sách tọa độ, kiểu click, thời gian giữa 2 click, số vòng |

### 4.1 Nâng cấp công trình

**Thành chính**: mở Thành chính rồi bấm Bắt đầu. Bot tự làm hết, không cần danh sách công trình:

1. Thu gọn mọi bảng che thành (bảng nhiệm vụ, Sắp mở, chat, hàng biểu tượng trên cùng, danh sách thông báo bên trái). Hàng biểu tượng được mở lại khi xong.
2. Đọc cấp Thành chính (số vàng trong vòng tròn dưới ảnh đại diện, góc trên bên trái).
3. Đọc chữ "lvX" trên từng công trình.
4. Mở công trình có cấp thấp nhất (thấp hơn cấp Thành chính), bấm "Tăng cấp", đóng bảng, đọc lại thành; lặp lại.
5. Công trình không lên cấp sau khi bấm (đang chờ thời gian, thiếu Bạc, hết Kiến Trúc Nhân) hoặc không mở được bảng thì bỏ qua đến hết lần chạy.
6. Tự dừng khi không còn công trình nào thấp hơn cấp Thành chính.

- **Số lần bấm Tăng cấp mỗi công trình**: chỉ dùng khi bật "Dùng vàng bỏ qua thời gian chờ" (thẻ Cài đặt). Khi tắt, bot chỉ bấm 1 lần mỗi lượt, vì lần bấm thứ hai chỉ hỏi dùng Vàng.

**Hải cảng** (6 công trình, theo tọa độ của v1.8.0): click từng công trình, bấm "Tăng cấp" đủ số lần, rồi đóng bảng.

- **Công trình**: để trống = tất cả; hoặc viết danh sách như `1-3,5`.
- **Kiểm tra màn hình trước khi click** (mặc định bật): chỉ bấm "Tăng cấp" khi bảng công trình thật sự mở. Tắt = click mù như v1.8.0.

### 4.2 Quay rương

Mở vòng quay **Tinh Tú** trong game rồi bấm Bắt đầu. Bot bấm "Tự mở", một ô, "Tự mở" cho mỗi lượt, đúng như v1.8.0.

- **Số lượt quay**, **Thời gian chờ giữa các click (ms)**.
- **Kiểm tra màn hình Tinh Tú trước mỗi lượt** (mặc định bật): vòng quay bị đóng hoặc có bảng lạ hiện lên thì bot dừng, không click tiếp.

### 4.3 Mở thẻ bài

Mở màn hình rút thẻ bài trong game rồi bấm Bắt đầu.

- **Loại thẻ bài**: Hắc Diệu Thạch, Hỏa Diễm Sơn, Bồng Lai Tiên Sơn.
- **Số lượng**: số lần rút (và số thẻ mua nếu chọn "Mua thẻ trước khi mở"). Phải ≥ 1.
- **Không chạy khi đang mở bảng khác**: nếu bot nhận ra một bảng khác (Khoa kỹ, Tướng, Trang bị...) đang mở, nó từ chối chạy.
- Lưu ý: màn hình rút thẻ chưa có ảnh mẫu, nên bot click theo tọa độ của v1.8.0 sau khi kiểm tra không có bảng lạ.

### 4.4 Nâng cấp khoa kỹ

Bot mở bảng Khoa kỹ (nút "Khoa kỹ" ở thanh dưới) nếu chưa mở, rồi đi lần lượt 3 hàng × 6 ô khoa kỹ, mỗi ô bấm "Tăng cấp" đủ số lần.

- **Số lần nâng cấp mỗi khoa kỹ**, **Thời gian chờ quanh mỗi ô**, **Thời gian chờ giữa 2 lần 'Tăng cấp'**.
- **Kiểm tra bảng Khoa kỹ trước mỗi ô** (mặc định bật).

### 4.5 Cày cấp chúa công

Vào Tháp Thí Luyện ở một tầng cố định nhiều lần để lấy kinh nghiệm chúa công. Bot tự mở bảng Tháp từ Thành chính (qua bảng công trình Tháp), hoặc dùng bảng đang mở.

- **Số Thí luyện lệnh sử dụng**: mỗi lần vào tháp mất 3 lệnh, nên số lần vào = số lệnh / 3.
- **Tầng tháp**: mỗi trang có 10 tầng; danh sách chỉ hiện các tầng đã mở khoá. Bot tự lật trang và dừng nếu tầng chưa mở khoá.
- Hết lệnh (không thấy hộp xác nhận) thì bot dừng, báo rõ lý do và chụp ảnh.

### 4.6 Tháp Thí Luyện

Leo tháp từ **Tầng bắt đầu** đến **Tầng kết thúc**.

- **Bấm vào quân mình khi vào tháp** (mặc định bật, như bản cũ).
- **Cách tới trang của tầng bắt đầu**:
    - *Về trang 1 rồi bấm sang trang* (mặc định): chỉ bấm chuột ◀ cho tới khi ô trang hiện 1, rồi bấm ▶ tới trang cần. Mỗi lần bấm, bot kiểm tra ô trang có đổi số. An toàn nhất, nhưng chậm hơn khi đang ở trang xa (ví dụ trang 11).
    - *Gõ số trang vào ô trang rồi Enter*: nhấp đúp ô trang, xoá, gõ số trang, Enter. Bot chỉ gõ phím khi thấy ô trang thay đổi theo (xoá thì trống, gõ thì hiện số); nếu không, bot dừng **mà không bấm Enter**. Sau Enter, nếu ô trang tự quay về số khác (trang đó chưa mở khoá) thì bot dừng và báo lỗi.
    - Cả hai cách: bot chỉ bấm khi thấy danh sách tầng của thẻ **Tháp**. Nếu đang mở thẻ Luyện hoặc Điểm tướng, bot bấm thẻ Tháp trước; không mở được thì dừng.
- **Thời gian tối đa mỗi trận (giây)**: không thấy Thắng/Bại sau thời gian này thì dừng và chụp ảnh (v1.8.0 chờ mãi mãi).
- Thua một trận thì dừng với dòng tóm tắt, không hiện hộp thoại.

### 4.7 Clear Phó Bản

Mở bản đồ Phó Bản. Bot tìm cờ đỏ của các đạo quân, click đạo quân, bấm "Tấn công", tự chiêu binh khi thiếu quân, chờ kết quả trận, rồi đánh đạo quân kế tiếp. Hết đạo quân thì sang phó bản kế tiếp (nút »).

- **Thu gọn biểu tượng và bảng nhiệm vụ trước khi đánh** (mặc định bật) để không che đạo quân.
- **Hết đạo quân thì sang phó bản kế tiếp**, **Số phó bản được chuyển sang tối đa**, **Số trận tối đa** (0 = không giới hạn).
- **Số lần không thấy đạo quân liên tiếp**, **Số lần chiêu binh tối đa mỗi trận**, **Dừng khi thua liên tiếp bấy nhiêu trận**, **Thời gian tối đa mỗi trận**.
- Bot cũng dừng nếu đánh cùng một đạo quân nhiều lần mà nó vẫn còn.

### 4.8 Võ Khôi Tháp

Bắt đầu ở Thành chính hoặc Bản đồ thế giới. Bot mở Võ Khôi Tháp, chọn boss, rồi đánh lặp lại.

- **Boss**: Thiên Lang (lv 100) … Đào Ngột (lv 175).
- **Auto quay rương khi hết lệnh**: hết lệnh thì mở Tinh Tú, (tùy chọn) **mua rương** rồi quay **Số rương mua / quay** lượt, sau đó đánh tiếp. Không bật thì hết lệnh là dừng.
- **Tùy chọn khi Full Bạc**: 1. bỏ qua và chạy tiếp; 2. mua rương "1 tỷ bạc" trong cửa hàng rồi chạy tiếp; 3. dừng và thông báo.
- **Số lượt đánh** (0 = đến khi bấm Dừng), **Số lần mở rương tối đa** (0 = không giới hạn). Vừa quay rương xong mà lại hết lệnh ngay thì bot dừng báo lỗi, không mua rương mãi.
- Lưu ý: các màn hình đánh boss sau nút "Tấn công" vẫn dùng ảnh mẫu của v1.8.0 và chưa được kiểm tra đầy đủ trên máy chủ này. Nên bật chế độ gỡ lỗi trong vài lần chạy đầu.

### 4.9 Thám Bảo

Mở màn hình Thám Bảo. Bot bấm "Càn quét" và đi hết từng lượt.

- **Chiến**: gặp đối thủ thì đánh (như ô "Chiến" của bản cũ).
- **Nhận quà x2**: thấy túi quà x2 thì bấm nhận thưởng gấp đôi, tối đa 1 lần mỗi lượt (bản cũ có ô này nhưng không làm gì).
- **Số lượt Càn quét** (0 = chạy đến khi bấm Dừng), **Thời gian tối đa mỗi lượt**.
- Lưu ý: màn hình Thám Bảo chưa được chụp lại trên ngoalongviet.com; bot dùng ảnh mẫu của v1.8.0 và chỉ click khi thấy dấu hiệu của màn hình Thám Bảo.

### 4.10 Tri Kỷ

Mở **Tửu Quán**. Đánh dấu các tri kỷ muốn tìm trong hai danh sách (tím, cam). Mỗi lần tìm, bot dò các khách đang ngồi; gặp tri kỷ đã chọn thì click để trò chuyện, bấm 确定 ở bảng kết quả; sau đó đổi khách và tìm lại.

- **Số lần tìm**: số lần dò (bản cũ đếm theo số lần trò chuyện nên có thể chạy mãi khi không gặp ai).
- **Nút đổi khách**: Tự động (có tri kỷ cam thì Mời nhanh, không thì Mời rượu), Thành khác, Mời rượu, Mời nhanh.
- **Dừng sau số lần trò chuyện** (0 = không giới hạn), **Độ khớp ảnh chân dung** (giảm nếu bot bỏ sót, tăng nếu bot click nhầm).
- Lưu ý: ảnh chân dung là của v1.8.0, chưa được kiểm tra trên máy chủ này.

### 4.11 AutoClick

Click lần lượt các điểm bạn ghi, ở chế độ nền.

- **Danh sách tọa độ**: điểm trên khung game 1000×600 (không phải tọa độ màn hình), nên vẫn đúng khi bạn kéo cửa sổ. Thêm điểm bằng nút **Thêm** (nhập tay) hoặc **Ghi từ con trỏ (3 giây)**: bấm nút, trong 3 giây đưa chuột tới chỗ cần click trên game và giữ yên.
- **Kiểu click**: chuột trái, giữa hoặc phải, click đơn hoặc click đúp (6 kiểu như bản cũ; bản cũ bỏ qua lựa chọn này, bản mới làm đúng).
- **Thời gian giữa 2 lần click (ms)**, **Số vòng** (0 = đến khi bấm Dừng).
- Ghi điểm không đổi cửa sổ game đang chọn (lỗi #4 của bản cũ).

### 4.12 Chạy Thành Tựu

Hoàn thành thành tựu (Hoạt Động > T.Tựu) của một phó bản: đánh các đạo quân "Đánh giá" đạt hạng S, và các thẻ "Khiêu chiến" đủ số lần.

1. Mở game ở Thành chính hoặc bản đồ Phó Bản, chọn tính năng, bấm **Đồng bộ** (bên phải ô **Phó bản**). Trước khi đồng bộ, chỉ nút này bấm được. Bot mở T.Tựu, lướt hết danh sách phó bản (nút ▲ ▼) và đọc thành tựu của từng phó bản.
2. Ô **Phó bản** liệt kê mọi phó bản: ✅ đã xong, ❌ chưa xong (kèm số nhiệm vụ đã xong). Chọn phó bản đã xong thì nút **Bắt đầu** tắt và có dòng cảnh báo đỏ.
3. Chọn một phó bản ❌, bấm **Bắt đầu**. Bot vào bản đồ Phó Bản, bấm « » tới đúng phó bản, rồi lặp: mở T.Tựu, đọc nhiệm vụ chưa xong, đóng bảng, mở từng đạo quân trên bản đồ để đọc tên (nhớ lại trong lần chạy), rồi đánh:
   - **Đánh giá (hạng S)**: đánh 1 trận. Lần đọc sau mà nhiệm vụ chưa có ✓ (không đạt S) thì bot **dừng và báo**.
   - **Khiêu chiến**: đánh tới khi đủ số lần (ví dụ 30/30). Số lần không tăng thì bot dừng và báo.
4. Xong hết thì bot bấm **Nhận thưởng** (tắt được) và dừng.

- **Số lượng** của đạo quân (ví dụ 48/50) giảm 1 sau mỗi trận. Khi về 0 chỉ còn nút **Tiến công**, tốn Vàng: bot chỉ dùng khi bật **Cho phép 'Tiến công' (tốn Vàng)**, không thì dừng và báo.
- Bot đọc chữ bằng OCR (RapidOCR, chạy trên mọi hệ điều hành, không cần cài thêm). Tên đọc ra có thể thiếu dấu ("Đng Trác"), bot so tên gần đúng; thẻ Khiêu chiến còn được so bằng ảnh chân dung.
- Dữ liệu đồng bộ lưu ở `data/thanh_tuu.json`, dùng chung cho mọi cửa sổ game: chạy nhiều tài khoản thì đồng bộ lại khi đổi tài khoản.
- Lưu ý: vị trí các ô trong bảng T.Tựu đo từ ảnh chụp, chưa chạy thử trên game. Nên bật chế độ gỡ lỗi (mỗi dòng chữ đọc được có khung xanh) trong vài lần chạy đầu. Sửa vị trí trong `profiles/ngoalongviet/coords/thanh_tuu.yaml`.

## 5. Chế độ gỡ lỗi

Chế độ gỡ lỗi cho bạn thấy bot đang "nhìn" gì. Mặc định tắt. Bật bằng một trong ba cách:

- ô **Bật chế độ gỡ lỗi** ở thẻ **Gỡ lỗi**;
- phím tắt `Ctrl+Alt+D` (đổi được ở thẻ Cài đặt);
- `debug = true` trong `settings.toml`, hoặc chạy `AutoNgoaLong.exe --debug`.

Khi bật:

- **Lớp phủ trực tiếp** vẽ ngay trên cửa sổ game (không chặn chuột, chỉ hiện khi game đang hiện trên màn hình):
    - khung **xanh lơ** = khung game bot tìm được (đỏ nếu chưa chắc chắn), kèm kích thước, gốc tọa độ và tỉ lệ;
    - khung **xanh lá** = vật bot tìm thấy, kèm tên và điểm khớp;
    - khung **đỏ** = vật bot tìm mà không thấy (vùng tìm kiếm, điểm cao nhất < ngưỡng);
    - **dấu cộng vàng** = chỗ bot sắp click;
    - dòng trạng thái dưới khung: màn hình hiện tại, cửa sổ, tính năng, bước, click kế tiếp.
- **Thẻ Gỡ lỗi** hiện ảnh chụp cuối cùng với cùng các khung đó, dùng được cả khi game bị che.
- **Ảnh PNG từng bước** được lưu vào `logs\run-<mã>\NNN-<bước>.png`.

Khi tắt, bot vẫn lưu ảnh màn hình cuối cùng của mọi lần chạy bị lỗi. Gửi kèm thư mục `logs\run-<mã>\` khi báo lỗi.

## 6. Chuỗi tác vụ và lịch chạy

Thẻ **Chuỗi tác vụ** cho phép chạy nhiều tính năng nối tiếp nhau, bằng tay hoặc theo giờ (ví dụ 08:00 mỗi ngày: Thám Bảo → Cày cấp chúa công → Tháp Thí Luyện).

1. Bấm **Thêm chuỗi**, đặt **Tên**.
2. **Các bước**: bấm **Thêm bước**, chọn tính năng. Mỗi bước có:
    - **Thử lại**: số lần chạy lại khi bước lỗi; **Chờ thử lại (giây)**: thời gian chờ trước khi chạy lại;
    - **Bật**: bỏ dấu để tạm bỏ qua bước;
    - **Tối đa (phút)**: hết thời gian thì dừng bước và chuyển sang bước sau (0 = không giới hạn). **Bắt buộc đặt** cho tính năng chạy đến khi bấm Dừng (Thám Bảo, Võ Khôi Tháp, AutoClick có số lượt/số vòng = 0), nếu không chuỗi sẽ không bao giờ sang bước sau.
    - Thông số của mỗi bước lấy từ thẻ **Tính năng** (giá trị đã lưu lúc bước bắt đầu).
    - Dùng **Lên**/**Xuống** để đổi thứ tự.
3. **Dừng cả chuỗi khi một bước vẫn lỗi sau khi thử lại**: bỏ dấu để chuỗi bỏ qua bước lỗi và chạy tiếp.
4. **Chạy theo lịch**: nhập **Giờ chạy** (ví dụ `08:00, 20:30`) và chọn **Ngày** (bỏ trống = mỗi ngày). Ô **Bật lịch tự động** ở trên cùng bật/tắt lịch cho mọi chuỗi; dòng bên cạnh cho biết lần chạy kế tiếp.
5. Bấm **Lưu**. **Chạy ngay** để chạy thử; **Dừng chuỗi** (hoặc phím Dừng) để dừng.

Lưu ý:

- Lịch chỉ chạy khi AutoNgoaLong đang mở. Nếu bot vừa mở sau giờ hẹn vài phút, chuỗi vẫn được chạy; trễ quá lâu thì bỏ qua lần đó.
- Nếu một tính năng khác đang chạy tới giờ hẹn, chuỗi xếp hàng chờ; chờ quá lâu thì bị hủy và ghi vào Nhật ký.
- Chuỗi được lưu trong `data\routines.json`.

## 7. Nhiều cửa sổ (nhiều tài khoản)

Thẻ **Nhiều cửa sổ** chạy song song một tính năng trên mỗi cửa sổ game (máy chủ này cho phép dùng bot).

1. Mở mỗi tài khoản trong một cửa sổ Coowon riêng, rồi bấm **Tìm cửa sổ**.
2. Với mỗi cửa sổ, chọn tính năng muốn chạy. Thông số lấy từ thẻ **Tính năng**.
3. Bấm **Bắt đầu** / **Tạm dừng** / **Dừng** ở từng dòng, hoặc **Bắt đầu tất cả** / **Dừng tất cả**. Phím Dừng dừng mọi cửa sổ.

Một cửa sổ đang chạy ở thẻ Tính năng (hoặc trong một chuỗi tác vụ) thì không chạy được ở thẻ Nhiều cửa sổ, và ngược lại. Lớp phủ gỡ lỗi chỉ đi theo cửa sổ chọn ở trên cùng; các cửa sổ khác xem ở thẻ Gỡ lỗi.

## 8. Các thẻ khác

- **Lịch sử**: mọi lần chạy (tính năng, cửa sổ, giờ, kết quả, tóm tắt). **Mở ảnh lỗi** mở ảnh chụp lúc lỗi; **Mở thư mục nhật ký** mở thư mục của lần chạy; **Xóa lịch sử** xóa danh sách. Lưu trong `data\history.sqlite3`.
- **Thông báo**: chọn gửi khi **Hoàn thành**, **Bị dừng**, **Gặp lỗi**; thông báo ở khay hệ thống; Telegram (mã bot + chat id) và Discord (webhook `https://discord.com/api/webhooks/...`), có thể đính kèm ảnh chụp game. Bấm **Lưu**, rồi **Gửi thử**. Mã bot và webhook nằm trong `settings.toml`: đừng chia sẻ tệp đó.
- **Mẫu ảnh** (dành cho người dùng nâng cao): **Chụp từ game** hoặc **Mở ảnh…**, kéo chuột chọn một vùng, **Thử vùng chọn** để xem vùng đó có dễ nhầm không, đặt tên rồi **Lưu mẫu**. **Thử mẫu đã lưu** kiểm tra điểm khớp của một mẫu có sẵn. Ảnh mẫu lưu vào `profiles\ngoalongviet\templates\`.
- **Cài đặt**: đổi phím tắt **Dừng**, **Bật/tắt gỡ lỗi**, **Tạm dừng / tiếp tục** (phải có Ctrl, Alt hoặc Win; để trống ô Tạm dừng = không dùng), rồi bấm **Áp dụng**. Thẻ cũng cho biết đường dẫn tệp cài đặt và tệp INI cũ đã nhập.
    - **Trong game → Dùng vàng bỏ qua thời gian chờ** (mặc định tắt). Khi game hỏi *"Bỏ qua thời gian chờ tốn … Vàng. Xác nhận?"* (ví dụ sau khi bấm Tăng cấp): **bật** thì bot bấm **确定** (tốn Vàng, không phải chờ) rồi chạy tiếp; **tắt** thì bot bấm **取消** và dừng tính năng đang chạy, ghi lý do vào Nhật ký. Thay đổi có tác dụng ngay, kể cả khi đang chạy. Bot không bao giờ tích ô "Lần sau không nhắc nhở". Câu hỏi này chỉ hiện khi tuỳ chọn cảnh báo trong nút bánh răng (góc trên bên phải bản đồ Thành chính) đang bật.
    - **Trong game → Thu gọn các bảng che game trước khi chạy** (mặc định bật). Trước mỗi tính năng, bot thu gọn bảng nhiệm vụ bên phải, khung "Sắp mở" và khung chat, để chúng không che nhà và đạo quân. Hàng biểu tượng trên cùng không bị thu gọn, vì nhiều tính năng mở từ đó (Quay Tinh Tú, ...). Áp dụng từ lần chạy sau.

## 9. Sửa tọa độ và màn hình không cần build lại

Mọi tọa độ click và mọi định nghĩa màn hình nằm trong `profiles\ngoalongviet\`:

- `coords\<tính năng>.yaml`: tọa độ click trên khung game 1000×600 (mỗi tệp một tính năng);
- `scenes\*.yaml`: mỗi màn hình gồm các ảnh mẫu nhận dạng ("signature") và các vật trên màn hình;
- `templates\**\*.png`: ảnh mẫu;
- `locator.yaml`: các mốc dùng để tìm khung game.

Sửa bằng Notepad (lưu dạng UTF-8), khởi động lại bot. Sao lưu thư mục `profiles` trước khi sửa. Sau khi sửa, chạy `--selftest` để chắc chắn các tệp vẫn đọc được và đủ ảnh mẫu.

## 10. Xử lý sự cố

| Hiện tượng | Cách xử lý |
| --- | --- |
| Danh sách cửa sổ ghi "Không thấy cửa sổ Coowon đang mở Ngọa Long" | Mở game trong **Coowon** (không phải Chrome/Edge), chờ tiêu đề tab có "Ngọa Long", rồi bấm **Tìm lại**. |
| Coowon báo "Adobe Flash Player was blocked" | Bấm **Run this time** mỗi lần tải lại trang. |
| `Khung game: … không thấy` hoặc `chưa chắc chắn` | Cửa sổ game bị thu nhỏ, game đang tải, hoặc có hộp thoại che khung. Mở lại cửa sổ, đưa game về Thành chính, đặt thu phóng 100% (Ctrl+0), bấm **Kiểm tra**. |
| `Màn hình: không rõ (gần nhất: …)` | Bot không nhận ra màn hình hiện tại. Đóng các bảng đang mở rồi đưa game về màn hình bắt đầu của tính năng (mục 4). |
| Tính năng dừng ngay với "không đúng màn hình" | Mở đúng màn hình bắt đầu trước khi bấm Bắt đầu. Bật gỡ lỗi để xem khung đỏ: vật nào bot tìm mà không thấy. |
| Bot dừng vì hết thời gian chờ | Máy hoặc mạng chậm: tăng "Thời gian chờ màn hình (giây)" hoặc "Thời gian tối đa mỗi trận". Xem ảnh lỗi ở thẻ Lịch sử. |
| Bot click lệch | Đặt thu phóng trang về 100%. Bật gỡ lỗi: dấu cộng vàng là chỗ click. Nếu một điểm lệch hẳn, sửa tọa độ trong `profiles\ngoalongviet\coords\`. |
| Phím tắt không chạy | Một chương trình khác đã giữ tổ hợp đó: Nhật ký ghi "Phím tắt: …". Đổi sang tổ hợp khác ở thẻ Cài đặt. |
| Telegram/Discord không nhận được tin | Kiểm tra mã bot, chat id, webhook; bấm **Gửi thử** và đọc dòng kết quả. |
| `settings.toml` bị hỏng | Bot đổi tên tệp hỏng thành `settings.toml.bak` và chạy với cài đặt mặc định. |
| Bot không mở được, không hiện cửa sổ | Xem `logs\app.log`. Chạy `--selftest` (mục 1) và gửi `selftest-report.txt` khi báo lỗi. |
| `--selftest` báo `FAIL [profile] templates` | Có ảnh mẫu bị xóa hoặc đổi tên trong `profiles\`. Lấy lại thư mục `profiles` từ bản tải về. |
| Windows Defender chặn hoặc xóa tệp | Tệp exe chưa ký số. Thêm thư mục bot vào danh sách loại trừ nếu bạn tin nguồn tải. |

Khi báo lỗi, gửi: dòng tóm tắt ở Nhật ký, thư mục `logs\run-<mã>\` của lần chạy (có ảnh lỗi), và nếu có thể, một lần chạy lại với chế độ gỡ lỗi bật.
