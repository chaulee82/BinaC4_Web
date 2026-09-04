import time
import logging
from typing import Dict, Any, Tuple
from core.market_data_repo import MarketDataRepository

logger = logging.getLogger("CacheService")

class CacheService:
    _instance = None

    def __new__(cls, repo: MarketDataRepository = None):
        if cls._instance is None:
            cls._instance = super(CacheService, cls).__new__(cls)
            cls._instance._initialize(repo)
        return cls._instance

    def _initialize(self, repo: MarketDataRepository):
        self.repo = repo if repo else MarketDataRepository()
        self._live_data_map: Dict[str, Any] = {}
        self._live_data_updated_at: float = 0.0
        self.ttl_seconds = 300  # Default TTL: 5 minutes

    def set_ttl(self, seconds: int):
        self.ttl_seconds = seconds

    def _refresh_live_data(self):
        """
        Lấy mới dữ liệu Ticker 24h từ Repository và parse vào dict theo định dạng cũ (tương thích ngược).
        """
        logger.info("[CacheService] Đang cập nhật live_data_map từ Binance...")
        tickers = self.repo.get_ticker_24h()
        if not tickers:
            logger.error("[CacheService] Lỗi: Không thể lấy dữ liệu 24h từ Repository.")
            return

        EXCLUDE = ["UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "DAIUSDT", "EURUSDT"]
        
        new_map = {}
        for t in tickers:
            symbol = t['symbol']
            if not symbol.endswith("USDT"): continue
            if any(x in symbol for x in EXCLUDE): continue
            
            try:
                high = float(t['highPrice'])
                low = float(t['lowPrice'])
                vol_usdt = float(t['quoteVolume'])
                change_pct = float(t['priceChangePercent'])
                close = float(t['lastPrice'])
                
                vola = 0.0
                if low > 0:
                    vola = (high - low) / low * 100
                
                new_map[symbol] = {
                    'daily_vola': vola,
                    'volume_usdt': vol_usdt,
                    'quote_vol': vol_usdt,          # Added for backward compatibility
                    'change_pct': change_pct,
                    'price_change_pct': change_pct, # Added for backward compatibility
                    'change_24h': change_pct,       # Added for backward compatibility
                    'close': close,
                    'last_price': close, # Added for backward compatibility
                    'high': high,
                    'low': low
                }
            except Exception as e:
                continue
                
        if new_map:
            self._live_data_map = new_map
            self._live_data_updated_at = time.time()
            logger.info(f"[CacheService] Đã cập nhật thành công {len(new_map)} mã.")

    def get_live_data_map(self) -> Dict[str, Any]:
        """
        Trả về live_data_map. Nếu cache hết hạn, tự động refresh.
        """
        now = time.time()
        if not self._live_data_map or (now - self._live_data_updated_at > self.ttl_seconds):
            self._refresh_live_data()
            
        return self._live_data_map

    def get_avg_vola_24h(self) -> float:
        """
        Tính toán độ biến động trung bình 24h của top coin.
        """
        data_map = self.get_live_data_map()
        if not data_map:
            return 8.0 # Default fallback
            
        volas = [v['daily_vola'] for v in data_map.values() if v.get('daily_vola', 0) > 0]
        if not volas:
            return 8.0
            
        # Lọc nhiễu: Lấy median hoặc cắt 5% hai đầu
        volas.sort()
        idx_5 = int(len(volas) * 0.05)
        idx_95 = int(len(volas) * 0.95)
        clean_volas = volas[idx_5:idx_95]
        if clean_volas:
            return sum(clean_volas) / len(clean_volas)
        return sum(volas) / len(volas)
