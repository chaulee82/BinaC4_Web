import math
from typing import List, Dict

class EntryCalculator:
    """
    Trạm Tính Toán Entry (Bước 1 trong Pipeline)
    Nhiệm vụ: Nhả ra tọa độ Entry thô (float) dựa trên các chiến thuật khác nhau,
    sử dụng lõi toán học Nội Suy (Interpolation) để có điểm rơi động.
    """

    @staticmethod
    def interpolate_linear(price_high: float, price_low: float, weight: float) -> float:
        """
        Nội suy tuyến tính tìm điểm Entry.
        - weight = 0.0 -> Trả về giá price_low (Hỗ trợ sâu)
        - weight = 1.0 -> Trả về giá price_high (Hỗ trợ nông/Kháng cự)
        - weight = 0.5 -> Trả về điểm chính giữa
        """
        return price_low + (price_high - price_low) * weight

    @staticmethod
    def get_grid_entries(
        darvas_top: float, 
        darvas_bottom: float, 
        num_grids: int, 
        mode: str = 'LINEAR'
    ) -> Dict[str, List[float]]:
        """
        Nội suy mảng giá Entry cho chiến lược 70/30
        Trả về dict chứa 2 mảng: 'gom_day' (70%) và 'breakout' (30%)
        """
        if num_grids <= 0 or darvas_top <= darvas_bottom:
            return {"gom_day": [], "breakout": []}

        grids_70 = int(num_grids * 0.7)
        grids_30 = num_grids - grids_70
        
        # Điểm chia 70/30 (Ví dụ: mốc 30% từ dưới lên)
        split_price = darvas_bottom + (darvas_top - darvas_bottom) * 0.3
        
        list_entries_gom_day = []
        list_entries_breakout = []

        # 1. Lưới Gom Đáy (70% số lệnh, rải từ split_price xuống darvas_bottom)
        if grids_70 > 0:
            if grids_70 == 1:
                list_entries_gom_day.append(split_price)
            else:
                for i in range(grids_70):
                    weight = 1.0 - (i / (grids_70 - 1)) # Chạy từ 1.0 (split) về 0.0 (bottom)
                    if mode.upper() == 'LINEAR':
                        entry = EntryCalculator.interpolate_linear(split_price, darvas_bottom, weight)
                        list_entries_gom_day.append(entry)
                    elif mode.upper() == 'GEOMETRIC':
                        # Nội suy logarit
                        log_high = math.log(split_price)
                        log_low = math.log(darvas_bottom)
                        log_entry = EntryCalculator.interpolate_linear(log_high, log_low, weight)
                        list_entries_gom_day.append(math.exp(log_entry))

        # 2. Lưới Hứng Breakout (30% số lệnh, rải từ split_price lên darvas_top)
        if grids_30 > 0:
            if grids_30 == 1:
                list_entries_breakout.append(darvas_top)
            else:
                for i in range(grids_30):
                    # Bỏ qua điểm split_price nếu đã trùng với gom đáy, nhưng ở đây
                    # tính weight từ >0.0 tới 1.0 để rải dần lên
                    weight = (i + 1) / grids_30 # Chạy dần lên 1.0 (top)
                    if mode.upper() == 'LINEAR':
                        entry = EntryCalculator.interpolate_linear(darvas_top, split_price, weight)
                        list_entries_breakout.append(entry)
                    elif mode.upper() == 'GEOMETRIC':
                        log_high = math.log(darvas_top)
                        log_low = math.log(split_price)
                        log_entry = EntryCalculator.interpolate_linear(log_high, log_low, weight)
                        list_entries_breakout.append(math.exp(log_entry))

        return {
            "gom_day": sorted(list_entries_gom_day),
            "breakout": sorted(list_entries_breakout)
        }

    @staticmethod
    def get_pullback_entry(upper_support: float, lower_support: float, rsi_1h: float) -> float:
        """
        Tính điểm mua Pullback thông minh bằng trọng số RSI.
        Ví dụ: upper_support = MA25 (Hỗ trợ nông), lower_support = Mép dưới BB (Hỗ trợ sâu).
        """
        # Trộn RSI (0-100) thành trọng số (0.0 -> 1.0). Giả sử RSI hoạt động từ mốc 30 đến 70.
        # RSI càng thấp (bán mạnh) -> weight càng gần 0 -> Mua ở hỗ trợ sâu (lower_support)
        # RSI càng cao (lực mua còn) -> weight càng gần 1 -> Mua ở hỗ trợ nông (upper_support)
        
        weight = max(0.0, min(1.0, (rsi_1h - 30) / (70 - 30))) 
        return EntryCalculator.interpolate_linear(upper_support, lower_support, weight)

    @staticmethod
    def get_breakout_entry(resistance_level: float, buffer_pct: float = 0.005) -> float:
        """
        Chiến lược Momentum Breakout cho Động cơ 3.
        Entry = Đỉnh Kháng Cự + Buffer (Mặc định 0.5%) để né Fakeout.
        """
        return resistance_level * (1 + buffer_pct)
