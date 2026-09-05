from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import time
import unicodedata
import pandas as pd
import numpy as np
import logging
from core.grid_calculator import GridCalculator
from core.api_client import BinanceClient

from strategies.macro_grid_darvas import MacroGridDarvas

_shared_darvas = None

def _get_shared_darvas():
    global _shared_darvas
    if _shared_darvas is None:
        _shared_darvas = MacroGridDarvas()
    return _shared_darvas

# 1. Danh sách ưu tiên theo dõi cố định + Mở rộng TOP 50 tự động
MANUAL_SYMBOLS = ["KAITOUSDT", "ENAUSDT", "ZAMAUSDT", "REUSDT", "UNIUSDT", "TUTUSDT", "ADAUSDT", "FILUSDT", "ONDOUSDT"]
TOP_AUTO_COUNT = 200

# Danh sách Large-Cap ưu tiên cho chế độ Tích Lũy 2-3 Ngày
LARGECAP_SYMBOLS = ["SOLUSDT", "ETHUSDT", "NEARUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT", "BNBUSDT", "DOTUSDT"]
LARGECAP_MIN_VOL = 50_000_000  # 50 triệu USDT — ngưỡng Large-Cap

# ── Bảng 4: Momentum Breakout — Scalping Grid 5 Lưới TP 2% ───────────────
MOM_VOL_SPIKE_MULT = 2.5    # Vol nến (-1) ≥ 2.5× avg(20 nến 15M trước)
MOM_RSI_LOW        = 60.0   # RSI 1H tối thiểu (còn lực đẩy)
MOM_RSI_HIGH       = 72.0   # RSI 1H tối đa (chưa quá mua)
MOM_MAX_UPPER_WICK = 0.30   # Râu trên ≤ 30% thân nến (-1) — chống Bull Trap
MOM_MAX_EMA7_GAP   = 3.0    # Giá Live không cách EMA7(15M) quá 3%
MOM_MIN_TAKER_BUY  = 65.0   # Taker Buy ≥ 65% vol nến (-1) — mua chủ động
MOM_MIN_VOL_USDT   = 2_000_000   # Vol 24h tối thiểu để vào pool quét
MOM_TOP_N          = 8      # Hiển thị Top N kết quả

# Cấu hình Bảng 3 - Bắt sớm nền tăng
EARLY_MIN_VOL_USDT = 2_000_000  # Vol 24h >= 2 triệu USDT
EARLY_MAX_VOL_USDT = 50_000_000 # Lọc bỏ Large Cap (> 50M USDT) để dễ x2, x3
EARLY_MIN_SWING_4H = 4.5         # Biên độ giật 4H >= 4.5%
EARLY_MAX_SWING_4H = 9.5         # Biên độ giật 4H <= 9.5%
EARLY_MIN_WICK_CNT = 5           # Tối thiểu 5/20 nến 15M rút chân đẹp (Tăng độ uy tín)
EARLY_MAX_UPPER_W  = 0.25        # Râu trên <= 25% (Siết chặt hơn)
EARLY_MIN_7D_CHG   = -2.0        # Cho phép sideway / vừa đảo chiều
EARLY_MIN_24H_CHG  = -3.0        # Tránh bắt dao rơi (không bắt mã đang xả mạnh trong ngày)

EXCLUDE = ["UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "DAIUSDT", "EURUSDT"]

def calculate_trend_score(close_price, ma25_price):
    if close_price < ma25_price:
        return 0.0

    diff_pct = ((close_price - ma25_price) / ma25_price) * 100

    if diff_pct <= 2.0:
        return 80.0 + (diff_pct / 2.0) * 20.0          # Scale: 80 -> 100
    elif 2.0 < diff_pct <= 5.0:
        return 100.0 - ((diff_pct - 2.0) / 3.0) * 35.0 # Scale: 100 -> 65
    elif 5.0 < diff_pct <= 10.0:
        return 65.0 - ((diff_pct - 5.0) / 5.0) * 40.0  # Scale: 65 -> 25
    else:
        return max(0.0, 25.0 - (diff_pct - 10.0) * 2.5) # Phạt mạnh

def calculate_rsi_score(rsi_val):
    if rsi_val < 30:
        return 65.0
    elif rsi_val <= 40:
        return 65.0 + ((rsi_val - 30) / 10.0) * 20.0   # 65 → 85
    elif rsi_val <= 65:
        return 85.0 + ((rsi_val - 40) / 25.0) * 15.0   # 85 → 100
    elif rsi_val <= 75:
        return 100.0 - ((rsi_val - 65) / 10.0) * 50.0  # 100 → 50
    else:
        return max(0.0, 50.0 - (rsi_val - 75) * 2.0)   # Phạt mạnh


def calculate_rsi(df, period=14):
    closes = pd.to_numeric(df['Close'], errors='coerce').dropna()
    if len(closes) < period + 1:
        return 50.0

    deltas = closes.diff()
    gains = deltas.mask(deltas < 0, 0)
    losses = -deltas.mask(deltas > 0, 0)

    avg_gain = gains.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = losses.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, 0.000001)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0

_klines_cache = {}
_CACHE_TTL = 60  # 60 giây (đủ cho 1 vòng quét toàn diện mà không lấy dữ liệu cũ)

def get_klines_live(symbol, interval, limit=100):
    global _klines_cache
    now = time.time()
    cache_key = (symbol, interval)
    
    # Trả về data từ cache nếu còn hạn và đủ số lượng nến
    if cache_key in _klines_cache:
        cached_time, df = _klines_cache[cache_key]
        if now - cached_time < _CACHE_TTL and len(df) >= limit:
            return df.tail(limit).copy()

    time.sleep(0.02)
    client = BinanceClient()
    
    # Tối ưu: Lấy dư một chút ở lần đầu để phục vụ cho các module sau yêu cầu limit lớn hơn
    fetch_limit = limit
    if interval == '15m': fetch_limit = max(limit, 100)
    elif interval == '1h': fetch_limit = max(limit, 168)
    elif interval == '4h': fetch_limit = max(limit, 120)
    elif interval == '1d': fetch_limit = max(limit, 180)

    data = client.get(f"/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": fetch_limit})
    if isinstance(data, list) and len(data) > 0:
        df = pd.DataFrame(data, columns=[
            'Open_Time', 'Open', 'High', 'Low', 'Close', 'Volume',
            'Close_Time', 'Quote_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
        ])
        for c in ['Open', 'High', 'Low', 'Close', 'Quote_Volume', 'Taker_Buy_Quote']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
            
        # Lưu vào cache
        _klines_cache[cache_key] = (now, df)
        
        # Dọn dẹp cache rác nếu phình to (vd > 1000 items)
        if len(_klines_cache) > 1000:
            keys_to_delete = [k for k, (t, _) in _klines_cache.items() if now - t > _CACHE_TTL]
            for k in keys_to_delete:
                del _klines_cache[k]
                
        return df.tail(limit).copy()
    return None

def check_higher_lows_4h(df_4h, lookback=6):
    lows = df_4h['Low'].tail(lookback).values
    hl_count = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i-1])
    return hl_count >= max(1, (lookback - 1) // 2)

def check_ma25_slope_4h(df_4h):
    if len(df_4h) < 27:
        return False
    ma25_curr = float(df_4h['Close'].tail(25).mean())
    ma25_prev = float(df_4h['Close'].iloc[-26:-1].mean())
    return ma25_curr > ma25_prev

def process_symbol(symbol, live_info):
    if not live_info: return None

    df_15m = get_klines_live(symbol, "15m", limit=100)
    df_1h = get_klines_live(symbol, "1h", limit=168)
    df_4h = get_klines_live(symbol, "4h", limit=120)
    df_1d = get_klines_live(symbol, "1d", limit=30)

    if df_15m is None or df_1h is None or df_4h is None or df_1d is None: return None

    close_live = live_info['last_price']

    df_1h['MA25'] = df_1h['Close'].rolling(window=25).mean()
    ma7_1h  = float(df_1h['Close'].tail(7).mean())
    ema7_1h = float(df_1h['Close'].ewm(span=7, adjust=False).mean().iloc[-1])
    ma25_1h = df_1h['Close'].tail(25).mean()
    rsi_1h = calculate_rsi(df_1h, period=14)
    score_rsi = calculate_rsi_score(rsi_1h)

    rsi_label = ""
    if rsi_1h < 30:
        rsi_label = f"⚠️ RSI 1H Quá Bán ({rsi_1h:.1f}) - Cảnh Giác Rebound"
    elif rsi_1h >= 75:
        rsi_label = f"⚠️ RSI 1H Quá Mua ({rsi_1h:.1f}) - Rủi Ro Đu Đỉnh"
    elif 40 <= rsi_1h <= 65:
        rsi_label = f"🟢 RSI 1H Lý Tưởng Grid ({rsi_1h:.1f})"

    close_4h = df_4h['Close'].iloc[-1]
    ma25_4h = df_4h['Close'].tail(25).mean()

    _is_4h_broken_early = close_4h < ma25_4h
    is_higher_lows_4h   = check_higher_lows_4h(df_4h, lookback=6)
    is_4h_upslope       = check_ma25_slope_4h(df_4h)
    mode_tich_luy       = is_higher_lows_4h and is_4h_upslope and not _is_4h_broken_early

    # --- Early Warning inline check (dùng close_live ticker realtime + data 1H sẵn có)
    # Tránh timing gap: close_live luôn mới hơn close của nến 1H cuối trong df
    _bb_window   = df_1h['Close'].iloc[-21:-1]  # 20 nến trước
    _bb_ma20     = float(_bb_window.mean()) if len(_bb_window) == 20 else ma25_1h
    _bb_std      = float(_bb_window.std(ddof=1)) if len(_bb_window) == 20 else 0
    _bb_lower    = _bb_ma20 - 2.0 * _bb_std
    _avg_vol_20  = float(df_1h['Quote_Volume'].tail(20).mean()) if 'Quote_Volume' in df_1h.columns else 0
    _cur_vol_1h  = float(df_1h['Quote_Volume'].iloc[-1]) if 'Quote_Volume' in df_1h.columns else 0

    if close_live < _bb_lower and _cur_vol_1h > _avg_vol_20:
        _ew_level   = 2
        _ew_trigger = "Xuyên thủng Dải Đáy BB(20) kèm Volume xả"
    elif close_live < ma7_1h and close_live < ema7_1h:
        _ew_level   = 1
        _ew_trigger = "Giá cắt xuống dưới cụm MA(7) & EMA(7)"
    else:
        _ew_level   = 0
        _ew_trigger = ""

    # Nếu CẤP 1 trở lên: tắt mode tích lũy — không thể tích lũy khi cấu trúc MA ngắn hạn bị phá
    if _ew_level >= 1 and mode_tich_luy:
        mode_tich_luy = False

    close_1d = df_1d['Close'].iloc[-1]
    ma25_1d = df_1d['Close'].tail(25).mean()
    ma50_1d = float(df_1d['Close'].tail(50).mean()) if len(df_1d) >= 50 else float(ma25_1d)
    
    rsi_1d = calculate_rsi(df_1d, period=14)

    # Cấu trúc Higher Lows khung 1D (tích lũy dài hạn) — dùng lại hàm sẵn có
    _is_hl_1d      = check_higher_lows_4h(df_1d, lookback=5)
    _is_1d_upslope = check_ma25_slope_4h(df_1d)
    if _is_hl_1d and _is_1d_upslope and close_1d >= float(ma25_1d):
        score_structure_1d = 100.0   # Cấu trúc tăng hoàn hảo trên 1D
    elif _is_hl_1d and _is_1d_upslope:
        score_structure_1d = 70.0    # HL + slope tốt nhưng dưới MA25
    elif _is_hl_1d or _is_1d_upslope:
        score_structure_1d = 40.0    # Một trong hai điều kiện
    else:
        score_structure_1d = 0.0     # Không có cấu trúc
    tr1 = df_1d['High'] - df_1d['Low']
    tr2 = abs(df_1d['High'] - df_1d['Close'].shift(1))
    tr3 = abs(df_1d['Low'] - df_1d['Close'].shift(1))
    df_1d_tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_1d = float(df_1d_tr.tail(14).mean())

    df_4h['Volatility_%'] = ((df_4h['High'] - df_4h['Low']) / df_4h['Low']) * 100
    avg_vola_4h = df_4h['Volatility_%'].tail(12).mean()

    df_1d['Volatility_1D_%'] = ((df_1d['High'] - df_1d['Low']) / df_1d['Low']) * 100
    vola_1d = df_1d['Volatility_1D_%'].iloc[-1]

    body_top_30d = df_1d[['Open', 'Close']].max(axis=1)
    upper_wicks = ((df_1d['High'] - body_top_30d) / body_top_30d) * 100
    max_wick_30d = upper_wicks.max()

    if mode_tich_luy:
        buy_target_price = ma25_4h * 1.005
    elif close_live > ma7_1h and ma7_1h > ma25_1h:
        buy_target_price = ma7_1h
    elif close_live > ma25_1h:
        buy_target_price = ma25_1h
    else:
        buy_target_price = ma25_4h

    discount_pct = ((close_live - buy_target_price) / close_live) * 100

    vol_usdt_24h = live_info['quote_vol']
    score_vol = min(100.0, 30.0 + (vol_usdt_24h / 30_000_000.0) * 70.0)

    if avg_vola_4h < 2.0:
        score_vola_4h = 40.0 + (avg_vola_4h / 2.0) * 40.0
    elif 2.0 <= avg_vola_4h <= 6.0:
        score_vola_4h = 80.0 + ((avg_vola_4h - 2.0) / 4.0) * 20.0 
    else:
        score_vola_4h = max(20.0, 100.0 - ((avg_vola_4h - 6.0) / 4.0) * 60.0)

    if vola_1d < 4.0:
        score_vola_1d = 40.0 + (vola_1d / 4.0) * 40.0
    elif 4.0 <= vola_1d <= 10.0:
        score_vola_1d = 80.0 + ((vola_1d - 4.0) / 6.0) * 20.0
    else:
        score_vola_1d = max(20.0, 100.0 - ((vola_1d - 10.0) / 5.0) * 50.0)

    score_trend_1d = calculate_trend_score(close_1d, ma25_1d)
    score_trend_4h = calculate_trend_score(close_4h, ma25_4h)
    # Dùng nến 1H đã đóng (iloc[-2]) thay giá live để tránh nhiễu giữa nến
    close_1h_for_trend = df_1h['Close'].iloc[-2] if len(df_1h) >= 2 else df_1h['Close'].iloc[-1]
    score_trend_1h = calculate_trend_score(close_1h_for_trend, ma25_1h)
    avg_trend_score = (score_trend_1d * 0.40) + (score_trend_4h * 0.40) + (score_trend_1h * 0.20)

    taker_buy_vol_3d = df_1d['Taker_Buy_Quote'].tail(3).sum()
    total_quote_vol_3d = df_1d['Quote_Volume'].tail(3).sum()
    taker_ratio = (taker_buy_vol_3d / total_quote_vol_3d * 100) if total_quote_vol_3d > 0 else 50.0
    
    score_flow = max(0.0, min(100.0, (taker_ratio - 45.0) / 10.0 * 100.0))
    score_giat = (score_vola_4h * 0.667 + score_vola_1d * 0.333)

    last_15m = df_15m.iloc[-1]
    prev_15m = df_15m.iloc[-2] if len(df_15m) >= 2 else last_15m

    open_15m, close_15m = last_15m['Open'], last_15m['Close']
    high_15m, low_15m = last_15m['High'], last_15m['Low']
    total_len_15m = high_15m - low_15m if high_15m > low_15m else 0.000001

    body_bottom_15m = min(open_15m, close_15m)
    body_top_15m = max(open_15m, close_15m)

    lower_wick_15m = body_bottom_15m - low_15m
    upper_wick_15m = high_15m - body_top_15m

    lower_wick_pct_15m = (lower_wick_15m / total_len_15m) * 100
    upper_wick_pct_15m = (upper_wick_15m / total_len_15m) * 100

    vol_15m = last_15m['Quote_Volume']
    avg_vol_15m_10 = df_15m['Quote_Volume'].tail(10).mean()

    is_engulfing_bear = (prev_15m['Close'] > prev_15m['Open']) and \
                         (close_15m < open_15m) and \
                         (open_15m >= prev_15m['Close']) and \
                         (close_15m <= prev_15m['Open'])

    # score_nen_15m: trung bình 3 nến đã đóng gần nhất — giảm noise từ 1 nến đơn lẻ
    _nen_scores = []
    for _ni in range(-4, -1):  # nến -4, -3, -2 (đã đóng chắc chắn)
        try:
            _c   = df_15m.iloc[_ni]
            _ttl = _c['High'] - _c['Low'] if _c['High'] > _c['Low'] else 0.000001
            _lw  = (min(_c['Open'], _c['Close']) - _c['Low'])  / _ttl * 100
            _uw  = (_c['High'] - max(_c['Open'], _c['Close'])) / _ttl * 100
            _nen_scores.append(min(100.0, 50.0 + _lw * 0.7) if _lw > _uw else max(0.0, 50.0 - _uw * 0.9))
        except IndexError:
            pass
    score_nen_15m = sum(_nen_scores) / len(_nen_scores) if _nen_scores else 50.0

    warning_reasons = []
    # --- Hiển thị cảnh báo Early Warning ngay trong bảng Rebalance ---
    if _ew_level >= 3:
        warning_reasons.append(f"💀 EW CẤP 3: {_ew_trigger}")
    elif _ew_level == 2:
        warning_reasons.append(f"🛑 EW CẤP 2: {_ew_trigger}")
    elif _ew_level == 1:
        warning_reasons.append(f"⚠️ EW CẤP 1: {_ew_trigger}")

    if rsi_label:
        warning_reasons.append(rsi_label)

    if lower_wick_pct_15m >= 45.0 and vol_15m >= avg_vol_15m_10 * 0.8:
        warning_reasons.append(f"🟢 Rút chân xịn 15M ({lower_wick_pct_15m:.0f}%)")
    elif close_15m > open_15m and upper_wick_pct_15m < 20.0:
        warning_reasons.append("🟢 Nến 15M xanh đẹp")
    elif upper_wick_pct_15m >= 50.0 and upper_wick_pct_15m > lower_wick_pct_15m * 1.5 and vol_15m >= avg_vol_15m_10 * 0.8:
        warning_reasons.append(f"⚠️ Râu trên xả 15M ({upper_wick_pct_15m:.0f}%)")
    elif close_15m < open_15m and lower_wick_pct_15m < 15.0:
        warning_reasons.append("🔴 Nến 15M xả thượt")
        
    # Màng lọc Đa khung: Nến 1H
    last_1h = df_1h.iloc[-1]
    open_1h, close_1h = last_1h['Open'], last_1h['Close']
    # Dùng nến 1H đã đóng gần nhất (iloc[-2]) để tránh nhiễu giữa nến đang mở
    close_1h_confirmed = df_1h['Close'].iloc[-2] if len(df_1h) >= 2 else close_1h
    high_1h, low_1h = last_1h['High'], last_1h['Low']
    total_len_1h = high_1h - low_1h if high_1h > low_1h else 0.000001
    body_top_1h = max(open_1h, close_1h)
    body_bottom_1h = min(open_1h, close_1h)
    
    upper_wick_1h = high_1h - body_top_1h
    lower_wick_1h = body_bottom_1h - low_1h
    
    upper_wick_pct_1h = (upper_wick_1h / total_len_1h) * 100
    lower_wick_pct_1h = (lower_wick_1h / total_len_1h) * 100
    
    vol_1h = last_1h['Quote_Volume']
    avg_vol_1h_10 = df_1h['Quote_Volume'].tail(10).mean() if len(df_1h) >= 10 else 0
    
    if upper_wick_pct_1h >= 40.0 and upper_wick_pct_1h > lower_wick_pct_1h * 1.5 and vol_1h >= avg_vol_1h_10 * 0.8:
        warning_reasons.append(f"⚠️ Râu trên xả 1H ({upper_wick_pct_1h:.0f}%)")
        
    if is_engulfing_bear:
        score_nen_15m = min(score_nen_15m, 20.0)
        warning_reasons.append("⚠️ Bearish Engulfing 15M")

    df_15m_24h = df_15m.tail(96)
    bodies_bottom = df_15m_24h[['Open', 'Close']].min(axis=1)
    bodies_top = df_15m_24h[['Open', 'Close']].max(axis=1)

    lowers = bodies_bottom - df_15m_24h['Low']
    uppers = df_15m_24h['High'] - bodies_top
    totals = df_15m_24h['High'] - df_15m_24h['Low']
    totals = totals.replace(0, 0.000001)

    lower_pcts = (lowers / totals) * 100
    upper_pcts = (uppers / totals) * 100

    sum_lower_wick_pct = lower_pcts.sum()
    sum_upper_wick_pct = upper_pcts.sum()
    net_wick_balance_24h = (sum_lower_wick_pct - sum_upper_wick_pct) / 96.0

    score_rau_24h = max(0.0, min(100.0, 50.0 + (net_wick_balance_24h * 2.5)))

    if net_wick_balance_24h >= +15.0:
        warning_reasons.append(f"🟢 Net Bắt Đáy 24H Dày (+{net_wick_balance_24h:.1f}%)")
    elif +5.0 <= net_wick_balance_24h < +15.0:
        warning_reasons.append(f"🟢 Net Rút Chân Dương (+{net_wick_balance_24h:.1f}%)")
    elif -15.0 <= net_wick_balance_24h <= -5.0:
        warning_reasons.append(f"⚠️ Net Giật Râu Âm (-{abs(net_wick_balance_24h):.1f}%)")
    elif net_wick_balance_24h < -15.0:
        warning_reasons.append(f"⚠️ Chuyên Úp Bô Xả Đỉnh (-{abs(net_wick_balance_24h):.1f}%)")

    df_1h['drop_1h_%'] = ((df_1h['High'] - df_1h['Low']) / df_1h['High']) * 100
    max_dump_7d = df_1h['drop_1h_%'].tail(168).max() if len(df_1h) >= 168 else 0.0
    # Nới lỏng từ 7.0% lên 10.0% vì altcoin giật 10% trong 1H là bình thường và tốt cho Grid
    is_long_term_dump_history = (max_dump_7d > 10.0)

    # Dùng nến 1H đã đóng (confirmed) thay giá live để tránh nhiễu giữa nến đang mở
    is_1h_broken = close_1h_confirmed < ma25_1h
    # Nới lỏng buffer từ 0.3% lên 1.0% (0.990) để tránh quét râu giả trên nến 4H
    is_4h_broken = close_4h < (ma25_4h * 0.990)
    penalty = 0.0

    recent_high_1h = df_1h['High'].tail(3).max()
    drop_from_1h_high = ((recent_high_1h - close_live) / recent_high_1h) * 100 if recent_high_1h > 0 else 0

    recent_10_candles = df_1h.tail(10)
    times_broken_1h = len(recent_10_candles[recent_10_candles['Close'] < recent_10_candles['MA25']])

    if times_broken_1h >= 3:
        penalty += 1.0
        warning_reasons.append(f"Nhiễu sóng ({times_broken_1h} lần/10 nến)")

    depth_1h_pct = 0.0
    if is_1h_broken:
        depth_1h_pct = ((ma25_1h - close_live) / ma25_1h) * 100
        if depth_1h_pct <= 1.0:
            penalty += 1.0
            warning_reasons.append(f"Lủng 1H Nhẹ (-{depth_1h_pct:.2f}%)")
        elif depth_1h_pct <= 3.5:
            penalty += 2.5
            warning_reasons.append(f"Gãy 1H (-{depth_1h_pct:.2f}%)")
        else:
            penalty += 5.0
            warning_reasons.append(f"XẢ MẠNH 1H (-{depth_1h_pct:.2f}%)")

    if is_4h_broken:
        depth_4h_pct = ((ma25_4h - close_4h) / ma25_4h) * 100
        if depth_4h_pct <= 1.5:
            penalty += 2.0
            warning_reasons.append(f"Lủng 4H Nhẹ (-{depth_4h_pct:.2f}%)")
        else:
            penalty += 6.0
            warning_reasons.append(f"XẢ MẠNH 4H (-{depth_4h_pct:.2f}%)")

    drops_15m_10d = len(df_15m[((df_15m['High'] - df_15m['Low']) / df_15m['High'] * 100) >= 3.0])

    if is_4h_broken:
        trang_thai = "🔴 DOWNTREND (Rủi Ro)"
    elif is_1h_broken:
        if mode_tich_luy:
            trang_thai = "🛡️ ĐIỀU CHỈNH NGẮN (Cơ Hội Gom)"
        else:
            trang_thai = "⚠️ CHỈNH NGẮN HẠN (Đợi Nền)"
    else:
        if drop_from_1h_high > 2.5:
            warning_reasons.append(f"Chỉnh -{drop_from_1h_high:.1f}% Đỉnh 1H")

        if mode_tich_luy:
            trang_thai = "🌊 TÍCH LŨY BỀN VỮNG (2-3 Ngày)"
        elif avg_trend_score >= 80.0:
            trang_thai = "🚀 UPTREND MẠNH (Leader)"
        elif avg_trend_score > 40.0:
            trang_thai = "🟢 ĐANG TĂNG / PHỤC HỒI"
        else:
            trang_thai = "🔴 DOWNTREND (Rủi Ro)"

    if max_wick_30d >= 15.0:
        warning_reasons.append(f"⚠️ Giật Râu 30D ({max_wick_30d:.1f}%)")

    price_7d_ago = df_1d['Close'].iloc[-7] if len(df_1d) >= 7 else df_1d['Close'].iloc[0]
    change_7d_pct = ((close_live - price_7d_ago) / price_7d_ago) * 100

    consecutive_green = 0
    for i in range(len(df_1h)-1, -1, -1):
        if df_1h['Close'].iloc[i] > df_1h['Open'].iloc[i]:
            consecutive_green += 1
        else:
            break

    consecutive_red = 0
    for i in range(len(df_1h)-1, -1, -1):
        if df_1h['Close'].iloc[i] < df_1h['Open'].iloc[i]:
            consecutive_red += 1
        else:
            break

    # score_entry: Phân biệt “điều chỉnh lành mạnh trên MA25” vs “đang sập dưới MA25”
    # Nến đỏ TRÊN MA25 = cơ hội mua thực sự (contrarian). Nến đỏ DƯỚI MA25 = đang sập → phạt
    if not is_1h_broken:
        if consecutive_red > 0:
            score_entry_base = 50.0 + min(consecutive_red * 10.0, 35.0)  # Tối đa 85
        elif consecutive_green > 0:
            score_entry_base = 50.0 - min(consecutive_green * 8.0, 30.0)  # Tối thiểu 20
        else:
            score_entry_base = 50.0
    else:
        # Dưới MA25: nến đỏ liên tiếp = đang sập → phạt mạnh
        if consecutive_red > 0:
            score_entry_base = max(5.0, 50.0 - consecutive_red * 12.0)
        elif consecutive_green > 0:
            # Xanh dưới MA25 = cố hồi → trung bình
            score_entry_base = 50.0
        else:
            score_entry_base = 30.0

    mod_7d = 0.0
    if -20.0 <= change_7d_pct <= -3.0:
        mod_7d = +20.0
        warning_reasons.append(f"🟢 Chiết khấu 7D đẹp ({change_7d_pct:+.1f}%)")
    elif change_7d_pct > 25.0:
        mod_7d = -25.0
        warning_reasons.append(f"⚠️ Đu đỉnh 7D (+{change_7d_pct:.1f}%)")

    score_entry = min(100.0, max(0.0, score_entry_base + mod_7d))

    if is_4h_broken:
        phan_loai_grid = "⛔ NÉ GRID (Gãy Cấu Trúc 4H)"
    elif is_long_term_dump_history:
        phan_loai_grid = "⛔ NÉ GRID (Tiền Sử Xả Dốc 7D)"
        warning_reasons.append(f"⚠️ SAFETY GATE: Tiền Sử Xả Dốc 7D: -{max_dump_7d:.1f}% ")
    # Nới lỏng ngưỡng điểm râu từ <= 30.0 xuống <= 20.0 (chấp nhận râu trên dài hơn một chút)
    elif score_rau_24h <= 20.0:
        phan_loai_grid = "⛔ NÉ GRID (Bơm Xả Râu Dài)"
    elif is_1h_broken and not mode_tich_luy:
        phan_loai_grid = "⛔ NÉ GRID (Cạn Cầu / Trượt Giá)"
    else:
        if mode_tich_luy:
            phan_loai_grid = "🌊 GRID TÍCH LŨY (24 Lưới / 2-3 Ngày)"
            hl_tag  = "HL✅" if is_higher_lows_4h else "HL❌"
            sl_tag  = "MA↑" if is_4h_upslope else "MA→"
            warning_reasons.append(f"🌊 4H Tích Lũy: {hl_tag} + {sl_tag} → Kệ Sóng Nhiễu 1H")
        elif avg_vola_4h >= 4.5 or change_7d_pct > 25.0:
            phan_loai_grid = "🔥 GRID RỘNG (16 Lưới)"
        else:
            phan_loai_grid = "🛡️ GRID HẸP (12 Lưới)"

    # Trọng số cập nhật: trend 18%, nen 10%, râu 14%, flow 14%, giật 10%, vol 10%, entry 12%, rsi 8%, structure1d 4%
    total_score_raw = (avg_trend_score    * 0.18) + (score_nen_15m      * 0.10) + \
                      (score_rau_24h      * 0.14) + (score_flow          * 0.14) + \
                      (score_giat         * 0.10) + (score_vol           * 0.10) + \
                      (score_entry        * 0.12) + (score_rsi           * 0.08) + \
                      (score_structure_1d * 0.04)

    # Hình phạt mềm (Soft Penalty) — phân cấp theo mức độ nguy hiểm thực sự
    if "Gãy Cấu Trúc 4H" in phan_loai_grid:
        total_score_raw -= 40.0   # Phá cấu trúc nghiêm trọng nhất
    elif "Bơm Xả Râu Dài" in phan_loai_grid:
        total_score_raw -= 35.0   # Bơm xả rô rệt
    elif "Tiền Sử Xả Dốc 7D" in phan_loai_grid:
        # Nới lỏng nếu mã đang hồi phục từ đáy (4H chưa gãy, 7D bắt đầu dương)
        if change_7d_pct > 0 and not is_4h_broken and avg_trend_score >= 40:
            total_score_raw -= 20.0  # Đang hồi phục → ít phạt hơn
        else:
            total_score_raw -= 35.0  # Tiền sử xấu và chưa hồi
    elif "Cạn Cầu / Trượt Giá" in phan_loai_grid:
        total_score_raw -= 30.0
    elif "⛔" in phan_loai_grid:
        total_score_raw -= 30.0   # Các lý do khác
    elif "🔴 DOWNTREND" in trang_thai:
        total_score_raw -= 20.0

    total_score = round(max(0.0, min(100.0, total_score_raw - penalty * 3.0)), 1)

    diff_from_ma25_pct = ((close_live - ma25_1h) / ma25_1h) * 100
    if close_live >= ma25_1h:
        if diff_from_ma25_pct <= 2.0:
            warning_reasons.append("🟢 Sát nền Mua Limit (Tăng Trên MA25)")
        elif diff_from_ma25_pct > 5.0:
            warning_reasons.append(f"⚠️ Tăng nóng xa MA25 (+{diff_from_ma25_pct:.1f}%)")

    warning_str = "  •  ".join(warning_reasons) if warning_reasons else "An Toàn"

    _is_grid_ok  = phan_loai_grid in (
        "🛡️ GRID HẸP (12 Lưới)", "🔥 GRID RỘNG (16 Lưới)",
        "🌊 GRID TÍCH LŨY (24 Lưới / 2-3 Ngày)"
    )
    _rsi_ok      = (rsi_1h < 70.0)
    _change24_ok = (live_info['change_24h'] < 12.0)
    # Phương pháp 3: Nới lỏng Discount lên 10% để bắt sóng (Bot tự chia lưới)
    _discount_ok = (discount_pct < 10.0)
    # Bỏ hoàn toàn điểm nến 15M cho Grid Tích Lũy (Chỉ dùng 15M để xếp hạng, không được cấm Grid)
    _nen_ok      = True if mode_tich_luy else (score_nen_15m >= 40.0)
    _no_wick_heavy = ("Chuyên Úp Bô" not in warning_str)

    _macro_trend_ok = close_live >= ma50_1d
    _macro_rsi_ok   = rsi_1d < 80.0
    
    _base_safe = _nen_ok and \
                 ("DOWNTREND" not in trang_thai) and \
                 _no_wick_heavy and \
                 ("Tiền Sử Xả Dốc" not in warning_str) and \
                 _is_grid_ok and _rsi_ok and _change24_ok and _discount_ok and _macro_rsi_ok

    darvas_floor = 0.0
    has_darvas_floor = False
    darvas_setup = {}
    darvas_score = 0

    # Phương pháp 2: Gọi Darvas bảo lãnh nếu vượt qua được màng lọc cơ sở (Giảm tải API)
    if _base_safe:
        from strategies.macro_grid_darvas import MacroGridDarvas
        darvas = _get_shared_darvas()
        darvas_res = darvas.scan_grid_candidate(symbol, '4h')
        darvas_setup = darvas_res.get('grid_setup', {})
        darvas_floor = darvas_setup.get('lower_price', 0)
        darvas_score = darvas_res.get('total_score', 0)
        has_darvas_floor = darvas_floor > 0 and darvas_score >= 60

    _trend_or_darvas_ok = _macro_trend_ok or has_darvas_floor
    
    if not _trend_or_darvas_ok:
        warning_reasons.append("⛔ Gãy Trend 1D & Không Có Móng")
    elif not _macro_trend_ok and has_darvas_floor:
        warning_reasons.append("🛡️ Đáy Darvas Bảo Lãnh (Dưới MA50)")

    if not _macro_rsi_ok:
        warning_reasons.append(f"⛔ RSI 1D Quá Khẩu Độ ({rsi_1d:.1f})")

    # Bẫy chặn động: Nếu RSI 1D >= 75.0, bắt buộc ATR Squeeze (C4) phải đạt tuyệt đối (20đ)
    if rsi_1d >= 75.0:
        c4_score = darvas_setup.get('c4_score', 0) if has_darvas_floor else (darvas_res.get('details', {}).get('C4_score', 0) if 'darvas_res' in locals() else 0)
        if c4_score < 20:
            warning_reasons.append(f"⛔ RSI 1D Cao ({rsi_1d:.1f}) nhưng ATR Squeeze chưa kịch trần")
            _base_safe = False

    is_safe = _base_safe and _trend_or_darvas_ok

    che_do = "🌊 Tích Lũy" if mode_tich_luy else "⚡ Lướt Ngắn"

    if live_info['change_24h'] >= 12.0:
        warning_reasons.append(f"⚠️ FOMO 24H Cao (+{live_info['change_24h']:.1f}%)")
    if drop_from_1h_high > 1.5 and not is_1h_broken:
        warning_reasons.append(f"⚠️ Giảm -{drop_from_1h_high:.1f}% Từ Đỉnh 1H")
    warning_str = "  •  ".join(warning_reasons) if warning_reasons else "An Toàn"


    gc = GridCalculator()
    grid_4h_setup = gc.calculate_grid_4h(df_4h, close_live)
    
    if has_darvas_floor and darvas_setup:
        logging.debug(f"[Hybrid Override - Bảng 1] {symbol}: Darvas Score {darvas_score} >= 60. Ghi đè thông số GRID 4H cũ: {grid_4h_setup}")
        final_grid_setup = {
            "status": "SUCCESS",
            "engine": "GRID Darvas (Lưới Kép)",
            "is_dual_grid": darvas_setup.get('is_dual_grid', False),
            "g1_lower": darvas_setup.get('g1_lower'),
            "g1_upper": darvas_setup.get('g1_upper'),
            "g1_grids": darvas_setup.get('g1_grids'),
            "g1_capital_pct": darvas_setup.get('g1_capital_pct'),
            "g2_lower": darvas_setup.get('g2_lower'),
            "g2_upper": darvas_setup.get('g2_upper'),
            "g2_grids": darvas_setup.get('g2_grids'),
            "g2_capital_pct": darvas_setup.get('g2_capital_pct'),
            "hard_stop_loss": darvas_setup.get('stop_loss'),
            "hard_take_profit": darvas_setup.get('take_profit'),
            "tp_buffer_pct": 0.05
        }
    else:
        final_grid_setup = grid_4h_setup
        if final_grid_setup.get("status") in ["SUCCESS", "WARNING_VOLATILE"]:
             final_grid_setup["engine"] = "GRID 4H (Phòng Thủ)"
             if _base_safe:
                 logging.debug(f"[Hybrid Fallback - Bảng 1] {symbol}: Darvas Score {darvas_score} < 60. Sử dụng GRID 4H mặc định.")

    return {
        "Symbol": symbol.replace("USDT", ""),
        "Chế Độ": che_do,
        "Trạng Thái": trang_thai,
        "Phân Loại Grid": phan_loai_grid,
        "TỔNG": total_score,
        "Cảnh Báo": warning_str,
        "Đ.Nến15M": round(score_nen_15m, 1),
        "Đ.Râu24H": round(score_rau_24h, 1),
        "Đ.Entry": round(score_entry, 1),
        "Đ.RSI": round(score_rsi, 1),
        "RSI1H": round(rsi_1h, 1),
        "Giá Live": smart_price(close_live),
        "Giá Mua Limit": smart_price(buy_target_price),
        "Cần Giảm": round(discount_pct, 2),
        # ── Thông tin thanh khoản & biến động ──
        "Vol24H(M)": round(live_info['quote_vol'] / 1_000_000, 1),   # Vol 24h tính bằng triệu USDT
        "Vola24H%": round(live_info.get('daily_vola', 0), 1),        # Biến động ngày (H-L)/L
        "Biến Động 24h": round(live_info['change_24h'], 2),
        "Giật 4H": round(avg_vola_4h, 2),
        "Đ.Vol": score_vol,
        "Đ.Giật": round(score_giat, 1),
        "Đ.Trend": round(avg_trend_score, 1),
        "is_safe": is_safe,
        "raw_close": close_live,
        "raw_limit": buy_target_price,
        "ATR_1D": atr_1d,
        "Darvas_Floor": darvas_floor,
        "Has_Darvas": has_darvas_floor,
        "EW_Level": _ew_level,
        "grid_setup": final_grid_setup
    }

def _cw(ch):
    cp = ord(ch)
    if 0xFE00 <= cp <= 0xFE0F or cp in (0x200B, 0x200C, 0x200D, 0xFEFF):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ('W', 'F'):
        return 2
    if eaw == 'A' and cp > 0x2600:
        return 2
    return 1

def dw(s):
    return sum(_cw(c) for c in str(s))

def trunc_w(s, max_w):
    s, w, out = str(s), 0, []
    for ch in s:
        cw = _cw(ch)
        if w + cw > max_w:
            break
        out.append(ch)
        w += cw
    return ''.join(out)

def ljust_w(s, width):
    s = str(s)
    return s + ' ' * max(0, width - dw(s))

# Cột: Hạng | Mã | TrạngThái | PhânLoạiGrid | TỔNG | ChếĐộ | CảnhBáo | Đ.Nến15M | Đ.Râu24H | Đ.Entry | Đ.RSI | RSI1H | GiáLive | GiáLimit | CầnGiảm | Vol24H(M) | Vola24H%
_WCOLS = [4, 7, 28, 38, 5, 14, 60, 9, 9, 8, 6, 6, 14, 16, 9, 10, 9]
_SEP   = ' | '
_TW    = sum(_WCOLS) + len(_SEP) * (len(_WCOLS) - 1)

def fmt_row(cells):
    return _SEP.join(ljust_w(trunc_w(c, w), w) for c, w in zip(cells, _WCOLS))

def smart_price(p):
    if p is None: return "N/A"
    try:
        p = float(p)
    except (ValueError, TypeError):
        return str(p)
        
    if p == 0: return "0.0"
    
    import math
    if abs(p) >= 1:
        decimals = 4
    else:
        first_sig = -math.floor(math.log10(abs(p)))
        decimals = min(first_sig + 3, 12)
        
    formatted = f"{p:.{decimals}f}"
    if '.' in formatted:
        formatted = formatted.rstrip('0').rstrip('.')
        if '.' not in formatted:
            formatted += '.0'
    return formatted

def analyze_early(symbol, info):
    info = info or {}
    if not info:
        return None
        
    vol_24h = info.get('quote_vol', 0)
    if vol_24h < EARLY_MIN_VOL_USDT or vol_24h > EARLY_MAX_VOL_USDT:
        return None
        
    if info.get('change_24h', 0) < EARLY_MIN_24H_CHG:
        return None

    # Lấy klines 1D (180 ngày)
    df_1d = get_klines_live(symbol, "1d", limit=180)
    if df_1d is None or df_1d.empty or len(df_1d) < 60:
        return None

    close_now = float(df_1d['Close'].iloc[-1])
    
    # 1. CHIẾT KHẤU TỪ ĐỈNH 180D (Màng lọc cứng: 60% - 90%)
    max_high_180d = float(df_1d['High'].max())
    drop_180d = ((max_high_180d - close_now) / max_high_180d) * 100 if max_high_180d > 0 else 0
    if drop_180d < 60.0 or drop_180d > 90.0:
        return None

    # 2. HỘP DARVAS 60 NGÀY (Màng lọc cứng: Biên độ < 30%)
    df_1d_60 = df_1d.tail(60)
    max_high_60d = float(df_1d_60['High'].max())
    min_low_60d = float(df_1d_60['Low'].min())
    if min_low_60d <= 0:
        return None
        
    box_amplitude = ((max_high_60d - min_low_60d) / min_low_60d) * 100
    if box_amplitude > 130.0:
        return None
        
    if not (min_low_60d <= close_now <= max_high_60d):
        return None

    # 3. KIỆT QUỆ THANH KHOẢN (Màng lọc cứng: ADV 60d < 65% ADV drop phase)
    # Giai đoạn rơi giá: nến từ -180 đến -60 (tối đa 120 ngày)
    df_drop_phase = df_1d.iloc[:-60]
    if df_drop_phase.empty:
        return None
        
    adv_box = float(df_1d_60['Quote_Volume'].mean())
    adv_drop = float(df_drop_phase['Quote_Volume'].mean())
    
    if adv_drop == 0:
        return None
        
    vol_dry_up_ratio = adv_box / adv_drop
    if vol_dry_up_ratio > 0.75:
        return None
        
    vol_drop_pct = (1.0 - vol_dry_up_ratio) * 100

    # 4. DẤU CHÂN GOM HÀNG (Tiêu chí mềm)
    accumulation_spikes = 0
    for _, k in df_1d_60.iterrows():
        is_green = float(k['Close']) > float(k['Open'])
        is_spike = float(k['Quote_Volume']) > (2.0 * adv_box)
        # Nến đóng cửa không vượt quá biên trên hộp
        closes_in_box = float(k['Close']) <= max_high_60d
        
        if is_green and is_spike and closes_in_box:
            accumulation_spikes += 1
            
    # Tính điểm xếp hạng (Thang 100 điểm)
    # Chiết khấu: 60% -> 90% (Max 30đ, càng sát 90% càng cao)
    score_drop = min(30.0, max(0.0, ((drop_180d - 60.0) / 30.0) * 30.0))
    # Nén hộp: 130% -> 0% (Max 25đ, biên độ càng hẹp càng cao)
    score_box = min(25.0, max(0.0, ((130.0 - box_amplitude) / 130.0) * 25.0))
    # Kiệt cung: 75% -> 0% (Max 25đ, cạn cung càng sâu càng cao)
    score_vol = min(25.0, max(0.0, ((0.75 - vol_dry_up_ratio) / 0.75) * 25.0))
    # Nến gom hàng: Mỗi nến 10đ (Max 20đ)
    score_spikes = min(20.0, accumulation_spikes * 10.0)
    
    total_early_score = score_drop + score_box + score_vol + score_spikes

    return {
        'Symbol':      symbol.replace('USDT', ''),
        '_raw_symbol': symbol,
        'Giá':         close_now,
        'Điểm':        round(total_early_score, 1),
        'Chiết Khấu':  round(drop_180d, 1),
        'Nén Hộp':     round(box_amplitude, 1),
        'Cạn Cung':    round(vol_drop_pct, 1),
        'Nến Gom':     accumulation_spikes,
        'Box_Floor':   min_low_60d,
        'Box_Ceiling': max_high_60d
    }

def _enrich_early_with_darvas(item: dict) -> dict:
    """Gọi Darvas Hybrid Override cho 1 mã đã lọc top — chỉ chạy trên Top 5 sau sort."""
    symbol = item.get('_raw_symbol', item['Symbol'] + 'USDT')
    grid_4h_setup = item.get('grid_setup', {})
    try:
        from strategies.macro_grid_darvas import MacroGridDarvas
        darvas = _get_shared_darvas()
        darvas_res = darvas.scan_grid_candidate(symbol, '4h')
        darvas_score = darvas_res.get('total_score', 0)
        darvas_setup = darvas_res.get('grid_setup', {})
        if darvas_setup:
            logging.debug(f"[Hybrid Override Bảng3] {symbol}: Có Darvas setup.")
            item['grid_setup'] = {
                "status": "SUCCESS",
                "engine": "GRID Darvas 4H (Lưới Kép)",
                "is_dual_grid": darvas_setup.get('is_dual_grid', False),
                "g1_lower": darvas_setup.get('g1_lower'),
                "g1_upper": darvas_setup.get('g1_upper'),
                "g1_grids": darvas_setup.get('g1_grids'),
                "g1_capital_pct": darvas_setup.get('g1_capital_pct'),
                "g2_lower": darvas_setup.get('g2_lower'),
                "g2_upper": darvas_setup.get('g2_upper'),
                "g2_grids": darvas_setup.get('g2_grids'),
                "g2_capital_pct": darvas_setup.get('g2_capital_pct'),
                "hard_stop_loss": darvas_setup.get('stop_loss'),
                "hard_take_profit": darvas_setup.get('take_profit'),
                "tp_buffer_pct": 0.05
            }
        else:
            # Fallback về 1D Box nếu 4H không ra lưới
            floor = item.get('Box_Floor')
            ceil = item.get('Box_Ceiling')
            if floor and ceil:
                gia = item.get('Giá')
                g1_l = round(floor, 5)
                g1_u = round(ceil * 0.8, 5)  # 80% box dưới
                g2_l = g1_u
                g2_u = round(ceil, 5)        # 20% box trên
                item['grid_setup'] = {
                    "status": "SUCCESS",
                    "engine": "GRID Vĩ Mô 1D (Lưới Kép)",
                    "is_dual_grid": True,
                    "g1_lower": g1_l,
                    "g1_upper": g1_u,
                    "g1_grids": 25,
                    "g1_capital_pct": 70,
                    "g2_lower": g2_l,
                    "g2_upper": g2_u,
                    "g2_grids": 10,
                    "g2_capital_pct": 30,
                    "hard_stop_loss": round(floor * 0.95, 5),
                    "hard_take_profit": round(ceil * 1.05, 5),
                    "tp_buffer_pct": 0.05
                }
    except Exception as e:
        logging.debug(f"[Hybrid Override Bảng3] {symbol} lỗi Darvas: {e}")
    return item

def analyze_momentum(symbol, info):
    info = info or {}
    if not info:
        return None

    if info.get('quote_vol', 0) < MOM_MIN_VOL_USDT:
        return None
    if info.get('change_24h', 0) < 1.5:
        return None

    df_15m = get_klines_live(symbol, "15m", limit=25)
    df_1h  = get_klines_live(symbol, "1h",  limit=30)
    if df_15m is None or df_1h is None:
        return None
    if len(df_15m) < 22 or len(df_1h) < 21:
        return None

    candle_m1  = df_15m.iloc[-2]
    vol_m1     = float(candle_m1['Quote_Volume'])
    avg_vol_20 = float(df_15m['Quote_Volume'].iloc[-22:-2].mean())
    if avg_vol_20 <= 0:
        return None
    vol_spike_x = vol_m1 / avg_vol_20
    if vol_spike_x < MOM_VOL_SPIKE_MULT:
        return None

    rsi_1h = calculate_rsi(df_1h, period=14)
    if not (MOM_RSI_LOW <= rsi_1h <= MOM_RSI_HIGH):
        return None

    close_1h_m1 = float(df_1h.iloc[-2]['Close'])
    bb_window   = df_1h['Close'].iloc[-21:-1]
    bb_ma20     = float(bb_window.mean())
    bb_std      = float(bb_window.std(ddof=1))
    bb_upper    = bb_ma20 + 2.0 * bb_std
    if close_1h_m1 <= bb_upper:
        return None

    o15, h15   = float(candle_m1['Open']), float(candle_m1['High'])
    c15        = float(candle_m1['Close'])
    l15        = float(candle_m1['Low'])
    body_top15 = max(o15, c15)
    body_bottom15 = min(o15, c15)
    body_size15 = abs(c15 - o15)
    upper_wick15 = h15 - body_top15
    lower_wick15 = body_bottom15 - l15
    if body_size15 <= 0:
        return None
    upper_wick_ratio = upper_wick15 / body_size15
    if upper_wick_ratio > MOM_MAX_UPPER_WICK and upper_wick15 > lower_wick15 * 1.5 and vol_m1 >= avg_vol_20 * 0.8:
        return None
        
    # Màng lọc vĩ mô 1H cho Breakout
    candle_1h = df_1h.iloc[-2]  # Nến đóng cửa gần nhất (nến số 2)
    o1h, h1h, l1h, c1h = float(candle_1h['Open']), float(candle_1h['High']), float(candle_1h['Low']), float(candle_1h['Close'])
    vol_1h = float(candle_1h['Quote_Volume'])
    avg_vol_1h = float(df_1h['Quote_Volume'].iloc[-12:-2].mean()) if len(df_1h) >= 12 else 0
    
    body_top1h = max(o1h, c1h)
    body_bottom1h = min(o1h, c1h)
    total_len1h = (h1h - l1h) if (h1h - l1h) > 0 else 0.000001
    
    upper_wick1h_pct = ((h1h - body_top1h) / total_len1h) * 100
    lower_wick1h_pct = ((body_bottom1h - l1h) / total_len1h) * 100
    
    if upper_wick1h_pct >= 40.0 and upper_wick1h_pct > lower_wick1h_pct * 1.5 and vol_1h >= avg_vol_1h * 0.8:
        return None

    ema7_15m     = float(df_15m['Close'].ewm(span=7, adjust=False).mean().iloc[-1])
    close_live   = info['last_price']
    ema7_gap_pct = ((close_live - ema7_15m) / ema7_15m) * 100 if ema7_15m > 0 else 999.0
    if ema7_gap_pct > MOM_MAX_EMA7_GAP:
        return None

    taker_buy = float(candle_m1['Taker_Buy_Quote'])
    total_vol  = float(candle_m1['Quote_Volume'])
    taker_pct  = (taker_buy / total_vol * 100) if total_vol > 0 else 0.0
    if taker_pct < MOM_MIN_TAKER_BUY:
        return None

    s_vol = min(100.0, max(0.0, (vol_spike_x - MOM_VOL_SPIKE_MULT) / (10.0 - MOM_VOL_SPIKE_MULT) * 100.0)) * 0.30

    rsi_peak = (MOM_RSI_LOW + MOM_RSI_HIGH) / 2.0
    s_rsi = max(0.0, 100.0 - abs(rsi_1h - rsi_peak) / ((MOM_RSI_HIGH - MOM_RSI_LOW) / 2.0) * 100.0) * 0.25

    s_taker = min(100.0, max(0.0, (taker_pct - MOM_MIN_TAKER_BUY) / (100.0 - MOM_MIN_TAKER_BUY) * 100.0)) * 0.25

    s_ema7 = max(0.0, (1.0 - ema7_gap_pct / MOM_MAX_EMA7_GAP) * 100.0) * 0.20

    total_mom_score = round(s_vol + s_rsi + s_taker + s_ema7, 1)

    grid_low  = close_live * 0.985
    grid_high = close_live * 1.020

    return {
        'Symbol':        symbol.replace('USDT', ''),
        'Giá Live':      close_live,
        'Điểm Mom':      total_mom_score,
        'Vol Spike(x)':  round(vol_spike_x, 1),
        'RSI 1H':        round(rsi_1h, 1),
        'Râu Trên %':    round(upper_wick_ratio * 100, 1),
        'EMA7 Gap %':    round(ema7_gap_pct, 2),
        'Taker Buy %':   round(taker_pct, 1),
        'Tăng 24H %':    round(info.get('change_24h', 0), 2),
        'Grid Low':      grid_low,
        'Grid High':     grid_high,
    }

def get_filtered_symbols(live_data_map):
    auto_candidates = []
    for symbol, info in live_data_map.items():
        if symbol.endswith("USDT") and symbol not in EXCLUDE and info.get('volume_usdt', 0) >= 2_000_000:
            if info.get('daily_vola', 0) >= 2.5:
                auto_candidates.append((symbol, info.get('volume_usdt', 0)))

    auto_candidates = sorted(auto_candidates, key=lambda x: x[1], reverse=True)[:TOP_AUTO_COUNT]
    auto_symbols = [s[0] for s in auto_candidates]
    all_symbols = list(dict.fromkeys(MANUAL_SYMBOLS + auto_symbols))
    
    print(f"🎯 TỔNG CỘNG CÓ {len(all_symbols)} MÃ ĐƯỢC ĐƯA VÀO BẢNG CHẤM ĐIỂM MOMENTUM!\n")
    print("⏳ Hệ thống đang bắt đầu tính toán GRID và chấm điểm (Vui lòng đợi 1-2 phút)...\n")
    
    summary_list = []
    if all_symbols:
        from concurrent.futures import as_completed
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(process_symbol, s, live_data_map.get(s, {})): s for s in all_symbols}
            
            processed = 0
            for future in as_completed(futures):
                res = future.result()
                if res is not None:
                    summary_list.append(res)
                processed += 1
                if processed % 5 == 0 or processed == len(all_symbols):
                    from datetime import datetime
                    current_time = datetime.now().strftime('%H:%M:%S')
                    print(f"  [{current_time}] ▶ Đã quét xong: {processed}/{len(all_symbols)} mã...", flush=True)
                    
    if summary_list:
        df_summary = (
            pd.DataFrame(summary_list)
            .sort_values(
                by=["is_safe", "Has_Darvas", "TỔNG", "Cần Giảm"],
                ascending=[False, False, False, True]
            )
            .reset_index(drop=True)
        )

        # ⚡ Gọi Darvas Hybrid Override cho Top 5 is_safe
        safe_indices = df_summary.index[df_summary['is_safe'] == True].tolist()[:5]
        for idx in safe_indices:
            row_dict = df_summary.loc[idx].to_dict()
            enriched = _enrich_early_with_darvas(row_dict)
            df_summary.at[idx, 'grid_setup'] = enriched.get('grid_setup', {})

        vietnam_tz = timezone(timedelta(hours=7))
        current_time_str = datetime.now(vietnam_tz).strftime("%Y-%m-%d %H:%M:%S")

        df_safe = df_summary[
            (df_summary["is_safe"] == True) & 
            (df_summary["Has_Darvas"] == True) &
            (df_summary["TỔNG"] >= 60.0) &
            (df_summary["Cần Giảm"] < 3.0) &
            (df_summary["Đ.Nến15M"] >= 50.0) &
            (df_summary["EW_Level"] == 0)
        ]

        _WCOLS2 = [7, 21, 10, 11, 28, 14, 5, 11, 11, 10]
        _TW2    = sum(_WCOLS2) + len(_SEP) * (len(_WCOLS2) - 1)
        def fmt_row_safe(cells):
            return _SEP.join(ljust_w(trunc_w(c, w), w) for c, w in zip(cells, _WCOLS2))

        # ── 6. 🏆 BẢNG THAM SỐ GRID (HỢP THỂ DARVAS ĐỘNG CƠ 1) ─────────────────
        print("\n" + "=" * _TW2)
        print(f"🏆 BẢNG THAM SỐ GRID CHUẨN (Hợp Thể Động Cơ 1 Darvas + ATR 1D)")
        print(f"(Sàn Lưới Darvas siêu cứng / Trần Lưới ATR siêu sóng)")
        print("=" * _TW2)

        if df_safe.empty:
            print("⚠️ KHÔNG CÓ MÃ NÀO ĐẠT CHUẨN 4 KHÔNG ĐỂ MỞ GRID LÚC NÀY! THỊ TRƯỜNG ĐANG DẦN BỊ NHIỄU.")
        else:
            print("-" * _TW2)
            gc = GridCalculator()
            for r in df_safe.to_dict(orient='records'):
                is_tich_luy = "TÍCH LŨY" in r["Phân Loại Grid"]
                is_wide     = "RỘNG"    in r["Phân Loại Grid"]
                sym         = r["Symbol"]
                p_trig      = r["raw_limit"]
                atr         = r.get("ATR_1D", p_trig * 0.05)
                floor       = r.get("Darvas_Floor", 0)
                has_darvas_floor = r.get("Has_Darvas", False)
                
                if is_tich_luy:
                    # BẢNG 1: ĐỘNG CƠ 1 (Darvas Grid - Áp dụng Grid 4H)
                    grid_setup = r.get('grid_setup', {})
                    if grid_setup.get('status') in ["SUCCESS", "WARNING_VOLATILE"]:
                        if grid_setup.get('is_dual_grid'):
                            g1_l, g1_u, g1_n, g1_cap = grid_setup.get('g1_lower'), grid_setup.get('g1_upper'), grid_setup.get('g1_grids'), grid_setup.get('g1_capital_pct')
                            g2_l, g2_u, g2_n, g2_cap = grid_setup.get('g2_lower'), grid_setup.get('g2_upper'), grid_setup.get('g2_grids'), grid_setup.get('g2_capital_pct')
                            sl, tp = grid_setup.get('hard_stop_loss'), grid_setup.get('hard_take_profit')
                            
                            trig_g1 = g1_u * 1.005
                            print(f"  ↳ ⚙️ GRID Darvas [Gom Đáy {g1_cap}%]: [{sym}] | Trig: {smart_price(trig_g1)} | Lưới: {smart_price(g1_l)} - {smart_price(g1_u)} ({g1_n}L) | SL: {smart_price(sl)} | TP: N/A")
                            print(f"  ↳ ⚙️ GRID Darvas [Hứng Breakout {g2_cap}%]: [{sym}] | Trig: {smart_price(g2_l)} | Lưới: {smart_price(g2_l)} - {smart_price(g2_u)} ({g2_n}L) | SL: {smart_price(sl)} | TP: {smart_price(tp)}")
                        else:
                            lower = grid_setup.get('lower_bound', p_trig * 0.8)
                            upper = grid_setup.get('upper_bound', p_trig * 1.2)
                            num_grids = grid_setup.get('num_grids', 25)
                            sl = grid_setup.get('hard_stop_loss', lower * 0.97)
                            tp = grid_setup.get('hard_take_profit', upper * 1.05)
                            tp_buf = grid_setup.get('tp_buffer_pct', 0.05) * 100
                            engine = grid_setup.get('engine', 'GRID 4H')
                            
                            current_price_live = r.get('raw_close', p_trig)
                            trigger_buffer_4h = 0.005  # Đệm +0.5% để chống front-run
                            trig_4h = upper * (1 + trigger_buffer_4h)
                            print(f"  ↳ ⚙️ {engine}: [{sym}] | Trig: {smart_price(trig_4h)} (Đón lõng hộp) | Lưới: {smart_price(lower)} - {smart_price(upper)} ({num_grids}L) | SL: {smart_price(sl)} (SL Cứng) | TP: {smart_price(tp)} (+{tp_buf:.1f}%)")
                    else:
                        error_msg = grid_setup.get('message', 'Không rõ lỗi')
                        engine = grid_setup.get('engine', 'GRID 4H')
                        print(f"  ↳ ⚙️ {engine}: [{sym}] - Lỗi tính toán: {error_msg}")
                        
                else:
                    # BẢNG 2/4: ĐỘNG CƠ 2 (Pullback Sniper - Áp dụng Grid 1H)
                    if is_wide:
                        sl_price = floor if has_darvas_floor else p_trig - (5.6 * atr)
                        tp_price = p_trig + (8.0 * atr) if has_darvas_floor else p_trig + (5.6 * atr)
                    else:
                        sl_price = floor if has_darvas_floor else p_trig - (2.4 * atr)
                        tp_price = p_trig + (4.0 * atr) if has_darvas_floor else p_trig + (2.4 * atr)
                        
                    setup1h = gc.calculate_grid_1h(current_price=r['raw_close'], entry=p_trig, stop_loss=sl_price, tp1=tp_price)
                    
                    if setup1h.get('status') == "SUCCESS":
                        lower = setup1h.get('lower_bound', sl_price)
                        upper = setup1h.get('upper_bound', tp_price)
                        num_grids = setup1h.get('num_grids', 15)
                        hard_sl = setup1h.get('hard_stop_loss', sl_price)
                        hard_tp = setup1h.get('hard_take_profit', tp_price)
                        buf_pct = setup1h.get('tp_buffer_pct', 0.015) * 100
                        
                        trigger_buffer_1h = 0.003  # Đệm +0.3% để đón lõng Pullback
                        trig_1h = p_trig * (1 + trigger_buffer_1h)
                        print(f"  ↳ ⚙️ GRID 1H: [{sym}] | Trig: {smart_price(trig_1h)} (Đón lõng Entry) | Lưới: {smart_price(hard_sl)} - {smart_price(p_trig)} ({num_grids}L) | SL: {smart_price(hard_sl)} (-{buf_pct:.1f}%) | TP: {smart_price(hard_tp)} (+{buf_pct:.1f}%)")
                    else:
                        error_msg = setup1h.get('message', 'Không rõ lỗi')
                        print(f"  ↳ ⚙️ GRID 1H: [{sym}] - Lỗi tính toán: {error_msg}")

        if len(df_safe) < 3:
            print("-" * _TW2)
            print("💡 Top 3 Mã Tiềm Năng Nhất (Radar Cảnh Giới Dự Phòng):")
            top3_df = df_summary.sort_values(by="TỔNG", ascending=False).head(3)
            for r in top3_df.to_dict(orient='records'):
                sym = r.get("Symbol", "UNK")
                score = r.get("TỔNG", 0)
                warn = r.get("Cảnh Báo", "")
                grid_type = r.get("Phân Loại Grid", "")
                print(f"  • {sym:<6} | Điểm: {score:<4.1f} | Grid: {grid_type}")
                print(f"    └─ Lỗi: {warn}")

        print("\n" + "=" * _TW + "\n")

    # ── 7. 🌱 BẢNG 3 - BẮT SỚM NỀN TĂNG (in sau GRID) ───────────────────
    early_symbols = [
        s for s in live_data_map
        if s.endswith('USDT') and s not in EXCLUDE
    ]
    # print(f"\n🌱 Đang quét Bảng 3 - Mỏ Vàng Ngủ Say ({len(early_symbols)} mã vol > ${EARLY_MIN_VOL_USDT // 1_000_000}M)...\n")

    early_list = []
    if early_symbols:
        with ThreadPoolExecutor(max_workers=8) as executor:
            early_list = [r for r in executor.map(lambda s: analyze_early(s, live_data_map.get(s, {})), early_symbols) if r is not None]

    early_list.sort(key=lambda x: x['Điểm'], reverse=True)
    early_list = early_list[:5]
    early_list = [_enrich_early_with_darvas(r) for r in early_list]


    



    momentum_symbols = [
        s for s in live_data_map
        if s.endswith('USDT') and s not in EXCLUDE
        and live_data_map[s].get('quote_vol', 0) >= MOM_MIN_VOL_USDT
        and live_data_map[s].get('change_24h', 0) >= 1.5
    ]
    # print(f"\n🚀 Đang quét Bảng 4 - Momentum Breakout ({len(momentum_symbols)} mã đang tăng vol >${MOM_MIN_VOL_USDT // 1_000_000}M)...\n")

    mom_list = []
    if momentum_symbols:
        with ThreadPoolExecutor(max_workers=8) as executor:
            mom_list = [r for r in executor.map(lambda s: analyze_momentum(s, live_data_map.get(s, {})), momentum_symbols) if r is not None]

    mom_list.sort(key=lambda x: x['Điểm Mom'], reverse=True)
    mom_list = mom_list[:MOM_TOP_N]

    safety_map = {}
    # ── Xây dựng watchlist CÓ THỨ TỰ ưu tiên (an toàn → điểm cao → early → momentum)
    # Dùng list + seen-set thay vì set thuần để tránh thứ tự random khi slice Top N
    symbols_ordered = []
    seen_usdt = set()

    if summary_list:
        # df_summary đã được sort: is_safe↓, Has_Darvas↓, TỔNG↓ — giữ nguyên thứ tự này
        df_candidates = df_summary
        for r in df_candidates.to_dict(orient='records'):
            sym_usdt  = r['Symbol'] + "USDT"
            sym_ccxt  = r['Symbol'] + "/USDT"
            if sym_usdt not in seen_usdt:
                symbols_ordered.append(sym_ccxt)
                seen_usdt.add(sym_usdt)
            if r['is_safe']:
                safety_map[sym_ccxt] = "✅ AN TOÀN"
            else:
                reason = r.get('Phân Loại Grid', '').replace('⛔ NÉ GRID (', '').replace(')', '').replace('⛔ ', '')
                if not reason.strip() or 'GRID' in reason:
                    reason = r.get('Cảnh Báo', 'Rủi ro chưa xác định')
                safety_map[sym_ccxt] = f"⚠️ {reason}"

    # Bổ sung mã từ Bảng 3 (early) và Bảng 4 (momentum) — thêm vào cuối nếu chưa có
    for r in early_list:
        base_sym = r.get('Symbol', r.get('symbol', '')).replace('USDT', '')
        sym_usdt = base_sym + "USDT"
        sym_ccxt = base_sym + "/USDT"
        if sym_usdt not in seen_usdt:
            symbols_ordered.append(sym_ccxt)
            seen_usdt.add(sym_usdt)
        safety_map.setdefault(sym_ccxt, "⚠️ CHƯA XÉT")

    for r in mom_list:
        sym_usdt = r['Symbol'] + "USDT"
        sym_ccxt = r['Symbol'] + "/USDT"
        if sym_usdt not in seen_usdt:
            symbols_ordered.append(sym_ccxt)
            seen_usdt.add(sym_usdt)
        safety_map.setdefault(sym_ccxt, "⚠️ CHƯA XÉT")

    return symbols_ordered, safety_map, early_list, df_summary if summary_list else None, current_time_str if summary_list else ""

if __name__ == "__main__":
    from core.cache_service import CacheService
    cache = CacheService()
    symbols, safety_map = get_filtered_symbols(cache.get_live_data_map())
    print("\n[+] Danh sách lọc (Format CCXT):", symbols)

def print_final_tables(early_list, df_summary, current_time_str):
    if df_summary is None or df_summary.empty:
        summary_list = []
    else:
        summary_list = [1]
    _WCOLS3 = [8, 12, 8, 12, 12, 12, 10]
    _TW3    = sum(_WCOLS3) + len(_SEP) * (len(_WCOLS3) - 1)
    def fmt_row3(cells):
        return _SEP.join(ljust_w(trunc_w(c, w), w) for c, w in zip(cells, _WCOLS3))

    print("=" * _TW3)
    print("🌱 BẢNG 3: MỎ VÀNG NGỦ SAY (WATCHLIST VĨ MÔ 1D - THANG ĐIỂM 100 - TOP 5)")
    print("=" * _TW3)

    if not early_list:
        print("⚠️ KHÔNG TÌM THẤY MÃ NÀO ĐẠT CHUẨN VĨ MÔ.")
    else:
        print(fmt_row3(["Mã", "Giá Live", "Điểm", "Chiết Khấu", "Nén Hộp", "Cạn Cung", "Nến Gom"]))
        print("-" * _TW3)
        for r in early_list:
            print(fmt_row3([
                r['Symbol'],
                smart_price(r['Giá']),
                str(r['Điểm']),
                f"-{r['Chiết Khấu']}%",
                f"{r['Nén Hộp']}%",
                f"Giảm {r['Cạn Cung']}%",
                f"{r['Nến Gom']} nến",
            ]))
            
            # Tính toán gợi ý Scale Order cho 1000 USDT (10 lệnh -> 100 USDT/lệnh)
            sym = r['Symbol']
            gia = float(r['Giá'])
            
            grid_setup = r.get('grid_setup', {})
            if grid_setup.get('status') in ["SUCCESS", "WARNING_VOLATILE"]:
                warning_tag = "[⚠️ VOLATILE]" if grid_setup.get('status') == "WARNING_VOLATILE" else ""
                if grid_setup.get('is_dual_grid'):
                    g1_l, g1_u, g1_n, g1_cap = grid_setup.get('g1_lower'), grid_setup.get('g1_upper'), grid_setup.get('g1_grids'), grid_setup.get('g1_capital_pct')
                    g2_l, g2_u, g2_n, g2_cap = grid_setup.get('g2_lower'), grid_setup.get('g2_upper'), grid_setup.get('g2_grids'), grid_setup.get('g2_capital_pct')
                    sl, tp = grid_setup.get('hard_stop_loss'), grid_setup.get('hard_take_profit')
                    trig_g1 = g1_u * 1.005 if g1_u is not None else None
                    print(f"  ↳ ⚙️ GRID Darvas [Gom Đáy {g1_cap}%] {warning_tag}: [{sym}] | Trig: {smart_price(trig_g1)} | Lưới: {smart_price(g1_l)} - {smart_price(g1_u)} ({g1_n}L) | SL: {smart_price(sl)} | TP: N/A")
                    print(f"  ↳ ⚙️ GRID Darvas [Hứng Breakout {g2_cap}%] {warning_tag}: [{sym}] | Trig: {smart_price(g2_l)} | Lưới: {smart_price(g2_l)} - {smart_price(g2_u)} ({g2_n}L) | SL: {smart_price(sl)} | TP: {smart_price(tp)}")
                else:
                    upper = grid_setup.get('upper_bound', gia * 0.97)
                    lower = grid_setup.get('lower_bound', gia * 0.80)
                    num_grids = grid_setup.get('num_grids', 10)
                    engine = grid_setup.get('engine', 'GRID 4H')
                    
                    trigger_buffer_4h = 0.005
                    trig = upper * (1 + trigger_buffer_4h) if upper is not None else None
                    sl = grid_setup.get('hard_stop_loss', lower * 0.97 if lower is not None else None)
                    tp = grid_setup.get('hard_take_profit', upper * 1.05 if upper is not None else None)
                    tp_buf = grid_setup.get('tp_buffer_pct', 0.05)
                    tp_buf_str = f"+{tp_buf*100:.1f}%" if tp_buf is not None else "N/A"
                    
                    print(f"  ↳ ⚙️ {engine} {warning_tag}: [{sym}] | Trig: {smart_price(trig)} (Đón lõng hộp) | Lưới: {smart_price(lower)} - {smart_price(upper)} ({num_grids}L) | SL: {smart_price(sl)} (SL Cứng) | TP: {smart_price(tp)} ({tp_buf_str})")
            else:
                error_msg = grid_setup.get('message', 'Không rõ lỗi')
                engine = grid_setup.get('engine', 'GRID 4H')
                print(f"  ↳ ⚙️ {engine}: [{sym}] - Lỗi tính toán: {error_msg}")

    print("=" * _TW3 + "\n")
    # ── 8. 🏆 BẢNG CHẤM ĐIỂM REBALANCE (in sau cùng) ──────────────────
    if summary_list:
        print("=" * _TW)
        print("=" * _TW)
        print(f"🏆 BẢNG CHẤM ĐIỂM REBALANCE / SPOT GRID (THANG ĐIỂM 100 - TUYẾN TÍNH & 4 KHÔNG)")
        print(f"⏰ Thời điểm cập nhật (UTC+7): {current_time_str}")
        print("=" * _TW)
        print(fmt_row(["Hạng", "Mã", "Trạng Thái", "Phân Loại Grid", "TỔNG",
                       "Chế Độ", "Tín Hiệu Cảnh Báo", "Đ.Nến15M", "Đ.Râu24H", "Đ.Entry",
                       "Đ.RSI", "RSI1H", "Giá Live", "Giá Limit", "Cần Giảm",
                       "Vol24H(M)", "Vola24H%"]))
        print("-" * _TW)
        for rank, row in enumerate(df_summary.head(30).to_dict(orient='records'), 1):
            print(fmt_row([
                "#" + str(rank),
                row["Symbol"],
                row["Trạng Thái"],
                row["Phân Loại Grid"],
                row["TỔNG"],
                row["Chế Độ"],
                row["Cảnh Báo"],
                row["Đ.Nến15M"],
                row["Đ.Râu24H"],
                row["Đ.Entry"],
                row["Đ.RSI"],
                row["RSI1H"],
                row["Giá Live"],
                row["Giá Mua Limit"],
                f"{row['Cần Giảm']:.2f}%",
                f"${row['Vol24H(M)']}M",
                f"{row['Vola24H%']}%",
            ]))
        print("\n" + "=" * _TW + "\n")
