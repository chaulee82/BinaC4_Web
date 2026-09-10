"""
Module: core/exchange_info_cache.py
Dự án: BinaC4
Mục đích: Fetch exchangeInfo từ Binance MỘT LẦN khi startup, trích xuất
         tick_size (PRICE_FILTER) cho tất cả mã, lưu vào RAM (dict).

Cách dùng:
    from core.exchange_info_cache import ExchangeInfoCache

    # Gọi 1 lần khi khởi động:
    exc_cache = ExchangeInfoCache()
    exc_cache.load()

    # Lookup O(1) trong vòng lặp:
    tick_size = exc_cache.get_tick_size("BTCUSDT")   # → 0.01
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger("ExchangeInfoCache")


class ExchangeInfoCache:
    """
    Singleton cache cho exchangeInfo.
    - load() gọi API /api/v3/exchangeInfo một lần duy nhất khi startup.
    - get_tick_size() tra cứu O(1) trong suốt vòng đời ứng dụng.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _init_data(self):
        # Dict: symbol → tick_size (float)
        self._tick_size_map: Dict[str, float] = {}
        self._initialized = False

    def load(self, force_reload: bool = False) -> bool:
        """
        Fetch /api/v3/exchangeInfo và parse PRICE_FILTER → tick_size.
        Gọi một lần khi startup. Subsequent calls là no-op (trừ force_reload=True).

        Returns:
            True nếu load thành công, False nếu lỗi.
        """
        if self._initialized and not force_reload:
            return True

        # Khởi tạo dict nếu chưa có (lần đầu tạo instance)
        if not hasattr(self, '_tick_size_map'):
            self._init_data()

        from core.api_client import BinanceClient
        client = BinanceClient()

        logger.info("[ExchangeInfoCache] Đang fetch exchangeInfo từ Binance (1 lần duy nhất)...")
        data = client.get("/api/v3/exchangeInfo")

        if not data or 'symbols' not in data:
            logger.error("[ExchangeInfoCache] Lỗi: Không lấy được exchangeInfo từ Binance.")
            return False

        parsed = {}
        for sym_info in data['symbols']:
            symbol = sym_info.get('symbol', '')
            if not symbol.endswith('USDT'):
                continue
            if sym_info.get('status') != 'TRADING':
                continue

            tick_size = self._extract_tick_size(sym_info.get('filters', []))
            if tick_size > 0:
                parsed[symbol] = tick_size

        if not parsed:
            logger.error("[ExchangeInfoCache] Không parse được tick_size từ exchangeInfo.")
            return False

        self._tick_size_map = parsed
        self._initialized = True
        logger.info(f"[ExchangeInfoCache] Đã cache tick_size cho {len(parsed)} mã USDT.")
        return True

    @staticmethod
    def _extract_tick_size(filters: list) -> float:
        """Trích xuất tickSize từ mảng filters của symbol."""
        for f in filters:
            if f.get('filterType') == 'PRICE_FILTER':
                try:
                    return float(f['tickSize'])
                except (KeyError, ValueError):
                    return 0.0
        return 0.0

    def get_tick_size(self, symbol: str, default: float = 0.0001) -> float:
        """
        Lookup tick_size cho symbol. Trả về default nếu không tìm thấy.
        O(1) — không gọi API.

        Args:
            symbol  : Mã Binance (VD: "BTCUSDT")
            default : Giá trị mặc định nếu symbol không trong cache
        """
        if not self._initialized:
            logger.warning(f"[ExchangeInfoCache] Cache chưa được load! Trả về default={default}")
            return default
        return self._tick_size_map.get(symbol, default)

    def is_ready(self) -> bool:
        """Kiểm tra cache đã sẵn sàng chưa."""
        return self._initialized and bool(self._tick_size_map)

    def size(self) -> int:
        """Số mã đã cache."""
        return len(self._tick_size_map) if hasattr(self, '_tick_size_map') else 0
