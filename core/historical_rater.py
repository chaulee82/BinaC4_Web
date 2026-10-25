"""
Module: core/historical_rater.py
Dự án: BinaC4
Mục đích: MODULE CHẤM ĐIỂM TÍN NHIỆM & SUY HAO VĨ MÔ (SCORING ENGINE V2.0)
Phiên bản 2.0:
  - Bước 1: Tính Điểm Móng Thô (Raw Base Score - Tối đa 40đ: 3D Strict + Rebalance + 3A/3B + Hội Tụ)
  - Bước 2: Kích Hoạt Suy Hao Cực Đại (Aggressive Decay 4 ngày: 50% - 25% - 15% - 10%)
  - Bước 3: Khuếch Đại Dòng Tiền (Cashflow Multiplier: Tiền >= 15 x1.2, Tiền <= 5 x0.5)
Hoạt động độc lập, không can thiệp trực tiếp vào luồng chính.
"""

import os
import sys
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

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


# ═════════════════════════════════════════════════════════════════════════════
# CÁC HÀM CỐT LÕI THEO ĐẶC TẢ KỸ THUẬT V2.0 (MODULE 1 SPEC FUNCTIONS)
# ═════════════════════════════════════════════════════════════════════════════

def calculate_raw_base_score(coin_data: Dict[str, Any]) -> float:
    """
    [MODULE 1: BƯỚC 1 - TÍNH ĐIỂM MÓNG THÔ - RAW BASE SCORE V2.0]
    Khóa trần 40đ.
    1. 3D Strict: +25đ (Price > MA7_3D, MA7_3D > MA99_3D, ST_4H Xanh)
                  +15đ (Price > MA7_3D, ST_4H Xanh)
    2. Rebalance: +10đ (Top 1-5 Bảng Spot Grid)
                  +5đ  (Top 6-15 Bảng Spot Grid)
                  (0đ nếu dính cờ 'NÉ GRID')
    3. 3A/3B:     +5đ  (Top 5 Bảng 3A hoặc Bảng 3B)
    4. Hội tụ:    +10đ (Lọt [HẠNG S] Bảng Kết Luận Tối Ưu)
                  +5đ  (Lọt [HẠNG A1/A/B])
    """
    raw_base = 0.0
    price = float(coin_data.get("Price", 0.0) or 0.0)
    ma7_3d = float(coin_data.get("MA7_3D", 0.0) or 0.0)
    ma99_3d = float(coin_data.get("MA99_3D", 0.0) or 0.0)
    st_4h = coin_data.get("ST_4H")
    st_4h_green = st_4h in ("Green", "GREEN", True, 1) or bool(coin_data.get("st_4h_green"))

    # 1. 3D Component (Tối đa 25đ - Nội suy tuyến tính từ Điểm Sóng 3B: (Score / 100) * 25)
    b3b_score = float(coin_data.get("b3b_score", 0.0) or 0.0)
    if b3b_score > 0:
        raw_base += min(25.0, (b3b_score / 100.0) * 25.0)
    else:
        # Fallback 3D Strict nếu chưa có bản ghi sóng 3B
        price_ok = (price > ma7_3d) if (price > 0 and ma7_3d > 0) else bool(coin_data.get("price_above_ma7_3d"))
        ma_aligned = (ma7_3d > ma99_3d) if (ma7_3d > 0 and ma99_3d > 0) else bool(coin_data.get("ma7_gt_ma99_3d"))
        if price_ok and st_4h_green:
            if ma_aligned:
                raw_base += 25.0
            else:
                raw_base += 15.0

    # 2. Rebalance
    grid_rank = int(coin_data.get("Grid_Rank", 999) or 999)
    grid_status = str(coin_data.get("Grid_Status", "") or "")
    if "NÉ GRID" not in grid_status:
        if grid_rank <= 5:
            raw_base += 10.0
        elif grid_rank <= 15:
            raw_base += 5.0

    # 3. 3A/3B Radar
    if coin_data.get("In_Top_3A") or coin_data.get("In_Top_3B"):
        raw_base += 5.0

    # 4. Optimal Conclusion (Hội tụ)
    optimal_tier = str(coin_data.get("Optimal_Conclusion_Tier", "") or "")
    if "HẠNG S" in optimal_tier:
        raw_base += 10.0
    elif any(t in optimal_tier for t in ["HẠNG A", "HẠNG B"]):
        raw_base += 5.0

    return min(raw_base, 40.0)


def calculate_aggressive_decay(daily_scores: list) -> float:
    """
    [MODULE 1: BƯỚC 2 - SUY HAO CỰC ĐẠI - AGGRESSIVE DECAY V2.0]
    T-0: 50% | T-1: 25% | T-2: 15% | T-3: 10%
    Chuẩn hóa trọng số nếu số lượng ngày thực tế ít hơn 4 (mã mới list).
    """
    if not daily_scores:
        return 0.0
    weights = [0.50, 0.25, 0.15, 0.10]
    valid_scores = daily_scores[:len(weights)]
    w_sum = sum(weights[:len(valid_scores)])
    if w_sum <= 0:
        return 0.0
    norm_weights = [w / w_sum for w in weights[:len(valid_scores)]]
    return sum(float(s) * w for s, w in zip(valid_scores, norm_weights))


def calculate_final_rating(coin_data: dict) -> float:
    """
    [MODULE 1: BƯỚC 3 - HỆ SỐ KHUẾCH ĐẠI DÒNG TIỀN - CASHFLOW MULTIPLIER V2.0]
    Tiền >= 15 -> x1.2 (Thưởng)
    Tiền <= 5  -> x0.5 (Trừng phạt)
    5 < Tiền < 15 -> x1.0
    """
    diem_mong = calculate_aggressive_decay(coin_data.get("Lich_Su_Mong", []))
    diem_day = calculate_aggressive_decay(coin_data.get("Lich_Su_Day", []))
    diem_tien = calculate_aggressive_decay(coin_data.get("Lich_Su_Tien", []))

    multiplier = 1.0
    if diem_tien >= 15.0:
        multiplier = 1.2
    elif diem_tien <= 5.0:
        multiplier = 0.5

    total = (diem_mong + diem_day + diem_tien) * multiplier
    total = max(0.0, min(100.0, total))
    return round(total, 2)


def calculate_macro_penalties_v2(
    raw_total: float,
    warning_info: Optional[Dict[str, Any]] = None,
    reb_info: Optional[Dict[str, Any]] = None,
    downgrade_info: Optional[Dict[str, Any]] = None,
    d24: float = 0.0,
    d72: float = 0.0
) -> Tuple[float, float, List[Tuple[str, float]]]:
    """
    [TÁI CẤU TRÚC BẢNG TÍN NHIỆM: MACRO PENALTY V2.0]
    Ma Trận Khấu Trừ Vĩ Mô Động (Dynamic Macro Penalty Matrix)
    Triết lý: Graceful Degradation - Đánh tụt 1 hạng thay vì bức tử về 0.

    1. Gãy Supertrend 1D (Cấp 3): -12.0đ
    2. Xả dốc mạnh / Cây thông (Cấp 1): -15.0đ
    3. Suy yếu / Mất bệ đỡ (Cấp 2): -8.0đ (Chỉ lấy max của Cấp 1 hoặc Cấp 2)
    4. Cảnh báo NÉ GRID (Rebalance): -10.0đ
    5. RSI 1H Quá Mua (>= 75.0): -5.0đ

    Returns:
        final_rating, total_penalty, macro_penalties
    """
    macro_penalties: List[Tuple[str, float]] = []
    total_penalty = 0.0

    # 1. Bảng Cảnh Báo Sớm: Cấp 3 Khẩn Cấp (Gãy ST 1D) -> Phạt -12.0đ
    if warning_info:
        w_lvl = int(warning_info.get('level', 0) or 0)
        w_lbl = str(warning_info.get('label', '') or '')
        if w_lvl == 3 or "CẤP 3" in w_lbl:
            macro_penalties.append(("💀 Gãy ST 1D", 12.0))
            total_penalty += 12.0

    # 2. Bảng Suy Giảm: Cấp 1 (-15.0đ) hoặc Cấp 2 (-8.0đ) (Loại trừ nội bộ)
    dg_lvl = str((downgrade_info or {}).get('level', '') or '').upper()
    if d72 <= -20.0 or d24 <= -15.0 or dg_lvl == "CAP_1":
        macro_penalties.append(("🔴 Xả Dốc C1", 15.0))
        total_penalty += 15.0
    elif d24 <= -8.0 or d72 <= -10.0 or dg_lvl == "CAP_2":
        macro_penalties.append(("🟠 Suy Yếu C2", 8.0))
        total_penalty += 8.0

    # 3. Bảng Rebalance: ⛔ NÉ GRID -> Phạt -10.0đ
    if reb_info:
        g_stat = str(reb_info.get('status', '') or '')
        if "NÉ GRID" in g_stat:
            macro_penalties.append(("⛔ Né Grid", 10.0))
            total_penalty += 10.0

    # 4. Bảng Rebalance: ⚠️ RSI 1H Quá Mua (>= 75.0) -> Phạt -5.0đ
    if reb_info:
        rsi_val = float(reb_info.get('rsi_1h', 0.0) or 0.0)
        if rsi_val >= 75.0:
            macro_penalties.append(("⚠️ RSI>75", 5.0))
            total_penalty += 5.0

    final_rating = round(max(0.0, min(100.0, raw_total - total_penalty)), 2)
    return final_rating, total_penalty, macro_penalties


def calculate_grid_priority_queue(
    grid_candidates: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    [PHẦN 1.2: CƠ CHẾ XẾP HẠNG & CẤP VỐN GRID (PRIORITY QUEUE)]
    Dành riêng cho các mã Tier B được phép mở Futures Grid.
    Sắp xếp độ ưu tiên dựa trên 4 biến số (Thang 100đ):
    1. Vị thế lưới (Zone Score - 30%): Giá Live càng gần Lower Price -> Điểm càng cao.
    2. Pingpong (Động cơ 5 - 30%): Tần suất đập nhả và biên độ mượt mà.
    3. Rebalance Score (25%): Độ êm của nến 15m, râu xả, RSI ngắn hạn.
    4. Trust Score 14D (15%): Nền tảng cấu trúc dài hạn.
    """
    scored_candidates = []
    for item in grid_candidates:
        c = dict(item)
        # 1. Zone Score (30%)
        live_price = float(c.get('live_price', c.get('price', c.get('entry', 0.0))) or 0.0)
        lower = float(c.get('lower', c.get('grid_low', 0.0)) or 0.0)
        upper = float(c.get('upper', c.get('grid_up', 0.0)) or 0.0)

        if upper > lower and lower > 0:
            if live_price <= lower:
                zone_score = 100.0  # Mua ở sát hoặc dưới đáy lưới -> Cực tốt
            elif live_price >= upper:
                zone_score = 0.0    # Đỉnh lưới -> Không ưu tiên mua
            else:
                zone_ratio = (upper - live_price) / (upper - lower)
                zone_score = round(zone_ratio * 100.0, 1)
        else:
            zone_score = 50.0

        # 2. Pingpong Động cơ 5 (30%)
        dc5_score = float(c.get('dc5_score', c.get('pingpong_score', c.get('dc5_pingpong_score', 50.0))) or 0.0)
        dc5_score = max(0.0, min(100.0, dc5_score))

        # 3. Rebalance Score (25%)
        reb_score = float(c.get('rebalance_score', c.get('reb_score', 50.0)) or 0.0)
        reb_score = max(0.0, min(100.0, reb_score))

        # 4. Trust Score 14D (15%)
        trust_score = float(c.get('total_score', c.get('uy_tin_score', 50.0)) or 0.0)
        trust_score = max(0.0, min(100.0, trust_score))

        priority_score = round(
            (zone_score * 0.30) +
            (dc5_score * 0.30) +
            (reb_score * 0.25) +
            (trust_score * 0.15),
            2
        )

        c.update({
            'zone_score': zone_score,
            'pingpong_score': dc5_score,
            'rebalance_score': reb_score,
            'trust_score': trust_score,
            'grid_priority_score': priority_score
        })
        scored_candidates.append(c)

    scored_candidates.sort(key=lambda x: x.get('grid_priority_score', 0.0), reverse=True)

    for idx, c in enumerate(scored_candidates, 1):
        c['grid_priority_rank'] = idx
        c['grid_priority_label'] = f"Ưu tiên #{idx}"

    return scored_candidates


# ═════════════════════════════════════════════════════════════════════════════
# LỚP TRUY VẤN VÀ ĐỒNG BỘ DỮ LIỆU SQLITE
# ═════════════════════════════════════════════════════════════════════════════

class HistoricalDataQuery:
    def __init__(self, db_path: str = "data/binac4_history.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self.create_tables()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def create_tables(self):
        """Khởi tạo bảng daily_metrics theo đặc tả kỹ thuật V2.0."""
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
                    raw_base_score REAL DEFAULT 0,
                    grid_rank INTEGER DEFAULT 999,
                    grid_status TEXT DEFAULT '',
                    in_top_3a INTEGER DEFAULT 0,
                    in_top_3b INTEGER DEFAULT 0,
                    optimal_tier TEXT DEFAULT '',
                    price_above_ma7_3d INTEGER DEFAULT 0,
                    ma7_gt_ma99_3d INTEGER DEFAULT 0,
                    st_4h_green INTEGER DEFAULT 0,
                    PRIMARY KEY (date, symbol)
                )
            ''')
            cols = [col[1] for col in cursor.execute("PRAGMA table_info(daily_metrics)").fetchall()]
            new_cols = {
                'strict_3d_score': 'REAL DEFAULT 0',
                'raw_base_score': 'REAL DEFAULT 0',
                'grid_rank': 'INTEGER DEFAULT 999',
                'grid_status': "TEXT DEFAULT ''",
                'in_top_3a': 'INTEGER DEFAULT 0',
                'in_top_3b': 'INTEGER DEFAULT 0',
                'optimal_tier': "TEXT DEFAULT ''",
                'price_above_ma7_3d': 'INTEGER DEFAULT 0',
                'ma7_gt_ma99_3d': 'INTEGER DEFAULT 0',
                'st_4h_green': 'INTEGER DEFAULT 0',
                'money_flow_score': 'REAL DEFAULT 0',
                'dc2_score': 'REAL DEFAULT 0',
                'dc3_score': 'REAL DEFAULT 0',
                'dc4_score': 'REAL DEFAULT 0',
                'dc5_score': 'REAL DEFAULT 0',
                'dc5_rank': 'INTEGER DEFAULT 999'
            }
            for col_name, col_def in new_cols.items():
                if col_name not in cols:
                    try:
                        cursor.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col_name} {col_def}")
                    except Exception as e:
                        logger.debug(f"Col {col_name} check: {e}")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_symbol_date ON daily_metrics(symbol, date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_date ON daily_metrics(date)")

            # Khởi tạo bảng session_scores theo chuẩn V2.0 cho tần suất quét 30 phút
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS session_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    mong_score REAL DEFAULT 0.0,
                    day_score REAL DEFAULT 0.0,
                    tien_score REAL DEFAULT 0.0
                )
            ''')
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sess_sym_ts ON session_scores(symbol, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sess_ts ON session_scores(timestamp)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sess_unique ON session_scores(timestamp, symbol)")
            # Khởi tạo bảng engine_3b_history lưu vết toàn bộ dữ liệu Bảng 3B
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS engine_3b_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    score REAL NOT NULL,
                    rail_score REAL DEFAULT 0,
                    cross_score REAL DEFAULT 0,
                    boll_score REAL DEFAULT 0,
                    flow_score REAL DEFAULT 0,
                    bonus REAL DEFAULT 0,
                    penalty REAL DEFAULT 0,
                    mode TEXT DEFAULT '',
                    price REAL DEFAULT 0,
                    ma7 REAL DEFAULT 0,
                    sl REAL DEFAULT 0,
                    vol_ratio REAL DEFAULT 0
                )
            ''')
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_3b_sym_time ON engine_3b_history (symbol, scan_time DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_3b_scan_time ON engine_3b_history (scan_time DESC)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_3b_unique ON engine_3b_history (scan_time, symbol)")
            conn.commit()

    def query_latest_3b_score(self, symbol: str) -> float:
        """Truy vấn điểm sóng 3B gần nhất của mã từ engine_3b_history."""
        clean_sym = symbol.replace('/USDT', '').replace('USDT', '').strip().upper()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                row = cursor.execute('''
                    SELECT score FROM engine_3b_history
                    WHERE symbol = ?
                    ORDER BY scan_time DESC
                    LIMIT 1
                ''', (clean_sym,)).fetchone()
                if row and row[0] is not None:
                    return float(row[0])
        except Exception as e:
            logger.debug(f"[3B-Query] {clean_sym}: {e}")
        return 0.0

    def record_session_score(self, timestamp: str, symbol: str, mong: float, day: float, tien: float):
        """
        Ghi nhận điểm thành phần thô của một phiên quét 30 phút vào session_scores (V2.0 Spec).
        """
        clean_sym = symbol.replace('/USDT', '').replace('USDT', '').strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO session_scores (timestamp, symbol, mong_score, day_score, tien_score)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(timestamp, symbol) DO UPDATE SET
                    mong_score = excluded.mong_score,
                    day_score = excluded.day_score,
                    tien_score = excluded.tien_score
            ''', (timestamp, clean_sym, round(float(mong), 2), round(float(day), 2), round(float(tien), 2)))
            conn.commit()

    def record_daily_metric(self, date_str: str, symbol: str, metrics: Dict[str, Any]):
        """
        Ghi nhận hoặc cập nhật chỉ số của một mã trong ngày cụ thể vào daily_metrics.
        Snapshot overwrite: Ghi đè bằng dữ liệu quét mới nhất thay vì cộng dồn (V2.0).
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
                    penalty_level_3, penalty_xaray, penalty_fakeout,
                    raw_base_score, grid_rank, grid_status, in_top_3a, in_top_3b,
                    optimal_tier, price_above_ma7_3d, ma7_gt_ma99_3d, st_4h_green,
                    dc5_rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, symbol) DO UPDATE SET
                    b3a_score = CASE WHEN excluded.b3a_score > 0 THEN excluded.b3a_score ELSE daily_metrics.b3a_score END,
                    b3b_score = CASE WHEN excluded.b3b_score > 0 THEN excluded.b3b_score ELSE daily_metrics.b3b_score END,
                    rebalance_score = CASE WHEN excluded.rebalance_score > 0 THEN excluded.rebalance_score ELSE daily_metrics.rebalance_score END,
                    strict_3d_score = CASE WHEN excluded.strict_3d_score > 0 THEN excluded.strict_3d_score ELSE daily_metrics.strict_3d_score END,
                    dc2_sniper_count = CASE WHEN excluded.dc2_sniper_count > 0 THEN excluded.dc2_sniper_count ELSE daily_metrics.dc2_sniper_count END,
                    dc3_breakout_count = CASE WHEN excluded.dc3_breakout_count > 0 THEN excluded.dc3_breakout_count ELSE daily_metrics.dc3_breakout_count END,
                    dc4_hottrend_count = CASE WHEN excluded.dc4_hottrend_count > 0 THEN excluded.dc4_hottrend_count ELSE daily_metrics.dc4_hottrend_count END,
                    dc5_pingpong_score = CASE WHEN excluded.dc5_pingpong_score > 0 THEN excluded.dc5_pingpong_score ELSE daily_metrics.dc5_pingpong_score END,
                    penalty_level_3 = CASE WHEN excluded.penalty_level_3 > 0 THEN excluded.penalty_level_3 ELSE daily_metrics.penalty_level_3 END,
                    penalty_xaray = CASE WHEN excluded.penalty_xaray > 0 THEN excluded.penalty_xaray ELSE daily_metrics.penalty_xaray END,
                    penalty_fakeout = CASE WHEN excluded.penalty_fakeout > 0 THEN excluded.penalty_fakeout ELSE daily_metrics.penalty_fakeout END,
                    raw_base_score = CASE WHEN excluded.raw_base_score > 0 THEN excluded.raw_base_score ELSE daily_metrics.raw_base_score END,
                    grid_rank = CASE WHEN excluded.grid_rank < 999 THEN excluded.grid_rank ELSE daily_metrics.grid_rank END,
                    grid_status = CASE WHEN excluded.grid_status != '' THEN excluded.grid_status ELSE daily_metrics.grid_status END,
                    in_top_3a = CASE WHEN excluded.in_top_3a > 0 THEN excluded.in_top_3a ELSE daily_metrics.in_top_3a END,
                    in_top_3b = CASE WHEN excluded.in_top_3b > 0 THEN excluded.in_top_3b ELSE daily_metrics.in_top_3b END,
                    optimal_tier = CASE WHEN excluded.optimal_tier != '' THEN excluded.optimal_tier ELSE daily_metrics.optimal_tier END,
                    price_above_ma7_3d = CASE WHEN excluded.price_above_ma7_3d IS NOT NULL THEN excluded.price_above_ma7_3d ELSE daily_metrics.price_above_ma7_3d END,
                    ma7_gt_ma99_3d = CASE WHEN excluded.ma7_gt_ma99_3d IS NOT NULL THEN excluded.ma7_gt_ma99_3d ELSE daily_metrics.ma7_gt_ma99_3d END,
                    st_4h_green = CASE WHEN excluded.st_4h_green IS NOT NULL THEN excluded.st_4h_green ELSE daily_metrics.st_4h_green END,
                    dc5_rank = CASE WHEN excluded.dc5_rank < 999 THEN excluded.dc5_rank ELSE daily_metrics.dc5_rank END
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
                metrics.get('penalty_fakeout', 0),
                metrics.get('raw_base_score', 0.0),
                metrics.get('grid_rank', 999),
                metrics.get('grid_status', ''),
                metrics.get('in_top_3a', 0),
                metrics.get('in_top_3b', 0),
                metrics.get('optimal_tier', ''),
                metrics.get('price_above_ma7_3d', 0),
                metrics.get('ma7_gt_ma99_3d', 0),
                metrics.get('st_4h_green', 0),
                metrics.get('dc5_rank', 999)
            ))
            conn.commit()

    def sync_from_signal_logs(self) -> int:
        """
        Đồng bộ và tổng hợp dữ liệu lịch sử đã quét từ bảng signal_logs sang daily_metrics.
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
                    MAX(CASE WHEN engine = '3A' AND score >= 60.0 THEN score ELSE 0 END) as b3a_sc,
                    MAX(CASE WHEN engine = '3B' AND score >= 70.0 THEN score ELSE 0 END) as b3b_sc,
                    AVG(CASE WHEN engine = 'REBALANCE' THEN score ELSE NULL END) as reb_sc,
                    AVG(CASE WHEN engine = '3D_STRICT' THEN score ELSE NULL END) as strict_sc,
                    SUM(CASE WHEN engine = 'DC2' AND score >= 60.0 THEN 1 ELSE 0 END) as dc2_cnt,
                    SUM(CASE WHEN engine = 'DC3' AND score >= 60.0 THEN 1 ELSE 0 END) as dc3_cnt,
                    SUM(CASE WHEN engine = 'DC4' AND score >= 60.0 THEN 1 ELSE 0 END) as dc4_cnt,
                    SUM(CASE WHEN engine = 'DC5' AND score >= 25.0 THEN 1 ELSE 0 END) as dc5_sc,
                    SUM(CASE WHEN engine = 'PENALTY_LVL3' THEN 1 ELSE 0 END) as p_lvl3,
                    SUM(CASE WHEN engine = 'PENALTY_XARAY' THEN 1 ELSE 0 END) as p_xaray,
                    SUM(CASE WHEN engine = 'PENALTY_FAKEOUT' THEN 1 ELSE 0 END) as p_fakeout,
                    MAX(CASE WHEN engine = 'OPTIMAL_CONVERGENCE' THEN action ELSE '' END) as opt_tier,
                    MAX(CASE WHEN action LIKE '%TIỀN BẠO PHÁT%' THEN 1 ELSE 0 END) as has_mf,
                    MAX(CASE WHEN engine = 'DC2' AND score >= 60.0 THEN score ELSE 0 END) as dc2_max_sc,
                    MAX(CASE WHEN engine = 'DC3' AND score >= 60.0 THEN score ELSE 0 END) as dc3_max_sc,
                    MAX(CASE WHEN engine = 'DC4' AND score >= 60.0 THEN score ELSE 0 END) as dc4_max_sc,
                    MAX(CASE WHEN engine = 'DC5' AND score >= 25.0 THEN score ELSE 0 END) as dc5_max_sc
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
                    penalty_level_3, penalty_xaray, penalty_fakeout,
                    optimal_tier, money_flow_score,
                    dc2_score, dc3_score, dc4_score, dc5_score,
                    dc5_rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, symbol) DO UPDATE SET
                    b3a_score = MAX(daily_metrics.b3a_score, excluded.b3a_score),
                    b3b_score = MAX(daily_metrics.b3b_score, excluded.b3b_score),
                    dc2_sniper_count = MAX(daily_metrics.dc2_sniper_count, excluded.dc2_sniper_count),
                    dc3_breakout_count = MAX(daily_metrics.dc3_breakout_count, excluded.dc3_breakout_count),
                    dc4_hottrend_count = MAX(daily_metrics.dc4_hottrend_count, excluded.dc4_hottrend_count),
                    dc5_pingpong_score = MAX(daily_metrics.dc5_pingpong_score, excluded.dc5_pingpong_score),
                    penalty_level_3 = MAX(daily_metrics.penalty_level_3, excluded.penalty_level_3),
                    penalty_xaray = MAX(daily_metrics.penalty_xaray, excluded.penalty_xaray),
                    penalty_fakeout = MAX(daily_metrics.penalty_fakeout, excluded.penalty_fakeout),
                    optimal_tier = CASE WHEN excluded.optimal_tier != '' THEN excluded.optimal_tier ELSE daily_metrics.optimal_tier END,
                    money_flow_score = MAX(daily_metrics.money_flow_score, excluded.money_flow_score),
                    dc2_score = MAX(daily_metrics.dc2_score, excluded.dc2_score),
                    dc3_score = MAX(daily_metrics.dc3_score, excluded.dc3_score),
                    dc4_score = MAX(daily_metrics.dc4_score, excluded.dc4_score),
                    dc5_score = MAX(daily_metrics.dc5_score, excluded.dc5_score),
                    dc5_rank = CASE WHEN excluded.dc5_rank < 999 THEN excluded.dc5_rank ELSE daily_metrics.dc5_rank END
            '''
            
            # Xếp hạng rank DC5 theo từng ngày (Top 1-5, Top 6-15)
            date_dc5_map = {}
            for r in rows:
                log_d = r[0]
                s_sym = r[1]
                dc5_val = float(r[18] or 0.0) if len(r) > 18 else 0.0
                if dc5_val > 0:
                    date_dc5_map.setdefault(log_d, []).append((s_sym, dc5_val))
            
            date_sym_dc5_rank = {}
            for log_d, items in date_dc5_map.items():
                items.sort(key=lambda x: x[1], reverse=True)
                for rnk, (s_sym, _) in enumerate(items):
                    date_sym_dc5_rank[(log_d, s_sym)] = rnk + 1

            records = []
            for r in rows:
                reb_val = r[4] if r[4] is not None else 0.0
                strict_val = r[5] if r[5] is not None else 0.0
                opt_val = str(r[13]) if len(r) > 13 and r[13] is not None else ''
                has_mf_val = float(r[14] or 0.0) if len(r) > 14 else 0.0
                dc2_sc_val = float(r[15] or 0.0) if len(r) > 15 else 0.0
                dc3_sc_val = float(r[16] or 0.0) if len(r) > 16 else 0.0
                dc4_sc_val = float(r[17] or 0.0) if len(r) > 17 else 0.0
                dc5_sc_val = float(r[18] or 0.0) if len(r) > 18 else 0.0
                dc5_rnk = date_sym_dc5_rank.get((r[0], r[1]), 999)
                records.append((
                    r[0], r[1],
                    r[2] or 0.0, r[3] or 0.0, reb_val, strict_val,
                    r[6] or 0, r[7] or 0, r[8] or 0,
                    r[9] or 0,
                    r[10] or 0, r[11] or 0, r[12] or 0,
                    opt_val, has_mf_val,
                    dc2_sc_val, dc3_sc_val, dc4_sc_val, dc5_sc_val,
                    dc5_rnk
                ))
                
            cursor.executemany(upsert_query, records)
            conn.commit()
            conn.commit()
            return len(records)

    def get_latest_convergence_tier(self, symbol: str) -> str:
        """Lấy kết luận tối ưu (Hội tụ) gần nhất của mã từ signal_logs hoặc daily_metrics."""
        clean_sym = symbol.upper().replace('/USDT', '').replace('USDT', '').strip()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT action FROM signal_logs
                    WHERE symbol = ? AND engine = 'OPTIMAL_CONVERGENCE'
                    ORDER BY timestamp DESC LIMIT 1
                """, (clean_sym,))
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0]

                cursor.execute("""
                    SELECT optimal_tier FROM daily_metrics
                    WHERE symbol = ? AND optimal_tier != ''
                    ORDER BY date DESC LIMIT 1
                """, (clean_sym,))
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0]
        except Exception as e:
            logger.debug(f"Lỗi lấy convergence tier cho {symbol}: {e}")
        return ""

    def sync_from_log_files(self, workspace_dir: str = ".") -> int:
        """
        Quét và nạp dữ liệu từ các file log quét.
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
                        
                        m_date = re.search(r'2026-\d{2}-\d{2}', content[:500])
                        log_date = m_date.group(0) if m_date else "2026-10-10"
                        
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

                        lines = content.splitlines()
                        for line in lines:
                            m3b = re.match(r'^\s*([A-Za-z0-9]+)\s*\|\s*[\d.]+\s*\|\s*([\d.]+)\s*\|', line)
                            if m3b:
                                sym3b, sc3b = m3b.groups()
                                clean_sym = sym3b.upper().replace('/USDT', '').replace('USDT', '').strip()
                                score_val = float(sc3b)
                                if score_val >= 20.0:
                                    cursor.execute('''
                                        INSERT INTO daily_metrics (date, symbol, b3b_score)
                                        VALUES (?, ?, ?)
                                        ON CONFLICT(date, symbol) DO UPDATE SET
                                            b3b_score = MAX(daily_metrics.b3b_score, excluded.b3b_score)
                                    ''', (log_date, clean_sym, score_val))
                                    parsed_cnt += 1

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

            conn.commit()

        return parsed_cnt

    def sync_all(self, workspace_dir: str = ".") -> Dict[str, int]:
        cnt_logs = self.sync_from_signal_logs()
        cnt_files = self.sync_from_log_files(workspace_dir)
        return {"signal_logs": cnt_logs, "log_files": cnt_files}

    @staticmethod
    def _calc_scores_from_daily_metric_row(r) -> Tuple[float, float, float]:
        """
        Tính điểm thô (Móng, Đẩy, Tiền) cho 1 bản ghi daily_metrics.
        """
        (
            _, raw_base, b3a, b3b, reb, strict_3d,
            dc2, dc3, dc4, dc5,
            p_lvl3, p_xaray, p_fakeout,
            g_rank, g_status, in_3a, in_3b, opt_tier,
            p_ma7, ma_gt, st_4h,
            mf_sc, dc2_sc_val, dc3_sc_val, dc4_sc_val, dc5_sc_val
        ) = r[:26]
        dc5_rnk_val = r[26] if len(r) > 26 else 999

        # 1. Điểm Móng
        if raw_base and raw_base > 0:
            day_base = float(raw_base)
        else:
            sc_strict = 0.0
            if b3b and float(b3b) > 0:
                sc_strict = min(25.0, (float(b3b) / 100.0) * 25.0)
            elif strict_3d and strict_3d >= 70:
                sc_strict = 25.0
            elif strict_3d and strict_3d >= 50:
                sc_strict = 15.0
            elif p_ma7 and st_4h:
                sc_strict = 25.0 if ma_gt else 15.0

            sc_reb = 0.0
            if "NÉ GRID" not in (g_status or ''):
                if (g_rank or 999) <= 5 or (reb and reb >= 65):
                    sc_reb = 10.0
                elif (g_rank or 999) <= 15 or (reb and reb >= 45):
                    sc_reb = 5.0

            sc_3ab = 5.0 if (in_3a or in_3b or (b3a and b3a >= 50) or (b3b and b3b >= 50)) else 0.0
            sc_opt = 10.0 if "HẠNG S" in (opt_tier or '') else (5.0 if any(t in (opt_tier or '') for t in ["HẠNG A", "HẠNG B"]) else 0.0)
            day_base = min(40.0, sc_strict + sc_reb + sc_3ab + sc_opt)

        # 2. Điểm Đẩy (Tối đa 40đ: DC2 10đ, DC3 15đ, DC4 15đ) - Yêu cầu điểm sàn >= 60.0đ
        sc_dc2 = min(10.0, max(5.0, (dc2_sc_val / 100.0) * 10.0)) if dc2_sc_val >= 60.0 else (min(10.0, (dc2 or 0) * 5.0) if (dc2 or 0) > 0 else 0.0)
        sc_dc3 = min(15.0, max(8.0, (dc3_sc_val / 100.0) * 15.0)) if dc3_sc_val >= 60.0 else (min(15.0, (dc3 or 0) * 8.0) if (dc3 or 0) > 0 else 0.0)
        sc_dc4 = min(15.0, max(8.0, (dc4_sc_val / 100.0) * 15.0)) if dc4_sc_val >= 60.0 else (min(15.0, (dc4 or 0) * 8.0) if (dc4 or 0) > 0 else 0.0)
        day_push = min(40.0, sc_dc2 + sc_dc3 + sc_dc4)

        # 3. Điểm Tiền (Tối đa 20đ)
        pen = ((p_lvl3 or 0) * -15) + ((p_xaray or 0) * -5) + ((p_fakeout or 0) * -10)
        cash_sum = 0.0
        if (mf_sc or 0) > 0:
            cash_sum += 10.0
        if sc_dc3 > 0:
            cash_sum += 5.0
        # Động cơ 5 phân tầng: Top 1-5 nhận 10đ, Top 6-15 nhận 5đ đệm, điểm sàn DC5 >= 25.0đ
        if dc5_rnk_val and dc5_rnk_val <= 5:
            cash_sum += 10.0
        elif dc5_rnk_val and dc5_rnk_val <= 15:
            cash_sum += 5.0
        elif (dc5_sc_val >= 25.0 or (dc5 or 0) > 0):
            cash_sum += 10.0 if (dc5_sc_val >= 40.0 or (dc5 or 0) >= 5) else 5.0
        if "HẠNG S" in (opt_tier or ''):
            cash_sum += 5.0
        if cash_sum == 0.0 and (reb or 0) >= 45 and pen == 0:
            cash_sum = 6.0

        day_cash = max(0.0, min(20.0, cash_sum + pen))
        return day_base, day_push, day_cash

    def query_14_day_profile(
        self, 
        symbol: str, 
        as_of_date: Optional[datetime] = None,
        warning_info: Optional[Dict[str, Any]] = None,
        reb_info: Optional[Dict[str, Any]] = None,
        downgrade_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Truy vấn dữ liệu 4 ngày gần nhất của 1 mã theo chuẩn V2.0 (Hỗ trợ quét 30 phút/phiên).
        Chuẩn hóa 4 lớp:
        - Lớp I: SQL Aggregation Layer (AVG mong_score, MAX day_score, MAX tien_score).
        - Lớp II: Date Alignment Layer (Ép chính xác 4 ngày dương lịch T0..T3, rớt top gán 0.0).
        - Lớp III: Toán tử Tính Điểm (Aggressive Decay & Cashflow Multiplier).
        - Lớp IV: Ma Trận Phạt Điểm Vĩ Mô V2.0 (Dynamic Macro Penalty Matrix: Downgrade 1 Tier).
        """
        clean_sym = symbol.replace('/USDT', '').replace('USDT', '').strip()
        ref_dt = as_of_date or datetime.now()
        ref_d = ref_dt.date() if isinstance(ref_dt, datetime) else ref_dt

        # ═════════════════════════════════════════════════════════════════════
        # LỚP II: ĐỒNG BỘ CHUỖI THỜI GIAN (Date Alignment Layer)
        # ═════════════════════════════════════════════════════════════════════
        target_dates = [(ref_d - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(4)]
        daily_agg = {}

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # ═════════════════════════════════════════════════════════════════
            # LỚP I: TRUY VẤN DỮ LIỆU TỔNG HỢP SQL (SQL Aggregation Layer)
            # ═════════════════════════════════════════════════════════════════
            has_session_table = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_scores'"
            ).fetchone()

            if has_session_table:
                cursor.execute('''
                    SELECT 
                        SUBSTR(timestamp, 1, 10) AS scan_date,
                        ROUND(AVG(mong_score), 2) AS mong_score,
                        MAX(day_score) AS day_score,
                        ROUND(AVG(tien_score), 2) AS tien_score
                    FROM session_scores
                    WHERE symbol = ?
                    GROUP BY scan_date
                    ORDER BY scan_date DESC
                    LIMIT 14
                ''', (clean_sym,))
                for r in cursor.fetchall():
                    daily_agg[r[0]] = {
                        'mong': float(r[1] or 0.0),
                        'day': float(r[2] or 0.0),
                        'tien': float(r[3] or 0.0)
                    }

            # Dự phòng: Chỉ bổ sung những ngày HOÀN TOÀN chưa có trong session_scores từ daily_metrics (dữ liệu lịch sử cũ)
            cursor.execute("SELECT DISTINCT SUBSTR(timestamp, 1, 10) FROM session_scores")
            known_sess_dates = set(r[0] for r in cursor.fetchall())
            truly_missing_dates = [d for d in target_dates if d not in known_sess_dates]

            if truly_missing_dates:
                placeholders = ', '.join(['?'] * len(truly_missing_dates))
                cursor.execute(f'''
                    SELECT 
                        date, raw_base_score, b3a_score, b3b_score, rebalance_score, strict_3d_score,
                        dc2_sniper_count, dc3_breakout_count, dc4_hottrend_count, dc5_pingpong_score,
                        penalty_level_3, penalty_xaray, penalty_fakeout,
                        grid_rank, grid_status, in_top_3a, in_top_3b, optimal_tier,
                        price_above_ma7_3d, ma7_gt_ma99_3d, st_4h_green,
                        money_flow_score, dc2_score, dc3_score, dc4_score, dc5_score,
                        dc5_rank
                    FROM daily_metrics
                    WHERE symbol = ? AND date IN ({placeholders})
                ''', [clean_sym] + truly_missing_dates)
                for r in cursor.fetchall():
                    d = r[0]
                    m_val, d_val, t_val = self._calc_scores_from_daily_metric_row(r)
                    daily_agg[d] = {'mong': m_val, 'day': d_val, 'tien': t_val}

        # ═════════════════════════════════════════════════════════════════════
        # LỚP II: ÉP MẢNG 4 NGÀY DƯƠNG LỊCH (Rớt top -> Gán 0.0)
        # ═════════════════════════════════════════════════════════════════════
        history_mong = [daily_agg.get(d, {}).get('mong', 0.0) for d in target_dates]
        history_day  = [daily_agg.get(d, {}).get('day', 0.0) for d in target_dates]
        history_tien = [daily_agg.get(d, {}).get('tien', 0.0) for d in target_dates]

        # ═════════════════════════════════════════════════════════════════════
        # LỚP III: TOÁN TỬ TÍNH ĐIỂM (Aggressive Decay & Cashflow Multiplier)
        # ═════════════════════════════════════════════════════════════════════
        weights = [0.50, 0.25, 0.15, 0.10]
        diem_mong = round(sum(m * w for m, w in zip(history_mong, weights)), 1)
        diem_day  = round(sum(d * w for d, w in zip(history_day, weights)), 1)
        diem_tien = round(sum(t * w for t, w in zip(history_tien, weights)), 1)

        # Cashflow Multiplier
        if diem_tien >= 15.0:
            multiplier = 1.2
        elif diem_tien <= 5.0:
            multiplier = 0.5
        else:
            multiplier = 1.0

        # Total Cap: 100.0
        raw_total = (diem_mong + diem_day + diem_tien) * multiplier

        # ═════════════════════════════════════════════════════════════════════
        # LỚP IV: MA TRẬN PHẠT ĐIỂM VĨ MÔ V2.0 (DYNAMIC MACRO PENALTY MATRIX)
        # ═════════════════════════════════════════════════════════════════════
        # Tính delta 24h và delta 72h dựa trên mảng 4 ngày
        m0 = history_mong[0] if len(history_mong) > 0 else 0.0
        m1 = history_mong[1] if len(history_mong) > 1 else 0.0
        m3 = history_mong[3] if len(history_mong) > 3 else 0.0

        d0 = history_day[0] if len(history_day) > 0 else 0.0
        d1 = history_day[1] if len(history_day) > 1 else 0.0
        d3 = history_day[3] if len(history_day) > 3 else 0.0

        t0 = history_tien[0] if len(history_tien) > 0 else 0.0
        t1 = history_tien[1] if len(history_tien) > 1 else 0.0
        t3 = history_tien[3] if len(history_tien) > 3 else 0.0

        raw0 = m0 + d0 + t0
        raw1 = m1 + d1 + t1
        raw3 = m3 + d3 + t3
        d24 = round(raw0 - raw1, 1)
        d72 = round(raw0 - raw3, 1)

        final_rating, total_penalty, macro_penalties = calculate_macro_penalties_v2(
            raw_total=raw_total,
            warning_info=warning_info,
            reb_info=reb_info,
            downgrade_info=downgrade_info,
            d24=d24,
            d72=d72
        )
        tier, action = self._determine_tier(final_rating, diem_mong)

        is_live = bool(target_dates[0] in daily_agg and (history_mong[0] > 0 or history_day[0] > 0 or history_tien[0] > 0))

        # ═════════════════════════════════════════════════════════════════════
        # LỚP V: TRÍCH XUẤT THÀNH PHẦN MÓNG T-0 CHO BẢNG HIỂN THỊ
        # ═════════════════════════════════════════════════════════════════════
        t0_components = {}
        with self._get_connection() as conn:
            cur = conn.cursor()
            row_t0 = cur.execute('''
                SELECT b3b_score, strict_3d_score, grid_rank, grid_status, in_top_3a, in_top_3b, optimal_tier,
                       price_above_ma7_3d, ma7_gt_ma99_3d, st_4h_green
                FROM daily_metrics
                WHERE symbol = ? AND date = ?
            ''', (clean_sym, target_dates[0])).fetchone()
            if row_t0:
                b3b_val, sc_st, g_rnk, g_stat, in_3a, in_3b, opt_t, p_ma7, ma_gt, st_4h = row_t0
                if b3b_val and float(b3b_val) > 0:
                    sc_3d = min(25.0, (float(b3b_val) / 100.0) * 25.0)
                elif sc_st and sc_st >= 70:
                    sc_3d = 25.0
                elif sc_st and sc_st >= 50:
                    sc_3d = 15.0
                elif p_ma7 and st_4h:
                    sc_3d = 25.0 if ma_gt else 15.0
                else:
                    sc_3d = 0.0

                sc_grid = 0.0
                if "NÉ GRID" not in (g_stat or ''):
                    if (g_rnk or 999) <= 5:
                        sc_grid = 10.0
                    elif (g_rnk or 999) <= 15:
                        sc_grid = 5.0

                sc_3ab = 5.0 if (in_3a or in_3b) else 0.0
                sc_opt = 10.0 if "HẠNG S" in (opt_t or '') else (5.0 if any(t in (opt_t or '') for t in ["HẠNG A", "HẠNG B"]) else 0.0)
                t0_components = {
                    '3d_strict': sc_3d,
                    'grid_reb': sc_grid,
                    'radar_3ab': sc_3ab,
                    'optimal_confluence': sc_opt
                }
            else:
                b3b_val = self.query_latest_3b_score(clean_sym)
                if b3b_val > 0:
                    t0_components = {
                        '3d_strict': min(25.0, (b3b_val / 100.0) * 25.0),
                        'grid_reb': 0.0,
                        'radar_3ab': 0.0,
                        'optimal_confluence': 0.0
                    }

        penalty_tags = [
            f"{tag} (-{pts:.0f}đ)" if (pts % 1 == 0) else f"{tag} (-{pts:.1f}đ)"
            for tag, pts in macro_penalties
        ]

        return {
            "symbol": clean_sym,
            "raw_score": round(raw_total, 2),
            "macro_penalty": round(total_penalty, 1),
            "penalty_tags": penalty_tags,
            "total_score": final_rating,
            "tier": tier,
            "action_recommendation": action,
            "multiplier": multiplier,
            "target_dates": target_dates,
            "is_live": is_live,
            "delta_24h": d24,
            "delta_72h": d72,
            "breakdown": {
                "foundation_40": diem_mong,
                "momentum_40": diem_day,
                "cashflow_20": diem_tien,
                "history_mong": [round(x, 1) for x in history_mong],
                "history_day": [round(x, 1) for x in history_day],
                "history_tien": [round(x, 1) for x in history_tien],
                "t0_components": t0_components
            }
        }

    def _determine_tier(self, score: float, foundation_score: float):
        if score >= 80.0:
            return "💎 Tier S (Kim Cương)", "🟢 DUYỆT TỰ ĐỘNG (Đánh Full Volume)"
        elif score >= 60.0:
            if foundation_score < 15.0:
                return "Tier A- (FOMO Trend)", "🟡 ĐÁNH 50% VOL (Đà tăng gắt nhưng thiếu móng tích lũy)"
            else:
                return "Tier A (Xu Hướng)", "🟢 DUYỆT TỰ ĐỘNG (Cấu trúc ổn định)"
        elif score >= 40.0:
            return "Tier B (Lướt Sóng)", "🟠 ĐÁNH 30% VOL (Bắt Pullback hoặc ăn ngắn)"
        elif score >= 25.0:
            return "Tier C (Theo Dõi)", "👀 THEO DÕI (Chờ dòng tiền bùng nổ xác nhận)"
        else:
            return "Tier F (Rủi Ro)", "⛔ TỪ CHỐI (Không có đà tăng, điểm quá thấp)"

    def _format_empty_profile(self, symbol: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "raw_score": 0.0,
            "macro_penalty": 0.0,
            "penalty_tags": [],
            "total_score": 0.0,
            "tier": "Unknown",
            "action_recommendation": "⛔ TỪ CHỐI (Không có dữ liệu)",
            "multiplier": 1.0,
            "delta_24h": 0.0,
            "delta_72h": 0.0,
            "breakdown": {
                "foundation_40": 0.0,
                "momentum_40": 0.0,
                "cashflow_20": 0.0,
                "history_mong": [],
                "history_day": [],
                "history_tien": [],
                "t0_components": {}
            }
        }

    def query_all_14_day_profiles(
        self, 
        as_of_date: Optional[datetime] = None,
        warning_map: Optional[Dict[str, Any]] = None,
        reb_map: Optional[Dict[str, Any]] = None,
        downgrade_map: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Truy vấn toàn bộ các mã có dữ liệu, sắp xếp theo điểm tín nhiệm giảm dần.
        Hỗ trợ nạp ma trận cảnh báo vĩ mô thời gian thực (warning_map, reb_map, downgrade_map).
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

            has_session_table = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_scores'"
            ).fetchone()
            if has_session_table:
                cursor.execute('''
                    SELECT DISTINCT symbol FROM session_scores
                    WHERE substr(timestamp, 1, 10) >= ? AND substr(timestamp, 1, 10) <= ?
                ''', (fourteen_days_ago, ref_date_str))
                for r in cursor.fetchall():
                    if r[0] not in symbols:
                        symbols.append(r[0])

        profiles = [
            self.query_14_day_profile(
                sym, 
                as_of_date=ref_date,
                warning_info=(warning_map or {}).get(sym),
                reb_info=(reb_map or {}).get(sym),
                downgrade_info=(downgrade_map or {}).get(sym)
            ) 
            for sym in symbols
        ]
        profiles.sort(key=lambda x: (x["total_score"], x["breakdown"]["foundation_40"]), reverse=True)
        return profiles

    def get_data_date_range(self, as_of_date: Optional[datetime] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Lấy mốc thời gian bắt đầu và kết thúc (có đầy đủ giờ:phút:giây) của dữ liệu được đánh giá trong 14 ngày gần nhất.
        - Start: Ngày/giờ nhỏ nhất (nếu chỉ có YYYY-MM-DD thì gán 00:00:00).
        - End: Ngày/giờ lớn nhất (ưu tiên MAX(timestamp) từ signal_logs, hoặc MAX(date) kèm HH:MM:SS hiện tại).
        Trả về (min_timestamp, max_timestamp) dạng chuỗi 'YYYY-MM-DD HH:MM:SS', hoặc (None, None).
        """
        ref_date = as_of_date or datetime.now()
        fourteen_days_ago = (ref_date - timedelta(days=14)).strftime('%Y-%m-%d')
        ref_date_str = ref_date.strftime('%Y-%m-%d')
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT MIN(date), MAX(date) FROM daily_metrics
                    WHERE date >= ? AND date <= ?
                ''', (fourteen_days_ago, ref_date_str))
                row = cursor.fetchone()
                min_d = row[0] if row and row[0] else None
                max_d = row[1] if row and row[1] else None

                if not min_d or not max_d:
                    cursor.execute('SELECT MIN(date), MAX(date) FROM daily_metrics')
                    row = cursor.fetchone()
                    min_d = row[0] if row and row[0] else None
                    max_d = row[1] if row and row[1] else None

                if not min_d or not max_d:
                    return None, None

                # Tìm timestamp chi tiết (HH:MM:SS) từ signal_logs
                has_signal_logs = cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_logs'"
                ).fetchone()

                min_ts, max_ts = None, None
                if has_signal_logs:
                    cursor.execute(
                        "SELECT MIN(timestamp), MAX(timestamp) FROM signal_logs WHERE substr(timestamp, 1, 10) >= ? AND substr(timestamp, 1, 10) <= ?",
                        (str(min_d), str(max_d))
                    )
                    ts_row = cursor.fetchone()
                    if ts_row:
                        min_ts, max_ts = ts_row[0], ts_row[1]

                # Chuẩn hóa start_str có giờ:phút:giây
                if min_ts and str(min_ts).startswith(str(min_d)):
                    start_str = str(min_ts)
                else:
                    start_str = f"{min_d} 00:00:00"

                # Chuẩn hóa end_str có giờ:phút:giây
                if max_ts and str(max_ts).startswith(str(max_d)):
                    end_str = str(max_ts)
                else:
                    today_s = datetime.now().strftime('%Y-%m-%d')
                    if str(max_d) == today_s:
                        end_str = f"{max_d} {datetime.now().strftime('%H:%M:%S')}"
                    else:
                        end_str = f"{max_d} 23:59:59"

                return start_str, end_str
        except Exception as e:
            logger.debug(f"Lỗi lấy date range từ daily_metrics: {e}")
        return None, None


# ═════════════════════════════════════════════════════════════════════════════
# GIAO DIỆN HIỂN THỊ BẢNG TÍN NHIỆM V2.0
# ═════════════════════════════════════════════════════════════════════════════

def calculate_tier_s_setups(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Tính toán tự động bộ 3 thiết lập lệnh thực chiến (OCO, Grid TP2, Wide Grid 3D)
    dành riêng cho các siêu mã đạt chuẩn 💎 Tier S (Kim Cương).
    """
    try:
        from core.macro_levels import get_3d_profile, _build_wide_grid
        from core.grid_tp2_builder import build_macro_grid_payload, _make_formatter
        from core.exchange_info_cache import ExchangeInfoCache

        d3 = get_3d_profile(symbol) or {}
        raw = d3.get('_raw') or {}
        close = float(raw.get('close_3d', 0.0) or 0.0)
        ma7 = float(raw.get('ma7_3d', 0.0) or 0.0)
        boll_up = float(raw.get('boll_up_3d', 0.0) or 0.0)

        tick_size = 0.0001
        try:
            cache = ExchangeInfoCache()
            t = cache.get_tick_size(f"{symbol}USDT")
            if t and t > 0:
                tick_size = t
        except Exception:
            pass

        f = _make_formatter(tick_size)
        if close <= 0:
            return None

        # 1. OCO Setup (Spot Scalp / Momentum 15M)
        # Thay vì lấy nhầm SL/TP vĩ mô của khung 3D (dẫn đến SL -15% ~ -26%, TP +6.5%, R:R đảo ngược),
        # OCO lướt sóng phải dựa trên nến 15M (Swing Low gần nhất) hoặc biên độ chuẩn (SL -3.5% ~ -4.5%, R:R >= 1:2.0).
        from core.klines_cache import get_klines_cached
        sym_api = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol

        entry = close
        sl = None

        df_15m = None
        try:
            df_15m = get_klines_cached(sym_api, "15m", limit=30)
        except Exception:
            pass

        if df_15m is not None and not df_15m.empty and len(df_15m) >= 10:
            low_col = 'Low' if 'Low' in df_15m.columns else 'low'
            recent_low = float(df_15m[low_col].iloc[-10:].min())
            sl_cand = recent_low * 0.995  # trừ 0.5% buffer né quét râu
            dist_pct = (entry - sl_cand) / entry * 100.0

            # Capping an toàn: SL lệnh lướt sóng dao động trong vùng 2.5% <= SL <= 5.0%
            if 2.5 <= dist_pct <= 5.0:
                sl = sl_cand
            elif dist_pct > 5.0:
                sl = entry * 0.955  # Khống chế tối đa SL = -4.5%
            else:
                sl = entry * 0.975  # Cắt lỗ tối thiểu 2.5% né noise
        
        if sl is None or sl <= 0 or sl >= entry:
            # Fallback nếu không có nến 15m: SL chuẩn -4.0%
            sl = entry * 0.96

        sl_pct = (entry - sl) / entry * 100.0

        # Chốt lời TP: Đảm bảo tỷ lệ R:R tối thiểu 1:2.0
        min_tp_rr2 = entry * (1.0 + (sl_pct * 2.0) / 100.0)
        # Nếu cản boll_up 3D cao hơn mốc R:R 1:2 thì lấy boll_up làm mục tiêu lớn, ngược lại khóa R:R = 1:2.0
        tp = boll_up if boll_up >= min_tp_rr2 else min_tp_rr2

        tp_pct = (tp - entry) / entry * 100.0
        rr = tp_pct / sl_pct if sl_pct > 0 else 2.0
        oco_line = f"⚙️ SETUP OCO: Buy={f(entry)} | SL={f(sl)}(-{sl_pct:.1f}%) | TP={f(tp)}(+{tp_pct:.1f}%) | R:R=1:{rr:.1f}"

        # 2. Grid TP2
        # Cận trên lưới TP2 tối thiểu +20%
        tp_grid = max(boll_up, entry * 1.20)
        grid_payload = build_macro_grid_payload(
            symbol=symbol,
            entry=entry,
            sl_short=sl,
            tp_target=tp_grid,
            macro_sl=ma7 * 0.96 if ma7 > 0 else sl * 0.95,
            tick_size=tick_size
        )
        grid_line = grid_payload["display_line"].strip() if grid_payload else ""

        # 3. Wide Grid 3D
        wg = _build_wide_grid(close, ma7 if ma7 > 0 else close * 0.95, boll_up if boll_up > 0 else close * 1.15, tick_size)
        wide_line = ""
        if wg and wg.get("valid"):
            wide_line = (
                f"🦅 [WIDE GRID 3D - {symbol}] {f(wg['wide_low'])} - {f(wg['wide_up'])} | {wg['wide_grids']}L | "
                f"Trig: {f(wg['wide_trig'])} | SL: {f(wg['wide_sl'])} | TP: {f(wg['wide_up'])} "
                f"(Vốn {wg['capital']:.0f}$ ≈ {wg['per_grid_usdt']}$/lưới)"
            )

        return {
            "symbol": symbol,
            "close": close,
            "oco": oco_line,
            "grid_tp2": grid_line,
            "wide_grid": wide_line
        }
    except Exception as e:
        logger.error(f"Lỗi tính toán setup Tier S cho {symbol}: {e}")
        return None


def render_profiles_table(
    profiles: List[Dict[str, Any]],
    title: Optional[str] = None,
    date_range: Optional[Tuple[str, str]] = None
):
    """
    In bảng hiển thị V2.0:
    - Bảng chi tiết: Các mã đạt chuẩn giao dịch (Tier S, Tier A, Tier B - Điểm >= 40.0).
    - Danh sách rút gọn: Liệt kê các mã Tier C (25–39đ) và Tier F (< 25đ) cách nhau bằng dấu phẩy.
    """
    # 📅 Xác định khoảng ngày dữ liệu (từ...đến...) để người dùng nhận diện data mới hay cũ
    min_date, max_date = None, None
    if date_range and date_range[0] and date_range[1]:
        min_date, max_date = str(date_range[0]), str(date_range[1])

    date_tag = f" (TỪ {min_date} ĐẾN {max_date})" if min_date and max_date else ""

    if not profiles:
        print(f"\n📊 BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY{date_tag}: Không có dữ liệu.")
        return

    import textwrap

    # Phân nhóm danh sách & Đảm bảo luôn sắp xếp theo thứ tự điểm cao đến thấp
    sorted_profiles = sorted(
        profiles,
        key=lambda x: (x.get('total_score', 0.0), x.get('breakdown', {}).get('foundation_40', 0.0)),
        reverse=True
    )
    active_profiles = [p for p in sorted_profiles if p.get('total_score', 0.0) >= 40.0]
    tier_c_symbols = [p['symbol'] for p in sorted_profiles if 25.0 <= p.get('total_score', 0.0) < 40.0]
    tier_f_symbols = [p['symbol'] for p in sorted_profiles if p.get('total_score', 0.0) < 25.0]

    # Nếu thị trường không có mã nào >= 40đ, hiển thị tối thiểu Top 5 để người dùng theo dõi
    display_profiles = active_profiles if active_profiles else sorted_profiles[:5]

    # Chuẩn hóa tiêu đề hiển thị: bổ sung thông tin ngày từ...đến...
    if not title:
        display_title = f"BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY V2.0{date_tag} (TỔNG {len(profiles)} MÃ)"
    else:
        display_title = title
        if date_tag and "TỪ" not in display_title.upper() and "ĐẾN" not in display_title.upper():
            if "(TỔNG" in display_title:
                display_title = display_title.replace("(TỔNG", f"{date_tag} (TỔNG")
            elif "V2.0" in display_title:
                display_title = display_title.replace("V2.0", f"V2.0{date_tag}")
            else:
                display_title = f"{display_title}{date_tag}"

    print(f"\n📊 {display_title}")
    sep = "=" * 158
    line = "-" * 158
    print(sep)
    header = (
        f"{'Hạng':<5} | {'Mã':<7} | {'Tổng':<6} | {'Phạt':<7} | {'Tier (Phân Hạng)':<21} | {'MÓNG(40)':<8} | "
        f"{'└ 3D(25)':<8} | {'└ Grid(10)':<10} | {'└ 3AB(5)':<8} | {'└ HộiTụ(10)':<11} | "
        f"{'Đẩy(40)':<7} | {'Tiền(20)':<8} | {'×HệSố':<6}"
    )
    print(header)
    print(line)

    for idx, p in enumerate(display_profiles, start=1):
        b = p.get('breakdown', {})
        f_tot = b.get('foundation_40', 0.0)
        t0 = b.get('t0_components', {})
        sc_3d = t0.get('3d_strict', 0.0)
        sc_grid = t0.get('grid_reb', 0.0)
        sc_3ab = t0.get('radar_3ab', 0.0)
        sc_opt = t0.get('optimal_confluence', 0.0)

        mom = b.get('momentum_40', 0.0)
        cash = b.get('cashflow_20', 0.0)
        mult = p.get('multiplier', 1.0)
        mult_str = f"x{mult:.1f}"
        if mult > 1.0:
            mult_str = f"🚀x{mult:.1f}"
        elif mult < 1.0:
            mult_str = f"🔻x{mult:.1f}"

        rank_str = f"#{idx}"
        sc_3d_str = f"{sc_3d:.1f}" if (sc_3d % 1 != 0) else f"{sc_3d:.0f}"
        pen_val = float(p.get('macro_penalty', 0.0) or 0.0)
        pen_str = f"-{pen_val:.0f}đ" if (pen_val % 1 == 0 and pen_val > 0) else (f"-{pen_val:.1f}đ" if pen_val > 0 else "-")
        row_str = (
            f"{rank_str:<5} | {p['symbol']:<7} | {p['total_score']:<6.1f} | {pen_str:<7} | {p['tier']:<21} | {f_tot:<8.1f} | "
            f"{sc_3d_str:<8} | {sc_grid:<10.0f} | {sc_3ab:<8.0f} | {sc_opt:<11.0f} | "
            f"{mom:<7.1f} | {cash:<8.1f} | {mult_str:<6}"
        )
        print(row_str)
    print(sep)

    if tier_c_symbols:
        c_str = ", ".join(tier_c_symbols)
        wrapped_c = textwrap.fill(c_str, width=150, initial_indent="   ", subsequent_indent="   ")
        print(f"👀 TIER C (25–39đ - Theo Dõi / Chờ Xác Nhận) [{len(tier_c_symbols)} mã - Sắp xếp điểm cao ➔ thấp]:")
        print(wrapped_c)
        print(line)

    if tier_f_symbols:
        f_str = ", ".join(tier_f_symbols)
        wrapped_f = textwrap.fill(f_str, width=150, initial_indent="   ", subsequent_indent="   ")
        print(f"⛔ TIER F (< 25đ - Rủi Ro / Mất Đà Tăng / Cạn Thanh Khoản) [{len(tier_f_symbols)} mã - Sắp xếp điểm cao ➔ thấp]:")
        print(wrapped_f)
        print(line)

    # Khối kiểm toán chi tiết các mã bị phạt vĩ mô
    penalized = [p for p in profiles if p.get('macro_penalty', 0.0) > 0]
    if penalized:
        print("⚠️ CHI TIẾT PHẠT ĐIỂM VĨ MÔ LIVE (DYNAMIC MACRO PENALTY AUDIT):")
        for p in penalized[:12]:
            tags_s = ", ".join(p.get('penalty_tags', []))
            print(f"   • {p['symbol']:<6}: Gốc {p.get('raw_score', 0.0):.1f}đ ➔ Điểm mới {p['total_score']:.1f}đ ({p['tier']}) | Phạt -{p['macro_penalty']:.1f}đ [{tags_s}]")
        if len(penalized) > 12:
            print(f"   • ... và {len(penalized) - 12} mã khác bị trừ điểm.")
        print(sep)


def print_14_day_summary_board(
    db_path: str = "data/binac4_history.db",
    df_summary: Optional[Any] = None,
    early_list: Optional[List[Dict[str, Any]]] = None,
    super_wave_results: Optional[List[Dict[str, Any]]] = None,
    warning_results: Optional[List[Dict[str, Any]]] = None,
    convergence_ranks: Optional[Dict[str, str]] = None,
    top_n: int = 25,
    scan_time: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Điểm vào chuẩn để hiển thị BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM V2.0 ngay trước Bảng Rebalance.
    Tự động ghi nhận định lượng T-0 (df_summary, early_list, super_wave_results, warning_results, convergence_ranks)
    vào SQLite daily_metrics, đồng bộ tín hiệu và in bảng xếp hạng.
    """
    try:
        rater = HistoricalDataQuery(db_path)
        today_str = datetime.now().strftime('%Y-%m-%d')

        # 1. Thu thập Top 5 của 3A và 3B
        top5_3a = set()
        if early_list:
            top5_3a = {str(ea.get('Symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip() for ea in early_list[:5]} - {''}

        top5_3b = set()
        if super_wave_results:
            top5_3b = {str(sw.get('symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip() for sw in super_wave_results[:5]} - {''}

        # Thu thập toàn bộ điểm sóng 3B (Backend Pipeline) để nội suy tuyến tính vào Móng
        all_3b_scores = {}
        try:
            from strategies.super_trend_3d import get_last_all_super_wave_scores
            all_3b_scores = get_last_all_super_wave_scores()
        except Exception:
            all_3b_scores = {}
        if super_wave_results:
            for sw in super_wave_results:
                sw_sym = str(sw.get('symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip()
                if sw_sym and sw_sym not in all_3b_scores:
                    all_3b_scores[sw_sym] = float(sw.get('score', 0.0) or 0.0)

        # 2. Thu thập bản đồ cảnh báo sớm (ST 4H)
        warning_map = {}
        if warning_results:
            for w in warning_results:
                sym_w = str(w.get('symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip()
                if sym_w:
                    warning_map[sym_w] = w

        # 3. Thu thập bản đồ Rebalance (Thứ hạng + Trạng thái Grid + RSI 1H)
        reb_map = {}
        if df_summary is not None and not getattr(df_summary, 'empty', True):
            for rank_idx, (_, r_reb) in enumerate(df_summary.iterrows(), start=1):
                sym_r = str(r_reb.get('Symbol', '')).upper().replace('/USDT', '').replace('USDT', '').strip()
                if sym_r:
                    try:
                        sc_reb = float(r_reb.get('TỔNG', 0.0))
                    except (ValueError, TypeError):
                        sc_reb = 0.0
                    g_status = str(r_reb.get('Phân Loại Grid', '') or '')
                    try:
                        rsi_1h_v = float(r_reb.get('RSI1H', 0.0) or 0.0)
                    except (ValueError, TypeError):
                        rsi_1h_v = 0.0
                    reb_map[sym_r] = {
                        "rank": rank_idx,
                        "status": g_status,
                        "score": sc_reb,
                        "rsi_1h": rsi_1h_v
                    }

        # 4. Thu thập tín hiệu động cơ phiên quét hiện tại từ signal_logs
        latest_signals_map = {}
        latest_ts = None
        try:
            with sqlite3.connect(rater.db_path) as conn_s:
                cur_s = conn_s.cursor()
                row_ts = cur_s.execute("SELECT MAX(timestamp) FROM signal_logs").fetchone()
                if row_ts and row_ts[0]:
                    latest_ts = row_ts[0]
                    sig_q = '''
                        SELECT 
                            symbol,
                            MAX(CASE WHEN engine = 'DC2' THEN score ELSE 0 END) as dc2_sc,
                            MAX(CASE WHEN engine = 'DC3' THEN score ELSE 0 END) as dc3_sc,
                            MAX(CASE WHEN engine = 'DC4' THEN score ELSE 0 END) as dc4_sc,
                            MAX(CASE WHEN engine = 'DC5' THEN score ELSE 0 END) as dc5_sc,
                            MAX(CASE WHEN action LIKE '%TIỀN BẠO PHÁT%' THEN 1 ELSE 0 END) as has_mf,
                            SUM(CASE WHEN engine = 'PENALTY_LVL3' THEN 1 ELSE 0 END) as p_lvl3,
                            SUM(CASE WHEN engine = 'PENALTY_XARAY' THEN 1 ELSE 0 END) as p_xaray,
                            SUM(CASE WHEN engine = 'PENALTY_FAKEOUT' THEN 1 ELSE 0 END) as p_fakeout
                        FROM signal_logs
                        WHERE timestamp >= datetime(?, '-15 minutes') AND timestamp <= datetime(?, '+1 minute')
                        GROUP BY symbol
                    '''
                    for row_s in cur_s.execute(sig_q, (latest_ts, latest_ts)).fetchall():
                        s_sym = str(row_s[0]).upper().replace('/USDT', '').replace('USDT', '').strip()
                        latest_signals_map[s_sym] = {
                            'dc2_sc': float(row_s[1] or 0.0),
                            'dc3_sc': float(row_s[2] or 0.0),
                            'dc4_sc': float(row_s[3] or 0.0),
                            'dc5_sc': float(row_s[4] or 0.0),
                            'has_mf': int(row_s[5] or 0),
                            'p_lvl3': int(row_s[6] or 0),
                            'p_xaray': int(row_s[7] or 0),
                            'p_fakeout': int(row_s[8] or 0),
                        }
        except Exception as e_sig:
            logger.warning(f"Không thể đọc tín hiệu phiên từ signal_logs: {e_sig}")

        # Xây dựng bảng xếp hạng Động cơ 5 trong phiên Live (Chỉ tính mã có dòng tiền đạt sàn >= 25.0đ)
        dc5_active_syms = sorted(
            [s for s, d in latest_signals_map.items() if d.get('dc5_sc', 0.0) >= 25.0],
            key=lambda s: latest_signals_map[s].get('dc5_sc', 0.0),
            reverse=True
        )
        dc5_live_rank_map = {s: rank + 1 for rank, s in enumerate(dc5_active_syms)}

        # 4.5. Tập hợp toàn bộ danh sách mã trong phiên T-0 (bao gồm cả mã có điểm 3B dải 40-69đ)
        all_symbols = set(reb_map.keys()) | top5_3a | top5_3b | set(warning_map.keys()) | set(latest_signals_map.keys()) | set(all_3b_scores.keys())
        if convergence_ranks:
            all_symbols |= {s.upper().replace('/USDT', '').replace('USDT', '').strip() for s in convergence_ranks.keys()}
        all_symbols -= {''}

        # 5. Ghi nhận Snapshot T-0 vào daily_metrics và session_scores
        from core.macro_levels import get_3d_profile
        sess_time = str(scan_time) if scan_time else (latest_ts or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        for sym in sorted(all_symbols):
            d3 = get_3d_profile(sym)
            raw3d = (d3 or {}).get('_raw') or {}
            price = float(raw3d.get('close_3d', 0.0) or 0.0)
            ma7_3d = float(raw3d.get('ma7_3d', 0.0) or 0.0)
            ma99_3d = float(raw3d.get('ma99_3d', 0.0) or 0.0)

            w_info = warning_map.get(sym, {})
            if 'st_4h_green' in w_info:
                st_4h_green = bool(w_info.get('st_4h_green', False))
            else:
                try:
                    from core.klines_cache import get_klines_cached
                    from core.macro_levels import _supertrend_val_dir
                    sym_4h = sym + "USDT" if not sym.endswith("USDT") else sym
                    df_4h_raw = get_klines_cached(sym_4h, "4h", 50)
                    if df_4h_raw is not None and not df_4h_raw.empty and len(df_4h_raw) >= 20:
                        _, st_dir_4h = _supertrend_val_dir(df_4h_raw.rename(columns=str.lower))
                        st_4h_green = (st_dir_4h == 1)
                    else:
                        st_4h_green = False
                except Exception:
                    st_4h_green = False

            reb_info = reb_map.get(sym, {})
            grid_rank = reb_info.get('rank', 999)
            grid_status = reb_info.get('status', '')
            reb_score = reb_info.get('score', 0.0)

            in_3a = 1 if sym in top5_3a else 0
            in_3b = 1 if sym in top5_3b else 0
            opt_tier = (convergence_ranks or {}).get(sym, '')
            if not opt_tier:
                opt_tier = rater.get_latest_convergence_tier(sym)

            b3b_sc = all_3b_scores.get(sym, 0.0)
            if b3b_sc == 0.0:
                b3b_sc = rater.query_latest_3b_score(sym)

            coin_data = {
                "Price": price,
                "MA7_3D": ma7_3d,
                "MA99_3D": ma99_3d,
                "ST_4H": "Green" if st_4h_green else "Red",
                "Grid_Rank": grid_rank,
                "Grid_Status": grid_status,
                "In_Top_3A": in_3a,
                "In_Top_3B": in_3b,
                "Optimal_Conclusion_Tier": opt_tier,
                "b3b_score": b3b_sc
            }
            raw_base = calculate_raw_base_score(coin_data)

            dc5_rnk = dc5_live_rank_map.get(sym, 999)

            rater.record_daily_metric(today_str, sym, {
                "b3b_score": b3b_sc,
                "rebalance_score": reb_score,
                "raw_base_score": raw_base,
                "grid_rank": grid_rank,
                "grid_status": grid_status,
                "in_top_3a": in_3a,
                "in_top_3b": in_3b,
                "optimal_tier": opt_tier,
                "price_above_ma7_3d": 1 if (price > ma7_3d and ma7_3d > 0) else 0,
                "ma7_gt_ma99_3d": 1 if (ma7_3d > ma99_3d and ma99_3d > 0) else 0,
                "st_4h_green": 1 if st_4h_green else 0,
                "dc5_rank": dc5_rnk
            })

            # Ghi nhận điểm phiên quét 30 phút vào session_scores (V2.0 Spec)
            sig_data = latest_signals_map.get(sym, {})
            dc2_sc = sig_data.get('dc2_sc', 0.0)
            dc3_sc = sig_data.get('dc3_sc', 0.0)
            dc4_sc = sig_data.get('dc4_sc', 0.0)
            dc5_sc = sig_data.get('dc5_sc', 0.0)
            has_mf = sig_data.get('has_mf', 0)
            p_lvl3 = sig_data.get('p_lvl3', 0)
            p_xaray = sig_data.get('p_xaray', 0)
            p_fakeout = sig_data.get('p_fakeout', 0)

            # Điểm Đẩy: Bắt trọn xung lực kích hoạt (Trigger Points: DC2, DC3, DC4 - Tối đa 40đ, yêu cầu sàn >= 60.0đ)
            p_dc2 = min(10.0, max(5.0, dc2_sc * 0.10)) if dc2_sc >= 60.0 else 0.0
            p_dc3 = min(15.0, max(8.0, dc3_sc * 0.15)) if dc3_sc >= 60.0 else 0.0
            p_dc4 = min(15.0, max(8.0, dc4_sc * 0.15)) if dc4_sc >= 60.0 else 0.0
            day_push = min(40.0, p_dc2 + p_dc3 + p_dc4)

            # Điểm Tiền: Trạng thái dòng tiền Net Flow & Phạt xả râu (Tối đa 20đ)
            opt_s = 1 if 'HẠNG S' in (opt_tier or '') else 0
            pen = (p_lvl3 * -15) + (p_xaray * -5) + (p_fakeout * -10)
            cash_sum = 0.0
            if has_mf:
                cash_sum += 10.0
            if p_dc3 > 0:
                cash_sum += 5.0
            # Động cơ 5 phân tầng: Top 1-5 nhận 10đ, Top 6-15 nhận 5đ đệm (điểm sàn >= 25.0đ)
            if dc5_rnk <= 5:
                cash_sum += 10.0
            elif dc5_rnk <= 15:
                cash_sum += 5.0
            elif dc5_sc >= 25.0:
                cash_sum += min(10.0, max(5.0, dc5_sc * 0.10))
            if opt_s:
                cash_sum += 5.0
            if cash_sum == 0.0 and reb_score >= 45 and pen == 0:
                cash_sum = 6.0
            day_cash = max(-30.0, min(20.0, cash_sum + pen))

            rater.record_session_score(sess_time, sym, raw_base, day_push, day_cash)

        # 6. Đồng bộ tín hiệu Động cơ từ signal_logs
        rater.sync_from_signal_logs()

        # 7. Truy vấn toàn bộ hồ sơ (kèm Ma Trận Phạt Vĩ Mô Live)
        profiles = rater.query_all_14_day_profiles(
            warning_map=warning_map,
            reb_map=reb_map
        )
        min_date, max_date = rater.get_data_date_range()
        if scan_time:
            max_date = str(scan_time)
        date_tag = f" (TỪ {min_date} ĐẾN {max_date})" if min_date and max_date else ""
        if profiles:
            render_profiles_table(
                profiles,
                f"BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY V2.0{date_tag} (TỔNG {len(profiles)} MÃ)",
                date_range=(min_date, max_date) if min_date and max_date else None
            )
        else:
            print(f"\n📊 BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY{date_tag}: Không có dữ liệu.")
        return profiles
    except Exception as e:
        logger.error(f"Render Error (Bảng Tín Nhiệm V2.0): {e}")
        return []


if __name__ == "__main__":
    rater = HistoricalDataQuery("data/binac4_history.db")
    profiles = rater.query_all_14_day_profiles()
    min_date, max_date = rater.get_data_date_range()
    date_tag = f" (TỪ {min_date} ĐẾN {max_date})" if min_date and max_date else ""
    render_profiles_table(
        profiles[:25],
        f"BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY V2.0{date_tag} (TỔNG {len(profiles[:25])} MÃ)",
        date_range=(min_date, max_date) if min_date and max_date else None
    )
