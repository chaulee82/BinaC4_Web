"""
File: scratch/test_trust_history.py
Kiểm thử toàn diện mô-đun trust_history.py (MOD-TRUST-HIST-01) - Phiên bản Multi-Day Delta Audit:
1. Khởi tạo DB, kiểm tra schema, WAL mode, indexes.
2. Lưu vết snapshot vào DB thành công.
3. Phân tích biến thiên Multi-Day Delta Audit (T0 vs T1 và T0 vs T3).
4. Hiển thị Bảng Biến Thiên Tín Nhiệm Theo Ngày chuẩn.
"""

import os
import sys
import sqlite3
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trust_history import (
    init_db,
    record_and_analyze,
    print_delta_tables
)

TEST_DB = "scratch/test_trust_score_history.db"

def cleanup():
    for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

def test_trust_history():
    cleanup()
    print("════════════════════════════════════════════════════════════════")
    print("🧪 BẮT ĐẦU KIỂM THỬ: TRUST SCORE HISTORY & MULTI-DAY DELTA AUDIT")
    print("════════════════════════════════════════════════════════════════")

    # 1. Khởi tạo DB
    init_db(TEST_DB)
    assert os.path.exists(TEST_DB), "Lỗi: Không tìm thấy file DB sau khi init_db"
    with sqlite3.connect(TEST_DB) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        print(f"✅ DB Journal Mode: {journal_mode.upper()} (mong đợi WAL)")
        assert journal_mode.lower() == "wal", f"Journal mode phải là WAL, nhận: {journal_mode}"

        cols = [r[1] for r in conn.execute("PRAGMA table_info(score_history);").fetchall()]
        expected_cols = ["id", "scan_time", "symbol", "tier", "total_score", "mong", "day", "tien"]
        for c in expected_cols:
            assert c in cols, f"Thiếu cột {c} trong bảng score_history"
        print(f"✅ Bảng score_history có đầy đủ các cột: {cols}")

        idx_names = [r[1] for r in conn.execute("PRAGMA index_list(score_history);").fetchall()]
        assert "idx_scan_symbol" in idx_names, "Thiếu index idx_scan_symbol"
        assert "idx_scan_time" in idx_names, "Thiếu index idx_scan_time"
        print(f"✅ Các chỉ mục đã được tạo: {idx_names}")

    # 2. Kiểm thử lưu vết snapshot
    session1_time = "2026-10-19 10:00:00"
    session1_data = [
        {
            "symbol": "AVA",
            "tier": "Tier S",
            "total_score": 75.0,
            "breakdown": {
                "history_mong": [25.0, 20.0, 15.0, 10.0],
                "history_day":  [35.0, 30.0, 25.0, 20.0],
                "history_tien": [15.0, 12.0, 10.0, 5.0],
                "foundation_40": 20.0,
                "momentum_40": 30.0,
                "cashflow_20": 12.0
            }
        },
        {
            "symbol": "ZK",
            "tier": "Tier A",
            "total_score": 50.0,
            "breakdown": {
                "history_mong": [15.0, 25.0, 25.0, 30.0],
                "history_day":  [25.0, 35.0, 35.0, 40.0],
                "history_tien": [10.0, 15.0, 15.0, 18.0],
                "foundation_40": 20.0,
                "momentum_40": 30.0,
                "cashflow_20": 13.0
            }
        }
    ]

    g1, l1 = record_and_analyze(session1_data, keep_snapshots=24, db_path=TEST_DB, scan_time=session1_time)
    print(f"✅ Snapshot đã được lưu vào DB. Top Gainers: {[x['symbol'] for x in g1]}, Top Losers: {[x['symbol'] for x in l1]}")
    assert any(x['symbol'] == 'AVA' for x in g1), "AVA phải lọt top_gainers vì tăng trưởng từ T3 lên T0"
    assert any(x['symbol'] == 'ZK' for x in l1), "ZK phải lọt top_losers vì suy giảm từ T3 xuống T0"

    print_delta_tables(g1, l1)
    print("✅ TEST PASSED: trust_history hoàn toàn tương thích và hoạt động chuẩn xác!")

if __name__ == "__main__":
    test_trust_history()
