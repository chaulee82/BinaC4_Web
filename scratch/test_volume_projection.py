import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import time
import pandas as pd
from core.volume_projection import project_volume
from strategies.momentum_breakout import MomentumBreakout

H = 3600 * 1000
# ── project_volume: chốt chặn theo khung ──
cases = [
    ("4h", 0.5 * H, 1.0, False, "4H mới 30' (12.5% < 20%) → giữ Vol thực"),
    ("4h", 1.0 * H, 4.0, True,  "4H chạy 1h (25%) → ×4.0"),
    ("4h", 3.0 * H, 4 / 3, True, "4H chạy 3h (75%) → ×1.33"),
    ("1d", 1.0 * H, 1.0, False, "1D chạy 1h (4.2% < 10%) → giữ"),
    ("1d", 6.0 * H, 4.0, True,  "1D chạy 6h (25%) → ×4.0"),
    ("1h", 0.1 * H, 1.0, False, "1H → KHÔNG nội suy (khung nhỏ)"),
    ("15m", 60_000, 1.0, False, "15M → KHÔNG nội suy (khung nhỏ)"),
]
for tf, elapsed, exp_mult, exp_app, desc in cases:
    p = project_volume(100.0, 0, tf, now_ms=int(elapsed))
    print(f"  {desc:<42} mult={p['mult']:.2f} applied={p['applied']}")
    assert abs(p["mult"] - exp_mult) < 1e-6 and p["applied"] == exp_app

# ── DC3 Gate 2: nến 4H mới chạy 1h, Vol = 1.0x MA20 → dự phóng 4.0x → Bạo Phát 25đ ──
now = int(time.time() * 1000)
ts = [now - (21 - i) * 4 * H for i in range(21)] + [now - 1 * H]
df = pd.DataFrame({"timestamp": ts, "volume": [100.0] * 21 + [100.0]})
r4 = MomentumBreakout.evaluate_volume(None, df, "4h")
r1 = MomentumBreakout.evaluate_volume(None, df, "1h")
r0 = MomentumBreakout.evaluate_volume(None, df)          # gọi kiểu cũ (không truyền timeframe)
print("  DC3 4h:", r4["score"], r4["status"])
print("  DC3 1h:", r1["score"], r1["status"])
print("  DC3 legacy:", r0["score"], r0["status"])
assert r4["score"] == 25 and "*" in r4["status"]
assert r1["score"] == 0 and "*" not in r1["status"]
assert r0["score"] == 0

# ── DC3 Gate 1: Breakout chỉ công nhận trên nến 4H ĐÃ ĐÓNG ──
def pa_df(closed, running):
    """101 nến nền (đỉnh 10.0) + nến đã đóng + nến đang chạy (mở 1h trước)."""
    n = 101
    rows = [{"timestamp": now - (n + 1 - i) * 4 * H, "open": 9.0, "high": 10.0, "low": 8.5, "close": 9.2} for i in range(n)]
    rows.append({"timestamp": now - 5 * H, **closed})
    rows.append({"timestamp": now - 1 * H, **running})
    return pd.DataFrame(rows)
flat = {"open": 9.5, "high": 9.6, "low": 9.4, "close": 9.5}
pa_cases = [
    ({"open": 9.5, "high": 10.6, "low": 9.4, "close": 10.5}, flat,                                         30, "Nến ĐÃ ĐÓNG vượt cản sạch"),
    (flat, {"open": 9.5, "high": 10.6, "low": 9.4, "close": 10.5},                                         15, "Chỉ nến ĐANG CHẠY vượt cản"),
    ({"open": 9.5, "high": 10.8, "low": 9.4, "close": 9.8}, {"open": 9.8, "high": 9.85, "low": 9.7, "close": 9.75}, 0, "Nến đã đóng chọc râu rút chân"),
    (flat, {"open": 9.5, "high": 9.95, "low": 9.4, "close": 9.93},                                          15, "Tiệm cận trong 1%"),
]
for closed, running, exp, desc in pa_cases:
    r = MomentumBreakout.evaluate_price_action(None, pa_df(closed, running), "4h")
    print(f"  Gate1 {desc:<32} → {r['score']:>2}đ | {r['status']}")
    assert r["score"] == exp
print("PROJECTION OK")
