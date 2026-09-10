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
    # → {"status": "success", "entry_4h": ..., "tp_1h": ..., "tp_4h": ..., "sl_4h": ...}
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
    sl_atr_multiplier: float = 1.5,
    sl_margin_pct: float = 0.03,
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
        raw_entry_4h = macro_low_4h + (box_height * 0.20)  # Vùng chiết khấu 20% từ đáy
        raw_tp_1h    = peak_1h                              # Cản ngắn hạn 24H
        raw_tp_4h    = macro_high_4h                        # Đỉnh hộp vĩ mô 5 ngày

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
            "status":   "success",
            "entry_4h": entry_4h,
            "tp_1h":    tp_1h,
            "tp_4h":    tp_4h,
            "sl_4h":    sl_4h,
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
