"""Test Hard Filter "Siêu sóng 3D" (Động cơ 5) với dữ liệu 3D tổng hợp."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np
import pandas as pd
from core.macro_levels import compute_3d_indicators, validate_strict_3d_wave


def make_df(closes, vols, opens=None):
    closes = np.asarray(closes, dtype=float)
    if opens is None:
        opens = np.r_[closes[0], closes[:-1]]
    opens = np.asarray(opens, dtype=float)
    high = np.maximum(opens, closes) * 1.01
    low = np.minimum(opens, closes) * 0.99
    return pd.DataFrame({"open": opens, "high": high, "low": low, "close": closes, "volume": vols})


def base_uptrend(n=100):
    closes = list(np.linspace(1.0, 2.0, n - 4))
    vols = [100.0] * (n - 4)
    return closes, vols


def run(name, df, expect):
    ind = compute_3d_indicators(df)
    r = validate_strict_3d_wave(df, ind)
    ok = "OK " if r["passed"] == expect else "FAIL"
    print(f"[{ok}] {name:<32} passed={r['passed']} | {r['tag']}")
    return r["passed"] == expect


results = []

# 1. Sóng thật: cột cờ nổ Vol → 3 nến đi ngang, Vol teo dần
c, v = base_uptrend()
c += [2.30, 2.28, 2.29, 2.31]
v += [400.0, 180.0, 140.0, 110.0]
results.append(run("Sóng thật (tích lũy)", make_df(c, v), True))

# 2. Sóng ảo: sau cột cờ có nến đỏ Vol to (phân phối)
c, v = base_uptrend()
c += [2.30, 2.20, 2.25, 2.27]
v += [400.0, 380.0, 150.0, 120.0]
results.append(run("Sóng ảo (nến đỏ Vol lớn)", make_df(c, v), False))

# 3. Vol chưa cạn: nến hiện tại vẫn ≥ 80% cột cờ
c, v = base_uptrend()
c += [2.30, 2.31, 2.32, 2.33]
v += [400.0, 200.0, 180.0, 350.0]
results.append(run("Vol chưa cạn", make_df(c, v), False))

# 4. Không có cột cờ (Vol phẳng)
c, v = base_uptrend()
c += [2.02, 2.04, 2.06, 2.08]
v += [100.0, 100.0, 100.0, 100.0]
results.append(run("Không có cột cờ", make_df(c, v), False))

# 5. Downtrend (ST đỏ, MA dốc xuống, dưới BOLL MB)
c = list(np.linspace(2.0, 1.0, 96)) + [0.98, 0.97, 0.96, 0.95]
v = [100.0] * 96 + [400.0, 150.0, 120.0, 100.0]
results.append(run("Downtrend", make_df(c, v), False))

# 6. Lùi sâu > 50% thân cờ
c, v = base_uptrend()
c += [2.60, 2.30, 2.25, 2.20]
v += [400.0, 150.0, 120.0, 100.0]
results.append(run("Lùi sâu > 50% thân cờ", make_df(c, v), False))

print(f"\n{sum(results)}/{len(results)} test đạt")

# ── API Universal Gatekeeper ────────────────────────────────────────────────
from core.macro_levels import (strict_3d_gate, get_strict_3d_rejections, reset_strict_3d_rejections,
                               validate_strict_3d_wave as V)
c, v = base_uptrend(); c += [2.60, 2.30, 2.25, 2.20]; v += [400.0, 150.0, 120.0, 100.0]
df_bad = make_df(c, v)
bad = V(df_bad, compute_3d_indicators(df_bad))
api_ok = (bad["is_valid"] is False and bad["failed_layers"] == [3] and bad["score"] == 75
          and bad["reasons"][0].startswith("Vi phạm Tầng 3 - Lùi quá 50% thân cờ"))
print(f"[{'OK ' if api_ok else 'FAIL'}] API dict: is_valid/score/failed_layers/reasons → {bad['score']}đ {bad['reasons']}")
results.append(api_ok)

# Đầu vào d3 dict (hồ sơ 3D) + sổ ghi
reset_strict_3d_rejections()
g = strict_3d_gate("FAKE/USDT", "DC4", "GRID TP2", d3={"symbol": "FAKEUSDT", "strict_3d": bad})
g2 = strict_3d_gate("FAKEUSDT", "DC1", "DARVAS", d3={"symbol": "FAKEUSDT", "strict_3d": bad})
rej = get_strict_3d_rejections()
reg_ok = (not g["is_valid"]) and "FAKE" in rej and rej["FAKE"]["engines"] == {"DC1", "DC4"}
print(f"[{'OK ' if reg_ok else 'FAIL'}] Sổ ghi mã bị loại (gộp nhiều Động cơ) → {rej}")
results.append(reg_ok)

# Mục 4 Bảng Tổng Kết
from views.summary_board import generate_summary_board
generate_summary_board([], "", {}, None, [], strict_3d_rejections=rej)

print(f"\nTỔNG: {sum(results)}/{len(results)} test đạt")
sys.exit(0 if all(results) else 1)
