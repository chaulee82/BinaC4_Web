"""
Kiểm thử toàn diện Module 1: Scoring Engine V2.0
- calculate_raw_base_score (4 tiêu chí, cap 40đ)
- calculate_aggressive_decay (50/25/15/10 & normalization)
- calculate_final_rating (Cashflow multiplier x1.2 / x1.0 / x0.5)
- End-to-end HistoricalDataQuery V2.0 render_profiles_table
"""

import sys
import os
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.historical_rater import (
    calculate_raw_base_score,
    calculate_aggressive_decay,
    calculate_final_rating,
    HistoricalDataQuery,
    render_profiles_table
)

def test_scoring_v2_logic():
    print("--- 1. Test calculate_raw_base_score ---")
    # Coin A: Siêu phẩm (3D Strict full 25đ, Reb Top 1 10đ, 3A Top 5 5đ, Confluence Tier S 10đ)
    # Tổng thô = 25 + 10 + 5 + 10 = 50đ -> Khóa trần 40đ
    coin_a = {
        "Price": 105.0, "MA7_3D": 100.0, "MA99_3D": 80.0, "ST_4H": "Green",
        "Grid_Rank": 1, "Grid_Status": "🟢 OK",
        "In_Top_3A": 1, "In_Top_3B": 0,
        "Optimal_Conclusion_Tier": "🏆 [HẠNG S] VỤ NỔ VĨ MÔ"
    }
    score_a = calculate_raw_base_score(coin_a)
    print("Coin A (Siêu phẩm - cap 40):", score_a)
    assert score_a == 40.0, f"Expected 40.0, got {score_a}"

    # Coin B: 3D chưa cắt MA99 (15đ) + Grid Top 10 (5đ) + Không 3A/3B (0đ) + Không Hội tụ (0đ) = 20đ
    coin_b = {
        "Price": 105.0, "MA7_3D": 100.0, "MA99_3D": 120.0, "ST_4H": "Green",
        "Grid_Rank": 8, "Grid_Status": "🟢 OK",
        "In_Top_3A": 0, "In_Top_3B": 0,
        "Optimal_Conclusion_Tier": ""
    }
    score_b = calculate_raw_base_score(coin_b)
    print("Coin B (Móng 20đ):", score_b)
    assert score_b == 20.0, f"Expected 20.0, got {score_b}"

    # Coin C: Dính NÉ GRID -> Tiêu chí Rebalance 0đ
    coin_c = {
        "Price": 90.0, "MA7_3D": 100.0, "MA99_3D": 80.0, "ST_4H": "Red",
        "Grid_Rank": 2, "Grid_Status": "⛔ NÉ GRID (Gãy Cấu Trúc 4H)",
        "In_Top_3A": 0, "In_Top_3B": 0,
        "Optimal_Conclusion_Tier": ""
    }
    score_c = calculate_raw_base_score(coin_c)
    print("Coin C (Né Grid - Móng 0đ):", score_c)
    assert score_c == 0.0, f"Expected 0.0, got {score_c}"

    print("\n--- 2. Test calculate_aggressive_decay ---")
    # Đủ 4 ngày: [40, 40, 40, 40] -> 40 * (0.50 + 0.25 + 0.15 + 0.10) = 40.0
    decay_full = calculate_aggressive_decay([40, 40, 40, 40])
    print("Decay 4 ngày đều 40:", decay_full)
    assert decay_full == 40.0

    # Rớt top ngày T-1: [40, 0, 40, 0] -> 40*0.50 + 0*0.25 + 40*0.15 + 0*0.10 = 20 + 6 = 26.0
    decay_gap = calculate_aggressive_decay([40, 0, 40, 0])
    print("Decay rớt top T-1 & T-3:", decay_gap)
    assert decay_gap == 26.0

    # Coin mới list (chỉ có T-0 và T-1): [30, 20]
    # w = [0.5, 0.25] -> sum = 0.75 -> norm = [0.5/0.75, 0.25/0.75] = [2/3, 1/3]
    # result = 30 * (2/3) + 20 * (1/3) = 20 + 6.6667 = 26.67
    decay_new = calculate_aggressive_decay([30, 20])
    print("Decay coin mới list 2 ngày:", round(decay_new, 2))
    assert abs(decay_new - 26.6667) < 0.01

    print("\n--- 3. Test calculate_final_rating ---")
    # Case Thưởng (Diem_tien >= 15 -> x1.2)
    # Móng=40, Đẩy=35, Tiền=18 -> Tổng thô = 93 -> 93 * 1.2 = 111.6 -> cap 100.0
    rate_super = calculate_final_rating({
        "Lich_Su_Mong": [40, 40, 40, 40],
        "Lich_Su_Day": [35, 35, 35, 35],
        "Lich_Su_Tien": [18, 18, 18, 18]
    })
    print("Rate Siêu Phẩm (x1.2, cap 100):", rate_super)
    assert rate_super == 100.0

    # Case Phạt (Diem_tien <= 5 -> x0.5)
    # Móng=20, Đẩy=20, Tiền=3 -> Tổng thô = 43 -> 43 * 0.5 = 21.5
    rate_dump = calculate_final_rating({
        "Lich_Su_Mong": [20, 20, 20, 20],
        "Lich_Su_Day": [20, 20, 20, 20],
        "Lich_Su_Tien": [3, 3, 3, 3]
    })
    print("Rate Cạn Tiền (x0.5):", rate_dump)
    assert rate_dump == 21.5

    # Case Trung Tính (Tiền = 10 -> x1.0)
    # Móng=25, Đẩy=25, Tiền=10 -> 60.0
    rate_normal = calculate_final_rating({
        "Lich_Su_Mong": [25, 25, 25, 25],
        "Lich_Su_Day": [25, 25, 25, 25],
        "Lich_Su_Tien": [10, 10, 10, 10]
    })
    print("Rate Bình Thường (x1.0):", rate_normal)
    assert rate_normal == 60.0

    print("\n✅ TẤT CẢ UNIT TESTS CỦA MODULE 1 ĐẠT CHUẨN 100%!")


def test_db_e2e():
    print("\n--- 4. Test End-to-End SQLite & Table Render ---")
    db_file = "scratch/test_v2_db.db"
    if os.path.exists(db_file):
        os.remove(db_file)

    rater = HistoricalDataQuery(db_file)

    # Nạp 4 ngày lịch sử cho 3 đồng coin:
    # SOL: Siêu phẩm giữ vững 4 ngày
    # ARB: Bị rớt top ngày T-1 và T-2
    # PEPE: Mới list từ hôm nay (1 ngày)
    dates = ["2026-10-15", "2026-10-14", "2026-10-13", "2026-10-12"]

    for d in dates:
        # SOL đều đặn
        rater.record_daily_metric(d, "SOL", {
            "raw_base_score": 40.0,
            "dc2_sniper_count": 4, "dc3_breakout_count": 4, "dc4_hottrend_count": 4, # Đẩy 35đ
            "dc5_pingpong_score": 6, # Tiền 18đ
            "optimal_tier": "🏆 [HẠNG S] VỤ NỔ VĨ MÔ"
        })

    # ARB chỉ có ngày T-0 và T-3 (bị mất T-1 và T-2)
    rater.record_daily_metric("2026-10-15", "ARB", {
        "raw_base_score": 25.0,
        "dc2_sniper_count": 2, "dc3_breakout_count": 2, "dc4_hottrend_count": 1,
        "dc5_pingpong_score": 2 # Tiền <= 5
    })
    rater.record_daily_metric("2026-10-12", "ARB", {
        "raw_base_score": 20.0,
        "dc2_sniper_count": 1, "dc3_breakout_count": 1, "dc4_hottrend_count": 1,
        "dc5_pingpong_score": 1
    })

    # PEPE chỉ xuất hiện hôm nay
    rater.record_daily_metric("2026-10-15", "PEPE", {
        "raw_base_score": 30.0,
        "dc2_sniper_count": 3, "dc3_breakout_count": 3, "dc4_hottrend_count": 3,
        "dc5_pingpong_score": 4 # Tiền 12đ
    })

    profiles = rater.query_all_14_day_profiles()
    render_profiles_table(profiles, "BẢNG TEST HỒ SƠ TÍN NHIỆM V2.0", date_range=rater.get_data_date_range())

if __name__ == "__main__":
    test_scoring_v2_logic()
    test_db_e2e()
