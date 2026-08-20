class MarketData:
    def __init__(self, exchange=None):
        self.exchange = exchange

    def fetch_data(self, symbol: str, timeframe: str):
        # TODO: Implement Level 1 Data fetching (OHLCV multi-timeframe)
        pass
