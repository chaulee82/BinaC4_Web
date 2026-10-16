"""
File: scratch/test_trust_history.py
Kiểm thử toàn diện mô-đun trust_history.py (MOD-TRUST-HIST-01):
1. Khởi tạo DB, kiểm tra schema, WAL mode, indexes.
2. Phiên 1 (Khởi tạo): Chưa có dữ liệu lịch sử -> top_gainers, top_losers rỗng.
3. Phiên 2: Có Delta 1, Delta 4 tạm lấy phiên 1.
4. Phiên 5+: Delta 1 lấy phiên 4, Delta 4 lấy phiên 1.
5. Kiểm tra đầy đủ ma trận đánh giá động học (Bứt phá mạnh, Tích lũy tăng, Xả dốc mạnh, Hạ nhiệt ngắn, Ổn định).
6. Kiểm tra cơ chế tự động dọn dẹp (auto-pruning) xoay vòng snapshot.
7. Hiển thị bảng print_delta_tables.
"""

import os
import sys
import sqlite3
import shutil
from datetime import datetime, timedelta

# Đảm bảo import được module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trust_history import init_db, record_and_analyze, print_delta_tables

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
    print("🧪 BẮT ĐẦU KIỂM THỬ: TRUST SCORE HISTORY & DELTA AUDIT ENGINE")
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

    # 2. Phiên 1: Khởi tạo
    session1_time = "2026-10-15 10:00:00"
    session1_data = [
        {"symbol": "AVA", "tier": "Tier A", "total": 60.0, "mong": 25.0, "day": 25.0, "tien": 10.0},
        {"symbol": "ZK", "tier": "Tier S", "total": 85.0, "mong": 35.0, "day": 35.0, "tien": 15.0},
        {"symbol": "NIL", "tier": "Tier B", "total": 52.0, "mong": 20.0, "day": 22.0, "tien": 10.0},
        {"symbol": "DOGE", "tier": "Tier F", "total": 20.0, "mong": 10.0, "day": 10.0, "tien": 0.0},
        {"symbol": "SOL", "tier": "Tier A", "total": 70.0, "mong": 30.0, "day": 30.0, "tien": 10.0},
        {"symbol": "NEAR", "tier": "Tier A", "total": 55.0, "mong": 22.0, "day": 23.0, "tien": 10.0},
    ]
    g1, l1 = record_and_analyze(session1_data, keep_snapshots=24, db_path=TEST_DB, scan_time=session1_time)
    assert len(g1) == 0, f"Phiên 1 chưa có lịch sử, g1 phải rỗng nhưng nhận: {len(g1)}"
    assert len(l1) == 0, f"Phiên 1 chưa có lịch sử, l1 phải rỗng nhưng nhận: {len(l1)}"
    print("✅ Phiên 1 (Khởi tạo): g1 và l1 rỗng chính xác (chưa đủ dữ liệu 2 phiên).")
    print_delta_tables(g1, l1, db_path=TEST_DB)

    # 3. Phiên 2: Bắt đầu có Delta 1 và Delta 4 (lấy phiên 1)
    session2_time = "2026-10-15 12:00:00"
    session2_data = [
        # AVA: Tăng mạnh (60 -> 68, +8đ)
        {"symbol": "AVA", "tier": "Tier S", "total": 68.0, "mong": 28.0, "day": 28.0, "tien": 12.0},
        # ZK: Giảm (85 -> 78, -7đ) -> Cảnh báo
        {"symbol": "ZK", "tier": "Tier A", "total": 78.0, "mong": 32.0, "day": 32.0, "tien": 14.0},
        # NIL: Tăng nhẹ (52 -> 54, +2đ)
        {"symbol": "NIL", "tier": "Tier B", "total": 54.0, "mong": 21.0, "day": 23.0, "tien": 10.0},
        # DOGE: Tăng nhưng < 50đ (20 -> 25) -> Không lọt Top 10
        {"symbol": "DOGE", "tier": "Tier F", "total": 25.0, "mong": 12.0, "day": 13.0, "tien": 0.0},
        # SOL: Giữ nguyên (70 -> 70, 0đ)
        {"symbol": "SOL", "tier": "Tier A", "total": 70.0, "mong": 30.0, "day": 30.0, "tien": 10.0},
    ]
    g2, l2 = record_and_analyze(session2_data, keep_snapshots=24, db_path=TEST_DB, scan_time=session2_time)
    print("✅ Phiên 2:")
    assert any(x['symbol'] == 'AVA' for x in g2), "AVA phải có trong top_gainers phiên 2"
    ava_stat = next(x for x in g2 if x['symbol'] == 'AVA')
    assert ava_stat['delta_1'] == 8.0, f"AVA delta_1 phải là 8.0, nhận: {ava_stat['delta_1']}"
    assert ava_stat['delta_4'] == 8.0, f"AVA delta_4 (phiên 1) phải là 8.0, nhận: {ava_stat['delta_4']}"
    assert ava_stat['eval'] == "🚀 Bứt phá mạnh", f"AVA eval phải là Bứt phá mạnh, nhận: {ava_stat['eval']}"

    assert any(x['symbol'] == 'ZK' for x in l2), "ZK phải có trong top_losers phiên 2"
    zk_stat = next(x for x in l2 if x['symbol'] == 'ZK')
    assert zk_stat['delta_1'] == -7.0, f"ZK delta_1 phải là -7.0, nhận: {zk_stat['delta_1']}"
    assert zk_stat['delta_4'] == -7.0, f"ZK delta_4 phải là -7.0, nhận: {zk_stat['delta_4']}"
    assert zk_stat['eval'] == "⛔ Xả dốc mạnh", f"ZK eval phải là Xả dốc mạnh, nhận: {zk_stat['eval']}"

    assert not any(x['symbol'] == 'DOGE' for x in g2), "DOGE (< 50đ) không được có trong top_gainers"
    print_delta_tables(g2, l2)

    # 4. Tạo các phiên 3, 4, 5 để test multi-horizon đầy đủ (T1 và T4)
    # Phiên 3
    record_and_analyze([
        {"symbol": "AVA", "tier": "Tier S", "total": 72.0},
        {"symbol": "ZK", "tier": "Tier A", "total": 75.0},
        {"symbol": "NIL", "tier": "Tier B", "total": 55.0},
        {"symbol": "SOL", "tier": "Tier A", "total": 71.0},
    ], keep_snapshots=24, db_path=TEST_DB, scan_time="2026-10-15 14:00:00")

    # Phiên 4
    record_and_analyze([
        {"symbol": "AVA", "tier": "Tier S", "total": 75.0},
        {"symbol": "ZK", "tier": "Tier A", "total": 74.0},
        {"symbol": "NIL", "tier": "Tier B", "total": 56.0},
        {"symbol": "SOL", "tier": "Tier A", "total": 69.0},
        {"symbol": "NEAR", "tier": "Tier A", "total": 60.0},
    ], keep_snapshots=24, db_path=TEST_DB, scan_time="2026-10-15 16:00:00")

    # Phiên 5 (T0 = 18:00):
    # T1 = Phiên 4 (16:00)
    # T4 = Phiên 1 (10:00)
    session5_time = "2026-10-15 18:00:00"
    session5_data = [
        # AVA: 16:00 là 75.0, 10:00 là 60.0. Giờ là 82.0 -> d1 = 82 - 75 = 7.0, d4 = 82 - 60 = 22.0 -> Bứt phá mạnh
        {"symbol": "AVA", "tier": "Tier S", "total": 82.0, "mong": 35.0, "day": 35.0, "tien": 12.0},
        # ZK: 16:00 là 74.0, 10:00 là 85.0. Giờ là 70.0 -> d1 = 70 - 74 = -4.0, d4 = 70 - 85 = -15.0 -> Xả dốc mạnh
        {"symbol": "ZK", "tier": "Tier A", "total": 70.0, "mong": 30.0, "day": 30.0, "tien": 10.0},
        # NIL: 16:00 là 56.0, 10:00 là 52.0. Giờ là 57.0 -> d1 = +1.0, d4 = +5.0 -> Tích lũy tăng
        {"symbol": "NIL", "tier": "Tier B", "total": 57.0, "mong": 22.0, "day": 25.0, "tien": 10.0},
        # SOL: 16:00 là 69.0, 10:00 là 70.0. Giờ là 68.0 -> d1 = -1.0, d4 = 68 - 70 = -2.0 -> Hạ nhiệt ngắn
        {"symbol": "SOL", "tier": "Tier A", "total": 68.0, "mong": 29.0, "day": 29.0, "tien": 10.0},
        # NEAR: 10:00 là 55.0, 16:00 là 60.0. Giờ là 60.0 -> d1 = 0.0, d4 = +5.0 -> Vẫn vào Top Tăng Uy Tín
        {"symbol": "NEAR", "tier": "Tier A", "total": 60.0, "mong": 25.0, "day": 25.0, "tien": 10.0},
    ]
    g5, l5 = record_and_analyze(session5_data, keep_snapshots=24, db_path=TEST_DB, scan_time=session5_time)
    print("✅ Phiên 5 (Đầy đủ T1 và T4):")
    ava_5 = next(x for x in g5 if x['symbol'] == 'AVA')
    assert ava_5['delta_1'] == 7.0, f"AVA d1 mong đợi 7.0, nhận {ava_5['delta_1']}"
    assert ava_5['delta_4'] == 22.0, f"AVA d4 mong đợi 22.0, nhận {ava_5['delta_4']}"
    assert ava_5['eval'] == "🚀 Bứt phá mạnh"
    print(f"  AVA: d1={ava_5['delta_1']}, d4={ava_5['delta_4']} -> {ava_5['eval']}")

    near_5 = next(x for x in g5 if x['symbol'] == 'NEAR')
    assert near_5['delta_1'] == 0.0
    assert near_5['delta_4'] == 5.0
    print(f"  NEAR: d1={near_5['delta_1']}, d4={near_5['delta_4']} -> {near_5['eval']} (Đã lọt Top Tăng thành công)")

    nil_5 = next(x for x in g5 if x['symbol'] == 'NIL')
    assert nil_5['delta_1'] == 1.0
    assert nil_5['delta_4'] == 5.0
    assert nil_5['eval'] == "🟢 Tích lũy tăng"
    print(f"  NIL: d1={nil_5['delta_1']}, d4={nil_5['delta_4']} -> {nil_5['eval']}")

    zk_5 = next(x for x in l5 if x['symbol'] == 'ZK')
    assert zk_5['delta_1'] == -4.0
    assert zk_5['delta_4'] == -15.0
    assert zk_5['eval'] == "⛔ Xả dốc mạnh"
    print(f"  ZK: d1={zk_5['delta_1']}, d4={zk_5['delta_4']} -> {zk_5['eval']}")

    sol_5 = next(x for x in l5 if x['symbol'] == 'SOL')
    assert sol_5['delta_1'] == -1.0
    assert sol_5['delta_4'] == -2.0
    print(f"  SOL: d1={sol_5['delta_1']}, d4={sol_5['delta_4']} -> {sol_5['eval']}")

    print_delta_tables(g5, l5)

    # 5. Kiểm tra cơ chế tự động xoay vòng Pruning (keep_snapshots)
    # Hiện đang có 5 snapshots: 10:00, 12:00, 14:00, 16:00, 18:00
    # Thêm 25 snapshots với keep_snapshots=5 -> chỉ còn đúng 5 snapshots mới nhất
    base_time = datetime(2026, 10, 16, 0, 0, 0)
    for i in range(25):
        s_time = (base_time + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
        record_and_analyze([{"symbol": "BTC", "total": 90.0}], keep_snapshots=5, db_path=TEST_DB, scan_time=s_time)

    with sqlite3.connect(TEST_DB) as conn:
        snapshots = [r[0] for r in conn.execute("SELECT DISTINCT scan_time FROM score_history ORDER BY scan_time DESC").fetchall()]
        print(f"✅ Số lượng snapshot sau khi prune: {len(snapshots)} (mong đợi: 5)")
        assert len(snapshots) == 5, f"Số snapshot sau pruning phải là 5, nhận: {len(snapshots)}"
        # Đảm bảo snapshot cũ nhất là của đợt thêm mới gần nhất
        assert "2026-10-15" not in snapshots[-1], "Snapshot cũ năm 2026-10-15 phải bị xóa"

    cleanup()
    print("════════════════════════════════════════════════════════════════")
    print("🎉 TẤT CẢ KIỂM THỬ MOD-TRUST-HIST-01 HOÀN TOÀN THÀNH CÔNG!")
    print("════════════════════════════════════════════════════════════════")

if __name__ == "__main__":
    test_trust_history()
