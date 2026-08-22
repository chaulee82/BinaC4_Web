import ccxt
import time
import pandas as pd
import logging
import math

logger = logging.getLogger("CN4-Executor")

def fmt_price(price: float) -> str:
    if price is None: return "N/A"
    if price == 0: return "0"
    if price >= 1:
        decimals = 4
    else:
        first_sig = -math.floor(math.log10(abs(price)))
        decimals = first_sig + 3
    return f"{price:.{decimals}f}".rstrip('0').rstrip('.')

class HybridExecutor:
    def __init__(self, api_key: str, secret_key: str):
        # Hiện tại mock mode, không cần thực sự init exchange nếu chỉ in ra
        # Nhưng cứ giữ nguyên structure để sau này cắm API vào
        pass

    def execute_trade(self, symbol: str, amount: float, setup: dict):
        """
        Gom logic thực thi vào 1 chỗ (Execution Layer).
        Hiện tại: Chỉ IN RA (Mock Logging) tiến trình đặt lệnh Multi-TP.
        Tương lai: Thay thế print/logger bằng ccxt create_order.
        """
        try:
            entry = setup.get('entry')
            sl = setup.get('stop_loss')
            tp1 = setup.get('tp1')
            tp2 = setup.get('tp2')
            tp_trail = setup.get('tp_trail')

            if not all([entry, sl, tp1]):
                logger.error(f"❌ [EXECUTOR] Thiếu thông số setup cho {symbol}")
                return

            order_type = setup.get('order_type', 'limit').upper()
            
            # Chế độ Mock: In ra ngắn gọn trên 1 dòng
            msg = f"🚀 [MOCK EXECUTE] BUY {order_type} {symbol} @ {fmt_price(entry)} | SL: {fmt_price(sl)} | TP1: {fmt_price(tp1)}"
            if tp2:
                msg += f" | TP2: {fmt_price(tp2)}"
            if tp_trail:
                msg += f" | Trail"
            
            logger.info(msg)

            # (Phần gọi CCXT đã bị comment lại để xử lý sau theo yêu cầu)
            # self.exchange.create_limit_buy_order(...)
            # self.exchange.create_order(ocoOrder=True, ...)

        except Exception as e:
            logger.error(f"❌ Lỗi thực thi Mock Execution: {e}")
