import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


from execution.order_gatekeeper import (
    optimized_execution_geometry_module,
    render_gatekeeper_board,
    extract_clean_summary_orders
)

def test_spec_examples():
    sample_clean_orders = [
        {
            "symbol": "SAND/USDT",
            "order_type": "SPOT_OCO",
            "entry": 0.05757,
            "raw_sl": 0.05728,
            "raw_tp": 0.06509,
            "uy_tin_score": 79.0,
            "tier_label": "Tier A",
            "engine": "DC2",
            "strategy_desc": "Bắt Đáy - Siêu Pullback"
        },
        {
            "symbol": "NEAR/USDT",
            "order_type": "GRID",
            "lower": 4.241,
            "upper": 6.476,
            "grids": 35,
            "uy_tin_score": 85.0,
            "tier_label": "Tier S",
            "engine": "DC1",
            "strategy_desc": "GRID TÍCH LŨY"
        },
        {
            "symbol": "INJ/USDT",
            "order_type": "GRID",
            "lower": 7.20,
            "upper": 9.54,
            "grids": 35,
            "entry": 8.533,
            "raw_sl": 8.486,
            "raw_tp": 8.811,
            "sl": 6.541,
            "uy_tin_score": 62.7,
            "tier_label": "Tier A",
            "engine": "DC2",
            "strategy_desc": "GRID TÍCH LŨY"
        },
        {
            "symbol": "TIA/USDT",
            "order_type": "SPOT_OCO",
            "entry": 0.6284,
            "raw_sl": 0.6251,
            "raw_tp": 0.7690,
            "uy_tin_score": 52.8,
            "tier_label": "Tier B",
            "engine": "DC2",
            "strategy_desc": "Bắt Đáy - Siêu Pullback"
        }
    ]

    final_orders = optimized_execution_geometry_module(sample_clean_orders)
    
    # 1. Kiểm tra SAND/USDT: Chọn mốc tâm lý gần nhất đạt chuẩn R:R >= 1:2.0 (0.06000)
    sand = final_orders[0]
    assert sand["status"] == "AUTO_EXECUTE", f"Expected AUTO_EXECUTE but got {sand['status']}"
    assert sand["volume_pct"] == 1.0
    assert abs(sand["sl"] - 0.05670) < 0.0001, f"Expected safe SL ~0.05670, got {sand['sl']}"
    assert abs(sand["tp"] - 0.06000) < 0.0001, f"Expected safe TP ~0.06000 (Mốc tâm lý), got {sand['tp']}"
    assert sand["auto_breakeven"] is True
    assert "grid_tp2_optimized" in sand, "sand phải có grid_tp2_optimized"
    sand_grid = sand["grid_tp2_optimized"]
    assert sand_grid["grids"] == 24, "Lưới dự phòng phải cố định 24 Lưới"
    assert sand_grid["upper"] >= round(sand["entry"] * 1.20, 5), f"Đỉnh lưới {sand_grid['upper']} phải đạt tối thiểu +20% so với Entry {sand['entry']}"
    assert sand_grid["sl"] == round(sand["entry"] * 0.88, 5), f"SL phải ghim cứng ở -12% ({sand_grid['sl']})"
    assert sand_grid["lower"] >= round(sand_grid["sl"] * 1.015, 5), "Đáy lưới phải cách SL tối thiểu +1.5%"

    # 2. Kiểm tra NEAR/USDT
    near = final_orders[1]
    assert near["status"] == "AUTO_EXECUTE"
    assert near["volume_pct"] == 1.0
    assert near["grids"] == 24
    assert near["upper"] >= round(near["entry"] * 1.20, 3)
    assert near["sl"] == round(near["entry"] * 0.88, 3)
    assert near["lower"] >= round(near["sl"] * 1.015, 3)

    # 3. Kiểm tra TIA/USDT: Chọn mốc tâm lý gần nhất đạt chuẩn R:R >= 1:2.0 (0.6500)
    tia = final_orders[3]
    assert tia["status"] == "MANUAL_APPROVAL", f"Expected MANUAL_APPROVAL but got {tia['status']}"
    assert tia["volume_pct"] == 0.3
    assert abs(tia["sl"] - 0.6189) < 0.001, f"Expected safe SL ~0.6189, got {tia['sl']}"
    assert abs(tia["tp"] - 0.6500) < 0.001, f"Expected safe TP ~0.6500 (Mốc tâm lý), got {tia['tp']}"

    print("OUTPUT TERMINAL UI RENDERED:")
    render_gatekeeper_board(final_orders)

    print("\n✅ TẤT CẢ CÁC BƯỚC TEST KIỂM THỬ ĐẶC TẢ ĐÃ PASS 100%!")


def test_extract_adapter():
    from views.summary_board import SignalRecord
    
    mock_summary_res = {
        "spot": [
            SignalRecord(
                symbol="SAND",
                engine_source="DC2",
                target_score=75.0,
                setup_line="⚙️ SETUP: In=0.05757 | SL=0.05728(-0.5%) | TP=0.06509(+13.1%) | R/R=1:2.6"
            ),
            SignalRecord(
                symbol="TIA",
                engine_source="DC2",
                target_score=50.0,
                setup_line="⚙️ SETUP: In=0.6284 | SL=0.6251(-0.5%) | TP=0.7690(+22.4%) | R/R=1:4.0"
            )
        ],
        "grid_tp2": [
            SignalRecord(
                symbol="NEAR",
                engine_source="DC1",
                target_score=80.0,
                grid_tp2_line="🎯 [GRID TP2 - NEAR] 4.241 - 6.476 | 35L | Trig: 5.0 | SL: 4.0 | TP: 6.476"
            )
        ],
        "wide_grid": [],
        "spot_btc_locked": False
    }

    mock_profiles = [
        {"symbol": "SAND", "total_score": 79.0, "tier": "Tier A"},
        {"symbol": "NEAR", "total_score": 85.0, "tier": "Tier S (Kim Cương)"},
        {"symbol": "TIA", "total_score": 52.8, "tier": "Tier B (Cửa Hậu)"}
    ]

    orders = extract_clean_summary_orders(mock_summary_res, mock_profiles)
    assert len(orders) == 3
    final = optimized_execution_geometry_module(orders)
    assert len(final) == 3
    print("\n[TEST ADAPTER] Render từ Mock Summary Board + 14-Day Profiles:")
    render_gatekeeper_board(final)
    print("✅ TEST ADAPTER PASS 100%!")


def test_money_flow_and_sorting():
    from views.summary_board import SignalRecord
    
    mock_summary_res = {
        "spot": [
            SignalRecord(
                symbol="SAND",
                engine_source="DC2",
                target_score=75.0,
                money_flow=0.0,
                setup_line="⚙️ SETUP: In=0.05757 | SL=0.05728(-0.5%) | TP=0.06509(+13.1%) | R/R=1:2.6"
            )
        ],
        "top_money_flow": [
            SignalRecord(
                symbol="AVA",
                engine_source="DC4",
                target_score=65.2,
                money_flow=1.7,
                setup_line="⚙️ SETUP: In=0.3735 | SL=0.3599(-3.6%) | TP=0.4642(+24.3%) | R/R=1:6.7 | 💥 TIỀN BẠO PHÁT (1.7x)",
                grid_tp2_line="🎯 [GRID TP2 - AVA] 0.2626 - 0.4642 | 24L | Trig: 0.3735"
            ),
            SignalRecord(
                symbol="API3",
                engine_source="DC3",
                target_score=85.0,
                money_flow=8.5,
                setup_line="⚙️ SETUP: In=1.850 | SL=1.750(-5.4%) | TP=2.050(+10.8%) | R/R=1:2.0 | 💥 TIỀN BẠO PHÁT (8.5x)"
            )
        ],
        "grid_tp2": [
            SignalRecord(
                symbol="AVA",
                engine_source="DC4",
                target_score=65.2,
                grid_tp2_line="🎯 [GRID TP2 - AVA] 0.2626 - 0.4642 | 24L | Trig: 0.3735 | SL: 0.1653 | TP: 0.4642"
            )
        ],
        "wide_grid": [],
        "spot_btc_locked": False
    }

    mock_profiles = [
        {"symbol": "SAND", "total_score": 75.0, "tier": "Tier A"},
        {"symbol": "AVA", "total_score": 65.2, "tier": "Tier A (Xu Hướng)"},
        {"symbol": "API3", "total_score": 85.0, "tier": "Tier S (Kim Cương)"}
    ]

    orders = extract_clean_summary_orders(mock_summary_res, mock_profiles)
    assert len(orders) == 4, f"Expected 4 orders (1 sand + 2 top_mf + 1 grid), got {len(orders)}"
    
    final = optimized_execution_geometry_module(orders)
    print("\n[TEST MONEY FLOW & DUAL COIN PRESENTATION (AVA)] Render:")
    render_gatekeeper_board(final)
    print("✅ TEST MONEY FLOW & DUAL COIN PASS 100%!")


if __name__ == "__main__":
    test_spec_examples()
    test_extract_adapter()
    test_money_flow_and_sorting()

