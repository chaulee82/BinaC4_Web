"""
Module: money_flow.py
Dự án: BinaC4
Mục đích: Phát hiện DÒNG TIỀN ĐỘT BIẾN (Quote Volume spike) trên khung 15M
          để gắn biểu tượng cảnh báo nhanh cạnh mã coin (DC3, DC4).

Thuật toán:
  - Nền (baseline) = trung bình Quote Volume của 20 nến 15M ĐÃ ĐÓNG trước cửa sổ gần nhất.
  - Cửa sổ gần nhất = 3 nến cuối (2 nến đã đóng + 1 nến đang chạy) ~ 45 phút.
  - Ratio = max(QuoteVol trong cửa sổ) / baseline.
  - Hướng dòng tiền = Taker Buy Quote / Quote Volume của nến đột biến.

Biểu tượng:
  💥💰 ≥ 5x | 💰💰 ≥ 3x | 💰 ≥ 2x   (+ 🟢 Mua chủ động / 🔴 Bán chủ động)
"""

import logging
from typing import Optional

logger = logging.getLogger("MoneyFlow")

SPIKE_MIN_X = 2.0      # Ngưỡng tối thiểu để coi là đột biến
SPIKE_STRONG_X = 3.0
SPIKE_EXTREME_X = 5.0
_BASELINE_N = 20
_WINDOW_N = 3


def _to_api_symbol(symbol: str) -> str:
    s = symbol.replace('/', '').upper()
    return s if s.endswith('USDT') else f"{s}USDT"


def detect_money_flow_spike(symbol: str) -> Optional[dict]:
    """Trả về dict {ratio, buy_pct, tag} nếu có dòng tiền đột biến, ngược lại None."""
    try:
        from core.klines_cache import get_klines_cached

        df = get_klines_cached(_to_api_symbol(symbol), '15m', limit=_BASELINE_N + _WINDOW_N + 5)
        if df is None or len(df) < _BASELINE_N + _WINDOW_N:
            return None

        qv = df['Quote_Volume'].astype(float).values
        tbq = df['Taker_Buy_Quote'].astype(float).values

        base = qv[-(_BASELINE_N + _WINDOW_N):-_WINDOW_N].mean()
        if base <= 0:
            return None

        window = qv[-_WINDOW_N:]
        idx_rel = int(window.argmax())
        peak_idx = len(qv) - _WINDOW_N + idx_rel
        ratio = float(window[idx_rel] / base)
        if ratio < SPIKE_MIN_X:
            return None

        peak_qv = qv[peak_idx]
        buy_pct = float(tbq[peak_idx] / peak_qv * 100) if peak_qv > 0 else 50.0

        if ratio >= SPIKE_EXTREME_X:
            icon = "💥💰"
        elif ratio >= SPIKE_STRONG_X:
            icon = "💰💰"
        else:
            icon = "💰"

        if buy_pct >= 55:
            direction = "🟢"
        elif buy_pct <= 45:
            direction = "🔴"
        else:
            direction = ""

        return {
            "ratio": ratio,
            "buy_pct": buy_pct,
            "tag": f"{icon}x{ratio:.1f}{direction}",
        }
    except Exception as e:
        logger.debug(f"[MoneyFlow] {symbol}: {e}")
        return None


def money_flow_tag(symbol: str) -> str:
    """Chuỗi biểu tượng ngắn gắn cạnh mã coin, rỗng nếu không đột biến."""
    res = detect_money_flow_spike(symbol)
    return res["tag"] if res else ""
