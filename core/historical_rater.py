"""
Module: core/historical_rater.py
Dự án: BinaC4
Mục đích: MODULE TRUY VẤN LỊCH SỬ 14 NGÀY (HISTORICAL RATER)
Phiên bản: Bóc tách minh bạch chi tiết Nền Móng (Rebalance + 3D Strict + Bảng 3A/3B)
Hoạt động độc lập, không can thiệp trực tiếp vào hàm main().
"""

import os
import sys
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

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

logger = logging.getLogger("HistoricalRater")


class HistoricalDataQuery:
    """
    Module: TRUY VẤN DỮ LIỆU 14 NGÀY (Historical Rater)
    Mục đích: Truy xuất lịch sử quét của các mã trong 14 ngày, chấm điểm tín nhiệm 
    bóc tách chi tiết "bộ rễ" Nền móng (Rebalance / 3D Strict / 3A / 3B), Sức đẩy (DC2/3/4) và Cashflow (DC5).
    """

    def __init__(self, db_path: str = "data/binac4_history.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self.create_tables()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def create_tables(self):
        """Khởi tạo bảng daily_metrics theo đặc tả kỹ thuật."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    date TEXT,
                    symbol TEXT,
                    b3a_score REAL DEFAULT 0,
                    b3b_score REAL DEFAULT 0,
                    rebalance_score REAL DEFAULT 0,
                    strict_3d_score REAL DEFAULT 0,
                    dc2_sniper_count INTEGER DEFAULT 0,
                    dc3_breakout_count INTEGER DEFAULT 0,
                    dc4_hottrend_count INTEGER DEFAULT 0,
                    dc5_pingpong_score INTEGER DEFAULT 0,
                    penalty_level_3 INTEGER DEFAULT 0,
                    penalty_xaray INTEGER DEFAULT 0,
                    penalty_fakeout INTEGER DEFAULT 0,
                    PRIMARY KEY (date, symbol)
                )
            ''')
            cols = [col[1] for col in cursor.execute("PRAGMA table_info(daily_metrics)").fetchall()]
            if 'strict_3d_score' not in cols:
                cursor.execute("ALTER TABLE daily_metrics ADD COLUMN strict_3d_score REAL DEFAULT 0")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_symbol_date ON daily_metrics(symbol, date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_date ON daily_metrics(date)")
            conn.commit()

    def record_daily_metric(self, date_str: str, symbol: str, metrics: Dict[str, Any]):
        """
        Ghi nhận hoặc cập nhật chỉ số của một mã trong ngày cụ thể vào daily_metrics.
        """
        clean_sym = symbol.replace('/USDT', '').replace('USDT', '').strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO daily_metrics (
                    date, symbol,
                    b3a_score, b3b_score, rebalance_score, strict_3d_score,
                    dc2_sniper_count, dc3_breakout_count, dc4_hottrend_count,
                    dc5_pingpong_score,
                    penalty_level_3, penalty_xaray, penalty_fakeout
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, symbol) DO UPDATE SET
                    b3a_score = MAX(daily_metrics.b3a_score, excluded.b3a_score),
                    b3b_score = MAX(daily_metrics.b3b_score, excluded.b3b_score),
                    rebalance_score = CASE WHEN excluded.rebalance_score > 0 THEN excluded.rebalance_score ELSE daily_metrics.rebalance_score END,
                    strict_3d_score = CASE WHEN excluded.strict_3d_score > 0 THEN excluded.strict_3d_score ELSE daily_metrics.strict_3d_score END,
                    dc2_sniper_count = daily_metrics.dc2_sniper_count + excluded.dc2_sniper_count,
                    dc3_breakout_count = daily_metrics.dc3_breakout_count + excluded.dc3_breakout_count,
                    dc4_hottrend_count = daily_metrics.dc4_hottrend_count + excluded.dc4_hottrend_count,
                    dc5_pingpong_score = daily_metrics.dc5_pingpong_score + excluded.dc5_pingpong_score,
                    penalty_level_3 = daily_metrics.penalty_level_3 + excluded.penalty_level_3,
                    penalty_xaray = daily_metrics.penalty_xaray + excluded.penalty_xaray,
                    penalty_fakeout = daily_metrics.penalty_fakeout + excluded.penalty_fakeout
            ''', (
                date_str, clean_sym,
                metrics.get('b3a_score', 0.0),
                metrics.get('b3b_score', 0.0),
                metrics.get('rebalance_score', 0.0),
                metrics.get('strict_3d_score', 0.0),
                metrics.get('dc2_sniper_count', 0),
                metrics.get('dc3_breakout_count', 0),
                metrics.get('dc4_hottrend_count', 0),
                metrics.get('dc5_pingpong_score', 0),
                metrics.get('penalty_level_3', 0),
                metrics.get('penalty_xaray', 0),
                metrics.get('penalty_fakeout', 0)
            ))
            conn.commit()

    def sync_from_signal_logs(self) -> int:
        """
        Tự động đồng bộ và tổng hợp dữ liệu lịch sử đã quét từ bảng signal_logs sang daily_metrics.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            table_check = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_logs'"
            ).fetchone()
            if not table_check:
                return 0

            query = '''
                SELECT 
                    substr(timestamp, 1, 10) as log_date,
                    symbol,
                    MAX(CASE WHEN engine = '3A' THEN score ELSE 0 END) as b3a_sc,
                    MAX(CASE WHEN engine = '3B' THEN score ELSE 0 END) as b3b_sc,
                    AVG(CASE WHEN engine = 'REBALANCE' THEN score ELSE NULL END) as reb_sc,
                    AVG(CASE WHEN engine = '3D_STRICT' THEN score ELSE NULL END) as strict_sc,
                    SUM(CASE WHEN engine = 'DC2' THEN 1 ELSE 0 END) as dc2_cnt,
                    SUM(CASE WHEN engine = 'DC3' THEN 1 ELSE 0 END) as dc3_cnt,
                    SUM(CASE WHEN engine = 'DC4' THEN 1 ELSE 0 END) as dc4_cnt,
                    SUM(CASE WHEN engine = 'DC5' THEN 1 ELSE 0 END) as dc5_sc,
                    SUM(CASE WHEN engine = 'PENALTY_LVL3' THEN 1 ELSE 0 END) as p_lvl3,
                    SUM(CASE WHEN engine = 'PENALTY_XARAY' THEN 1 ELSE 0 END) as p_xaray,
                    SUM(CASE WHEN engine = 'PENALTY_FAKEOUT' THEN 1 ELSE 0 END) as p_fakeout
                FROM signal_logs
                WHERE symbol IS NOT NULL AND timestamp IS NOT NULL
                GROUP BY log_date, symbol
            '''
            cursor.execute(query)
            rows = cursor.fetchall()
            if not rows:
                return 0

            upsert_query = '''
                INSERT INTO daily_metrics (
                    date, symbol,
                    b3a_score, b3b_score, rebalance_score, strict_3d_score,
                    dc2_sniper_count, dc3_breakout_count, dc4_hottrend_count,
                    dc5_pingpong_score,
                    penalty_level_3, penalty_xaray, penalty_fakeout
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, symbol) DO UPDATE SET
                    b3a_score = MAX(daily_metrics.b3a_score, excluded.b3a_score),
                    b3b_score = MAX(daily_metrics.b3b_score, excluded.b3b_score),
                    dc2_sniper_count = MAX(daily_metrics.dc2_sniper_count, excluded.dc2_sniper_count),
                    dc3_breakout_count = MAX(daily_metrics.dc3_breakout_count, excluded.dc3_breakout_count),
                    dc4_hottrend_count = MAX(daily_metrics.dc4_hottrend_count, excluded.dc4_hottrend_count),
                    dc5_pingpong_score = MAX(daily_metrics.dc5_pingpong_score, excluded.dc5_pingpong_score),
                    penalty_level_3 = MAX(daily_metrics.penalty_level_3, excluded.penalty_level_3),
                    penalty_xaray = MAX(daily_metrics.penalty_xaray, excluded.penalty_xaray),
                    penalty_fakeout = MAX(daily_metrics.penalty_fakeout, excluded.penalty_fakeout)
            '''
            
            records = []
            for r in rows:
                reb_val = r[4] if r[4] is not None else 0.0
                strict_val = r[5] if r[5] is not None else 0.0
                records.append((
                    r[0], r[1],
                    r[2] or 0.0, r[3] or 0.0, reb_val, strict_val,
                    r[6] or 0, r[7] or 0, r[8] or 0,
                    r[9] or 0,
                    r[10] or 0, r[11] or 0, r[12] or 0
                ))
                
            cursor.executemany(upsert_query, records)
            conn.commit()
            return len(records)

    def sync_from_log_files(self, workspace_dir: str = ".") -> int:
        """
        Quét và nạp dữ liệu định lượng (Rebalance, Bảng 3A, Bảng 3B, 3D Strict)
        từ các file log hiện có trong thư mục dự án (BinaC4_latest_log.txt, main_run_3b_utf8.txt, main_output*.txt).
        """
        import re
        import glob
        
        parsed_cnt = 0
        log_files = (
            glob.glob(os.path.join(workspace_dir, "*.txt")) +
            glob.glob(os.path.join(workspace_dir, "scratch", "*.txt"))
        )
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            for file_path in log_files:
                fname = os.path.basename(file_path).lower()
                if "requirements" in fname or "headers" in fname:
                    continue
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as fp:
                        content = fp.read()
                        if not content:
                            continue
                        
                        # Xác định ngày từ header log hoặc fallback
                        m_date = re.search(r'2026-\d{2}-\d{2}', content[:500])
                        log_date = m_date.group(0) if m_date else "2026-10-10"
                        
                        # 1. Trích xuất Bảng Rebalance / Spot Grid (cột TỔNG)
                        # Định dạng: #1   | XLM     | ... | 74.8  |
                        reb_matches = re.findall(r'#\d+\s*\|\s*([A-Za-z0-9]+)\s*\|[^|]+\|[^|]+\|\s*([\d.]+)\s*\|', content)
                        for sym, sc_str in reb_matches:
                            sc = float(sc_str)
                            clean_sym = sym.upper().replace('/USDT', '').replace('USDT', '').strip()
                            cursor.execute('''
                                INSERT INTO daily_metrics (date, symbol, rebalance_score)
                                VALUES (?, ?, ?)
                                ON CONFLICT(date, symbol) DO UPDATE SET
                                    rebalance_score = MAX(daily_metrics.rebalance_score, excluded.rebalance_score)
                            ''', (log_date, clean_sym, sc))
                            parsed_cnt += 1

                        # 2. Trích xuất Bảng 3B (Điểm Sóng 3D)
                        lines = content.splitlines()
                        for line in lines:
                            # Match rows like: ARPA | 0.01306 | 95.0 | ...
                            m3b = re.match(r'^\s*([A-Za-z0-9]+)\s*\|\s*[\d.]+\s*\|\s*([\d.]+)\s*\|', line)
                            if m3b:
                                sym3b, sc3b = m3b.groups()
                                clean_sym = sym3b.upper().replace('/USDT', '').replace('USDT', '').strip()
                                score_val = float(sc3b)
                                if score_val >= 20.0: # Loại bỏ các dòng nhiễu
                                    cursor.execute('''
                                        INSERT INTO daily_metrics (date, symbol, b3b_score)
                                        VALUES (?, ?, ?)
                                        ON CONFLICT(date, symbol) DO UPDATE SET
                                            b3b_score = MAX(daily_metrics.b3b_score, excluded.b3b_score)
                                    ''', (log_date, clean_sym, score_val))
                                    parsed_cnt += 1

                        # 3. Trích xuất tag [3D STRICT] từ các dòng GRID TP2 và WIDE GRID 3D
                        strict_matches = re.findall(r'\[(?:GRID TP2|WIDE GRID 3D)\s*-\s*([A-Za-z0-9]+)\].*?\[3D STRICT.*?\]\s*(\d+)', content)
                        for sym_st, sc_st in strict_matches:
                            clean_sym = sym_st.upper().replace('/USDT', '').replace('USDT', '').strip()
                            cursor.execute('''
                                INSERT INTO daily_metrics (date, symbol, strict_3d_score)
                                VALUES (?, ?, ?)
                                ON CONFLICT(date, symbol) DO UPDATE SET
                                    strict_3d_score = MAX(daily_metrics.strict_3d_score, excluded.strict_3d_score)
                            ''', (log_date, clean_sym, float(sc_st)))
                            parsed_cnt += 1

                except Exception as e:
                    logger.debug(f"Bỏ qua file {file_path} khi parse log: {e}")

            # 4. Kế thừa (Carry-over) điểm Rebalance và 3D Strict cho các mã đã quét trong cùng chu kỳ
            cursor.execute('''
                UPDATE daily_metrics
                SET rebalance_score = (
                    SELECT d2.rebalance_score
                    FROM daily_metrics d2
                    WHERE d2.symbol = daily_metrics.symbol AND d2.rebalance_score > 0
                    ORDER BY d2.date DESC
                    LIMIT 1
                )
                WHERE rebalance_score = 0 OR rebalance_score IS NULL
            ''')
            cursor.execute('''
                UPDATE daily_metrics
                SET strict_3d_score = (
                    SELECT d2.strict_3d_score
                    FROM daily_metrics d2
                    WHERE d2.symbol = daily_metrics.symbol AND d2.strict_3d_score > 0
                    ORDER BY d2.date DESC
                    LIMIT 1
                )
                WHERE strict_3d_score = 0 OR strict_3d_score IS NULL
            ''')
            conn.commit()

        return parsed_cnt

    def sync_all(self, workspace_dir: str = ".") -> Dict[str, int]:
        """
        Đồng bộ toàn diện:
        1. Gom các sự kiện quét DC1..DC5 từ signal_logs sang daily_metrics.
        2. Nạp điểm Rebalance, 3A, 3B, 3D Strict từ các file log quét trong thư mục.
        """
        cnt_logs = self.sync_from_signal_logs()
        cnt_files = self.sync_from_log_files(workspace_dir)
        return {"signal_logs": cnt_logs, "log_files": cnt_files}

    def query_14_day_profile(self, symbol: str, as_of_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Truy vấn dữ liệu 14 ngày gần nhất của 1 mã và trả về Hồ sơ Tín nhiệm.
        Bóc tách chi tiết từng thành phần Nền Móng (Rebalance, 3D Strict, 3A/3B).
        """
        clean_sym = symbol.replace('/USDT', '').replace('USDT', '').strip()
        ref_date = as_of_date or datetime.now()
        fourteen_days_ago = (ref_date - timedelta(days=14)).strftime('%Y-%m-%d')
        ref_date_str = ref_date.strftime('%Y-%m-%d')
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    MAX(b3a_score), MAX(b3b_score), 
                    AVG(rebalance_score), AVG(strict_3d_score),
                    SUM(dc2_sniper_count), SUM(dc3_breakout_count), SUM(dc4_hottrend_count),
                    SUM(dc5_pingpong_score),
                    SUM(penalty_level_3), SUM(penalty_xaray), SUM(penalty_fakeout)
                FROM daily_metrics
                WHERE symbol = ? AND date >= ? AND date <= ?
            ''', (clean_sym, fourteen_days_ago, ref_date_str))
            
            row = cursor.fetchone()
            
            if row is None or (row[0] is None and row[2] is None and row[4] is None):
                return self._format_empty_profile(clean_sym)

            (b3a, b3b, avg_reb, avg_3d_strict, dc2, dc3, dc4, dc5, p_lvl3, p_xaray, p_fakeout) = row
            
            # --- 1. NỀN MÓNG (BUFF 3D STRICT VĨ MÔ - Max 40đ) ---
            # 3D Strict chiếm kịch trần 25đ (hơn 60% tổng sức mạnh Nền Móng)
            score_3d_strict = min(25.0, (avg_3d_strict or 0.0) * 0.25)
            score_reb = min(10.0, (avg_reb or 0.0) * 0.10)             # Rebalance chiếm 10đ
            score_macro_3ab = min(5.0, max(b3a or 0.0, b3b or 0.0) * 0.05) # Bảng 3A/3B chiếm 5đ
            foundation_score = min(40.0, score_3d_strict + score_reb + score_macro_3ab)
            
            # --- 2. ĐỘNG LỰC BỨT PHÁ (SỨC MẠNH CỐT LÕI - Max 40đ) ---
            score_dc2 = min(10.0, (dc2 or 0) * 2.5)             
            score_dc3 = min(15.0, (dc3 or 0) * 3.0)               
            score_dc4 = min(20.0, (dc4 or 0) * 4.0)               # Dòng tiền Hot Trend bạo phát -> Nâng kịch trần 20đ
            momentum_score = min(40.0, score_dc2 + score_dc3 + score_dc4)
            
            # --- 3. CASHFLOW & TRỪNG PHẠT ---
            cashflow_score = min(20.0, (dc5 or 0) * 3.0)          # Nhịp đập PingPong khỏe x3
            penalty_score = ((p_lvl3 or 0) * -15) + ((p_xaray or 0) * -5) + ((p_fakeout or 0) * -10)
            
            total_score = foundation_score + momentum_score + cashflow_score + penalty_score
            total_score = max(0.0, min(100.0, total_score))
            
            tier, action = self._determine_tier(total_score, foundation_score)
            
            return {
                "symbol": clean_sym,
                "total_score": round(total_score, 1),
                "tier": tier,
                "action_recommendation": action,
                "breakdown": {
                    "foundation_40": {
                        "total": round(foundation_score, 1),
                        "3d_strict_score": round(score_3d_strict, 1),
                        "3d_strict_avg": round(avg_3d_strict or 0.0, 1),
                        "rebalance_score": round(score_reb, 1),
                        "rebalance_avg": round(avg_reb or 0.0, 1),
                        "macro_3ab_score": round(score_macro_3ab, 1)
                    },
                    "momentum_40": round(momentum_score, 1),
                    "cashflow_20": round(cashflow_score, 1),
                    "penalties": penalty_score
                }
            }

    def _determine_tier(self, score: float, foundation_score: float):
        # Hệ thống đánh giá dựa trên TỔNG ĐIỂM + Cảnh báo độ rỗng nền móng
        if score >= 80.0:
            return "Tier S (Kim Cương)", "🟢 DUYỆT TỰ ĐỘNG (Đánh Full Volume)"
        elif score >= 60.0:
            # Nếu móng yếu (<15đ) nhưng điểm vẫn cao (60-79) -> Sức đẩy đang quá gắt (FOMO)
            if foundation_score < 15.0:
                return "Tier A- (FOMO Trend)", "🟡 ĐÁNH 50% VOL (Đà tăng gắt nhưng thiếu móng tích lũy 14 ngày)"
            else:
                return "Tier A (Xu Hướng)", "🟢 DUYỆT TỰ ĐỘNG (Cấu trúc ổn định)"
        elif score >= 40.0:
            return "Tier B (Lướt Sóng)", "🟠 ĐÁNH 30% VOL (Bắt Pullback hoặc ăn ngắn)"
        else:
            return "Tier F (Rủi Ro)", "⛔ TỪ CHỐI (Không có đà tăng, điểm quá thấp)"

    def _format_empty_profile(self, symbol: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "total_score": 0.0,
            "tier": "Unknown",
            "action_recommendation": "⛔ TỪ CHỐI (Không có dữ liệu 14 ngày)",
            "breakdown": {
                "foundation_40": {
                    "total": 0.0,
                    "rebalance_score": 0.0,
                    "rebalance_avg": 0.0,
                    "3d_strict_score": 0.0,
                    "3d_strict_avg": 0.0,
                    "macro_3ab_score": 0.0
                },
                "momentum_40": 0.0,
                "cashflow_20": 0.0,
                "penalties": 0
            }
        }

    def query_all_14_day_profiles(self, as_of_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Truy vấn toàn bộ các mã có dữ liệu trong 14 ngày, sắp xếp theo điểm tín nhiệm giảm dần.
        """
        ref_date = as_of_date or datetime.now()
        fourteen_days_ago = (ref_date - timedelta(days=14)).strftime('%Y-%m-%d')
        ref_date_str = ref_date.strftime('%Y-%m-%d')
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT symbol FROM daily_metrics
                WHERE date >= ? AND date <= ?
            ''', (fourteen_days_ago, ref_date_str))
            symbols = [r[0] for r in cursor.fetchall()]

        profiles = [self.query_14_day_profile(sym, as_of_date=ref_date) for sym in symbols]
        profiles.sort(key=lambda x: (x["total_score"], x["breakdown"]["foundation_40"]["total"]), reverse=True)
        return profiles


def render_profiles_table(profiles: List[Dict[str, Any]], title: str = "BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY (PHIÊN BẢN CHI TIẾT NỀN MÓNG)"):
    """
    In bảng hiển thị nâng cấp, thể hiện rõ từng thành phần cấu tạo nên Nền móng vĩ mô:
    TỔNG MÓNG (40đ) | └ Rebalance (15đ) | └ 3D Strict (15đ) | └ 3A/3B (10đ)
    """
    print(f"\n📊 {title}")
    sep = "=" * 143
    line = "-" * 143
    print(sep)
    header = (
        f"{'Hạng':<5} | {'Mã':<7} | {'Tổng':<5} | {'Tier (Phân Hạng)':<21} | {'MÓNG (40)':<9} | "
        f"{'└ 3D Strict (25đ)':<18} | {'└ Rebalance (10đ)':<18} | {'└ 3A/3B (5đ)':<11} | "
        f"{'Đẩy(40)':<7} | {'Tiền(20)':<8} | {'Phạt':<5}"
    )
    print(header)
    print(line)
    
    for idx, p in enumerate(profiles, start=1):
        b = p['breakdown']
        f = b.get('foundation_40', {})
        if isinstance(f, dict):
            f_tot = f.get('total', 0.0)
            s_sc = f.get('3d_strict_score', 0.0)
            s_avg = f.get('3d_strict_avg', 0.0)
            r_sc = f.get('rebalance_score', 0.0)
            r_avg = f.get('rebalance_avg', 0.0)
            m_sc = f.get('macro_3ab_score', 0.0)
            str_3d = f"{s_sc:.1f} (Avg:{s_avg:.0f})"
            str_reb = f"{r_sc:.1f} (Avg:{r_avg:.1f})"
        else:
            f_tot = float(f)
            str_3d = "0.0 (Avg:0)"
            str_reb = "0.0 (Avg:0.0)"
            m_sc = 0.0

        mom = b.get('momentum_40', 0.0)
        cash = b.get('cashflow_20', 0.0)
        pen = b.get('penalties', 0)
        rank_str = f"#{idx}"
        
        row_str = (
            f"{rank_str:<5} | {p['symbol']:<7} | {p['total_score']:<5.1f} | {p['tier']:<21} | {f_tot:<9.1f} | "
            f"{str_3d:<18} | {str_reb:<18} | {m_sc:<11.1f} | "
            f"{mom:<7.1f} | {cash:<8.1f} | {pen:<5}"
        )
        print(row_str)
    print(sep)


def print_14_day_summary_board(
    db_path: str = "data/binac4_history.db",
    df_summary: Optional[Any] = None,
    early_list: Optional[List[Dict[str, Any]]] = None,
    super_wave_results: Optional[List[Dict[str, Any]]] = None,
    top_n: int = 25
) -> List[Dict[str, Any]]:
    """
    Điểm vào chuẩn để hiển thị BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY ngay trước Bảng Rebalance.
    Tự động ghi nhận dữ liệu định lượng của phiên quét hiện tại (df_summary, early_list, super_wave_results)
    vào SQLite daily_metrics, đồng bộ tín hiệu và in bảng xếp hạng.
    """
    try:
        rater = HistoricalDataQuery(db_path)
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        # 1. Ghi nhận điểm Rebalance từ df_summary của phiên quét hiện tại
        if df_summary is not None and not getattr(df_summary, 'empty', True):
            for _, r_reb in df_summary.iterrows():
                sym = str(r_reb.get('Symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip()
                if sym:
                    try:
                        reb_sc = float(r_reb.get('TỔNG', 0.0))
                    except (ValueError, TypeError):
                        reb_sc = 0.0
                    rater.record_daily_metric(today_str, sym, {"rebalance_score": reb_sc})

        # 2. Ghi nhận điểm Bảng 3A từ early_list
        if early_list:
            for ea in early_list:
                sym = str(ea.get('Symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip()
                if sym:
                    try:
                        sc_3a = float(ea.get('Điểm', 0.0))
                    except (ValueError, TypeError):
                        sc_3a = 0.0
                    rater.record_daily_metric(today_str, sym, {"b3a_score": sc_3a})

        # 3. Ghi nhận điểm 3B và 3D Strict từ super_wave_results
        if super_wave_results:
            for sw in super_wave_results:
                sym = str(sw.get('symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip()
                if sym:
                    try:
                        sc_3b = float(sw.get('score', 0.0))
                        sc_3d = float(sw.get('strict_score', 0.0) or (80.0 if sw.get('strict_ok') else 0.0))
                    except (ValueError, TypeError):
                        sc_3b, sc_3d = 0.0, 0.0
                    rater.record_daily_metric(today_str, sym, {
                        "b3b_score": sc_3b,
                        "strict_3d_score": sc_3d
                    })

        # 4. Đồng bộ từ signal_logs sang daily_metrics
        rater.sync_from_signal_logs()

        # 5. Truy vấn toàn bộ hồ sơ 14 ngày
        profiles = rater.query_all_14_day_profiles()
        if profiles:
            display_count = min(top_n, len(profiles))
            render_profiles_table(
                profiles[:display_count],
                f"BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY (TOP {display_count} / {len(profiles)} MÃ)"
            )
        return profiles
    except Exception as e:
        print(f"⚠️ [Historical Rater] Lỗi khi tạo Bảng 14 ngày: {e}")
        return []



if __name__ == "__main__":
    rater = HistoricalDataQuery("data/binac4_history.db")
    
    # 1. Đồng bộ toàn diện: từ signal_logs VÀ từ các file log quét trong dự án
    print("\n⏳ Đang đồng bộ toàn diện dữ liệu (signal_logs + các file log quét)...")
    res = rater.sync_all()
    print(f"✅ Đã đồng bộ {res['signal_logs']} dòng từ signal_logs và nạp {res['log_files']} chỉ số định lượng từ các file log.")
    
    # 2. Truy vấn thử nghiệm với ngày mốc gần nhất trong dữ liệu
    with rater._get_connection() as conn:
        c = conn.cursor()
        max_date_row = c.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
        sample_date_str = max_date_row[0] if max_date_row and max_date_row[0] else datetime.now().strftime('%Y-%m-%d')

    print(f"📅 Ngày mốc tham chiếu: {sample_date_str}")
    ref_dt = datetime.strptime(sample_date_str, '%Y-%m-%d')
    
    profiles = rater.query_all_14_day_profiles(as_of_date=ref_dt)
    render_profiles_table(profiles[:25], f"BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY ({len(profiles)} MÃ)")
