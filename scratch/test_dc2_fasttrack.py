"""
Kiểm tra Module 3: DC2 Fast-track Bắt đáy (Wash-out)
Mã có nhãn 'Miễn Cấp 3 - Uptrend 3D' sẽ:
1. Được cộng +20đ Vĩ mô
2. Kích hoạt cờ skip_rsi_check=True
3. Action đổi thành '🛡️ ƯU TIÊN BẮT ĐÁY (Siêu Pullback - Đã rũ bỏ 3D)'
"""
import sys
import os
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import core.macro_levels
core.macro_levels.get_3d_profile = lambda sym: {"is_3d_broken_ma7": False}

from strategies.macro_pullback.pullback_sniper import PullbackSniper

class MockExchange:
    def fetch_ohlcv(self, symbol, timeframe, limit=120):
        # Tạo 120 nến giả lập
        data = []
        base = 100.0
        for i in range(limit):
            data.append([
                1600000000000 + i * 3600000,
                base, base + 2, base - 2, base + 1, 1000
            ])
        return data

    def fetch_order_book(self, symbol, limit=50):
        return {
            'bids': [[99.0, 500], [98.0, 1000]],
            'asks': [[101.0, 500], [102.0, 1000]]
        }

def test_dc2_fasttrack():
    mock_ex = MockExchange()
    sniper = PullbackSniper(exchange=mock_ex)
    
    # 1. Trường hợp bình thường (không có nhãn Miễn Cấp 3)
    macro_gate = {"ok": True, "trend_pct": 5.0}
    res_normal = sniper.evaluate_candidate("TEST/USDT", timeframe='4h', macro_gate=macro_gate, early_warning_status="✅ AN TOÀN")
    
    # 2. Trường hợp có nhãn Miễn Cấp 3
    res_washout = sniper.evaluate_candidate("TEST/USDT", timeframe='4h', macro_gate=macro_gate, early_warning_status="⚠️ CẤP 1: THEO DÕI (Miễn Cấp 3 - Uptrend 3D)")
    
    print(f"Normal score: {res_normal['total_score']} | Action: {res_normal['action']} | Skip RSI: {res_normal.get('skip_rsi_check')}")
    print(f"Washout score: {res_washout['total_score']} | Action: {res_washout['action']} | Skip RSI: {res_washout.get('skip_rsi_check')}")
    
    assert res_washout['total_score'] == min(100, res_normal['total_score'] + 20), "Score phải tăng 20đ"
    assert res_washout['skip_rsi_check'] is True, "skip_rsi_check phải là True"
    assert "ƯU TIÊN BẮT ĐÁY" in res_washout['action'], "Action phải chứa ƯU TIÊN BẮT ĐÁY"
    print("✅ TEST DC2 FAST-TRACK THÀNH CÔNG!")

if __name__ == "__main__":
    test_dc2_fasttrack()
