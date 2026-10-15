"""
File: scratch/test_historical_rater.py
Mục đích: Kiểm thử độc lập và hiển thị BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM V2.0
(AGGRESSIVE DECAY & CASHFLOW MULTIPLIER)
Chứa đầy đủ các mã case study: ZK, STX, FIL, ARPA, CTK, WAL.
"""

import sys
import os
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.historical_rater import HistoricalDataQuery, render_profiles_table

def test_historical_rater():
    test_db = "scratch/test_historical_rater.db"
    if os.path.exists(test_db):
        os.remove(test_db)
        
    rater = HistoricalDataQuery(test_db)
    today = datetime.now()
    d0 = today.strftime('%Y-%m-%d')
    d1 = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    d2 = (today - timedelta(days=2)).strftime('%Y-%m-%d')
    d3 = (today - timedelta(days=3)).strftime('%Y-%m-%d')
    
    # 1. ZK: Siêu phẩm giữ vững 4 ngày (Móng 40đ, Đẩy 35đ, Tiền 18đ -> Multiplier x1.2 -> 100đ)
    for d in [d0, d1, d2, d3]:
        rater.record_daily_metric(d, "ZK", {
            "raw_base_score": 40.0,
            "dc2_sniper_count": 4, "dc3_breakout_count": 4, "dc4_hottrend_count": 4,
            "dc5_pingpong_score": 6,
            "optimal_tier": "🏆 [HẠNG S] VỤ NỔ VĨ MÔ"
        })

    # 2. STX: Tích lũy ổn định (Móng 25đ, Đẩy 32đ, Tiền 17đ -> Multiplier x1.2 -> 88.8đ)
    for d in [d0, d1, d2, d3]:
        rater.record_daily_metric(d, "STX", {
            "raw_base_score": 25.0,
            "dc2_sniper_count": 4, "dc3_breakout_count": 4, "dc4_hottrend_count": 3,
            "dc5_pingpong_score": 6
        })

    # 3. FIL: Móng 30đ, Đẩy 25đ, Tiền 10đ -> Multiplier x1.0 -> 65đ (Tier A Xu Hướng)
    for d in [d0, d1, d2, d3]:
        rater.record_daily_metric(d, "FIL", {
            "raw_base_score": 30.0,
            "dc2_sniper_count": 2, "dc3_breakout_count": 3, "dc4_hottrend_count": 3,
            "dc5_pingpong_score": 4
        })

    # 4. ARPA: Móng 15đ, Đẩy 20đ, Tiền 3đ (<= 5) -> Multiplier x0.5 (Trừng phạt) -> 19đ (Tier F)
    for d in [d0, d1, d2, d3]:
        rater.record_daily_metric(d, "ARPA", {
            "raw_base_score": 15.0,
            "dc2_sniper_count": 2, "dc3_breakout_count": 2, "dc4_hottrend_count": 2,
            "dc5_pingpong_score": 1
        })

    # 5. CTK: Dính NÉ GRID, mất thanh khoản -> 0đ
    rater.record_daily_metric(d0, "CTK", {
        "raw_base_score": 0.0,
        "grid_status": "⛔ NÉ GRID",
        "dc2_sniper_count": 1, "dc3_breakout_count": 1, "dc4_hottrend_count": 0,
        "dc5_pingpong_score": 0
    })

    # 6. WAL: FOMO Trend (Móng 10đ, Đẩy 37đ, Tiền 14đ -> Multiplier x1.0 -> 61đ, Tier A- FOMO)
    for d in [d0, d1, d2, d3]:
        rater.record_daily_metric(d, "WAL", {
            "raw_base_score": 10.0,
            "dc2_sniper_count": 4, "dc3_breakout_count": 5, "dc4_hottrend_count": 4,
            "dc5_pingpong_score": 5
        })

    profiles = rater.query_all_14_day_profiles()
    render_profiles_table(profiles, "BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM V2.0 (AGGRESSIVE DECAY & CASHFLOW MULTIPLIER)")

if __name__ == "__main__":
    test_historical_rater()
