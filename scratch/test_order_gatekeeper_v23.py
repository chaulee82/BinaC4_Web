"""
Kiểm thử toàn diện OrderGatekeeper V2.3:
1. check_pump_dump_trap (Rule 2.1 & Rule 2.2)
2. 5 Tầng Firewall Logic
3. Leverage Advisor (Tier S/A cấm futures grid, Tier B warning > 5x, Tier C warning > 3x)
4. Priority Queue cho Grid Tier B (Zone 30%, DC5 30%, Rebalance 25%, Trust 15%)
5. Giao diện Terminal UI 3 Vùng (Auto-Execute, Manual Approval, Veto Reject)
"""
import os
import sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from execution.order_gatekeeper import (
    check_pump_dump_trap,
    evaluate_order_gatekeeper_firewall,
    optimized_execution_geometry_module,
    render_gatekeeper_board
)
from core.historical_rater import calculate_grid_priority_queue


def test_pump_dump_traps():
    print("--- 1. KIỂM THỬ MODULE CHỐNG BẪY 15M (check_pump_dump_trap) ---")

    # Rule 2.1: Râu xả đỉnh 15M (Râu trên >= 60% và biên độ > 1%)
    # Nến: open=1.00, close=1.01, high=1.06, low=0.99
    # range = 0.07, amp = 0.07/0.99 ~ 7% (>1%)
    # body_top = 1.01, upper_wick = 1.06 - 1.01 = 0.05
    # wick_ratio = 0.05 / 0.07 ~ 71.4% (>= 60%)
    df_wick = pd.DataFrame([
        {'open': 0.98, 'high': 1.00, 'low': 0.97, 'close': 0.99},
        {'open': 0.99, 'high': 1.01, 'low': 0.98, 'close': 1.00},
        {'open': 1.00, 'high': 1.06, 'low': 0.99, 'close': 1.01}
    ])
    is_trap, msg = check_pump_dump_trap("AVA/USDT", klines_15m=df_wick)
    assert is_trap is True, "Phải phát hiện bẫy râu xả Rule 2.1"
    assert "Rule 2.1" in msg, f"Thông báo phải chứa Rule 2.1: {msg}"
    print(f"✅ Rule 2.1 Pass: {msg}")

    # Rule 2.2: Cây thông xả đỉnh (Nến đỏ nuốt >= 80% nến xanh bạo phát liền trước)
    # Nến 1: open=1.00, close=1.05 (tăng +5%)
    # Nến 2: open=1.05, close=1.005 (xả body = 0.045 / 0.05 = 90% >= 80%)
    df_dump = pd.DataFrame([
        {'open': 0.99, 'high': 1.00, 'low': 0.98, 'close': 1.00},
        {'open': 1.00, 'high': 1.05, 'low': 0.99, 'close': 1.05},
        {'open': 1.05, 'high': 1.05, 'low': 1.00, 'close': 1.005}
    ])
    is_trap2, msg2 = check_pump_dump_trap("BULL/USDT", klines_15m=df_dump)
    assert is_trap2 is True, "Phải phát hiện bẫy cây thông Rule 2.2"
    assert "Rule 2.2" in msg2, f"Thông báo phải chứa Rule 2.2: {msg2}"
    print(f"✅ Rule 2.2 Pass: {msg2}")


def test_priority_queue():
    print("\n--- 2. KIỂM THỬ GRID PRIORITY QUEUE TIER B ---")
    candidates = [
        # Mã 1: ARKM (Live sát đáy lower: live=0.122, lower=0.1205, upper=0.1438)
        # Zone score cao, DC5 cao
        {
            'symbol': 'ARKM/USDT',
            'live_price': 0.122,
            'lower': 0.1205,
            'upper': 0.1438,
            'dc5_score': 85.0,
            'rebalance_score': 70.0,
            'uy_tin_score': 58.0
        },
        # Mã 2: FET (Live ở lưng chừng: live=0.50, lower=0.40, upper=0.60)
        {
            'symbol': 'FET/USDT',
            'live_price': 0.50,
            'lower': 0.40,
            'upper': 0.60,
            'dc5_score': 60.0,
            'rebalance_score': 50.0,
            'uy_tin_score': 52.0
        }
    ]
    ranked = calculate_grid_priority_queue(candidates)
    assert ranked[0]['symbol'] == 'ARKM/USDT', "ARKM phải xếp ưu tiên #1"
    assert ranked[0]['grid_priority_rank'] == 1, "Rank phải là 1"
    print(f"✅ Priority Queue Pass: Top 1 = {ranked[0]['symbol']} (Điểm: {ranked[0]['grid_priority_score']}đ)")


def test_gatekeeper_v23_full_pipeline():
    print("\n--- 3. KIỂM THỬ TOÀN BỘ PIPELINE & GIAO DIỆN 3 VÙNG GATEKEEPER V2.3 ---")

    # Giả lập danh sách lệnh đầu vào từ Tầng 2
    raw_orders = [
        # 1. PEPE: Tier S (82.5đ), Động cơ 2 (Bắt đáy), vượt qua 5 tầng -> AUTO_EXECUTE
        {
            'symbol': 'PEPE/USDT',
            'order_type': 'SPOT_OCO',
            'entry': 0.0000100,
            'raw_sl': 0.0000095,
            'raw_tp': 0.0000120,
            'uy_tin_score': 82.5,
            'tier_label': '💎 Tier S',
            'engine': 'DC2',
            'engine_label': 'Động cơ 2 (Bắt Đáy)',
            'strategy_desc': 'Bắt Đáy - Siêu Pullback'
        },
        # 2. ARKM: Tier B (58.0đ), Mở Futures Grid 12x -> Leverage warning hạ về 5x, APPROVED -> AUTO_EXECUTE
        {
            'symbol': 'ARKM/USDT',
            'order_type': 'GRID',
            'entry': 0.1250,
            'trigger': 0.1250,
            'lower': 0.1205,
            'upper': 0.1438,
            'raw_sl': 0.1100,
            'raw_tp': 0.1500,
            'uy_tin_score': 58.0,
            'tier_label': 'Tier B',
            'engine': 'DC5',
            'strategy_desc': 'FUTURES GRID 12x',
            'leverage': 12,
            'market_type': 'FUTURES',
            'live_price': 0.1220,
            'dc5_score': 85.0,
            'rebalance_score': 70.0
        },
        # 3. BMT: Tier A (67.9đ), Động cơ 4 (Hot Trend), nhưng BTC gãy MA25 1H -> MANUAL_APPROVAL (LOCKED)
        {
            'symbol': 'BMT/USDT',
            'order_type': 'SPOT_OCO',
            'entry': 0.350,
            'raw_sl': 0.330,
            'raw_tp': 0.420,
            'uy_tin_score': 67.9,
            'tier_label': 'Tier A',
            'engine': 'DC4',
            'engine_label': 'Động cơ 4 (Hot Trend)',
            'macro_risk': True,
            'macro_risk_reason': 'BTC gãy MA25 1H - Fakeout Risk'
        },
        # 4. AVA: Động cơ 2, nhưng nến 15M có râu xả đỉnh 65% (Rule 2.1) -> VETO_REJECT
        {
            'symbol': 'AVA/USDT',
            'order_type': 'SPOT_OCO',
            'entry': 2.50,
            'raw_sl': 2.35,
            'raw_tp': 2.90,
            'uy_tin_score': 55.0,
            'tier_label': 'Tier B',
            'engine': 'DC2',
            'engine_label': 'Động cơ 2 (Bắt Đáy)'
        },
        # 5. DASH: Tier A (71.0đ), Mở Futures Grid -> REJECT (CẤM Futures Grid cho Tier S/A)
        {
            'symbol': 'DASH/USDT',
            'order_type': 'GRID',
            'entry': 28.5,
            'trigger': 28.5,
            'lower': 25.0,
            'upper': 35.0,
            'uy_tin_score': 71.0,
            'tier_label': 'Tier A',
            'engine': 'DC1',
            'strategy_desc': 'FUTURES GRID',
            'leverage': 5,
            'market_type': 'FUTURES'
        }
    ]

    # Cung cấp nến 15m sạch cho các mã không dính bẫy
    df_clean = pd.DataFrame([
        {'open': 1.00, 'high': 1.01, 'low': 0.995, 'close': 1.008},
        {'open': 1.008, 'high': 1.015, 'low': 1.005, 'close': 1.012},
        {'open': 1.012, 'high': 1.020, 'low': 1.010, 'close': 1.018}
    ])
    # Cung cấp nến 15m bẫy cho AVA (dính Rule 2.1)
    df_ava_trap = pd.DataFrame([
        {'open': 2.45, 'high': 2.50, 'low': 2.42, 'close': 2.48},
        {'open': 2.48, 'high': 2.65, 'low': 2.46, 'close': 2.51}  # wick = 2.65 - 2.51 = 0.14 / 0.19 = 73.6%
    ])
    klines_map = {
        'PEPE/USDT': df_clean,
        'ARKM/USDT': df_clean,
        'BMT/USDT': df_clean,
        'DASH/USDT': df_clean,
        'AVA/USDT': df_ava_trap
    }

    final_orders = optimized_execution_geometry_module(raw_orders, klines_cache_map=klines_map)

    # In giao diện theo chuẩn V2.3
    render_gatekeeper_board(final_orders)

    # Kiểm tra assertions
    order_map = {o['symbol']: o for o in final_orders}

    # PEPE -> AUTO_EXECUTE
    assert order_map['PEPE/USDT']['status'] == 'AUTO_EXECUTE', "PEPE phải là AUTO_EXECUTE"

    # ARKM -> AUTO_EXECUTE, kèm WARNING 12x
    assert order_map['ARKM/USDT']['status'] == 'AUTO_EXECUTE', "ARKM phải là AUTO_EXECUTE"
    assert "Đòn bẩy 12x quá cao" in order_map['ARKM/USDT']['leverage_warning'], "ARKM phải có warning đòn bẩy"

    # BMT -> MANUAL_APPROVAL (Khóa nòng BTC)
    assert order_map['BMT/USDT']['status'] == 'MANUAL_APPROVAL', "BMT phải là MANUAL_APPROVAL"
    assert "Fakeout Risk" in order_map['BMT/USDT']['locked_reason'], "BMT phải có lý do khóa BTC"

    # AVA -> VETO_REJECT (Rule 2.1)
    assert order_map['AVA/USDT']['status'] == 'VETO_REJECT', "AVA phải bị VETO_REJECT"
    assert "Rule 2.1" in order_map['AVA/USDT']['veto_reason'], "AVA phải dính Rule 2.1"

    # DASH -> VETO_REJECT (CẤM Futures Grid Tier A)
    assert order_map['DASH/USDT']['status'] == 'VETO_REJECT', "DASH phải bị VETO_REJECT"
    assert "CẤM Futures Grid" in order_map['DASH/USDT']['veto_reason'], "DASH phải bị cấm Futures Grid"

    print("\n🎉 TẤT CẢ CÁC ĐẶC TẢ V2.3 ĐÃ HOÀN TOÀN ĐẠT CHUẨN!")


if __name__ == "__main__":
    test_pump_dump_traps()
    test_priority_queue()
    test_gatekeeper_v23_full_pipeline()
