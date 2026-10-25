"""
Module: execution/order_gatekeeper.py
Dự án: BinaC4 Trading Bot
Phân hệ: Final Execution & Order Geometry (Giai đoạn 1)

Mục đích:
Module Bảng Duyệt Lệnh Tối Ưu (Optimized Gatekeeper).
- Tiếp nhận danh sách lệnh ĐÃ ĐƯỢC LỌC SẠCH 100% RỦI RO VĨ MÔ từ Tầng 2 (Summary Board).
- Tiến hành thực tế hóa hình học lệnh (Order Geometry: Anti-wick buffer, Realistic TP, Auto-Breakeven, Grid Compression).
- Phân luồng kích hoạt: AUTO_EXECUTE (Uy tín >= 60đ) hoặc MANUAL_APPROVAL (Tier B: 40-59đ).
- Render giao diện bảng Terminal UI chuẩn đặc tả.
"""

import re
import sys
import math
import logging
from typing import List, Dict, Any, Optional, Tuple

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

logger = logging.getLogger("OrderGatekeeper")


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Chuyển đổi an toàn sang float, bóc tách ký tự lạ (đ, x, %, $), không bao giờ văng lỗi."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = re.sub(r"[^\d.-]", "", str(val).strip())
        return float(cleaned) if cleaned else default
    except Exception:
        return default


def _count_decimals(val: float, max_dec: Optional[int] = None) -> int:
    """Đếm số chữ số thập phân thực tế và cần thiết của một giá trị float."""
    if val is None or val == 0:
        return 2
    try:
        val = abs(float(val))
    except Exception:
        return 2
    if val >= 1000:
        return 2
    if val >= 1:
        s = f"{val:.6f}".rstrip('0')
        d = len(s.split('.')[1]) if '.' in s else 2
        return min(max(d, 2), 4)
    # Coin nhỏ (< 1 USDT): cần đủ chữ số thập phân để thể hiện % biến động
    import math
    first_sig = -math.floor(math.log10(val))
    needed_dec = min(max(first_sig + 3, 6), 10 if max_dec is None else max_dec)
    s = f"{val:.10f}".rstrip('0')
    actual_d = len(s.split('.')[1]) if '.' in s else 2
    return max(actual_d, needed_dec)


def _fmt_price_val(val: float, decimals: Optional[int] = None) -> str:
    """Format giá gọn đẹp, khớp số chữ số thập phân chuẩn, bỏ số 0 dư thừa nhưng giữ tối thiểu 2 số thập phân."""
    if val is None:
        return "0.00"
    try:
        val = float(val)
    except Exception:
        return str(val)
    if val == 0:
        return "0.00"
    if decimals is not None and decimals > 0:
        s = f"{val:.{decimals}f}".rstrip('0')
        if '.' in s:
            parts = s.split('.')
            if len(parts[1]) < 2:
                s = parts[0] + '.' + parts[1].ljust(2, '0')
        else:
            s += ".00"
        return s
    if abs(val) < 0.001:
        s = f"{val:.10f}".rstrip('0')
    else:
        s = f"{val:.4f}".rstrip('0')
    if '.' in s:
        parts = s.split('.')
        if len(parts[1]) < 2:
            s = parts[0] + '.' + parts[1].ljust(2, '0')
    else:
        s += ".00"
    return s


ENGINE_LABELS = {
    "DC1": "DC1 (Chân Sóng Vĩ Mô)",
    "DC2": "DC2 (Bắt Đáy)",
    "DC3": "DC3 (Bứt Phá - Breakout)",
    "DC4": "DC4 (Đu Trend - Hot Trend)",
    "DC5": "DC5 (Nén Vol Chờ Nổ)",
    "GRID_GOM_HANG": "Grid Gom Hàng",
}


# ── THUẬT TOÁN THÁC NƯỚC KHÁNG CỰ & MỐC TÂM LÝ ───────────────────────────────

def get_psychological_levels(entry: float, max_ratio: float = 2.5) -> List[float]:
    """
    Sinh các mốc tâm lý số tròn (Psychological Levels) cao hơn entry dựa trên thị giá.
    Ví dụ:
    - entry = 0.0088 -> [0.0090, 0.0095, 0.0100, 0.0105, 0.0110, 0.0120, 0.0150, 0.0200]
    - entry = 0.36   -> [0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.75, 0.80, 0.90, 1.00]
    - entry = 8.53   -> [9.0, 9.5, 10.0, 10.5, 11.0, 12.0, 15.0]
    """
    if entry <= 0:
        return []
    levels = set()
    upper_bound = entry * max_ratio
    mag_order = math.floor(math.log10(entry))
    base_mag = 10 ** mag_order

    steps = [base_mag * 0.5, base_mag * 1.0, base_mag * 2.0, base_mag * 5.0, base_mag * 10.0]
    for step in steps:
        if step <= 0:
            continue
        start_k = math.ceil(entry / step)
        for k in range(start_k, start_k + 20):
            val = round(k * step, 8)
            if entry < val <= upper_bound:
                levels.add(val)
            elif val > upper_bound:
                break
    return sorted(levels)


def get_technical_resistances(symbol: str) -> Dict[str, float]:
    """
    Thu thập các mức kháng cự kỹ thuật: tp_1h, tp_4h, boll_up_3d
    từ cache klines và macro 3D profile.
    """
    res = {}
    sym_clean = (symbol or "").upper().replace("/USDT", "").replace("USDT", "").strip()
    if not sym_clean:
        return res
    sym_api = f"{sym_clean}USDT"

    # 1. boll_up_3d
    try:
        from core.macro_levels import get_3d_profile
        d3 = get_3d_profile(sym_clean)
        if d3:
            raw = d3.get('_raw') or {}
            boll_up_3d = float(raw.get('boll_up_3d', 0.0) or d3.get('boll_up_3d', 0.0) or 0.0)
            if boll_up_3d > 0:
                res['boll_up_3d'] = boll_up_3d
    except Exception:
        pass

    # 2. tp_1h & tp_4h
    try:
        from core.klines_cache import get_klines_cached
        # Kháng cự 1H: Đỉnh 24 nến 1H gần nhất
        df_1h = get_klines_cached(sym_api, "1h", limit=48)
        if df_1h is not None and not df_1h.empty:
            high_col = 'High' if 'High' in df_1h.columns else 'high'
            tp_1h = float(df_1h[high_col].iloc[-24:].max())
            if tp_1h > 0:
                res['tp_1h'] = tp_1h

        # Kháng cự 4H: Đỉnh 18 nến 4H gần nhất (3 ngày)
        df_4h = get_klines_cached(sym_api, "4h", limit=30)
        if df_4h is not None and not df_4h.empty:
            high_col = 'High' if 'High' in df_4h.columns else 'high'
            tp_4h = float(df_4h[high_col].iloc[-18:].max())
            if tp_4h > 0:
                res['tp_4h'] = tp_4h
    except Exception:
        pass

    return res


def calculate_waterfall_tp(
    symbol: str,
    entry: float,
    safe_sl: float,
    raw_tp: float,
    min_rr: float = 2.0
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Thuật Toán Mới: "Thác Nước Kháng Cự & Mốc Tâm Lý" (Waterfall Resistance & Psychological Levels)

    1. Bước 1: Xác định mức R:R tối thiểu (Min Target):
       min_tp = entry + (entry - safe_sl) * min_rr
       (Ranh giới 'không ăn non').

    2. Bước 2: Tôn trọng tuyệt đối Động cơ gốc (Macro TP / Đỉnh hộp Darvas):
       NẾU raw_tp >= min_tp:
       -> GIỮ NGUYÊN 100% target tự nhiên của Động cơ (như mã S, ALT, PHA ăn siêu sóng),
          TUYỆT ĐỐI KHÔNG GỌT TRẦN!

    3. Bước 3: Bộ Lọc Thác Nước (khi raw_tp bị ăn non hoặc khuyết thiếu):
       NẾU raw_tp < min_tp:
       - Thu thập ứng viên: Kháng cự kỹ thuật (1H, 4H, Boll Up 3D) và Mốc tâm lý tròn số >= min_tp.
       - CHỌN MỐC THẤP NHẤT / GẦN NHẤT để nâng vị thế lên tỷ lệ R:R an toàn khả thi nhất.
       - Fallback: min_tp nếu không có ứng viên nào.
    """
    if entry <= 0 or safe_sl <= 0 or safe_sl >= entry:
        return raw_tp, "Gốc", {}

    risk = entry - safe_sl
    min_tp = entry + (risk * min_rr)

    # 1. TÔN TRỌNG TUYỆT ĐỐI ĐỘNG CƠ GỐC NẾU ĐÃ ĐẠT CHUẨN R:R >= MIN_RR
    # Tránh làm đổ sông đổ bể nỗ lực đo lường sóng Darvas / Bollinger Bands vĩ mô của Động cơ
    if raw_tp and raw_tp >= min_tp - 1e-8:
        return raw_tp, "Động cơ gốc (Macro TP)", {
            "min_tp": min_tp,
            "source": "Động cơ gốc (Macro TP)",
            "raw_tp": raw_tp
        }

    # 2. Thu thập ứng viên khi raw_tp bị ăn non (< min_tp) hoặc khuyết thiếu
    candidates: Dict[str, float] = {}

    # Kháng cự kỹ thuật (1H, 4H, Boll Up 3D)
    tech_res = get_technical_resistances(symbol)
    for k, v in tech_res.items():
        if v > entry:
            candidates[k] = v

    # Mốc tâm lý tròn số
    psy_levels = get_psychological_levels(entry, max_ratio=2.5)
    for p in psy_levels:
        candidates[f"psy_{p}"] = p

    # Lọc bỏ ứng viên < min_tp (trừ epsilon 1e-8 né sai số float)
    valid_candidates = {k: v for k, v in candidates.items() if v >= min_tp - 1e-8}

    if valid_candidates:
        best_k = min(valid_candidates, key=lambda k: valid_candidates[k])
        chosen_tp = valid_candidates[best_k]
        if best_k == "boll_up_3d":
            source_desc = "Cản Boll Up 3D"
        elif best_k == "tp_4h":
            source_desc = "Đỉnh 4H"
        elif best_k == "tp_1h":
            source_desc = "Đỉnh 1H"
        elif best_k.startswith("psy_"):
            source_desc = f"Mốc tâm lý {chosen_tp}"
        else:
            source_desc = best_k
    else:
        chosen_tp = min_tp
        source_desc = f"Sàn Min R:R 1:{min_rr:.1f}"

    return chosen_tp, source_desc, {
        "min_tp": min_tp,
        "valid_count": len(valid_candidates),
        "source": source_desc,
        "best_key": best_k if valid_candidates else "fallback"
    }


def optimize_grid_tp2_geometry(
    order: Dict[str, Any],
    entry: float,
    safe_sl: float,
    realistic_tp: float
) -> Dict[str, Any]:
    """
    Quy tắc nắn hình học chuẩn PRD: +8% Tổng Vốn / -12% Cắt Lỗ cho Spot Grid.
    1. Chốt chặn Cắt Lỗ Cứng (Stop-Loss): Ghim chặt ở mốc -12% từ Trigger (SL = P_trig * 0.88).
    2. Biên độ Tăng Trưởng (Upper Bound): Đảm bảo trần tối thiểu +20% (Upper = max(P_raw_upper, P_trig * 1.20)),
       hoàn toàn độc lập với OCO TP ngắn hạn để đủ biên sinh lời +8% tổng vốn.
    3. Vùng Gom Đáy (Lower Bound): Tự động nắn chỉnh Lower = max(P_raw_lower, SL * 1.015)
       (cách SL >= 1.5% chống quét râu nến khi bot khớp mua ở đáy, hướng về vùng nhúng -10%).
    """
    dec = _count_decimals(entry)
    clean_sym = order.get('symbol', '').upper().replace("/USDT", "").replace("USDT", "").strip()

    # 1. Chốt chặn Cắt Lỗ Cứng: SL = P_trig * 0.88 (-12%)
    grid_sl = round(entry * 0.88, dec)

    # 2. Lấy thông số macro thô ban đầu
    raw_lower = None
    raw_upper = None

    line = str(order.get("grid_tp2_line") or "").strip()
    if line:
        m = re.search(r"([\d.]+)\s*-\s*([\d.]+)", line)
        if m:
            raw_lower = float(m.group(1))
            raw_upper = float(m.group(2))

    if raw_lower is None or raw_upper is None or raw_lower <= 0 or raw_upper <= raw_lower:
        payload = order.get("grid_tp2_payload") or order.get("grid_tp2")
        if isinstance(payload, dict):
            try:
                raw_lower = float(payload.get("lower", payload.get("grid_low", 0.0)) or 0.0)
                raw_upper = float(payload.get("upper", payload.get("grid_up", 0.0)) or 0.0)
            except (ValueError, TypeError):
                raw_lower = None
                raw_upper = None

    if raw_lower is None or raw_upper is None or raw_lower <= 0 or raw_upper <= raw_lower:
        try:
            from core.grid_tp2_builder import build_macro_grid_payload
            calc_tp = float(order.get("raw_tp") or (entry * 1.20))
            p = build_macro_grid_payload(symbol=clean_sym, entry=entry, sl_short=safe_sl, tp_target=calc_tp)
            if p and p.get("lower") and p.get("upper"):
                raw_lower = float(p['lower'])
                raw_upper = float(p['upper'])
        except Exception:
            pass

    if raw_lower is None or raw_lower <= 0 or raw_lower >= entry:
        raw_lower = round(entry * 0.90, dec)
    if raw_upper is None or raw_upper <= entry:
        raw_upper = round(entry * 1.20, dec)

    # 3. Cận Trên Tối Thiểu (Upper Bound): max(P_raw_upper, P_trig * 1.20)
    # Tuyệt đối KHÔNG gọt trần theo realistic_tp của OCO
    opt_upper = max(raw_upper, round(entry * 1.20, dec))

    # 4. Cận Dưới (Lower Bound): max(P_raw_lower, SL * 1.015)
    min_safe_lower = round(grid_sl * 1.015, dec)
    opt_lower = max(raw_lower, min_safe_lower)
    if opt_lower >= entry:
        opt_lower = round(entry * 0.90, dec)
    opt_lower = max(opt_lower, min_safe_lower)

    return {
        "lower": round(opt_lower, dec),
        "upper": round(opt_upper, dec),
        "grids": 24,
        "trigger": round(entry, dec),
        "sl": round(grid_sl, dec),
        "tp": round(opt_upper, dec),
        "raw_lower": round(raw_lower, dec),
        "raw_upper": round(raw_upper, dec),
        "geometry_note": "Chuẩn hóa +8% Vốn / -12% SL, Đệm SL +1.5%, 24 Lưới"
    }


def _resolve_grid_tp2_fallback(order: Dict[str, Any], symbol: str, entry: float, sl: float, tp: float) -> str:
    """Lấy hoặc định dạng setup Grid TP2 đã được nắn bóp (Fallback Strategy)."""
    opt = order.get("grid_tp2_optimized")
    if not opt:
        opt = optimize_grid_tp2_geometry(order, entry, sl, tp)
        order["grid_tp2_optimized"] = opt

    dec = _count_decimals(entry)
    f_low = _fmt_price_val(opt["lower"], dec)
    f_up = _fmt_price_val(opt["upper"], dec)
    f_trig = _fmt_price_val(opt["trigger"], dec)
    f_sl = _fmt_price_val(opt["sl"], dec)
    f_tp = _fmt_price_val(opt["tp"], dec)
    return f"L={f_low} - U={f_up} | {opt.get('grids', 24)} Lưới | Trig: {f_trig} | SL: {f_sl} | TP: {f_tp} [Khóa đỉnh TP 1:2, Bóp đáy 15%]"



def check_pump_dump_trap(
    symbol: str,
    klines_15m: Optional[Any] = None
) -> Tuple[bool, str]:
    """
    [PHẦN 2.1: MODULE CHỐNG BẪY ĐỘC LẬP (check_pump_dump_trap)]
    Quét 4 nến 15M gần nhất để bắt quả tang hành vi thao túng trước khi bảng Tín nhiệm kịp update:
    - Rule 2.1 (Râu xả đỉnh 15M): Nến có râu trên chiếm >= 60% biên độ (high - low) và biên độ dao động > 1% -> VETO.
    - Rule 2.2 (Cây thông xả đỉnh): Nến đỏ xả nuốt chửng >= 80% nến xanh bạo phát liền trước -> VETO.

    Returns:
        (is_veto: bool, reason: str)
    """
    clean_sym = str(symbol or "").upper().replace("/USDT", "").replace("USDT", "").strip()
    if not clean_sym or "COIN_" in clean_sym or "TEST" in clean_sym:
        return False, ""
    sym_api = f"{clean_sym}USDT"

    df_15m = klines_15m
    if df_15m is None:
        try:
            from core.klines_cache import get_klines_cached
            df_15m = get_klines_cached(sym_api, "15m", limit=10)
        except Exception as e:
            logger.debug(f"Không lấy được nến 15m cho {symbol}: {e}")
            return False, ""

    if df_15m is None or len(df_15m) < 2:
        return False, ""

    try:
        cols = {str(c).lower(): c for c in df_15m.columns}
        c_open = cols.get('open')
        c_high = cols.get('high')
        c_low = cols.get('low')
        c_close = cols.get('close')
        if not (c_open and c_high and c_low and c_close):
            return False, ""

        recent_df = df_15m.tail(4)
        records = recent_df[[c_open, c_high, c_low, c_close]].to_dict('records')

        # Rule 2.1: Quét râu xả đỉnh 15M trên từng nến trong 4 nến gần nhất
        for r in reversed(records):
            o = float(r[c_open])
            h = float(r[c_high])
            l = float(r[c_low])
            c = float(r[c_close])
            candle_range = h - l
            if l <= 0 or candle_range <= 0:
                continue

            pct_amplitude = candle_range / l
            body_top = max(o, c)
            upper_wick = h - body_top
            wick_ratio = upper_wick / candle_range

            if wick_ratio >= 0.60 and pct_amplitude > 0.01:
                wick_pct = int(round(wick_ratio * 100))
                return True, f"Khóa bóp cò: Rule 2.1 (Râu xả đỉnh 15M) - Nến xả ngược râu {wick_pct}%."

        # Rule 2.2: Cây thông xả đỉnh (Nến đỏ nuốt chửng >= 80% nến xanh bạo phát liền trước)
        for i in range(len(records) - 1):
            prev = records[i]
            curr = records[i + 1]
            o_p, c_p = float(prev[c_open]), float(prev[c_close])
            o_c, c_c = float(curr[c_open]), float(curr[c_close])

            if c_p > o_p and o_p > 0:
                pump_body = c_p - o_p
                pump_pct = pump_body / o_p
                if c_c < o_c:
                    dump_body = o_c - c_c
                    if pump_body > 0 and (dump_body >= 0.80 * pump_body) and pump_pct >= 0.005:
                        dump_pct = int(round((dump_body / pump_body) * 100))
                        return True, f"Khóa bóp cò: Rule 2.2 (Cây thông xả đỉnh) - Nến đỏ nuốt {dump_pct}% nến xanh bạo phát."

    except Exception as e:
        logger.debug(f"Lỗi kiểm tra check_pump_dump_trap cho {symbol}: {e}")

    return False, ""


def evaluate_order_gatekeeper_firewall(
    order: Dict[str, Any],
    klines_15m: Optional[Any] = None
) -> Tuple[str, str, Optional[str]]:
    """
    [PHẦN 2.2: PHÂN LUỒNG 5 TẦNG (FIREWALL LOGIC V2.3)]
    Kiểm tra 5 tầng tường lửa trước khi cho phép kích hoạt lệnh:

    - TẦNG 0 (Bẫy 15M): check_pump_dump_trap -> REJECT
    - TẦNG 1 (Cảnh Báo Sớm): Cấp 3 -> REJECT
    - TẦNG 2 (Suy Giảm): Cấp 1 -> REJECT; Cấp 2 và Grid -> REJECT
    - TẦNG 3 (Bảng Rebalance): MẤT MA7_3D -> REJECT; NÉ GRID và Grid -> REJECT; Tier S/A Động cơ 3/4 RSI_1H >= 70 -> REJECT
    - TẦNG 4 (Kỹ Thuật & R:R): SL >= 15% hoặc R:R < 2.0 -> REJECT; Động cơ 3 Gate 1 < 25 -> PENDING
    - TẦNG 5 (Leverage Advisor):
        * Tier S/A Futures Grid -> REJECT (Chỉ cấp phép Spot OCO / Spot Grid)
        * Tier B Futures Grid > 5x -> Warning hạ về 5x (APPROVED)
        * Tier C Futures Grid > 3x -> Warning hạ về 3x (APPROVED)

    Returns:
        (verdict: 'APPROVED' | 'REJECT' | 'PENDING', reason: str, leverage_warning: Optional[str])
    """
    clean_sym = str(order.get('symbol', '')).replace('/', '').replace('USDT', '').strip()
    sym_api = f"{clean_sym}USDT"
    uy_tin = _safe_float(order.get('uy_tin_score'), 0.0)
    tier_name = str(order.get('tier_label', '') or '')
    is_tier_sa = ('Tier S' in tier_name or 'Tier A' in tier_name or uy_tin >= 60.0)
    is_tier_b = ('Tier B' in tier_name or (40.0 <= uy_tin < 60.0))
    is_tier_c = ('Tier C' in tier_name or (25.0 <= uy_tin < 40.0))
    is_grid = (order.get('order_type') == 'GRID')
    penalty_tags = order.get('penalty_tags', []) or []

    # -------------------------------------------------------------
    # TẦNG 0: Bẫy 15M (check_pump_dump_trap)
    # -------------------------------------------------------------
    is_trap, trap_msg = check_pump_dump_trap(order.get('symbol', ''), klines_15m=klines_15m)
    if is_trap:
        return 'REJECT', trap_msg, None

    # -------------------------------------------------------------
    # TẦNG 1: Cảnh Báo Sớm (Cấp 3 Khẩn Cấp - Gãy ST 1D)
    # -------------------------------------------------------------
    w_lvl = int(_safe_float(order.get('warning_level', 0), 0))
    w_lbl = str(order.get('warning_label', '') or '')
    if w_lvl == 3 or "CẤP 3" in w_lbl or any("Gãy ST 1D" in t or "Cấp 3" in t for t in penalty_tags):
        return 'REJECT', "Gãy Supertrend 1D (Cảnh Báo Sớm Cấp 3 Khẩn Cấp)", None

    # -------------------------------------------------------------
    # TẦNG 2: Suy Giảm (Cấp 1 & Cấp 2)
    # -------------------------------------------------------------
    dg_lvl = str(order.get('downgrade_level', '') or '').upper()
    if dg_lvl == "CAP_1" or any("Xả Dốc C1" in t for t in penalty_tags):
        return 'REJECT', "Khóa bóp cò: Suy giảm Cấp 1 (Xả dốc mạnh / Cây thông)", None
    if (dg_lvl == "CAP_2" or any("Suy Yếu C2" in t for t in penalty_tags)) and is_grid:
        return 'REJECT', "Suy yếu Cấp 2: CẤM mở Grid khi mất bệ đỡ đà tăng", None

    # -------------------------------------------------------------
    # TẦNG 3: Bảng Rebalance & Động Lực Ngắn Hạn
    # -------------------------------------------------------------
    if order.get('lost_ma7_3d') or any("MẤT MA7" in str(x).upper() for x in [order.get('strategy_desc', ''), *penalty_tags]):
        return 'REJECT', "Khóa bóp cò: MẤT MA7_3D (Đáy sóng trung hạn bị gãy)", None
    if is_grid and (order.get('is_ne_grid') or any("Né Grid" in str(x) for x in penalty_tags)):
        if order.get('is_grid_gom_hang'):
            return 'PENDING', "Grid Gom Hàng dính cờ NÉ GRID / Chiết khấu sâu - Chờ duyệt tay bắt dao rơi", None
        return 'REJECT', "Cảnh báo NÉ GRID từ Bảng Rebalance (Rủi ro biến động giật / cạn cầu)", None

    engine_str = str(order.get('engine', '') or '')
    is_dc3_dc4 = any(e in engine_str for e in ['DC3', 'DC4', 'Động cơ 3', 'Động cơ 4', 'Breakout', 'Hot Trend'])
    rsi_1h_val = float(order.get('rsi_1h', 0.0) or 0.0)
    if is_tier_sa and is_dc3_dc4 and rsi_1h_val >= 70.0:
        return 'REJECT', f"Tier S/A đánh {engine_str} nhưng RSI 1H Quá Mua ({rsi_1h_val:.1f} >= 70) - Rủi ro mua đỉnh", None

    # -------------------------------------------------------------
    # TẦNG 4: Kỹ Thuật & R:R
    # -------------------------------------------------------------
    entry = _safe_float(order.get('entry', order.get('trigger', 0.0)), 0.0)
    sl_val = _safe_float(order.get('sl', 0.0), 0.0)
    if entry > 0 and sl_val > 0:
        sl_dist_pct = abs(entry - sl_val) / entry
        if sl_dist_pct >= 0.15:
            return 'REJECT', f"SL khoảng cách quá rộng ({sl_dist_pct*100:.1f}% >= 15.0%) - Vi phạm trần cắt lỗ", None

    rr_val = _safe_float(order.get('rr_ratio', 2.0), 2.0)
    if not is_grid and rr_val < 2.0:
        return 'REJECT', f"Tỷ lệ R:R = 1:{rr_val:.1f} < 2.0 (Không đạt kỳ vọng sinh lời)", None

    gate1_val = _safe_float(order.get('gate1_score', 30.0), 30.0)
    if ('DC3' in engine_str or 'Breakout' in engine_str) and gate1_val < 25.0:
        return 'PENDING', f"Động cơ 3 (Breakout) nhưng Gate 1 = {gate1_val:.1f} < 25đ - Chờ xác nhận móng", None

    # -------------------------------------------------------------
    # TẦNG 5: Leverage Advisor & Định Tuyến Hợp Trọng
    # -------------------------------------------------------------
    lev = int(_safe_float(order.get('leverage', 1), 1))
    market_type = str(order.get('market_type', 'SPOT')).upper()
    is_futures_grid = is_grid and (market_type == 'FUTURES' or 'FUTURES' in str(order.get('strategy_desc', '')).upper() or lev > 1)

    lev_warning = None
    if is_futures_grid:
        if is_tier_sa:
            tier_display = "Tier S" if "Tier S" in tier_name else ("Tier A" if "Tier A" in tier_name else "Tier S/A")
            return 'REJECT', f"{sym_api} ({tier_display}): CẤM Futures Grid. Chỉ cấp phép Spot OCO / Spot Grid.", None

        if is_tier_b and lev > 5:
            lev_warning = f"{sym_api} (KHUYẾN NGHỊ: Đòn bẩy {lev}x quá cao cho Tier B. Nên hạ xuống Max 5x)"
        elif is_tier_c and lev > 3:
            lev_warning = f"{sym_api} (KHUYẾN NGHỊ: Đòn bẩy {lev}x quá cao cho Tier C. Nên hạ xuống Max 3x)"

    return 'APPROVED', "Lệnh vượt qua 5 tầng Gatekeeper thành công.", lev_warning


def optimized_execution_geometry_module(
    clean_summary_orders: List[Dict[str, Any]],
    klines_cache_map: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Module Tinh Chỉnh Hình Học Lệnh & Phân Luồng Thực Thi V2.3.
    Input: Danh sách lệnh ĐÃ ĐƯỢC LỌC SẠCH RỦI RO từ Tầng 2.
    Output: Danh sách lệnh hoàn chỉnh sau kiểm định 5 tầng Firewall,
            phân vùng thành AUTO_EXECUTE, MANUAL_APPROVAL, VETO_REJECT.
    """
    final_execution_orders = []
    grid_tier_b_candidates = []

    for order in clean_summary_orders:
        order_copy = dict(order)
        symbol = order_copy['symbol']
        uy_tin = float(order_copy.get('uy_tin_score', 0.0))
        order_type = order_copy.get('order_type', 'SPOT_OCO')

        # =========================================================
        # TIẾN TRÌNH 1: THỰC TẾ HÓA HÌNH HỌC LỆNH (ORDER GEOMETRY)
        # =========================================================
        if order_type == 'SPOT_OCO':
            entry = float(order_copy['entry'])
            raw_sl = float(order_copy['raw_sl'])
            raw_tp = float(order_copy.get('raw_tp', entry * 1.05))

            order_copy['raw_sl'] = raw_sl
            order_copy['raw_tp'] = raw_tp

            # 1. Anti-Wick SL: Kéo giãn SL nếu quá khít (tạo vùng đệm 1.5%)
            sl_distance_pct = (entry - raw_sl) / entry if entry > 0 else 0
            if sl_distance_pct < 0.015:
                safe_sl = entry * 0.985
                sl_adjusted = True
            else:
                safe_sl = raw_sl
                sl_adjusted = False

            risk_amount = entry - safe_sl

            # 2. Realistic TP: Thác Nước Kháng Cự & Mốc Tâm Lý
            realistic_tp, tp_source_desc, waterfall_meta = calculate_waterfall_tp(
                symbol=symbol,
                entry=entry,
                safe_sl=safe_sl,
                raw_tp=raw_tp,
                min_rr=2.0
            )
            tp_adjusted = (abs(realistic_tp - raw_tp) > 1e-6)

            # 3. Nắn Bóp Hình Học GRID TP2 Dự Phòng
            grid_tp2_opt = optimize_grid_tp2_geometry(order_copy, entry, safe_sl, realistic_tp)

            reward_amount = realistic_tp - entry
            rr_val = round(reward_amount / risk_amount, 2) if risk_amount > 0 else 2.0

            note_parts = []
            if sl_adjusted:
                note_parts.append("Nới đệm SL (+1.5%)")
            note_parts.append(f"TP: {tp_source_desc}")
            geom_note = ", ".join(note_parts)

            from core.price_formatter import round_price
            order_copy.update({
                'entry': entry,
                'sl': round_price(safe_sl),
                'tp': round_price(realistic_tp),
                'tp_source': tp_source_desc,
                'sl_adjusted': sl_adjusted,
                'tp_adjusted': tp_adjusted,
                'auto_breakeven': True,
                'geometry_note': geom_note,
                'grid_tp2': grid_tp2_opt,
                'grid_tp2_optimized': grid_tp2_opt,
                'rr_ratio': rr_val,
                'money_flow': float(order_copy.get('money_flow', 0.0) or 0.0)
            })

        elif order_type == 'GRID':
            macro_lower = float(order_copy['lower'])
            macro_upper = float(order_copy['upper'])
            raw_grids = int(order_copy.get('raw_grids', order_copy.get('grids', 24)))
            dec = _count_decimals(macro_lower)

            trigger = order_copy.get('entry', order_copy.get('trigger'))
            if trigger is None or trigger == 'N/A':
                trigger = round((macro_lower + macro_upper) / 2.0, dec)
            else:
                trigger = float(trigger)

            order_copy['raw_lower'] = macro_lower
            order_copy['raw_upper'] = macro_upper
            order_copy['raw_grids'] = raw_grids

            grid_sl = round(trigger * 0.88, dec)
            opt_upper = max(macro_upper, round(trigger * 1.20, dec))

            min_safe_lower = round(grid_sl * 1.015, dec)
            opt_lower = max(macro_lower, min_safe_lower)
            if opt_lower >= trigger:
                opt_lower = round(trigger * 0.90, dec)
            opt_lower = max(opt_lower, min_safe_lower)

            grid_tp = opt_upper

            raw_sl = order_copy.get('raw_sl')
            if raw_sl is None or raw_sl == 'N/A':
                raw_sl = round(trigger * 0.995, dec)
            else:
                raw_sl = float(raw_sl)

            raw_tp = order_copy.get('raw_tp')
            if raw_tp is None or raw_tp == 'N/A':
                raw_tp = round(trigger * 1.033, dec)
            else:
                raw_tp = float(raw_tp)

            grid_risk = trigger - grid_sl
            grid_reward = grid_tp - trigger
            grid_rr = round(grid_reward / grid_risk, 2) if grid_risk > 0 else 1.67

            order_copy.update({
                'lower': round(opt_lower, dec),
                'upper': round(opt_upper, dec),
                'grids': 24,
                'entry': round(trigger, dec),
                'trigger': round(trigger, dec),
                'sl': round(grid_sl, dec),
                'tp': round(grid_tp, dec),
                'raw_sl': raw_sl,
                'raw_tp': raw_tp,
                'geometry_note': "Chuẩn hóa +8% Vốn / -12% SL, Đệm SL +1.5%, 24 Lưới",
                'rr_ratio': grid_rr,
                'money_flow': float(order_copy.get('money_flow', 0.0) or 0.0)
            })

        if order_copy.get('engine') in ENGINE_LABELS:
            order_copy['engine'] = ENGINE_LABELS[order_copy['engine']]

        # =========================================================
        # TIẾN TRÌNH 2: TƯỜNG LỬA 5 TẦNG GATEKEEPER (FIREWALL V2.3)
        # =========================================================
        k15 = (klines_cache_map or {}).get(symbol)
        verdict, reason, lev_warning = evaluate_order_gatekeeper_firewall(order_copy, klines_15m=k15)

        if verdict == 'REJECT':
            order_copy['status'] = 'VETO_REJECT'
            order_copy['veto_reason'] = reason
            final_execution_orders.append(order_copy)
            continue

        if verdict == 'PENDING':
            order_copy['status'] = 'MANUAL_APPROVAL'
            order_copy['locked_reason'] = reason
            order_copy['volume_pct'] = 0.3
            final_execution_orders.append(order_copy)
            continue

        # Lệnh vượt 5 tầng thành công (APPROVED)
        order_copy['gatekeeper_approved_msg'] = reason
        if lev_warning:
            order_copy['leverage_warning'] = lev_warning

    # -------------------------------------------------------------
        # 1. Chốt chặn Tier F (< 25đ) & Tier C thường (< 40đ không bạo phát)
        # Loại bỏ hoàn toàn khỏi hàng đợi thực thi
        # -------------------------------------------------------------
        if uy_tin < 25.0:
            continue

        mf_val = _safe_float(order_copy.get('money_flow'), 0.0)
        is_mf_surge = bool(order_copy.get('is_money_flow_surge', False)) or mf_val >= 3.0
        if uy_tin < 40.0:
            if order_copy.get('is_grid_gom_hang'):
                order_copy['status'] = 'MANUAL_APPROVAL'
                order_copy['volume_pct'] = 0.2
                order_copy['locked_reason'] = f"Tier C - Grid Gom Hàng chiết khấu sâu (Bắt dao rơi {mf_val:.1f}x)" if mf_val > 0 else "Tier C - Grid Gom Hàng chiết khấu sâu (Bắt dao rơi)"
                final_execution_orders.append(order_copy)
                continue
            if not is_mf_surge:
                continue
            order_copy['status'] = 'MANUAL_APPROVAL'
            order_copy['volume_pct'] = 0.2
            order_copy['tier_c_surge'] = True
            order_copy['locked_reason'] = f"Tier C - Dòng Tiền Bạo Phát {mf_val:.1f}x"
            final_execution_orders.append(order_copy)
            continue

        # 3. Phân luồng Tier S/A và Tier B
        has_macro_risk = bool(order_copy.get('macro_risk', False))
        tier_label = str(order_copy.get('tier_label', ''))
        is_tier_sa = ('Tier S' in tier_label or 'Tier A' in tier_label or uy_tin >= 60.0)

        if has_macro_risk:
            # BTC Gate khóa nòng -> MANUAL_APPROVAL
            order_copy['status'] = 'MANUAL_APPROVAL'
            order_copy['locked_reason'] = order_copy.get('macro_risk_reason') or 'BTC Fakeout Risk'
            order_copy['volume_pct'] = 0.3
            final_execution_orders.append(order_copy)
        elif is_tier_sa:
            if order_copy.get('is_grid_gom_hang') and not order_copy.get('is_entry_4h_chosen', True):
                order_copy['status'] = 'MANUAL_APPROVAL'
                order_copy['locked_reason'] = 'Grid Gom Hàng - Trigger chiết khấu sâu (Bắt dao rơi)'
                order_copy['volume_pct'] = 0.3
            else:
                order_copy['status'] = 'AUTO_EXECUTE'
                order_copy['volume_pct'] = 1.0
            final_execution_orders.append(order_copy)
        else:
            # Tier B (40 - 59.9đ)
            if order_type == 'GRID':
                if order_copy.get('is_grid_gom_hang') and not order_copy.get('is_entry_4h_chosen', True):
                    order_copy['status'] = 'MANUAL_APPROVAL'
                    order_copy['locked_reason'] = 'Tier B - Grid Gom Hàng chiết khấu sâu (Bắt dao rơi)'
                    order_copy['volume_pct'] = 0.3
                else:
                    # Grid cho Tier B được cấp vốn -> AUTO_EXECUTE
                    order_copy['status'] = 'AUTO_EXECUTE'
                    order_copy['volume_pct'] = 1.0
                    grid_tier_b_candidates.append(order_copy)
            else:
                # Spot OCO Tier B -> Chờ duyệt tay
                order_copy['status'] = 'MANUAL_APPROVAL'
                order_copy['locked_reason'] = 'Tier B - Cần xác nhận bóp cò tay'
                order_copy['volume_pct'] = 0.3
            final_execution_orders.append(order_copy)

    # Tính toán Priority Queue cho Grid Tier B
    if grid_tier_b_candidates:
        try:
            from core.historical_rater import calculate_grid_priority_queue
            ranked_grids = calculate_grid_priority_queue(grid_tier_b_candidates)
            rank_map = {c['symbol']: (c.get('grid_priority_rank'), c.get('grid_priority_label')) for c in ranked_grids}
            for o in final_execution_orders:
                if o['symbol'] in rank_map:
                    rnk, lbl = rank_map[o['symbol']]
                    o['grid_priority_rank'] = rnk
                    o['grid_priority_label'] = lbl
        except Exception as e:
            logger.debug(f"Không tính được grid priority queue: {e}")

    return final_execution_orders


def _order_sort_key(o: Dict[str, Any]) -> tuple:
    """
    Khóa sắp xếp đa tầng ưu tiên trong Hàng Đợi Thực Thi:
    1. Điểm Tín Nhiệm 14 ngày (uy_tin_score) DESC
    2. Hệ số Dòng Tiền Bạo Phát (money_flow) DESC
    3. Tỷ lệ R:R sau nắn hình học DESC
    """
    uy_tin = _safe_float(o.get('uy_tin_score'), 0.0)
    mf = _safe_float(o.get('money_flow'), 0.0)
    rr = _safe_float(o.get('rr_ratio'), 0.0)
    return (uy_tin, mf, rr)


def _format_macro_4h_line_gk(sym: str, macro: Any, dec: int = 4) -> str:
    """Format dòng thông tin Macro 4H cho lệnh trong Gatekeeper."""
    if not macro:
        return ""
    e4 = getattr(macro, "entry_4h", None)
    sl4 = getattr(macro, "sl_4h", None)
    tp1 = getattr(macro, "tp_1h", None)
    tp4 = getattr(macro, "tp_4h", None)
    if e4 is not None and sl4 is not None and tp1 is not None and tp4 is not None:
        return f"    ↳ [Macro 4H] Entry: {_fmt_price_val(float(e4), dec):<10} | SL Cứng: {_fmt_price_val(float(sl4), dec):<10} | TP 1H: {_fmt_price_val(float(tp1), dec):<10} | TP 4H: {_fmt_price_val(float(tp4), dec)}"
    return ""


def _print_order_geometry_details(o: Dict[str, Any]):
    """In chi tiết thông số hình học lệnh (Entry, SL, TP, R:R, Grid TP2) cho người vận hành."""
    try:
        order_type = o.get('order_type', 'SPOT_OCO')
        entry = _safe_float(o.get('entry', o.get('trigger', 0.0)), 0.0)
        sl_val = _safe_float(o.get('sl', o.get('raw_sl', 0.0)), 0.0)
        tp_val = _safe_float(o.get('tp', o.get('raw_tp', 0.0)), 0.0)

        # 1. Khởi tạo an toàn biến dec (số chữ số thập phân) từ trước
        dec = 2
        all_vals = [entry, sl_val, tp_val, _safe_float(o.get('lower', 0.0)), _safe_float(o.get('upper', 0.0))]
        valid_vals = [v for v in all_vals if v > 0]
        if valid_vals:
            dec = max(_count_decimals(v) for v in valid_vals)

        clean_sym = str(o.get('symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip()
        if clean_sym:
            try:
                from core.exchange_info_cache import ExchangeInfoCache
                tick_size = ExchangeInfoCache.get_tick_size(f"{clean_sym}USDT")
                if tick_size and tick_size > 0:
                    s_tick = f"{tick_size:.10f}".rstrip('0')
                    if '.' in s_tick:
                        dec = max(dec, len(s_tick.split('.')[1]))
            except Exception:
                pass

        if order_type == 'SPOT_OCO':
            if entry > 0:
                sl_pct = ((sl_val - entry) / entry) * 100 if sl_val > 0 else 0.0
                tp_pct = ((tp_val - entry) / entry) * 100 if tp_val > 0 else 0.0
                rr_val = _safe_float(o.get('rr_ratio'), 2.0)
                print(f"    ↳ 🎯 LỆNH OCO: Entry={_fmt_price_val(entry, dec)} | SL={_fmt_price_val(sl_val, dec)} ({sl_pct:+.1f}%) | TP={_fmt_price_val(tp_val, dec)} ({tp_pct:+.1f}%) | R:R 1:{rr_val:.1f}")

            if 'grid_tp2' in o and isinstance(o['grid_tp2'], dict):
                g = o['grid_tp2']
                opt_note = " | Kèm Auto-Breakeven (+1%)" if o.get('auto_breakeven') else ""
                g_low = _safe_float(g.get('lower', 0.0))
                g_up = _safe_float(g.get('upper', 0.0))
                f_low = _fmt_price_val(g_low, dec)
                f_up = _fmt_price_val(g_up, dec)
                grids_cnt = g.get('grids', 24)
                print(f"    ↳ 🛡️ LƯỚI TP2: Dải L={f_low} - U={f_up} ({grids_cnt} Lưới){opt_note}")
            elif o.get('auto_breakeven'):
                print(f"    ↳ 🛡️ Option: Kích hoạt Auto-Breakeven (+1%).")

        elif order_type == 'GRID':
            trigger = _safe_float(o.get('entry', o.get('trigger', entry)), 0.0)
            lower = _safe_float(o.get('lower', o.get('raw_lower', 0.0)), 0.0)
            upper = _safe_float(o.get('upper', o.get('raw_upper', 0.0)), 0.0)
            grids = o.get('grids', 24)
            lbl = "LỆNH GRID GOM HÀNG" if (o.get('is_grid_gom_hang') or o.get('engine') == 'GRID_GOM_HANG') else "LỆNH GRID"
            print(f"    ↳ 🥅 {lbl}: Dải L={_fmt_price_val(lower, dec)} - U={_fmt_price_val(upper, dec)} ({grids} Lưới) | Trig: {_fmt_price_val(trigger, dec)} | SL: {_fmt_price_val(sl_val, dec)} | TP: {_fmt_price_val(tp_val, dec)}")
            if (o.get('is_grid_gom_hang') or o.get('engine') == 'GRID_GOM_HANG') and o.get('macro_levels'):
                macro_line = _format_macro_4h_line_gk(clean_sym, o.get('macro_levels'), dec)
                if macro_line:
                    print(macro_line)

    except Exception as geom_err:
        logger.debug(f"Lỗi hiển thị geometry details cho {o.get('symbol')}: {geom_err}")


def render_gatekeeper_board(final_orders):
    print("=" * 100)
    print("🛡️ [MODULE BẢNG DUYỆT LỆNH TỐI ƯU] HÀNG ĐỢI THỰC THI (PHIÊN BẢN V2.3)")
    print("=" * 100)

    final_orders = final_orders or []
    auto_exec = sorted([o for o in final_orders if o.get('status') == 'AUTO_EXECUTE'], key=_order_sort_key, reverse=True)
    manual_exec = sorted([o for o in final_orders if o.get('status') == 'MANUAL_APPROVAL'], key=_order_sort_key, reverse=True)
    veto_reject = sorted([o for o in final_orders if o.get('status') == 'VETO_REJECT'], key=_order_sort_key, reverse=True)

    if not auto_exec and not manual_exec and not veto_reject:
        print("   ℹ️ Hiện tại không có lệnh nào trong Hàng Đợi Thực Thi.")
        print("=" * 100)
        return

    # 🟢 VÙNG 1: LỆNH DUYỆT TỰ ĐỘNG (AUTO-EXECUTE)
    print("\n🟢 VÙNG 1: LỆNH DUYỆT TỰ ĐỘNG (AUTO-EXECUTE)")
    if not auto_exec:
        print("   (Không có lệnh nào trong Vùng 1)")
    for idx, o in enumerate(auto_exec, 1):
        sym = o.get('symbol', '')
        uy_tin = _safe_float(o.get('uy_tin_score'), 0.0)
        tier_label = o.get('tier_label', '')
        tier_str = f" | {tier_label} ({uy_tin:.1f}đ)" if tier_label else f" | Uy tín: {uy_tin:.1f}đ"
        engine_str = o.get('engine_label', o.get('engine', o.get('strategy_desc', '')))
        order_type = o.get('order_type', 'SPOT_OCO')
        lev = int(_safe_float(o.get('leverage', 1), 1))
        is_fut = (o.get('market_type') == 'FUTURES' or 'FUTURES' in str(o.get('strategy_desc', '')).upper() or lev > 1)

        if order_type == 'SPOT_OCO':
            tag = "🎯 [SPOT OCO]"
            print(f"[{idx}] {tag} {sym}{tier_str} | {engine_str}")
        elif o.get('is_grid_gom_hang') or o.get('engine') == 'GRID_GOM_HANG':
            tag = "🥅 [GRID GOM HÀNG]"
            lower = _fmt_price_val(_safe_float(o.get('lower'), 0.0))
            upper = _fmt_price_val(_safe_float(o.get('upper'), 0.0))
            print(f"[{idx}] {tag} {sym}{tier_str} | Zone: {lower} - {upper}")
        else:
            tag = f"🥅 [GRID {lev}x]" if is_fut and lev > 1 else ("🥅 [FUTURES GRID]" if is_fut else "🥅 [GRID]")
            lower = _fmt_price_val(_safe_float(o.get('lower'), 0.0))
            upper = _fmt_price_val(_safe_float(o.get('upper'), 0.0))
            p_rank = o.get('grid_priority_rank')
            p_str = f" (Ưu tiên #{p_rank})" if p_rank else ""
            print(f"[{idx}] {tag} {sym}{tier_str} | Zone: {lower} - {upper}{p_str}")

        if o.get('leverage_warning'):
            print(f"    ↳ Gatekeeper: [WARNING] {o['leverage_warning']}")
        app_msg = o.get('gatekeeper_approved_msg', 'Lệnh vượt qua 5 tầng Gatekeeper thành công.')
        print(f"    ↳ Gatekeeper: [APPROVED] {app_msg}")
        _print_order_geometry_details(o)

    # 🟡 VÙNG 2: LỆNH CHỜ DUYỆT TAY (MANUAL APPROVAL / KHÓA NÒNG BTC)
    print("\n🟡 VÙNG 2: LỆNH CHỜ DUYỆT TAY (MANUAL APPROVAL / KHÓA NÒNG BTC)")
    if not manual_exec:
        print("   (Không có lệnh nào trong Vùng 2)")
    for idx, o in enumerate(manual_exec, 1):
        sym = o.get('symbol', '')
        uy_tin = _safe_float(o.get('uy_tin_score'), 0.0)
        tier_label = o.get('tier_label', '')
        tier_str = f" | {tier_label} ({uy_tin:.1f}đ)" if tier_label else f" | Uy tín: {uy_tin:.1f}đ"
        engine_str = o.get('engine_label', o.get('engine', o.get('strategy_desc', '')))
        order_type = o.get('order_type', 'SPOT_OCO')

        if order_type == 'SPOT_OCO':
            tag = "🎯 [SPOT OCO]"
            print(f"[{idx}] {tag} {sym}{tier_str} | {engine_str}")
        elif o.get('is_grid_gom_hang') or o.get('engine') == 'GRID_GOM_HANG':
            tag = "🥅 [GRID GOM HÀNG]"
            lower = _fmt_price_val(_safe_float(o.get('lower'), 0.0))
            upper = _fmt_price_val(_safe_float(o.get('upper'), 0.0))
            print(f"[{idx}] {tag} {sym}{tier_str} | Zone: {lower} - {upper}")
        else:
            tag = "🥅 [GRID]"
            print(f"[{idx}] {tag} {sym}{tier_str} | {engine_str}")

        lock_msg = o.get('locked_reason', 'Chờ duyệt tay')
        print(f"    ↳ Gatekeeper: [LOCKED] Chờ duyệt tay ({lock_msg}).")
        _print_order_geometry_details(o)

    # 🔴 VÙNG 3: LỆNH BỊ PHỦ QUYẾT TỪ CHỐI (VETO REJECT)
    print("\n🔴 VÙNG 3: LỆNH BỊ PHỦ QUYẾT TỪ CHỐI (VETO REJECT)")
    if not veto_reject:
        print("   (Không có lệnh nào bị từ chối)")
    for idx, o in enumerate(veto_reject, 1):
        sym = o.get('symbol', '')
        uy_tin = _safe_float(o.get('uy_tin_score'), 0.0)
        tier_label = o.get('tier_label', '')
        tier_str = f" | {tier_label} ({uy_tin:.1f}đ)" if tier_label else ""
        engine_str = o.get('engine_label', o.get('engine', o.get('strategy_desc', '')))
        order_type = o.get('order_type', 'SPOT_OCO')
        lev = int(_safe_float(o.get('leverage', 1), 1))
        is_fut = (o.get('market_type') == 'FUTURES' or 'FUTURES' in str(o.get('strategy_desc', '')).upper() or lev > 1)

        if order_type == 'SPOT_OCO':
            tag = "🚫 [SPOT OCO]"
            print(f"[{idx}] {tag} {sym} | {engine_str}")
        elif o.get('is_grid_gom_hang') or o.get('engine') == 'GRID_GOM_HANG':
            tag = "🚫 [GRID GOM HÀNG]"
            print(f"[{idx}] {tag} {sym}{tier_str}")
        else:
            tag = "🚫 [FUTURES GRID]" if is_fut else "🚫 [GRID]"
            print(f"[{idx}] {tag} {sym}{tier_str}")

        veto_msg = o.get('veto_reason', 'Vi phạm chốt chặn Gatekeeper.')
        print(f"    ↳ Gatekeeper: [VETO] {veto_msg}")
        _print_order_geometry_details(o)

    print("=" * 100)



# ── ADAPTER CHUYỂN ĐỔI DỮ LIỆU TỪ TẦNG 2 ──────────────────────────────────────

_RE_ENTRY = re.compile(r"(?:In|Entry|Buy|Trig)\s*[:=]\s*([\d.]+)")
_RE_SL    = re.compile(r"SL\s*[:=]\s*([\d.]+)")
_RE_TP    = re.compile(r"TP1?\s*[:=]\s*([\d.]+)")
_RE_GRID  = re.compile(r"([\d.]+)\s*-\s*([\d.]+)\s*\|\s*(\d+)\s*(?:L|Lưới)")
_RE_MONEY_FLOW = re.compile(r"(?:TIỀN BẠO PHÁT|BẠO PHÁT)\s*\((\d+(?:\.\d+)?)x\)")


def extract_clean_summary_orders(
    summary_board_res: Dict[str, Any],
    v2_profiles: Optional[List[Dict[str, Any]]] = None,
    grid_gom_hang_items: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Trích xuất danh sách lệnh sạch từ Tầng 2 (Bảng Tổng Kết & Hồ Sơ Tín Nhiệm).
    Tầng 2 đã lọc 100% rủi ro vĩ mô (chốt chặn sinh tử, Blacklist...).
    Nếu BTC Gate đóng (spot_btc_locked = True), các lệnh SPOT không bị xóa bỏ
    mà được chuyển hướng sang 'MANUAL_APPROVAL' (Chờ duyệt tay / 30% Vol thăm dò) theo đặc tả.
    Bổ sung nhóm Dòng Tiền Bạo Phát (top_money_flow) đã qua chốt kiểm duyệt bóp cò.
    """
    clean_orders: List[Dict[str, Any]] = []
    if not summary_board_res:
        return clean_orders

    # 1. Bản đồ Hồ sơ tín nhiệm (Uy tín & Tier)
    trust_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(v2_profiles, dict):
        for k, v in v2_profiles.items():
            sym = str(k).upper().replace("/USDT", "").replace("USDT", "").strip()
            if sym and isinstance(v, dict):
                trust_map[sym] = v
            elif sym:
                trust_map[sym] = {"symbol": sym, "total_score": float(v) if isinstance(v, (int, float)) else 50.0}
    elif isinstance(v2_profiles, list):
        for p in v2_profiles:
            if isinstance(p, dict):
                sym = str(p.get("symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
                if sym:
                    trust_map[sym] = p
            elif hasattr(p, "symbol"):
                sym = str(getattr(p, "symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
                if sym:
                    trust_map[sym] = {
                        "symbol": sym,
                        "total_score": getattr(p, "total_score", 50.0),
                        "tier": getattr(p, "tier", "")
                    }

    # 2. Xử lý Lệnh SPOT (Lướt sóng OCO & Top Dòng Tiền Bạo Phát)
    btc_locked = bool(summary_board_res.get("spot_btc_locked", False))
    spot_signals = summary_board_res.get("spot", []) or []
    top_mf_signals = summary_board_res.get("top_money_flow", []) or []

    seen_spot_syms = set()
    
    # Gom nguồn tín hiệu: Spot thông thường + Top 3 Dòng tiền Bạo Phát
    combined_spot: List[Tuple[Any, bool]] = []
    for s in spot_signals:
        combined_spot.append((s, False))
    for s in top_mf_signals:
        combined_spot.append((s, True))

    for s, is_top_mf in combined_spot:
        sym_raw = getattr(s, "symbol", "")
        clean_sym = sym_raw.upper().replace("/USDT", "").replace("USDT", "").strip()
        if not clean_sym or clean_sym in seen_spot_syms:
            continue

        # Đọc điểm uy tín từ hồ sơ 14 ngày hoặc từ target_score
        p_info = trust_map.get(clean_sym, {})
        score = float(p_info.get("total_score", getattr(s, "target_score", 0.0) or 50.0))
        tier = str(p_info.get("tier", ""))

        # Phân tích chuỗi setup
        line = getattr(s, "setup_line", "") or ""
        m_entry = _RE_ENTRY.search(line)
        m_sl = _RE_SL.search(line)
        m_tp = _RE_TP.search(line)

        # Trích xuất hệ số dòng tiền bạo phát
        mf_val = float(getattr(s, "money_flow", 0.0) or 0.0)
        if mf_val <= 0.0 and line:
            m_mf = _RE_MONEY_FLOW.search(line)
            if m_mf:
                try:
                    mf_val = float(m_mf.group(1))
                except Exception:
                    pass

        if m_entry and m_sl:
            entry = float(m_entry.group(1))
            raw_sl = float(m_sl.group(1))
            if m_tp:
                raw_tp = float(m_tp.group(1))
            else:
                rr = float(getattr(s, "rr_ratio", 2.0) or 2.0)
                raw_tp = entry + (entry - raw_sl) * rr

            engine = getattr(s, "engine_source", "")
            if is_top_mf or mf_val >= 3.0:
                if engine == "DC3":
                    strat = f"Breakout - Bạo Phát ({mf_val:.1f}x)" if mf_val > 0 else "Breakout - Bạo Phát"
                elif engine == "DC4":
                    strat = f"Hot Trend - Bạo Phát ({mf_val:.1f}x)" if mf_val > 0 else "Hot Trend - Bạo Phát"
                else:
                    strat = f"Dòng Tiền Bạo Phát ({mf_val:.1f}x)" if mf_val > 0 else "Dòng Tiền Bạo Phát"
            else:
                if engine == "DC2":
                    strat = "Bắt Đáy - Siêu Pullback"
                elif engine == "DC3":
                    strat = "Breakout"
                elif engine == "DC4":
                    strat = "Hot Trend"
                else:
                    strat = "Spot Swing"

            clean_orders.append({
                "symbol": f"{clean_sym}/USDT",
                "order_type": "SPOT_OCO",
                "entry": entry,
                "raw_sl": raw_sl,
                "raw_tp": raw_tp,
                "uy_tin_score": score,
                "tier_label": tier,
                "engine": engine,
                "engine_label": ENGINE_LABELS.get(engine, engine),
                "strategy_desc": strat,
                "money_flow": mf_val,
                "is_money_flow_surge": (mf_val >= 3.0 or is_top_mf),
                "grid_tp2_line": getattr(s, "grid_tp2_line", "") or "",
                "macro_risk": btc_locked,
                "macro_risk_reason": "BTC gãy MA25 1H - Fakeout Risk" if btc_locked else "",
                "penalty_tags": p_info.get("penalty_tags", []),
                "warning_level": int(getattr(s, "warning_level", 0) or p_info.get("warning_level", 0) or 0),
                "warning_label": str(getattr(s, "warning_label", "") or p_info.get("warning_label", "") or ""),
                "downgrade_level": str(getattr(s, "downgrade_level", "") or p_info.get("downgrade_level", "") or ""),
                "rsi_1h": float(getattr(s, "rsi_1h", 0.0) or p_info.get("rsi_1h", 0.0) or 0.0),
                "gate1_score": float(getattr(s, "gate1_score", 0.0) or p_info.get("breakdown", {}).get("t0_components", {}).get("3d_strict", 30.0) or 30.0),
                "lost_ma7_3d": bool(getattr(s, "lost_ma7_3d", False) or p_info.get("lost_ma7_3d", False)),
                "is_ne_grid": bool(getattr(s, "is_ne_grid", False) or any("Né Grid" in str(x) for x in p_info.get("penalty_tags", []))),
                "leverage": int(getattr(s, "leverage", getattr(s, "lev", 1)) or 1),
                "market_type": getattr(s, "market_type", "SPOT")
            })
            seen_spot_syms.add(clean_sym)

    # 3. Xử lý Lệnh GRID (GRID TP2 & WIDE GRID 3D)
    seen_grid_syms = set()
    grid_sources = [
        ("grid_tp2", summary_board_res.get("grid_tp2", []) or [], "GRID TÍCH LŨY"),
        ("wide_grid", summary_board_res.get("wide_grid", []) or [], "WIDE GRID 3D")
    ]

    # Bản đồ OCO Setup từ Bảng Tổng Kết (để làm OCO Tham Chiếu cho lệnh GRID)
    spot_setup_map = {}
    for sp in (summary_board_res.get("spot", []) or []) + (summary_board_res.get("top_money_flow", []) or []):
        sym_k = getattr(sp, "symbol", "").upper().replace("/USDT", "").replace("USDT", "").strip()
        s_line = getattr(sp, "setup_line", "")
        if sym_k and s_line and sym_k not in spot_setup_map:
            spot_setup_map[sym_k] = s_line

    for source_key, signals, default_strat in grid_sources:
        for s in signals:
            sym_raw = getattr(s, "symbol", "")
            clean_sym = sym_raw.upper().replace("/USDT", "").replace("USDT", "").strip()
            if clean_sym in seen_grid_syms:
                continue
            
            p_info = trust_map.get(clean_sym, {})
            score = float(p_info.get("total_score", getattr(s, "target_score", 0.0) or getattr(s, "rebalance_score", 0.0) or 50.0))
            tier = str(p_info.get("tier", ""))

            grid_line = getattr(s, "grid_tp2_line", "") if source_key == "grid_tp2" else getattr(s, "wide_grid_line", "")
            setup_line = getattr(s, "setup_line", "") or spot_setup_map.get(clean_sym, "")
            m_grid = _RE_GRID.search(grid_line)

            if m_grid:
                lower = float(m_grid.group(1))
                upper = float(m_grid.group(2))
                grids = int(m_grid.group(3))
                engine = getattr(s, "engine_source", "") or ("DC1" if default_strat == "WIDE GRID 3D" else "DC1")
                dec = _count_decimals(lower)

                # 1. Trích xuất Trigger / Entry theo phân luồng Gatekeeper
                m_trig = _RE_ENTRY.search(grid_line) or _RE_ENTRY.search(setup_line)
                raw_trig_val = float(m_trig.group(1)) if m_trig else round((lower + upper) / 2.0, dec)

                is_solid_grid = any(t in tier for t in ["Tier S", "Tier A", "Tier B"]) and not any(t in tier for t in ["Tier C", "Tier F"])
                p_tags = p_info.get("penalty_tags", []) or []
                is_ne_grid_coin = bool(getattr(s, "is_ne_grid", False) or any("Né Grid" in str(x) or "Ne Grid" in str(x) for x in p_tags))

                macro_entry = None
                macro_obj = getattr(s, "macro_levels", None) or p_info.get("macro_levels")
                if macro_obj and getattr(macro_obj, "entry_4h", None):
                    try:
                        macro_entry = float(macro_obj.entry_4h)
                    except (ValueError, TypeError):
                        macro_entry = None

                if is_solid_grid and not is_ne_grid_coin and macro_entry and macro_entry > 0:
                    trigger_val = round(macro_entry, dec)
                else:
                    trigger_val = raw_trig_val

                # 2. Trích xuất Grid SL & TP
                m_sl_grid = _RE_SL.search(grid_line)
                grid_sl_val = float(m_sl_grid.group(1)) if m_sl_grid else round(lower * 0.95, dec)

                m_tp_grid = _RE_TP.search(grid_line)
                grid_tp_val = float(m_tp_grid.group(1)) if m_tp_grid else upper

                # 3. Trích xuất OCO Tham Chiếu (Gốc)
                m_sl_oco = _RE_SL.search(setup_line)
                m_tp_oco = _RE_TP.search(setup_line)
                if m_sl_oco and m_tp_oco:
                    raw_sl_val = float(m_sl_oco.group(1))
                    raw_tp_val = float(m_tp_oco.group(1))
                else:
                    raw_sl_val = round(trigger_val * 0.995, dec)
                    raw_tp_val = round(trigger_val * 1.033, dec)

                # Trích xuất hệ số dòng tiền bạo phát cho GRID (nếu có)
                mf_val = float(getattr(s, "money_flow", 0.0) or 0.0)
                if mf_val <= 0.0 and (setup_line or grid_line):
                    m_mf = _RE_MONEY_FLOW.search(setup_line) or _RE_MONEY_FLOW.search(grid_line)
                    if m_mf:
                        try:
                            mf_val = float(m_mf.group(1))
                        except Exception:
                            pass

                # Trích xuất đòn bẩy nếu có cấu hình
                m_lev = re.search(r"(\d+)\s*x", f"{grid_line} {setup_line} {default_strat}", re.IGNORECASE)
                grid_lev = int(m_lev.group(1)) if m_lev else int(getattr(s, "leverage", getattr(s, "lev", 1)) or 1)
                is_fut = (getattr(s, "market_type", "") == "FUTURES") or ("FUTURES" in str(default_strat).upper()) or (grid_lev > 1)
                mkt_type = "FUTURES" if is_fut else "SPOT"

                clean_orders.append({
                    "symbol": f"{clean_sym}/USDT",
                    "order_type": "GRID",
                    "lower": lower,
                    "upper": upper,
                    "raw_lower": lower,
                    "raw_upper": upper,
                    "grids": grids,
                    "raw_grids": grids,
                    "entry": trigger_val,
                    "trigger": trigger_val,
                    "sl": grid_sl_val,
                    "tp": grid_tp_val,
                    "raw_sl": raw_sl_val,
                    "raw_tp": raw_tp_val,
                    "uy_tin_score": score,
                    "tier_label": tier,
                    "engine": engine,
                    "engine_label": ENGINE_LABELS.get(engine, engine),
                    "strategy_desc": default_strat,
                    "money_flow": mf_val,
                    "is_money_flow_surge": (mf_val >= 3.0),
                    "penalty_tags": p_info.get("penalty_tags", []),
                    "warning_level": int(getattr(s, "warning_level", 0) or p_info.get("warning_level", 0) or 0),
                    "warning_label": str(getattr(s, "warning_label", "") or p_info.get("warning_label", "") or ""),
                    "downgrade_level": str(getattr(s, "downgrade_level", "") or p_info.get("downgrade_level", "") or ""),
                    "rsi_1h": float(getattr(s, "rsi_1h", 0.0) or p_info.get("rsi_1h", 0.0) or 0.0),
                    "gate1_score": float(getattr(s, "gate1_score", 0.0) or p_info.get("breakdown", {}).get("t0_components", {}).get("3d_strict", 30.0) or 30.0),
                    "lost_ma7_3d": bool(getattr(s, "lost_ma7_3d", False) or p_info.get("lost_ma7_3d", False)),
                    "is_ne_grid": bool(getattr(s, "is_ne_grid", False) or any("Né Grid" in str(x) for x in p_info.get("penalty_tags", []))),
                    "leverage": grid_lev,
                    "market_type": mkt_type
                })
                seen_grid_syms.add(clean_sym)

    # 4. Xử lý Lệnh GRID GOM HÀNG (Pre-Breakout Radar / Grid Gom Hàng Chuẩn Hình Học)
    if grid_gom_hang_items:
        for b_item in grid_gom_hang_items:
            b_sym = str(b_item.get("symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
            if not b_sym or b_sym in seen_grid_syms:
                continue

            b_trig = float(b_item.get("ref_buy", 0.0) or 0.0)
            if b_trig <= 0:
                continue

            dec = _count_decimals(b_trig)
            b_low = round(b_trig * 0.90, dec)
            b_up = round(b_trig * 1.20, dec)
            b_sl = round(b_trig * 0.88, dec)

            p_info = trust_map.get(b_sym, {})
            score = float(b_item.get("decay_score", b_item.get("total_score", p_info.get("total_score", 50.0))))
            tier = str(b_item.get("tier_clean", b_item.get("tier", p_info.get("tier", ""))))

            is_entry_4h = bool(b_item.get("is_entry_4h_chosen", False))
            p_tags = b_item.get("penalty_tags", []) or p_info.get("penalty_tags", []) or []
            is_ne = bool(
                b_item.get("is_ne_grid", False)
                or p_info.get("is_ne_grid", False)
                or any("Né Grid" in str(x) or "Ne Grid" in str(x) for x in p_tags)
            )

            clean_orders.append({
                "symbol": f"{b_sym}/USDT",
                "order_type": "GRID",
                "is_grid_gom_hang": True,
                "entry": b_trig,
                "trigger": b_trig,
                "lower": b_low,
                "upper": b_up,
                "raw_lower": b_low,
                "raw_upper": b_up,
                "grids": 24,
                "raw_grids": 24,
                "sl": b_sl,
                "raw_sl": b_sl,
                "tp": b_up,
                "raw_tp": b_up,
                "uy_tin_score": score,
                "tier_label": tier,
                "engine": "GRID_GOM_HANG",
                "engine_label": "Grid Gom Hàng",
                "strategy_desc": "Grid Gom Hàng Chuẩn Hình Học (+8%/-12%)",
                "money_flow": float(b_item.get("money_flow", 0.0) or 0.0),
                "macro_risk": btc_locked,
                "macro_risk_reason": "BTC gãy MA25 1H - Fakeout Risk" if btc_locked else "",
                "penalty_tags": p_tags,
                "warning_level": int(b_item.get("warning_level", 0) or p_info.get("warning_level", 0) or 0),
                "warning_label": str(b_item.get("warning_label", "") or p_info.get("warning_label", "") or ""),
                "downgrade_level": str(b_item.get("downgrade_level", "") or p_info.get("downgrade_level", "") or ""),
                "rsi_1h": float(b_item.get("rsi_1h", 0.0) or p_info.get("rsi_1h", 0.0) or 0.0),
                "gate1_score": float(b_item.get("gate1_score", 30.0) or 30.0),
                "lost_ma7_3d": bool(b_item.get("lost_ma7_3d", False) or p_info.get("lost_ma7_3d", False)),
                "is_ne_grid": is_ne,
                "is_entry_4h_chosen": is_entry_4h,
                "macro_levels": b_item.get("macro_levels"),
                "leverage": 1,
                "market_type": "SPOT"
            })
            seen_grid_syms.add(b_sym)

    return clean_orders
