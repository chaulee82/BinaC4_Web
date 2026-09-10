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

import threading

logger = logging.getLogger("APIClient")

class RateLimiter:
    def __init__(self, max_req_per_sec: float):
        self.interval = 1.0 / max_req_per_sec
        self.lock = threading.Lock()
        self.last_req_time = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_req_time
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_req_time = time.time()

# ── HTTP Call Counter (Verification) ───────────────────────────────────
# Đếm số HTTP request thực sự ra Binance (cache hit không tính).
# Thread-safe: dùng lock vì BinanceClient được gọi từ nhiều thread song song.
_http_call_lock  = threading.Lock()
_http_call_count = 0

def get_http_call_count() -> int:
    """Trả về số HTTP request thực sự kể từ lần reset gần nhất."""
    return _http_call_count

def reset_http_call_count():
    """Reset counter về 0 — gọi trước mỗi phase cần đo."""
    global _http_call_count
    with _http_call_lock:
        _http_call_count = 0

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
            "https://data-api.binance.vision",
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
            "https://api3.binance.com"
        ]
        
        # Giới hạn 15 req/s = 900 req/min (An toàn dưới mức 1200 của Binance)
        self.rate_limiter = RateLimiter(15.0)

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Gửi GET request tới Binance. Tự động fallback sang domain khác nếu domain chính lỗi.
        Mỗi lần gọi hàm này = 1 HTTP request thực sự ra internet (được đếm vào counter).
        """
        global _http_call_count
        with _http_call_lock:
            _http_call_count += 1

        for domain in self.domains:
            url = f"{domain}{endpoint}"
            try:
                self.rate_limiter.wait()
                res = self.session.get(url, headers=self.headers, params=params, timeout=3.0)
                res.raise_for_status()
                return res.json()
            except Exception as e:
                logger.debug(f"[BinanceClient] That bai khi goi {url}: {e}")
                continue

        logger.error(f"[BinanceClient] Tat ca domain deu that bai cho endpoint {endpoint}")
        return None
