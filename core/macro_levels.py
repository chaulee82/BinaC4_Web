"""
Module: core/macro_levels.py
Dự án: BinaC4
Mục đích: Trích xuất trọn bộ 4 mốc chiến lược (Entry 4H, TP 1H, TP 4H, SL 4H)
         cho mọi mã dựa trên dữ liệu nến 4H và 1H.

Thiết kế: Pure Function — không tự gọi API, không import engine khác.
         Nhận DataFrame + tham số thuần túy → trả về dict.
         Dễ Unit Test: mớm dữ liệu giả vào mà không cần kết nối mạng.

Cách dùng:
    from core.macro_levels import calculate_universal_macro_levels

    result = calculate_universal_macro_levels(
        klines_4h_df=df_4h,
        klines_1h_df=df_1h,
        tick_size=0.0001,
        sl_atr_multiplier=1.5,   # Từ settings.json
        sl_margin_pct=0.03,       # Fallback nếu ATR không tính được
    )
    # → {"status": "SUCCESS", "entry_4h": ..., "tp_1h": ..., "tp_4h": ..., "sl_4h": ...}
"""

import logging
import pandas as pd
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger("MacroLevels")


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Tính ATR (Average True Range) nội bộ từ DataFrame chuẩn hóa (lowercase cột).
    Trả về 0.0 nếu thiếu dữ liệu.
    """
    try:
        if len(df) < period + 1:
            return 0.0
        high = df['high'].astype(float)
        low  = df['low'].astype(float)
        close = df['close'].astype(float)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low  - close.shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = float(true_range.tail(period).mean())
        return atr if atr > 0 else 0.0
    except Exception:
        return 0.0


def _round_tick(value: float, tick_size: float) -> float:
    """
    Phễu ép kiểu Decimal — làm tròn giá về bội số của tick_size.
    Chặn lỗi PRICE_FILTER của Binance API khi đặt lệnh.
    """
    if tick_size <= 0:
        return round(value, 8)
    dec_tick = Decimal(str(tick_size))
    return float(
        (Decimal(str(value)) / dec_tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * dec_tick
    )


def calculate_universal_macro_levels(
    klines_4h_df: pd.DataFrame,
    klines_1h_df: pd.DataFrame,
    tick_size: float,
    klines_15m_df: pd.DataFrame = None,
    sl_atr_multiplier: float = 1.5,
    sl_margin_pct: float = 0.03,
    symbol: str = None,
) -> dict:
    """
    Trích xuất trọn bộ 4 mốc chiến lược cho mọi mã.

    Thuật toán:
      - entry_4h : Vùng chiết khấu 20% từ đáy hộp Vĩ mô 4H (30 nến ~ 5 ngày)
      - sl_4h    : Đáy vĩ mô − (ATR 4H × sl_atr_multiplier)  [linh hoạt theo biến động]
                  Fallback về đáy × (1 − sl_margin_pct) nếu ATR không tính được
      - tp_1h    : Đỉnh ngắn hạn 1H (24 nến ~ 24 giờ)         [chốt 50% khi lướt sóng]
      - tp_4h    : Đỉnh hộp vĩ mô 4H                          [chốt 100% khi gom đáy]

    Args:
        klines_4h_df      : DataFrame nến 4H (cột lowercase: open, high, low, close, volume)
        klines_1h_df      : DataFrame nến 1H (cột lowercase: open, high, low, close, volume)
        tick_size         : Bước giá tối thiểu từ exchangeInfo PRICE_FILTER
        sl_atr_multiplier : Hệ số ATR cho SL (từ settings.json → macro_levels.sl_atr_multiplier)
        sl_margin_pct     : Fallback SL cứng % từ đáy (từ settings.json → macro_levels.sl_margin_pct)

    Returns:
        dict với keys: status, entry_4h, tp_1h, tp_4h, sl_4h
        hoặc {"status": "error", "message": "..."} nếu thất bại
    """
    try:
        # ── 1. Kiểm tra dữ liệu đầu vào ──────────────────────────────────────
        if klines_4h_df is None or klines_4h_df.empty or len(klines_4h_df) < 5:
            return {"status": "error", "message": "Không đủ dữ liệu nến 4H (cần ≥ 5 nến)"}
        if klines_1h_df is None or klines_1h_df.empty or len(klines_1h_df) < 5:
            return {"status": "error", "message": "Không đủ dữ liệu nến 1H (cần ≥ 5 nến)"}

        klines_4h_df = klines_4h_df.rename(columns=str.lower)
        klines_1h_df = klines_1h_df.rename(columns=str.lower)
        if klines_15m_df is not None:
            klines_15m_df = klines_15m_df.rename(columns=str.lower)

        # ── 2. Quét biên độ Vĩ mô 4H (30 nến ~ 5 ngày) ──────────────────────
        recent_4h = klines_4h_df.tail(30)
        macro_low_4h  = float(recent_4h['low'].min())
        macro_high_4h = float(recent_4h['high'].max())
        box_height    = macro_high_4h - macro_low_4h

        if macro_low_4h <= 0 or box_height <= 0:
            return {"status": "error", "message": f"Biên độ hộp 4H không hợp lệ: low={macro_low_4h}, high={macro_high_4h}"}

        # ── 3. Quét đỉnh Ngắn hạn 1H (24 nến ~ 24 giờ) ──────────────────────
        recent_1h = klines_1h_df.tail(24)
        peak_1h = float(recent_1h['high'].max())

        # ── 4. Tính 4 mốc thô ────────────────────────────────────────────────
        raw_entry_4h = macro_low_4h + (box_height * 0.382)  # Vùng cảnh giới 38.2% từ đáy
        raw_tp_1h    = peak_1h                              # Cản ngắn hạn 24H
        raw_tp_4h    = macro_high_4h                        # Đỉnh hộp vĩ mô 5 ngày

        # ── 4.5. Nội Suy Kỹ Thuật (15M Interpolation) ───────────────────────
        if klines_15m_df is not None and not klines_15m_df.empty and len(klines_15m_df) >= 200:
            close_15m = klines_15m_df['close'].astype(float)
            ema20 = float(close_15m.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(close_15m.ewm(span=50, adjust=False).mean().iloc[-1])
            ma99 = float(close_15m.rolling(99).mean().iloc[-1])
            ma200 = float(close_15m.rolling(200).mean().iloc[-1])
            
            # BB(20, 2)
            ma20 = close_15m.rolling(20).mean()
            std20 = close_15m.rolling(20).std()
            lower_bb = float((ma20 - 2 * std20).iloc[-1])
            
            supports = [ema20, ema50, ma99, ma200, lower_bb]
            valid_supports = [s for s in supports if not pd.isna(s) and s > 0]
            
            if valid_supports:
                closest_support = min(valid_supports, key=lambda x: abs(x - raw_entry_4h))
                dist_pct = abs(closest_support - raw_entry_4h) / raw_entry_4h
                
                # Ngưỡng ±3%
                if dist_pct <= 0.03:
                    logger.debug(f"[MacroLevels] Nội suy 15M: Hút Entry từ {raw_entry_4h} về {closest_support} (sai số {dist_pct:.1%})")
                    raw_entry_4h = closest_support

        # ── 5. SL linh hoạt theo ATR (chống "quét thanh khoản" của đội lái) ──
        atr_4h = _calc_atr(klines_4h_df, period=14)
        if atr_4h > 0:
            # Đặt SL dưới đáy 1 khoảng ATR × hệ số → né râu nến quét thanh khoản
            raw_sl_4h = macro_low_4h - (atr_4h * sl_atr_multiplier)
        else:
            # Fallback: cắt cứng theo % nếu ATR không tính được
            raw_sl_4h = macro_low_4h * (1.0 - sl_margin_pct)
            logger.debug(f"[MacroLevels] ATR=0, fallback SL = đáy × (1 - {sl_margin_pct:.1%})")

        # ── 6. Phễu ép kiểu Decimal (Tick Size Capper) ───────────────────────
        entry_4h = _round_tick(raw_entry_4h, tick_size)
        tp_1h    = _round_tick(raw_tp_1h,    tick_size)
        tp_4h    = _round_tick(raw_tp_4h,    tick_size)
        sl_4h    = _round_tick(raw_sl_4h,    tick_size)

        # ── 7. Sanity check: SL < Entry < TP ─────────────────────────────────
        if not (sl_4h < entry_4h):
            logger.warning(f"[MacroLevels] Cảnh báo: SL({sl_4h}) >= Entry({entry_4h}) — box quá hẹp?")
        if not (entry_4h < tp_1h):
            logger.debug(f"[MacroLevels] TP 1H ({tp_1h}) ≤ Entry ({entry_4h}) — giá đang trên đỉnh ngắn hạn")

        return {
            "status":   "SUCCESS",
            "entry_4h": entry_4h,
            "tp_1h":    tp_1h,
            "tp_4h":    tp_4h,
            "sl_4h":    sl_4h,
            # Hồ sơ Khung 3D (MA/BOLL/ST 3D + Định tuyến chiến thuật) — None nếu không truyền symbol
            "d3":       get_3d_profile(symbol, tick_size) if symbol else None,
            # Metadata debug — không bắt buộc nhưng hữu ích để log
            "_debug": {
                "macro_low_4h":  macro_low_4h,
                "macro_high_4h": macro_high_4h,
                "atr_4h":        round(atr_4h, 8),
                "sl_method":     "ATR" if atr_4h > 0 else "margin_pct",
            }
        }

    except Exception as e:
        logger.error(f"[MacroLevels] Lỗi tính toán: {e}", exc_info=True)
        return {"status": "error", "message": f"Lỗi tính toán Macro: {str(e)}"}


# ═════════════════════════════════════════════════════════════════════════════
# 3D STRATEGY ROUTER — Khung 3 Ngày tổng hợp từ nến 1D trong RAM (0 API call)
# ═════════════════════════════════════════════════════════════════════════════
import time
import threading
import numpy as np

# ── Ngưỡng cấu hình ──────────────────────────────────────────────────────────
WIDE_GRID_LEVELS        = 28
WIDE_GRID_CAPITAL_USDT  = 1000.0
WIDE_GRID_MIN_QVOL_24H  = 12_000_000     # Thanh khoản 24h tối thiểu cho Wide Grid / Cột Cờ Cao
HIGH_FLAG_MIN_DIST_PCT  = 12.0           # Cột cờ: neo cao ≥ 12% trên MA7 3D
BREAKOUT_MAX_DIST_PCT   = 22.0           # Chân sóng: chưa xa MA7 3D quá 22%
EXPLOSIVE_VR_1H         = 2.8
EXPLOSIVE_VR_4H         = 2.2
EXPLOSIVE_VR_3D         = 1.6
BROKEN_MA7_RATIO        = 0.985          # close < MA7 3D × 0.985 → Gãy đường ray
COMBINED_BONUS_CAP      = 30             # Trần thưởng kết hợp (Chân Sóng 3D + Dòng Tiền Bạo Phát) tại DC3/DC4
_PROFILE_TTL            = 600            # Memo profile 3D (giây)

# ── Mã chiến thuật ───────────────────────────────────────────────────────────
STRAT_3D_HIGH_FLAG = "HIGH_FLAG_WIDE_GRID"
STRAT_3D_BREAKOUT  = "BREAKOUT_WAVE"
STRAT_3D_CAPPED    = "OVERHEAD_CAPPED"
STRAT_3D_BROKEN    = "BROKEN_MA7"
STRAT_3D_NEUTRAL   = "NEUTRAL"

STRATEGY_3D_TAGS = {
    STRAT_3D_HIGH_FLAG: "🦅 [3D: CỘT CỜ CAO - WIDE GRID 28L]",
    STRAT_3D_BREAKOUT:  "🚀 [3D: CHÂN SÓNG BỨT PHÁ - SIÊU SÓNG]",
    STRAT_3D_CAPPED:    "⚠️ [3D: ĐỤNG CẢN VĨ MÔ - KHÓA TRẦN TP2]",
    STRAT_3D_BROKEN:    "⛔ [3D: GÃY ĐƯỜNG RAY MA7 3D - CẤM VÀO]",
    STRAT_3D_NEUTRAL:   "➖ [3D: TRUNG TÍNH]",
}

_profile_memo: dict = {}          # sym_api → (timestamp, profile | None)
_profile_lock = threading.Lock()


def _to_api_symbol(symbol: str) -> str:
    """'ARB/USDT' | 'ARBUSDT' | 'ARB' → 'ARBUSDT'"""
    s = (symbol or "").upper().replace("/", "").replace("-", "").strip()
    if s and not s.endswith("USDT"):
        s += "USDT"
    return s


def _norm_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Chuẩn hóa cột lowercase + ép kiểu số cho DataFrame nến (klines_cache / ccxt)."""
    out = df.rename(columns=str.lower).copy()
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def resample_1d_to_3d(klines_1d: pd.DataFrame) -> pd.DataFrame:
    """
    Gộp nến 1D thành nến 3D trong RAM.

    Cụm được cắt từ cây nến MỚI NHẤT lùi về trước → cụm cuối luôn là [t-2, t-1, t].
    Các nến 1D lẻ (n % 3) ở đầu chuỗi (cũ nhất) bị bỏ.
      open  = cluster[0].open      | high  = max(high)   | low = min(low)
      close = cluster[-1].close    | volume / quote_volume = sum
    """
    if klines_1d is None or len(klines_1d) < 3:
        return pd.DataFrame()
    df = _norm_ohlcv(klines_1d)
    rem = len(df) % 3
    df = df.iloc[rem:].reset_index(drop=True)
    groups = np.arange(len(df)) // 3

    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "quote_volume" in df.columns:
        agg["quote_volume"] = "sum"
    if "open_time" in df.columns:
        agg["open_time"] = "first"
    return df.groupby(groups).agg(agg).reset_index(drop=True)


def _supertrend_val_dir(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """SUPERTREND(period, mult) — trả về (giá trị, hướng) của nến cuối. 1 = Xanh, -1 = Đỏ."""
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    close = df["close"].values.astype(np.float64)
    n = len(df)
    if n < period + 2:
        return 0.0, 1

    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr = np.full(n, np.nan, dtype=np.float64)
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    final_upper = np.where(np.isnan(basic_upper), high, basic_upper).copy()
    final_lower = np.where(np.isnan(basic_lower), low, basic_lower).copy()
    direction = np.ones(n, dtype=np.int8)

    for i in range(period, n):
        final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
        if direction[i - 1] == -1:
            direction[i] = 1 if close[i] > final_upper[i] else -1
        else:
            direction[i] = -1 if close[i] < final_lower[i] else 1

    d = int(direction[-1])
    val = float(final_lower[-1]) if d == 1 else float(final_upper[-1])
    return val, d


def compute_3d_indicators(df_3d: pd.DataFrame) -> dict:
    """
    Chỉ báo khung 3D tại cây nến 3D mới nhất.
    Trả về None nếu < 12 nến 3D (không đủ cho Supertrend 10).
    """
    if df_3d is None or len(df_3d) < 12:
        return None
    c = df_3d["close"].astype(float)
    v = df_3d["volume"].astype(float)
    n = len(df_3d)
    close = float(c.iloc[-1])

    ma7 = float(c.rolling(7).mean().iloc[-1])
    ma25 = float(c.rolling(25).mean().iloc[-1]) if n >= 25 else float(c.ewm(span=25, adjust=False).mean().iloc[-1])
    if n >= 99:
        ma99, ma99_src = float(c.rolling(99).mean().iloc[-1]), "MA99"
    else:
        ma99, ma99_src = float(c.ewm(span=50, adjust=False).mean().iloc[-1]), "EMA50"

    win = c.tail(20)
    boll_mid = float(win.mean())
    boll_std = float(win.std(ddof=0))
    boll_up = boll_mid + 2.0 * boll_std
    boll_dn = boll_mid - 2.0 * boll_std

    st_val, st_dir = _supertrend_val_dir(df_3d, period=10, multiplier=3.0)

    cur_vol = float(v.iloc[-1])
    vol_ma5 = float(v.tail(5).mean())
    vol_ma10 = float(v.tail(10).mean())
    vol_ratio = cur_vol / vol_ma5 if vol_ma5 > 0 else 0.0

    dist_ma7 = (close - ma7) / ma7 * 100 if ma7 > 0 else 0.0

    return {
        "close_3d": close,
        "n_3d": n,
        "ma7_3d": ma7, "ma25_3d": ma25, "ma99_3d": ma99, "ma99_src": ma99_src,
        "boll_up_3d": boll_up, "boll_mid_3d": boll_mid, "boll_dn_3d": boll_dn,
        "st_val_3d": st_val, "st_dir_3d": st_dir,
        "vol_3d": cur_vol, "vol_ma5_3d": vol_ma5, "vol_ma10_3d": vol_ma10,
        "vol_ratio_3d": vol_ratio,
        "dist_ma7_3d_pct": dist_ma7,
    }


def _spike_ratio(qv: pd.Series, base_n: int = 20) -> float:
    """Tỷ lệ nổ Volume: max(nến đang chạy, nến vừa đóng) / MA(base_n) các nến trước đó."""
    qv = pd.to_numeric(qv, errors="coerce").dropna()
    if len(qv) < base_n + 2:
        return 0.0
    base = float(qv.iloc[-(base_n + 2):-2].mean())
    if base <= 0:
        return 0.0
    return max(float(qv.iloc[-1]), float(qv.iloc[-2])) / base


def _intraday_flow(df_1h: pd.DataFrame) -> dict:
    """Quote Vol 24h, EMA20 1H, vol_ratio_1h & vol_ratio_4h (4H gộp từ 1H theo mốc UTC)."""
    out = {"quote_vol_24h": 0.0, "ema20_1h": 0.0, "vol_ratio_1h": 0.0, "vol_ratio_4h": 0.0}
    if df_1h is None or len(df_1h) < 25:
        return out
    d = _norm_ohlcv(df_1h)
    qcol = "quote_volume" if "quote_volume" in d.columns else "volume"
    out["quote_vol_24h"] = float(d[qcol].tail(24).sum())
    out["ema20_1h"] = float(d["close"].ewm(span=20, adjust=False).mean().iloc[-1])
    out["vol_ratio_1h"] = _spike_ratio(d[qcol], 20)
    if "open_time" in d.columns:
        ot = pd.to_numeric(d["open_time"], errors="coerce")
        qv_4h = d[qcol].groupby((ot // (4 * 3600 * 1000)).values).sum()
    else:
        qv_4h = d[qcol].groupby(np.arange(len(d))[::-1] // 4).sum().iloc[::-1]
    out["vol_ratio_4h"] = _spike_ratio(qv_4h.reset_index(drop=True), 20)
    return out


def detect_explosive_volume(vol_ratio_1h: float, vol_ratio_4h: float, vol_ratio_3d: float) -> dict:
    """Dòng Tiền Bạo Phát: 1H ≥ 2.8x | 4H ≥ 2.2x | 3D ≥ 1.6x → thưởng +15 / +20 / +25 theo số khung nổ."""
    hits = []
    if vol_ratio_1h >= EXPLOSIVE_VR_1H:
        hits.append(("1H", vol_ratio_1h))
    if vol_ratio_4h >= EXPLOSIVE_VR_4H:
        hits.append(("4H", vol_ratio_4h))
    if vol_ratio_3d >= EXPLOSIVE_VR_3D:
        hits.append(("3D", vol_ratio_3d))
    if not hits:
        return {"is_explosive": False, "bonus": 0, "ratio": 0.0, "frames": "", "tag": ""}
    bonus = {1: 15, 2: 20}.get(len(hits), 25)
    ratio = max(r for _, r in hits)
    frames = "+".join(f for f, _ in hits)
    return {"is_explosive": True, "bonus": bonus, "ratio": ratio, "frames": frames,
            "tag": f"💥 TIỀN BẠO PHÁT ({ratio:.1f}x)"}


def explosive_trigger_price(close: float, ema20_1h: float, boll_up_3d: float, tick_size: float = 0.0) -> float:
    """Trigger đón nhịp nhúng −2.5% → −4.5%: ưu tiên EMA20 1H, kế đến BOLL UP 3D, mặc định −3.5%."""
    hi, lo = close * 0.975, close * 0.955
    for cand in (ema20_1h, boll_up_3d):
        if cand and lo <= cand <= hi:
            return _round_tick(cand, tick_size)
    return _round_tick(close * 0.965, tick_size)


def _build_wide_grid(close: float, ma7: float, boll_up: float, tick_size: float) -> dict:
    """Thông số 🦅 WIDE GRID 3D - 28L (vốn 1.000 USDT)."""
    wide_low = max(ma7 * 1.04, close * 0.82)
    wide_up = max(boll_up * 1.12, close * 1.18)
    wide_trig = min(close * 0.972, boll_up * 0.995)
    # Mã chưa giãn xa MA7 (ma7×1.04 ≥ Trig) → hạ đáy lưới về sát MA7 để lưới vẫn hợp lệ
    if wide_low >= wide_trig:
        wide_low = max(ma7 * 0.99, close * 0.82)
        if wide_low >= wide_trig:
            wide_low = wide_trig * 0.94
    wide_sl = min(ma7 * 0.96, wide_low * 0.95)

    wide_low = _round_tick(wide_low, tick_size)
    wide_up = _round_tick(wide_up, tick_size)
    wide_trig = _round_tick(wide_trig, tick_size)
    wide_sl = _round_tick(wide_sl, tick_size)
    valid = 0 < wide_sl < wide_low < wide_trig < wide_up
    return {
        "valid": valid,
        "wide_low": wide_low, "wide_up": wide_up, "wide_trig": wide_trig, "wide_sl": wide_sl,
        "wide_grids": WIDE_GRID_LEVELS,
        "capital": WIDE_GRID_CAPITAL_USDT,
        "per_grid_usdt": round(WIDE_GRID_CAPITAL_USDT / WIDE_GRID_LEVELS, 2),
    }


def route_3d_strategy(ind: dict, flow: dict, tick_size: float = 0.0) -> dict:
    """
    Bộ Định Tuyến 4 Chiến Thuật 3D — mỗi mã rơi vào ĐÚNG 1 chế độ.

    Thứ tự ưu tiên:
      1. ⛔ Gãy MA7 3D        (an toàn vốn trước tiên)
      2. ⚠️ Đụng cản vĩ mô    (ST 3D đỏ / MA99 3D đè đầu)
      3. 🚀 Chân sóng — nhánh vừa nổ Volume đục BOLL UP 3D (ưu tiên hơn Cột cờ)
      4. 🦅 Cột cờ cao        (dist ≥ 12%, vol 24h ≥ 12M)
      5. 🚀 Chân sóng — nhánh dist 0–22%
      6. ➖ Trung tính
    """
    close = ind["close_3d"]
    ma7, ma25, ma99 = ind["ma7_3d"], ind["ma25_3d"], ind["ma99_3d"]
    st_val, st_dir = ind["st_val_3d"], ind["st_dir_3d"]
    boll_up = ind["boll_up_3d"]
    dist = ind["dist_ma7_3d_pct"]
    qv24 = flow.get("quote_vol_24h", 0.0)
    vr1h = flow.get("vol_ratio_1h", 0.0)

    expl = detect_explosive_volume(vr1h, flow.get("vol_ratio_4h", 0.0), ind["vol_ratio_3d"])
    # Nhánh bứt phá: Vol 3D ≥ 1.6x hoặc Vol 1H ≥ 2.8x đục thủng BOLL UP 3D
    bw_vol_burst = ind["vol_ratio_3d"] >= EXPLOSIVE_VR_3D or vr1h >= EXPLOSIVE_VR_1H
    explosive_break = bw_vol_burst and close > boll_up

    above_ma7 = close > ma7
    is_broken = close < ma7 * BROKEN_MA7_RATIO
    overhead_st = (st_dir == -1 and close < st_val)
    overhead_ma99 = close < ma99 * 0.98
    is_capped = above_ma7 and (overhead_st or overhead_ma99)
    trend_clear = (close > ma7 > ma25) and close > ma99 and st_dir == 1
    is_high_flag = trend_clear and dist >= HIGH_FLAG_MIN_DIST_PCT and qv24 >= WIDE_GRID_MIN_QVOL_24H
    bw_base = above_ma7 and close > ma25 and st_dir == 1 and (close > ma99 or close >= 0.96 * ma99)
    is_breakout = bw_base and ((0.0 <= dist < BREAKOUT_MAX_DIST_PCT) or explosive_break)

    if is_broken:
        strategy = STRAT_3D_BROKEN
    elif is_capped:
        strategy = STRAT_3D_CAPPED
    elif is_breakout and explosive_break:
        strategy = STRAT_3D_BREAKOUT
    elif is_high_flag:
        strategy = STRAT_3D_HIGH_FLAG
    elif is_breakout:
        strategy = STRAT_3D_BREAKOUT
    else:
        strategy = STRAT_3D_NEUTRAL

    res = {
        "strategy_3d": strategy,
        "strategy_tag_3d": STRATEGY_3D_TAGS[strategy],
        "is_3d_high_flag_wide_grid": strategy == STRAT_3D_HIGH_FLAG,
        "is_3d_breakout_wave": strategy == STRAT_3D_BREAKOUT,
        "is_3d_overhead_capped": strategy == STRAT_3D_CAPPED,
        "is_3d_broken_ma7": strategy == STRAT_3D_BROKEN,
        "explosive": expl,
        "hard_sl_3d": _round_tick(ma7 * 0.96, tick_size),
        "score_bonus_3d": 0,
        "ew_exempt_3d": False,
        "overhead_cap_3d": None,
        "overhead_cap_name": "",
        "breakout_state": "",
    }

    if strategy == STRAT_3D_HIGH_FLAG:
        res["score_bonus_3d"] = 15
        res["ew_exempt_3d"] = close > ma7 * 1.10
    elif strategy == STRAT_3D_BREAKOUT:
        if close > boll_up and close > ma99 and bw_vol_burst:
            res["score_bonus_3d"], res["breakout_state"] = 25, "NỔ VOL VƯỢT BOLL UP 3D"
        elif boll_up * 0.95 <= close <= boll_up:
            res["score_bonus_3d"], res["breakout_state"] = 20, "NÉN SÁT BOLL UP 3D"
    elif strategy == STRAT_3D_CAPPED:
        caps = []
        if st_dir == -1 and st_val > 0:
            caps.append(("ST3D", st_val))
        if close < ma99:
            caps.append(("MA99_3D", ma99))
        if caps:
            name, val = min(caps, key=lambda x: x[1])
            res["overhead_cap_3d"] = _round_tick(val, tick_size)
            res["overhead_cap_name"] = name

    # Wide Grid: in cho mọi mã close > MA7 3D & vol 24h ≥ 12M (không chỉ Chiến thuật 1)
    wide_ok = above_ma7 and qv24 >= WIDE_GRID_MIN_QVOL_24H
    res["wide_grid"] = _build_wide_grid(close, ma7, boll_up, tick_size) if wide_ok else None
    return res


# ═════════════════════════════════════════════════════════════════════════════
# BỘ LỌC CỨNG "SIÊU SÓNG 3D" — CỔNG KIỂM DỊCH CHUNG (Universal Gatekeeper)
# Áp dụng cho MỌI lưới: DC1 Darvas / Wide Grid 3D, Grid TP2 (DC2/DC3/DC4), DC5 PingPong.
# Chặn các mã pump & dump bằng Điểm Strict 3D (0–100, 25đ/tầng): ≥ 75đ mới được cấp phép dựng lưới,
# 50–74đ = Sát chuẩn (Watchlist), < 50đ = Loại hẳn.
# ═════════════════════════════════════════════════════════════════════════════
STRICT_3D_TAG_OK       = "🛡️ [3D STRICT ✅]"
STRICT_3D_TAG_WATCH    = "🟡 [3D STRICT ⚠️]"       # Sát chuẩn (50–74đ): Watchlist / thăm dò tỷ trọng thấp
STRICT_3D_TAG_REJECT   = "⛔ [3D STRICT] - NÉ GRID"
STRICT_3D_TAG_FAIL     = "⛔ [3D STRICT]"          # Tag ngắn gắn vào dòng lưới ở từng Động cơ (chi tiết → Bảng Tổng Kết)

# ── Hệ thống Điểm Strict 3D (0–100) ─────────────────────────────────────────
# Mỗi tầng đạt = 25đ. Tầng trượt được "điểm an ủi" tối đa STRICT_3D_PARTIAL_MAX theo tỷ lệ điều kiện con đạt
# → phân hạng trong cùng bậc. Giữ PARTIAL_MAX ≤ 8 để bậc luôn khớp số tầng đạt:
#   4 tầng = 100 | 3 tầng = 75–83 (PASS) | 2 tầng = 50–66 (WATCH) | ≤1 tầng ≤ 49 (REJECT). Đặt 0 → 25đ/tầng thuần.
STRICT_3D_LAYER_PTS     = 25
STRICT_3D_PARTIAL_MAX   = 8
STRICT_3D_PASS_SCORE    = 75     # ≥ 75: Đạt chuẩn → cấp phép tự động dựng lưới mọi Động cơ
STRICT_3D_WATCH_SCORE   = 50     # 50–74: Sát chuẩn → Watchlist / Mã dự phòng; < 50: Loại hẳn
STRICT_3D_VETO_CAP      = 74     # Quyền phủ quyết: nến xả phân phối / Vol vượt thân cờ → khóa trần (tối đa 🟡)
STRICT_3D_TIER_PASS, STRICT_3D_TIER_WATCH, STRICT_3D_TIER_REJECT = "PASS", "WATCH", "REJECT"
STRICT_3D_POLE_LOOKBACK = 10     # Tìm cột cờ trong 10 nến 3D gần nhất (~30 ngày, không tính nến hiện tại)
STRICT_3D_POLE_BASE_N   = 10     # Nền so sánh Vol cột cờ: TB 10 nến 3D trước nó
STRICT_3D_POLE_MIN_VR   = 1.5    # Cột cờ: nến 3D tăng có Vol ≥ 1.5× nền
STRICT_3D_DRYUP_RATIO   = 0.8    # Vol nến 3D hiện tại phải < 80% Vol cột cờ (kiệt cung)
STRICT_3D_DUMP_RATIO    = 0.8    # Nến 3D đỏ sau cột cờ có Vol ≥ 80% cột cờ → Phân phối
STRICT_3D_MAX_RETRACE   = 0.5    # Chỉ cho phép lùi nhẹ: giá giữ ≥ 50% thân cột cờ
STRICT_3D_MIN_PCT_B     = 0.5    # %B BOLL 3D tối thiểu (0.5 = đường giữa MB)


def _strict_ma_series(c: pd.Series):
    """Chuỗi MA7 / MA25 / MA99 khung 3D (fallback EMA khi thiếu nến — đồng bộ compute_3d_indicators)."""
    n = len(c)
    ma7 = c.rolling(7).mean()
    ma25 = c.rolling(25).mean() if n >= 26 else c.ewm(span=25, adjust=False).mean()
    ma99 = c.rolling(99).mean() if n >= 100 else c.ewm(span=50, adjust=False).mean()
    return ma7, ma25, ma99


def strict_3d_tier(score: float) -> str:
    """Quy đổi Điểm Strict 3D → bậc cấp phép."""
    if score >= STRICT_3D_PASS_SCORE:
        return STRICT_3D_TIER_PASS
    if score >= STRICT_3D_WATCH_SCORE:
        return STRICT_3D_TIER_WATCH
    return STRICT_3D_TIER_REJECT


def strict_3d_short_tag(res: dict) -> str:
    """Tag ngắn gắn cuối dòng lưới: `🛡️ [3D STRICT ✅] 81đ (✗T3)` | `🟡 [3D STRICT ⚠️] 58đ (✗T3,T4)` | `⛔ [3D STRICT] 25đ`."""
    res = res or {}
    score = int(res.get("score", 0) or 0)
    tier = res.get("tier") or strict_3d_tier(score)
    head = {STRICT_3D_TIER_PASS: STRICT_3D_TAG_OK, STRICT_3D_TIER_WATCH: STRICT_3D_TAG_WATCH}.get(tier, STRICT_3D_TAG_FAIL)
    failed = res.get("failed_layers") or []
    miss = f" (✗T{',T'.join(str(x) for x in failed)})" if failed and tier != STRICT_3D_TIER_REJECT else ""
    return f"{head} {score}đ{miss}"


def _strict_fail(reason: str) -> dict:
    """Kết quả trượt cổng do thiếu dữ liệu / lỗi (fail-safe = loại)."""
    return {"is_valid": False, "passed": False, "full_pass": False, "score": 0, "tier": STRICT_3D_TIER_REJECT,
            "layer_scores": [0, 0, 0, 0], "checks": {}, "failed_layers": [],
            "reasons": [reason], "tag": f"{STRICT_3D_TAG_REJECT} | {reason}"}


def _evaluate_strict_3d(df_3d: pd.DataFrame, ind: dict) -> dict:
    """
    Kiểm định 4 tầng "Siêu sóng 3D" từ nến 3D thô (đánh giá đủ cả 4 tầng để log lý do loại):
      1. Supertrend 3D xanh và close > ST.
      2. MA7 > MA25 > MA99 (3D) và cả 3 đường dốc lên (so với nến 3D trước).
      3. Kiệt cung sau cột cờ: có nến 3D tăng nổ Vol (cột cờ), sau đó Vol teo lại
         (< 80% cột cờ), không có nến đỏ Vol lớn (phân phối), không lùi sâu > 50% thân cờ.
      4. Giá ở nửa trên BOLL 3D: close > MB và %B ≥ STRICT_3D_MIN_PCT_B.
    """
    try:
        if df_3d is None or ind is None or len(df_3d) < 12:
            return _strict_fail("Thiếu dữ liệu nến 3D")

        c = df_3d["close"].astype(float)
        o = df_3d["open"].astype(float).values
        v = df_3d["volume"].astype(float).values
        cl = c.values
        n = len(c)
        close = float(cl[-1])
        reasons = []
        L1, L2, L3, L4 = (f"Vi phạm Tầng {i} - " for i in range(1, 5))

        # ── Tầng 1: Supertrend 3D xanh, giá nằm trên ST ─────────────────────
        st_green, st_above = ind["st_dir_3d"] == 1, close > ind["st_val_3d"] > 0
        st_ok = st_green and st_above
        st_frac = (st_green + st_above) / 2.0
        if not st_ok:
            reasons.append(L1 + ("Mất Supertrend 3D (đỏ)" if ind["st_dir_3d"] != 1 else "Giá dưới Supertrend 3D"))

        # ── Tầng 2: MA stack bullish + dốc dương ────────────────────────────
        ma7_s, ma25_s, ma99_s = _strict_ma_series(c)
        m7, m25, m99 = float(ma7_s.iloc[-1]), float(ma25_s.iloc[-1]), float(ma99_s.iloc[-1])
        p7, p25, p99 = float(ma7_s.iloc[-2]), float(ma25_s.iloc[-2]), float(ma99_s.iloc[-2])
        stack_ok = bool(m7 > m25 > m99 > 0)
        slopes = {"MA7": m7 > p7, "MA25": m25 > p25, "MA99": m99 > p99}
        slope_ok = all(slopes.values())
        if not stack_ok:
            reasons.append(L2 + "MA 3D không xếp tầng 7>25>99")
        if not slope_ok:
            reasons.append(L2 + "MA 3D dốc xuống (" + ",".join(k for k, ok in slopes.items() if not ok) + ")")
        ma_ok = stack_ok and slope_ok
        ma_frac = (stack_ok + sum(slopes.values())) / 4.0

        # ── Tầng 3: Kiệt cung sau Cột cờ (Volume Profile) ───────────────────
        pole = None
        for i in range(max(1, n - 1 - STRICT_3D_POLE_LOOKBACK), n - 1):
            if cl[i] <= o[i]:
                continue  # Cột cờ phải là nến 3D tăng
            base = v[max(0, i - STRICT_3D_POLE_BASE_N):i]
            if len(base) < 3 or base.mean() <= 0:
                continue
            if v[i] / base.mean() >= STRICT_3D_POLE_MIN_VR and (pole is None or v[i] > v[pole]):
                pole = i

        vol_ok = False
        vol_frac = 0.0                       # Không có cột cờ → 0 điểm an ủi
        veto = False                         # Dấu hiệu pump & dump → phủ quyết cấp phép
        dryup_ratio = None
        pole_age = None
        if pole is None:
            reasons.append(L3 + "Không có thân cờ nổ Vol 3D")
        else:
            pole_vol = float(v[pole])
            pole_age = n - 1 - pole
            post = range(pole + 1, n)
            dryup_ratio = float(v[-1]) / pole_vol if pole_vol > 0 else 9.99
            dry_ok = dryup_ratio < STRICT_3D_DRYUP_RATIO
            exceed = any(v[j] > pole_vol for j in post)
            dump = any(cl[j] < o[j] and v[j] >= pole_vol * STRICT_3D_DUMP_RATIO for j in post)
            hold_lvl = o[pole] + (cl[pole] - o[pole]) * (1.0 - STRICT_3D_MAX_RETRACE)
            hold_ok = close >= hold_lvl
            if not dry_ok:
                reasons.append(L3 + f"Vol chưa cạn ({dryup_ratio:.2f}x thân cờ)")
            if exceed:
                reasons.append(L3 + "Vol vượt thân cờ")
            if dump:
                reasons.append(L3 + f"Nến xả ≥{STRICT_3D_DUMP_RATIO:.0%} Vol thân cờ (phân phối)")
            if not hold_ok:
                reasons.append(L3 + f"Lùi quá {STRICT_3D_MAX_RETRACE:.0%} thân cờ")
            vol_ok = dry_ok and not exceed and not dump and hold_ok
            vol_frac = (dry_ok + (not exceed) + (not dump) + hold_ok) / 4.0
            veto = bool(exceed or dump)

        # ── Tầng 4: Nửa trên dải BOLL 3D ────────────────────────────────────
        up, mid, dn = ind["boll_up_3d"], ind["boll_mid_3d"], ind["boll_dn_3d"]
        pct_b = (close - dn) / (up - dn) if up > dn else 0.0
        boll_ok = close > mid and pct_b >= STRICT_3D_MIN_PCT_B
        if not boll_ok:
            reasons.append(L4 + f"Dưới nửa trên BOLL 3D (%B {pct_b:.2f})")

        boll_frac = min(1.0, max(0.0, pct_b / STRICT_3D_MIN_PCT_B)) if STRICT_3D_MIN_PCT_B > 0 else 0.0

        checks = {"supertrend": st_ok, "ma_stack": ma_ok, "volume_dryup": vol_ok, "boll_upper_half": boll_ok}
        layer_ok = [st_ok, ma_ok, vol_ok, boll_ok]
        fracs = [st_frac, ma_frac, vol_frac, boll_frac]
        layer_scores = [STRICT_3D_LAYER_PTS if ok else int(round(STRICT_3D_PARTIAL_MAX * f))
                        for ok, f in zip(layer_ok, fracs)]
        score = int(sum(layer_scores))
        if veto and score > STRICT_3D_VETO_CAP:
            score = STRICT_3D_VETO_CAP
            reasons.append(f"⛔ Phủ quyết: dấu hiệu phân phối → khóa trần {STRICT_3D_VETO_CAP}đ")
        tier = strict_3d_tier(score)
        full_pass = all(layer_ok)
        passed = tier == STRICT_3D_TIER_PASS                    # ≥ 75đ → cấp phép dựng lưới
        out = {
            "is_valid": passed,
            "passed": passed,                                   # alias tương thích cũ
            "full_pass": full_pass,                             # Đạt đủ cả 4 tầng (100đ)
            "score": score,                                     # 0–100
            "tier": tier,                                       # PASS | WATCH | REJECT
            "veto": veto,                                       # True = bị khóa trần do phân phối
            "layer_scores": layer_scores,
            "checks": checks,
            "failed_layers": [i + 1 for i, ok in enumerate(layer_ok) if not ok],
            "reasons": reasons,
            "dryup_ratio": dryup_ratio,
            "pole_age": pole_age,
            "pct_b": pct_b,
        }
        if full_pass:
            out["tag"] = (f"{STRICT_3D_TAG_OK} {score}đ ST🟢 | MA7>25>99↗ | Vol cạn {dryup_ratio:.2f}x thân cờ "
                          f"({pole_age} nến 3D trước) | %B {pct_b:.2f}")
        elif tier == STRICT_3D_TIER_PASS:
            out["tag"] = f"{STRICT_3D_TAG_OK} {score}đ | " + "; ".join(reasons)
        elif tier == STRICT_3D_TIER_WATCH:
            out["tag"] = f"{STRICT_3D_TAG_WATCH} {score}đ - WATCHLIST | " + "; ".join(reasons)
        else:
            out["tag"] = f"{STRICT_3D_TAG_REJECT} {score}đ | " + "; ".join(reasons)
        return out
    except Exception as e:
        return _strict_fail(f"Lỗi kiểm định 3D: {e}")


def validate_strict_3d_wave(source, ind: dict = None) -> dict:
    """
    CỔNG KIỂM DỊCH CHUNG — dùng cho mọi Động cơ Grid.

    source chấp nhận:
      - dict hồ sơ 3D (d3) từ get_3d_profile()  → dùng kết quả đã tính sẵn (memo, 0 API call)
      - str symbol ('ARB/USDT' | 'ARBUSDT')     → tự lấy hồ sơ 3D
      - DataFrame nến 3D + ind                  → tính trực tiếp (Unit Test)

    Trả về dict: is_valid, score (0–100), checks, failed_layers, reasons ("Vi phạm Tầng N - ..."), tag.
    Thiếu dữ liệu → is_valid=False (fail-safe: né lưới).
    """
    try:
        if isinstance(source, pd.DataFrame):
            return _evaluate_strict_3d(source, ind)
        d3 = source
        if isinstance(source, str):
            d3 = get_3d_profile(source)
        if not d3:
            return _strict_fail("Thiếu dữ liệu nến 3D")
        res = d3.get("strict_3d")
        if res is None and d3.get("symbol"):
            res = (get_3d_profile(d3["symbol"]) or {}).get("strict_3d")
        return res or _strict_fail("Thiếu dữ liệu nến 3D")
    except Exception as e:
        return _strict_fail(f"Lỗi kiểm định 3D: {e}")


def validate_engine_5_strict_3d(d3: dict) -> bool:
    """Cổng vào Động cơ 5: True chỉ khi hồ sơ 3D qua đủ 4 tầng. Thiếu dữ liệu → loại (fail-safe)."""
    return bool(validate_strict_3d_wave(d3).get("is_valid"))


# ── Sổ ghi mã bị loại (dùng chung mọi Động cơ → Bảng Tổng Kết) ────────────────────
_strict_rejections: dict = {}          # sym_clean → {"engines": set, "kinds": set, "reasons": list, "failed_layers": list}
_strict_rej_lock = threading.Lock()


def _clean_sym(symbol: str) -> str:
    s = (symbol or "").upper().replace("/", "").replace("-", "").strip()
    return s[:-4] if s.endswith("USDT") and len(s) > 4 else s


def record_strict_3d_rejection(symbol: str, engine: str, result: dict, kind: str = "GRID") -> None:
    """Ghi nhận 1 mã trượt cổng Strict 3D (gộp theo mã, nhiều Động cơ / loại lưới)."""
    sym = _clean_sym(symbol)
    if not sym:
        return
    with _strict_rej_lock:
        rec = _strict_rejections.setdefault(sym, {"engines": set(), "kinds": set(), "reasons": [], "failed_layers": [],
                                                  "score": 0, "tier": STRICT_3D_TIER_REJECT})
        rec["engines"].add(engine)
        rec["kinds"].add(kind)
        rec["reasons"] = list((result or {}).get("reasons") or ["Thiếu dữ liệu nến 3D"])
        rec["failed_layers"] = list((result or {}).get("failed_layers") or [])
        rec["score"] = int((result or {}).get("score", 0) or 0)
        rec["tier"] = (result or {}).get("tier") or strict_3d_tier(rec["score"])


def get_strict_3d_rejections() -> dict:
    with _strict_rej_lock:
        return {k: {**v, "engines": set(v["engines"]), "kinds": set(v["kinds"])} for k, v in _strict_rejections.items()}


def reset_strict_3d_rejections() -> None:
    with _strict_rej_lock:
        _strict_rejections.clear()


def strict_3d_gate(symbol: str, engine: str, kind: str = "GRID", d3: dict = None) -> dict:
    """Kiểm dịch + tự ghi sổ nếu < 75đ (WATCH / REJECT). Dùng trước khi dựng bất kỳ lưới nào."""
    res = validate_strict_3d_wave(d3 if d3 else symbol)
    if not res.get("is_valid"):
        record_strict_3d_rejection(symbol, engine, res, kind)
    return res


def get_3d_profile(symbol: str, tick_size: float = None) -> dict:
    """
    Hồ sơ 3D đầy đủ của 1 mã (chỉ báo + định tuyến + Wide Grid + dòng tiền).
    Đọc nến từ klines_cache (1D=300, 1H=168) → cache hit = 0 API call. Memo 10 phút.
    Trả về None nếu thiếu dữ liệu.
    """
    sym_api = _to_api_symbol(symbol)
    if not sym_api:
        return None
    now = time.time()
    with _profile_lock:
        hit = _profile_memo.get(sym_api)
        if hit and now - hit[0] < _PROFILE_TTL:
            return hit[1]

    profile = None
    try:
        from core.klines_cache import get_klines_cached
        df_1d = get_klines_cached(sym_api, "1d", limit=300)
        df_1h = get_klines_cached(sym_api, "1h", limit=168)
        df_3d = resample_1d_to_3d(df_1d) if df_1d is not None else None
        ind = compute_3d_indicators(df_3d) if df_3d is not None else None
        if ind is not None:
            if tick_size is None:
                try:
                    from core.exchange_info_cache import ExchangeInfoCache
                    _c = ExchangeInfoCache()
                    tick_size = _c.get_tick_size(sym_api, default=0.0) if _c.is_ready() else 0.0
                except Exception:
                    tick_size = 0.0
            tick_size = tick_size or 0.0
            flow = _intraday_flow(df_1h)
            _price_keys = ("ma7_3d", "ma25_3d", "ma99_3d", "boll_up_3d", "boll_mid_3d", "boll_dn_3d", "st_val_3d")
            profile = {"symbol": sym_api, "tick_size": tick_size}
            profile.update({k: (_round_tick(v, tick_size) if k in _price_keys else v) for k, v in ind.items()})
            profile["_raw"] = ind          # Giá trị thô (chưa làm tròn) cho so sánh chính xác
            profile.update(flow)
            profile.update(route_3d_strategy(ind, flow, tick_size))
            profile["strict_3d"] = _evaluate_strict_3d(df_3d, ind)
    except Exception as e:
        logger.debug(f"[3D] {sym_api}: lỗi tính profile 3D: {e}")
        profile = None

    with _profile_lock:
        _profile_memo[sym_api] = (time.time(), profile)
    return profile


def format_3d_summary(d3: dict, fmt=None) -> str:
    """Chuỗi tóm tắt nối vào cuối dòng [Macro 4H]."""
    if not d3:
        return ""
    f = fmt or (lambda x: f"{x:.8g}")
    st_icon = "🟢" if d3.get("st_dir_3d") == 1 else "🔴"
    ma99_lbl = "MA99" if d3.get("ma99_src") == "MA99" else "EMA50*"
    s = (f" | [3D: MA7={f(d3['ma7_3d'])} {ma99_lbl}={f(d3['ma99_3d'])} BOLL_UP={f(d3['boll_up_3d'])} "
         f"ST={st_icon}{f(d3['st_val_3d'])} -> {d3.get('strategy_tag_3d', '')}]")
    if d3.get("is_3d_overhead_capped") and d3.get("overhead_cap_3d"):
        s += f" [3D: ⚠️ CẢN ĐÈ {d3['overhead_cap_name']}={f(d3['overhead_cap_3d'])} -> Khóa TP]"
    return s


def apply_3d_tp2_cap(tp2_target: float, d3: dict) -> float:
    """Chiến thuật 3: khóa trần TP2 ≤ overhead_cap_3d × 1.015."""
    if d3 and d3.get("is_3d_overhead_capped") and d3.get("overhead_cap_3d") and tp2_target:
        return min(tp2_target, d3["overhead_cap_3d"] * 1.015)
    return tp2_target


def breakout_tp_target(entry: float, sl: float, swing_tp: float, d3: dict) -> float:
    """Chiến thuật 2: mở rộng TP = max(swing, BOLL_UP_3D×1.22, close×1.20, entry + 2.2R)."""
    close = d3.get("close_3d", entry) if d3 else entry
    boll_up = d3.get("boll_up_3d", 0.0) if d3 else 0.0
    return max(swing_tp or 0.0, boll_up * 1.22, close * 1.20, entry + 2.2 * max(entry - sl, 0.0))
