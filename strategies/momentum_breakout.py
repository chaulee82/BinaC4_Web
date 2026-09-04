"""
Module: momentum_breakout.py
Dự án: CN4-Platform
Mục đích: Động cơ 3 - Săn Bứt Phá Động Lượng (Momentum Breakout)
Thang điểm: 100 điểm (4 Cửa kiểm duyệt định lượng)

Changelog Phase 1:
  - [DC3-1] Kháng cự đo từ 100 nến 4H (~17 ngày) — bắt kháng cự macro thay vì cục bộ 8 ngày
  - [DC3-2] SL buffer 3% (resistance * 0.97) — tránh SL hunt trong vùng retest
  - [DC3-3] BTC 1H Gate độc lập: reject breakout khi BTC RSI < 45 hoặc dưới MA25 1H
  - [DC3-5] trade_setup bổ sung tp1/tp2/tp_trail cho Scale-out Mock Log
"""

import ccxt
import pandas as pd
import numpy as np
from core.indicator_engine import IndicatorEngine


class MomentumBreakout:
    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()
        self.engine   = IndicatorEngine()

    # =========================================================================
    # [MỚI - DC3-3] BTC 1H GATE — Kiểm Tra Sức Khỏe Thị Trường Chung
    # Gọi MỘT LẦN từ main.py rồi truyền kết quả vào evaluate_breakout()
    # Trả về dict: {"ok": bool, "reason": str, "rsi": float}
    # =========================================================================
    def check_btc_trend_1h(self) -> dict:
        """
        Kiểm tra BTC/USDT khung 1H để xác nhận môi trường vĩ mô trước khi bắt breakout.
        Khi altcoin chực chờ phá đỉnh 4H mà BTC 1H đột ngột yếu → tỷ lệ fakeout rất cao.

        REJECT khi:
          - RSI 1H BTC < 45  (đà yếu, dễ kéo altcoin sập theo)
          - Giá BTC đóng nến dưới MA25 1H  (cấu trúc ngắn hạn bị gãy)
        """
        try:
            candles = self.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=50)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # Dùng IndicatorEngine (có cache) — tránh tính lại nếu gọi nhiều lần trong cùng tick
            self.engine.clear_cache()
            ma25          = self.engine.get_ma(df, 25)
            rsi_series    = self.engine.get_rsi(df, 14)
            current_close = float(df['close'].iloc[-1])
            ma25_val      = float(ma25.iloc[-1])
            rsi           = float(rsi_series.iloc[-1])

            btc_above_ma25 = current_close > ma25_val
            btc_rsi_ok     = rsi >= 45.0

            if not btc_above_ma25:
                return {
                    "ok": True,
                    "reason": f"⚠️ CẢNH BÁO: BTC gãy MA25 1H (RSI={rsi:.1f}) — Fakeout Risk Cao",
                    "rsi": round(rsi, 1)
                }
            if not btc_rsi_ok:
                return {
                    "ok": True,
                    "reason": f"⚠️ CẢNH BÁO: BTC RSI 1H Yếu ({rsi:.1f} < 45) — Cẩn Thận Breakout Giả",
                    "rsi": round(rsi, 1)
                }

            return {
                "ok": True,
                "reason": f"✅ BTC 1H Khỏe (RSI={rsi:.1f}, Trên MA25)",
                "rsi": round(rsi, 1)
            }

        except Exception as e:
            # Lỗi API BTC → cho qua để không chặn toàn bộ hệ thống
            return {
                "ok": True,
                "reason": f"⚠️ BTC gate lỗi API (bỏ qua): {str(e)[:60]}",
                "rsi": 50.0
            }

    # =========================================================================
    # CỬA 1: ĐIỂM NỔ CẤU TRÚC GIÁ (PRICE ACTION BREAKOUT) — TỐI ĐA 30 ĐIỂM
    # =========================================================================
    def evaluate_price_action(self, df: pd.DataFrame) -> dict:
        past_highs = df['high'].iloc[-100:-1]
        resistance = past_highs.max()

        latest_candle = df.iloc[-1]
        close_price = latest_candle['close']
        high_price = latest_candle['high']
        open_price = latest_candle['open']

        body = abs(close_price - open_price)
        upper_wick = high_price - max(close_price, open_price)

        if close_price > resistance:
            if upper_wick >= body:
                return {"score": 0, "status": "Fakeout (Râu bị dội ngược)", "resistance": resistance}
            elif upper_wick > body * 0.4:
                return {"score": 15, "status": "Breakout (Có râu nến xả nhẹ)", "resistance": resistance}
            else:
                return {"score": 30, "status": "Breakout Sạch (Đóng nến qua kháng cự)", "resistance": resistance}
        elif high_price > resistance:
            # Giá chọc râu qua nhưng đóng nến dưới kháng cự -> Fakeout
            return {"score": -100, "status": "Fakeout (Chọc râu rút chân)", "resistance": resistance}
        elif close_price >= resistance * 0.99:
            return {"score": 15, "status": "Tiệm cận Kháng cự (Chờ Breakout)", "resistance": resistance}
        else:
            return {"score": 0, "status": "Chưa có dấu hiệu Breakout", "resistance": resistance}

    # =========================================================================
    # CỬA 2: ĐỘT BIẾN KHỐI LƯỢNG (VOLUME ANOMALY) — TỐI ĐA 25 ĐIỂM
    # =========================================================================
    def evaluate_volume(self, df: pd.DataFrame) -> dict:
        ma20_vol = df['volume'].iloc[-21:-1].mean()
        current_vol = df['volume'].iloc[-1]

        if ma20_vol == 0:
            return {"score": 0, "status": "Không có dữ liệu Volume"}

        vol_ratio = current_vol / ma20_vol

        if vol_ratio >= 3.0:
            return {"score": 25, "status": f"Dòng tiền Bạo Phát ({vol_ratio:.1f}x MA20)"}
        elif vol_ratio >= 2.5:
            return {"score": 15, "status": f"Volume Đột Biến ({vol_ratio:.1f}x MA20)"}
        else:
            return {"score": -100, "status": f"Volume Yếu ({vol_ratio:.1f}x MA20) - Bull Trap"}

    # =========================================================================
    # CỬA 3: ĐỘNG NĂNG DUY TRÌ (TAKER BUY RATIO) — TỐI ĐA 20 ĐIỂM
    # =========================================================================
    def evaluate_taker_buy(self, symbol: str) -> dict:
        try:
            binance_symbol = symbol.replace("/", "").replace("-", "")
            raw_klines = self.exchange.publicGetKlines({
                "symbol": binance_symbol,
                "interval": "15m",
                "limit": 1
            })
            if not raw_klines:
                return {"score": 0, "status": "Không có dữ liệu Taker Buy"}

            last_kline = raw_klines[-1]
            quote_vol = float(last_kline[7])
            taker_buy_quote = float(last_kline[10])

            if quote_vol == 0:
                return {"score": 0, "status": "Volume bằng 0"}

            taker_ratio = (taker_buy_quote / quote_vol) * 100

            if taker_ratio >= 60.0:
                return {"score": 20, "status": f"Phe Mua Áp Đảo (Taker Buy {taker_ratio:.1f}%)"}
            else:
                return {"score": -100, "status": f"Lực Mua Yếu (Taker Buy {taker_ratio:.1f}%)"}

        except Exception as e:
            return {"score": 0, "status": f"Lỗi Taker Buy: {str(e)[:30]}"}

    # =========================================================================
    # CỬA 4: TỶ LỆ R/R & CẮT LỖ (RISK MANAGEMENT) — TỐI ĐA 25 ĐIỂM
    # SL đặt ngay dưới nắp hộp Darvas. R/R tối thiểu 1:2.
    # =========================================================================
    def evaluate_risk(self, df: pd.DataFrame, entry: float) -> dict:
        past_highs = df['high'].iloc[-100:-1]
        past_lows = df['low'].iloc[-100:-1]
        resistance = past_highs.max()
        support = past_lows.min()
        box_size = resistance - support

        if entry < resistance:
            sl = df['low'].iloc[-1] * 0.99
        else:
            sl = resistance * 0.995 # Đặt sát dưới nắp hộp

        sl_dist = entry - sl
        tp1_price = entry + box_size
        tp1_dist = tp1_price - entry

        if sl_dist <= 0:
            return {"score": -100, "status": "Lỗi SL", "sl": sl, "rr": 0}

        rr_ratio = tp1_dist / sl_dist

        if rr_ratio >= 2.0:
            return {"score": 25, "status": f"R/R Tối Ưu ({rr_ratio:.1f}) — SL Sát Hộp", "sl": sl, "rr": rr_ratio}
        else:
            return {"score": -100, "status": f"🚫 HỦY SETUP (R/R {rr_ratio:.1f} < 2.0 quá rủi ro)", "sl": sl, "rr": rr_ratio}

    # =========================================================================
    # HÀM CHÍNH: CHẤM ĐIỂM TOÀN DIỆN
    # =========================================================================
    def evaluate_breakout(self, symbol: str, timeframe: str = '1h',
                          btc_gate: dict = None) -> dict:
        """
        btc_gate: Kết quả từ check_btc_trend_1h() — truyền vào để tránh gọi API nhiều lần.
                  Nếu None, tự gọi nội bộ (chỉ dùng khi test đơn lẻ).
        """
        try:
            # [DC3-3] Áp dụng BTC Gate
            if btc_gate is None:
                btc_gate = self.check_btc_trend_1h()

            if not btc_gate.get("ok", True):
                return {
                    "symbol": symbol,
                    "price": 0,
                    "total_score": 0,
                    "sort_score": 0,
                    "rr_ratio": 0,
                    "action": f"🛑 BTC GATE: {btc_gate['reason']}",
                    "details": {
                        "Gate_0_BTC": btc_gate["reason"],
                        "Gate_1_PriceAction": "N/A (BTC Gate đóng)",
                        "Gate_2_Volume": "N/A",
                        "Gate_3_OrderBook": "N/A",
                        "Gate_4_RR": "N/A",
                    },
                    "trade_setup": {},
                    "btc_rsi": btc_gate.get("rsi", 0)
                }

            # Kéo 150 nến (đủ cho resistance 100 nến + buffer)
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=150)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            current_price = df['close'].iloc[-1]

            # Kéo Orderbook (Dự phòng nếu cần, không chấm điểm trực tiếp nữa)
            # order_book = self.exchange.fetch_order_book(symbol, limit=100)

            # Chấm điểm 4 Gates
            c1 = self.evaluate_price_action(df)
            c2 = self.evaluate_volume(df)
            c3 = self.evaluate_taker_buy(symbol)
            c4 = self.evaluate_risk(df, current_price)

            total_score = c1['score'] + c2['score'] + c3['score'] + c4['score']

            # Tính R/R ratio thực tế và Dynamic TP
            sl_price = c4.get('sl')
            rr_ratio = 0.0
            
            if sl_price and sl_price < current_price:
                rr_ratio = c4.get('rr', 0)
                # Tính TP1 theo R/R
                tp1_dist = (current_price - sl_price) * max(rr_ratio, 2.0)
                tp1_price = current_price + tp1_dist
                
                # Cân đối TP1/TP2 theo cấu trúc mới
                tp2_price = current_price + (tp1_dist * 1.5)
                tp_trail  = current_price + (tp1_dist * 1.7)
            else:
                tp1_price = current_price * 1.05
                tp2_price = current_price * 1.10
                tp_trail  = current_price * 1.15
            
            # sort_score = total_score + rr_ratio (tiebreaker: cùng điểm thì R/R cao hơn lên trước)
            sort_score = total_score + rr_ratio

            action = "🔴 TỪ CHỐI"
            if total_score >= 85:
                action = "🟢 BREAKOUT HÀNG THẬT: Bắn lệnh Hybrid Executor"
            elif total_score >= 65:
                action = "🟡 THEO DÕI: Cần tích lũy thêm Volume"

            trade_setup = {}
            is_real_breakout = (c1['score'] >= 25)
            if total_score >= 65 or is_real_breakout:
                trade_setup = {
                    "entry": current_price,
                    "stop_loss": sl_price,
                    "take_profit": tp1_price,   # Tương thích ngược với executor hiện tại
                    "tp1": tp1_price,            # [DC3-5] Scale-out Mock
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
                    "Gate_0_BTC": btc_gate.get("reason", ""),
                    "Gate_1_PriceAction": c1['status'],
                    "Gate_2_Volume": c2['status'],
                    "Gate_3_OrderBook": c3['status'],
                    "Gate_4_RR": c4['status'],
                },
                "trade_setup": trade_setup,
                "btc_rsi": btc_gate.get("rsi", 0)
            }

        except Exception as e:
            return {"symbol": symbol, "error": str(e), "total_score": 0, "sort_score": 0}
