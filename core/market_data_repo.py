import pandas as pd
from typing import List, Dict, Any, Optional
from core.api_client import BinanceClient

class MarketDataRepository:
    def __init__(self):
        self.api_client = BinanceClient()
        self._cache = {}

    def get_klines_df(self, symbol: str, interval: str, limit: int = 50) -> pd.DataFrame:
        """
        Lấy dữ liệu nến (Klines) và trả về DataFrame.
        Symbol phải là dạng chuẩn Binance (ví dụ: BTCUSDT).
        """
        import time
        now = time.time()
        cache_key = (symbol, interval)
        
        if cache_key in self._cache:
            cached_time, df = self._cache[cache_key]
            # Cache valid for 60 seconds
            if now - cached_time < 60 and len(df) >= limit:
                return df.tail(limit).copy()

        fetch_limit = limit
        if interval == '15m': fetch_limit = max(limit, 100)
        elif interval == '1h': fetch_limit = max(limit, 168)
        elif interval == '4h': fetch_limit = max(limit, 120)
        elif interval == '1d': fetch_limit = max(limit, 180)

        endpoint = "/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": fetch_limit
        }
        
        data = self.api_client.get(endpoint, params=params)
        if not data:
            return pd.DataFrame()
            
        rows = []
        for x in data:
            # Binance klines format:
            # [0] Open time, [1] Open, [2] High, [3] Low, [4] Close, [5] Volume
            rows.append([
                int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])
            ])
            
        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        self._cache[cache_key] = (now, df)
        
        # Cleanup
        if len(self._cache) > 1000:
            keys_to_delete = [k for k, (t, _) in self._cache.items() if now - t > 60]
            for k in keys_to_delete:
                del self._cache[k]
                
        return df.tail(limit).copy()

    def get_ticker_24h(self) -> Optional[List[Dict[str, Any]]]:
        """
        Lấy dữ liệu Ticker 24h của tất cả các cặp giao dịch.
        """
        return self.api_client.get("/api/v3/ticker/24hr")
