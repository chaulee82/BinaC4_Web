import ccxt
import pandas as pd
import numpy as np

class HotTrendPullback:
    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()

    def _calculate_indicators(self, df: pd.DataFrame):
        # Calculate MA20, MA50
        df['MA20'] = df['close'].rolling(window=20).mean()
        df['MA50'] = df['close'].rolling(window=50).mean()
        
        # Calculate EMA7, EMA20
        df['EMA7'] = df['close'].ewm(span=7, adjust=False).mean()
        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        
        # Calculate RSI 14
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=13, min_periods=14).mean()
        avg_loss = loss.ewm(com=13, min_periods=14).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # Calculate ATR 14
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['ATR'] = true_range.rolling(14).mean()
        
        return df

    def evaluate_trend(self, df: pd.DataFrame) -> dict:
        """
        Cửa 1: Cấu trúc bứt phá (Breakout Structure)
        Giá hiện tại phải > MA20 và MA50. RSI > 65.
        """
        latest = df.iloc[-1]
        
        if pd.isna(latest['MA50']) or pd.isna(latest['RSI']):
            return {"score": 0, "status": "Thiếu dữ liệu", "ok": False}
            
        if latest['close'] > latest['MA20'] and latest['close'] > latest['MA50'] and latest['RSI'] > 65:
            return {"score": 20, "status": f"🟢 Khỏe (RSI: {latest['RSI']:.1f})", "ok": True}
        else:
            return {"score": 0, "status": f"🟡 Yếu (RSI: {latest['RSI']:.1f})", "ok": False}

    def evaluate_volume(self, df: pd.DataFrame) -> dict:
        """
        Cửa 2: Đột biến khối lượng (Volume Spikes)
        Khối lượng giao dịch đột biến gấp 2-3 lần trung bình 20 nến trước đó.
        """
        ma20_vol = df['volume'].iloc[-21:-1].mean()
        current_vol = df['volume'].iloc[-1]
        
        if ma20_vol == 0:
            return {"score": 0, "status": "Vol = 0", "ok": False}
            
        vol_ratio = current_vol / ma20_vol
        
        if vol_ratio >= 3.0:
            return {"score": 20, "status": f"Đột biến ({vol_ratio:.1f}x)", "ok": True}
        elif vol_ratio >= 2.0:
            return {"score": 10, "status": f"Vol Khá ({vol_ratio:.1f}x)", "ok": True}
        else:
            return {"score": 0, "status": f"Vol Yếu ({vol_ratio:.1f}x)", "ok": False}

    def evaluate_taker_buy(self, symbol: str, timeframe: str) -> dict:
        """
        Cửa 3: Mua chủ động áp đảo (Taker Buy Volume)
        """
        try:
            binance_symbol = symbol.replace("/", "").replace("-", "")
            raw_klines = self.exchange.publicGetKlines({
                "symbol": binance_symbol,
                "interval": timeframe,
                "limit": 1
            })
            if not raw_klines:
                return {"score": 0, "status": "No Data"}

            last_kline = raw_klines[-1]
            quote_vol = float(last_kline[7])
            taker_buy_quote = float(last_kline[10])

            if quote_vol == 0:
                return {"score": 0, "status": "Vol = 0"}

            taker_ratio = (taker_buy_quote / quote_vol) * 100

            if taker_ratio >= 65.0:
                return {"score": 20, "status": f"Taker Áp Đảo ({taker_ratio:.1f}%)"}
            elif taker_ratio >= 55.0:
                return {"score": 10, "status": f"Taker Tốt ({taker_ratio:.1f}%)"}
            else:
                return {"score": 0, "status": f"Taker Yếu ({taker_ratio:.1f}%)"}
        except Exception as e:
            return {"score": 0, "status": f"Lỗi: {str(e)[:15]}"}

    def evaluate_entry(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Cửa 4: Điểm Mua Pullback
        Giá chạm EMA7/EMA20 + Inside Bar/Pinbar.
        Bao gồm Check Khung 15M: Soi vi mạch 10 nến gần nhất xem có bị xả gãy nền không.
        """
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        touched_ema = (latest['low'] <= latest['EMA7'] and latest['high'] >= latest['EMA7']) or \
                      (latest['low'] <= latest['EMA20'] and latest['high'] >= latest['EMA20']) or \
                      (prev['low'] <= prev['EMA7'] and prev['high'] >= prev['EMA7']) or \
                      (prev['low'] <= prev['EMA20'] and prev['high'] >= prev['EMA20'])
                      
        if not touched_ema:
            return {"score": 0, "status": "Chưa chạm EMA", "ok": False}
            
        body_size = abs(latest['close'] - latest['open'])
        full_size = latest['high'] - latest['low']
        is_red = latest['close'] < latest['open']
        is_solid_red = is_red and (body_size / full_size > 0.8 if full_size > 0 else False)
        
        if is_solid_red:
            return {"score": 0, "status": "🚫 Bỏ qua (Nến xả đặc)", "ok": False}
            
        # ---- 15M STABILITY CHECK (10 nến ~ 2.5h) ----
        try:
            m15_candles = self.exchange.fetch_ohlcv(symbol, '15m', limit=10)
            m15_df = pd.DataFrame(m15_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            selling_pressure = False
            for _, row in m15_df.iterrows():
                b_size = abs(row['close'] - row['open'])
                f_size = row['high'] - row['low']
                r_red = row['close'] < row['open']
                # Xả mạnh: Nến đỏ đặc chiếm > 80% và biên độ giảm > 0.4%
                if r_red and f_size > 0 and (b_size / f_size) > 0.8 and b_size > (row['open'] * 0.004):
                    selling_pressure = True
                    break
                
                # Râu trên quá dài
                upper_wick = row['high'] - max(row['close'], row['open'])
                if upper_wick > b_size * 2 and upper_wick > (row['close'] * 0.004):
                    selling_pressure = True
                    break
                    
            if selling_pressure:
                return {"score": 0, "status": "🚫 Gãy nền 15M (Bị Xả)", "ok": False}
        except Exception as e:
            pass # Bỏ qua nếu lỗi API 15M
        # ---------------------------------------------
            
        is_inside_bar = latest['high'] <= prev['high'] and latest['low'] >= prev['low']
        lower_wick = min(latest['close'], latest['open']) - latest['low']
        is_pinbar = lower_wick > body_size * 2
        
        if is_inside_bar and is_pinbar:
            return {"score": 20, "status": "✅ Đẹp (15M Ổn định)", "ok": True}
        elif is_inside_bar:
            return {"score": 10, "status": "✅ Inside Bar (15M Ổn)", "ok": True}
        elif is_pinbar:
            return {"score": 10, "status": "✅ Pinbar (15M Ổn)", "ok": True}
            
        return {"score": 5, "status": "Chạm EMA (thiếu nến đảo)", "ok": False}

    def evaluate_risk(self, df: pd.DataFrame, entry_price: float) -> dict:
        """
        Cửa 5: Quản trị Rủi Ro (ATR)
        """
        latest = df.iloc[-1]
        atr = latest['ATR']
        
        if pd.isna(atr):
            return {"score": 0, "status": "Lỗi ATR", "sl": 0}
            
        sl = latest['low'] - (0.5 * atr)
        sl_pct = (entry_price - sl) / entry_price
        
        if sl_pct > 0.1: 
            return {"score": 0, "status": f"SL quá xa (-{sl_pct*100:.1f}%)", "sl": sl}
            
        return {"score": 20, "status": f"🛡️ SL An Toàn", "sl": sl}

    def evaluate_pullback(self, symbol: str, timeframe: str = '4h') -> dict:
        try:
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=100)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = self._calculate_indicators(df)
            
            current_price = df['close'].iloc[-1]
            
            c1 = self.evaluate_trend(df)
            c2 = self.evaluate_volume(df)
            c3 = self.evaluate_taker_buy(symbol, timeframe)
            c4 = self.evaluate_entry(symbol, df)
            c5 = self.evaluate_risk(df, current_price)
            
            sl_price = c5.get('sl', current_price * 0.95)
            sl_dist = current_price - sl_price
            
            # Tính TP Động theo Swing High (Đỉnh cũ 30 nến gần nhất)
            swing_high = df['high'].iloc[-30:-1].max()
            
            # Khống chế TP tối đa để tránh các râu nến ảo hoặc đỉnh quá xa làm sai lệch R/R
            max_tp_price = current_price + (sl_dist * 3) # Cap R/R tối đa là 1:3
            
            if swing_high > current_price * 1.01:
                tp1_price = min(swing_high, max_tp_price)
            else:
                tp1_price = current_price + (sl_dist * 2)
                
            tp_dist = tp1_price - current_price
            rr_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
            
            # Sàng lọc R/R tàn khốc
            if rr_ratio < 1.0 and c5['score'] > 0:
                c5['status'] = f"🚫 RR Thực Tế Thấp ({rr_ratio:.1f})"
                c5['score'] = 0
            
            rr_bonus = 10 if rr_ratio >= 2.5 else 0
            total_score = c1['score'] + c2['score'] + c3['score'] + c4['score'] + c5['score'] + rr_bonus
            
            tp2_price = current_price + (tp_dist * 1.5)
            tp_trail = current_price + (tp_dist * 2)
            
            sort_score = total_score + (rr_ratio * 2)
            
            action = "🟡 TỪ CHỐI (Smart Money Yếu)"
            if not c1['ok']:
                 action = "⚠️ HỦY SETUP (Mất Xu Hướng)"
            elif total_score >= 80 and c4['ok']:
                 action = "🚀 VÀO LỆNH PULLBACK"
            elif c1['ok']:
                 action = "⏳ CHỜ PULLBACK/DÒNG TIỀN"
                 
            trade_setup = {
                "entry": current_price,
                "stop_loss": sl_price,
                "take_profit": tp1_price,
                "tp1": tp1_price,
                "tp2": tp2_price,
                "tp_trail": tp_trail,
            }
                
            return {
                "symbol": symbol,
                "price": current_price,
                "total_score": total_score,
                "rr_ratio": rr_ratio,
                "sort_score": sort_score,
                "action": action,
                "details": {
                    "Gate_1_Trend": c1['status'],
                    "Gate_2_Volume": c2['status'],
                    "Gate_3_Taker": c3['status'],
                    "Gate_4_Entry": c4['status'],
                    "Gate_5_Risk": c5['status'],
                },
                "trade_setup": trade_setup
            }
            
        except Exception as e:
            return {"symbol": symbol, "error": str(e), "total_score": 0, "sort_score": 0}
