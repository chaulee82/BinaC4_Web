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


def _wrap_coin_tokens(coins: List[str], max_width: int = 64) -> List[str]:
    """Bọc các mã tiền thành các dòng vừa vặn, không làm gãy đứt chuỗi của từng mã"""
    lines = []
    curr_line = ""
    for c in coins:
        if not curr_line:
            curr_line = c
        elif len(curr_line) + 3 + len(c) <= max_width:
            curr_line += " | " + c
        else:
            lines.append(curr_line)
            curr_line = c
    if curr_line:
        lines.append(curr_line)
    return lines


def render_downgrade_warning_table(
    losers_pool: List[Dict[str, Any]],
    live_data_map: Optional[Dict[str, Any]] = None,
    warning_results: Optional[List[Dict[str, Any]]] = None
) -> None:
    """
    Hiển thị BẢNG CẢNH BÁO SUY GIẢM & PHÂN PHỐI theo:
    - Cấp Độ (Mức Rủi Ro)
    - Tín Hiệu Kỹ Thuật
    - Danh Sách Mã
    kèm Dòng Kết Luận Hành Động Phòng Thủ.
    """
    sep_width = 145
    print("\n" + "=" * sep_width)
    print("📉 BẢNG CẢNH BÁO SUY GIẢM & PHÂN PHỐI (DOWNGRADE & SELL-OFF RADAR)")
    print("⚠️ BỘ LỌC CẢNH BÁO: Phân loại theo cấp độ suy thoái dòng tiền & gãy cấu trúc kỹ thuật")
    print("=" * sep_width)

    if not losers_pool:
        print("  (Không có mã uy tín nào ghi nhận áp lực xả dốc hoặc suy giảm bất thường)")
        print("=" * sep_width)
        return

    # Thu thập ew_map từ warning_results nếu có
    ew_map = {}
    if warning_results:
        for r in warning_results:
            sym_w = str(r.get("symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
            if sym_w:
                ew_map[sym_w] = {
                    "level": int(r.get("level", 0) or 0),
                    "label": str(r.get("label", "") or ""),
                    "trigger": str(r.get("trigger", "") or "")
                }

    # Phân loại thành 3 Cấp Độ
    cap_1_coins = []  # Xả dốc mạnh / Nguy hiểm
    cap_2_coins = []  # Suy yếu đà tăng / Mất bệ đỡ
    cap_3_coins = []  # Hạ nhiệt ngắn / Chốt lời thoái lui

    for item in losers_pool:
        sym = str(item.get("symbol", "")).upper().replace("/USDT", "").replace("USDT", "").strip()
        if not sym:
            continue

        d24 = float(item.get("delta_24h", item.get("delta_1", 0.0)) or 0.0)
        d72 = float(item.get("delta_72h", item.get("delta_4", 0.0)) or 0.0)
        score = float(item.get("decay_score", item.get("total_score", item.get("current", 0.0))) or 0.0)

        state = None
        if live_data_map:
            state = live_data_map.get(sym) or live_data_map.get(f"{sym}/USDT") or live_data_map.get(f"{sym}USDT")

        p_live = float(getattr(state, "last_price", 0.0) or 0.0)
        ma7_3d = None
        macro = getattr(state, "macro_levels", None)
        if macro is not None:
            if isinstance(macro, dict):
                ma7_3d = macro.get("ma7_3d")
            else:
                ma7_3d = getattr(macro, "ma7_3d", None)

        w_info = ew_map.get(sym, {})
        ew_lvl = w_info.get("level", 0)

        # Định dạng chuỗi hiển thị mã kèm điểm và delta nổi bật
        if d72 <= -20.0:
            coin_str = f"{sym} ({score:.1f}đ | Δ72H: 🔴 {d72:.1f}đ)"
        elif d24 <= -8.0:
            coin_str = f"{sym} ({score:.1f}đ | Δ24H: 🔴 {d24:.1f}đ)"
        elif d24 < 0:
            coin_str = f"{sym} ({score:.1f}đ | 🔴 {d24:.1f}đ)"
        else:
            coin_str = f"{sym} ({score:.1f}đ | Δ72H: 🔴 {d72:.1f}đ)"

        # Phân loại cấp độ rủi ro:
        # Cấp 1: Xả dốc mạnh (d72 <= -20đ HOẶC d24 <= -15đ HOẶC ew_level >= 3 HOẶC gãy sâu MA7 3D kèm d72 < -10đ)
        if d72 <= -20.0 or d24 <= -15.0 or ew_lvl >= 3 or (ma7_3d and p_live > 0 and p_live < ma7_3d * 0.95 and d72 < -10.0):
            cap_1_coins.append(coin_str)
        # Cấp 2: Suy giảm đà tăng (d24 <= -8.0đ HOẶC d72 <= -10.0đ HOẶC ew_lvl == 2 HOẶC mất MA7 3D kèm d24 < -3đ)
        elif d24 <= -8.0 or d72 <= -10.0 or ew_lvl == 2 or (ma7_3d and p_live > 0 and p_live < ma7_3d and d24 < -3.0):
            cap_2_coins.append(coin_str)
        # Cấp 3: Hạ nhiệt ngắn / thoái lui nhẹ
        else:
            cap_3_coins.append(coin_str)

    header = f"{'Cấp Độ (Mức Rủi Ro)':<30} | {'Tín Hiệu Kỹ Thuật Cảnh Báo':<45} | {'Danh Sách Mã'}"
    print(header)
    print("-" * sep_width)

    levels_def = [
        (
            "🔴 CẤP 1: XẢ DỐC MẠNH / NGUY HIỂM",
            "Δ72H ≤ -20đ / Δ24H ≤ -15đ (Xả dốc tháo chạy)",
            cap_1_coins
        ),
        (
            "🟠 CẤP 2: SUY YẾU / MẤT BỆ ĐỠ",
            "Δ24H ≤ -8đ / Mất ray MA7 3D (Đứt gãy đà tăng)",
            cap_2_coins
        ),
        (
            "🟡 CẤP 3: HẠ NHIỆT / CHỐT LỜI",
            "Δ24H < 0đ (-1đ đến -7đ) (Hấp thụ chốt lời ngắn)",
            cap_3_coins
        )
    ]

    col3_w = 64
    for lvl_name, sig_desc, coins in levels_def:
        if coins:
            lines = _wrap_coin_tokens(coins, max_width=col3_w)
            for i, line in enumerate(lines):
                if i == 0:
                    print(f"{lvl_name:<30} | {sig_desc:<45} | {line}")
                else:
                    print(f"{'':<30} | {'':<45} | {line}")
        else:
            print(f"{lvl_name:<30} | {sig_desc:<45} | (Không có mã vi phạm)")

    print("-" * sep_width)
    print("💡 HÀNH ĐỘNG PHÒNG THỦ:")
    if cap_1_coins:
        print("• CẤP 1 (Rủi ro cao): Thoát vị thế dứt khoát, dời SL bảo toàn vốn, tuyệt đối KHÔNG bắt dao rơi.")
    if cap_2_coins:
        print("• CẤP 2 (Suy yếu): Hạ 50% tỷ trọng, ngừng mua gia tăng, chờ tín hiệu tạo đáy cân bằng mới.")
    if cap_3_coins:
        print("• CẤP 3 (Hạ nhiệt): Theo dõi nền tích lũy, quan sát phản ứng tại các mốc hỗ trợ kỹ thuật.")
    if not (cap_1_coins or cap_2_coins or cap_3_coins):
        print("• Thị trường duy trì ổn định, không ghi nhận mã suy yếu nghiêm trọng.")
    print("=" * sep_width)


def print_delta_tables(
    top_gainers: List[Dict[str, Any]],
    top_losers: List[Dict[str, Any]],
    has_history: Optional[bool] = None,
    db_path: Optional[str] = None,
    live_data_map: Optional[Dict[str, Any]] = None,
    df_summary: Optional[Any] = None,
    global_signals_pool: Optional[List[Any]] = None,
    warning_results: Optional[List[Dict[str, Any]]] = None
) -> None:
    """
    Hiển thị BẢNG BIẾN THIÊN TÍN NHIỆM & BÁO GOM BỨT PHÁ (PRE-BREAKOUT RADAR)
    thay thế bảng số thô, lọc sạch nhiễu và hiển thị vùng gom, trạng thái cùng dòng kết luận.
    Đồng thời hiển thị BẢNG CẢNH BÁO SUY GIẢM & PHÂN PHỐI theo cấp độ, tín hiệu và danh sách mã.
    """
    # 1. Render Bảng Pre-Breakout Radar
    radar_candidates = render_pre_breakout_radar_table(
        scored_pool=top_gainers,
        live_data_map=live_data_map,
        df_summary=df_summary,
        global_signals_pool=global_signals_pool
    )

    sep_width = 145

    # 2. Render BẢNG CẢNH BÁO SUY GIẢM & PHÂN PHỐI (theo Cấp độ, Tín hiệu, Danh sách mã)
    render_downgrade_warning_table(
        losers_pool=top_losers,
        live_data_map=live_data_map,
        warning_results=warning_results
    )

    # 3. 📋 BẢNG THÔNG SỐ GRID GOM HÀNG CHUẨN HÌNH HỌC (+8% / -12%) (CÀI ĐẶT SPOT GRID)
    buy_ready_items = [r for r in radar_candidates if r.get('action_group') == "BUY_READY"]
    if buy_ready_items:
        print("\n📋 BẢNG THÔNG SỐ GRID GOM HÀNG CHUẨN HÌNH HỌC (+8% / -12%) (CÀI ĐẶT SPOT GRID):")
        print("-" * sep_width)
        grid_fmt_hdr = f"{'Mã Cặp':<12} | {'Dải Lưới & Số Lưới':<38} | {'Trig (Trigger)':<18} | {'SL (Stop-Loss)':<18} | {'TP (Take-Profit)':<18} | {'Biên Độ/Lưới'}"
        print(grid_fmt_hdr)
        print("-" * sep_width)
        for b_item in buy_ready_items:
            b_sym = b_item["symbol"]
            b_trig = float(b_item.get("ref_buy", 0.0))
            if b_trig > 0:
                b_low = b_trig * 0.90
                b_up = b_trig * 1.20
                b_sl = b_trig * 0.88
                grid_str = f"Dải L={_format_price(b_low)} - U={_format_price(b_up)} (24 Lưới)"
                print(f"{b_sym + '/USDT':<12} | {grid_str:<38} | {_format_price(b_trig):<18} | {_format_price(b_sl):<18} | {_format_price(b_up):<18} | ~1.04%")
        print("-" * sep_width)
        print("=" * sep_width)
    print("\n")


# Alias tương thích ngược
analyze_multi_day_delta = record_and_analyze
print_multi_day_delta_tables = print_delta_tables

