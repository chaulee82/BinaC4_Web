"""
Unit test kiểm thử toàn diện:
1. Pipeline Bảng 3B tách Data (Backend lưu dồi dào vào engine_3b_history) & View (Top 5 >= 70đ).
2. Nội suy tuyến tính Điểm 3D vào Móng Tín Nhiệm V2.0 (Bảng 2): Điểm_Thành_Phần_3D = (Điểm_Sóng_3B / 100) * 25đ.
3. T-0 components breakdown trong query_14_day_profile & render_profiles_table.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import sqlite3
import tempfile
import pandas as pd
from strategies.super_trend_3d import (
    save_engine_3b_records,
    get_last_all_super_wave_scores,
    B3B_MIN_SCORE
)
from core.historical_rater import (
    HistoricalDataQuery,
    calculate_raw_base_score,
    print_14_day_summary_board
)


def run_all_tests():
    print("=" * 70)
    print("🧪 BẮT ĐẦU KIỂM THỬ: PIPELINE BẢNG 3B & NỘI SUY TUYẾN TÍNH V2.0")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # TEST 1: Công thức quy đổi tuyến tính Điểm 3D vào Móng V2.0
    # -------------------------------------------------------------------------
    print("\n[TEST 1] Kiểm tra công thức tuyến tính calculate_raw_base_score:")
    # Mã 60đ Bảng 3B (không lên UI Bảng 3B) -> (60/100)*25 = 15.0đ Móng
    coin_60 = {
        "b3b_score": 60.0,
        "Grid_Rank": 999,
        "In_Top_3A": 0, "In_Top_3B": 0,
        "Optimal_Conclusion_Tier": ""
    }
    score_60 = calculate_raw_base_score(coin_60)
    print(f"  • Mã 60đ Bảng 3B -> Điểm Móng: {score_60:.1f}đ (Kỳ vọng: 15.0đ)")
    assert abs(score_60 - 15.0) < 1e-4, f"Lỗi: Kỳ vọng 15.0, nhận {score_60}"

    # Mã 80đ Bảng 3B -> (80/100)*25 = 20.0đ Móng
    coin_80 = {
        "b3b_score": 80.0,
        "Grid_Rank": 999,
        "In_Top_3A": 0, "In_Top_3B": 0,
        "Optimal_Conclusion_Tier": ""
    }
    score_80 = calculate_raw_base_score(coin_80)
    print(f"  • Mã 80đ Bảng 3B -> Điểm Móng: {score_80:.1f}đ (Kỳ vọng: 20.0đ)")
    assert abs(score_80 - 20.0) < 1e-4, f"Lỗi: Kỳ vọng 20.0, nhận {score_80}"

    # Mã 100đ Bảng 3B -> (100/100)*25 = 25.0đ Móng
    coin_100 = {
        "b3b_score": 100.0,
        "Grid_Rank": 999,
        "In_Top_3A": 0, "In_Top_3B": 0,
        "Optimal_Conclusion_Tier": ""
    }
    score_100 = calculate_raw_base_score(coin_100)
    print(f"  • Mã 100đ Bảng 3B -> Điểm Móng: {score_100:.1f}đ (Kỳ vọng: 25.0đ)")
    assert abs(score_100 - 25.0) < 1e-4, f"Lỗi: Kỳ vọng 25.0, nhận {score_100}"

    # Mã có cả 3B 60đ (15đ) + Top 1 Grid (10đ) + Hạng S Hội tụ (10đ) + Top 3A (5đ) = 40đ (Trần)
    coin_combo = {
        "b3b_score": 60.0,
        "Grid_Rank": 1,
        "Grid_Status": "🟢 OK",
        "In_Top_3A": 1, "In_Top_3B": 0,
        "Optimal_Conclusion_Tier": "🏆 [HẠNG S]"
    }
    score_combo = calculate_raw_base_score(coin_combo)
    print(f"  • Mã Combo (15+10+10+5=40đ cap) -> Điểm Móng: {score_combo:.1f}đ (Kỳ vọng: 40.0đ)")
    assert abs(score_combo - 40.0) < 1e-4, f"Lỗi: Kỳ vọng 40.0, nhận {score_combo}"
    print("  ✅ TEST 1 PASSED: Công thức quy đổi tuyến tính hoạt động hoàn hảo.")

    # -------------------------------------------------------------------------
    # TEST 2: Lưu trữ dồi dào vào engine_3b_history trong SQLite
    # -------------------------------------------------------------------------
    print("\n[TEST 2] Kiểm tra lưu vết SQLite engine_3b_history:")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        test_db = os.path.join(tmp_dir, "test_binac4.db")
        rater = HistoricalDataQuery(test_db)

        # Giả lập danh sách quét chứa cả mã dải 40-69đ và >= 70đ
        mock_3b_records = [
            {"symbol": "BTC", "score": 90.0, "pts": {"rail": 30, "cross": 25, "boll": 25, "flow": 10}, "mode": "RIDE_WAVE", "price": 68000, "ma7": 66000, "sl": 63000, "vol_ratio": 2.5},
            {"symbol": "ETH", "score": 75.0, "pts": {"rail": 30, "cross": 15, "boll": 15, "flow": 15}, "mode": "RAIL_SURF", "price": 2600, "ma7": 2500, "sl": 2400, "vol_ratio": 1.8},
            {"symbol": "SOL", "score": 60.0, "pts": {"rail": 15, "cross": 15, "boll": 15, "flow": 15}, "mode": "RAIL_SURF", "price": 150, "ma7": 145, "sl": 138, "vol_ratio": 1.4},
            {"symbol": "DOGE", "score": 45.0, "pts": {"rail": 15, "cross": 15, "boll": 0, "flow": 15}, "mode": "RAIL_SURF", "price": 0.12, "ma7": 0.11, "sl": 0.10, "vol_ratio": 1.2},
        ]

        inserted = save_engine_3b_records(mock_3b_records, db_path=test_db, scan_time="2026-10-17 12:00:00")
        print(f"  • Đã chèn {inserted} bản ghi vào engine_3b_history")
        assert inserted == 4, f"Lỗi: Kỳ vọng 4 bản ghi, nhận {inserted}"

        # Kiểm tra truy vấn trực tiếp từ bảng engine_3b_history
        with sqlite3.connect(test_db) as conn:
            cur = conn.cursor()
            rows = cur.execute("SELECT symbol, score FROM engine_3b_history ORDER BY score DESC").fetchall()
            print(f"  • Dữ liệu trong DB: {rows}")
            assert len(rows) == 4
            assert rows[2] == ("SOL", 60.0)
            assert rows[3] == ("DOGE", 45.0)

        # Kiểm tra hàm rater.query_latest_3b_score
        sol_sc = rater.query_latest_3b_score("SOL")
        doge_sc = rater.query_latest_3b_score("DOGE")
        print(f"  • Query latest score SOL: {sol_sc}đ | DOGE: {doge_sc}đ")
        assert sol_sc == 60.0
        assert doge_sc == 45.0
        print("  ✅ TEST 2 PASSED: Bảng engine_3b_history lưu trữ đầy đủ mọi dải điểm.")

        # -------------------------------------------------------------------------
        # TEST 3: Tích hợp print_14_day_summary_board và phân rã t0_components
        # -------------------------------------------------------------------------
        print("\n[TEST 3] Kiểm tra print_14_day_summary_board và bảng Bảng 2:")
        df_summary = pd.DataFrame([
            {"Symbol": "SOL/USDT", "TỔNG": 70.0, "Phân Loại Grid": "🟢 GRID ĐẸP"},
            {"Symbol": "BTC/USDT", "TỔNG": 85.0, "Phân Loại Grid": "🟢 GRID ĐẸP"},
        ])

        profiles = print_14_day_summary_board(
            db_path=test_db,
            df_summary=df_summary,
            super_wave_results=mock_3b_records, # Cung cấp đầy đủ danh sách kết quả
            scan_time="2026-10-17 12:00:00"
        )

        assert len(profiles) > 0, "Lỗi: Không trả về profile nào"
        sol_prof = next((p for p in profiles if p["symbol"] == "SOL"), None)
        assert sol_prof is not None, "Lỗi: Không tìm thấy profile của SOL"

        bdown = sol_prof.get("breakdown", {})
        t0 = bdown.get("t0_components", {})
        print(f"  • SOL t0_components: {t0}")
        print(f"  • SOL └ 3D(25) component: {t0.get('3d_strict')}đ (Kỳ vọng: 15.0đ)")
        assert abs(t0.get("3d_strict", 0.0) - 15.0) < 1e-4, f"Lỗi: Kỳ vọng 15.0đ, nhận {t0.get('3d_strict')}"
        print("  ✅ TEST 3 PASSED: Bảng 2 hiển thị chính xác điểm nội suy tuyến tính 3D.")

    print("\n" + "=" * 70)
    print("🎉 TẤT CẢ 3 BỘ TEST ĐÃ HOÀN TOÀN VƯỢT QUA! HỆ THỐNG ĐÃ SẴN SÀNG.")
    print("=" * 70)


if __name__ == "__main__":
    run_all_tests()
