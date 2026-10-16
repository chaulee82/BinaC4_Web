"""
Module: trust_history.py
Mục đích: Lưu trữ lịch sử Trust Score V2.0 và phân tích biến thiên Delta.
Đường dẫn: /root/BinaC4_Web/trust_history.py (hoặc project root trên môi trường local)
Mã phân hệ: MOD-TRUST-HIST-01
"""

import os
import sys
import sqlite3
from datetime import datetime
from typing import List, Dict, Tuple, Optional

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


def record_and_analyze(
    current_scores: List[Dict],
    keep_snapshots: int = 24,
    db_path: Optional[str] = None,
    scan_time: Optional[str] = None
) -> Tuple[List[Dict], List[Dict]]:
    """
    Ghi nhận dữ liệu phiên hiện tại và tính toán độ biến thiên đa mốc (Delta Audit).
    - T1: 1 phiên liền trước (Spike/Impulse)
    - T4: 4 phiên trước (Trend Persistence, hoặc phiên xa nhất có sẵn)
    """
    if not current_scores:
        return [], []

    target_db = db_path or DB_PATH
    init_db(target_db)
    current_scan_time = scan_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(target_db) as conn:
        cursor = conn.cursor()

        # 1. Trích xuất danh sách mốc thời gian quá khứ (trước khi ghi phiên hiện tại)
        cursor.execute("SELECT DISTINCT scan_time FROM score_history ORDER BY scan_time DESC LIMIT 10")
        past_times = [r[0] for r in cursor.fetchall()]

        t_prev_1: Optional[str] = past_times[0] if len(past_times) >= 1 else None
        t_prev_4: Optional[str] = past_times[3] if len(past_times) >= 4 else (past_times[-1] if past_times else None)

        # 2. Truy xuất điểm số lịch sử
        scores_t1: Dict[str, float] = {}
        if t_prev_1:
            cursor.execute("SELECT symbol, total_score FROM score_history WHERE scan_time = ?", (t_prev_1,))
            scores_t1 = dict(cursor.fetchall())

        scores_t4: Dict[str, float] = {}
        if t_prev_4:
            cursor.execute("SELECT symbol, total_score FROM score_history WHERE scan_time = ?", (t_prev_4,))
            scores_t4 = dict(cursor.fetchall())

        # 3. Batch Insert dữ liệu phiên hiện tại
        insert_data = []
        for item in current_scores:
            sym = str(item.get('symbol', '')).replace('/USDT', '').replace('USDT', '').strip().upper()
            if not sym:
                continue
            tier_val = item.get('tier', '')
            total_val = float(item.get('total', item.get('total_score', 0.0)))
            mong_val = float(item.get('mong', item.get('mong_score', item.get('breakdown', {}).get('foundation_40', 0.0))))
            day_val = float(item.get('day', item.get('day_score', item.get('breakdown', {}).get('momentum_40', 0.0))))
            tien_val = float(item.get('tien', item.get('tien_score', item.get('breakdown', {}).get('cashflow_20', 0.0))))
            insert_data.append((
                current_scan_time,
                sym,
                tier_val,
                total_val,
                mong_val,
                day_val,
                tien_val
            ))

        cursor.executemany("""
            INSERT INTO score_history (scan_time, symbol, tier, total_score, mong, day, tien)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, insert_data)

        # 4. Tự động dọn dẹp các snapshot cũ (giữ tối đa keep_snapshots phiên gần nhất)
        cursor.execute("""
            DELETE FROM score_history 
            WHERE scan_time NOT IN (
                SELECT scan_time FROM (
                    SELECT DISTINCT scan_time FROM score_history ORDER BY scan_time DESC LIMIT ?
                )
            )
        """, (keep_snapshots,))
        conn.commit()

    # 5. Xử lý logic Delta
    delta_list = []
    for item in current_scores:
        sym = str(item.get('symbol', '')).replace('/USDT', '').replace('USDT', '').strip().upper()
        if not sym:
            continue
        curr = float(item.get('total', item.get('total_score', 0.0)))
        p1 = scores_t1.get(sym)
        p4 = scores_t4.get(sym)

        d1 = round(curr - p1, 2) if p1 is not None else 0.0
        d4 = round(curr - p4, 2) if p4 is not None else 0.0

        # Đánh giá trạng thái động học
        if p1 is None:
            eval_tag = "➖ Khởi tạo"
        elif d1 >= 5.0 and d4 >= 8.0:
            eval_tag = "🚀 Bứt phá mạnh"
        elif (d1 > 0 and d4 >= 0) or (d4 >= 3.0 and d1 >= 0):
            eval_tag = "🟢 Tích lũy tăng"
        elif d1 < 0 and d4 < -5.0:
            eval_tag = "⛔ Xả dốc mạnh"
        elif d1 < 0:
            eval_tag = "⚠️ Hạ nhiệt ngắn"
        else:
            eval_tag = "➖ Ổn định"

        delta_list.append({
            'symbol': sym,
            'tier': item.get('tier', ''),
            'action_recommendation': item.get('action_recommendation', ''),
            'current': curr,
            'prev_1': p1,
            'delta_1': d1,
            'delta_4': d4,
            'eval': eval_tag
        })

    # Top 10 Tăng Uy Tín: có dữ liệu quá khứ, (delta_1 > 0 hoặc delta_4 > 0), tổng delta > 0, current >= 50.0
    top_gainers = sorted(
        [
            x for x in delta_list 
            if x['prev_1'] is not None 
            and (x['delta_1'] > 0 or x['delta_4'] > 0)
            and (x['delta_1'] + x['delta_4']) > 0
            and x['current'] >= 50.0
        ],
        key=lambda x: (x['delta_4'] + x['delta_1']),
        reverse=True
    )[:10]

    # Top 10 Giảm Cảnh Báo: có dữ liệu quá khứ, (delta_1 < 0 hoặc delta_4 < 0), và prev_1 >= 50.0 hoặc current >= 50.0
    top_losers = sorted(
        [
            x for x in delta_list 
            if x['prev_1'] is not None 
            and (x['delta_1'] < 0 or x['delta_4'] < 0) 
            and (x['prev_1'] >= 50.0 or x['current'] >= 50.0)
        ],
        key=lambda x: (x['delta_4'] + x['delta_1'])
    )[:10]

    return top_gainers, top_losers


def _format_delta_cell(val: float) -> str:
    """Định dạng ô hiển thị Delta có kèm icon màu sắc trực quan"""
    if val > 0:
        return f"🟢 +{val:<7}"
    elif val < 0:
        return f"🔴 {val:<8}"
    else:
        return f"⚪ {val:<8}"


def print_delta_tables(
    top_gainers: List[Dict],
    top_losers: List[Dict],
    has_history: Optional[bool] = None,
    db_path: Optional[str] = None
) -> None:
    """Hiển thị định dạng bảng phân tích Delta ra Terminal Log"""
    if has_history is None:
        if top_gainers or top_losers:
            has_history = True
        else:
            target_db = db_path or DB_PATH
            try:
                if os.path.exists(target_db):
                    with sqlite3.connect(target_db) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(DISTINCT scan_time) FROM score_history")
                        row = cursor.fetchone()
                        has_history = bool(row and row[0] >= 2)
                else:
                    has_history = False
            except Exception:
                has_history = False

    print("\n" + "=" * 110)
    print("📊 BẢNG THEO DÕI BIẾN THIÊN TÍN NHIỆM (DELTA AUDIT)")
    print("=" * 110)

    print("\n📈 [TOP 10 TĂNG ĐIỂM UY TÍN] (Dòng tiền gia tốc / Thoát đáy vĩ mô)")
    print("-" * 110)
    print(f"{'Mã':<8} | {'Tier':<22} | {'Hiện Tại':<9} | {'Δ 1-Phiên':<12} | {'Δ 4-Phiên':<12} | {'Đánh Giá':<20}")
    print("-" * 110)
    if not top_gainers:
        if has_history:
            print("  (Không có mã uy tín nào (>= 50đ) ghi nhận tăng điểm trong các phiên gần nhất)")
        else:
            print("  (Chưa đủ dữ liệu tối thiểu 2 phiên quét để tính toán biến thiên)")
    else:
        for item in top_gainers:
            c1 = _format_delta_cell(item['delta_1'])
            c4 = _format_delta_cell(item['delta_4'])
            print(f"{item['symbol']:<8} | {item['tier']:<22} | {item['current']:<9.1f} | {c1} | {c4} | {item['eval']:<20}")

    print("\n📉 [TOP 10 GIẢM ĐIỂM CẢNH BÁO] (Lực mua suy yếu / Bị phân phối)")
    print("-" * 110)
    print(f"{'Mã':<8} | {'Tier':<22} | {'Hiện Tại':<9} | {'Δ 1-Phiên':<12} | {'Δ 4-Phiên':<12} | {'Đánh Giá':<20}")
    print("-" * 110)
    if not top_losers:
        if has_history:
            print("  (Không có mã điểm cao nào ghi nhận suy giảm bất thường)")
        else:
            print("  (Chưa đủ dữ liệu tối thiểu 2 phiên quét để tính toán biến thiên)")
    else:
        for item in top_losers:
            c1 = _format_delta_cell(item['delta_1'])
            c4 = _format_delta_cell(item['delta_4'])
            print(f"{item['symbol']:<8} | {item['tier']:<22} | {item['current']:<9.1f} | {c1} | {c4} | {item['eval']:<20}")
    print("=" * 110)

    # 🚀 Thiết lập lệnh tự động cho Top 3 mã tăng nhiều nhất (tương tự như Thiết lập lệnh Tier S)
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
            print("=" * 110)
    print("\n")
