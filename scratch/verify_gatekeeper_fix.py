import sys
import os

# Ensure workspace in sys.path
sys.path.insert(0, r"c:\DData\Source\wwwScr\BinaC4")

from execution.order_gatekeeper import (
    extract_clean_summary_orders,
    optimized_execution_geometry_module,
    render_gatekeeper_board
)

class MockSignal:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

print("=== TEST 1: BTC GATE BỊ KHÓA (spot_btc_locked = True) ===")
# Giả lập Bảng Tổng Kết khi BTC bị gãy MA7 3D khóa chốt chặn vĩ mô
mock_summary_btc_locked = {
    "spot_btc_locked": True,
    "spot": [
        MockSignal(
            symbol="SAND/USDT",
            engine_source="DC2",
            setup_line="Entry: 0.05757 | SL: 0.05671 | TP: 0.0593 | R:R: 1:2.0",
            grid_tp2_line="Dải: 0.057 - 0.06366 | 24 Lưới",
            money_flow=1.2,
            target_score=75.0
        )
    ],
    "top_money_flow": [
        MockSignal(
            symbol="API3/USDT",
            engine_source="DC3",
            setup_line="Entry: 1.85 | SL: 1.75 | TP: 2.05 | R:R: 1:2.0",
            grid_tp2_line="",
            money_flow=8.5,
            target_score=82.0
        )
    ],
    "grid_tp2": [],
    "wide_grid": []
}

mock_profiles = {
    "SAND": {"total_score": 75.0, "tier": "Tier A"},
    "API3": {"total_score": 82.0, "tier": "Tier S"}
}

clean = extract_clean_summary_orders(mock_summary_btc_locked, mock_profiles)
final = optimized_execution_geometry_module(clean)
render_gatekeeper_board(final)

print("\n=== TEST 2: HOÀN TOÀN KHÔNG CÓ LỆNH (EMPTY QUEUE) ===")
render_gatekeeper_board([])
