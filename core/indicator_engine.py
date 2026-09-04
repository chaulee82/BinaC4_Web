"""
Module: indicator_engine.py
Dự án: CN4-Platform
Mục đích: Bộ máy tính chỉ báo kỹ thuật dùng chung, có in-memory TTL cache.

Thiết Kế Cache:
  - Cache key: tuple (last_timestamp, indicator_name, *params)
  - TTL: 60 giây (1 chu kỳ quét tiêu chuẩn)
  - Khi nhiều hàm tính toán trong cùng tick cùng yêu cầu một chỉ báo trên
    cùng một DataFrame, cache nội bộ trả về kết quả đã có sẵn —
    Pandas không phải chạy lại rolling/ewm dư thừa.
  - Gọi clear_cache() ở đầu mỗi chu kỳ quét mới để đảm bảo dữ liệu tươi.
"""

import logging
import math
import time
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("CN4.IndicatorEngine")


class IndicatorEngine:
    """
    Bộ máy tính chỉ báo kỹ thuật với in-memory cache per scan-tick.

    Sử dụng:
        engine = IndicatorEngine()
        engine.clear_cache()                         # Xóa cache đầu mỗi tick mới

        ema25 = engine.get_ema(df_15m, 25)           # Tính lần đầu
        bb    = engine.get_bollinger_bands(df_15m)   # Tính lần đầu
        ema25 = engine.get_ema(df_15m, 25)           # Cache hit — không tính lại
    """

    _CACHE_TTL: int = 60  # seconds

    def __init__(self):
        self._cache: dict = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Quản Lý Cache
    # ──────────────────────────────────────────────────────────────────────────

    def _make_key(self, df: pd.DataFrame, indicator: str, *params) -> tuple:
        """
        Tạo cache key dựa trên timestamp nến cuối cùng + tên chỉ báo + tham số.
        Dùng timestamp thay vì id(df) để key bất biến kể cả khi df được copy.
        """
        if 'timestamp' in df.columns:
            last_ts = int(df['timestamp'].iloc[-1])
            row_count = len(df)
        else:
            # Fallback: dùng hash của giá trị cuối cùng
            last_ts = hash(float(df['close'].iloc[-1]))
            row_count = len(df)
        return (last_ts, row_count, indicator, *params)

    def _get_cached(self, key: tuple):
        """Trả về giá trị cache nếu còn hạn, None nếu không tìm thấy hoặc hết TTL."""
        entry = self._cache.get(key)
        if entry is not None:
            value, ts = entry
            if time.monotonic() - ts < self._CACHE_TTL:
                return value
            # TTL hết hạn — xóa entry cũ
            del self._cache[key]
        return None

    def _set_cache(self, key: tuple, value):
        """Lưu giá trị vào cache với timestamp hiện tại."""
        self._cache[key] = (value, time.monotonic())

    def clear_cache(self):
        """Xóa toàn bộ cache. Gọi khi bắt đầu chu kỳ quét (tick) mới."""
        self._cache.clear()
        logger.debug("Cache cleared.")

    @property
    def cache_size(self) -> int:
        """Số lượng entries đang được cache."""
        return len(self._cache)

    # ──────────────────────────────────────────────────────────────────────────
    # Chỉ Báo Đường Trung Bình
    # ──────────────────────────────────────────────────────────────────────────

    def get_ema(self, df: pd.DataFrame, period: int) -> pd.Series:
        """
        Exponential Moving Average (EMA).

        Args:
            df:     OHLCV DataFrame với cột 'close'
            period: Chu kỳ EMA

        Returns:
            pd.Series giá trị EMA theo index của df
        """
        key = self._make_key(df, 'ema', period)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        result = df['close'].ewm(span=period, adjust=False).mean()
        self._set_cache(key, result)
        return result

    def get_ma(self, df: pd.DataFrame, period: int) -> pd.Series:
        """
        Simple Moving Average (SMA/MA).

        Args:
            df:     OHLCV DataFrame với cột 'close'
            period: Chu kỳ MA

        Returns:
            pd.Series giá trị MA theo index của df
        """
        key = self._make_key(df, 'ma', period)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        result = df['close'].rolling(window=period).mean()
        self._set_cache(key, result)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Bollinger Bands
    # ──────────────────────────────────────────────────────────────────────────

    def get_bollinger_bands(
        self, df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
    ) -> dict:
        """
        Bollinger Bands (BB).

        Args:
            df:      OHLCV DataFrame với cột 'close'
            period:  Chu kỳ rolling (mặc định 20)
            std_dev: Hệ số độ lệch chuẩn (mặc định 2.0)

        Returns:
            dict với keys:
                'mid'   : pd.Series — dải giữa (SMA20)
                'upper' : pd.Series — dải trên (mid + std_dev * σ)
                'lower' : pd.Series — dải dưới (mid - std_dev * σ)
        """
        key = self._make_key(df, 'boll', period, std_dev)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        mid = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std(ddof=1)

        result = {
            'mid':   mid,
            'upper': mid + std * std_dev,
            'lower': mid - std * std_dev,
        }
        self._set_cache(key, result)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Supertrend
    # ──────────────────────────────────────────────────────────────────────────

    def get_supertrend(
        self, df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
    ) -> dict:
        """
        Supertrend Indicator (Wilder ATR-based).

        Thuật toán:
          1. Tính ATR bằng Wilder smoothing (SMMA)
          2. Tính Upper/Lower band cơ bản: HL2 ± multiplier * ATR
          3. Điều chỉnh band theo chiều giá (tránh band flip không cần thiết)
          4. Xác định direction: 1 = uptrend, -1 = downtrend

        Args:
            df:         OHLCV DataFrame với cột 'high', 'low', 'close'
            period:     Chu kỳ ATR (mặc định 10)
            multiplier: Hệ số ATR (mặc định 3.0)

        Returns:
            dict với keys:
                'direction'     : int   — 1 (uptrend/bệ đỡ) / -1 (downtrend/kháng cự)
                'current_value' : float — giá trị Supertrend nến hiện tại
                'support'       : float | None — bệ đỡ (chỉ có khi uptrend)
                'resistance'    : float | None — kháng cự (chỉ có khi downtrend)
                'series'        : pd.Series — toàn bộ chuỗi giá trị Supertrend
        """
        key = self._make_key(df, 'supertrend', period, multiplier)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        n     = len(df)
        high  = df['high'].values.astype(np.float64)
        low   = df['low'].values.astype(np.float64)
        close = df['close'].values.astype(np.float64)

        # ── Trường hợp thiếu dữ liệu ──────────────────────────────────────────
        if n < period + 2:
            fallback_val = float(low[-1])
            result = {
                'direction':     1,
                'current_value': fallback_val,
                'support':       fallback_val,
                'resistance':    None,
                'series':        pd.Series(np.full(n, fallback_val), index=df.index),
            }
            self._set_cache(key, result)
            logger.warning(f"Supertrend: Thiếu dữ liệu (n={n} < {period + 2}), dùng fallback.")
            return result

        # ── True Range (vectorized) ────────────────────────────────────────────
        tr = np.empty(n, dtype=np.float64)
        tr[0] = high[0] - low[0]
        hl    = high[1:] - low[1:]
        hc    = np.abs(high[1:] - close[:-1])
        lc    = np.abs(low[1:]  - close[:-1])
        tr[1:] = np.maximum(hl, np.maximum(hc, lc))

        # ── Wilder Smoothed ATR (SMMA) ─────────────────────────────────────────
        # Warmup: index 0..(period-2) = nan; index (period-1) = SMA seed
        atr = np.full(n, np.nan, dtype=np.float64)
        atr[period - 1] = tr[:period].mean()
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        # ── Upper/Lower Bands cơ bản ───────────────────────────────────────────
        hl2         = (high + low) / 2.0
        basic_upper = hl2 + multiplier * atr
        basic_lower = hl2 - multiplier * atr

        # ── Band Adjustment + Direction (vòng lặp bắt buộc — self-referential) ──
        # Vòng lặp chỉ hợp lệ từ index `period` (ATR đã có giá trị thực).
        # Các index trong giai đoạn warmup (0..period-1) dùng fallback = low.
        final_upper = np.where(np.isnan(basic_upper), high, basic_upper).copy()
        final_lower = np.where(np.isnan(basic_lower), low,  basic_lower).copy()
        supertrend  = np.full(n, np.nan, dtype=np.float64)
        direction   = np.ones(n, dtype=np.int8)  # default uptrend

        # Seed tại index (period-1): dùng giá trị band đầu tiên hợp lệ
        seed_idx = period - 1
        supertrend[seed_idx] = final_lower[seed_idx]  # Giả sử uptrend ở seed
        direction[seed_idx]  = 1

        for i in range(period, n):
            # Final upper: chỉ siết chặt (giảm) nếu giá chưa phá trên
            if basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
                final_upper[i] = basic_upper[i]
            else:
                final_upper[i] = final_upper[i - 1]

            # Final lower: chỉ nâng lên nếu giá chưa phá dưới
            if basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
                final_lower[i] = basic_lower[i]
            else:
                final_lower[i] = final_lower[i - 1]

            # Xác định chiều xu hướng và giá trị Supertrend
            if direction[i - 1] == -1:
                # Đang downtrend: chuyển sang uptrend khi giá vượt upper band
                if close[i] > final_upper[i]:
                    direction[i]  = 1
                    supertrend[i] = final_lower[i]
                else:
                    direction[i]  = -1
                    supertrend[i] = final_upper[i]
            else:
                # Đang uptrend: chuyển sang downtrend khi giá thủng lower band
                if close[i] < final_lower[i]:
                    direction[i]  = -1
                    supertrend[i] = final_upper[i]
                else:
                    direction[i]  = 1
                    supertrend[i] = final_lower[i]

        current_dir = int(direction[-1])
        current_val = float(supertrend[-1])

        result = {
            'direction':     current_dir,
            'current_value': current_val,
            'support':       current_val if current_dir ==  1 else None,
            'resistance':    current_val if current_dir == -1 else None,
            'series':        pd.Series(supertrend, index=df.index),
        }
        self._set_cache(key, result)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # ATR (standalone — dùng khi chỉ cần ATR mà không cần Supertrend)
    # ──────────────────────────────────────────────────────────────────────────

    def get_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Average True Range (Wilder smoothing).

        Args:
            df:     OHLCV DataFrame
            period: Chu kỳ ATR (mặc định 14)

        Returns:
            pd.Series giá trị ATR
        """
        key = self._make_key(df, 'atr', period)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        high  = df['high']
        low   = df['low']
        close = df['close']

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        self._set_cache(key, atr)
        return atr

    # ──────────────────────────────────────────────────────────────────────────
    # RSI (Wilder Smoothing — khớp với chuẩn TradingView)
    # ──────────────────────────────────────────────────────────────────────────

    def get_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Relative Strength Index (Wilder EWM smoothing, com = period - 1).

        Công thức khớp với TradingView và cách tính hiện có trong DC2/DC3:
            avg_gain = gains.ewm(com=period-1, min_periods=period).mean()
            avg_loss = losses.ewm(com=period-1, min_periods=period).mean()
            RSI = 100 - 100 / (1 + avg_gain / avg_loss)

        Args:
            df:     OHLCV DataFrame với cột 'close'
            period: Chu kỳ RSI (mặc định 14)

        Returns:
            pd.Series giá trị RSI (0–100)
        """
        key = self._make_key(df, 'rsi', period)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        delta  = df['close'].diff()
        gains  = delta.clip(lower=0)
        losses = (-delta.clip(upper=0))

        avg_gain = gains.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = losses.ewm(com=period - 1, min_periods=period).mean()

        # Tránh chia cho 0 khi avg_loss = 0 (chuỗi tăng liên tục → RSI = 100)
        rs  = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        self._set_cache(key, rsi)
        return rsi
