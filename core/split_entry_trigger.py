"""
Module: core/split_entry_trigger.py
Dự án: BinaC4 — Update v2.7 (Split-Entry & Buffer Rules Final)
Mục đích: Thuật toán Giá Kích Hoạt Tối Ưu (Optimal Trigger) cho chiến lược Grid "Rải Đinh".

Luồng xử lý `calculate_optimal_trigger(symbol_data)`:
    B1. Rủi ro vĩ mô BTC   : is_market_dump = btc_rsi_1h < 40
    B2. Mốc hỗ trợ động    : OCO-1 (DC2) | MA7_3D (hoặc MA25_3D khi dump) | Swing Low 15m | BB MB(20,2) 4H
    B3. Chọn mốc gần live×0.95 → Đệm tối thiểu: Distance > -3.0% → ép live×(1-3.5%) rồi lùi sâu về MA7 3D
    B4. Chốt Supertrend(10,3) 1D: trigger ≤ ST → trigger = ST × 1.015
    B5. Làm tròn theo tick_size của mã

Hàm thuần (pure) — KHÔNG gọi API. Việc thu thập dữ liệu nằm ở `build_symbol_data()`.
"""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("CN4.SplitEntry.Trigger")

# ── Hằng số TRD v2.7 ──────────────────────────────────────────────────────────
BTC_DUMP_RSI_THRESHOLD = 40.0     # B1: btc_rsi_1h < 40 → market dump
BASE_DISCOUNT_RATIO    = 0.95     # B3: mốc tham chiếu = live × 0.95
MIN_DISCOUNT_PCT       = 3.5      # B3: biên độ đệm ép buộc (%)
BUFFER_TRIGGER_PCT     = -3.0     # B3: Distance > -3.0% → kích hoạt Force Update
SUPERTREND_GAP_RATIO   = 1.015    # B4: cách Supertrend 1.5% lên trên
SWING_LOOKBACK_15M     = 20       # B2.3: số nến 15m quét Swing Low
SWING_WICK_MIN_RATIO   = 0.3      # B2.3: (Open - Low) / (High - Low) > 0.3
BB_PERIOD_4H           = 20       # B2.4: MB(20,2) 4H = SMA20 close 4H


# ── Helpers ───────────────────────────────────────────────────────────────────
def _num(x: Any) -> Optional[float]:
    """Ép float dương hợp lệ, ngược lại trả None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v <= 0:  # NaN hoặc ≤ 0
        return None
    return v


def round_to_tick(value: float, tick_size: Optional[float]) -> float:
    """Làm tròn giá về bội số tick_size (dùng chung phễu Decimal của macro_levels)."""
    try:
        from core.macro_levels import _round_tick
        return _round_tick(float(value), float(tick_size or 0.0))
    except Exception:
        return round(float(value), 8)


def _ohlc_frame(klines: Any) -> Optional[pd.DataFrame]:
    """Chuẩn hóa klines (DataFrame / list[dict]) → DataFrame cột lowercase open/high/low/close."""
    if klines is None:
        return None
    df = klines if isinstance(klines, pd.DataFrame) else pd.DataFrame(list(klines))
    if df.empty:
        return None
    df = df.rename(columns={c: str(c).lower() for c in df.columns})
    if not {"open", "high", "low"}.issubset(df.columns):
        return None
    return df


def find_swing_low_15m(klines_15m: Any, lookback: int = SWING_LOOKBACK_15M,
                       min_wick_ratio: float = SWING_WICK_MIN_RATIO) -> Optional[float]:
    """B2.3 — Low thấp nhất trong `lookback` nến 15m gần nhất, chỉ xét nến có râu dưới
    (Open - Low) / (High - Low) > min_wick_ratio."""
    df = _ohlc_frame(klines_15m)
    if df is None:
        return None
    df = df.tail(lookback)
    o = pd.to_numeric(df["open"], errors="coerce")
    h = pd.to_numeric(df["high"], errors="coerce")
    lo = pd.to_numeric(df["low"], errors="coerce")
    rng = h - lo
    valid = (rng > 0) & (((o - lo) / rng.where(rng > 0)) > min_wick_ratio)
    lows = lo[valid].dropna()
    return float(lows.min()) if not lows.empty else None


def calc_bb_mid_4h(klines_4h: Any, period: int = BB_PERIOD_4H) -> Optional[float]:
    """B2.4 — Trục giữa Bollinger MB(20,2) khung 4H = SMA(20) giá đóng cửa."""
    df = _ohlc_frame(klines_4h)
    if df is None or "close" not in df.columns or len(df) < period:
        return None
    c = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(c) < period:
        return None
    return _num(c.tail(period).mean())


def calc_supertrend_1d(klines_1d: Any, period: int = 10, multiplier: float = 3.0):
    """B4 — Supertrend(10,3) 1D → (value, direction). direction: 1 = Xanh (hỗ trợ), -1 = Đỏ (kháng cự)."""
    df = _ohlc_frame(klines_1d)
    if df is None or "close" not in df.columns:
        return None, 0
    try:
        from core.macro_levels import _supertrend_val_dir
        df = df[["high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna().reset_index(drop=True)
        if len(df) < period + 2:
            return None, 0
        val, d = _supertrend_val_dir(df, period=period, multiplier=multiplier)
        return _num(val), int(d)
    except Exception as e:
        logger.debug(f"[SplitEntry] Lỗi tính Supertrend 1D: {e}")
        return None, 0


def calc_btc_rsi_1h(klines_btc_1h: Any, period: int = 14) -> Optional[float]:
    """RSI(14) Wilder khung 1H của BTC (cùng công thức coin_filter._rsi_series)."""
    df = _ohlc_frame(klines_btc_1h)
    if df is None or "close" not in df.columns:
        return None
    try:
        from core.coin_filter import _rsi_series
        rsi = _rsi_series(df["close"], period).dropna()
        return float(rsi.iloc[-1]) if not rsi.empty else None
    except Exception as e:
        logger.debug(f"[SplitEntry] Lỗi tính RSI BTC 1H: {e}")
        return None


# ── CORE LOGIC ────────────────────────────────────────────────────────────────
def calculate_optimal_trigger_detail(symbol_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tính Giá Kích Hoạt Tối Ưu kèm vết quyết định (để hiển thị cột "Trạng Thái / Hỗ trợ").

    symbol_data (key nào thiếu → bỏ qua mốc đó):
        live_price           : float  (bắt buộc)
        btc_rsi_1h           : float
        oco_buy_limit_price  : float  — Giá Buy Limit OCO-1 từ Engine 2 (Pullback Sniper)
        ma7_3d, ma25_3d      : float
        swing_low_15m        : float  — hoặc truyền `klines_15m` để tự quét
        bb_mid_4h            : float  — hoặc truyền `klines_4h`
        supertrend_1d        : float  — hoặc truyền `klines_1d`
        supertrend_dir_1d    : int    (1 = Xanh, -1 = Đỏ)
        tick_size            : float
    """
    live = _num(symbol_data.get("live_price"))
    result: Dict[str, Any] = {
        "optimal_trigger": None, "live_price": live, "is_market_dump": False,
        "supports": {}, "base_label": "", "selected_label": "", "flags": [],
    }
    if live is None:
        result["flags"].append("NO_LIVE_PRICE")
        return result

    tick = symbol_data.get("tick_size")

    # ── B1: Rủi ro vĩ mô BTC ──
    btc_rsi = symbol_data.get("btc_rsi_1h")
    is_dump = btc_rsi is not None and float(btc_rsi) < BTC_DUMP_RSI_THRESHOLD
    result["is_market_dump"] = is_dump

    # ── B2: Thu thập mốc hỗ trợ động ──
    ma7 = _num(symbol_data.get("ma7_3d"))
    ma25 = _num(symbol_data.get("ma25_3d"))
    swing = _num(symbol_data.get("swing_low_15m"))
    if swing is None and symbol_data.get("klines_15m") is not None:
        swing = find_swing_low_15m(symbol_data.get("klines_15m"))
    bb_mid = _num(symbol_data.get("bb_mid_4h"))
    if bb_mid is None and symbol_data.get("klines_4h") is not None:
        bb_mid = calc_bb_mid_4h(symbol_data.get("klines_4h"))

    supports: Dict[str, float] = {}
    oco1 = _num(symbol_data.get("oco_buy_limit_price"))
    if oco1:
        supports["OCO-1"] = oco1
    ma_label, ma_val = ("MA25 3D", ma25) if is_dump else ("MA7 3D", ma7)
    if ma_val:
        supports[ma_label] = ma_val
    if swing:
        supports["Swing Low 15m"] = swing
    if bb_mid:
        supports["BB MB 4H"] = bb_mid
    result["supports"] = supports

    # ── B3: Chọn mốc gần live × 0.95 nhất ──
    ref = live * BASE_DISCOUNT_RATIO
    if supports:
        label, selected = min(supports.items(), key=lambda kv: abs(kv[1] - ref))
    else:
        label, selected = "Live × 0.95 (thiếu mốc)", ref
        result["flags"].append("NO_SUPPORT")
    result["base_label"] = label

    # Quy tắc đệm tối thiểu: Distance > -3.0% → ép live × (1 - 3.5%) rồi lùi sâu về MA7 3D
    distance = (selected - live) / live * 100
    if distance > BUFFER_TRIGGER_PCT:
        selected = live * (1 - MIN_DISCOUNT_PCT / 100)
        label = f"Đệm -{MIN_DISCOUNT_PCT:g}%"
        result["flags"].append("BUFFER_FORCED")
        if ma7 and ma7 < selected:
            selected = ma7
            label = "MA7 3D (lùi sâu)"
            result["flags"].append("PUSHED_TO_MA7_3D")

    # ── B4: Chốt chặn Supertrend 1D ──
    st_val = _num(symbol_data.get("supertrend_1d"))
    st_dir = symbol_data.get("supertrend_dir_1d")
    if st_val is None and symbol_data.get("klines_1d") is not None:
        st_val, st_dir = calc_supertrend_1d(symbol_data.get("klines_1d"))
    result["supertrend_1d"] = st_val
    result["supertrend_dir_1d"] = st_dir
    # Chỉ áp dụng khi ST đang Xanh (đóng vai hỗ trợ dưới giá). ST Đỏ nằm TRÊN giá → nếu áp dụng
    # sẽ đẩy lệnh chờ lên cao hơn giá Live (mua đuổi), trái mục tiêu "tránh đu đỉnh".
    if st_val and st_dir != -1 and selected <= st_val:
        selected = st_val * SUPERTREND_GAP_RATIO
        label = "Supertrend 1D +1.5%"
        result["flags"].append("SUPERTREND_GUARD")

    # ── B5: Làm tròn theo độ chính xác của mã ──
    result["optimal_trigger"] = round_to_tick(selected, tick)
    result["selected_label"] = label
    result["distance_pct"] = (result["optimal_trigger"] - live) / live * 100
    return result


def calculate_optimal_trigger(symbol_data: Dict[str, Any]) -> Optional[float]:
    """TRD v2.7 §2 — trả về `optimal_trigger_price` (float, đã làm tròn tick). None nếu thiếu giá Live."""
    return calculate_optimal_trigger_detail(symbol_data).get("optimal_trigger")


# ── Thu thập dữ liệu (I/O) ────────────────────────────────────────────────────
def get_btc_rsi_1h(repo=None) -> Optional[float]:
    """Đọc RSI 1H của BTC (klines_cache → 0 API call nếu cache ấm)."""
    try:
        if repo is None:
            from core.market_data_repo import MarketDataRepository
            repo = MarketDataRepository()
        return calc_btc_rsi_1h(repo.get_klines_df("BTCUSDT", "1h", 100))
    except Exception as e:
        logger.debug(f"[SplitEntry] Không lấy được RSI BTC 1H: {e}")
        return None


def build_symbol_data(symbol: str, live_price: Optional[float], btc_rsi_1h: Optional[float],
                      oco_buy_limit_price: Optional[float] = None, repo=None) -> Dict[str, Any]:
    """Gom Data Object cho 1 mã (klines lấy qua shared klines_cache)."""
    sym = str(symbol).upper().replace("/USDT", "").replace("USDT", "").strip()
    sym_api = f"{sym}USDT"
    data: Dict[str, Any] = {
        "symbol": sym, "live_price": live_price, "btc_rsi_1h": btc_rsi_1h,
        "oco_buy_limit_price": oco_buy_limit_price,
    }
    if repo is None:
        from core.market_data_repo import MarketDataRepository
        repo = MarketDataRepository()

    try:
        from core.exchange_info_cache import ExchangeInfoCache
        _c = ExchangeInfoCache()
        data["tick_size"] = _c.get_tick_size(sym_api, default=0.0) if _c.is_ready() else 0.0
    except Exception:
        data["tick_size"] = 0.0

    try:
        from core.macro_levels import get_3d_profile
        d3 = get_3d_profile(sym_api) or {}
        raw = d3.get("_raw") or d3
        data["ma7_3d"] = raw.get("ma7_3d")
        data["ma25_3d"] = raw.get("ma25_3d")
    except Exception as e:
        logger.debug(f"[SplitEntry] {sym}: lỗi profile 3D: {e}")

    for key, itv, limit in (("klines_15m", "15m", SWING_LOOKBACK_15M),
                            ("klines_4h", "4h", 50),
                            ("klines_1d", "1d", 100)):
        try:
            df = repo.get_klines_df(sym_api, itv, limit)
            data[key] = df if df is not None and not df.empty else None
        except Exception as e:
            logger.debug(f"[SplitEntry] {sym}: lỗi klines {itv}: {e}")
            data[key] = None

    if not _num(data.get("live_price")) and data.get("klines_15m") is not None:
        try:
            data["live_price"] = float(data["klines_15m"]["close"].iloc[-1])
        except Exception:
            pass
    return data
