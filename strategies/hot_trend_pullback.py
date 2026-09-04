"""
Module: hot_trend_pullback.py
Dự án: BinaC4
Mục đích: Động cơ 4 — Bảng Chấm Điểm Hot Trend Pullback
           Săn điểm vào lệnh pullback trên các mã đang tăng mạnh (Hot Trend).
           Hoạt động HOÀN TOÀN ĐỘC LẬP với watchlist tích lũy của coin_filter.
           Dùng trực tiếp Binance REST API (qua get_klines_live) — không qua ccxt.

Thang điểm: 100 điểm (C1-C5) + điều chỉnh C0 Vĩ Mô

  C0 — Vị Thế Chu Kỳ Vĩ Mô (Macro Cycle Detector / Wyckoff + VSA):
    C0.1 Chiết Khấu 180D   : -20 → +20 | Phân biệt Chân Sóng vs Đu Đỉnh Phân Phối
    C0.2 Ngâm Móng 60D     :   0 → +15 | Biên độ nén < 30% = nền tảng tích lũy vững
    C0.3 VSA No Supply     :   0 → +10 | Đếm nến đỏ/doji Vol < 0.5x avg (Cạn Cung)
    C0.4 MA99 Slope 1D     :  -5 → +10 | MA99 Flat + Price Above = Golden Transition
  Tổng C0: tối đa +55, tối thiểu -25 (điều chỉnh cộng/trừ vào tổng điểm)

  C1 — Uptrend Xác Nhận  : 25đ | EMA20 > EMA50 (1H) + RSI 1H phân cấp lũy giảm
  C2 — Pullback Chất Lượng: 25đ | Điều chỉnh 3–15% từ Swing High, không phá EMA50
  C3 — Volume Cạn Kiệt   : 20đ | Vol xả < 60% vol đẩy đỉnh sóng (10 nến 1H gần nhất)
  C4 — Bệ Đỡ Kỹ Thuật   : 20đ | EMA20 / MA25 / Fib 0.382 / Fib 0.500 (±1.5%)
  C5 — Taker Buy Hồi Phục: 10đ | Taker Buy Quote (USDT) / Quote Vol ≥ 55% (15M)

Lưu ý RSI C1 (lũy giảm):
  RSI 50–70  → 25đ | Setup Full Vol
  RSI 70–75  → 20đ | Tín hiệu tốt, dời SL sớm
  RSI 75–80  → 10đ | Giới hạn 50% vốn
  RSI > 80   →  0đ | Climax — TỪ CHỐI, chờ xả thực sự
  RSI < 50   →  0đ | Chưa đủ momentum
"""

import time
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

from core.coin_filter import get_klines_live, EXCLUDE


# ─── Tham Số Cấu Hình ──────────────────────────────────────────────────────────
HTB_MIN_CHANGE_24H = 5.0         # Tăng 24h tối thiểu (%) để lọc vào Hot Trend Pool
HTB_MIN_VOL_USDT   = 3_000_000   # Vol 24h tối thiểu (3M USDT)
HTB_MIN_VOLA_24H   = 3.0         # Biến động tối thiểu (H-L)/L trong 24h (%)
HTB_TOP_N          = 60          # Số mã quét tối đa (cân bằng tốc độ / độ bao phủ)
HTB_WORKERS        = 20          # ThreadPoolExecutor workers
HTB_RESULT_TOP     = 20          # Top 20 kết quả (5 mã in chi tiết, còn lại in tóm tắt)


# ─── Hàm Tính RSI (Nội Bộ) ─────────────────────────────────────────────────────
def _calc_rsi(series: pd.Series, period: int = 14) -> float:
    closes = pd.to_numeric(series, errors='coerce').dropna()
    if len(closes) < period + 1:
        return 50.0
    deltas   = closes.diff()
    gains    = deltas.mask(deltas < 0, 0)
    losses   = -deltas.mask(deltas > 0, 0)
    avg_gain = gains.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = losses.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-9)
    rsi      = 100 - (100 / (1 + rs))
    val      = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0


# ─── C1: RSI Phân Cấp Lũy Giảm ────────────────────────────────────────────────
def _score_rsi_c1(rsi: float) -> tuple:
    """Trả về (score, label) theo bảng phân cấp đã định nghĩa."""
    if rsi < 50:
        return 0,  f"RSI Yếu ({rsi:.1f}) — Chưa đủ momentum"
    elif rsi <= 70:
        return 25, f"RSI Lý Tưởng ({rsi:.1f}) — Setup Full Vol"
    elif rsi <= 75:
        return 20, f"RSI Nóng ({rsi:.1f}) — Tín Hiệu Tốt, Dời SL Sớm"
    elif rsi <= 80:
        return 10, f"RSI Quá Mua ({rsi:.1f}) — Giới Hạn 50% Vốn"
    else:
        return 0,  f"RSI Climax ({rsi:.1f}) — TỪ CHỐI (Chờ Xả Thực Sự)"


# ─── Tìm Swing High / Low (Khung 1H) ───────────────────────────────────────────
def _swing_high(df_1h: pd.DataFrame, lookback: int = 48) -> float:
    """Max High trong lookback nến 1H đã đóng (không tính nến đang mở)."""
    window = df_1h['High'].iloc[-(lookback + 1):-1]
    return float(window.max()) if len(window) > 0 else 0.0


def _swing_low(df_1h: pd.DataFrame, lookback: int = 48) -> float:
    """Min Low tương ứng để tính Fib Retracement."""
    window = df_1h['Low'].iloc[-(lookback + 1):-1]
    return float(window.min()) if len(window) > 0 else 0.0


# ─── C0: Macro Cycle Detector (Wyckoff + VSA) ──────────────────────────────────
def _check_c0_cycle(df_1d: pd.DataFrame) -> dict:
    """
    C0: Vị Thế Chu Kỳ Vĩ Mô — Phân biệt "Chân Sóng Tăng" vs "Kéo Xả Phân Phối Đỉnh".

    4 điểm lịch sử theo phương pháp Wyckoff & VSA:

    C0.1 — Chiết Khấu 180D (Distance from 180D High)
      Nguyên lý: Mã chiết khấu sâu > 40-60% từ đỉnh 6 tháng mà nổ Vol xanh
                 → xác suất cao là bắt đầu chu kỳ Markup mới.
      Scoring: drawdown < -60% → +20 | < -40% → +12 | < -15% → 0 | ≥ -15% → -20

    C0.2 — Thời Gian Ngâm Móng (60D Accumulation Time Base)
      Nguyên lý: Nền móng tích lũy ≥ 30-60 ngày biên hẹp = cá mập đang gom hàng.
      Scoring: range 60D < 20% → +15 | < 30% → +8 | < 50% → +3 | ≥ 50% → 0

    C0.3 — VSA No Supply (Historical Spring / Test)
      Nguyên lý: Nến đỏ/doji với Volume < 0.5x avg = cạn cung lịch sử (No Supply).
      Scoring: ≥ 6 nến → +10 | ≥ 3 nến → +5 | < 3 → 0

    C0.4 — MA99 Slope Transition (Golden Transition)
      Nguyên lý: MA99 1D chuyển từ dốc xuống → nằm ngang, giá cắt lên trên
                 = điểm chuyển giao chu kỳ cực kỳ có ý nghĩa.
      Scoring: Flat(-1%~+1%) + Price > MA99 → +10 | Dốc xuống + Price > MA99 → +3
               Price < MA99 → -5

    Args:
        df_1d: DataFrame 1D với limit=180 từ get_klines_live()

    Returns:
        dict: {'score': int, 'label': str, 'detail': dict}
    """
    if df_1d is None or len(df_1d) < 30:
        return {'score': 0, 'label': 'Không đủ dữ liệu lịch sử 1D', 'detail': {}}

    # Binance REST trả về tất cả cột dưới dạng string → cast sang numeric
    for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Quote_Volume']:
        if col in df_1d.columns:
            df_1d[col] = pd.to_numeric(df_1d[col], errors='coerce')

    close_live = float(df_1d['Close'].iloc[-1])

    # ── C0.1: Chiết Khấu 180D ─────────────────────────────────────────────────
    n_bars_180  = min(180, len(df_1d))
    high_180d   = float(df_1d['High'].tail(n_bars_180).max())
    drawdown_180 = (close_live - high_180d) / high_180d * 100 if high_180d > 0 else 0.0

    if drawdown_180 < -60:
        s01 = 20
        l01 = f"Chân Sóng Sâu ({drawdown_180:.0f}% từ đỉnh 180D) ✅"
    elif drawdown_180 < -40:
        s01 = 12
        l01 = f"Vùng Tích Lũy ({drawdown_180:.0f}% từ đỉnh 180D)"
    elif drawdown_180 < -15:
        s01 = 0
        l01 = f"Vùng Trung Tính ({drawdown_180:.0f}% từ đỉnh 180D)"
    else:
        s01 = -20
        l01 = f"Sát Đỉnh 180D ({drawdown_180:.0f}%) — Rủi Ro Phân Phối ⚠️"

    # ── C0.2: Thời Gian Ngâm Móng (60D Accumulation Box) ─────────────────────
    n_bars_60 = min(60, len(df_1d))
    df_60d    = df_1d.tail(n_bars_60)
    high_60d  = float(df_60d['High'].max())
    low_60d   = float(df_60d['Low'].min())
    range_60d = (high_60d - low_60d) / low_60d * 100 if low_60d > 0 else 999.0

    if range_60d < 20:
        s02 = 15
        l02 = f"Nền Cực Chắc ({range_60d:.0f}% biên 60D) ✅"
    elif range_60d < 30:
        s02 = 8
        l02 = f"Nền Tốt ({range_60d:.0f}% biên 60D)"
    elif range_60d < 50:
        s02 = 3
        l02 = f"Nền Trung Bình ({range_60d:.0f}% biên 60D)"
    else:
        s02 = 0
        l02 = f"Chưa Có Nền ({range_60d:.0f}% biên 60D)"

    # ── C0.3: VSA No Supply (30 nến 1D gần nhất) ─────────────────────────────
    # Nến đỏ hoặc doji (Close <= Open) mà Volume < 0.5x trung bình
    # → Tín hiệu cạn cung lịch sử tại vùng giá thấp
    df_30d   = df_1d.tail(30)
    avg_vol  = float(df_30d['Volume'].mean()) if len(df_30d) > 0 else 0.0

    if avg_vol > 0:
        is_red_doji      = df_30d['Close'] <= df_30d['Open']
        is_low_vol       = df_30d['Volume'] < (avg_vol * 0.5)
        no_supply_count  = int((is_red_doji & is_low_vol).sum())
    else:
        no_supply_count = 0

    if no_supply_count >= 6:
        s03 = 10
        l03 = f"No Supply Mạnh ({no_supply_count}/30 nến 1D) ✅"
    elif no_supply_count >= 3:
        s03 = 5
        l03 = f"No Supply ({no_supply_count}/30 nến 1D)"
    else:
        s03 = 0
        l03 = f"Ít No Supply ({no_supply_count}/30 nến 1D)"

    # ── C0.4: MA99 Slope Transition (Golden Transition) ───────────────────────
    # Điều kiện lý tưởng: MA99 từ dốc xuống → nằm ngang (-1%~+1%/30D)
    # VÀ giá đang cắt lên trên MA99 = tín hiệu chuyển giao chu kỳ
    if len(df_1d) >= 100:
        ma99_series = df_1d['Close'].rolling(99).mean()
        ma99_now    = float(ma99_series.iloc[-1])
        ma99_30d    = float(ma99_series.iloc[-31]) if len(ma99_series) >= 31 else ma99_now

        if ma99_now > 0 and ma99_30d > 0:
            slope_30d = (ma99_now - ma99_30d) / ma99_30d * 100

            if -1.0 <= slope_30d <= 1.0 and close_live > ma99_now:
                s04 = 10
                l04 = f"Golden Transition ✅ MA99 Flat({slope_30d:+.1f}%) + Giá Trên MA99"
            elif slope_30d < -1.0 and close_live > ma99_now:
                s04 = 3
                l04 = f"Cắt Lên MA99 Downtrend ({slope_30d:+.1f}%/30D)"
            elif close_live > ma99_now:
                s04 = 0
                l04 = f"MA99 Dốc Lên ({slope_30d:+.1f}%/30D) — Đà Tăng Trưởng"
            else:
                s04 = -5
                l04 = f"Dưới MA99 ({slope_30d:+.1f}%/30D) ⚠️"
        else:
            s04, l04 = 0, "MA99: Dữ liệu chưa sẵn"
    else:
        s04, l04 = 0, f"MA99: Cần thêm {100 - len(df_1d)} nến 1D"

    # ── Tổng Hợp C0 ───────────────────────────────────────────────────────────
    total_c0 = s01 + s02 + s03 + s04

    if total_c0 >= 35:
        phase = "CHÂN SÓNG TĂNG"
        phase_icon = "🚀"
    elif total_c0 >= 15:
        phase = "TÍCH LŨY / HỒI PHỤC"
        phase_icon = "🟢"
    elif total_c0 >= -5:
        phase = "TRUNG TÍNH"
        phase_icon = "🟡"
    else:
        phase = "RỦI RO PHÂN PHỐI"
        phase_icon = "🔴"

    return {
        'score':  total_c0,
        'label':  f"{phase_icon} {phase} | C0={total_c0:+d}đ",
        'detail': {
            'C0.1 Drawdown180D':   l01,
            'C0.2 NgâmMóng60D':    l02,
            'C0.3 NoSupply VSA':   l03,
            'C0.4 MA99 Slope':     l04,
            'drawdown_180d_pct':   round(drawdown_180, 1),
            'range_60d_pct':       round(range_60d, 1),
            'no_supply_count':     no_supply_count,
        }
    }


# ─── Class Chính ────────────────────────────────────────────────────────────────
class HotTrendPullback:
    """
    Động Cơ 4 — Hot Trend Pullback Scorer.

    Sử dụng static/class methods để tránh phụ thuộc vào exchange object.
    Dùng get_klines_live() (Binance REST trực tiếp) giống như coin_filter.
    """

    # ──────────────────────────────────────────────────────────────────────────
    # API công khai (dùng trong main.py)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_hot_trend_symbols(live_data_map: dict, top_n: int = HTB_TOP_N) -> list:
        """
        Lọc danh sách Hot Trend từ live_data_map đã được cập nhật bởi coin_filter.

        Tiêu chí:
          - change_24h >= HTB_MIN_CHANGE_24H (%)
          - quote_vol  >= HTB_MIN_VOL_USDT
          - daily_vola >= HTB_MIN_VOLA_24H (%)
          - Không nằm trong danh sách EXCLUDE

        Returns:
            list[str]: Danh sách symbol USDT, sắp xếp theo change_24h giảm dần.
        """
        candidates = []
        for symbol, info in live_data_map.items():
            if not symbol.endswith('USDT') or symbol in EXCLUDE:
                continue
            if info.get('change_24h', 0)  < HTB_MIN_CHANGE_24H:
                continue
            if info.get('quote_vol', 0)   < HTB_MIN_VOL_USDT:
                continue
            if info.get('daily_vola', 0)  < HTB_MIN_VOLA_24H:
                continue
            candidates.append((symbol, info['change_24h']))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in candidates[:top_n]]

    @staticmethod
    def analyze_symbol(symbol: str) -> dict:
        """
        Phân tích 1 mã qua 5 cửa Hot Trend Pullback.

        Returns:
            dict với đầy đủ scores, labels, trade_setup.
            None nếu thiếu dữ liệu hoặc không qua cửa lọc cứng.
        """
        try:
            time.sleep(0.03)  # Giãn nhẹ để tránh burst rate limit

            # ── 1. Lấy Dữ Liệu ────────────────────────────────────────────
            # 1H : limit=100 → EMA50 (cần 50 nến) + swing 48 nến + buffer
            # 15M: limit=10  → C5 Taker Buy (chỉ cần 2-3 nến đã đóng)
            # 1D : limit=180 → C0 Macro Cycle (180D High + MA99 + 60D box + 30D VSA)
            df_1h  = get_klines_live(symbol, '1h',  limit=100)
            df_15m = get_klines_live(symbol, '15m', limit=10)
            df_1d  = get_klines_live(symbol, '1d',  limit=180)

            if df_1h is None or len(df_1h) < 55:
                return None
            if df_15m is None or len(df_15m) < 3:
                return None
            if df_1d is None or len(df_1d) < 30:
                return None

            # ── 2. Tính Chỉ Báo 1H ────────────────────────────────────────
            close_live = float(df_1h['Close'].iloc[-1])
            ema20_1h   = float(df_1h['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
            ema50_1h   = float(df_1h['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
            ma25_1h    = float(df_1h['Close'].rolling(25).mean().iloc[-1])
            rsi_1h     = _calc_rsi(df_1h['Close'], period=14)

            # Swing High/Low từ 48 nến 1H đã đóng (tránh nhiễu từ nến đang mở)
            sh_48      = _swing_high(df_1h, lookback=48)
            sl_48      = _swing_low(df_1h,  lookback=48)
            swing_rng  = sh_48 - sl_48 if sh_48 > sl_48 else 0.0

            # ── C1: Uptrend Xác Nhận (25đ) ──────────────────────────────────────
            # SOFT GATE: EMA không đúng cấu trúc → score=0, không return None
            # Mã vẫn xuất hiện trong top 5 nhưng action=TỪ CHỐI
            ema_ok = (ema20_1h > ema50_1h) and (close_live > ema20_1h)
            score_c1_rsi, label_rsi = _score_rsi_c1(rsi_1h)

            if ema_ok:
                score_c1   = score_c1_rsi
                status_c1  = f"EMA20({ema20_1h:.4f}) > EMA50({ema50_1h:.4f}) ✅ | {label_rsi}"
            else:
                score_c1     = 0
                score_c1_rsi = 0   # Force → action sẽ ra TỪ CHỐI
                if ema20_1h <= ema50_1h:
                    status_c1 = f"⚠️ EMA Chưa Cross (EMA20={ema20_1h:.4f} ≤ EMA50={ema50_1h:.4f}) | {label_rsi}"
                else:
                    status_c1 = f"⚠️ Giá Dưới EMA20 ({close_live:.4f} < {ema20_1h:.4f}) | {label_rsi}"

            # ── C2: Pullback Chất Lượng (25đ) ──────────────────────────────────────
            # Tính pullback_pct trước, sau đó score theo ngưỡng
            # Nếu close < ema50 (gãy cấu trúc) → C2 = 0, skip toàn bộ pullback%
            if close_live < ema50_1h:
                pullback_pct = 0.0
                score_c2     = 0
                status_c2    = f"🔴 Phá EMA50 ({close_live:.4f} < {ema50_1h:.4f}) — Gãy Cấu Trúc"
            else:
                pullback_pct = (sh_48 - close_live) / sh_48 * 100 if (sh_48 > 0 and close_live < sh_48) else 0.0

                if 1.0 <= pullback_pct <= 4.0:
                    score_c2  = 25
                    status_c2 = f"Cờ Đuôi Nheo/Nền Khỏe (-{pullback_pct:.1f}%)"
                elif 4.0 < pullback_pct <= 8.0:
                    score_c2  = 20
                    status_c2 = f"Pullback Tốt (-{pullback_pct:.1f}% từ Swing)"
                elif 8.0 < pullback_pct <= 12.0:
                    score_c2  = 10
                    status_c2 = f"Khá Sâu (-{pullback_pct:.1f}% — Rủi Ro Gãy)"
                elif 12.0 < pullback_pct <= 15.0:
                    score_c2  = 5
                    status_c2 = f"Rất Sâu (-{pullback_pct:.1f}% — Cẩn Thận)"
                elif pullback_pct < 1.0:
                    score_c2  = 0
                    status_c2 = f"Sát Đỉnh (-{pullback_pct:.1f}%) — Chưa Có Pullback"
                else:
                    score_c2  = 0
                    status_c2 = f"Quá Sâu (-{pullback_pct:.1f}%) — Có Thể Gãy"

                # Lọc Bơm Xả (Wick Rejection): Kiểm tra 2 nến 1H gần nhất
                # Nếu râu trên > 3% và gấp 2 lần thân nến -> Từ chối Pullback
                for idx in range(-3, -1):
                    c_h = float(df_1h['High'].iloc[idx])
                    c_o = float(df_1h['Open'].iloc[idx])
                    c_c = float(df_1h['Close'].iloc[idx])
                    c_upper = c_h - max(c_o, c_c)
                    c_body = abs(c_o - c_c)
                    c_wick_pct = c_upper / max(c_o, c_c) * 100
                    if c_wick_pct > 3.0 and c_upper > c_body * 2:
                        score_c2 = 0
                        status_c2 = f"🔴 Bơm xả (Râu trên {c_wick_pct:.1f}%) — TỪ CHỐI"
                        break

            # ── C3: Volume Cạn Kiệt (20đ) ─────────────────────────────────
            # So sánh vol xả trung bình (chỉ lấy nến đỏ/doji) trong 3 nến 1H gần nhất vs vol nến xanh đỉnh
            recent_10   = df_1h.iloc[-11:-1]   # 10 nến 1H đã đóng, không tính nến đang mở
            green_c10   = recent_10[recent_10['Close'] > recent_10['Open']]
            peak_push   = float(green_c10['Quote_Volume'].max()) if len(green_c10) > 0 else 0.0
            
            # Lấy 3 nến đã đóng gần nhất
            recent_3 = df_1h.iloc[-4:-1]
            # Lọc ra các nến đỏ hoặc doji
            red_doji_3 = recent_3[recent_3['Close'] <= recent_3['Open']]
            
            if len(red_doji_3) > 0:
                avg_dump_vol = float(red_doji_3['Quote_Volume'].mean())
            else:
                # Nếu 3 nến gần nhất đều là xanh, coi như vol xả rất thấp (chưa có nhịp xả gần)
                avg_dump_vol = float(recent_3['Quote_Volume'].mean()) * 0.3

            if peak_push > 0:
                vol_ratio = avg_dump_vol / peak_push
            else:
                vol_ratio = 1.0

            if vol_ratio <= 0.40:
                score_c3  = 20
                status_c3 = f"Kiệt Cung ({vol_ratio*100:.0f}% đỉnh push) ✅"
            elif vol_ratio <= 0.60:
                score_c3  = 12
                status_c3 = f"Vol Giảm ({vol_ratio*100:.0f}% đỉnh push)"
            elif vol_ratio <= 0.80:
                score_c3  = 5
                status_c3 = f"Vol Còn Cao ({vol_ratio*100:.0f}% đỉnh push)"
            else:
                score_c3  = 0
                status_c3 = f"Xả Mạnh ({vol_ratio*100:.0f}% đỉnh push) ⚠️"

            # ── C4: Bệ Đỡ Kỹ Thuật (20đ) ─────────────────────────────────
            # Kiểm tra giá gần bất kỳ bệ đỡ nào trong danh sách
            supports = [
                ('EMA20',    ema20_1h),
                ('MA25',     ma25_1h),
            ]
            if swing_rng > 0:
                supports.append(('Fib0.382', sh_48 - swing_rng * 0.382))
                supports.append(('Fib0.500', sh_48 - swing_rng * 0.500))

            nearest_name = '—'
            min_dist_pct = 999.0
            for name, lvl in supports:
                if lvl > 0:
                    d = abs(close_live - lvl) / close_live * 100
                    if d < min_dist_pct:
                        min_dist_pct, nearest_name = d, name

            if min_dist_pct <= 1.5:
                score_c4  = 20
                status_c4 = f"Chạm {nearest_name} (±{min_dist_pct:.1f}%) ✅"
            elif min_dist_pct <= 3.0:
                score_c4  = 10
                status_c4 = f"Gần {nearest_name} (±{min_dist_pct:.1f}%)"
            else:
                score_c4  = 0
                status_c4 = f"Xa Bệ Đỡ ({nearest_name} ±{min_dist_pct:.1f}%)"

            # ── C5: Taker Buy Hồi Phục (10đ) ──────────────────────────────
            # Tính tỷ lệ USDT mua chủ động từ 2 nến 15M đã đóng gần nhất
            # Dùng Quote Asset (USDT): taker_buy_quote / quote_volume
            # → Phản ánh đúng lượng USDT bơm vào chặn giá giảm
            two_15m       = df_15m.iloc[-3:-1]   # 2 nến đã đóng (bỏ nến đang mở)
            taker_sum     = float(two_15m['Taker_Buy_Quote'].sum()) \
                            if 'Taker_Buy_Quote' in two_15m.columns else 0.0
            total_vol_sum = float(two_15m['Quote_Volume'].sum()) \
                            if 'Quote_Volume' in two_15m.columns else 0.0
            taker_pct     = (taker_sum / total_vol_sum * 100) if total_vol_sum > 0 else 50.0

            if taker_pct >= 60.0:
                score_c5  = 10
                status_c5 = f"Smart Money Vào ({taker_pct:.1f}% Taker) ✅"
            elif taker_pct >= 55.0:
                score_c5  = 5
                status_c5 = f"Taker Hồi Phục ({taker_pct:.1f}%)"
            else:
                score_c5  = 0
                status_c5 = f"Taker Còn Yếu ({taker_pct:.1f}%)"

            # ── C0: Macro Cycle Detector (Wyckoff + VSA) ──────────────────
            # Gọi sau khi đã qua các hard-gate 1H để giảm số lần gọi df_1d
            c0 = _check_c0_cycle(df_1d)

            # ── Tổng Điểm & Tính Setup ────────────────────────────────────
            # C0 cộng/trừ điều chỉnh vào tổng (có thể âm nếu rủi ro phân phối)
            score_c1c5  = score_c1 + score_c2 + score_c3 + score_c4 + score_c5
            total_score = score_c1c5 + c0['score']

            # SL: ngay dưới EMA50 với cushion 0.5% (bảo vệ khỏi râu sweep)
            sl_price   = ema50_1h * 0.995
            # TP: dùng Swing High 24H gần nhất làm mục tiêu thực tế
            sh_24       = _swing_high(df_1h, lookback=24)
            atr_14      = float((df_1h['High'] - df_1h['Low']).tail(14).mean())
            tp1_price   = sh_24 if sh_24 > close_live * 1.01 else close_live + atr_14 * 2.5
            sl_dist     = max(close_live - sl_price, 1e-9)
            tp_dist     = max(tp1_price - close_live, 0.0)
            rr_ratio    = round(tp_dist / sl_dist, 2)
            sl_pct      = round(sl_dist / close_live * 100, 2)
            tp1_pct     = round(tp_dist / close_live * 100, 2)

            # ── Phán Quyết Hành Động ──────────────────────────────────────
            # Ngưỡng giữ nguyên: C0 là bộ khuếch đại/triệt tiêu, không thay đổi ngưỡng
            # Mã Chân Sóng (+35 C0) + C1-C5 tốt = tổng cao → dễ đạt ngưỡng vào lệnh
            # Mã Đu Đỉnh (-20 C0) + C1-C5 cao   = tổng bị kéo xuống → từ chối tự nhiên
            if score_c1_rsi == 0:
                action = "🔴 TỪ CHỐI (RSI Climax/Yếu)"
            elif c0['score'] <= -15:
                # Tín hiệu phân phối vĩ mô quá rõ → từ chối dù vi mô tốt
                action = "🔴 MACRO GATE: Rủi Ro Phân Phối Đỉnh"
            elif total_score >= 75 and rr_ratio >= 2.0 and score_c2 >= 15:
                # score_c2 >= 15 bắt buộc: pullback thực sự ≥ 3% (C2 Lý Tưởng hoặc Khá Sâu)
                # Loại bỏ hoàn toàn trường hợp mã ở đỉnh điểm cao nhờ các cửa khác
                action = "🚀 VÀO LỆNH PULLBACK"
            elif total_score >= 60:
                if total_score >= 75:
                    missing = []
                    if rr_ratio < 2.0:
                        missing.append(f"R/R={rr_ratio}<2.0")
                    if score_c2 < 15:
                        missing.append("Pullback nông")
                    action = f"⏳ CHỜ XÁC NHẬN ({' & '.join(missing)})"
                else:
                    action = f"⏳ CHỜ XÁC NHẬN (Điểm {total_score}/75)"
            else:
                action = "🔴 BỎ QUA"

            # sort_score: ưu tiên điểm cao + R/R tốt + RSI lý tưởng + chu kỳ vĩ mô
            sort_score = total_score + (rr_ratio * 2) + (score_c1_rsi * 0.1)

            return {
                'symbol':      symbol.replace('USDT', ''),
                '_raw_symbol': symbol,
                'Giá Live':    close_live,
                'RSI 1H':      round(rsi_1h, 1),
                'Pullback%':   round(pullback_pct, 1),
                'Điểm':        total_score,
                'Điểm C1-C5':  score_c1c5,
                'C0 Score':    c0['score'],
                'sort_score':  sort_score,
                'C0 Chu Kỳ':   c0['label'],
                'C0 Detail':   c0['detail'],
                'C1 Trend':    status_c1,
                'C2 Pullback': status_c2,
                'C3 Volume':   status_c3,
                'C4 Bệ Đỡ':   status_c4,
                'C5 Taker':    status_c5,
                'Hành Động':   action,
                'trade_setup': {
                    'entry':       close_live,
                    'stop_loss':   round(sl_price, 8),
                    'take_profit': round(tp1_price, 8),
                    'sl_pct':      sl_pct,
                    'tp1_pct':     tp1_pct,
                    'rr_ratio':    rr_ratio,
                    'ema20':       round(ema20_1h, 8),
                    'ema50':       round(ema50_1h, 8),
                }
            }

        except Exception:
            return None

    @classmethod
    def run_scan(cls, live_data_map: dict,
                 top_n: int = HTB_TOP_N,
                 result_top: int = HTB_RESULT_TOP) -> list:
        """
        Entry point chính cho main.py.

        Quy trình:
          1. Lọc Hot Trend Universe từ live_data_map
          2. Quét song song qua ThreadPoolExecutor
          3. Sắp xếp và trả về Top result_top kết quả

        Args:
            live_data_map: dict từ core.coin_filter (đã cập nhật sau get_filtered_symbols)
            top_n:         Số mã tối đa đưa vào quét
            result_top:    Số kết quả trả về

        Returns:
            list[dict]: Danh sách kết quả đã sắp xếp
        """
        symbols = cls.get_hot_trend_symbols(live_data_map, top_n=top_n)
        if not symbols:
            return []

        results = []
        with ThreadPoolExecutor(max_workers=HTB_WORKERS) as pool:
            for res in pool.map(cls.analyze_symbol, symbols):
                if res is not None:
                    results.append(res)

        # Ưu tiên sắp xếp: Hành Động trước (VÀO LỆNH lên đầu), sau đó mới theo điểm
        _action_priority = {
            '🚀 VÀO LỆNH PULLBACK':              0,  # Tín hiệu mạnh nhất — lên đầu
            '⏳ CHỜ XÁC NHẬN':                    1,  # Tiềm năng, chưa đủ
            '🔴 TỪ CHỐI (RSI Climax/Yếu)':        2,  # Đang bay quá nóng, chờ xả (Cần hiển thị để theo dõi)
            '🔴 BỎ QUA':                          3,  # Điểm thấp, không đạt
            '🔴 MACRO GATE: Rủi Ro Phân Phối Đỉnh': 4,  # Bị lọc vĩ mô
        }
        results.sort(
            key=lambda x: (
                # Lấy phần đầu của Hành Động để match nếu có thêm chi tiết phía sau (ví dụ: ⏳ CHỜ XÁC NHẬN (R/R...))
                next((v for k, v in _action_priority.items() if x.get('Hành Động', '').startswith(k)), 9),
                -x.get('sort_score', 0)                             # Ưu tiên 2: điểm cao hơn lên trước
            )
        )
        return results[:result_top]

    # ──────────────────────────────────────────────────────────────────────────
    # Backward compatibility: giữ __init__ và evaluate_pullback
    # để không break code cũ nếu có nơi nào khác còn gọi
    # ──────────────────────────────────────────────────────────────────────────

    def __init__(self, exchange=None):
        """
        Giữ lại __init__ để backward compatible.
        exchange không còn được sử dụng (DC4 dùng Binance REST trực tiếp).
        """
        self.exchange = exchange  # Không dùng nhưng giữ để không lỗi import

    def evaluate_pullback(self, symbol: str, timeframe: str = '4h') -> dict:
        """
        [DEPRECATED] Wrapper backward-compatible cho code cũ.
        Gọi analyze_symbol nội bộ với symbol format chuẩn.
        """
        sym_usdt = symbol if symbol.endswith('USDT') else symbol.replace('/', '') + 'USDT' \
                   if '/' in symbol else symbol + 'USDT'
        result = self.analyze_symbol(sym_usdt)
        if result is None:
            return {
                'symbol':      symbol,
                'total_score': 0,
                'sort_score':  0,
                'action':      '🔴 Không đủ dữ liệu / Không qua cửa lọc',
                'details':     {},
                'trade_setup': {}
            }
        # Map sang format cũ để không break print block cũ
        return {
            'symbol':      result['symbol'],
            'price':       result['Giá Live'],
            'total_score': result['Điểm'],
            'sort_score':  result['sort_score'],
            'action':      result['Hành Động'],
            'details': {
                'Gate_1_Trend':   result['C1 Trend'],
                'Gate_2_Volume':  result['C2 Pullback'],
                'Gate_3_Taker':   result['C3 Volume'],
                'Gate_4_Entry':   result['C4 Bệ Đỡ'],
                'Gate_5_Risk':    result['C5 Taker'],
            },
            'trade_setup': result['trade_setup'],
            'rr_ratio':    result['trade_setup']['rr_ratio'],
        }
