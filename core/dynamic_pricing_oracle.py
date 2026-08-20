"""
Module: dynamic_pricing_oracle.py
Dự án: CN4-Platform
Mục đích: Tính toán Entry, SL, TP động dựa trên VWAP và Biến động thực tế (ATR)
Mục tiêu: Đảm bảo Tỷ lệ khớp lệnh > 60% và R/R mặc định >= 3.0
"""

import pandas as pd
import pandas_ta as ta

class DynamicPricingOracle:
    def __init__(self):
        self.min_rr_ratio = 3.0
        self.atr_multiplier = 1.5 # Mức đệm chống quét râu nến (Stop-Loss Hunt)

    def calculate_optimal_setup(self, df: pd.DataFrame) -> dict:
        """
        Nạp DataFrame chứa nến OHLCV (tối thiểu 100 nến 1H/4H).
        Trả về Dictionary tọa độ giá chuẩn xác.
        """
        try:
            # Đảm bảo DataFrame có DatetimeIndex để tính VWAP
            if not isinstance(df.index, pd.DatetimeIndex):
                if 'timestamp' in df.columns:
                    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('datetime', inplace=True)

            # 1. Tính toán các chỉ số định lượng lõi
            df.ta.vwap(append=True)
            df.ta.ema(length=25, append=True)
            df.ta.atr(length=14, append=True)
            
            # Lấy thông số của cây nến hiện tại (Latest data)
            current_vwap = df['VWAP_D'].iloc[-1]
            current_ema25 = df['EMA_25'].iloc[-1]
            current_atr = df['ATRr_14'].iloc[-1]
            current_close = df['close'].iloc[-1]
            recent_low = df['low'].tail(10).min() # Đáy thấp nhất 10 phiên

            # ==========================================
            # BƯỚC 1: XÁC ĐỊNH VÙNG HẠ CÁNH MỀM (ENTRY)
            # ==========================================
            # Entry là điểm cân bằng giữa trục dòng tiền (VWAP) và trục động lượng (EMA25)
            optimal_entry = current_vwap + ((current_ema25 - current_vwap) / 2)
            
            # Khóa bảo vệ: Không mua giá cao hơn giá Live
            if optimal_entry > current_close:
                optimal_entry = current_close * 0.995 # Mặc định kê dưới giá Live 0.5% để đón râu

            # ==========================================
            # BƯỚC 2: XÂY TƯỜNG BẢO VỆ ATR (STOP-LOSS)
            # ==========================================
            # SL = Giá Mua - (Biên độ dao động thực tế x 1.5)
            atr_stop_loss = optimal_entry - (self.atr_multiplier * current_atr)
            
            # Đối chiếu chéo với Đáy gần nhất. Nếu ATR vẫn cao hơn đáy, đẩy SL xuống dưới đáy để an toàn tuyệt đối.
            final_stop_loss = min(atr_stop_loss, recent_low * 0.99)

            # ==========================================
            # BƯỚC 3: KIẾN TẠO MỤC TIÊU BẤT ĐỐI XỨNG (TAKE-PROFIT)
            # ==========================================
            risk_amount = optimal_entry - final_stop_loss
            target_take_profit = optimal_entry + (self.min_rr_ratio * risk_amount)

            # Đóng gói tọa độ
            setup = {
                "status": "SUCCESS",
                "entry": round(optimal_entry, 5),
                "stop_loss": round(final_stop_loss, 5),
                "take_profit": round(target_take_profit, 5),
                "metrics": {
                    "risk_per_trade_pct": round((risk_amount / optimal_entry) * 100, 2),
                    "expected_rr": self.min_rr_ratio,
                    "atr_value": round(current_atr, 5)
                }
            }
            return setup

        except Exception as e:
            return {"status": "ERROR", "message": f"Lỗi tính toán Oracle: {str(e)}"}

# === TEST KHỐI ĐỊNH GIÁ ===
if __name__ == "__main__":
    pass
