# AutoNgoaLong v2

**AutoNgoaLong** là chương trình tự động chơi giúp bạn một số việc lặp đi lặp lại trong game **Ngọa Long** (máy chủ ngoalongviet.com). Bot tự bấm chuột vào game thay bạn, **ngay cả khi bạn đang làm việc khác trên máy**.

> Bạn là lập trình viên muốn sửa mã nguồn hoặc tự build? Xem [development-guide.md](development-guide.md) (tiếng Anh).

## Bot làm được gì?

| Tính năng | Bot làm giúp bạn |
| --- | --- |
| **Nâng cấp công trình** | Nâng lần lượt các công trình trong Thành chính và/hoặc Hải cảng, công trình thấp nhất trước. |
| **Quay Tinh Tú** | Mở rương Tinh Tú thật nhanh (khoảng 200 rương trong 10 giây), có thể mua thêm rương trước. |
| **Nâng cấp khoa kỹ** | Nâng các ô khoa kỹ trong Phủ Sách Lược. |
| **Tháp Thí Luyện** | Leo tháp từ tầng này đến tầng kia, hoặc quét (tự vượt) một tầng đã thắng nhiều lần. |
| **Clear Phó Bản** | Đánh hết các đạo quân trong phó bản, rồi tự sang phó bản tiếp theo. |
| **Võ Khôi Tháp** | Đánh boss Võ Khôi Tháp liên tục; tự mua rương khi bạc sắp đầy, tự mở rương Tinh Tú khi hết Quân lệnh. |
| **AutoClick** | Bấm lần lượt vào các điểm bạn chọn trên game, lặp lại nhiều lần. |

Bạn có thể **bấm Bắt đầu ở bất kỳ màn hình nào** của game: bot tự đi tới đúng chỗ, tự đóng các bảng thông báo đang che. Bot cũng có thể **tự chạy theo giờ** (ví dụ 8 giờ sáng mỗi ngày) và **chạy nhiều tài khoản cùng lúc**.

---

## Bắt đầu nhanh

1. Cài trình duyệt **Coowon** và vào game Ngọa Long bằng Coowon.
2. Tải tệp `AutoNgoaLong-…-win64.zip` ở trang [Releases](../../releases/latest).
3. Giải nén vào ổ D (ví dụ `D:\AutoNgoaLong`), mở `AutoNgoaLong.exe`.
4. Nếu Windows hiện màn hình xanh "Windows protected your PC": bấm **More info** → **Run anyway** (xem [mục 3](#3-windows-báo-chương-trình-nguy-hiểm)).
5. Trong bot, bấm **Kiểm tra** → chọn tính năng → bấm **Bắt đầu**.

Muốn dừng bot bất cứ lúc nào: bấm **Dừng**, hoặc nhấn **`Ctrl + Alt + F10`**.

Các mục dưới đây giải thích chi tiết từng bước.

---

## 1. Bạn cần có

- **Máy tính Windows 10 hoặc Windows 11** (bản 64-bit, hầu hết máy hiện nay đều là bản này). Bot không chạy trên máy Mac.
- **Trình duyệt Coowon.** Game Ngọa Long dùng Flash, mà Chrome, Edge, Cốc Cốc… đã bỏ Flash. Coowon vẫn chạy được Flash. **Bot chỉ làm việc với game mở trong Coowon.**
- **Tài khoản Ngọa Long** trên ngoalongviet.com.

Bạn **không cần cài thêm gì khác**. Mọi thứ bot cần đã nằm sẵn trong tệp tải về.

## 2. Tải về và cài đặt

1. Mở trang [Releases](../../releases/latest), tải tệp có tên dạng `AutoNgoaLong-v2.0.1-win64.zip`.
2. **Bỏ chặn tệp vừa tải (nên làm):** bấm chuột phải vào tệp zip → chọn **Properties** → nếu ở dưới cùng có ô **Unblock** thì đánh dấu vào → bấm **OK**. Việc này giúp Windows bớt cảnh báo về sau.
3. **Giải nén:** bấm chuột phải vào tệp zip → **Extract All…** → chọn nơi giải nén, ví dụ `D:\AutoNgoaLong` → **Extract**.
    - Nên để ở ổ D hoặc trong thư mục Documents.
    - **Không** để trong `C:\Program Files`: bot cần lưu cài đặt và nhật ký ngay trong thư mục của nó, mà Windows không cho ghi vào đó.
4. Mở thư mục vừa giải nén, bấm đúp vào **`AutoNgoaLong.exe`** để chạy bot. Bạn có thể bấm chuột phải vào tệp này → **Send to** → **Desktop (create shortcut)** để có biểu tượng ngoài màn hình chính.

Trong thư mục bot:

| Tên | Là gì |
| --- | --- |
| `AutoNgoaLong.exe` | Chương trình bot. Bấm đúp để mở. |
| `HUONG-DAN.md` | Tài liệu hướng dẫn này. |
| `_internal` | Các tệp bot cần để chạy. **Đừng sửa, đừng xóa.** |
| `profiles` | Dữ liệu giúp bot nhận ra màn hình game. **Đừng sửa** nếu không được hướng dẫn. |
| `settings.toml`, `logs`, `data` | Bot tự tạo khi chạy lần đầu: cài đặt của bạn, nhật ký, lịch sử. |

## 3. Windows báo chương trình nguy hiểm?

Lần đầu mở bot, Windows có thể hiện màn hình xanh **"Windows protected your PC"**, hoặc phần mềm diệt virus (Windows Defender) chặn hay xóa tệp. **Đây là chuyện bình thường với các bot game nhỏ như thế này**, vì:

- **Bot không có "chữ ký" của nhà phát hành.** Muốn có chữ ký phải mua chứng nhận hằng năm, nên các chương trình nhỏ, miễn phí thường không có. Windows thấy chương trình lạ, ít người tải thì cảnh báo cho chắc.
- **Bot bấm chuột và chụp ảnh cửa sổ của chương trình khác** (cửa sổ game). Bot game nào cũng phải làm vậy, nhưng phần mềm diệt virus lại hay nghi ngờ những việc này.
- **Bot được đóng gói thành một tệp .exe tự giải nén**, giống cách một số phần mềm xấu hay dùng, nên đôi khi bị nghi ngờ nhầm.

Bot **không** cần quyền quản trị (Administrator), **không** cài gì vào máy, **không** đổi cài đặt Windows, và **không** gửi gì lên mạng (trừ khi bạn tự bật thông báo qua Telegram/Discord, xem [mục 9](#9-thông-báo-về-điện-thoại)).

**Cách cho phép bot chạy** (chỉ làm nếu bạn tải bot từ nguồn bạn tin tưởng):

- **Màn hình xanh "Windows protected your PC":** bấm dòng chữ **More info** → bấm nút **Run anyway**. Chỉ cần làm một lần.
- **Windows Defender chặn hoặc xóa tệp:**
    1. Bấm nút Start, gõ **Windows Security**, mở lên.
    2. Chọn **Virus & threat protection** → **Protection history**.
    3. Bấm vào mục bị chặn → **Actions** → **Allow on device** (Cho phép).
    4. Để không bị chặn lại: trong **Virus & threat protection**, bấm **Manage settings** → kéo xuống **Exclusions** → **Add or remove exclusions** → **Add an exclusion** → **Folder** → chọn thư mục bot (ví dụ `D:\AutoNgoaLong`).
    5. Nếu Defender đã xóa mất tệp, giải nén lại tệp zip.

## 4. Lần đầu mở bot

1. Mở **Coowon**, vào game. Nếu Coowon hiện thanh báo "Adobe Flash Player was blocked", bấm **Run this time**.
2. Để trang game ở **cỡ chữ 100%**: bấm vào trang game rồi nhấn **`Ctrl + 0`** (số không).
3. Mở **`AutoNgoaLong.exe`**.
4. Ở phía trên cửa sổ bot, trong ô **Cửa sổ game (Coowon)**, bot tự tìm cửa sổ game của bạn. Không thấy thì bấm **Tìm lại**.
5. Bấm **Kiểm tra**. Nếu dòng *Khung game* có chữ **"tin cậy"** là bot đã nhìn thấy game. Bạn đã sẵn sàng!

Nếu dòng đó ghi **"không thấy"** hoặc **"chưa chắc chắn"**: đợi game tải xong, đóng các hộp thoại đang che game, đặt lại cỡ chữ 100% (`Ctrl + 0`), rồi bấm **Kiểm tra** lại.

### Ba điều cần nhớ khi bot chạy

- **Không thu nhỏ (minimize) cửa sổ game** xuống thanh tác vụ. Khi bị thu nhỏ, Windows không vẽ game nữa nên bot không nhìn thấy gì.
- **Để cửa sổ game lộ ra ít nhất một phần.** Bạn vẫn có thể mở cửa sổ khác che bớt và làm việc khác bình thường.
- **Hạn chế bấm vào game trong lúc bot đang chạy**, để không làm bot bị rối.

## 5. Cách chạy một tính năng

1. Mở thẻ **Tính năng**, chọn tính năng ở danh sách bên trái.
2. Điền các ô ở bên phải. **Rê chuột lên tên một ô** để xem giải thích. Ô bị tô đỏ là giá trị chưa hợp lệ.
3. Bấm **Bắt đầu**. Game đang ở màn hình nào cũng được.
4. Trong lúc chạy:
    - **Tạm dừng**: dừng tạm, bấm lại để chạy tiếp.
    - **Dừng** (hoặc **`Ctrl + Alt + F10`**): kết thúc ngay.
5. Khi xong, khung **Nhật ký** ở dưới cùng ghi lại bot đã làm được gì.

Bot **tự nhớ** những gì bạn điền, lần sau mở lên không cần điền lại. Tính năng nào cũng có ô **Thời gian chạy tối đa (phút)**: hết giờ thì bot tự dừng (để **0** nếu không muốn giới hạn).

**Bot tự lo giúp bạn:**

- Đóng các bảng phần thưởng, thông báo, hộp cốt truyện khi chúng hiện lên.
- Thu gọn bảng nhiệm vụ, khung chat… để chúng không che game.
- Khi bấm mà game không phản ứng như mong đợi, bot tự thử cách khác (đóng bảng, quay lại). Vẫn không được thì bot dừng, ghi lý do và chụp ảnh màn hình để bạn xem.
- Nếu game bị đơ (màn hình đứng yên 30 giây), bot dừng và nhắc bạn tải lại trang game (**F5**).

> **Về Vàng:** khi hết thợ xây, game hỏi *"Bỏ qua thời gian chờ tốn … Vàng?"*. Mặc định bot **không** tiêu Vàng: bot bấm Hủy và dừng lại. Muốn bot tiêu Vàng để chạy tiếp, bật **Dùng vàng bỏ qua thời gian chờ** trong thẻ **Cài đặt**.

## 6. Từng tính năng

### 6.1 Nâng cấp công trình

Bot đọc cấp của từng công trình, rồi nâng **công trình thấp nhất trước**, cho tới cấp bạn chọn. Công trình đã đạt cấp cao nhất thì bot bỏ qua.

- **Khu vực**: Thành chính, Hải cảng, hoặc Cả hai (làm Thành chính trước, rồi bot tự sang Hải cảng).
- **Cấp tối đa**: nâng tới cấp này thì thôi. Để **0** = nâng tới bằng cấp Chúa công.
- **Số lần bấm Tăng cấp mỗi lần mở**: mỗi lần mở một công trình, bot bấm Tăng cấp tối đa bấy nhiêu lần.
- Hết thợ xây: xem lưu ý **Về Vàng** ở mục 5.

### 6.2 Quay Tinh Tú

Bot tự mở bảng **Tinh Tú**, mua thêm rương nếu bạn muốn, rồi mở rương rất nhanh.

- **Số rương cần mở**: để **0** = mở hết rương đang có.
- **Mua thêm rương trước khi mở**: mỗi rương **240 Vàng**. Để **0** = không mua.
- Các ô về "mở nhanh" (số rương mỗi đợt, khoảng cách giữa các click): **để mặc định**.

### 6.3 Nâng cấp khoa kỹ

Bot mở **Phủ Sách Lược** → thẻ **Khoa kỹ**, rồi nâng lần lượt từng ô. Ô nào đã tối đa thì bỏ qua.

- **Số cấp nâng tối đa mỗi ô khoa kỹ**: mỗi ô nâng nhiều nhất bấy nhiêu cấp.
- **Các ô khoa kỹ**: để **trống** = cả 18 ô. Muốn chọn một số ô: các ô được đánh số theo hàng, từ trái sang phải, bắt đầu từ 1. Ví dụ `1-8` = 8 ô đầu.
- Nếu mọi ô đều đã tối đa, bot nhắc bạn nâng công trình Phủ Sách Lược trước.

### 6.4 Tháp Thí Luyện

Bot tự mở bảng Tháp Thí Luyện và chọn tầng cho bạn. Có hai chế độ:

- **Vượt tháp**: đánh từ **Tầng bắt đầu** tới **Tầng kết thúc**. Mỗi tầng tốn 3 Thí luyện lệnh. Thắng thì lên tầng sau; **thua thì bot dừng**.
- **Tự vượt tháp** (quét): quét **Số lần tự vượt tháp** lần ở **Tầng bắt đầu**. Tầng này phải là tầng bạn **đã thắng**, nếu chưa thì bot dừng và báo.
- **Thời gian tối đa mỗi tầng (giây)**: quá thời gian này mà chưa có kết quả Thắng/Bại thì bot dừng.

### 6.5 Clear Phó Bản

Bot mở **Phó Bản**, tìm đạo quân tiếp theo (đạo quân có **tia sáng xanh**), đánh nhanh (bỏ qua xem trận), rồi tìm đạo quân kế tiếp. Bot đánh được cả đoàn chiến và boss. Hết đạo quân thì bot bấm **»** để sang phó bản tiếp theo.

- **Hết đạo quân thì sang phó bản kế tiếp**: bật để đánh nối nhiều phó bản.
- **Số phó bản được chuyển sang tối đa**, **Số trận tối đa**: để **0** = không giới hạn số trận.
- **Dừng khi thua liên tiếp bấy nhiêu trận**: mặc định 3.
- Hết **Quân lệnh** thì bot dừng.

### 6.6 Võ Khôi Tháp

Bot đánh boss Võ Khôi Tháp liên tục, phù hợp để **chạy lâu** (ví dụ treo qua đêm).

- **Boss**: để **0** = đánh boss đang hiện trong sảnh; số N = boss thứ N.
- **Số lượt đánh**: để **0** = đánh mãi cho tới khi bạn bấm Dừng.
- **Khi bạc vượt 2 tỷ**: bạc tối đa trong game là 2 tỷ, phần dư bị mất. Bạn chọn:
    - *Mua rương 1 tỷ bạc rồi chạy tiếp* (mặc định): bot vào Cửa hiệu mua rương 1 tỷ bạc (mỗi lần mua **Số rương 1 tỷ bạc mua mỗi lần**, mặc định 2) để bạc không bị phí;
    - *Bỏ qua, chạy tiếp*; hoặc *Dừng*.
- **Hết Quân lệnh thì mở rương Tinh Tú**: rương Tinh Tú có thể cho Quân lệnh, bot mở rương rồi đánh tiếp.
    - **Hết rương Tinh Tú thì mua thêm (tốn Vàng)**: 240 Vàng mỗi rương, mua **Số rương Tinh Tú mua mỗi lần**.
    - **Số lần mở rương tối đa**: để **0** = không giới hạn.
    - Mở rương mà không có thêm Quân lệnh, hoặc không mua được rương, thì bot dừng.

### 6.7 AutoClick

Bot bấm lần lượt vào các điểm bạn chọn trên game, rồi lặp lại. AutoClick **không nhìn** game, chỉ bấm đúng chỗ đã ghi.

- **Thêm điểm**: bấm **Ghi từ con trỏ (3 giây)**, rồi trong 3 giây đưa chuột tới chỗ cần bấm trên game và giữ yên. Hoặc bấm **Thêm** để tự gõ tọa độ.
- Điểm được tính theo khung game, nên kéo cửa sổ đi chỗ khác vẫn bấm đúng.
- **Kiểu click**: chuột trái/giữa/phải, bấm một lần hoặc bấm đúp.
- **Thời gian giữa 2 lần click (ms)**: 1000 ms = 1 giây.
- **Số vòng**: để **0** = lặp mãi cho tới khi bấm Dừng.

## 7. Tự chạy theo giờ (Chuỗi tác vụ)

Thẻ **Chuỗi tác vụ** giúp bot chạy **nhiều tính năng nối tiếp nhau**, bằng tay hoặc **tự động theo giờ**. Ví dụ: 8 giờ sáng mỗi ngày → Nâng cấp khoa kỹ → Nâng cấp công trình → Tháp Thí Luyện → Võ Khôi Tháp.

1. Bấm **Thêm chuỗi**, đặt **Tên**.
2. Bấm **Thêm bước**, chọn tính năng. Lặp lại cho mỗi tính năng muốn chạy. Dùng **Lên** / **Xuống** để đổi thứ tự.
    - Mỗi bước dùng thông số bạn đã điền ở thẻ **Tính năng**.
    - **Thử lại**: bước bị lỗi thì chạy lại bấy nhiêu lần.
    - **Tối đa (phút)**: bước chạy quá thời gian này thì chuyển sang bước sau.
3. Muốn chạy theo giờ: gõ **Giờ chạy** (ví dụ `08:00, 20:30`), chọn **Ngày** (để trống = mỗi ngày), và đánh dấu **Bật lịch tự động** ở trên cùng.
4. Bấm **Lưu**. Bấm **Chạy ngay** để thử.

> **Quan trọng:**
> - Với **Võ Khôi Tháp** và **AutoClick** đặt chạy mãi (số lượt/số vòng = 0), **phải điền Tối đa (phút)**, nếu không chuỗi sẽ kẹt ở bước đó mãi.
> - Lịch chỉ chạy khi **bot đang mở** (và máy không tắt, không ngủ).

## 8. Nhiều tài khoản cùng lúc

Thẻ **Nhiều cửa sổ** chạy mỗi tài khoản một tính năng, cùng một lúc.

1. Mở mỗi tài khoản trong **một cửa sổ Coowon riêng**, rồi bấm **Tìm cửa sổ**.
2. Ở mỗi dòng (mỗi cửa sổ), chọn tính năng muốn chạy. Thông số lấy từ thẻ **Tính năng**.
3. Bấm **Bắt đầu** ở từng dòng, hoặc **Bắt đầu tất cả**. Phím **`Ctrl + Alt + F10`** dừng tất cả.

## 9. Thông báo về điện thoại

Thẻ **Thông báo** cho bot báo cho bạn khi chạy xong, bị dừng hoặc gặp lỗi: hiện thông báo ở góc màn hình, hoặc gửi tin nhắn qua **Telegram** hay **Discord** (có thể kèm ảnh chụp game). Điền thông tin, bấm **Lưu**, rồi bấm **Gửi thử** để kiểm tra.

Mã Telegram/Discord được lưu trong tệp `settings.toml`: **đừng gửi tệp này cho người khác.**

## 10. Thẻ Cài đặt và Lịch sử

**Cài đặt:**

- **Phím tắt**: đổi phím Dừng (mặc định `Ctrl + Alt + F10`), phím bật/tắt Gỡ lỗi (`Ctrl + Alt + D`) và phím Tạm dừng. Phím tắt phải có Ctrl, Alt hoặc Win. Bấm **Áp dụng**.
- **Dùng vàng bỏ qua thời gian chờ** (mặc định tắt): xem lưu ý **Về Vàng** ở mục 5. Lưu ý: game tự xóa lựa chọn "dùng vàng" mỗi khi bạn tải lại trang.
- **Thu gọn các bảng che game khi chạy** (mặc định bật): bot thu gọn bảng nhiệm vụ, khung chat… để chúng không che công trình và đạo quân.

**Lịch sử:** danh sách mọi lần chạy và kết quả. Bấm **Mở ảnh lỗi** để xem ảnh chụp lúc lỗi, **Mở thư mục nhật ký** để mở thư mục chứa nhật ký của lần chạy đó.

## 11. Khi gặp sự cố

| Bạn thấy | Cách xử lý |
| --- | --- |
| Bot ghi "Không thấy cửa sổ Coowon đang mở Ngọa Long" | Mở game bằng **Coowon** (không phải Chrome/Edge/Cốc Cốc), đợi game tải xong, rồi bấm **Tìm lại**. |
| Coowon báo "Adobe Flash Player was blocked" | Bấm **Run this time**. Mỗi lần tải lại trang phải bấm lại. |
| Dòng *Khung game* ghi "không thấy" hoặc "chưa chắc chắn" | Cửa sổ game đang bị thu nhỏ, game đang tải, hoặc có hộp thoại che game. Mở cửa sổ game lên, nhấn `Ctrl + 0`, bấm **Kiểm tra**. |
| Bot bấm lệch chỗ | Nhấn `Ctrl + 0` trên trang game để về cỡ chữ 100%. |
| "game không phản hồi" | Game bị đơ. Nhấn **F5** để tải lại trang game, rồi chạy lại. |
| "cửa sổ game bị che, hãy để lộ một phần cửa sổ" | Kéo cửa sổ game ra để thấy được một phần, rồi chạy lại. |
| Nhật ký có vài dòng "Gỡ rối (…)" | Bình thường: game chậm, bot đã tự xử lý. Chỉ cần quan tâm nếu bot dừng hẳn. |
| Phím tắt không có tác dụng | Chương trình khác đang dùng phím đó. Đổi sang phím khác trong thẻ **Cài đặt**. |
| Telegram/Discord không nhận được tin | Kiểm tra lại mã, bấm **Gửi thử** và đọc dòng kết quả. |
| Windows báo nguy hiểm, Defender chặn hoặc xóa tệp | Xem [mục 3](#3-windows-báo-chương-trình-nguy-hiểm). |
| Bot không mở lên được | Kiểm tra Defender có xóa tệp không (mục 3). Nếu có, cho phép rồi giải nén lại. |

### Cách báo lỗi

Khi nhờ người khác xem lỗi, hãy gửi:

1. Dòng cuối trong khung **Nhật ký** (dòng tóm tắt).
2. **Thư mục nhật ký của lần chạy bị lỗi**: vào thẻ **Lịch sử**, chọn lần chạy đó, bấm **Mở thư mục nhật ký**, rồi nén thư mục đó (chuột phải → **Send to** → **Compressed (zipped) folder**) và gửi tệp zip.
3. Nếu được nhờ: bật **Gỡ lỗi** (`Ctrl + Alt + D`), chạy lại cho tới khi lỗi, rồi gửi thư mục nhật ký mới. Khi bật, bot vẽ khung màu lên game để cho thấy nó đang "nhìn" gì, và lưu ảnh từng bước.

## 12. Cập nhật lên bản mới

1. Tải bản mới, giải nén vào **một thư mục mới** (ví dụ `D:\AutoNgoaLong-2.1`).
2. Từ thư mục cũ, chép tệp **`settings.toml`** và thư mục **`data`** sang thư mục mới, để giữ cài đặt, lịch sử và chuỗi tác vụ.
3. Mở bot từ thư mục mới. Chạy tốt rồi thì có thể xóa thư mục cũ.

## 13. Dành cho người rành máy tính

**Tự kiểm tra bản cài** (không cần mở game): mở PowerShell trong thư mục bot và chạy

```powershell
Start-Process .\AutoNgoaLong.exe -ArgumentList "--selftest" -Wait
Get-Content .\selftest-report.txt -Encoding UTF8
```

Mỗi dòng `OK` là một phép kiểm tra đạt, dòng cuối ghi tổng số lỗi. Dòng `SKIP [perception]` là bình thường với bản tải về (gói không kèm bộ ảnh game mẫu). Dòng `FAIL [profile]` nghĩa là có tệp trong `profiles` bị sửa hỏng hoặc bị xóa: lấy lại thư mục `profiles` từ tệp zip.

**Sửa dữ liệu nhận diện:** mọi thứ bot dùng để nhận ra game (vị trí nút, màn hình, đường đi giữa các màn hình, ảnh mẫu) nằm trong `profiles\ngoalongviet\`, là các tệp văn bản `.yaml` và ảnh `.png`. Sửa được bằng Notepad (lưu dạng UTF-8) rồi mở lại bot, không cần build lại. Hãy sao lưu thư mục `profiles` trước khi sửa và chạy `--selftest` sau khi sửa.

**Các tệp bot tạo ra:** `settings.toml` (cài đặt; nếu bị hỏng, bot đổi tên thành `settings.toml.bak` và dùng cài đặt mặc định), `logs\app.log` (nhật ký chung), `logs\run-<mã>\` (nhật ký `run.log` và ảnh của từng lần chạy), `data\history.sqlite3` (lịch sử), `data\routines.json` (chuỗi tác vụ).

**Chế độ Gỡ lỗi** còn bật được bằng `debug = true` trong `settings.toml` hoặc chạy `AutoNgoaLong.exe --debug`. Màu các khung vẽ trên game: xanh lơ = khung game bot tìm được (đỏ nếu chưa chắc), xanh lá = nút/bảng bot nhận ra, đỏ = nút ✕ của bảng lạ, dấu cộng vàng = chỗ bot sắp bấm.

## 14. Giấy phép

AutoNgoaLong được phát hành theo giấy phép **MIT** (xem tệp [LICENSE](LICENSE), trong gói tải về là `LICENSE.txt`): bạn được tự do dùng, sửa và chia sẻ. Máy chủ ngoalongviet.com cho phép dùng bot. Bạn dùng bot trên tài khoản của mình và tự chịu rủi ro.
