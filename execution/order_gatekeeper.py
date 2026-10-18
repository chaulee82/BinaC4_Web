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
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("OrderGatekeeper")


def _count_decimals(val: float, max_dec: int = 5) -> int:
    """Đếm số chữ số thập phân thực tế của một giá trị float."""
    s = f"{val:.8f}".rstrip('0')
    if '.' in s:
        d = len(s.split('.')[1])
        return min(max(d, 2), max_dec)
    return 2


def _fmt_price_val(val: float, decimals: Optional[int] = None) -> str:
    """Format giá gọn đẹp, khớp số chữ số thập phân chuẩn."""
    if val is None:
        return "0"
    if decimals is not None:
        return f"{val:.{decimals}f}"
    s = f"{val:.5f}".rstrip('0').rstrip('.')
    if '.' in s:
        dec_len = len(s.split('.')[1])
        if dec_len < 2:
            s = f"{val:.2f}"
    return s


ENGINE_LABELS = {
    "DC1": "DC1 (Chân Sóng Vĩ Mô)",
    "DC2": "DC2 (Bắt Đáy)",
    "DC3": "DC3 (Bứt Phá - Breakout)",
    "DC4": "DC4 (Đu Trend - Hot Trend)",
    "DC5": "DC5 (Nén Vol Chờ Nổ)",
}


def optimize_grid_tp2_geometry(
    order: Dict[str, Any],
    entry: float,
    safe_sl: float,
    realistic_tp: float
) -> Dict[str, Any]:
    """
    Thuật Toán Tinh Chỉnh GRID TP2 (Grid TP2 Geometry Optimization):
    1. Bóp Đáy Lưới (Raise Lower Limit): Cắt bỏ 15% biên độ dưới cùng của hộp vĩ mô.
    2. Khóa Đỉnh Lưới (Cap Upper Limit): Ép không vượt quá Realistic TP (R:R 1:2) của lệnh OCO.
    3. Chuẩn Hóa Mật Độ: Cố định 24 Lưới để bào Grid Profit tối đa.
    """
    clean_sym = order.get('symbol', '').upper().replace("/USDT", "").replace("USDT", "").strip()
    
    # Lấy thông số macro thô ban đầu
    raw_lower = None
    raw_upper = None
    
    # 1. Từ grid_tp2_line
    line = str(order.get("grid_tp2_line") or "").strip()
    if line:
        m = re.search(r"([\d.]+)\s*-\s*([\d.]+)", line)
        if m:
            raw_lower = float(m.group(1))
            raw_upper = float(m.group(2))
            
    # 2. Từ payload dict
    if raw_lower is None or raw_upper is None or raw_lower <= 0 or raw_upper <= raw_lower:
        payload = order.get("grid_tp2_payload") or order.get("grid_tp2")
        if isinstance(payload, dict):
            try:
                raw_lower = float(payload.get("lower", payload.get("grid_low", 0.0)) or 0.0)
                raw_upper = float(payload.get("upper", payload.get("grid_up", 0.0)) or 0.0)
            except (ValueError, TypeError):
                raw_lower = None
                raw_upper = None
            
    # 3. Từ build_macro_grid_payload
    if raw_lower is None or raw_upper is None or raw_lower <= 0 or raw_upper <= raw_lower:
        try:
            from core.grid_tp2_builder import build_macro_grid_payload
            calc_tp = float(order.get("raw_tp") or realistic_tp)
            p = build_macro_grid_payload(symbol=clean_sym, entry=entry, sl_short=safe_sl, tp_target=calc_tp)
            if p and p.get("lower") and p.get("upper"):
                raw_lower = float(p['lower'])
                raw_upper = float(p['upper'])
        except Exception:
            pass
            
    # 4. Fallback nếu vẫn chưa có
    if raw_lower is None or raw_upper is None or raw_lower <= 0 or raw_upper <= raw_lower:
        raw_lower = safe_sl * 0.98
        raw_upper = float(order.get("raw_tp") or realistic_tp)

    macro_range = raw_upper - raw_lower
    
    # BƯỚC 1: Bóp Đáy Lưới (Raise Lower Limit +15%)
    opt_lower = raw_lower + (macro_range * 0.15)
    
    # BƯỚC 2: Khóa Đỉnh Lưới (Cap Upper Limit <= Realistic TP)
    opt_upper = min(raw_upper, realistic_tp)
    
    # Bảo vệ hình học: opt_lower < entry < opt_upper
    if opt_lower >= entry:
        opt_lower = min(safe_sl, entry * 0.985)
    if opt_upper <= entry:
        opt_upper = max(realistic_tp, entry * 1.015)
    if opt_lower >= opt_upper:
        opt_lower = entry * 0.985
        opt_upper = entry * 1.02
        
    grid_sl = min(safe_sl * 0.98, opt_lower * 0.98)
    
    return {
        "lower": round(opt_lower, 5),
        "upper": round(opt_upper, 5),
        "grids": 24,
        "trigger": round(entry, 5),
        "sl": round(grid_sl, 5),
        "tp": round(opt_upper, 5),
        "raw_lower": round(raw_lower, 5),
        "raw_upper": round(raw_upper, 5),
        "geometry_note": "Bóp đáy 15%, Khóa đỉnh TP 1:2, Chuẩn hóa 24 Lưới"
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



def optimized_execution_geometry_module(clean_summary_orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Module Tinh Chỉnh Hình Học Lệnh & Phân Luồng Thực Thi.
    Input: Danh sách lệnh ĐÃ ĐƯỢC LỌC SẠCH RỦI RO từ Tầng 2.
    """
    final_execution_orders = []
    
    for order in clean_summary_orders:
        # Tạo bản sao shallow dict để không làm hỏng dữ liệu gốc
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
            
            # Lưu giữ thông số gốc để in bảng đối chiếu
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
                
            # 2. Realistic TP: Ép R:R tối đa về 1:2.0
            risk_amount = entry - safe_sl
            realistic_tp = min(raw_tp, entry + (risk_amount * 2.0))
            tp_adjusted = (realistic_tp < raw_tp - 1e-9)

            # 3. Nắn Bóp Hình Học GRID TP2 Dự Phòng (Grid TP2 Geometry Optimization)
            grid_tp2_opt = optimize_grid_tp2_geometry(order_copy, entry, safe_sl, realistic_tp)
            
            # Cập nhật thông số
            order_copy.update({
                'entry': entry,
                'sl': round(safe_sl, 5),
                'tp': round(realistic_tp, 5),
                'sl_adjusted': sl_adjusted,
                'tp_adjusted': tp_adjusted,
                'auto_breakeven': True,
                'geometry_note': "Nới đệm SL (+1.5%), Bóp TP (Max R:R 1:2)",
                'grid_tp2': grid_tp2_opt,
                'grid_tp2_optimized': grid_tp2_opt
            })

            
        elif order_type == 'GRID':
            macro_lower = float(order_copy['lower'])
            macro_upper = float(order_copy['upper'])
            macro_range = macro_upper - macro_lower
            raw_grids = int(order_copy.get('grids', 24))
            
            order_copy['raw_lower'] = macro_lower
            order_copy['raw_upper'] = macro_upper
            order_copy['raw_grids'] = raw_grids
            
            # Bóp 15% biên độ thừa ở mỗi đầu, chuẩn hóa 24 lưới
            order_copy.update({
                'lower': round(macro_lower + (macro_range * 0.15), 5),
                'upper': round(macro_upper - (macro_range * 0.15), 5),
                'grids': 24,
                'geometry_note': "Bóp biên độ 15% hai đầu, chuẩn hóa 24 Lưới"
            })

        if order_copy.get('engine') in ENGINE_LABELS:
            order_copy['engine'] = ENGINE_LABELS[order_copy['engine']]

        # =========================================================
        # TIẾN TRÌNH 2: PHÂN LUỒNG KÍCH HOẠT (ROUTING)
        # =========================================================
        if uy_tin >= 60:
            order_copy['status'] = 'AUTO_EXECUTE'
            order_copy['volume_pct'] = 1.0  # 100% Vol
        else:
            # Rơi vào trường hợp Tier B (40-59đ) lọt cửa hậu Siêu Pullback
            order_copy['status'] = 'MANUAL_APPROVAL'
            order_copy['volume_pct'] = 0.3  # Đề xuất 30% Vol thăm dò

        final_execution_orders.append(order_copy)
        
    return final_execution_orders


def render_gatekeeper_board(final_orders):
    print("=" * 100)
    print("🛡️ [MODULE BẢNG DUYỆT LỆNH TỐI ƯU] HÀNG ĐỢI THỰC THI (GIAI ĐOẠN 1)")
    print("=" * 100)
    print("💡 Bộ lọc hình học: Đệm SL chống quét râu (1.5%), Max R:R 1:2.0, Bóp lưới Grid 15% rìa.\n")

    auto_exec = [o for o in final_orders if o['status'] == 'AUTO_EXECUTE']
    manual_exec = [o for o in final_orders if o['status'] == 'MANUAL_APPROVAL']

    def print_order_block(orders, title, desc):
        if not orders: return
        print(f"🟢 {title} | {desc}" if "AUTO" in title else f"🟡 {title} | {desc}")
        print("-" * 100)
        for idx, o in enumerate(orders, 1):
            engine_name = o.get('engine', 'UNKNOWN_ENGINE')
            
            if o['order_type'] == 'SPOT_OCO':
                entry = o['entry']
                raw_sl_pct = ((o['raw_sl'] - entry) / entry) * 100
                raw_tp_pct = ((o['raw_tp'] - entry) / entry) * 100
                new_sl_pct = ((o['sl'] - entry) / entry) * 100
                new_tp_pct = ((o['tp'] - entry) / entry) * 100
                
                print(f"[{idx}] Mã: {o['symbol']} | Động cơ: {engine_name} | Loại: OCO | Cấp vốn: {int(o['volume_pct']*100)}% (Uy tín: {o['uy_tin_score']}đ)")
                print(f"    ↳ ⚙️ OCO GỐC: Entry={entry} | SL={o['raw_sl']} ({raw_sl_pct:+.1f}%) | TP={o['raw_tp']} ({raw_tp_pct:+.1f}%)")
                print(f"    ↳ 🛠️ OCO ĐÃ NẮN: Entry={entry} | SL={o['sl']} ({new_sl_pct:+.1f}%) [Nới đệm] | TP={o['tp']} ({new_tp_pct:+.1f}%) [Kéo R:R 1:2]")
                
                if 'grid_tp2' in o:
                    g = o['grid_tp2']
                    r_low = g.get('raw_lower', g.get('lower'))
                    r_up = g.get('raw_upper', g.get('upper'))
                    print(f"    ↳ 🎯 GRID TP2 (GỐC): Dải L={r_low} - U={r_up} | 24 Lưới")
                    print(f"    ↳ 🛠️ GRID TP2 (ĐÃ NẮN): Dải L={g.get('lower')} - U={g.get('upper')} | 24 Lưới | Trig: {entry} | SL: {o['sl']} | TP: {g.get('upper')}")
                
                if o.get('auto_breakeven'):
                    print(f"    ↳ 🛡️ Option: Kích hoạt Auto-Breakeven (+1%).")
                    
            elif o['order_type'] == 'GRID':
                trigger = o.get('entry', o.get('trigger', 'N/A'))
                sl = o.get('sl', o.get('raw_sl', 'N/A'))
                tp = o.get('tp', o.get('raw_upper', o['upper']))
                
                print(f"[{idx}] Mã: {o['symbol']} | Động cơ: {engine_name} | Loại: GRID TÍCH LŨY | Cấp vốn: {int(o['volume_pct']*100)}% (Uy tín: {o['uy_tin_score']}đ)")
                
                if 'raw_sl' in o and 'raw_tp' in o and trigger != 'N/A':
                    raw_sl_pct = ((o['raw_sl'] - trigger) / trigger) * 100
                    raw_tp_pct = ((o['raw_tp'] - trigger) / trigger) * 100
                    print(f"    ↳ 🎯 OCO THAM CHIẾU (GỐC): Entry={trigger} | SL={o['raw_sl']} ({raw_sl_pct:+.1f}%) | TP={o['raw_tp']} ({raw_tp_pct:+.1f}%)")
                
                print(f"    ↳ ⚙️ GRID GỐC: Lưới Macro (L={o.get('raw_lower', o['lower'])} - U={o.get('raw_upper', o['upper'])}) | {o.get('raw_grids', 35)} Lưới")
                print(f"    ↳ 🛠️ GRID ĐÃ NẮN: Bóp 15% biên độ (L={o['lower']} - U={o['upper']}) | {o['grids']} Lưới | Trig: {trigger} | SL: {sl} | TP: {tp}")
                print(f"    ↳ 🛡️ Option: Tăng tần suất khớp lệnh vùng tích lũy 72H.")
            
            if o['status'] == 'MANUAL_APPROVAL':
                print(f"    ↳ ⚠️ Trạng thái: Treo chờ lệnh. Nhấn [Y] để đẩy lệnh lên sàn.")
            print("")

    print_order_block(auto_exec, "LỆNH DUYỆT TỰ ĐỘNG (AUTO-EXECUTE)", "Điều kiện: Tier S/A (Uy tín >= 60đ)")
    print_order_block(manual_exec, "LỆNH CHỜ DUYỆT TAY (MANUAL APPROVAL)", "Điều kiện: Tier B (Cửa hậu - Rủi ro vĩ mô)")
    print("=" * 100)



# ── ADAPTER CHUYỂN ĐỔI DỮ LIỆU TỪ TẦNG 2 ──────────────────────────────────────

_RE_ENTRY = re.compile(r"(?:In|Entry|Buy|Trig)\s*[:=]\s*([\d.]+)")
_RE_SL    = re.compile(r"SL\s*[:=]\s*([\d.]+)")
_RE_TP    = re.compile(r"TP1?\s*[:=]\s*([\d.]+)")
_RE_GRID  = re.compile(r"([\d.]+)\s*-\s*([\d.]+)\s*\|\s*(\d+)\s*(?:L|Lưới)")


def extract_clean_summary_orders(
    summary_board_res: Dict[str, Any],
    v2_profiles: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Trích xuất danh sách lệnh sạch từ Tầng 2 (Bảng Tổng Kết & Hồ Sơ Tín Nhiệm).
    Tầng 2 đã lọc 100% rủi ro vĩ mô (chốt chặn sinh tử, BTC Gate, Blacklist...).
    """
    clean_orders: List[Dict[str, Any]] = []
    if not summary_board_res:
        return clean_orders

    # 1. Bản đồ Hồ sơ tín nhiệm (Uy tín & Tier)
    trust_map: Dict[str, Dict[str, Any]] = {}
    for p in (v2_profiles or []):
        sym = str(p.get("symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
        if sym:
            trust_map[sym] = p

    # 2. Xử lý Lệnh SPOT (Lướt sóng OCO)
    # Nếu BTC Gate khóa -> Tầng 2 đã dán nhãn spot_btc_locked = True
    btc_locked = bool(summary_board_res.get("spot_btc_locked", False))
    spot_signals = summary_board_res.get("spot", []) or []

    if not btc_locked:
        for s in spot_signals:
            sym_raw = getattr(s, "symbol", "")
            clean_sym = sym_raw.upper().replace("/USDT", "").replace("USDT", "").strip()
            
            # Đọc điểm uy tín từ hồ sơ 14 ngày hoặc từ target_score
            p_info = trust_map.get(clean_sym, {})
            score = float(p_info.get("total_score", getattr(s, "target_score", 0.0) or 50.0))
            tier = str(p_info.get("tier", ""))

            # Phân tích chuỗi setup
            line = getattr(s, "setup_line", "") or ""
            m_entry = _RE_ENTRY.search(line)
            m_sl = _RE_SL.search(line)
            m_tp = _RE_TP.search(line)

            if m_entry and m_sl:
                entry = float(m_entry.group(1))
                raw_sl = float(m_sl.group(1))
                if m_tp:
                    raw_tp = float(m_tp.group(1))
                else:
                    rr = float(getattr(s, "rr_ratio", 2.0) or 2.0)
                    raw_tp = entry + (entry - raw_sl) * rr

                engine = getattr(s, "engine_source", "")
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
                    "grid_tp2_line": getattr(s, "grid_tp2_line", "") or ""
                })

    # 3. Xử lý Lệnh GRID (GRID TP2 & WIDE GRID 3D)
    seen_grid_syms = set()
    grid_sources = [
        ("grid_tp2", summary_board_res.get("grid_tp2", []) or [], "GRID TÍCH LŨY"),
        ("wide_grid", summary_board_res.get("wide_grid", []) or [], "WIDE GRID 3D")
    ]

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
            m_grid = _RE_GRID.search(grid_line)

            if m_grid:
                lower = float(m_grid.group(1))
                upper = float(m_grid.group(2))
                grids = int(m_grid.group(3))
                engine = getattr(s, "engine_source", "") or ("DC1" if default_strat == "WIDE GRID 3D" else "DC1")

                clean_orders.append({
                    "symbol": f"{clean_sym}/USDT",
                    "order_type": "GRID",
                    "lower": lower,
                    "upper": upper,
                    "grids": grids,
                    "uy_tin_score": score,
                    "tier_label": tier,
                    "engine": engine,
                    "engine_label": ENGINE_LABELS.get(engine, engine),
                    "strategy_desc": default_strat
                })
                seen_grid_syms.add(clean_sym)

    return clean_orders
