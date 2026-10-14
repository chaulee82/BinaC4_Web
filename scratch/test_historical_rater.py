"""
File: scratch/test_historical_rater.py
Mục đích: Kiểm thử độc lập và hiển thị BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY (PHIÊN BẢN CHI TIẾT NỀN MÓNG)
Chứa đầy đủ các mã case study: ZK, STX, FIL, ARPA, CTK.
"""

import sys
import os
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.historical_rater import HistoricalDataQuery, render_profiles_table

def test_historical_rater():
    test_db = "scratch/test_historical_rater.db"
    if os.path.exists(test_db):
        os.remove(test_db)
        
    rater = HistoricalDataQuery(test_db)
    today = datetime.now()
    d0 = today.strftime('%Y-%m-%d')
    
    # 1. ZK: Reb 53.7 (8.0đ), 3D 79.0 (11.8đ), 3B 75.0 (7.5đ) -> Móng 27.3đ | Đẩy 35.0đ | Tiền 16.2đ -> Tổng 78.5đ
    # dc2=4 (10đ), dc3=4 (12đ), dc4=4.33 (13đ) -> 35đ | dc5=8.1 (16.2đ)
    rater.record_daily_metric(d0, "ZK", {
        "b3a_score": 0.0, "b3b_score": 75.0,
        "rebalance_score": 53.7, "strict_3d_score": 79.0,
        "dc2_sniper_count": 4, "dc3_breakout_count": 4, "dc4_hottrend_count": 4, # Đẩy 10 + 12 + 13 = 35đ (được round)
        "dc5_pingpong_score": 8, # 16đ (round ~16.2)
        "penalty_level_3": 0, "penalty_xaray": 0, "penalty_fakeout": 0
    })

    # 2. STX: Reb 60.0 (9.0đ), 3D 80.0 (12.0đ), 3B 40.0 (4.0đ) -> Móng 25.0đ | Đẩy 32.0đ | Tiền 17.0đ -> Tổng 74.0đ
    rater.record_daily_metric(d0, "STX", {
        "b3a_score": 40.0, "b3b_score": 0.0,
        "rebalance_score": 60.0, "strict_3d_score": 80.0,
        "dc2_sniper_count": 4, "dc3_breakout_count": 4, "dc4_hottrend_count": 3, # Đẩy 10 + 12 + 10 = 32đ
        "dc5_pingpong_score": 8
    })

    # 3. FIL: Reb 68.0 (10.2đ), 3D 80.0 (12.0đ), 3A 80.0 (8.0đ) -> Móng 30.2đ | Đẩy 25.0đ | Tiền 13.0đ -> Tổng 68.2đ
    rater.record_daily_metric(d0, "FIL", {
        "b3a_score": 80.0, "b3b_score": 0.0,
        "rebalance_score": 68.0, "strict_3d_score": 80.0,
        "dc2_sniper_count": 2, "dc3_breakout_count": 3, "dc4_hottrend_count": 3, # Đẩy: 5 + 9 + 11 = 25đ
        "dc5_pingpong_score": 7
    })

    # 4. ARPA: Reb 20.0 (3.0đ), 3D 13.0 (2.0đ), 3A/3B 0.0 -> Móng 5.0đ | Đẩy 27.0đ | Tiền 0.0đ -> Tổng 32.0đ (Tier F Rủi Ro)
    rater.record_daily_metric(d0, "ARPA", {
        "b3a_score": 0.0, "b3b_score": 0.0,
        "rebalance_score": 20.0, "strict_3d_score": 13.33,
        "dc2_sniper_count": 4, "dc3_breakout_count": 3, "dc4_hottrend_count": 3, # Đẩy: 10 + 9 + 8 = 27đ
        "dc5_pingpong_score": 0
    })

    # 5. CTK: Reb 6.6 (1.0đ), 3D 0.0 (0.0đ), 3A/3B 0.0 -> Móng 1.0đ | Đẩy 17.5đ | Tiền 0.0đ -> Tổng 18.5đ (Tier F Rủi Ro)
    rater.record_daily_metric(d0, "CTK", {
        "b3a_score": 0.0, "b3b_score": 0.0,
        "rebalance_score": 6.67, "strict_3d_score": 0.0,
        "dc2_sniper_count": 3, "dc3_breakout_count": 2, "dc4_hottrend_count": 1, # Đẩy: 7.5 + 6 + 4 = 17.5đ
        "dc5_pingpong_score": 0
    })

    # 6. WAL (FOMO Trend): Móng 10đ (<15đ) nhưng Đẩy 37đ + Tiền 14đ = 61đ -> Tier A- (FOMO Trend)
    rater.record_daily_metric(d0, "WAL", {
        "b3a_score": 0.0, "b3b_score": 0.0,
        "rebalance_score": 40.0, "strict_3d_score": 26.67, # Móng = 6 + 4 = 10đ
        "dc2_sniper_count": 4, "dc3_breakout_count": 5, "dc4_hottrend_count": 4, # Đẩy 37đ
        "dc5_pingpong_score": 7 # Tiền 14đ -> 61đ
    })

    profiles = rater.query_all_14_day_profiles()
    render_profiles_table(profiles, "BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY (PHIÊN BẢN CHI TIẾT NỀN MÓNG)")

if __name__ == "__main__":
    test_historical_rater()
