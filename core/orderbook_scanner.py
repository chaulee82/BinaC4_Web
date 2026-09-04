import logging

logger = logging.getLogger(__name__)

class OrderbookScanner:
    def __init__(self, exchange=None):
        self.exchange = exchange

    def scan(self, symbol: str, limit: int = 100) -> dict:
        """
        Scan Level 2 Data (Orderbook) to analyze Buy/Sell walls and volume imbalance.
        """
        if not self.exchange:
            logger.error("Exchange instance is missing.")
            return None

        try:
            ob = self.exchange.fetch_order_book(symbol, limit=limit)
            bids = ob.get('bids', [])
            asks = ob.get('asks', [])

            total_bid_vol = sum(amount for price, amount in bids)
            total_ask_vol = sum(amount for price, amount in asks)
            
            imbalance = 0.0
            if (total_bid_vol + total_ask_vol) > 0:
                imbalance = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)

            max_bid = max(bids, key=lambda x: x[1]) if bids else [0, 0]
            max_ask = max(asks, key=lambda x: x[1]) if asks else [0, 0]

            return {
                'symbol': symbol,
                'total_bid_volume': total_bid_vol,
                'total_ask_volume': total_ask_vol,
                'imbalance': imbalance,
                'buy_wall': {'price': max_bid[0], 'volume': max_bid[1]},
                'sell_wall': {'price': max_ask[0], 'volume': max_ask[1]}
            }
        except Exception as e:
            logger.error(f"Error scanning orderbook for {symbol}: {e}")
            return None
