"""
Kiểm thử Module 2: DC3 Fast Trigger V2.0 & Anti-FOMO RSI Climax
"""
import sys
import os
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategies.momentum_breakout import dc3_breakout_trigger

def test_dc3_fast_trigger():
    # Case 1: Giá chưa chạm nắp hộp
    ok, reason = dc3_breakout_trigger(price_live=99.0, box_top=100.0, current_vol=400, ma20_vol=100, taker_buy_ratio=0.6, rsi_15m=60)
    print("Case 1:", ok, reason)
    assert not ok and "chưa chạm nắp" in reason

    # Case 2: Chạm nắp hộp nhưng Volume yếu (< 3.0x)
    ok, reason = dc3_breakout_trigger(price_live=100.5, box_top=100.0, current_vol=200, ma20_vol=100, taker_buy_ratio=0.6, rsi_15m=60)
    print("Case 2:", ok, reason)
    assert not ok and "Volume yếu" in reason

    # Case 3: Chạm nắp hộp, Volume nổ 3.5x nhưng Taker buy yếu (< 55%)
    ok, reason = dc3_breakout_trigger(price_live=100.5, box_top=100.0, current_vol=350, ma20_vol=100, taker_buy_ratio=0.45, rsi_15m=60)
    print("Case 3:", ok, reason)
    assert not ok and "Volume yếu" in reason # hoặc taker buy không đủ

    # Case 4: Đủ Vol 3.5x, Taker 60% nhưng RSI 15M = 88 (> 85: RSI Climax - Anti-FOMO)
    ok, reason = dc3_breakout_trigger(price_live=100.5, box_top=100.0, current_vol=350, ma20_vol=100, taker_buy_ratio=0.60, rsi_15m=88)
    print("Case 4:", ok, reason)
    assert not ok and "RSI Climax" in reason

    # Case 5: Đủ mọi điều kiện -> BÓP CÒ NGAY!
    ok, reason = dc3_breakout_trigger(price_live=100.5, box_top=100.0, current_vol=350, ma20_vol=100, taker_buy_ratio=0.60, rsi_15m=72)
    print("Case 5:", ok, reason)
    assert ok and "BÓP CÒ NGAY" in reason

    print("✅ TẤT CẢ 5 CASES CỦA DC3 FAST TRIGGER ĐẠT CHUẨN 100%!")

if __name__ == "__main__":
    test_dc3_fast_trigger()
