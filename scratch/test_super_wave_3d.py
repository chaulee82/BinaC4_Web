import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np, pandas as pd
from strategies.super_trend_3d import build_df_3d, score_super_wave_3d, scan_super_wave_3d, print_super_wave_table, analyze_super_wave

def make_1d(closes):
    closes = np.asarray(closes, float)
    o = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, closes) * 1.005, "Low": np.minimum(o, closes) * 0.995,
                         "Close": closes, "Volume": np.full(len(closes), 1000.0)})

# 1) Downtrend → bị Hard Gate loại
down = make_1d(np.linspace(10, 5, 330))
s, d = score_super_wave_3d(build_df_3d(down), 0.6)
print("Downtrend:", s, d.get("status")); assert s == 0 and d["status"] == "REJECTED_STRICT_GATE"

# 2) Đáy dài rồi bứt phá mạnh + nổ vol → điểm cao, có Golden Cross gần
base = list(np.linspace(10, 6, 300)) + list(np.linspace(6, 9.5, 30))
df1 = make_1d(base); df1.loc[df1.index[-3:], "Volume"] = 5000
df3 = build_df_3d(df1)
s, d = score_super_wave_3d(df3, 0.6)
print("Breakout:", s, d.get("signals"), d.get("golden"))
assert s >= 70

# 3) Dữ liệu thiếu
s, d = score_super_wave_3d(build_df_3d(make_1d(np.linspace(1, 2, 150))), 0.6)
print("Short:", s, d.get("status")); assert d["status"] == "INSUFFICIENT_DATA"

# 5) Nội suy Volume theo tỷ trọng thời gian + chốt chặn
from strategies.super_trend_3d import volume_time_weight
H = 3600 * 1000
for hrs, exp_mult, exp_app in [(3, 1.0, False),      # 4.2% < 10% → giữ Vol thực
                               (7.2, 5.0, True),     # 10% → 1/0.1=10 → trần 5.0
                               (24, 3.0, True),      # 33.3% → ×3.0 (ví dụ 10M → 30M)
                               (48, 1.5, True),      # 66.7% → ×1.5
                               (72, 1.0, False),     # đủ nến
                               (100, 1.0, False)]:   # quá hạn → kẹp 100%
    tw = volume_time_weight(0, hrs * H)
    print(f"  {hrs:>5}h → frac {tw['frac']:.3f} mult {tw['mult']:.2f} applied {tw['applied']}")
    assert abs(tw["mult"] - exp_mult) < 1e-6 and tw["applied"] == exp_app
assert volume_time_weight(None)["mult"] == 1.0

# 6) Micro-Trigger 15M — xét nến VỪA ĐÓNG, bỏ nến đang chạy
from strategies.super_trend_3d import evaluate_micro_trigger_15m
M = 15 * 60 * 1000
def m15_df(last_open, last_close, last_vol, running_vol=99999.0):
    n = 25  # 23 nến đã đóng + 1 nến xét + 1 nến đang chạy
    rows = [{"Open": 1.0, "Close": 1.0, "Volume": 100.0, "Close_Time": (i + 1) * M - 1} for i in range(n - 2)]
    rows.append({"Open": last_open, "Close": last_close, "Volume": last_vol, "Close_Time": (n - 1) * M - 1})
    rows.append({"Open": 1.0, "Close": 0.5, "Volume": running_vol, "Close_Time": n * M - 1})  # đang chạy → bỏ
    return pd.DataFrame(rows)
NOW = 24 * M + 5 * 60 * 1000  # nến cuối mới chạy 5 phút
for args, exp in [((1.0, 0.98, 150), "DUMP"),      # đỏ, x1.5 > MA20
                  ((1.0, 0.98, 90), "NEUTRAL"),    # đỏ, vol thấp
                  ((1.0, 1.03, 300), "TRIGGER"),   # xanh, x3.0 ≥ 2.5
                  ((1.0, 1.03, 200), "NEUTRAL")]:  # xanh, x2.0 < 2.5
    m = evaluate_micro_trigger_15m(m15_df(*args), NOW)
    print(f"  15M {args} → {m['state']} (x{m['vol_ratio']:.2f})"); assert m["state"] == exp
assert evaluate_micro_trigger_15m(None)["state"] == "NA"
print("UNIT OK\n")

# 4) Live smoke test
if "--live" in sys.argv:
    from core.cache_service import CacheService
    from core.market_data_repo import MarketDataRepository
    ldm = CacheService(MarketDataRepository()).get_live_data_map()
    print_super_wave_table(scan_super_wave_3d(ldm))
