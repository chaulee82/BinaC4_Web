import pandas as pd
from typing import List, Dict, Any, Optional
from core.api_client import BinanceClient

class MarketDataRepository:
    def __init__(self):
        self.api_client = BinanceClient()
        # self._cache đã được thay bằng shared klines_cache (core/klines_cache.py)

    def get_klines_df(self, symbol: str, interval: str, limit: int = 50) -> pd.DataFrame:
        """
        Lấy dữ liệu nến (Klines) và trả về DataFrame.
        Symbol phải là dạng chuẩn Binance (ví dụ: BTCUSDT).

        Bước 2+3: Dùng shared klines_cache toàn hệ thống (TTL 120s).
        Không còn tự cache riêng — tránh gọi API trùng với get_klines_live.

        Lưu ý: DataFrame trả về dùng tên cột lowercase (timestamp, open, high, low, close, volume)
        để tương thích với các module dùng repo trực tiếp (EarlyWarningMatrix, v.v.).
        """
        from core.klines_cache import get_klines_cached
        df = get_klines_cached(symbol, interval, limit)
        if df is None or df.empty:
            return pd.DataFrame()

        # Chuẩn hóa tên cột sang lowercase để tương thích với EarlyWarningMatrix
        col_map = {
            'Open_Time': 'timestamp',
            'Open':      'open',
            'High':      'high',
            'Low':       'low',
            'Close':     'close',
            'Volume':    'volume',
        }
        df = df.rename(columns=col_map)
        keep_cols = [c for c in ['timestamp', 'open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        return df[keep_cols].copy()


    def get_ticker_24h(self) -> Optional[List[Dict[str, Any]]]:
        """
        Lấy dữ liệu Ticker 24h của tất cả các cặp giao dịch.
        """
        return self.api_client.get("/api/v3/ticker/24hr")
