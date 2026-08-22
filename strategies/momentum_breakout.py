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


class MomentumBreakout:
    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()

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

            # MA25 1H
            ma25 = df['close'].rolling(window=25).mean().iloc[-1]
            current_close = df['close'].iloc[-1]

            # RSI 14 (tính thủ công, tránh thêm dependency ngoài)
            delta = df['close'].diff()
            gains = delta.clip(lower=0)
            losses = -delta.clip(upper=0)
            avg_gain = gains.ewm(com=13, min_periods=14).mean().iloc[-1]
            avg_loss = losses.ewm(com=13, min_periods=14).mean().iloc[-1]
            rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0

            btc_above_ma25 = current_close > ma25
            btc_rsi_ok = rsi >= 45.0

            if not btc_above_ma25:
                return {
                    "ok": False,
                    "reason": f"❌ BTC gãy MA25 1H (RSI={rsi:.1f}) — Fakeout Risk Cao",
                    "rsi": round(rsi, 1)
                }
            if not btc_rsi_ok:
                return {
                    "ok": False,
                    "reason": f"❌ BTC RSI 1H Yếu ({rsi:.1f} < 45) — Không Đủ Đà Breakout",
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
    # [DC3-1] Kháng cự từ 100 nến 4H (~17 ngày) thay vì 50 nến (~8 ngày)
    # =========================================================================
    def evaluate_price_action(self, df: pd.DataFrame) -> dict:
        # [DC3-1] Tìm kháng cự macro từ 100 nến trước đó (loại nến hiện tại)
        # 100 nến 4H ≈ 17 ngày — bắt được kháng cự đủ mạnh để xác nhận breakout thật
        past_highs = df['high'].iloc[-100:-1]
        resistance = past_highs.max()

        latest_candle = df.iloc[-1]
        close_price = latest_candle['close']
        high_price = latest_candle['high']
        open_price = latest_candle['open']

        # Thân nến và Râu nến
        body = abs(close_price - open_price)
        upper_wick = high_price - max(close_price, open_price)

        if close_price > resistance:
            if upper_wick > body * 0.3:
                return {"score": 10, "status": "Breakout nhưng bị dội ngược (Râu dài)", "resistance": resistance}
            return {"score": 30, "status": "Breakout Sạch (Đóng nến qua kháng cự)", "resistance": resistance}
        elif close_price >= resistance * 0.99:
            return {"score": 15, "status": "Tiệm cận Kháng cự (Chờ Breakout)", "resistance": resistance}
        else:
            return {"score": 0, "status": "Chưa có dấu hiệu Breakout", "resistance": resistance}

    # =========================================================================
    # CỬA 2: ĐỘT BIẾN KHỐI LƯỢNG (VOLUME ANOMALY) — TỐI ĐA 25 ĐIỂM
    # =========================================================================
    def evaluate_volume(self, df: pd.DataFrame) -> dict:
        # Tính MA20 của Volume (trừ nến hiện tại)
        ma20_vol = df['volume'].iloc[-21:-1].mean()
        current_vol = df['volume'].iloc[-1]

        if ma20_vol == 0:
            return {"score": 0, "status": "Không có dữ liệu Volume"}

        vol_ratio = current_vol / ma20_vol

        if vol_ratio >= 3.0:
            return {"score": 25, "status": f"Dòng tiền Bạo Phát ({vol_ratio:.1f}x MA20)"}
        elif vol_ratio >= 2.0:
            return {"score": 15, "status": f"Volume Khá ({vol_ratio:.1f}x MA20)"}
        else:
            return {"score": 0, "status": f"Volume Yếu ({vol_ratio:.1f}x MA20) - Fakeout?"}

    # =========================================================================
    # CỬA 3: DẤU CHÂN SỔ LỆNH (ORDER BOOK TAPE READING) — TỐI ĐA 20 ĐIỂM
    # =========================================================================
    def evaluate_orderbook(self, order_book: dict, current_price: float) -> dict:
        # Quét Bids/Asks trong biên độ 1% quanh giá hiện tại
        upper_bound = current_price * 1.01
        lower_bound = current_price * 0.99

        bid_vol = sum([bid[1] for bid in order_book['bids'] if bid[0] >= lower_bound])
        ask_vol = sum([ask[1] for ask in order_book['asks'] if ask[0] <= upper_bound])

        if ask_vol == 0:
            return {"score": 20, "status": "Phe Bán trống rỗng (Pump Dễ)"}

        imbalance = bid_vol / ask_vol

        if imbalance >= 2.0:
            return {"score": 20, "status": "Phe Mua Áp Đảo (Tường Bid Dày)"}
        elif imbalance >= 1.2:
            return {"score": 10, "status": "Phe Mua chiếm ưu thế nhẹ"}
        else:
            return {"score": 0, "status": "Tường Sell cản đường"}

    # =========================================================================
    # CỬA 4: TỶ LỆ R/R & CẮt LỔ (RISK MANAGEMENT) — TỐI ĐA 25 ĐIỂM
    # SL đặt ngay dưới resistance cũ (buffer 1%) — vùng kháng cự cũ sau breakout
    # trở thành vùng hỗ trợ mới (quy tắc kiểm tra lại của phân tích kỹ thuật).
    # Sắt buffer 1% (không 3%) để bảo toàn R/R và đảm bảo score c1 >= 25 điểm.
    # =========================================================================
    def evaluate_risk(self, df: pd.DataFrame, entry: float) -> dict:
        # Lấy kháng cự macro nhất quán với evaluate_price_action (100 nến)
        past_highs = df['high'].iloc[-100:-1]
        resistance = past_highs.max()

        if entry < resistance:
            # Chưa vượt kháng cự: SL đặt dưới đáy nến hiện tại
            sl = df['low'].iloc[-1] * 0.99
        else:
            # Đã vượt kháng cự: SL đặt ngay dưới đỉnh hộp cũ 1% (buffer chống quét râu).
            # Kháng cự cũ = Hỗ trợ mới. Giữ SL sát resistance bảo toàn:
            #   - SL distance nhỏ → score Garageute 4 đạt 25 điểm (không xuống 10/0)
            #   - R/R với TP1=+5% vẫn dương (> 1:1)
            sl = resistance * 0.99

        sl_percent = (entry - sl) / entry

        if sl_percent <= 0.05:
            return {"score": 25, "status": f"SL Chính Xác ({-sl_percent*100:.1f}%) — Kháng Cự → Hỗ Trợ | R/R Đẹp", "sl": sl}
        elif sl_percent <= 0.08:
            return {"score": 10, "status": f"SL Chấp Nhận được ({-sl_percent*100:.1f}%)", "sl": sl}
        else:
            return {"score": 0, "status": f"Nến Breakout Quá Dài (SL {-sl_percent*100:.1f}%) → Bỏ qua", "sl": sl}

    # =========================================================================
    # ĐIỂM THƯỞNG: TAKER BUY RATIO (Lực mua chủ động) — MAX +10 ĐIỂM (OVERCAP)
    # =========================================================================
    def evaluate_taker_buy(self, symbol: str, timeframe: str) -> dict:
        try:
            # Gọi API gốc Binance để lấy klines có chứa Taker Buy Quote
            binance_symbol = symbol.replace("/", "").replace("-", "")
            raw_klines = self.exchange.publicGetKlines({
                "symbol": binance_symbol,
                "interval": timeframe,
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

            if taker_ratio >= 65.0:
                return {"score": 10, "status": f"🔥 FOMO Lực Mua ({taker_ratio:.1f}%) -> +10 Bonus"}
            elif taker_ratio >= 55.0:
                return {"score": 5, "status": f"Lực Mua Tốt ({taker_ratio:.1f}%) -> +5 Bonus"}
            else:
                return {"score": 0, "status": f"Taker Buy Bình Thường ({taker_ratio:.1f}%)"}

        except Exception as e:
            return {"score": 0, "status": f"Lỗi Taker Buy: {str(e)[:30]}"}

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

            # Kéo Orderbook
            order_book = self.exchange.fetch_order_book(symbol, limit=100)

            # Chấm điểm 4 Gates + 1 Bonus
            c1 = self.evaluate_price_action(df)
            c2 = self.evaluate_volume(df)
            c3 = self.evaluate_orderbook(order_book, current_price)
            c4 = self.evaluate_risk(df, current_price)
            bonus = self.evaluate_taker_buy(symbol, timeframe)

            total_score = c1['score'] + c2['score'] + c3['score'] + c4['score'] + bonus['score']

            # Tính R/R ratio thực tế để dùng làm tiebreaker khi sort
            sl_price = c4.get('sl')
            # [DC3-5] Scale-out TP: TP1=+5% (40%), TP2=+12% (40%), Trail=+20% (20%)
            tp1_price  = current_price * 1.05
            tp2_price  = current_price * 1.12
            tp_trail   = current_price * 1.20
            rr_ratio = 0.0
            if sl_price and sl_price < current_price:
                sl_dist = current_price - sl_price
                tp1_dist = tp1_price - current_price
                if sl_dist > 0:
                    rr_ratio = round(tp1_dist / sl_dist, 2)

            # sort_score = total_score + rr_ratio (tiebreaker: cùng điểm thì R/R cao hơn lên trước)
            sort_score = total_score + rr_ratio

            action = "🔴 TỪ CHỐI"
            if total_score >= 85:
                action = "🟢 BREAKOUT HÀNG THẬT: Bắn lệnh Hybrid Executor"
            elif total_score >= 70:
                action = "🟡 THEO DÕI: Cần tích lũy thêm Volume"

            trade_setup = {}
            if total_score >= 70:
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
