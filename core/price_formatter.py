import math
import decimal
import logging
import ccxt
from typing import Optional

logger = logging.getLogger("PriceFormatter")


def round_price(price: Optional[float]) -> float:
    """Làm tròn giá thông minh theo số chữ số có nghĩa (hỗ trợ cả meme coin siêu nhỏ):
      >= 1000 USDT : 2 chữ số thập phân (BTC, ETH...)
      >= 1    USDT : 4 chữ số thập phân (SOL, BNB...)
      < 1     USDT : first_significant_digit + 4 (tối đa 10 chữ số, ví dụ PEPE, SHIB...)
    """
    if price is None or price <= 0:
        return price or 0.0
    try:
        if price >= 1000:
            return round(price, 2)
        if price >= 1:
            return round(price, 4)
        first_sig = -math.floor(math.log10(abs(price)))
        decimals = min(max(first_sig + 4, 8), 10)
        return round(price, decimals)
    except Exception:
        return round(price, 8)


class PriceFormatter:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(PriceFormatter, cls).__new__(cls)
            cls._instance._markets = {}
        return cls._instance

    def load_markets(self, exchange: ccxt.Exchange):
        """Tải và cache markets từ ccxt để lấy tickSize cục bộ"""
        if not self._markets:
            try:
                # Load markets if not already loaded
                if not exchange.markets:
                    exchange.load_markets()
                self._markets = exchange.markets
                logger.info(f"✅ [PriceFormatter] Đã cache markets ({len(self._markets)} symbols) từ CCXT")
            except Exception as e:
                logger.error(f"❌ [PriceFormatter] Lỗi khi load markets: {e}")

    def get_tick_size(self, symbol: str) -> Optional[float]:
        if not self._markets or symbol not in self._markets:
            return None
        
        try:
            return self._markets[symbol]['precision']['price']
        except KeyError:
            return None

    def format_price(self, symbol: str, raw_price: float) -> float:
        """Cắt gọt số thập phân chuẩn xác tuyệt đối theo quy định Tick Size"""
        if raw_price is None or raw_price <= 0:
            return raw_price
            
        tick_size = self.get_tick_size(symbol)
        if tick_size is None:
            # Fallback nếu không có tick_size
            return raw_price

        try:
            dec_price = decimal.Decimal(str(raw_price))
            
            if isinstance(tick_size, int) and tick_size >= 1:
                dec_tick = decimal.Decimal('1e-' + str(tick_size))
            else:
                dec_tick = decimal.Decimal(str(tick_size))
            
            # Làm tròn xuống để tránh vi phạm giới hạn số dư/giá
            formatted_price = dec_price.quantize(dec_tick, rounding=decimal.ROUND_DOWN)
            return float(formatted_price)
        except Exception as e:
            logger.error(f"❌ [PriceFormatter] Lỗi format_price cho {symbol}: {e}")
            return raw_price

# Global instance
price_formatter = PriceFormatter()
