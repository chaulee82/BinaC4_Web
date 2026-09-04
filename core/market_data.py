import pandas as pd
import logging

logger = logging.getLogger(__name__)

class MarketData:
    def __init__(self, exchange=None):
        self.exchange = exchange

    def fetch_data(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """
        Fetch Level 1 Data (OHLCV) and return as pandas DataFrame
        """
        if not self.exchange:
            logger.error("Exchange instance is missing.")
            return None
            
        try:
            data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not data:
                return None
                
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching data for {symbol} on {timeframe}: {e}")
            return None
