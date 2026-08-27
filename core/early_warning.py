import pandas as pd
import pandas_ta as ta

class EarlyWarningMatrix:
    def __init__(self):
        pass

    def check_warning_level(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> dict:
        """
        Nạp DataFrame chứa nến OHLCV 1H, 4H, 1D.
        Trả về cấp độ cảnh báo rủi ro (0: An toàn, 1: Theo dõi, 2: Nguy hiểm, 3: Khẩn cấp)
        """
        try:
            # Helper to preprocess dataframe
            def prep_df(df, length_sma=7, length_ema=7, length_bb=20, length_st=10, st_mult=3.0):
                if not isinstance(df.index, pd.DatetimeIndex):
                    if 'timestamp' in df.columns:
                        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('datetime', inplace=True)
                
                df.ta.sma(length=length_sma, append=True)
                df.ta.ema(length=length_ema, append=True)
                df.ta.bbands(length=length_bb, std=2, append=True)
                df.ta.sma(close='volume', length=length_bb, append=True)
                df.ta.supertrend(length=length_st, multiplier=st_mult, append=True)
                return df
                
            df_1h = prep_df(df_1h.copy())
            df_4h = prep_df(df_4h.copy())
            df_1d = prep_df(df_1d.copy())
            
            if df_1d.empty or len(df_1d) < 20 or df_4h.empty or len(df_4h) < 20 or df_1h.empty or len(df_1h) < 20:
                return {"level": 0, "status": "Không đủ dữ liệu"}

            latest_1d = df_1d.iloc[-1]
            latest_4h = df_4h.iloc[-1]
            latest_1h = df_1h.iloc[-1]
            
            # CẤP 3: KHẨN CẤP (Gãy Trend 1D) - Bỏ qua ngay
            supertrend_col_1d = [col for col in df_1d.columns if 'SUPERT_' in col]
            st_1d = latest_1d[supertrend_col_1d[0]] if supertrend_col_1d else 0
            if st_1d > 0 and latest_1d['close'] < st_1d:
                return {
                    "level": 3,
                    "label": "💀 CẤP 3: KHẨN CẤP (Gãy Trend 1D)",
                    "trigger": "Giá thủng Supertrend mốc 1D",
                    "action": "Kích hoạt cắt lỗ. Hủy mọi lệnh mua."
                }
                
            # CẤP 2: NGUY HIỂM (Thủng BB Dưới 4H kèm Vol xả) - Cần xem xét kỹ
            bbl_col_4h = [col for col in df_4h.columns if col.startswith('BBL_20_')]
            bb_lower_4h = latest_4h[bbl_col_4h[0]] if bbl_col_4h else 0
            vol_sma20_col_4h = [col for col in df_4h.columns if col.startswith('SMA_20')]
            vol_sma20_4h = latest_4h[vol_sma20_col_4h[0]] if vol_sma20_col_4h else 0
            if latest_4h['close'] < bb_lower_4h and latest_4h['volume'] > vol_sma20_4h:
                return {
                    "level": 2,
                    "label": "🛑 CẤP 2: NGUY HIỂM (Cảnh Báo 4H)",
                    "trigger": "Xuyên thủng Đáy BB(20) 4H kèm Volume xả",
                    "action": "Hủy ngay lệnh Limit sóng hồi. Cấm bắt dao rơi."
                }
                
            # CẤP 1: THEO DÕI (Gãy MA ngắn hạn 1H) - Cảnh giác
            sma7_col_1h = [col for col in df_1h.columns if col.startswith('SMA_7')]
            sma7_1h = latest_1h[sma7_col_1h[0]] if sma7_col_1h else 0
            ema7_col_1h = [col for col in df_1h.columns if col.startswith('EMA_7')]
            ema7_1h = latest_1h[ema7_col_1h[0]] if ema7_col_1h else 0
            
            if latest_1h['close'] < sma7_1h and latest_1h['close'] < ema7_1h:
                return {
                    "level": 1,
                    "label": "⚠️ CẤP 1: THEO DÕI (Vùng Nhạy Cảm 1H)",
                    "trigger": "Giá cắt xuống dưới cụm MA(7) & EMA(7) 1H",
                    "action": "Dừng mua đuổi. Đưa vào tầm ngắm giám sát."
                }
                
            # CẤP 0: AN TOÀN
            return {
                "level": 0,
                "label": "✅ AN TOÀN",
                "trigger": "Cấu trúc ổn định đa khung",
                "action": "Hoạt động bình thường."
            }

        except Exception as e:
            return {"level": 0, "status": f"Lỗi tính toán EW: {str(e)}"}
