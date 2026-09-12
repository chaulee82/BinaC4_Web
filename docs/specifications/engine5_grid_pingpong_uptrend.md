# TÀI LIỆU ĐẶC TẢ KỸ THUẬT: HỆ THỐNG ĐỘNG CƠ 5 (GRID PINGPONG UPTREND)

## 1. Tổng Quan Mục Tiêu
Chuyển đổi Động cơ 5 từ mô hình "Sideways Pingpong" tĩnh sang **"Uptrend Pingpong" động**. Hệ thống tập trung săn lùng các đồng coin có cấu trúc tăng trưởng vĩ mô khỏe, tự động bung lưới đối xứng dựa trên móng hỗ trợ 4H. Mục tiêu cốt lõi là tối đa hóa tần suất cày dòng tiền tươi (Grid Profit) trong chu kỳ 3 đến 7 ngày mà không lo rủi ro dời móng đu đỉnh.

---

## 2. Quy Trình Sàng Lọc Mã (5 Bước Tiêu Chuẩn)

### Bước 1: Sàng Lọc Vĩ Mô Khung 4H (Bệ Đỡ Xu Hướng)
- Đường giá và các đường trung bình động phải duy trì trạng thái dốc lên ($\text{EMA20} > \text{EMA50}$).
- Chỉ báo **Supertrend khung 4H** bắt buộc hiển thị trạng thái màu xanh để xác nhận cấu trúc vĩ mô an toàn.

### Bước 2: Kiểm Tra Nhịp Điệu Vi Mô & Tránh Đỉnh (Khung 1H/15m)
- Chỉ số **RSI 1H** phải hạ nhiệt xuống dưới ngưỡng 70 để triệt tiêu hoàn toàn các mã đang hưng phấn tột độ (Climax Top).
- Ưu tiên nhận diện các nhịp điều chỉnh kỹ thuật (Pullback) hoặc râu nến quét thanh khoản lành mạnh.

### Bước 3: Đo Lường Biên Độ Hộp Vĩ Mô (Chuẩn "Hộp 40% / 24 Lưới")
- Hệ thống tự động tính khoảng cách từ **Giá Trung Tâm (Center)** xuống mốc **Cắt lỗ cứng (Hard SL)** của khung 4H (dựa vào mốc Hỗ trợ hoặc MA99).
- Khoảng cách này bắt buộc nằm trong "vùng vàng" từ `-16%` đến `-20%` (tương ứng tổng biên độ hộp khoảng `35% - 40%`). *(Tham số cấu hình động)*.

### Bước 4: Đếm Vòng Đập Nhả Có Điều Kiện (Bounce Counter)
- Quan sát 30 - 50 nến gần nhất trên khung 1H/15m để đếm tần suất đập nhả thực tế.
- Áp dụng ngưỡng biên độ tối thiểu cho mỗi nhịp giật (Ví dụ: `≥ 1.69%`, `Bounce ≥ 4.0`, hoặc `Range ≥ 3.5%`) để lọc bỏ nhiễu thị trường (Noise Filter). *(Tham số cấu hình động)*.

### Bước 5: Chấm Điểm Tổng Hợp 3D & Xếp Hạng Final
- Tổng hợp điểm số dựa trên **Tần suất đập nhả (Frequency)** kết hợp **Hệ số gia tốc dốc lên** (Khoảng cách từ giá hiện tại đến EMA50 4H).
- **Bộ chặn quá mua (Climax Filter)**: Phạt nặng hoặc loại bỏ hoàn toàn các mã đã rướn quá xa đường EMA50 (`>25%`) để tránh bẫy FOMO.

---

## 3. Thuật Toán Thiết Lập Hộp Lưới (Symmetric Macro Grid)

Sử dụng móng vĩ mô 4H làm điểm chặn rủi ro và bung lưới đối xứng:

1. **Giá Cân Bằng (Center)**: Trích xuất từ mức giá cân bằng hiện tại trên khung vi mô (1H).
2. **Giá Cắt Lỗ (Hard SL)**: Neo chặt theo mốc hỗ trợ cứng của khung 4H (Supertrend hoặc MA99).
3. **Đáy Lưới (Lower Price)**: Tính bằng `Hard SL + Buffer` (Kê cao hơn móng cắt lỗ từ `1%` đến `2%` để tạo lớp đệm bảo vệ nấc mua cuối cùng).
4. **Đỉnh Lưới (Upper Price)**: Tính theo công thức đối xứng qua trục Center: `Center + (Center - Lower Price)`.
5. **Quy Chuẩn Hộp & Số Lưới**: Hệ thống linh hoạt bung từ `12 đến 24 lưới` *(tham số cấu hình)* để ép mức sinh lời mỗi nấc (Profit/Grid) rơi vào điểm ngọt từ `1.5% - 1.8%`.

---

## 4. Quản Trị Vốn & Rủi Ro

### Phân Bổ Khối Lượng (Volume Allocation)
Áp dụng cơ chế phân bổ vốn linh hoạt theo kết quả Xếp hạng 3D (3D Scoring Ranking), tư duy phân tầng bảo vệ vốn:
- **Hạng S**: Đánh `100%` volume phân bổ tiêu chuẩn.
- **Hạng A**: Đánh `70%` volume phân bổ tiêu chuẩn.
- **Hạng B**: Đánh `50%` volume phân bổ tiêu chuẩn (mang tính chất thăm dò).

### Đóng Lưới Khẩn Cấp (Emergency Stop)
- Kích hoạt lệnh bán tháo toàn bộ tài sản về USDT (**Sell all on stop**) ngay lập tức nếu giá thủng móng vĩ mô 4H hoặc chạm mốc Stop-Loss cứng đã định hình (ví dụ các case cấu hình biên độ ±10%).
- Cơ chế này nhằm thiết lập ranh giới an toàn tuyệt đối, tránh tình trạng "cưa chân bàn" khi xu hướng vĩ mô bị phá vỡ gãy.

---

## 5. Biến Số Cấu Hình Hệ Thống (Configurable Variables)

> [!WARNING]
> Tuyệt đối không hardcode các thông số chiến lược. Tất cả các tham số dưới đây bắt buộc phải được định nghĩa trong file cấu hình (.env, YAML/JSON) hoặc có thể điều chỉnh trực tiếp trên UI quản trị của BinaC4 để hệ thống linh hoạt thích ứng với từng giai đoạn thị trường.

- **Số lượng lưới (`grid_count`)**: Phạm vi `12 - 24` lưới.
- **Biên độ hộp vĩ mô (`macro_box_range`)**: Khoảng cách Center đến Hard SL (ví dụ: `-16% đến -20%`, hoặc `±10%`).
- **Ngưỡng lọc Bounce (`bounce_threshold`)**: `≥ 4.0`, hoặc `≥ 1.69%`.
- **Ngưỡng biên độ đập nhả (`range_threshold`)**: `≥ 3.5%`.
- **Lớp đệm đáy lưới (`lower_price_buffer`)**: `1% - 2%`.
- **Bộ lọc Climax (`climax_penalty_threshold`)**: Khoảng cách rướn tối đa so với EMA50 4H (ví dụ: `25%`).
- **Mức chốt lời mỗi lưới mục tiêu (`target_profit_per_grid`)**: `1.5% - 1.8%`.
