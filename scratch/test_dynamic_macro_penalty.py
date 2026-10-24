"""
Kiểm thử Ma Trận Phạt Điểm Vĩ Mô V2.0 (Dynamic Macro Penalty Matrix)
Kiểm tra chính xác công thức và các ca kiểm thử mô phỏng theo yêu cầu.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import sqlite3
from datetime import datetime, timedelta
from core.historical_rater import HistoricalDataQuery, render_profiles_table

def test_dynamic_macro_penalties():
    db_path = "scratch/test_penalty_v2.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    rater = HistoricalDataQuery(db_path)
    
    today = datetime.now()
    t0_str = today.strftime('%Y-%m-%d')
    t1_str = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    t2_str = (today - timedelta(days=2)).strftime('%Y-%m-%d')
    t3_str = (today - timedelta(days=3)).strftime('%Y-%m-%d')
    
    # 1. PEPE: Mã khỏe, không vi phạm
    # Mong=32, Day=25, Tien=12 => raw_total ~ 69đ
    for dt_str in [t3_str, t2_str, t1_str, t0_str]:
        rater.record_session_score(f"{dt_str} 12:00:00", 'PEPE', 32.0, 25.0, 12.0)
        
    # 2. BMT: Tích lũy tốt nhưng Live bị Né Grid + RSI 1H >= 75
    # Mong=34, Day=24, Tien=10 => raw_total ~ 68đ
    for dt_str in [t3_str, t2_str, t1_str, t0_str]:
        rater.record_session_score(f"{dt_str} 12:00:00", 'BMT', 34.0, 24.0, 10.0)
        
    # 3. AVA: Bị Suy Yếu C2 (d24 <= -8) + Né Grid
    # T3, T2, T1 cao (Mong=34, Day=24, Tien=10 = 68đ), T0 tụt Day xuống 15đ => d24 = -9đ
    for dt_str in [t3_str, t2_str, t1_str]:
        rater.record_session_score(f"{dt_str} 12:00:00", 'AVA', 34.0, 24.0, 10.0)
    rater.record_session_score(f"{t0_str} 12:00:00", 'AVA', 34.0, 15.0, 10.0)
    
    # 4. ERA: Gãy Supertrend 1D Cấp 3
    # Mong=25, Day=15, Tien=10 => raw_total ~ 50đ
    for dt_str in [t3_str, t2_str, t1_str, t0_str]:
        rater.record_session_score(f"{dt_str} 12:00:00", 'ERA', 25.0, 15.0, 10.0)

    # Dữ liệu từ các bảng vĩ mô phiên Live
    warning_map = {
        "ERA": {"level": 3, "label": "💀 CẤP 3: KHẨN CẤP (Gãy Trend 1D)", "trigger": "Gãy ST 1D"}
    }
    
    reb_map = {
        "PEPE": {"rank": 1, "status": "🛡️ GRID HẸP", "score": 60.0, "rsi_1h": 55.0},
        "BMT": {"rank": 8, "status": "⛔ NÉ GRID (Bơm Xả Râu Dài)", "score": 45.0, "rsi_1h": 76.5},
        "AVA": {"rank": 12, "status": "⛔ NÉ GRID (Cạn Cầu / Trượt Giá)", "score": 40.0, "rsi_1h": 60.0},
        "ERA": {"rank": 20, "status": "🛡️ GRID PULLBACK", "score": 35.0, "rsi_1h": 50.0}
    }
    
    downgrade_map = {
        "AVA": {"level": "CAP_2"}
    }

    profiles = rater.query_all_14_day_profiles(
        warning_map=warning_map,
        reb_map=reb_map,
        downgrade_map=downgrade_map
    )

    print("\n" + "=" * 90)
    print("KẾT QUẢ KIỂM THỬ MA TRẬN PHẠT VĨ MÔ V2.0:")
    print("=" * 90)
    for p in profiles:
        print(f"Mã: {p['symbol']:<5} | Gốc: {p['raw_score']:<5.1f} | Phạt: -{p['macro_penalty']:<4.1f} | Tổng: {p['total_score']:<5.1f} | {p['tier']:<22} | Tags: {p['penalty_tags']}")
    print("=" * 90)
    
    # Kiểm tra Assertions
    p_pepe = next(p for p in profiles if p['symbol'] == 'PEPE')
    assert p_pepe['macro_penalty'] == 0.0, "PEPE không được bị phạt"
    assert "Tier A" in p_pepe['tier'], "PEPE phải là Tier A"

    p_bmt = next(p for p in profiles if p['symbol'] == 'BMT')
    assert p_bmt['macro_penalty'] == 15.0, f"BMT phải bị phạt đúng 15.0đ, thực tế {p_bmt['macro_penalty']}"
    assert "Tier B" in p_bmt['tier'], f"BMT phải rớt xuống Tier B, thực tế {p_bmt['tier']}"

    p_ava = next(p for p in profiles if p['symbol'] == 'AVA')
    assert p_ava['macro_penalty'] == 18.0, f"AVA phải bị phạt đúng 18.0đ, thực tế {p_ava['macro_penalty']}"
    assert "Tier B" in p_ava['tier'], f"AVA phải rớt xuống Tier B, thực tế {p_ava['tier']}"

    p_era = next(p for p in profiles if p['symbol'] == 'ERA')
    assert p_era['macro_penalty'] == 12.0, f"ERA phải bị phạt đúng 12.0đ, thực tế {p_era['macro_penalty']}"
    assert "Tier C" in p_era['tier'], f"ERA phải rớt xuống Tier C, thực tế {p_era['tier']}"

    print("\n✅ TẤT CẢ CÁC ASSERTIONS ĐÃ PASS HOÀN HẢO!")
    
    # In thử bảng render UI
    render_profiles_table(profiles, "BẢNG TEST GIAO DIỆN TÍN NHIỆM V2.0")

if __name__ == "__main__":
    test_dynamic_macro_penalties()
