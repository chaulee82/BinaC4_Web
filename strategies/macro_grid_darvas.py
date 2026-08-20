"""
Module: macro_grid_darvas.py
Dự án: CN4-Platform
Mục đích: Động cơ 1 - Quét và chấm điểm cấu trúc Hộp Darvas / Đáy tròn để chạy Bot Spot Grid.
Thang điểm: 100 điểm (4 Cửa kiểm duyệt định lượng)
"""

import ccxt
import pandas as pd
import numpy as np

class MacroGridDarvas:
    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()

    # =========================================================================
    # CỬA 1: ĐỘ CỨNG CỦA SÀN BÊ TÔNG (TỐI ĐA 30 ĐIỂM)
    # =========================================================================
    def evaluate_floor_strength(self, df: pd.DataFrame) -> dict:
        """
        Quét đáy hộp: Tìm vùng giá thấp nhất trong 30-50 phiên qua và đếm số lần test đáy.
        """
        recent_lows = df['low'].tail(24)
        absolute_floor = recent_lows.min()
        
        # Đếm số lần giá nhúng xuống vùng sát đáy (cách đáy 2%) và rút chân
        test_zone = absolute_floor * 1.02
        # Chỉ xét trong 24 nến gần nhất
        recent_df = df.tail(24)
        bounces = recent_df[(recent_df['low'] <= test_zone) & (recent_df['close'] > test_zone)]
        bounce_count = len(bounces)

        if bounce_count >= 2:
            score = 30
            status = f"Sàn bê tông vững chắc ({bounce_count} lần test thành công)"
        elif bounce_count == 1:
            score = 20
            status = "Sàn hộp cơ bản (1 lần test)"
        else:
            score = 0
            status = "Chưa định hình đáy rõ ràng (Rủi ro thủng nền)"

        return {"score": score, "floor_price": absolute_floor, "status": status}

    # =========================================================================
    # CỬA 2: BIÊN ĐỘ ĐẬP NHẢ TỐI ƯU (TỐI ĐA 25 ĐIỂM)
    # =========================================================================
    def evaluate_amplitude(self, df: pd.DataFrame, floor_price: float) -> dict:
        """
        Tính toán khoảng cách từ Sàn lên Trần. Mức tối ưu cho Grid là 15% - 30%.
        """
        # Xác định trần hộp dựa trên mức giá đóng cửa cao nhất trong 24 phiên qua (loại trừ râu nến đột biến)
        ceiling_price = df['close'].tail(24).max()
        
        amplitude = (ceiling_price - floor_price) / floor_price

        # Kiểm tra sức sống (volatility) trong 24h gần nhất (6 nến 4H)
        recent_24h = df.tail(6)
        vola_24h = (recent_24h['high'].max() - recent_24h['low'].min()) / recent_24h['low'].min()

        if vola_24h < 0.08:
            score = 0
            status = f"Sóng 24h quá yếu ({vola_24h*100:.1f}%). Khước từ."
        elif 0.15 <= amplitude <= 0.30:
            score = 25
            status = f"Biên độ Vàng cho Grid ({amplitude*100:.1f}%)"
        elif (0.10 <= amplitude < 0.15) or (0.30 < amplitude <= 0.45):
            score = 15
            status = f"Biên độ khả thi ({amplitude*100:.1f}%)"
        else:
            score = 0
            status = f"Biên độ dị thường ({amplitude*100:.1f}%). Khước từ."

        return {"score": score, "ceiling_price": ceiling_price, "amplitude": amplitude, "status": status}

    # =========================================================================
    # CỬA 3: VỊ THẾ GIÁ VỐN HIỆN TẠI (TỐI ĐA 25 ĐIỂM)
    # =========================================================================
    def evaluate_current_position(self, current_price: float, floor: float, ceiling: float) -> dict:
        """
        Xác định giá Live đang nằm ở phần nào của chiếc hộp.
        """
        if ceiling <= floor:
            return {"score": 0, "status": "Lỗi logic cấu trúc hộp"}

        # Tính vị trí tương đối (0.0 là đáy, 1.0 là đỉnh)
        position_ratio = (current_price - floor) / (ceiling - floor)

        if position_ratio <= 0.45:
            score = 25
            status = "Giá nằm ở nửa dưới hộp (Vị thế mua an toàn)"
        elif position_ratio <= 0.55:
            score = 10
            status = "Giá lơ lửng giữa tâm hộp (Cân bằng)"
        else:
            score = 0
            status = "Giá đang áp sát nắp hộp (Rủi ro kẹt hàng đỉnh)"

        return {"score": score, "position_ratio": position_ratio, "status": status}

    # =========================================================================
    # CỬA 4: TƯỜNG ĐỠ SỔ LỆNH KÉP (TỐI ĐA 20 ĐIỂM)
    # =========================================================================
    def analyze_grid_order_book(self, order_book: dict, floor: float, ceiling: float) -> dict:
        """
        Quét sự hiện diện của Market Maker chặn 2 đầu.
        """
        try:
            bids = pd.DataFrame(order_book['bids'], columns=['price', 'volume'])
            asks = pd.DataFrame(order_book['asks'], columns=['price', 'volume'])

            # Tính khối lượng tiền đỡ ở Sàn (+3%) và chặn ở Trần (-3%)
            bid_wall_total = (bids[bids['price'] >= floor * 0.97]['price'] * bids['volume']).sum()
            ask_wall_total = (asks[asks['price'] <= ceiling * 1.03]['price'] * asks['volume']).sum()
            
            # Tính trung bình thanh khoản quanh giá Live để làm mốc so sánh
            avg_liquidity = (bid_wall_total + ask_wall_total) / 2

            has_bid_wall = bid_wall_total > (avg_liquidity * 1.2)
            has_ask_wall = ask_wall_total > (avg_liquidity * 1.2)

            if has_bid_wall and has_ask_wall:
                score = 20
                status = "Cấu trúc chặn 2 đầu hoàn hảo (Bị nhốt trong hộp)"
            elif has_bid_wall:
                score = 10
                status = "Có bệ đỡ Sàn, nhưng nắp hộp trống rỗng"
            else:
                score = 0
                status = "Thanh khoản mỏng, thiếu vắng nhà tạo lập"

            return {"score": score, "status": status}
        except Exception as e:
            return {"score": 0, "status": f"Lỗi truy xuất Order Book: {str(e)}"}

    # =========================================================================
    # ĐIỀU PHỐI VÀ CHẤM ĐIỂM TỔNG HỢP
    # =========================================================================
    def scan_grid_candidate(self, symbol: str, timeframe: str = '4h') -> dict:
        try:
            # 1. Kéo dữ liệu
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=150)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            current_price = df['close'].iloc[-1]
            order_book = self.exchange.fetch_order_book(symbol, limit=100)

            # 2. Xử lý các cửa logic
            c1 = self.evaluate_floor_strength(df)
            floor = c1.get('floor_price', 0)
            
            c2 = self.evaluate_amplitude(df, floor)
            ceiling = c2.get('ceiling_price', 0)
            
            c3 = self.evaluate_current_position(current_price, floor, ceiling)
            c4 = self.analyze_grid_order_book(order_book, floor, ceiling)

            total_score = c1['score'] + c2['score'] + c3['score'] + c4['score']
            
            # 3. Phân loại thực thi
            if total_score >= 80:
                action = "🟢 LƯỚI TỐI ƯU: Truyền tín hiệu cho Grid Manager"
            elif total_score >= 60:
                action = "🟡 THEO DÕI: Cần giá hồi về sát sàn hộp hơn"
            else:
                action = "🔴 TỪ CHỐI: Cấu trúc tích lũy bị vỡ hoặc nhiễu"

            # 4. Tính toán thông số Bot Grid (Nếu đủ điều kiện)
            grid_setup = {}
            if total_score >= 60:
                grid_setup = {
                    "lower_price": round(floor * 0.99, 5),     # Đặt sàn lưới dưới đáy thực tế 1%
                    "upper_price": round(ceiling * 0.99, 5),   # Đặt trần lưới ngay sát dưới đỉnh cũ
                    "stop_loss": round(floor * 0.95, 5),       # Cắt lỗ cứng khi thủng sàn 5%
                    "grid_quantity": int((c2['amplitude'] * 100) / 0.8) # Cấu hình mỗi lưới ăn khoảng ~0.8%
                }

            return {
                "symbol": symbol,
                "price": current_price,
                "total_score": total_score,
                "action": action,
                "details": {
                    "Gate_1_Floor": c1['status'],
                    "Gate_2_Amplitude": c2['status'],
                    "Gate_3_Position": c3['status'],
                    "Gate_4_OrderBook": c4['status']
                },
                "grid_setup": grid_setup
            }

        except Exception as e:
            return {"symbol": symbol, "error": str(e), "total_score": 0}
