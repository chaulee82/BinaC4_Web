import ccxt
import time
import pandas as pd

class HybridExecutor:
    def __init__(self, api_key: str, secret_key: str):
        from core.exchange_factory import get_working_exchange
        self.exchange = get_working_exchange(api_key=api_key, secret_key=secret_key)

    def execute_trade(self, symbol: str, entry: float, sl: float, tp: float, amount: float):
        try:
            print(f"🚀 Treo bẫy Buy Limit {symbol} tại {entry}")
            self.exchange.create_limit_buy_order(symbol, amount, entry)
            
            # Lưu ý: Cần websocket để xác nhận lệnh Buy đã khớp hoàn toàn trước khi chạy tiếp
            
            half_amt = amount / 2
            
            # PHẦN 1: BẢO TOÀN VỐN (50% OCO)
            self.exchange.create_order(
                symbol=symbol, type='limit', side='sell', amount=half_amt, price=tp,
                params={'ocoOrder': True, 'stopPrice': sl, 'stopLimitPrice': sl}
            )
            print(f"🛡️ Lập khiên OCO bảo toàn 50% vốn tại TP: {tp}")

            # PHẦN 2: BẮT ĐÁY BREAKOUT (50% Trailing Stop)
            self._trailing_stop_loop(symbol, half_amt, sl)

        except Exception as e:
            print(f"❌ Lỗi thực thi: {e}")

    def _trailing_stop_loop(self, symbol: str, amount: float, current_sl: float):
        print(f"🌪️ Khởi động Trailing Stop. SL gốc: {current_sl}")
        while True:
            try:
                candles = self.exchange.fetch_ohlcv(symbol, '15m', limit=15)
                df = pd.DataFrame(candles, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
                
                ma7 = df['c'].rolling(window=7).mean().iloc[-1]
                latest_price = df['c'].iloc[-1]
                
                dynamic_sl = ma7 * 0.995 # Kéo SL bám sát dưới MA7 0.5%
                
                if dynamic_sl > current_sl:
                    current_sl = dynamic_sl
                    print(f"📈 Dời SL tự động lên: {current_sl:.6f}")
                
                if latest_price < current_sl:
                    print(f"🚨 Gãy cấu trúc đẩy. Xả Market chốt lời!")
                    self.exchange.create_market_sell_order(symbol, amount)
                    break
                    
                time.sleep(60) # Tần suất đo lường: 1 phút/lần
            except Exception as e:
                time.sleep(60)
