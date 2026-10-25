import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("HistoryLogger")

class HistoryLogger:
    def __init__(self, db_path="data/binac4_history.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS signal_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        symbol TEXT,
                        engine TEXT,
                        score REAL,
                        action TEXT,
                        details TEXT
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol_timestamp ON signal_logs(symbol, timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sig_ts ON signal_logs(timestamp)")
                conn.commit()
        except Exception as e:
            logger.error(f"Lỗi tạo database: {e}")

    # Ngưỡng sàn thu thập Data Pool (Tầng 1 - Ingestion Filter):
    # Loại bỏ rác, chỉ lưu các mã có tiềm năng vào signal_logs
    INGESTION_MIN_SCORES = {
        "DC1": 40.0,
        "DC2": 45.0,
        "DC3": 50.0,
        "DC4": 45.0,
        "DC5": 20.0,
        "3A": 40.0,
        "3B": 40.0,
    }

    def save_signals(self, signals_list, filter_min_score: bool = True):
        """
        Ghi hàng loạt tín hiệu vào DB.
        signals_list: list of dicts hoặc SignalRecord.
        filter_min_score: Nếu True, áp dụng bộ lọc sàn Tầng 1 (Ingestion Filter) để loại bỏ
                          tín hiệu rác / 0 điểm, chỉ lưu các mã có tiềm năng vào Data Pool.
        """
        if not signals_list:
            return
        
        records = []
        for s in signals_list:
            if isinstance(s, dict):
                timestamp = s.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                symbol = s.get('symbol')
                engine = s.get('engine')
                score = s.get('score', 0.0)
                action = s.get('action', '')
                details = s.get('details', {})
                is_displayed = s.get('is_displayed', False)
            else:
                timestamp = getattr(s, 'timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                symbol = getattr(s, 'symbol', None)
                engine = getattr(s, 'engine_source', getattr(s, 'engine', None))
                score = getattr(s, 'target_score', getattr(s, 'score', 0.0))
                action = getattr(s, 'action_label', getattr(s, 'action', ''))
                details = getattr(s, 'details', {})
                is_displayed = getattr(s, 'is_displayed', False)

            if symbol and engine:
                score_val = float(score or 0.0)
                # Tầng 1: Lọc nạp Data Pool (Ingestion Filter)
                if filter_min_score and engine in self.INGESTION_MIN_SCORES:
                    if score_val <= 0.0:
                        continue
                    min_ingest = self.INGESTION_MIN_SCORES[engine]
                    if not is_displayed and score_val < min_ingest:
                        continue

                records.append((
                    timestamp,
                    symbol,
                    engine,
                    score_val,
                    action,
                    json.dumps(details)
                ))

        if not records:
            return

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.executemany("""
                    INSERT INTO signal_logs (timestamp, symbol, engine, score, action, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, records)
                conn.commit()
        except Exception as e:
            logger.error(f"Lỗi ghi log lịch sử: {e}")

    def get_symbol_history(self, symbol, days=14, min_score=60.0):
        """
        Lấy tiền sử của mã trong X ngày qua.
        Chỉ đếm các tín hiệu đạt chuẩn: score >= min_score và không bị từ chối/cảnh báo tử thần.
        Trả về danh sách (engine, timestamp) xếp từ mới đến cũ.
        """
        res = self.get_symbols_history_bulk([symbol], days=days, min_score=min_score)
        return res.get(symbol, [])

    def get_symbols_history_bulk(self, symbols: List[str], days=14, min_score=60.0) -> Dict[str, List[Tuple[str, str]]]:
        """
        Lấy tiền sử hàng loạt mã trong 1 câu SQL duy nhất (tránh N+1 queries).
        Trả về dict: {symbol: [(engine, timestamp), ...]}
        """
        if not symbols:
            return {}
        past_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        clean_symbols = [s for s in set(symbols) if s]
        result: Dict[str, List[Tuple[str, str]]] = {s: [] for s in clean_symbols}
        if not clean_symbols:
            return result
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                placeholders = ', '.join(['?'] * len(clean_symbols))
                query = f"""
                    SELECT symbol, engine, timestamp FROM signal_logs 
                    WHERE symbol IN ({placeholders}) AND timestamp >= ? AND score >= ?
                      AND (action NOT LIKE '%REJECT%' AND action NOT LIKE '%TỪ CHỐI%' AND action NOT LIKE '%CẤP 3%')
                    ORDER BY timestamp DESC
                """
                params = list(clean_symbols) + [past_date, min_score]
                cursor.execute(query, params)
                for sym, eng, ts in cursor.fetchall():
                    if sym in result:
                        result[sym].append((eng, ts))
        except Exception as e:
            logger.error(f"Lỗi đọc lịch sử bulk: {e}")
        return result

    def clean_old_records(self, days=30):
        """
        Xóa log cũ hơn X ngày để giữ DB nhẹ.
        """
        past_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM signal_logs WHERE timestamp < ?", (past_date,))
                conn.commit()
        except Exception as e:
            logger.error(f"Lỗi dọn dẹp DB: {e}")

    def save_convergence_signals(self, convergence_map: Dict[str, Any]):
        """
        Lưu kết quả Bảng Kết Luận Tối Ưu (Điểm Chạm Vàng) vào database signal_logs.
        convergence_map: dict symbol -> rank string hoặc dict chi tiết
        """
        if not convergence_map:
            return
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        records = []
        for sym, item in convergence_map.items():
            clean_sym = sym.replace('/USDT', '').replace('USDT', '').strip()
            if not clean_sym:
                continue
            if isinstance(item, dict):
                rank = item.get('rank', '')
                details = item
            else:
                rank = str(item)
                details = {"rank": rank}
            score = 10.0 if "HẠNG S" in rank else (5.0 if any(t in rank for t in ["HẠNG A", "HẠNG B"]) else 0.0)
            records.append({
                "timestamp": now_str,
                "symbol": clean_sym,
                "engine": "OPTIMAL_CONVERGENCE",
                "score": score,
                "action": rank,
                "details": details
            })
        self.save_signals(records)

    def get_latest_convergence_tier(self, symbol: str, before_timestamp: Optional[str] = None) -> str:
        """
        Lấy nhãn kết luận tối ưu gần nhất (phiên trước hoặc phiên hiện tại) từ database.
        """
        clean_sym = symbol.replace('/USDT', '').replace('USDT', '').strip()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if before_timestamp:
                    cursor.execute("""
                        SELECT action FROM signal_logs
                        WHERE symbol = ? AND engine = 'OPTIMAL_CONVERGENCE' AND timestamp < ?
                        ORDER BY timestamp DESC LIMIT 1
                    """, (clean_sym, before_timestamp))
                else:
                    cursor.execute("""
                        SELECT action FROM signal_logs
                        WHERE symbol = ? AND engine = 'OPTIMAL_CONVERGENCE'
                        ORDER BY timestamp DESC LIMIT 1
                    """, (clean_sym,))
                row = cursor.fetchone()
                return row[0] if row else ""
        except Exception as e:
            logger.error(f"Lỗi lấy lịch sử kết luận tối ưu cho {symbol}: {e}")
            return ""
