"""Test 🌊 [SÓNG 3D - XXX] setup (Bảng 3B) + mục 🌊 4 trong Bảng Tổng Kết. --live: quét thật."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

from strategies.super_trend_3d import build_wave_setup

# 1. Chuẩn: MA7=1.00, SL Ray=0.96 → R=0.04 → 3R TP=1.12; Đỉnh 3D 1.30 > 1.12 → TP=1.30
s = build_wave_setup("METUSDT", 1.00, 0.96, 1.30)
print(s["display_line"])
assert s["trigger"] == 1.00 and s["stop_loss"] == 0.96 and abs(s["take_profit"] - 1.30) < 1e-9
assert abs(s["lower"] - 0.96 * 1.005) < 1e-9 and 10 <= s["grids"] <= 24 and s["display_line"].startswith("🌊 [SÓNG 3D - MET]")
# 2. Đỉnh 3D thấp hơn 3R → TP = Trig + 3R
s = build_wave_setup("X", 1.00, 0.96, 1.05)
assert abs(s["take_profit"] - 1.12) < 1e-9 and abs(s["rr"] - 3.0) < 1e-9
# 3. ST 3D nằm trên MA7 (SL 0.99 ≥ MA7×0.99) → Trig nâng ≥ SL/0.97
s = build_wave_setup("X", 1.00, 0.995, 1.2)
assert abs(s["trigger"] - 0.995 / 0.97) < 1e-9 and s["stop_loss"] < s["lower"] < s["trigger"] < s["take_profit"]
# 4. Tick size làm tròn + dữ liệu hỏng → None
s = build_wave_setup("X", 0.0123456, 0.0118, 0.015, tick_size=0.00001)
assert "Trig: 0.01235" in s["display_line"], s["display_line"]
assert build_wave_setup("X", 0, 0.9, 1) is None and build_wave_setup("X", 1, None, 1) is None
print("UNIT OK")

# 5. Bảng Tổng Kết 🌊 4: ẩn mã XA RAY, lấp slot bằng mã an toàn kế tiếp
import io
from contextlib import redirect_stdout
from views.summary_board import print_super_wave_summary
_ws = build_wave_setup("X", 1.0, 0.96, 1.3)
_rows = [{"symbol": "MAGIC", "score": 95, "mode": "OVEREXTENDED", "dist_ma7_pct": 75.5, "wave_setup": _ws},
         {"symbol": "MET", "score": 90, "mode": "RIDE_WAVE", "dist_ma7_pct": 21.9, "wave_setup": _ws},
         {"symbol": "STRK", "score": 85, "mode": "OVEREXTENDED", "dist_ma7_pct": 44.3, "wave_setup": _ws},
         {"symbol": "ARPA", "score": 85, "mode": "RAIL_SURF", "dist_ma7_pct": 8.9, "wave_setup": _ws}]
_buf = io.StringIO()
with redirect_stdout(_buf):
    _picked = print_super_wave_summary(_rows, 3)
print(_buf.getvalue())
assert [r["symbol"] for r in _picked] == ["MET", "ARPA"]
assert "🚫 Ẩn mã" not in _buf.getvalue() and "MAGIC" not in _buf.getvalue()   # đã chuyển xuống 👀 WATCHLIST
print("FILTER OK")

if "--live" in sys.argv:
    from core.cache_service import CacheService
    from core.market_data_repo import MarketDataRepository
    from core.exchange_info_cache import ExchangeInfoCache
    from strategies.super_trend_3d import scan_super_wave_3d, print_super_wave_table, get_last_super_wave_results
    from views.summary_board import print_super_wave_summary
    ExchangeInfoCache().load()
    ldm = CacheService(MarketDataRepository()).get_live_data_map()
    print_super_wave_table(scan_super_wave_3d(ldm))
    print_super_wave_summary(get_last_super_wave_results(), 3)
