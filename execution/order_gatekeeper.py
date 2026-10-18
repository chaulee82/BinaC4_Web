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
from typing import List, Dict, Any, Optional, Tuple

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
            
            # Tính R:R sau nắn
            reward_amount = realistic_tp - entry
            rr_val = round(reward_amount / risk_amount, 2) if risk_amount > 0 else 2.0

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
                'grid_tp2_optimized': grid_tp2_opt,
                'rr_ratio': rr_val,
                'money_flow': float(order_copy.get('money_flow', 0.0) or 0.0)
            })

            
        elif order_type == 'GRID':
            macro_lower = float(order_copy['lower'])
            macro_upper = float(order_copy['upper'])
            macro_range = macro_upper - macro_lower
            raw_grids = int(order_copy.get('raw_grids', order_copy.get('grids', 24)))
            dec = _count_decimals(macro_lower)
            
            order_copy['raw_lower'] = macro_lower
            order_copy['raw_upper'] = macro_upper
            order_copy['raw_grids'] = raw_grids
            
            # Bóp 15% biên độ thừa ở mỗi đầu, chuẩn hóa 24 lưới
            opt_lower = round(macro_lower + (macro_range * 0.15), 5)
            opt_upper = round(macro_upper - (macro_range * 0.15), 5)

            trigger = order_copy.get('entry', order_copy.get('trigger'))
            if trigger is None or trigger == 'N/A':
                trigger = round((macro_lower + macro_upper) / 2.0, dec)
            else:
                trigger = float(trigger)
                
            grid_sl = order_copy.get('sl')
            if grid_sl is None or grid_sl == 'N/A':
                grid_sl = round(macro_lower * 0.95, dec)
            else:
                grid_sl = float(grid_sl)
                
            grid_tp = order_copy.get('tp')
            if grid_tp is None or grid_tp == 'N/A':
                grid_tp = macro_upper
            else:
                grid_tp = float(grid_tp)
                
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
            grid_rr = round(grid_reward / grid_risk, 2) if grid_risk > 0 else 1.5
            
            order_copy.update({
                'lower': opt_lower,
                'upper': opt_upper,
                'grids': 24,
                'entry': trigger,
                'trigger': trigger,
                'sl': grid_sl,
                'tp': grid_tp,
                'raw_sl': raw_sl,
                'raw_tp': raw_tp,
                'geometry_note': "Bóp biên độ 15% hai đầu, chuẩn hóa 24 Lưới",
                'rr_ratio': grid_rr,
                'money_flow': float(order_copy.get('money_flow', 0.0) or 0.0)
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


def _order_sort_key(o: Dict[str, Any]) -> tuple:
    """
    Khóa sắp xếp đa tầng ưu tiên trong Hàng Đợi Thực Thi:
    1. Điểm Tín Nhiệm 14 ngày (uy_tin_score) DESC
    2. Hệ số Dòng Tiền Bạo Phát (money_flow) DESC
    3. Tỷ lệ R:R sau nắn hình học DESC
    """
    uy_tin = float(o.get('uy_tin_score', 0.0) or 0.0)
    mf = float(o.get('money_flow', 0.0) or 0.0)
    rr = float(o.get('rr_ratio', 0.0) or 0.0)
    return (uy_tin, mf, rr)


def render_gatekeeper_board(final_orders):
    print("=" * 100)
    print("🛡️ [MODULE BẢNG DUYỆT LỆNH TỐI ƯU] HÀNG ĐỢI THỰC THI (GIAI ĐOẠN 1)")
    print("=" * 100)
    print("💡 Bộ lọc hình học: Đệm SL chống quét râu (1.5%), Max R:R 1:2.0, Bóp lưới Grid 15% rìa.")
    print("🎯 Thứ tự ưu tiên: 1. Uy tín 14 ngày (Tier S > A > B) ➔ 2. Dòng tiền bạo phát ➔ 3. R:R tối ưu.\n")

    auto_exec = sorted([o for o in final_orders if o['status'] == 'AUTO_EXECUTE'], key=_order_sort_key, reverse=True)
    manual_exec = sorted([o for o in final_orders if o['status'] == 'MANUAL_APPROVAL'], key=_order_sort_key, reverse=True)

    def print_order_block(orders, title, desc):
        if not orders: return
        print(f"🟢 {title} | {desc}" if "AUTO" in title else f"🟡 {title} | {desc}")
        print("-" * 100)

        # Đếm số lượng lệnh của mỗi mã để phát hiện mã có cả OCO và GRID (như AVA)
        sym_list = [o.get('symbol') for o in orders]
        dual_symbols = {s for s in sym_list if sym_list.count(s) > 1}

        for idx, o in enumerate(orders, 1):
            engine_name = o.get('engine', 'UNKNOWN_ENGINE')
            mf = float(o.get('money_flow', 0.0) or 0.0)
            mf_str = f" [💥 BẠO PHÁT {mf:.1f}x]" if mf > 0 else ""
            tier_label = o.get('tier_label', '')
            tier_str = f" - {tier_label}" if tier_label else ""
            sym = o.get('symbol')
            has_dual = sym in dual_symbols
            order_type = o.get('order_type', 'SPOT_OCO')
            
            if order_type == 'SPOT_OCO':
                entry = o['entry']
                sl_val = o['sl']
                tp_val = o['tp']
                sl_pct = ((sl_val - entry) / entry) * 100
                tp_pct = ((tp_val - entry) / entry) * 100
                rr_val = o.get('rr_ratio', 2.0)
                
                type_tag = "🎯 [LỰA CHỌN 1: SPOT OCO]" if has_dual else "🎯 [LỆNH SPOT OCO]"
                print(f"[{idx}] {type_tag} Mã: {o['symbol']} | Động cơ: {engine_name}{mf_str} | Cấp vốn: {int(o['volume_pct']*100)}% (Uy tín: {o['uy_tin_score']}đ{tier_str})")
                print(f"    ↳ 🎯 LỆNH OCO: Entry={entry} | SL={sl_val} ({sl_pct:+.1f}%) | TP={tp_val} ({tp_pct:+.1f}%) | R:R 1:{rr_val:.1f}")
                
                if 'grid_tp2' in o:
                    g = o['grid_tp2']
                    opt_note = " | Kèm Auto-Breakeven (+1%)" if o.get('auto_breakeven') else ""
                    print(f"    ↳ 🛡️ LƯỚI TP2: Dải L={g.get('lower')} - U={g.get('upper')} (24 Lưới){opt_note}")
                elif o.get('auto_breakeven'):
                    print(f"    ↳ 🛡️ Option: Kích hoạt Auto-Breakeven (+1%).")
                    
            elif order_type == 'GRID':
                trigger = o.get('entry', o.get('trigger', 'N/A'))
                sl = o.get('sl', 'N/A')
                tp = o.get('tp', o.get('upper', 'N/A'))
                strat_name = o.get('strategy_desc', 'GRID TÍCH LŨY')
                
                type_tag = "🥅 [LỰA CHỌN 2: GRID LƯỚI]" if has_dual else f"🥅 [{strat_name}]"
                print(f"[{idx}] {type_tag} Mã: {o['symbol']} | Động cơ: {engine_name}{mf_str} | Cấp vốn: {int(o['volume_pct']*100)}% (Uy tín: {o['uy_tin_score']}đ{tier_str})")
                print(f"    ↳ 🥅 LỆNH GRID: Dải L={o['lower']} - U={o['upper']} (24 Lưới) | Trig: {trigger} | SL: {sl} | TP: {tp}")
                print(f"    ↳ 🛡️ Option: Bóp 15% biên độ, tăng tần suất khớp lệnh vùng tích lũy 72H.")
            
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
_RE_MONEY_FLOW = re.compile(r"(?:TIỀN BẠO PHÁT|BẠO PHÁT)\s*\((\d+(?:\.\d+)?)x\)")


def extract_clean_summary_orders(
    summary_board_res: Dict[str, Any],
    v2_profiles: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Trích xuất danh sách lệnh sạch từ Tầng 2 (Bảng Tổng Kết & Hồ Sơ Tín Nhiệm).
    Tầng 2 đã lọc 100% rủi ro vĩ mô (chốt chặn sinh tử, BTC Gate, Blacklist...).
    Bổ sung nhóm Dòng Tiền Bạo Phát (top_money_flow) đã qua chốt kiểm duyệt bóp cò.
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

    # 2. Xử lý Lệnh SPOT (Lướt sóng OCO & Top Dòng Tiền Bạo Phát)
    # Nếu BTC Gate khóa -> Tầng 2 đã dán nhãn spot_btc_locked = True
    btc_locked = bool(summary_board_res.get("spot_btc_locked", False))
    spot_signals = summary_board_res.get("spot", []) or []
    top_mf_signals = summary_board_res.get("top_money_flow", []) or []

    if not btc_locked:
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
                    "grid_tp2_line": getattr(s, "grid_tp2_line", "") or ""
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

                # 1. Trích xuất Trigger / Entry
                m_trig = _RE_ENTRY.search(grid_line) or _RE_ENTRY.search(setup_line)
                if m_trig:
                    trigger_val = float(m_trig.group(1))
                else:
                    trigger_val = round((lower + upper) / 2.0, dec)

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
                    "is_money_flow_surge": (mf_val >= 3.0)
                })
                seen_grid_syms.add(clean_sym)

    return clean_orders
