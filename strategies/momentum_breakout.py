import ccxt
import pandas as pd
import numpy as np

class MomentumBreakout:
    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()

    # =========================================================================
    # CỬA 1: ĐIỂM NỔ CẤU TRÚC GIÁ (PRICE ACTION BREAKOUT) - TỐI ĐA 30 ĐIỂM
    # =========================================================================
    def evaluate_price_action(self, df: pd.DataFrame) -> dict:
        # Tìm kháng cự vĩ mô (Trần hộp) từ 50 nến trước đó (không tính nến hiện tại)
        past_highs = df['high'].iloc[-50:-1]
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
                return {"score": 10, "status": "Breakout nhưng bị dội ngược (Râu dài)"}
            return {"score": 30, "status": "Breakout Sạch (Đóng nến qua kháng cự)"}
        elif close_price >= resistance * 0.99:
            return {"score": 15, "status": "Tiệm cận Kháng cự (Chờ Breakout)"}
        else:
            return {"score": 0, "status": "Chưa có dấu hiệu Breakout"}

    # =========================================================================
    # CỬA 2: ĐỘT BIẾN KHỐI LƯỢNG (VOLUME ANOMALY) - TỐI ĐA 25 ĐIỂM
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
    # CỬA 3: DẤU CHÂN SỔ LỆNH (ORDER BOOK TAPE READING) - TỐI ĐA 20 ĐIỂM
    # =========================================================================
    def evaluate_orderbook(self, order_book: dict, current_price: float) -> dict:
        # Quét Bids/Asks trong biên độ 1% quanh giá hiện tại
        upper_bound = current_price * 1.01
        lower_bound = current_price * 0.99
        
        bid_vol = sum([bid[1] for bid in order_book['bids'] if bid[0] >= lower_bound])
        ask_vol = sum([ask[1] for ask in order_book['asks'] if ask[0] <= upper_bound])
        
        if ask_vol == 0:
            return {"score": 20, "status": "Phe Bán trống rỗng (Pumb Dễ)"}
            
        imbalance = bid_vol / ask_vol
        
        if imbalance >= 2.0:
            return {"score": 20, "status": "Phe Mua Áp Đảo (Tường Bid Dày)"}
        elif imbalance >= 1.2:
            return {"score": 10, "status": "Phe Mua chiếm ưu thế nhẹ"}
        else:
            return {"score": 0, "status": "Tường Sell cản đường"}

    # =========================================================================
    # CỬA 4: TỶ LỆ R/R & CẮT LỖ (RISK MANAGEMENT) - TỐI ĐA 25 ĐIỂM
    # =========================================================================
    def evaluate_risk(self, df: pd.DataFrame, entry: float) -> dict:
        # SL đặt dưới Hỗ trợ mới (Trần hộp cũ)
        past_highs = df['high'].iloc[-50:-1]
        resistance = past_highs.max()
        
        # Nếu chưa vượt kháng cự, SL đặt dưới đáy nến hiện tại
        if entry < resistance:
            sl = df['low'].iloc[-1] * 0.99
        else:
            # Nếu đã vượt kháng cự, SL đặt dưới đỉnh hộp cũ 1 chút (tránh quét râu)
            sl = resistance * 0.99
            
        sl_percent = (entry - sl) / entry
        
        if sl_percent <= 0.05:
            # Mục tiêu TP phần 1 (Scale-out 50%) là +8%
            return {"score": 25, "status": f"SL Cực Ngắn ({-sl_percent*100:.1f}%) - R/R Đẹp", "sl": sl}
        elif sl_percent <= 0.08:
            return {"score": 10, "status": f"SL Rủi Ro ({-sl_percent*100:.1f}%)", "sl": sl}
        else:
            return {"score": 0, "status": f"Nến Breakout Quá Dài (SL {-sl_percent*100:.1f}%) -> Bỏ qua", "sl": sl}

    # =========================================================================
    # HÀM CHÍNH: CHẤM ĐIỂM TOÀN DIỆN
    # =========================================================================
    def evaluate_breakout(self, symbol: str, timeframe: str = '1h') -> dict:
        try:
            # Kéo 100 nến
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=100)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            current_price = df['close'].iloc[-1]
            
            # Kéo Orderbook
            order_book = self.exchange.fetch_order_book(symbol, limit=100)
            
            # Chấm điểm 4 Gates
            c1 = self.evaluate_price_action(df)
            c2 = self.evaluate_volume(df)
            c3 = self.evaluate_orderbook(order_book, current_price)
            c4 = self.evaluate_risk(df, current_price)
            
            total_score = c1['score'] + c2['score'] + c3['score'] + c4['score']
            
            action = "🔴 TỪ CHỐI"
            if total_score >= 85:
                action = "🟢 BREAKOUT HÀNG THẬT: Bắn lệnh Hybrid Executor"
            elif total_score >= 70:
                action = "🟡 THEO DÕI: Cần tích lũy thêm Volume"
                
            trade_setup = {}
            if total_score >= 70:
                trade_setup = {
                    "entry": current_price,
                    "stop_loss": c4.get('sl'),
                    "take_profit": current_price * 1.08  # TP1 là +8% theo thiết kế
                }

            return {
                "symbol": symbol,
                "price": current_price,
                "total_score": total_score,
                "action": action,
                "details": {
                    "Gate_1_PriceAction": c1['status'],
                    "Gate_2_Volume": c2['status'],
                    "Gate_3_OrderBook": c3['status'],
                    "Gate_4_RR": c4['status']
                },
                "trade_setup": trade_setup
            }
            
        except Exception as e:
            return {"symbol": symbol, "error": str(e), "total_score": 0}
