"""
Module: trust_history.py
Mục đích: Lưu trữ lịch sử Trust Score V2.0 và phân tích biến thiên Delta Audit theo ngày (T0, T1, T3).
Đường dẫn: /root/BinaC4_Web/trust_history.py (hoặc project root trên môi trường local)
Mã phân hệ: MOD-TRUST-HIST-01
"""

import os
import sys
import sqlite3
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Cấu hình đường dẫn DB: ưu tiên biến môi trường, sau đó đến đường dẫn VPS chuẩn /root/BinaC4_Web/trust_score_history.db (nếu thư mục tồn tại), hoặc thư mục gốc ứng dụng
DEFAULT_DB_DIR = "/root/BinaC4_Web" if os.path.exists("/root/BinaC4_Web") else os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("TRUST_HIST_DB", os.path.join(DEFAULT_DB_DIR, "trust_score_history.db"))


def init_db(db_path: Optional[str] = None) -> None:
    """Tạo bảng và các chỉ mục nếu chưa tồn tại (WAL journal mode)"""
    target_db = db_path or DB_PATH
    db_dir = os.path.dirname(os.path.abspath(target_db))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with sqlite3.connect(target_db) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS score_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                tier TEXT,
                total_score REAL NOT NULL,
                mong REAL DEFAULT 0.0,
                day REAL DEFAULT 0.0,
                tien REAL DEFAULT 0.0
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_symbol ON score_history (scan_time, symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_time ON score_history (scan_time DESC)")
        conn.commit()


def _format_delta_cell(val: float) -> str:
    """Định dạng ô hiển thị Delta có kèm icon màu sắc trực quan"""
    if val > 0:
        return f"🟢 +{val:.1f}đ"
    elif val < 0:
        return f"🔴 {val:.1f}đ"
    else:
        return "➖ 0.0đ"


def _format_price(p: Optional[float]) -> str:
    """Định dạng mức giá thông minh theo biên độ giá trị để hiển thị đẹp trên console"""
    if p is None or p <= 0:
        return "0.0"
    if p < 0.0001:
        return f"{p:.6f}"
    if p < 0.01:
        return f"{p:.5f}"
    if p < 1.0:
        return f"{p:.4f}"
    if p < 10.0:
        return f"{p:.3f}"
    if p < 1000.0:
        return f"{p:.2f}"
    return f"{p:.1f}"


def _format_tier(tier: str) -> str:
    """Rút gọn chuỗi phân hạng Tier để hiển thị vừa vặn trong cột bảng (độ rộng 10)"""
    if not tier:
        return "Tier B"
    t_clean = tier.replace("💎", "").strip()
    for prefix in ["Tier S", "Tier A-", "Tier A", "Tier B", "Tier C", "Tier F"]:
        if prefix in t_clean:
            return prefix
    return t_clean[:10]


def extract_buy_zone_and_action(
    state: Optional[Any],
    item: Dict[str, Any],
    delta_24h: float,
    delta_72h: float,
    df_summary: Optional[Any] = None,
    global_signals_pool: Optional[List[Any]] = None
) -> Tuple[str, str, str, float, str, Optional[float]]:
    """
    Trích xuất vùng giá đón gom, xác định trạng thái hành động và động cơ cốt lõi nổi bật
    dựa trên PRD PRE-BREAKOUT RADAR.
    """
    sym = str(item.get("symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
    p_live = float(getattr(state, "last_price", 0.0) or 0.0)
    macro = getattr(state, "macro_levels", None)

    ma7_3d = None
    rebalance_limit = None
    swing_high = None

    if macro is not None:
        if isinstance(macro, dict):
            ma7_3d = macro.get("ma7_3d")
            rebalance_limit = macro.get("tp_limit_rebalance")
            swing_high = macro.get("swing_high_24h") or macro.get("swing_high")
        else:
            ma7_3d = getattr(macro, "ma7_3d", None)
            rebalance_limit = getattr(macro, "tp_limit_rebalance", None)
            swing_high = getattr(macro, "swing_high_24h", None) or getattr(macro, "swing_high", None)

    # Fallback nếu thiếu ma7_3d hoặc p_live: lấy từ get_3d_profile
    if not ma7_3d or p_live <= 0:
        try:
            from core.macro_levels import get_3d_profile
            d3 = get_3d_profile(sym)
            if d3:
                if not ma7_3d:
                    ma7_3d = float(d3.get("ma7_3d", 0.0) or 0.0)
                if p_live <= 0:
                    raw_ind = d3.get("_raw") or {}
                    p_live = float(raw_ind.get("close_3d", 0.0) or d3.get("close", 0.0) or 0.0)
                if not swing_high:
                    swing_high = float(d3.get("swing_high", 0.0) or 0.0)
        except Exception:
            pass

    ema20_1h = getattr(state, "ema20_1h", None)
    ma25_4h = getattr(state, "ma25_4h", None)

    # Kiểm tra từ df_summary (Rebalance Grid)
    reb_rank = None
    reb_score = None
    if df_summary is not None and not getattr(df_summary, "empty", True):
        try:
            for r_idx, (_, r_reb) in enumerate(df_summary.iterrows(), start=1):
                s_r = str(r_reb.get("Symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
                if s_r == sym:
                    reb_rank = r_idx
                    reb_score = float(r_reb.get("TỔNG", 0.0) or 0.0)
                    gs = r_reb.get("grid_setup")
                    if isinstance(gs, dict):
                        if not rebalance_limit:
                            rebalance_limit = gs.get("lower_bound") or gs.get("entry")
                    break
        except Exception:
            pass

    # Kiểm tra tín hiệu từ global_signals_pool và state.scores
    dc4_score = 0.0
    kiet_cung = False
    dc4_exhaustion_val = None
    dc3_score = 0.0
    dc2_score = 0.0

    if global_signals_pool:
        for sig in global_signals_pool:
            sig_sym = str(getattr(sig, "symbol", "") or "").upper().replace("/USDT", "").replace("USDT", "").strip()
            if sig_sym == sym:
                esrc = str(getattr(sig, "engine_source", "") or "")
                sc = float(getattr(sig, "target_score", 0) or 0)
                lbl = str(getattr(sig, "action_label", "") or "")
                if esrc == "DC4":
                    dc4_score = max(dc4_score, sc)
                    if "kiệt cung" in lbl.lower() or "kiet cung" in lbl.lower():
                        kiet_cung = True
                        import re
                        m = re.search(r"(\d+)%", lbl)
                        if m:
                            dc4_exhaustion_val = int(m.group(1))
                elif esrc == "DC3":
                    dc3_score = max(dc3_score, sc)
                elif esrc == "DC2":
                    dc2_score = max(dc2_score, sc)

    if hasattr(state, "scores") and isinstance(state.scores, dict):
        if "DC4" in state.scores:
            s4 = state.scores["DC4"]
            dc4_score = max(dc4_score, float(getattr(s4, "total_score", 0) or 0))
            if getattr(s4, "exhaustion_ratio", None) is not None:
                ratio = float(s4.exhaustion_ratio) * 100 if s4.exhaustion_ratio <= 1.0 else float(s4.exhaustion_ratio)
                if ratio <= 30.0:
                    kiet_cung = True
                    dc4_exhaustion_val = int(ratio)
        if "DC3" in state.scores:
            dc3_score = max(dc3_score, float(getattr(state.scores["DC3"], "total_score", 0) or 0))
        if "DC2" in state.scores:
            dc2_score = max(dc2_score, float(getattr(state.scores["DC2"], "total_score", 0) or 0))

    # Động cơ nổi bật (highlight_engine)
    if dc3_score >= 80 and dc4_score >= 80:
        ex_str = f" (Kiệt cung {dc4_exhaustion_val}%)" if dc4_exhaustion_val is not None else ""
        highlight_engine = f"DC3: {dc3_score:.0f}đ, DC4: {dc4_score:.0f}đ{ex_str}"
    elif dc4_score >= 80 or (kiet_cung and dc4_score > 0):
        ex_str = f" (Kiệt cung {dc4_exhaustion_val}%)" if dc4_exhaustion_val is not None else (" (Kiệt cung)" if kiet_cung else "")
        highlight_engine = f"DC4: {dc4_score:.0f}đ{ex_str}"
    elif reb_rank and reb_rank <= 5 and reb_score and reb_score >= 40:
        highlight_engine = f"Rebalance #{reb_rank} ({reb_score:.1f}đ)"
    elif dc3_score >= 80:
        highlight_engine = f"DC3: {dc3_score:.0f}đ"
    elif dc2_score >= 70:
        highlight_engine = f"DC2 Sniper: {dc2_score:.0f}đ"
    elif reb_rank and reb_score and reb_score > 0:
        highlight_engine = f"Rebalance #{reb_rank} ({reb_score:.1f}đ)"
    else:
        t0_comp = item.get("profile", {}).get("breakdown", {}).get("t0_components", {})
        sc_3d = float(t0_comp.get("3d_strict", 0.0) or 0.0)
        sc_reb = float(t0_comp.get("grid_reb", 0.0) or 0.0)
        if sc_3d > 0 and sc_reb > 0:
            highlight_engine = f"Sóng 3D ({sc_3d:.0f}đ) | Reb ({sc_reb:.0f}đ)"
        elif sc_3d > 0:
            highlight_engine = f"Sóng 3D ({sc_3d:.0f}đ)"
        else:
            highlight_engine = "Dòng tiền gia tốc"

    # 1. Xác định Vùng Giá Đón Gom (Buy Zone)
    ref_buy = 0.0
    if rebalance_limit and float(rebalance_limit) > 0:
        rl = float(rebalance_limit)
        low_b = rl * 0.995
        high_b = rl * 1.005
        ref_buy = rl
        buy_zone = f"{_format_price(low_b)} - {_format_price(high_b)}"
    elif ema20_1h and ma25_4h and float(ema20_1h) > 0 and float(ma25_4h) > 0:
        e = float(ema20_1h)
        m = float(ma25_4h)
        low_b = min(e, m)
        high_b = max(e, m)
        ref_buy = low_b
        buy_zone = f"{_format_price(low_b)} - {_format_price(high_b)}"
    elif ma7_3d and float(ma7_3d) > 0:
        m7 = float(ma7_3d)
        low_b = m7 * 0.995
        high_b = m7 * 1.01
        ref_buy = m7
        buy_zone = f"{_format_price(low_b)} - {_format_price(high_b)}"
    elif p_live > 0:
        ref_buy = p_live * 0.96
        buy_zone = f"{_format_price(ref_buy)} - {_format_price(p_live * 0.98)}"
    else:
        ref_buy = 0.0
        buy_zone = "Theo dõi nến"

    # 2. Xác định Trạng Thái Kỹ Thuật (Action Status & Action Group)
    dist_ma7_pct = ((p_live - ma7_3d) / ma7_3d * 100) if (ma7_3d and float(ma7_3d) > 0 and p_live > 0) else 0.0
    pattern_str = str(item.get("pattern", "")).upper()

    # Ưu tiên 1: Báo gom đón bứt phá: Giá sát MA7 3D (<= 10%) và có kiệt cung hoặc DC4/DC3 cao hoặc sát MA7
    if (dist_ma7_pct <= 10.0 and dist_ma7_pct >= -6.0) and (dc4_score >= 80 or kiet_cung or dist_ma7_pct <= 3.5):
        note = " (Pullback đẹp)" if (dc4_score >= 80 or kiet_cung) else " (Sát ray MA7 3D)"
        action_status = f"🎯 BÁO GOM ĐÓN BỨT PHÁ{note}"
        action_group = "BUY_READY"
    # Ưu tiên 2: Wide Grid đón nhúng: Cột cờ / Wide Grid hoặc chiết khấu > 10% từ đỉnh
    elif "CỘT CỜ" in pattern_str or "WIDE" in pattern_str or (swing_high and float(swing_high) > 0 and p_live > 0 and (swing_high - p_live) / swing_high >= 0.10):
        if swing_high and float(swing_high) > 0 and p_live > 0 and (swing_high - p_live) > 0:
            drop_pct = (swing_high - p_live) / swing_high * 100
            action_status = f"🦅 WIDE GRID ĐÓN NHÚNG (-{drop_pct:.1f}% từ đỉnh)"
        else:
            action_status = "🦅 WIDE GRID ĐÓN NHÚNG"
        action_group = "WIDE_GRID"
    # Ưu tiên 3: Chốt lời từng phần: Giá đã bứt phá >= 15% từ vùng gom
    elif ref_buy > 0 and p_live >= ref_buy * 1.15:
        gain_pct = (p_live - ref_buy) / ref_buy * 100
        action_status = f"💰 CHỐT LỜI TỪNG PHẦN (+{gain_pct:.1f}%)"
        action_group = "TAKE_PROFIT"
    # Ưu tiên 4: Đã bứt phá (Test nắp hộp): Giá đã vượt qua vùng gom > 4%
    elif ref_buy > 0 and p_live > ref_buy * 1.04:
        sh_tag = f" {_format_price(swing_high)}" if (swing_high and float(swing_high) > 0) else ""
        action_status = f"🚀 ĐÃ BỨT PHÁ (Test nắp hộp{sh_tag})"
        action_group = "BREAKOUT_WATCH"
    else:
        action_status = "🟡 THEO DÕI NỀN"
        action_group = "WATCH"

    return buy_zone, action_status, action_group, ref_buy, highlight_engine, swing_high


def render_pre_breakout_radar_table(
    scored_pool: List[Dict[str, Any]],
    live_data_map: Optional[Dict[str, Any]] = None,
    df_summary: Optional[Any] = None,
    global_signals_pool: Optional[List[Any]] = None
) -> List[Dict[str, Any]]:
    """
    Render BẢNG BIẾN THIÊN TÍN NHIỆM & BÁO GOM BỨT PHÁ (PRE-BREAKOUT RADAR)
    với bộ lọc dòng tiền chặt chẽ và dòng kết luận hành động tự động.
    """
    radar_candidates = []
    for item in scored_pool:
        sym = str(item.get("symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
        if not sym:
            continue

        tier_raw = str(item.get("tier", ""))
        decay_score = float(item.get("decay_score", item.get("total_score", item.get("current", 0.0))) or 0.0)

        # Lọc 1: Loại bỏ Tier F và điểm thấp (< 45đ decay)
        is_valid_tier = any(t in tier_raw for t in ["Tier S", "Tier A", "Tier B"]) and "Tier F" not in tier_raw
        if not is_valid_tier or decay_score < 45.0:
            continue

        d24 = float(item.get("delta_24h", item.get("delta_1", 0.0)) or 0.0)
        d72 = float(item.get("delta_72h", item.get("delta_4", 0.0)) or 0.0)

        # Lọc 2: Ngưỡng gia tốc bắt buộc: d72 >= +40.0 HOẶC d24 >= +10.0
        if not (d72 >= 40.0 or d24 >= 10.0):
            continue

        state = None
        if live_data_map:
            state = (
                live_data_map.get(sym)
                or live_data_map.get(f"{sym}/USDT")
                or live_data_map.get(f"{sym}USDT")
            )

        buy_zone, action_status, action_group, ref_buy, highlight_engine, swing_high = extract_buy_zone_and_action(
            state=state,
            item=item,
            delta_24h=d24,
            delta_72h=d72,
            df_summary=df_summary,
            global_signals_pool=global_signals_pool
        )

        radar_candidates.append({
            **item,
            "symbol": sym,
            "tier_clean": _format_tier(tier_raw),
            "decay_score": decay_score,
            "delta_24h": d24,
            "delta_72h": d72,
            "buy_zone": buy_zone,
            "action_status": action_status,
            "action_group": action_group,
            "ref_buy": ref_buy,
            "highlight_engine": highlight_engine,
            "swing_high": swing_high
        })

    sep_width = 145
    print("\n" + "=" * sep_width)
    print("📊 BẢNG BIẾN THIÊN TÍN NHIỆM & BÁO GOM BỨT PHÁ (PRE-BREAKOUT RADAR)")
    print("⚠️ BỘ LỌC DÒNG TIỀN: Chỉ hiển thị mã có Δ72H ≥ +40đ HOẶC Δ24H ≥ +10đ | Loại bỏ Tier F & Nhiễu")
    print("=" * sep_width)

    if not radar_candidates:
        print("  (Không có mã uy tín nào thỏa mãn bộ lọc gia tốc Δ72H ≥ +40đ hoặc Δ24H ≥ +10đ)")
        print("=" * sep_width)
        return []

    # Sắp xếp theo thứ tự ưu tiên: Delta 72H giảm dần, kế đến Delta 24H
    radar_candidates.sort(key=lambda x: (x.get("delta_72h", 0.0), x.get("delta_24h", 0.0)), reverse=True)

    header = f"{'Mã':<6} | {'Tier':<10} | {'Điểm':<6} | {'Δ 24H':<9} | {'Δ 72H':<9} | {'Động Cơ Nổi Bật':<28} | {'Vùng Gom (Buy Zone)':<19} | {'Trạng Thái Kỹ Thuật'}"
    print(header)
    print("-" * sep_width)

    buy_list, watch_list, grid_list = [], [], []

    for r in radar_candidates:
        sym = r['symbol']
        tier = r['tier_clean']
        score_str = f"{r['decay_score']:.1f}"
        d24_str = _format_delta_cell(r['delta_24h'])
        d72_str = _format_delta_cell(r['delta_72h'])
        engine_str = r['highlight_engine'][:28]
        bz_str = r['buy_zone'][:19]
        status_str = r['action_status']

        print(f"{sym:<6} | {tier:<10} | {score_str:<6} | {d24_str:<9} | {d72_str:<9} | {engine_str:<28} | {bz_str:<19} | {status_str}")

        # Phân loại cho dòng kết luận hành động
        if r['action_group'] == "BUY_READY":
            buy_list.append(f"{sym} (Limit {_format_price(r['ref_buy'])})")
        elif r['action_group'] == "BREAKOUT_WATCH":
            if r.get('swing_high') and float(r['swing_high']) > 0:
                watch_list.append(f"{sym} (Chờ vượt dứt khoát {_format_price(r['swing_high'])})")
            else:
                watch_list.append(f"{sym} (Chờ test nắp hộp)")
        elif r['action_group'] == "WIDE_GRID":
            grid_list.append(f"{sym} (Wide Grid {r['buy_zone']})")
        elif r['action_group'] == "TAKE_PROFIT":
            grid_list.append(f"{sym} (Chốt lời từng phần, dời SL {_format_price(r['ref_buy'] * 1.08)})")
        else:
            watch_list.append(f"{sym} (Theo dõi nền)")

    print("-" * sep_width)
    print("💡 KẾT LUẬN HÀNH ĐỘNG:")
    if buy_list:
        print(f"• ƯU TIÊN VÀO LỆNH (Vùng gom tối ưu): {' | '.join(buy_list)}")
    if watch_list:
        print(f"• THEO DÕI NẮP HỘP (Không mua đuổi): {' | '.join(watch_list)}")
    if grid_list:
        print(f"• ĐÓN NHÚNG SÂU / WIDE GRID: {' | '.join(grid_list)}")
    if not (buy_list or watch_list or grid_list):
        print("• Hiện tại chưa có mã nào chạm vùng kích hoạt hành động cụ thể.")
    print("=" * sep_width)

    return radar_candidates


def record_and_analyze(
    current_scores: List[Dict[str, Any]],
    keep_snapshots: int = 24,
    db_path: Optional[str] = None,
    scan_time: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Ghi nhận dữ liệu snapshot và tính toán độ biến thiên theo cột ngày lịch sử (Multi-Day Delta Audit):
    - Thay thế cơ chế so sánh phiên giờ ngắn hạn dễ gây nhiễu bằng chuỗi ngày lịch sử chuẩn (T0, T1, T3).
    - Δ 24H: Biến thiên ngắn hạn 1 ngày (Score(T0) - Score(T1)).
    - Δ 72H: Biến thiên trung hạn 3 ngày (Score(T0) - Score(T3)).
    """
    if not current_scores:
        return [], []

    target_db = db_path or DB_PATH
    try:
        init_db(target_db)
        current_scan_time = scan_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(target_db) as conn:
            cursor = conn.cursor()
            insert_data = []
            for item in current_scores:
                sym = str(item.get('symbol', '')).replace('/USDT', '').replace('USDT', '').strip().upper()
                if not sym:
                    continue
                tier_val = item.get('tier', '')
                total_val = float(item.get('total', item.get('total_score', 0.0)))
                bdown = item.get('breakdown', {})
                mong_val = float(item.get('mong', item.get('mong_score', bdown.get('foundation_40', 0.0))))
                day_val = float(item.get('day', item.get('day_score', bdown.get('momentum_40', 0.0))))
                tien_val = float(item.get('tien', item.get('tien_score', bdown.get('cashflow_20', 0.0))))
                insert_data.append((
                    current_scan_time, sym, tier_val, total_val, mong_val, day_val, tien_val
                ))
            if insert_data:
                cursor.executemany("""
                    INSERT INTO score_history (scan_time, symbol, tier, total_score, mong, day, tien)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, insert_data)
                cursor.execute("""
                    DELETE FROM score_history 
                    WHERE scan_time NOT IN (
                        SELECT scan_time FROM (
                            SELECT DISTINCT scan_time FROM score_history ORDER BY scan_time DESC LIMIT ?
                        )
                    )
                """, (keep_snapshots,))
                conn.commit()
    except Exception:
        pass

    # ═════════════════════════════════════════════════════════════════════════
    # TÍNH TOÁN BIẾN THIÊN THEO NGÀY (T0, T1, T3) DỰA TRÊN PHÂN RÃ SUY HAO
    # ═════════════════════════════════════════════════════════════════════════
    delta_list = []
    for item in current_scores:
        sym = str(item.get('symbol', '')).replace('/USDT', '').replace('USDT', '').strip().upper()
        if not sym:
            continue

        bdown = item.get('breakdown', {})
        h_mong = bdown.get('history_mong', [])
        h_day  = bdown.get('history_day', [])
        h_tien = bdown.get('history_tien', [])

        if h_mong or h_day or h_tien:
            m0 = float(h_mong[0]) if len(h_mong) > 0 else 0.0
            m1 = float(h_mong[1]) if len(h_mong) > 1 else 0.0
            m3 = float(h_mong[3]) if len(h_mong) > 3 else 0.0

            d0 = float(h_day[0]) if len(h_day) > 0 else 0.0
            d1 = float(h_day[1]) if len(h_day) > 1 else 0.0
            d3 = float(h_day[3]) if len(h_day) > 3 else 0.0

            t0 = float(h_tien[0]) if len(h_tien) > 0 else 0.0
            t1 = float(h_tien[1]) if len(h_tien) > 1 else 0.0
            t3 = float(h_tien[3]) if len(h_tien) > 3 else 0.0

            raw0 = round(m0 + d0 + t0, 1)
            raw1 = round(m1 + d1 + t1, 1)
            raw3 = round(m3 + d3 + t3, 1)

            curr_score = float(item.get('total_score', item.get('total', raw0)))
            if raw0 == 0.0 and curr_score > 0:
                raw0 = curr_score
        else:
            curr_score = float(item.get('total_score', item.get('total', 0.0)))
            raw0 = curr_score
            raw1 = float(item.get('prev_1', raw0))
            raw3 = float(item.get('prev_3', item.get('prev_4', raw1)))

        d24 = round(raw0 - raw1, 1)
        d72 = round(raw0 - raw3, 1)

        # Đánh giá trạng thái động học
        if d24 >= 5.0 and d72 >= 15.0:
            eval_tag = "🚀 Bứt phá mạnh"
        elif (d24 > 0 and d72 >= 0) or (d72 >= 8.0 and d24 >= 0):
            eval_tag = "🟢 Tích lũy tăng"
        elif d24 < 0 and d72 < -10.0:
            eval_tag = "⛔ Xả dốc mạnh"
        elif d24 < 0:
            eval_tag = "⚠️ Hạ nhiệt ngắn"
        else:
            eval_tag = "➖ Ổn định"

        decay_sc = float(item.get('total_score', item.get('total', raw0)))

        delta_list.append({
            'symbol': sym,
            'tier': item.get('tier', ''),
            'action_recommendation': item.get('action_recommendation', ''),
            'current': raw0,
            'decay_score': decay_sc,
            'prev_1': raw1,
            'prev_3': raw3,
            'delta_1': d24,
            'delta_4': d72,
            'delta_24h': d24,
            'delta_72h': d72,
            'eval': eval_tag,
            'profile': item
        })

    # Toàn bộ danh sách mã có tăng điểm để Radar lọc chính xác
    top_gainers = sorted(
        [
            x for x in delta_list 
            if (x['delta_24h'] > 0 or x['delta_72h'] > 0)
        ],
        key=lambda x: (x['delta_72h'], x['delta_24h']),
        reverse=True
    )

    # [TOP 10 GIẢM ĐIỂM CẢNH BÁO]:
    # Có suy giảm (Δ24h < 0 hoặc Δ72h < 0), và có điểm quá khứ hoặc hiện tại >= 40.0
    top_losers = sorted(
        [
            x for x in delta_list 
            if (x['delta_24h'] < 0 or x['delta_72h'] < 0) 
            and (x['prev_1'] >= 40.0 or x['current'] >= 40.0)
        ],
        key=lambda x: (x['delta_72h'] + x['delta_24h'])
    )[:10]

    return top_gainers, top_losers


def print_delta_tables(
    top_gainers: List[Dict[str, Any]],
    top_losers: List[Dict[str, Any]],
    has_history: Optional[bool] = None,
    db_path: Optional[str] = None,
    live_data_map: Optional[Dict[str, Any]] = None,
    df_summary: Optional[Any] = None,
    global_signals_pool: Optional[List[Any]] = None
) -> None:
    """
    Hiển thị BẢNG BIẾN THIÊN TÍN NHIỆM & BÁO GOM BỨT PHÁ (PRE-BREAKOUT RADAR)
    thay thế bảng số thô, lọc sạch nhiễu và hiển thị vùng gom, trạng thái cùng dòng kết luận.
    Giữ lại bảng cảnh báo giảm điểm và thiết lập lệnh Top 3.
    """
    # 1. Render Bảng Pre-Breakout Radar
    radar_candidates = render_pre_breakout_radar_table(
        scored_pool=top_gainers,
        live_data_map=live_data_map,
        df_summary=df_summary,
        global_signals_pool=global_signals_pool
    )

    sep_width = 145

    # 2. Render [TOP 10 GIẢM ĐIỂM CẢNH BÁO] (nếu có)
    if top_losers:
        print(f"\n📉 [TOP 10 GIẢM ĐIỂM CẢNH BÁO] (Lực mua suy yếu / Bị phân phối)")
        print("-" * sep_width)
        print(f"{'Mã':<8} | {'Tier':<22} | {'Hiện Tại':<9} | {'Δ 24H (T0-T1)':<15} | {'Δ 72H (T0-T3)':<15} | {'Đánh Giá':<20}")
        print("-" * sep_width)
        for item in top_losers:
            c1 = _format_delta_cell(item.get('delta_24h', item.get('delta_1', 0.0)))
            c4 = _format_delta_cell(item.get('delta_72h', item.get('delta_4', 0.0)))
            print(f"{item['symbol']:<8} | {item['tier']:<22} | {item['current']:<9.1f} | {c1:<15} | {c4:<15} | {item['eval']:<20}")
        print("=" * sep_width)

    # 3. 🚀 Thiết lập lệnh tự động cho Top 3 mã bứt phá nhiều nhất (Dòng tiền gia tốc)
    targets_for_orders = radar_candidates if radar_candidates else top_gainers
    top_3_targets = targets_for_orders[:3] if targets_for_orders else []
    if top_3_targets:
        try:
            from core.historical_rater import calculate_tier_s_setups
        except Exception:
            calculate_tier_s_setups = None

        if calculate_tier_s_setups:
            print(f"\n🚀 THIẾT LẬP LỆNH CHO TOP 3 MÃ BỨT PHÁ NHIỀU NHẤT (DÒNG TIỀN GIA TỐC) [{len(top_3_targets)} mã]:")
            for item in top_3_targets:
                sym = item["symbol"]
                setups = calculate_tier_s_setups(sym)
                if setups:
                    score_val = item.get("decay_score", item.get("current", 0.0))
                    tier_str = item.get("tier", "")
                    rec = item.get("action_recommendation")
                    if not rec:
                        if "Tier S" in tier_str or score_val >= 80.0:
                            rec = "🟢 DUYỆT TỰ ĐỘNG (Đánh Full Volume)"
                        elif "Tier A" in tier_str or score_val >= 60.0:
                            rec = "🟢 DUYỆT TỰ ĐỘNG (Cấu trúc ổn định)"
                        elif "Tier B" in tier_str or score_val >= 40.0:
                            rec = "🟠 ĐÁNH 30% VOL (Bắt Pullback hoặc ăn ngắn)"
                        else:
                            rec = "👀 THEO DÕI"

                    print(f"  ⭐ {sym} ({score_val:.1f}đ - {tier_str}) - Khuyến nghị: {rec}")
                    if setups.get("oco"):
                        print(f"     ↳ {setups['oco']}")
                    if setups.get("grid_tp2"):
                        line_tp2 = setups['grid_tp2'].strip()
                        if line_tp2.startswith("↳"):
                            line_tp2 = line_tp2[1:].strip()
                        print(f"     ↳ {line_tp2}")
                    if setups.get("wide_grid"):
                        line_wg = setups['wide_grid'].strip()
                        if line_wg.startswith("↳"):
                            line_wg = line_wg[1:].strip()
                        print(f"     ↳ {line_wg}")
            print("=" * sep_width)
    print("\n")


# Alias tương thích ngược
analyze_multi_day_delta = record_and_analyze
print_multi_day_delta_tables = print_delta_tables

