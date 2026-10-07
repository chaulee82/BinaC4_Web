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


def run(name, df, expect_tier, expect_score=None):
    ind = compute_3d_indicators(df)
    r = validate_strict_3d_wave(df, ind)
    good = r["tier"] == expect_tier and (expect_score is None or r["score"] == expect_score)
    print(f"[{'OK ' if good else 'FAIL'}] {name:<32} {r['tier']:<6} {r['score']:>3}đ valid={r['is_valid']} | {r['tag']}")
    return good


results = []

# 1. Sóng thật: cột cờ nổ Vol → 3 nến đi ngang, Vol teo dần → 100đ
c, v = base_uptrend()
c += [2.30, 2.28, 2.29, 2.31]
v += [400.0, 180.0, 140.0, 110.0]
results.append(run("Sóng thật (tích lũy)", make_df(c, v), "PASS", 100))

# 2. Sóng ảo: sau cột cờ có nến đỏ Vol to (phân phối) → PHỦ QUYẾT, khóa trần 74đ
c, v = base_uptrend()
c += [2.30, 2.20, 2.25, 2.27]
v += [400.0, 380.0, 150.0, 120.0]
results.append(run("Sóng ảo (nến đỏ Vol lớn)", make_df(c, v), "WATCH", 74))

# 3. Vol chưa cạn: trượt nhẹ Tầng 3 → vẫn cấp phép (3/4 tầng + điểm an ủi)
c, v = base_uptrend()
c += [2.30, 2.31, 2.32, 2.33]
v += [400.0, 200.0, 180.0, 350.0]
results.append(run("Vol chưa cạn", make_df(c, v), "PASS", 81))

# 4. Không có cột cờ (Vol phẳng) → 75đ đúng ngưỡng
c, v = base_uptrend()
c += [2.02, 2.04, 2.06, 2.08]
v += [100.0, 100.0, 100.0, 100.0]
results.append(run("Không có cột cờ", make_df(c, v), "PASS", 75))

# 5. Downtrend (ST đỏ, MA dốc xuống, dưới BOLL MB) → loại hẳn
c = list(np.linspace(2.0, 1.0, 96)) + [0.98, 0.97, 0.96, 0.95]
v = [100.0] * 96 + [400.0, 150.0, 120.0, 100.0]
results.append(run("Downtrend", make_df(c, v), "REJECT"))

# 6. Lùi sâu > 50% thân cờ (không phân phối) → vẫn cấp phép
c, v = base_uptrend()
c += [2.60, 2.30, 2.25, 2.20]
v += [400.0, 150.0, 120.0, 100.0]
results.append(run("Lùi sâu > 50% thân cờ", make_df(c, v), "PASS", 81))

print(f"\n{sum(results)}/{len(results)} test đạt")

# ── API Universal Gatekeeper ────────────────────────────────────────────────
from core.macro_levels import (strict_3d_gate, get_strict_3d_rejections, reset_strict_3d_rejections,
                               validate_strict_3d_wave as V)
c, v = base_uptrend(); c += [2.30, 2.20, 2.25, 2.27]; v += [400.0, 380.0, 150.0, 120.0]
df_bad = make_df(c, v)
bad = V(df_bad, compute_3d_indicators(df_bad))
api_ok = (bad["is_valid"] is False and bad["tier"] == "WATCH" and bad["veto"] is True
          and bad["failed_layers"] == [3] and bad["score"] == 74
          and bad["reasons"][0].startswith("Vi phạm Tầng 3 - Nến xả"))
print(f"[{'OK ' if api_ok else 'FAIL'}] API dict: is_valid/tier/veto/score/failed_layers → {bad['score']}đ {bad['reasons']}")
results.append(api_ok)

# Tag ngắn trên dòng lưới
from core.macro_levels import strict_3d_short_tag
tag_ok = strict_3d_short_tag(bad) == "🟡 [3D STRICT ⚠️] 74đ (✗T3)"
print(f"[{'OK ' if tag_ok else 'FAIL'}] Short tag → {strict_3d_short_tag(bad)}")
results.append(tag_ok)

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
