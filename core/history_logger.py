import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("HistoryLogger")

class HistoryLogger:
    def __init__(self, db_path="data/binac4_history.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
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
                conn.commit()
        except Exception as e:
            logger.error(f"Lỗi tạo database: {e}")

    def save_signals(self, signals_list):
        """
        Ghi hàng loạt tín hiệu vào DB.
        signals_list: list of dicts.
        """
        if not signals_list:
            return
        
        records = []
        for s in signals_list:
            # signals_list có thể chứa dict hoặc object. Cố gắng lấy data.
            if isinstance(s, dict):
                timestamp = s.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                symbol = s.get('symbol')
                engine = s.get('engine')
                score = s.get('score', 0.0)
                action = s.get('action', '')
                details = s.get('details', {})
            else:
                timestamp = getattr(s, 'timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                symbol = getattr(s, 'symbol', None)
                engine = getattr(s, 'engine_source', getattr(s, 'engine', None))
                score = getattr(s, 'target_score', getattr(s, 'score', 0.0))
                action = getattr(s, 'action_label', getattr(s, 'action', ''))
                details = getattr(s, 'details', {})

            if symbol and engine:
                records.append((
                    timestamp,
                    symbol,
                    engine,
                    score,
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

    def get_symbol_history(self, symbol, days=14):
        """
        Lấy tiền sử của mã trong X ngày qua.
        Trả về danh sách (engine, timestamp) xếp từ mới đến cũ.
        """
        past_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT engine, timestamp FROM signal_logs 
                    WHERE symbol = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                """, (symbol, past_date))
                rows = cursor.fetchall()
                return rows
        except Exception as e:
            logger.error(f"Lỗi đọc lịch sử cho {symbol}: {e}")
            return []

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
