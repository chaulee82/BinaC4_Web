"""
Kiểm tra sửa lỗi UnboundLocalError: dec trong _print_order_geometry_details.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from execution.order_gatekeeper import _print_order_geometry_details, render_gatekeeper_board

def test_dec_fix():
    print("Test 1: PEPE với giá nhỏ và có grid_tp2")
    pepe_order = {
        'symbol': 'PEPE/USDT',
        'order_type': 'SPOT_OCO',
        'entry': 0.0000100,
        'sl': 0.0000095,
        'tp': 0.0000120,
        'rr_ratio': 4.0,
        'auto_breakeven': True,
        'grid_tp2': {
            'lower': 0.0000088,
            'upper': 0.0000120,
            'grids': 24
        }
    }
    _print_order_geometry_details(pepe_order)

    print("\nTest 2: Lệnh có entry = 0 hoặc None nhưng có grid_tp2")
    edge_order = {
        'symbol': 'CORRUPT/USDT',
        'order_type': 'SPOT_OCO',
        'entry': 0.0,
        'grid_tp2': {
            'lower': 1.25,
            'upper': 2.50,
            'grids': 24
        }
    }
    _print_order_geometry_details(edge_order)

    print("\nTest 3: Gọi render_gatekeeper_board với các case biên")
    render_gatekeeper_board([
        {**pepe_order, 'status': 'AUTO_EXECUTE', 'tier_label': 'Tier A (Xu Hướng)', 'uy_tin_score': 61.7, 'engine_label': 'DC2 (Bắt Đáy)'},
        {**edge_order, 'status': 'VETO_REJECT', 'veto_reason': 'Dữ liệu thiếu entry'}
    ])

    print("\n✅ TẤT CẢ TEST ĐÃ PASS HOÀN TOÀN MÀ KHÔNG GẶP LỖI UNBOUNDLOCALERROR!")

if __name__ == "__main__":
    test_dec_fix()
