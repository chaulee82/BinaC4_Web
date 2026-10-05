# Tài liệu Đặc tả Hệ thống Động cơ (BinaC4 Engines)

## 1. Cấu trúc Tổng thể (System Overview)
- **Dự án:** CN4-Platform (BinaC4)
- **Môi trường:** Live / Dry-Run (Mock Mode)
- **Timeframe:** 15M (Tín hiệu) / 1H (Xác nhận) / 4H & 1D (Khung Vĩ mô)

## 2. Đặc tả Logic Cốt lõi (Engine Specifications)

### Động cơ 1: Lưới Darvas (Darvas Grid & Macro 1D)
- **Điều kiện Kích hoạt:** Tích lũy hộp nén $\ge 50\%$, Chiết khấu $\ge 60\%$, Cung giảm dần (Nến gom).
- **Logic Xử lý (Dual Grid):**
  - **Lưới Dưới (70% Vốn):** Gom đáy, biên độ hẹp (20-25 lưới).
  - **Lưới Trên (30% Vốn):** Hứng Breakout, bám theo trần ATR.
- **Đầu ra:** JSON tọa độ `[Grid 1, Grid 2, SL, TP]`.

### Động cơ 2: Pullback Sniper (Đánh chặn râu nến)
- **Điều kiện Kích hoạt:** Tín hiệu Early Warning (EW) ở trạng thái an toàn (Cấp 3).
- **Logic Xử lý:** Đẩy 2 lệnh OCO song song (OCO-1 chốt ngắn, OCO-2 gồng lãi).
- **Quản trị Rủi ro:** Kích hoạt Trailing Stop dời SL về vạch hòa vốn (Break-even) ngay khi OCO-1 khớp TP.

### Động cơ 3: Momentum Breakout (Đột phá Dòng tiền)
- **Cổng kiểm duyệt (Gate):** Hủy toàn bộ lệnh nếu BTC 1H gãy MA25 (Rủi ro Fakeout).
- **Điều kiện Kích hoạt:** Nến 15M đóng cửa vượt đỉnh cũ, Volume bạo phát ($> 3x$), Taker Buy áp đảo.
- **Bộ chặn (Blocker):** Tự động TỪ CHỐI nếu tỷ lệ R/R $< 2.0$.

### Động cơ 4: Hot Trend Pullback (Bắt Sóng Hồi)
- **Điều kiện Kích hoạt:** EMA20 cắt lên EMA50, RSI nằm trong ngưỡng $50-70$.
- **Bộ lọc Bắt Đáy:** Giá hồi (Pullback) về vùng Fib $0.382 - 0.5$ kèm điều kiện kiệt cung.
- **Đầu ra:** Lệnh OCO chờ mua tại đường MA25.

### Động cơ 5: Grid Pingpong & Perpetual OCO
- **Phân loại Hạng S/A (ETHFI, RAY):**
  - **Điều kiện:** Tần suất lật pha ($F$) cao, biên độ đều.
  - **Thực thi:** 24 mắt lưới.
  - **Tối ưu Entry:** Kích hoạt lưới tại mốc 1/3 (Công thức: `Low + (Range / 3)`).
- **Phân loại Bạo lực (MINA - Perpetual OCO):**
  - **Điều kiện:** $cycles \le 1.5$ VÀ $avg\_range \ge 20.0$.
  - **Thực thi:** Ép cấu hình về 2 lưới, loại bỏ hoàn toàn biến Trigger (để bot vào lệnh ngay).
  - **Quản trị:** Đệm Stop Loss cách đáy 1.5%.

### Tiện ích chung DC2/DC3/DC4: Dòng `🎯 [GRID TP2 - {symbol}]` (Spot Grid nhanh)
- **Module:** `core/grid_tp2_builder.py` → `build_macro_grid_payload()`; hiển thị qua `ConsoleRenderer._print_grid_tp2()`.
- **Thứ tự tham số (khớp form Binance Spot Grid):** `Lower - Upper | Grids(L) | Trig | SL | TP`.
- **Công thức:**
  - Fallback SL 4H: nếu `macro_sl` khuyết, $\le 0$ hoặc $\ge sl\_short \times 0.99$ → `macro_sl = sl_short × fallback_ratio`.
  - `lower = (sl_short + macro_sl) / 2`, `upper = tp2_target`.
  - `grids = clamp(int((upper - lower) / (lower × 0.009)), 10, 24)` (mỗi nấc ~0.9%).
  - `Trig = entry`, `SL = macro_sl`, `TP = upper`. Giá làm tròn theo `tick_size` (ExchangeInfoCache).
  - Bỏ qua (không in) nếu vi phạm `lower < entry < upper`.
- **Ánh xạ dữ liệu:**

| Động cơ | entry | sl_short | tp2_target | fallback_ratio |
| :--- | :--- | :--- | :--- | :--- |
| DC2 | OCO-2 Buy | OCO-2 SL | OCO-2 TP | 0.96 |
| DC3 | SETUP In | SETUP SL | TP2 (rỗng → TP1 × 1.05) | 0.95 |
| DC4 | SETUP In | SETUP SL | max(TP, TP 4H) (khuyết 4H → TP × 1.04) | 0.95 |

## 3. Quy ước Biến số Hệ thống (Variables Dictionary)

| Tên Biến trong Code | Định dạng | Ý nghĩa & Ứng dụng |
| :--- | :--- | :--- |
| `bounces_24h` | Float | Số vòng đập nhả chéo trục Center trong 24h. |
| `avg_range` | Float | Biên độ xóc ATR tính bằng %. |
| `center_line` | Float | Trục trọng tâm Trimmed Mean 25. |
| `lower_bound` | Float | Vạch đáy của hộp Grid. |
| `upper_bound` | Float | Vạch trần của hộp Grid. |
| `is_oco_perpetual` | Boolean | Cờ đánh dấu kích hoạt tính năng hack 2 lưới. |
