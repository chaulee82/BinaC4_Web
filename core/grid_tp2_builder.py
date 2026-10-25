"""
Module: core/grid_tp2_builder.py
Dự án: BinaC4
Mục đích: Dựng dòng thông số cài đặt nhanh Bot Spot Grid (Binance) mang nhãn
         `🎯 [GRID TP2 - {symbol}]` cho Động cơ 2, 3, 4.

Thứ tự tham số khớp 100% thứ tự nhập liệu trên giao diện Binance Spot Grid:
    1. Lower Price - Upper Price
    2. Number of Grids (Arithmetic, ký hiệu L)
    3. Trigger Price
    4. Stop Loss
    5. Take Profit

Thiết kế: Pure Function — không gọi API, dễ Unit Test.
"""

from decimal import Decimal
from typing import Optional

from core.macro_levels import _round_tick

# Biên độ mục tiêu mỗi nấc lưới (~0.9% → lãi ròng ~0.7-0.8% sau phí)
GRID_STEP_PCT = 0.009
MIN_GRIDS = 10
MAX_GRIDS = 24
# macro_sl phải thấp hơn sl_short ít nhất 1% để đáy lưới có độ giãn bọc râu
MACRO_SL_MIN_GAP_RATIO = 0.99


def _clean_symbol(symbol: str) -> str:
    """'DOT/USDT' | 'DOTUSDT' | 'DOT' → 'DOT'"""
    s = (symbol or "").upper().replace("/", "").strip()
    if s.endswith("USDT") and len(s) > 4:
        s = s[:-4]
    return s


def _tick_decimals(tick_size: float) -> int:
    """Số chữ số thập phân của tick_size (VD: 0.0005 → 4, 0.01 → 2, 1 → 0)."""
    exp = Decimal(str(tick_size)).normalize().as_tuple().exponent
    return max(0, -int(exp))


def _make_formatter(tick_size: Optional[float]):
    def fmt(val: float) -> str:
        if tick_size and tick_size > 0:
            return f"{val:.{_tick_decimals(tick_size)}f}"
        if val >= 100:
            return f"{val:.2f}"
        if val >= 1:
            return f"{val:.4f}"
        if val < 0.001 and val > 0:
            import math
            first_sig = -math.floor(math.log10(abs(val)))
            dec = min(max(first_sig + 3, 6), 10)
            return f"{val:.{dec}f}"
        return f"{val:.6f}"
    return fmt


def build_macro_grid_payload(symbol: str, entry: float, sl_short: float, tp_target: float,
                             macro_sl: Optional[float] = None, tick_size: Optional[float] = None,
                             fallback_ratio: float = 0.96) -> Optional[dict]:
    """
    Tính bộ thông số Spot Grid TP2.

    Args:
        symbol        : Mã coin (VD: 'DOT/USDT', 'DOTUSDT', 'DOT')
        entry         : Giá kích hoạt lưới (Trigger)
        sl_short      : SL ngắn hạn của setup
        tp_target     : Trần lưới = Take Profit (TP2)
        macro_sl      : SL Cứng 4H (tùy chọn)
        tick_size     : Bước giá của cặp (tùy chọn)
        fallback_ratio: Hệ số lùi khi khuyết / không hợp lệ macro_sl (DC2=0.96, DC3/DC4=0.95)

    Returns:
        dict payload hoặc None nếu dữ liệu không hợp lệ.
    """
    try:
        entry = float(entry or 0)
        sl_short = float(sl_short or 0)
        tp_target = float(tp_target or 0)
    except (TypeError, ValueError):
        return None

    if entry <= 0 or sl_short <= 0 or tp_target <= 0:
        return None

    clean_sym = _clean_symbol(symbol)

    # 1. Chốt chặn Cắt Lỗ Cứng (Stop-Loss): SL = P_trig * 0.88 (-12% từ Trigger)
    grid_sl = entry * 0.88

    # 2. Cận Trên Tối Thiểu (Upper Bound): max(tp_target, entry * 1.20) (tối thiểu +20%)
    grid_up = max(tp_target, entry * 1.20)

    # 3. Cận Dưới (Lower Bound): max(P_raw_lower, SL * 1.015)
    # Đảm bảo cách SL >= 1.5% và hướng về vùng gom -10%
    raw_lower = (sl_short + grid_sl) / 2.0
    min_safe_lower = grid_sl * 1.015
    grid_low = max(raw_lower, min_safe_lower)
    if grid_low >= entry:
        grid_low = entry * 0.90
    grid_low = max(grid_low, min_safe_lower)

    # Làm tròn giá trị về bội số tick_size (chặn lỗi PRICE_FILTER khi nhập lên Binance)
    if tick_size and tick_size > 0:
        grid_low = _round_tick(grid_low, tick_size)
        grid_up = _round_tick(grid_up, tick_size)
        grid_sl = _round_tick(grid_sl, tick_size)
        entry = _round_tick(entry, tick_size)

    if grid_low <= 0 or grid_low >= entry or grid_up <= entry or grid_low >= grid_up:
        return None

    range_pct = (grid_up - grid_low) / grid_low
    opt_grids = int(range_pct / GRID_STEP_PCT)
    grids = max(MIN_GRIDS, min(opt_grids, MAX_GRIDS))

    fmt = _make_formatter(tick_size)
    f_low = fmt(grid_low)
    f_up = fmt(grid_up)
    f_trig = fmt(entry)
    f_sl = fmt(grid_sl)
    f_tp = fmt(grid_up)

    display_str = (
        f"   ↳ 🎯 [GRID TP2 - {clean_sym}] {f_low} - {f_up} | {grids}L | "
        f"Trig: {f_trig} | SL: {f_sl} | TP: {f_tp}"
    )
    copy_str = f"[{clean_sym}] Low {f_low} | Up {f_up} | Grids {grids} | Trig {f_trig} | SL {f_sl} | TP {f_tp}"

    return {
        "symbol": clean_sym,
        "lower": f_low,
        "upper": f_up,
        "grids": grids,
        "trigger": f_trig,
        "stop_loss": f_sl,
        "take_profit": f_tp,
        "display_line": display_str,
        "copy_line": copy_str,
    }
