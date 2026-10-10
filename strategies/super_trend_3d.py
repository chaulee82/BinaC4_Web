"""
Module: strategies/super_trend_3d.py
Dự án: BinaC4
Mục đích: 🌊 BẢNG 3B — SIÊU SÓNG TĂNG 3D (Super-Wave 3D Trend Hunter)

Triết lý: Khác Bảng 3A (Mỏ Vàng — mã ngủ say dưới đáy), Bảng 3B chỉ săn mã ĐÃ kích hoạt
đà tăng vĩ mô và đang bám đường ray, mở rộng dải sóng trên khung 3D.

HARD GATE 3D (trượt 1 điều kiện → loại):
  • Supertrend(10, 3) 3D XANH và Giá live > ST
  • Giá live ≥ MA7 3D
  • MA7 3D > MA25 3D

THANG ĐIỂM 100 (+10 Bonus):
  1. Đường ray  (30đ): MA25 3D dốc lên — Gia tốc móng (+15) | P > MA99 (+15)
  2. Golden Cross (25đ): MA7 cắt lên MA99 trong 3 nến 3D gần nhất (+25) | MA7 > MA99 duy trì (+15)
  3. Bollinger  (25đ): P ≥ BOLL_UP (+15) | Bandwidth nở rộng (+10)
  4. Dòng tiền  (20đ): Vol 3D ≥ 1.5× MA5 Vol (+10) | Taker Buy 24h ≥ 53% (+10)
  Bonus (+10): Nến 3D LIỀN TRƯỚC (đã đóng) xanh thân đặc (râu trên < 20% tổng chiều dài nến)
               → nến 3D đang chạy (tới 72h) liên tục tạo râu ảo nên không dùng để chấm hình thái.
  Phạt (−15): 🔴 FOMO xa ray — Giá cách MA7 3D > 22% (rủi ro xả ngược, R/R xấu).
  Bonus (+5) : 💥 TIỀN BẠO PHÁT — Vol Dự Phóng ≥ 3.0× MA5 Vol (2.0x đã là "bình thường mới" của mã có sóng).

NỘI SUY KHỐI LƯỢNG THEO TỶ TRỌNG THỜI GIAN (Time-Weighted Volume Extrapolation):
  Tỷ trọng = (now − open_time nến 3D) / 72h  →  Vol Dự Phóng = Vol hiện tại / Tỷ trọng
  Chốt chặn: Tỷ trọng < 10% → giữ Vol thực | Hệ số nhân ≤ 5.0x
  Lưu ý: Nến 3D của BinaC4 là cửa sổ cuốn chiếu [t-2, t-1, t] → Tỷ trọng luôn 66.7–100% (hệ số 1.0–1.5x),
         chỉ bù phần khuyết của nến 1D hôm nay. Hai chốt chặn giữ lại làm hàm phòng vệ chuẩn
         (tái dùng cho khung cố định theo lịch). KHÔNG đổi sang nến 3D cố định — các Động cơ khác tune theo nến cuốn chiếu.
  MA5 Vol = trung bình 5 nến 3D ĐÃ ĐÓNG (không trộn nến đang chạy vào mẫu số).
  → Chỉ hiển thị khi ≥ 70 điểm.

Dữ liệu: nến 1D (klines_cache, 330 nến → 110 nến 3D) + nến 1H (Taker Buy 24h).
Cache hit = 0 API call. Nến 1D cuối được "vá" bằng giá live để chấm theo giá thời gian thực.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from core.macro_levels import resample_1d_to_3d, _supertrend_val_dir

logger = logging.getLogger("SuperWave3D")

# ── Cấu hình ─────────────────────────────────────────────────────────────────
B3B_MIN_CANDLES_3D   = 100            # Tối thiểu 100 nến 3D (MA99 thật, không fallback EMA)
B3B_FETCH_1D         = 330            # 330 nến 1D → 110 nến 3D (đủ MA99 cho cửa sổ Golden Cross 3 nến)
B3B_MIN_VOL_USDT     = 5_000_000      # Thanh khoản 24h tối thiểu vào pool quét
B3B_MIN_24H_CHG      = -5.0           # Loại mã đang xả mạnh trong ngày
B3B_SCAN_TOP_N       = 150            # Quét Top N mã theo thanh khoản
B3B_MIN_SCORE        = 70             # Ngưỡng kích hoạt hiển thị
B3B_TOP_N            = 5              # Số mã hiển thị
B3B_CROSS_LOOKBACK   = 3              # Golden Cross trong 3 nến 3D gần nhất
B3B_VOL_MULT         = 1.5
B3B_TAKER_MIN        = 0.53           # Khung thời gian lớn: Taker ≥ 53% đã đủ xác nhận gom chủ động
B3B_MAX_UPPER_WICK   = 0.20
B3B_NEAR_BOLL_UP     = 0.95           # P ≥ 95% BOLL_UP → "Nén sát đỉnh"
B3B_OVEREXT_PCT      = 22.0           # Cách MA7 3D > 22% → quá xa ray (đồng bộ BREAKOUT_MAX_DIST_PCT)
B3B_OVEREXT_PENALTY  = 15             # Trừ thẳng vào tổng điểm khi XA RAY
B3B_SL_MA7_RATIO     = 0.96           # SL nền = MA7 3D × 0.96 (đồng bộ hard_sl_3d)

# ── Setup 🌊 [SÓNG 3D - XXX] (Spot Grid — thứ tự nhập liệu Binance: Low - Up | Lưới | Trig | SL | TP) ──
B3B_TP_R_MULT        = 3.0            # TP = Trig + 3R (R = Trig − SL) ...
B3B_TP_HIGH_LOOKBACK = 10             # ... nhưng không thấp hơn Đỉnh 10 nến 3D gần nhất (~30 ngày)
B3B_TRIG_MIN_SL_GAP  = 0.97           # ST 3D nằm trên MA7 → Trig nâng lên ≥ SL / 0.97 (SL cách Trig ≥ 3%)
B3B_GRID_LOW_BUFFER  = 1.005          # Đáy lưới nằm trên SL 0.5% (SL không trùng nấc mua cuối)

# ── Nội suy Volume theo tỷ trọng thời gian ──
B3B_CANDLE_MS        = 3 * 24 * 3600 * 1000   # 1 nến 3D = 72h
B3B_PROJ_MIN_FRAC    = 0.10           # Nến chạy < 10% (~7.2h) → không dự phóng (chống nhiễu lệnh lớn đầu nến)
B3B_PROJ_MAX_MULT    = 5.0            # Trần hệ số nội suy 1/Tỷ trọng
B3B_EXPLOSIVE_MULT   = 3.0            # Vol Dự Phóng ≥ 3.0× MA5 → 💥 TIỀN BẠO PHÁT (2.0x quá lỏng — mọi mã có sóng đều đạt)
B3B_EXPLOSIVE_BONUS  = 5              # Điểm thưởng Bạo Phát (cộng thêm ngoài 10đ Vol_Explosion)

# ── Lớp Micro-Trigger 15M (CHỈ HIỂN THỊ — không đổi điểm / thứ hạng 3D) ──
B3B_M15_MA_N         = 20             # MA20 Volume của 20 nến 15M đã đóng trước nến xét
B3B_M15_SPIKE        = 2.5            # Nổ lớn: Vol ≥ 2.5× MA20 (đồng bộ MOM_VOL_SPIKE_MULT Bảng 4)
B3B_M15_FETCH        = 30             # Số nến 15M lấy tươi (đủ 1 nến xét + 20 nền + nến đang chạy)
B3B_M15_OVERHEAT_PCT = 3.0            # Thân nến 15M tăng > 3% (RSI 15M cháy) → ⚠️ Nổ quá nóng, đè mọi nhãn
M15_DUMP, M15_TRIGGER, M15_NEUTRAL, M15_NA = "DUMP", "TRIGGER", "NEUTRAL", "NA"
M15_OVERHEAT = "OVERHEAT"
M15_LABELS = {
    M15_OVERHEAT: "⚠️ Nổ quá nóng - Chờ hồi",
    M15_DUMP:    "⏳ Đang bị xả 15M - Chờ",
    M15_TRIGGER: "⚡ Điểm nổ 15M - Kích hoạt",
    M15_NEUTRAL: "➖ Trung tính 15M",
    M15_NA:      "N/A (thiếu dữ liệu 15M)",
}

B3B_EXCLUDE = ("UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT", "USDCUSDT",
               "FDUSDUSDT", "TUSDUSDT", "DAIUSDT", "EURUSDT")


# ═════════════════════════════════════════════════════════════════════════════
# 1. CHUẨN BỊ DỮ LIỆU 3D
# ═════════════════════════════════════════════════════════════════════════════
def build_df_3d(df_1d: pd.DataFrame, live_price: float = None) -> pd.DataFrame:
    """
    Gộp 1D → 3D và tính đủ cột: open, high, low, close, volume, ma7, ma25, ma99,
    boll_up, boll_mid, boll_dn, supertrend, st_dir.
    live_price: vá nến 1D cuối (close/high/low) bằng giá live trước khi gộp.
    """
    if df_1d is None or len(df_1d) < 3:
        return pd.DataFrame()
    d = df_1d.rename(columns=str.lower).copy()
    for col in ("open", "high", "low", "close", "volume"):
        d[col] = pd.to_numeric(d[col], errors="coerce")

    if live_price and live_price > 0:
        i = d.index[-1]
        d.at[i, "close"] = live_price
        d.at[i, "high"] = max(float(d.at[i, "high"]), live_price)
        d.at[i, "low"] = min(float(d.at[i, "low"]), live_price)

    df = resample_1d_to_3d(d)
    if df.empty:
        return df

    c = df["close"].astype(float)
    df["ma7"] = c.rolling(7).mean()
    df["ma25"] = c.rolling(25).mean()
    df["ma99"] = c.rolling(99).mean()
    df["boll_mid"] = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)          # ddof=0 — đồng bộ compute_3d_indicators
    df["boll_up"] = df["boll_mid"] + 2.0 * std
    df["boll_dn"] = df["boll_mid"] - 2.0 * std

    st_val, st_dir = _supertrend_val_dir(df, period=10, multiplier=3.0)
    df["supertrend"] = np.nan
    df["st_dir"] = np.nan
    df.loc[df.index[-1], "supertrend"] = st_val
    df.loc[df.index[-1], "st_dir"] = st_dir
    return df


def taker_buy_ratio_24h(df_1h: pd.DataFrame) -> float:
    """Taker Buy Quote / Quote Volume của 24 nến 1H gần nhất. Thiếu dữ liệu → 0.5 (trung tính)."""
    try:
        if df_1h is None or len(df_1h) < 24:
            return 0.5
        d = df_1h.rename(columns=str.lower).tail(24)
        tb = pd.to_numeric(d.get("taker_buy_quote"), errors="coerce").sum()
        qv = pd.to_numeric(d.get("quote_volume"), errors="coerce").sum()
        return float(tb / qv) if qv > 0 else 0.5
    except Exception:
        return 0.5


# ═════════════════════════════════════════════════════════════════════════════
# 2. CHẤM ĐIỂM
# ═════════════════════════════════════════════════════════════════════════════
def volume_time_weight(open_time_ms, now_ms=None, candle_ms: int = B3B_CANDLE_MS) -> dict:
    """
    Tỷ trọng thời gian của nến 3D đang chạy + hệ số nội suy Volume (đã qua chốt chặn <10% / ≤5.0x).
    Ủy quyền cho tiện ích dùng chung core.volume_projection (giữ chữ ký cũ cho Bảng 3B / Unit Test).
    """
    from core.volume_projection import volume_time_weight as _vtw
    return _vtw(open_time_ms, candle_ms, now_ms, min_frac=B3B_PROJ_MIN_FRAC, max_mult=B3B_PROJ_MAX_MULT)


def score_super_wave_3d(df_3d: pd.DataFrame, taker_buy_ratio: float, now_ms: int = None):
    """
    df_3d: DataFrame nến 3D (build_df_3d) với cột close, open, high, low, volume, ma7, ma25, ma99,
           boll_up, boll_mid, boll_dn, supertrend, st_dir (+ open_time để nội suy Volume)
    taker_buy_ratio: Tỷ lệ Taker Buy 24h (float, VD 0.58)
    now_ms: mốc thời gian hiện tại (ms) — để Unit Test; mặc định = time.time()
    Trả về (score, detail_dict).
    """
    if df_3d is None or len(df_3d) < B3B_MIN_CANDLES_3D:
        return 0, {"status": "INSUFFICIENT_DATA"}

    current = df_3d.iloc[-1]
    prev = df_3d.iloc[-2]

    price = float(current["close"])
    ma7, ma25, ma99 = float(current["ma7"]), float(current["ma25"]), float(current["ma99"])
    boll_up, boll_mid, boll_dn = float(current["boll_up"]), float(current["boll_mid"]), float(current["boll_dn"])
    st_val = float(current["supertrend"])
    st_dir = int(current["st_dir"]) if not pd.isna(current["st_dir"]) else -1
    vol_curr = float(current["volume"])
    # MA5 Vol = 5 nến 3D ĐÃ ĐÓNG trước nến hiện tại (so sánh cùng chuẩn 72h đầy đủ)
    vol_ma5 = float(df_3d["volume"].iloc[-6:-1].mean())

    if any(pd.isna(x) for x in (ma7, ma25, ma99, boll_up, boll_mid, boll_dn, st_val)):
        return 0, {"status": "INSUFFICIENT_DATA"}

    # ── 1. HARD GATE: LỌC BỎ NẾU GÃY ĐƯỜNG RAY ──────────────────────────────
    if st_dir != 1 or price <= st_val or price < ma7 or ma7 <= ma25:
        return 0, {"status": "REJECTED_STRICT_GATE"}

    score = 0
    signals = []
    pts = {"rail": 0, "cross": 0, "boll": 0, "flow": 0, "bonus": 0, "explosive": 0, "penalty": 0}

    # ── 2. CẤU TRÚC ĐƯỜNG RAY (30đ) ─────────────────────────────────────────
    # Gia tốc móng: MA25 3D dốc lên (P > MA7 > MA25 đã nằm trong Hard Gate → không tặng điểm trùng)
    ma25_prev = float(prev["ma25"]) if not pd.isna(prev["ma25"]) else ma25
    ma25_rising = ma25 > ma25_prev
    if ma25_rising:
        pts["rail"] += 15
        signals.append("MA25_Slope_Up")
    if price > ma99:
        pts["rail"] += 15
        signals.append("Above_MA99")

    # ── 3. GIAO CẮT VÀNG (25đ) — cắt lên trong 3 nến 3D gần nhất ────────────
    win = B3B_CROSS_LOOKBACK + 1
    ma7_s = df_3d["ma7"].iloc[-win:].values
    ma99_s = df_3d["ma99"].iloc[-win:].values
    recent_cross = False
    if not np.isnan(ma99_s).any():
        recent_cross = any(ma7_s[i - 1] <= ma99_s[i - 1] and ma7_s[i] > ma99_s[i] for i in range(1, win))
    golden = "BELOW"
    if recent_cross:
        pts["cross"] = 25
        signals.append("Golden_Cross_Recent")
        golden = "RECENT"
    elif ma7 > ma99:
        pts["cross"] = 15
        signals.append("Golden_Trend")
        golden = "TREND"

    # ── 4. ĐỘ MỞ DẢI BOLLINGER (25đ) ────────────────────────────────────────
    if price >= boll_up:
        pts["boll"] += 15
        signals.append("Break_Boll_Up")
    bw_curr = (boll_up - boll_dn) / boll_mid if boll_mid > 0 else 0.0
    bw_prev = (float(prev["boll_up"]) - float(prev["boll_dn"])) / float(prev["boll_mid"]) \
        if float(prev["boll_mid"] or 0) > 0 else 0.0
    band_expanding = bw_curr > bw_prev
    if band_expanding:
        pts["boll"] += 10
        signals.append("Band_Expanding")

    # ── 5. DÒNG TIỀN VĨ MÔ (20đ) — Volume Dự Phóng theo tỷ trọng thời gian ──────
    tw = volume_time_weight(current.get("open_time"), now_ms)
    vol_proj = vol_curr * tw["mult"]
    vol_ratio_raw = vol_curr / vol_ma5 if vol_ma5 > 0 else 0.0
    vol_ratio = vol_proj / vol_ma5 if vol_ma5 > 0 else 0.0
    if vol_ratio >= B3B_VOL_MULT:
        pts["flow"] += 10
        signals.append("Vol_Explosion")
    if taker_buy_ratio >= B3B_TAKER_MIN:
        pts["flow"] += 10
        signals.append("Taker_Bullish")
    explosive = vol_ratio >= B3B_EXPLOSIVE_MULT
    if explosive:
        pts["explosive"] = B3B_EXPLOSIVE_BONUS
        signals.append("Explosive_Money")

    # ── BONUS: NẾN 3D LIỀN TRƯỚC (ĐÃ ĐÓNG) XANH THÂN ĐẶC (+10đ) ───────────────
    # Cụm 3D cuối = [t-2, t-1, t] còn chạy → cụm trước [t-5..t-3] gồm 3 nến 1D đã đóng hoàn toàn
    po, ph, pl, pc = (float(prev[k]) for k in ("open", "high", "low", "close"))
    prev_range = ph - pl
    prev_wick_pct = (ph - max(po, pc)) / prev_range if prev_range > 0 else 1.0
    if pc > po and prev_range > 0 and prev_wick_pct < B3B_MAX_UPPER_WICK:
        pts["bonus"] = 10
        signals.append("Solid_Bull_Body_Prev")

    # ── PHẠT: FOMO XA RAY (−15đ) — bảo vệ vốn, ưu tiên chân sóng sát bệ MA7 ────────
    dist_ma7_pct = (price - ma7) / ma7 * 100 if ma7 > 0 else 0.0
    if dist_ma7_pct > B3B_OVEREXT_PCT:
        pts["penalty"] = -B3B_OVEREXT_PENALTY
        signals.append("FOMO_Overextended")

    score = sum(pts.values())
    return score, {
        "status": "OK",
        "score": score,
        "pts": pts,
        "signals": signals,
        "price": price,
        "ma7": ma7, "ma25": ma25, "ma99": ma99,
        "ma25_prev": ma25_prev, "ma25_rising": ma25_rising,
        "ma25_slope_pct": (ma25 - ma25_prev) / ma25_prev * 100 if ma25_prev > 0 else 0.0,
        "prev_bull": pc > po, "prev_wick_pct": prev_wick_pct,
        "boll_up": boll_up, "boll_mid": boll_mid, "boll_dn": boll_dn,
        "bw_curr": bw_curr, "bw_prev": bw_prev, "band_expanding": band_expanding,
        "st": st_val,
        "golden": golden,
        "vol_ratio": vol_ratio,
        "vol_ratio_raw": vol_ratio_raw,
        "vol_proj_frac": tw["frac"], "vol_proj_mult": tw["mult"], "vol_proj_applied": tw["applied"],
        "explosive": explosive,
        "taker": taker_buy_ratio,
        "dist_ma7_pct": dist_ma7_pct,
    }


def _recommend_action(d: dict) -> dict:
    """Hành động khuyến nghị + SL (SL = max(Supertrend 3D, MA7 3D × 0.96) — mốc gãy ray gần nhất)."""
    sl = max(d["st"], d["ma7"] * B3B_SL_MA7_RATIO)
    strong = d["score"] >= 90 and ("Break_Boll_Up" in d["signals"] or d["golden"] == "RECENT")
    if d["dist_ma7_pct"] > B3B_OVEREXT_PCT:
        label = f"⚠️ XA RAY {d['dist_ma7_pct']:.0f}%: Chờ hồi MA7 ({{ma7}}) / SL {{sl}}"
        mode = "OVEREXTENDED"
    elif strong:
        label = "🚀 NUÔI SÓNG: Wide Grid 3D / Chặn SL {sl}"
        mode = "RIDE_WAVE"
    else:
        label = "⚡ LƯỚT THEO RAY: Đón nhúng MA7 ({ma7})"
        mode = "RAIL_SURF"
    return {"sl": sl, "mode": mode, "label_tpl": label}


def _tick_size_of(symbol: str):
    """tick_size từ ExchangeInfoCache nếu đã load (không tự gọi API). Chưa sẵn sàng → None."""
    try:
        from core.exchange_info_cache import ExchangeInfoCache
        c = ExchangeInfoCache()
        return c.get_tick_size(symbol, 0.0) or None if c.is_ready() else None
    except Exception:
        return None


def build_wave_setup(symbol: str, ma7: float, sl: float, recent_high: float, tick_size: float = None):
    """
    🌊 [SÓNG 3D - XXX] Low - Up | NL | Trig | SL | TP  (thứ tự nhập liệu Binance Spot Grid)
      Trig = MA7 3D (đón nhúng về ray) — ST 3D nằm trên MA7 thì nâng Trig ≥ SL / 0.97
      SL   = SL Ray = max(ST 3D, MA7 × 0.96)
      TP   = max(Trig + 3R, Đỉnh 10 nến 3D gần nhất)      | Low = SL × 1.005 | Up = TP
    Trả về dict hoặc None nếu dữ liệu không hợp lệ.
    """
    from core.grid_tp2_builder import _clean_symbol, _make_formatter, GRID_STEP_PCT, MIN_GRIDS, MAX_GRIDS
    from core.macro_levels import _round_tick
    try:
        ma7, sl, recent_high = float(ma7), float(sl), float(recent_high or 0)
        if ma7 <= 0 or sl <= 0:
            return None
        trig = max(ma7, sl / B3B_TRIG_MIN_SL_GAP)
        tp = max(trig + B3B_TP_R_MULT * (trig - sl), recent_high)
        low = sl * B3B_GRID_LOW_BUFFER
        if tick_size and tick_size > 0:
            trig, sl, tp, low = (_round_tick(x, tick_size) for x in (trig, sl, tp, low))
        if not (0 < sl < low < trig < tp):
            return None
        grids = max(MIN_GRIDS, min(int((tp - low) / low / GRID_STEP_PCT), MAX_GRIDS))
        rr = (tp - trig) / (trig - sl)
        f = _make_formatter(tick_size)
        sym = _clean_symbol(symbol)
        return {
            "symbol": sym, "lower": low, "upper": tp, "grids": grids, "trigger": trig, "stop_loss": sl,
            "take_profit": tp, "rr": rr,
            "display_line": (f"🌊 [SÓNG 3D - {sym}] {f(low)} - {f(tp)} | {grids}L | Trig: {f(trig)} | "
                             f"SL: {f(sl)}({(sl / trig - 1) * 100:+.1f}%) | TP: {f(tp)}({(tp / trig - 1) * 100:+.1f}%) "
                             f"| R:R=1:{rr:.1f}"),
        }
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ═════════════════════════════════════════════════════════════════════════════
# 3. QUÉT THỊ TRƯỜNG
# ═════════════════════════════════════════════════════════════════════════════
def analyze_super_wave(symbol: str, live_info: dict):
    """Phân tích 1 mã. Trả về dict kết quả nếu ≥ ngưỡng, ngược lại None. Fail-safe."""
    try:
        from core.klines_cache import get_klines_cached
        df_1d = get_klines_cached(symbol, "1d", limit=B3B_FETCH_1D)
        if df_1d is None or len(df_1d) < B3B_MIN_CANDLES_3D * 3:
            return None
        live_price = float((live_info or {}).get("close") or 0) or None
        df_3d = build_df_3d(df_1d, live_price)
        if len(df_3d) < B3B_MIN_CANDLES_3D:
            return None

        # Gate rẻ trước khi tốn công lấy nến 1H
        last = df_3d.iloc[-1]
        if last["st_dir"] != 1 or last["close"] < last["ma7"] or last["ma7"] <= last["ma25"]:
            return None

        df_1h = get_klines_cached(symbol, "1h", limit=24)
        score, d = score_super_wave_3d(df_3d, taker_buy_ratio_24h(df_1h))
        if d.get("status") != "OK" or score < B3B_MIN_SCORE:
            return None

        d.update(_recommend_action(d))
        d["symbol"] = symbol[:-4] if symbol.endswith("USDT") else symbol
        d["quote_vol"] = float((live_info or {}).get("quote_vol", 0) or 0)
        d["recent_high_3d"] = float(pd.to_numeric(df_3d["high"], errors="coerce").iloc[-B3B_TP_HIGH_LOOKBACK:].max())
        d["wave_setup"] = build_wave_setup(symbol, d["ma7"], d["sl"], d["recent_high_3d"], _tick_size_of(symbol))
        return d
    except Exception as e:
        logger.debug(f"[B3B] {symbol}: {e}")
        return None


def scan_super_wave_3d(live_data_map: dict, top_n: int = B3B_TOP_N, exclude_symbols=None) -> list:
    """Quét Top thanh khoản → chấm điểm → trả về Top N (sort Điểm↓, Vol ratio↓)."""
    if not live_data_map:
        return []
    exclude_symbols = set(exclude_symbols or ())
    pool = [
        (s, info) for s, info in live_data_map.items()
        if s.endswith("USDT") and not any(x in s for x in B3B_EXCLUDE) and s not in exclude_symbols
        and float(info.get("quote_vol", 0) or 0) >= B3B_MIN_VOL_USDT
        and float(info.get("change_24h", 0) or 0) >= B3B_MIN_24H_CHG
    ]
    pool.sort(key=lambda x: float(x[1].get("quote_vol", 0) or 0), reverse=True)
    pool = pool[:B3B_SCAN_TOP_N]

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = [r for r in ex.map(lambda p: analyze_super_wave(p[0], p[1]), pool) if r]
    results.sort(key=lambda r: (r["score"], r["vol_ratio"]), reverse=True)
    results = results[:top_n]

    # Lớp Micro-Trigger 15M: chỉ cho Top N cuối (top_n lệnh gọi API tươi), KHÔNG đổi điểm/thứ hạng
    if results:
        with ThreadPoolExecutor(max_workers=min(5, len(results))) as ex:
            m15 = list(ex.map(lambda r: fetch_micro_trigger_15m(r["symbol"] + "USDT"), results))
        for r, m in zip(results, m15):
            r["m15"] = m
    global _LAST_RESULTS
    _LAST_RESULTS = list(results)
    return results


_LAST_RESULTS: list = []


def get_last_super_wave_results() -> list:
    """Kết quả lần quét Bảng 3B gần nhất (cho BẢNG TỔNG KẾT — không quét lại, 0 API call).
    Mỗi phần tử bổ sung `action_short`, `m15_short` (chuỗi hiển thị ngắn)."""
    out = []
    for r in _LAST_RESULTS:
        r = dict(r)
        r["action_short"] = _ACTION_SHORT.get(r.get("mode"), r.get("mode", ""))
        r["m15_short"] = _M15_SHORT.get((r.get("m15") or {}).get("state"), "N/A")
        out.append(r)
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 3B. LỚP MICRO-TRIGGER 15M (Sniper — quyết định có bấm mua NGAY hay không)
# ═════════════════════════════════════════════════════════════════════════════
def evaluate_micro_trigger_15m(df_15m: pd.DataFrame, now_ms: int = None) -> dict:
    """
    Xét nến 15M VỪA ĐÓNG gần nhất (loại nến đang chạy theo close_time > now → không nhiễu Volume thiếu giờ).
      🔴 Đỏ  & Vol > MA20          → DUMP    (⏳ Đang bị xả 15M - Chờ)
      🟢 Xanh & Vol ≥ 2.5× MA20   → TRIGGER (⚡ Điểm nổ 15M - Kích hoạt)
      còn lại                     → NEUTRAL
    ⚠️ CHỐT NỔ QUÁ NÓNG (ưu tiên cao nhất, bất chấp Volume): nến 15M vừa đóng HOẶC nến 15M đang chạy
       (Giá live − Open) / Open > 3% → OVERHEAT (⚠️ Nổ quá nóng - Chờ hồi) — tránh ăn nhịp giật râu xả ngược.
    MA20 = trung bình Volume 20 nến 15M đã đóng TRƯỚC nến xét.
    """
    import time as _t
    na = {"state": M15_NA, "label": M15_LABELS[M15_NA], "vol_ratio": None, "change_pct": None,
          "live_change_pct": None}
    try:
        if df_15m is None or df_15m.empty:
            return na
        d = df_15m.rename(columns=str.lower).copy()
        for col in ("open", "close", "volume", "close_time"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        now_ms = int(now_ms if now_ms is not None else _t.time() * 1000)
        closed = d[d["close_time"] < now_ms].reset_index(drop=True)
        if len(closed) < B3B_M15_MA_N + 1:
            return na
        last = closed.iloc[-1]
        ma20 = float(closed["volume"].iloc[-(B3B_M15_MA_N + 1):-1].mean())
        o, c, v = float(last["open"]), float(last["close"]), float(last["volume"])
        ratio = v / ma20 if ma20 > 0 else 0.0
        chg = (c - o) / o * 100 if o > 0 else 0.0
        # Nến 15M đang chạy (close = giá live) — chỉ dùng cho chốt Nổ quá nóng, KHÔNG dùng Volume thiếu giờ
        running = d[d["close_time"] >= now_ms]
        live_chg = None
        if not running.empty:
            ro, rc = float(running.iloc[-1]["open"]), float(running.iloc[-1]["close"])
            live_chg = (rc - ro) / ro * 100 if ro > 0 else None
        if round(chg, 6) > B3B_M15_OVERHEAT_PCT or (live_chg is not None and round(live_chg, 6) > B3B_M15_OVERHEAT_PCT):
            state = M15_OVERHEAT
        elif c < o and ratio > 1.0:
            state = M15_DUMP
        elif c > o and ratio >= B3B_M15_SPIKE:
            state = M15_TRIGGER
        else:
            state = M15_NEUTRAL
        return {"state": state, "label": M15_LABELS[state], "vol_ratio": ratio, "change_pct": chg,
                "live_change_pct": live_chg, "close_time": int(last["close_time"])}
    except Exception:
        return na


def fetch_micro_trigger_15m(symbol: str) -> dict:
    """Lấy nến 15M TƯƠI từ API (bỏ qua cache 15 phút — tín hiệu bóp cò cần real-time). Fail-safe → N/A."""
    try:
        from core.klines_cache import _fetch_from_binance
        return evaluate_micro_trigger_15m(_fetch_from_binance(symbol, "15m", B3B_M15_FETCH))
    except Exception as e:
        logger.debug(f"[B3B-15M] {symbol}: {e}")
        return {"state": M15_NA, "label": M15_LABELS[M15_NA], "vol_ratio": None, "change_pct": None}


# ═════════════════════════════════════════════════════════════════════════════
# 4. HIỂN THỊ
# ═════════════════════════════════════════════════════════════════════════════
# Thứ tự cột: thông tin RA QUYẾT ĐỊNH trước (Điểm → Hành động → 15M → Giá / Vùng mua / SL), cấu trúc 3D sau
_WCOLS3B = [8, 5, 17, 16, 11, 11, 11, 8, 9, 42]
_ACTION_SHORT = {"RIDE_WAVE": "🚀 NUÔI SÓNG", "RAIL_SURF": "⚡ LƯỚT RAY", "OVEREXTENDED": "⚠️ XA RAY-Chờ hồi"}
_M15_SHORT = {M15_OVERHEAT: "⚠️ Quá nóng-Chờ", M15_DUMP: "⏳ Bị xả-Chờ", M15_TRIGGER: "⚡ Nổ-Kích hoạt",
              M15_NEUTRAL: "➖ Trung tính", M15_NA: "N/A"}


def _structure_tags(r: dict) -> str:
    """Cấu trúc 3D gói 1 ô: MA25 dốc · vị trí MA99 · giao cắt · Bollinger."""
    tags = ["MA25↗" if r["ma25_rising"] else "MA25↘",
            ">MA99" if "Above_MA99" in r["signals"] else "<MA99"]
    if r["golden"] == "RECENT":
        tags.append("✂️ Cắt vàng")
    if r["price"] >= r["boll_up"]:
        tags.append("Vượt Boll")
    elif r["price"] >= r["boll_up"] * B3B_NEAR_BOLL_UP:
        tags.append("Sát Boll")
    elif r["band_expanding"]:
        tags.append("Boll nở")
    return " · ".join(tags)


def _detail_line(r: dict, m15: dict) -> str:
    """1 dòng chi tiết: điểm thành phần (chỉ hiện thưởng/phạt khi có) | Taker | 15M."""
    p = r["pts"]
    parts = [f"Ray {p['rail']}/30", f"Cross {p['cross']}/25", f"Boll {p['boll']}/25", f"Tiền {p['flow']}/20"]
    if p.get("bonus"):
        parts.append(f"Nến 3D đặc +{p['bonus']}")
    if p.get("explosive"):
        parts.append(f"💥 +{p['explosive']}")
    if p.get("penalty"):
        parts.append(f"🔴 FOMO {p['penalty']}")
    tail = [f"Taker {r['taker']*100:.0f}%"]
    if m15.get("change_pct") is not None:
        s = f"15M {m15['change_pct']:+.2f}% vol x{m15['vol_ratio']:.1f}"
        if m15.get("live_change_pct") is not None:
            s += f" (đang chạy {m15['live_change_pct']:+.1f}%)"
        tail.append(s)
    return "    ↳ " + " · ".join(parts) + " | " + " · ".join(tail)


def print_super_wave_table(results: list):
    """In 🌊 BẢNG 3B theo định dạng log BinaC4 — gọn, thông tin chính đứng trước."""
    from core.coin_filter import smart_price, ljust_w, trunc_w, _SEP

    tw = sum(_WCOLS3B) + len(_SEP) * (len(_WCOLS3B) - 1)

    def row(cells):
        return _SEP.join(ljust_w(trunc_w(str(c), w), w) for c, w in zip(cells, _WCOLS3B))

    print("=" * tw)
    print(f"🌊 BẢNG 3B: SIÊU SÓNG TĂNG 3D — TOP {B3B_TOP_N} (≥ {B3B_MIN_SCORE}đ / 100)")
    print("=" * tw)
    if not results:
        print(f"⚠️ KHÔNG CÓ MÃ NÀO VƯỢT CỔNG ĐƯỜNG RAY 3D ĐẠT ≥ {B3B_MIN_SCORE} ĐIỂM.")
        print("=" * tw + "\n")
        return

    print(row(["Mã", "Điểm", "Hành Động", "15M", "Giá Live", "Mua ~MA7", "SL Ray", "Cách MA7", "Vol 3D",
               "Cấu Trúc 3D"]))
    print("-" * tw)
    for r in results:
        m15 = r.get("m15") or {"state": M15_NA}
        vol = ("💥" if r.get("explosive") else "") + f"x{r['vol_ratio']:.2f}" + ("*" if r.get("vol_proj_applied") else "")
        print(row([r["symbol"], f"{float(r['score']):.0f}", _ACTION_SHORT.get(r.get("mode"), r.get("mode", "")),
                   _M15_SHORT.get(m15.get("state"), "N/A"), smart_price(r["price"]), smart_price(r["ma7"]),
                   smart_price(r["sl"]), f"{r['dist_ma7_pct']:+.1f}%", vol, _structure_tags(r)]))
        print(_detail_line(r, m15))
        if r.get("wave_setup"):
            print(f"    ↳ {r['wave_setup']['display_line']}")
    print("-" * tw)
    print("ℹ️ Vol 3D = Vol dự phóng / MA5 nến 3D đã đóng (* đã nội suy, 💥 ≥ 3x) | SL Ray = max(ST 3D, MA7×0.96) "
          "| 15M chỉ hiển thị, không đổi điểm 3D")
    print("=" * tw + "\n")
