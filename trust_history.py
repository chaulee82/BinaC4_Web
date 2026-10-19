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
        return "⚪ 0.0đ"


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

            # Fallback nếu điểm thô T0 bằng 0 nhưng có điểm total_score
            curr_score = float(item.get('total_score', item.get('total', raw0)))
            if raw0 == 0.0 and curr_score > 0:
                raw0 = curr_score
        else:
            # Fallback nếu caller truyền dict điểm phẳng
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

        delta_list.append({
            'symbol': sym,
            'tier': item.get('tier', ''),
            'action_recommendation': item.get('action_recommendation', ''),
            'current': raw0,
            'prev_1': raw1,
            'prev_3': raw3,
            'delta_1': d24,
            'delta_4': d72,
            'delta_24h': d24,
            'delta_72h': d72,
            'eval': eval_tag,
            'profile': item
        })

    # [TOP 10 TĂNG ĐIỂM UY TÍN]:
    # Có tăng điểm (Δ24h > 0 hoặc Δ72h > 0), tổng delta > 0, và điểm hiện tại >= 40.0
    top_gainers = sorted(
        [
            x for x in delta_list 
            if (x['delta_24h'] > 0 or x['delta_72h'] > 0)
            and (x['delta_24h'] + x['delta_72h']) > 0
            and x['current'] >= 40.0
        ],
        key=lambda x: (x['delta_72h'] + x['delta_24h']),
        reverse=True
    )[:10]

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
    db_path: Optional[str] = None
) -> None:
    """
    Hiển thị [TOP 10 TĂNG ĐIỂM UY TÍN] và [TOP 10 GIẢM ĐIỂM CẢNH BÁO] ra Terminal Log
    dựa trên biến thiên ngày Δ24H (T0-T1) và Δ72H (T0-T3).
    """
    sep_width = 110
    print("\n" + "=" * sep_width)
    print("📊 BẢNG THEO DÕI BIẾN THIÊN TÍN NHIỆM (DELTA AUDIT)")
    print("=" * sep_width)

    print("\n📈 [TOP 10 TĂNG ĐIỂM UY TÍN] (Dòng tiền gia tốc / Thoát đáy vĩ mô)")
    print("-" * sep_width)
    print(f"{'Mã':<8} | {'Tier':<22} | {'Hiện Tại':<9} | {'Δ 24H (T0-T1)':<15} | {'Δ 72H (T0-T3)':<15} | {'Đánh Giá':<20}")
    print("-" * sep_width)
    if not top_gainers:
        print("  (Không có mã uy tín nào (>= 40đ) ghi nhận tăng điểm trong 24h/72h gần nhất)")
    else:
        for item in top_gainers:
            c1 = _format_delta_cell(item.get('delta_24h', item.get('delta_1', 0.0)))
            c4 = _format_delta_cell(item.get('delta_72h', item.get('delta_4', 0.0)))
            print(f"{item['symbol']:<8} | {item['tier']:<22} | {item['current']:<9.1f} | {c1:<15} | {c4:<15} | {item['eval']:<20}")

    print("\n📉 [TOP 10 GIẢM ĐIỂM CẢNH BÁO] (Lực mua suy yếu / Bị phân phối)")
    print("-" * sep_width)
    print(f"{'Mã':<8} | {'Tier':<22} | {'Hiện Tại':<9} | {'Δ 24H (T0-T1)':<15} | {'Δ 72H (T0-T3)':<15} | {'Đánh Giá':<20}")
    print("-" * sep_width)
    if not top_losers:
        print("  (Không có mã điểm cao nào ghi nhận suy giảm bất thường)")
    else:
        for item in top_losers:
            c1 = _format_delta_cell(item.get('delta_24h', item.get('delta_1', 0.0)))
            c4 = _format_delta_cell(item.get('delta_72h', item.get('delta_4', 0.0)))
            print(f"{item['symbol']:<8} | {item['tier']:<22} | {item['current']:<9.1f} | {c1:<15} | {c4:<15} | {item['eval']:<20}")
    print("=" * sep_width)

    # 🚀 Thiết lập lệnh tự động cho Top 3 mã tăng nhiều nhất (Dòng tiền gia tốc)
    top_3_gainers = top_gainers[:3] if top_gainers else []
    if top_3_gainers:
        try:
            from core.historical_rater import calculate_tier_s_setups
        except Exception:
            calculate_tier_s_setups = None

        if calculate_tier_s_setups:
            print(f"\n🚀 THIẾT LẬP LỆNH CHO TOP 3 MÃ TĂNG NHIỀU NHẤT (DÒNG TIỀN GIA TỐC) [{len(top_3_gainers)} mã]:")
            for item in top_3_gainers:
                sym = item["symbol"]
                setups = calculate_tier_s_setups(sym)
                if setups:
                    score_val = item.get("current", 0.0)
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
