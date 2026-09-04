import pandas as pd
from typing import List, Dict, Any, Optional
from core.api_client import BinanceClient

class MarketDataRepository:
    def __init__(self):
        self.api_client = BinanceClient()

    def get_klines_df(self, symbol: str, interval: str, limit: int = 50) -> pd.DataFrame:
        """
        Lấy dữ liệu nến (Klines) và trả về DataFrame.
        Symbol phải là dạng chuẩn Binance (ví dụ: BTCUSDT).
        """
        endpoint = "/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
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
        return df

    def get_ticker_24h(self) -> Optional[List[Dict[str, Any]]]:
        """
        Lấy dữ liệu Ticker 24h của tất cả các cặp giao dịch.
        """
        return self.api_client.get("/api/v3/ticker/24hr")
