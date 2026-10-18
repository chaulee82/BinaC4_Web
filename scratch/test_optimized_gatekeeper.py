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
    
    # 1. Kiểm tra SAND/USDT
    sand = final_orders[0]
    assert sand["status"] == "AUTO_EXECUTE", f"Expected AUTO_EXECUTE but got {sand['status']}"
    assert sand["volume_pct"] == 1.0
    assert abs(sand["sl"] - 0.05670) < 0.0001, f"Expected safe SL ~0.05670, got {sand['sl']}"
    assert abs(sand["tp"] - 0.05931) < 0.0001, f"Expected safe TP ~0.05931, got {sand['tp']}"
    assert sand["auto_breakeven"] is True
    assert "grid_tp2_optimized" in sand, "sand phải có grid_tp2_optimized"
    sand_grid = sand["grid_tp2_optimized"]
    assert sand_grid["grids"] == 24, "Lưới dự phòng phải cố định 24 Lưới"
    assert sand_grid["upper"] <= sand["tp"] + 1e-4, f"Đỉnh lưới {sand_grid['upper']} không được vượt Realistic TP {sand['tp']}"
    assert sand_grid["lower"] > sand_grid["raw_lower"], "Đáy lưới phải được bóp nâng lên 15%"


    # 2. Kiểm tra NEAR/USDT
    near = final_orders[1]
    assert near["status"] == "AUTO_EXECUTE"
    assert near["volume_pct"] == 1.0
    assert abs(near["lower"] - 4.576) < 0.001, f"Expected lower ~4.576, got {near['lower']}"
    assert abs(near["upper"] - 6.140) < 0.001, f"Expected upper ~6.140, got {near['upper']}"
    assert near["grids"] == 24

    # 3. Kiểm tra TIA/USDT
    tia = final_orders[3]
    assert tia["status"] == "MANUAL_APPROVAL", f"Expected MANUAL_APPROVAL but got {tia['status']}"
    assert tia["volume_pct"] == 0.3
    assert abs(tia["sl"] - 0.6189) < 0.001, f"Expected safe SL ~0.6189, got {tia['sl']}"
    assert abs(tia["tp"] - 0.6474) < 0.001, f"Expected safe TP ~0.6474, got {tia['tp']}"

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


if __name__ == "__main__":
    test_spec_examples()
    test_extract_adapter()

