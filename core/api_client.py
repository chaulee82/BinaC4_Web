import time
import logging
from typing import Optional, Dict, Any

try:
    import cloudscraper
    _session_builder = cloudscraper.create_scraper
except ImportError:
    import requests
    _session_builder = requests.Session

from requests.adapters import HTTPAdapter
# Cần import urllib3 Retry nhưng custom backoff thì tự viết sẽ tốt hơn để kiểm soát
# Tuy nhiên, urllib3 có sẵn Retry
from urllib3.util.retry import Retry

logger = logging.getLogger("APIClient")

class BinanceClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BinanceClient, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.session = _session_builder()
        
        # Connection Pooling & Exponential Backoff
        # Tái sử dụng connection và retry khi gặp lỗi mạng/rate limit
        retry_strategy = Retry(
            total=4,
            backoff_factor=0.5, # 0.5s, 1s, 2s, 4s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        self.domains = [
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
            "https://api3.binance.com",
            "https://data-api.binance.vision"
        ]

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Gửi GET request tới Binance. Tự động fallback sang domain khác nếu domain chính lỗi.
        """
        for domain in self.domains:
            url = f"{domain}{endpoint}"
            try:
                res = self.session.get(url, headers=self.headers, params=params, timeout=5)
                res.raise_for_status()
                return res.json()
            except Exception as e:
                logger.debug(f"[BinanceClient] Thất bại khi gọi {url}: {e}")
                continue
        
        logger.error(f"[BinanceClient] Tất cả domain đều thất bại cho endpoint {endpoint}")
        return None
