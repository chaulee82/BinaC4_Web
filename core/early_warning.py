import pandas as pd
import pandas_ta as ta

class EarlyWarningMatrix:
    def __init__(self):
        pass

    def check_warning_level(self, df: pd.DataFrame) -> dict:
        """
        Nạp DataFrame chứa nến OHLCV 1H.
        Trả về cấp độ cảnh báo rủi ro (0: An toàn, 1: Theo dõi, 2: Nguy hiểm, 3: Khẩn cấp)
        """
        try:
            # Đảm bảo có DatetimeIndex cho pandas_ta
            if not isinstance(df.index, pd.DatetimeIndex):
                if 'timestamp' in df.columns:
                    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('datetime', inplace=True)

            # Tính toán các chỉ báo
            df.ta.sma(length=7, append=True)
            df.ta.ema(length=7, append=True)
            df.ta.bbands(length=20, std=2, append=True)
            df.ta.sma(close='volume', length=20, append=True)
            df.ta.supertrend(length=10, multiplier=3.0, append=True)

            # Đảm bảo đủ dữ liệu
            if df.empty or len(df) < 20:
                return {"level": 0, "status": "Không đủ dữ liệu"}

            latest = df.iloc[-1]
            close_price = latest['close']
            volume = latest['volume']
            
            sma7_col = [col for col in df.columns if col.startswith('SMA_7')]
            sma7 = latest[sma7_col[0]] if sma7_col else 0
            
            ema7_col = [col for col in df.columns if col.startswith('EMA_7')]
            ema7 = latest[ema7_col[0]] if ema7_col else 0
            
            bbl_col = [col for col in df.columns if col.startswith('BBL_20_')]
            bb_lower = latest[bbl_col[0]] if bbl_col else 0
            
            vol_sma20_col = [col for col in df.columns if col.startswith('SMA_20')]
            vol_sma20 = latest[vol_sma20_col[0]] if vol_sma20_col else 0
            
            # Supertrend column names can vary based on direction, usually it's SUPERT_10_3.0
            supertrend_col = [col for col in df.columns if 'SUPERT_' in col]
            supertrend_val = latest[supertrend_col[0]] if supertrend_col else 0

            # CẤP 3: KHẨN CẤP (Gãy Supertrend)
            # Nếu giá đóng cửa rớt xuống dưới đường Supertrend vĩ mô
            if supertrend_val > 0 and close_price < supertrend_val:
                return {
                    "level": 3,
                    "label": "💀 CẤP 3: KHẨN CẤP (Kích Hoạt Phòng Vệ)",
                    "trigger": "Gãy Nền Hỗ Trợ Supertrend",
                    "action": "Kích hoạt cắt lỗ. Hủy mọi lệnh mua."
                }

            # CẤP 2: NGUY HIỂM (Xuyên thủng BB Dưới kèm Vol xả lớn)
            if close_price < bb_lower and volume > vol_sma20:
                return {
                    "level": 2,
                    "label": "🛑 CẤP 2: NGUY HIỂM (Cảnh Báo Đỏ)",
                    "trigger": "Xuyên thủng Dải Đáy (Bollinger DN) kèm Volume xả",
                    "action": "Hủy ngay lệnh Limit sóng hồi. Cấm bắt dao rơi."
                }

            # CẤP 1: THEO DÕI (Phá vỡ cụm MA ngắn hạn)
            if close_price < sma7 and close_price < ema7:
                return {
                    "level": 1,
                    "label": "⚠️ CẤP 1: THEO DÕI (Vùng Nhạy Cảm)",
                    "trigger": "Giá cắt xuống dưới cụm MA(7) & EMA(7)",
                    "action": "Dừng mua đuổi. Đưa vào tầm ngắm giám sát."
                }

            # CẤP 0: AN TOÀN
            return {
                "level": 0,
                "label": "✅ AN TOÀN",
                "trigger": "Cấu trúc ổn định",
                "action": "Hoạt động bình thường."
            }

        except Exception as e:
            return {"level": 0, "status": f"Lỗi tính toán: {str(e)}"}
