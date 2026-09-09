"""
Module: klines_cache.py
Dự án: BinaC4
Mục đích: Cache klines dùng chung toàn hệ thống (thread-safe).

Thay thế _klines_cache dict riêng lẻ trong coin_filter.py và self._cache trong
market_data_repo.py bằng 1 cache duy nhất, tránh gọi API trùng lặp.

Cách dùng:
    from core.klines_cache import get_klines_cached, warm_klines_cache
"""

import time
import threading
import pandas as pd
import logging
from typing import Optional, List, Tuple

logger = logging.getLogger("KlinesCache")

# ── Config ────────────────────────────────────────────────────────────────────
_CACHE_TTL = 120   # 120 giây — đủ cho process_symbol + EarlyWarning + Darvas dùng chung 1 batch
_MAX_ITEMS = 2000  # Tối đa 2000 entries (symbol × interval combinations)

# ── Fetch limits mặc định cho từng interval ──────────────────────────────────
# Lấy dư để phục vụ nhiều module với limit khác nhau mà không fetch lại
_DEFAULT_FETCH_LIMIT = {
    '15m': 100,
    '1h':  168,
    '4h':  120,
    '1d':  180,
}

# ── Internal storage ─────────────────────────────────────────────
# Key dạng string: f"{symbol}_{interval}" (dễ debug, serialize, evict hơn tuple)
_cache: dict = {}   # key: "{BTCUSDT_1h}" → (timestamp: float, df: DataFrame)
_lock  = threading.Lock()

# ── API call counter (diagnostic) ────────────────────────────────────────────
_api_call_count = 0

def get_api_call_count() -> int:
    """Trả về số lần gọi API thực sự (dùng để debug)."""
    return _api_call_count

def reset_api_call_count():
    global _api_call_count
    _api_call_count = 0


def _fetch_from_binance(symbol: str, interval: str, fetch_limit: int) -> Optional[pd.DataFrame]:
    """
    Gọi Binance API lấy klines, parse thành DataFrame chuẩn.
    Trả về None nếu lỗi.

    NOTE: time.sleep() chỉ đặt ở đây — ngay trước HTTP request thực sự.
    Cache hit hoàn toàn không chạm sleep này.
    Sleep 50ms giúp các thread gối đầu nhau, tránh bục toàn bộ requests cùng 1 ms.
    """
    global _api_call_count
    from core.api_client import BinanceClient
    client = BinanceClient()

    # Rate-limit throttle: chỉ đặt khi thực sự gọi HTTP, không ảnh hưởng cache hit
    time.sleep(0.05)   # 50ms — giao động tự nhiên giữa các thread

    _api_call_count += 1
    data = client.get("/api/v3/klines", params={
        "symbol":   symbol,
        "interval": interval,
        "limit":    fetch_limit
    })

    if not isinstance(data, list) or len(data) == 0:
        return None

    df = pd.DataFrame(data, columns=[
        'Open_Time', 'Open', 'High', 'Low', 'Close', 'Volume',
        'Close_Time', 'Quote_Volume', 'Trades',
        'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
    ])
    for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Quote_Volume', 'Taker_Buy_Quote']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


def _cleanup_if_needed(now: float):
    """Dọn dẹp cache nếu quá lớn — chỉ gọi trong lock."""
    if len(_cache) > _MAX_ITEMS:
        expired = [k for k, (t, _) in _cache.items() if now - t > _CACHE_TTL]
        for k in expired:
            del _cache[k]
        logger.debug(f"[KlinesCache] Evicted {len(expired)} entries. Remaining: {len(_cache)}")


def get_klines_cached(symbol: str, interval: str, limit: int = 100) -> Optional[pd.DataFrame]:
    """
    Lấy klines từ cache nếu có, không thì gọi API và lưu vào cache.
    Cache hit → trả ngay, KHÔNG có sleep nào cả.
    Cache miss → gọi _fetch_from_binance() (có sleep 50ms bên trong).
    """
    now = time.time()
    cache_key = f"{symbol}_{interval}"   # string key: dễ debug, evict, serialize

    # ── Check cache (read path) ──
    with _lock:
        if cache_key in _cache:
            cached_time, df = _cache[cache_key]
            if now - cached_time < _CACHE_TTL and len(df) >= limit:
                return df.tail(limit).copy()   # cache hit: instant, 0ms

    # ── Cache miss → gọi API (outside lock để không block thread khác) ──
    fetch_limit = max(limit, _DEFAULT_FETCH_LIMIT.get(interval, limit))
    df = _fetch_from_binance(symbol, interval, fetch_limit)  # sleep 50ms ở đây

    if df is None:
        return None

    # ── Write to cache ──
    now2 = time.time()
    with _lock:
        _cache[cache_key] = (now2, df)
        _cleanup_if_needed(now2)

    return df.tail(limit).copy()


def warm_klines_cache(
    symbols: List[str],
    intervals: Optional[List[str]] = None,
    max_workers: int = 12          # Binance-safe: 12 luồng song song ~ 240 req/s max
):
    """
    Pre-fetch klines song song cho danh sách symbols trước khi chạy analysis loop.
    Điền vào cache để các lời gọi sau đều là cache-hit (0ms, 0 API call).

    max_workers=12 đảm bảo các luồng gối đầu nhau (mỗi luồng có sleep 50ms),
    tránh bục toàn bộ requests trong cùng 1 millisecond gây Rate Limit 429.
    """
    if intervals is None:
        intervals = ['15m', '1h', '4h', '1d']

    from concurrent.futures import ThreadPoolExecutor

    tasks: List[Tuple[str, str]] = []
    now = time.time()

    # Chỉ fetch những cái chưa có cache hoặc đã hết hạn
    with _lock:
        for sym in symbols:
            for itv in intervals:
                key = f"{sym}_{itv}"   # string key — nhất quán với get_klines_cached
                if key not in _cache:
                    tasks.append((sym, itv))
                else:
                    cached_time, df = _cache[key]
                    min_limit = _DEFAULT_FETCH_LIMIT.get(itv, 100)
                    if now - cached_time >= _CACHE_TTL or len(df) < min_limit:
                        tasks.append((sym, itv))

    if not tasks:
        logger.info(f"[KlinesCache] Warm cache: Tất cả {len(symbols)} x {len(intervals)} đã sẵn sàng.")
        return

    logger.info(f"[KlinesCache] Warm cache: Cần fetch {len(tasks)} cặp (symbol×interval) song song...")

    def _fetch_task(args):
        sym, itv = args
        fetch_limit = _DEFAULT_FETCH_LIMIT.get(itv, 100)
        df = _fetch_from_binance(sym, itv, fetch_limit)   # sleep 50ms ở đây
        if df is not None:
            t = time.time()
            key = f"{sym}_{itv}"   # string key
            with _lock:
                _cache[key] = (t, df)
        return sym, itv, df is not None

    ok = 0
    fail = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for sym, itv, success in pool.map(_fetch_task, tasks):
            if success:
                ok += 1
            else:
                fail += 1

    logger.info(f"[KlinesCache] Warm cache xong: {ok} thanh cong, {fail} that bai.")
