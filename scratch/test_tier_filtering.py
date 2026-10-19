import sys
import os

sys.path.insert(0, r"c:\DData\Source\wwwScr\BinaC4")
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from execution.order_gatekeeper import (
    optimized_execution_geometry_module,
    render_gatekeeper_board
)

test_orders = [
    {
        "symbol": "COIN_F/USDT",
        "order_type": "SPOT_OCO",
        "entry": 1.0,
        "raw_sl": 0.98,
        "raw_tp": 1.05,
        "uy_tin_score": 15.0,
        "tier_label": "Tier F",
        "engine": "DC2",
        "money_flow": 0.8
    },
    {
        "symbol": "COIN_C_NORMAL/USDT",
        "order_type": "SPOT_OCO",
        "entry": 2.0,
        "raw_sl": 1.95,
        "raw_tp": 2.10,
        "uy_tin_score": 32.0,
        "tier_label": "Tier C",
        "engine": "DC2",
        "money_flow": 1.1
    },
    {
        "symbol": "COIN_C_SURGE/USDT",
        "order_type": "SPOT_OCO",
        "entry": 0.50,
        "raw_sl": 0.48,
        "raw_tp": 0.55,
        "uy_tin_score": 35.0,
        "tier_label": "Tier C",
        "engine": "DC3",
        "money_flow": 4.5
    },
    {
        "symbol": "COIN_B/USDT",
        "order_type": "SPOT_OCO",
        "entry": 10.0,
        "raw_sl": 9.8,
        "raw_tp": 10.5,
        "uy_tin_score": 52.0,
        "tier_label": "Tier B",
        "engine": "DC2",
        "money_flow": 1.5
    },
    {
        "symbol": "COIN_A/USDT",
        "order_type": "SPOT_OCO",
        "entry": 50.0,
        "raw_sl": 49.0,
        "raw_tp": 53.0,
        "uy_tin_score": 78.0,
        "tier_label": "Tier A",
        "engine": "DC1",
        "money_flow": 2.0
    }
]

final = optimized_execution_geometry_module(test_orders)

symbols = [o["symbol"] for o in final]
print(f"Danh sách mã được duyệt vào Gatekeeper ({len(symbols)} mã): {symbols}")

assert "COIN_F/USDT" not in symbols, "LỖI: COIN_F không được xuất hiện trong hàng đợi!"
assert "COIN_C_NORMAL/USDT" not in symbols, "LỖI: COIN_C_NORMAL không được xuất hiện trong hàng đợi!"
assert "COIN_C_SURGE/USDT" in symbols, "LỖI: COIN_C_SURGE (Bạo phát 4.5x) phải được cứu qua cửa hậu!"
assert "COIN_B/USDT" in symbols, "COIN_B phải có trong hàng đợi!"
assert "COIN_A/USDT" in symbols, "COIN_A phải có trong hàng đợi!"

# Kiểm tra volume cấp phát
c_surge = next(o for o in final if o["symbol"] == "COIN_C_SURGE/USDT")
assert c_surge["status"] == "MANUAL_APPROVAL"
assert c_surge["volume_pct"] == 0.2, f"Expected 0.2 for Tier C surge, got {c_surge['volume_pct']}"

b_order = next(o for o in final if o["symbol"] == "COIN_B/USDT")
assert b_order["status"] == "MANUAL_APPROVAL"
assert b_order["volume_pct"] == 0.3, f"Expected 0.3 for Tier B, got {b_order['volume_pct']}"

a_order = next(o for o in final if o["symbol"] == "COIN_A/USDT")
assert a_order["status"] == "AUTO_EXECUTE"
assert a_order["volume_pct"] == 1.0, f"Expected 1.0 for Tier A, got {a_order['volume_pct']}"

print("\n--- RENDER BẢNG DUYỆT LỆNH ---")
render_gatekeeper_board(final)
print("✅ TẤT CẢ TEST LỌC TIER C & F ĐÃ PASS 100%!")
