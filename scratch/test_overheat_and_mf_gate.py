"""Test: (1) Chốt Nổ quá nóng 15M — Bảng 3B, (2) Nhãn quyền bóp cò ở mục 🔥 TOP 3 BẠO PHÁT (Summary Board)."""
import io
import os
import sys
from contextlib import redirect_stdout

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

from strategies.super_trend_3d import (evaluate_micro_trigger_15m, M15_OVERHEAT, M15_TRIGGER,
                                       M15_DUMP, M15_NEUTRAL)
from views.summary_board import (SignalRecord, generate_summary_board, mf_gate_label,
                                 STATUS_APPROVED, STATUS_WAIT, STATUS_REJECTED)

M15 = 15 * 60 * 1000
NOW = 1_800_000_000_000 // M15 * M15 + 5 * 60 * 1000      # 5 phút sau khi nến đang chạy mở


def make_15m(last_closed=(100, 100.5, 1000), running=(100.5, 100.6), base_vol=1000):
    """21 nến nền (vol base) + 1 nến đã đóng (o, c, v) + 1 nến đang chạy (o, c)."""
    rows, t0 = [], NOW - 5 * 60 * 1000 - 22 * M15
    for i in range(21):
        rows.append({"open_time": t0 + i * M15, "open": 100, "close": 100, "volume": base_vol,
                     "close_time": t0 + (i + 1) * M15 - 1})
    o, c, v = last_closed
    t = t0 + 21 * M15
    rows.append({"open_time": t, "open": o, "close": c, "volume": v, "close_time": t + M15 - 1})
    ro, rc = running
    t += M15
    rows.append({"open_time": t, "open": ro, "close": rc, "volume": 50, "close_time": t + M15 - 1})
    return pd.DataFrame(rows)


fails = 0


def check(name, cond, info=""):
    global fails
    fails += 0 if cond else 1
    print(f"{'✅' if cond else '❌'} {name} {info}")


# ── 1. 15M Overheat ──────────────────────────────────────────────────────────
r = evaluate_micro_trigger_15m(make_15m((100, 103.5, 5000)), NOW)          # đóng +3.5%, vol 5x
check("Nến đóng +3.5% vol 5x → OVERHEAT (đè TRIGGER)", r["state"] == M15_OVERHEAT, r["label"])
r = evaluate_micro_trigger_15m(make_15m((100, 102, 3000), (102, 105.2)), NOW)  # đóng +2% vol 3x, đang chạy +3.1%
check("Nến đang chạy +3.1% (live) → OVERHEAT", r["state"] == M15_OVERHEAT,
      f"live={r['live_change_pct']:.2f}%")
r = evaluate_micro_trigger_15m(make_15m((100, 102.9, 3000)), NOW)          # đóng +2.9% vol 3x
check("Nến đóng +2.9% vol 3x → TRIGGER", r["state"] == M15_TRIGGER, r["label"])
r = evaluate_micro_trigger_15m(make_15m((100, 96, 1500), (96, 99.2)), NOW)  # đóng đỏ, đang chạy +3.33%
check("Đóng đỏ xả nhưng đang chạy +3.3% → OVERHEAT (ưu tiên cao nhất)", r["state"] == M15_OVERHEAT)
r = evaluate_micro_trigger_15m(make_15m((100, 96, 1500)), NOW)
check("Đóng đỏ vol 1.5x → DUMP", r["state"] == M15_DUMP)
r = evaluate_micro_trigger_15m(make_15m(), NOW)
check("Nến bình thường → NEUTRAL", r["state"] == M15_NEUTRAL)

# ── 2. Summary Board — Top 3 Bạo phát ───────────────────────────────────────
GRID = "🎯 [GRID TP2 - {s}] 1 - 2 | 24L | Trig: 1.5 | SL: 0.9 | TP: 2 | 🟡 [3D STRICT ⚠️] 60đ (✗T2,T3)"
lumia = SignalRecord("LUMIA", "DC3", STATUS_APPROVED, "🟢 BREAKOUT HÀNG THẬT: Bắn lệnh Hybrid Executor", 110,
                     101.0, 2.4, -19.6, setup_line="⚙️ SETUP: In=0.1079 | SL=0.0867(-19.6%) | R/R=1:2.4",
                     grid_tp2_line=GRID.format(s="LUMIA"))
arpa = SignalRecord("ARPA", "DC3", STATUS_REJECTED, "🟡 THEO DÕI: Chờ đóng nến xác nhận Breakout", 90,
                    21.8, 2.2, -8.0, setup_line="⚙️ SETUP: In=0.01281 | SL=0.0118(-8.0%) | R/R=1:2.2")
ok = SignalRecord("OKC", "DC3", STATUS_APPROVED, "🟢 BREAKOUT HÀNG THẬT", 95, 9.0, 3.0, -8.0,
                  setup_line="⚙️ SETUP: In=1 | SL=0.92(-8.0%) | R/R=1:3.0")
fatal = SignalRecord("DEAD", "DC4", STATUS_WAIT, "⏳ CHỜ - GÃY MA7 3D", 99, 500.0, 2.0, -3.0,
                     setup_line="⚙️ SETUP: Trig=1 | SL=0.97(-3.0%) | R/R=1:2.0")

check("LUMIA 🟢 nhưng SL −19.6% → 🔴 TỪ CHỐI — SL RỘNG", mf_gate_label(lumia) == ("🔴 TỪ CHỐI — SL RỘNG (≥ 15%)", False))
check("ARPA VETO → 🟡 CHỜ ĐÓNG NẾN", mf_gate_label(arpa)[0].startswith("🟡 CHỜ ĐÓNG NẾN") and not mf_gate_label(arpa)[1])
check("OKC 🟢 SL −8% → ĐƯỢC PHÉP", mf_gate_label(ok) == ("🟢 ĐƯỢC PHÉP BÓP CÒ", True))


def _with_sl(sl, status=STATUS_APPROVED):
    return SignalRecord("X", "DC3", status, "🟢 BREAKOUT", 90, 5.0, 2.0, sl, setup_line=f"⚙️ SETUP: In=1 | SL=0.9({sl}%)")


check("SL −15.0% (biên) → TỪ CHỐI", mf_gate_label(_with_sl(-15.0))[1] is False)
check("SL −14.9% đã duyệt → được phép, giảm vốn", mf_gate_label(_with_sl(-14.9)) == ("⚠️ SL -14.9% — được phép, giảm vốn", True))
check("SL −19% dù bị VETO Gate 1 → ưu tiên nhãn SL RỘNG",
      mf_gate_label(_with_sl(-19.0, STATUS_REJECTED))[0].startswith("🔴 TỪ CHỐI — SL RỘNG"))

# Nhãn DC3 thực tế sau Execution Gatekeeper
lumia_real = SignalRecord("LUMIA", "DC3", STATUS_REJECTED,
                          "🔴 TỪ CHỐI — SL RỘNG 19.6% (≥ 15%) | 💥 TIỀN BẠO PHÁT (101.0x) → Trig đón nhúng", 110,
                          101.0, 2.4, -19.6, setup_line="⚙️ SETUP: In=0.1079 | SL=0.0867(-19.6%) | R/R=1:2.4")
wide_ok = SignalRecord("WIDEOK", "DC3", STATUS_APPROVED, "🟢 BREAKOUT HÀNG THẬT", 92, 7.0, 2.5, -12.0,
                       setup_line="⚙️ SETUP: In=1 | SL=0.88(-12.0%) | R/R=1:2.5")
waves = [{"symbol": "MET", "score": 95, "mode": "RIDE_WAVE", "dist_ma7_pct": 21.9},
         {"symbol": "MAGIC", "score": 80, "mode": "OVEREXTENDED", "dist_ma7_pct": 75.5},
         {"symbol": "STRK", "score": 80, "mode": "OVEREXTENDED", "dist_ma7_pct": 44.3}]
lpt = SignalRecord("LPT", "DC1", STATUS_WAIT, "⏳ CHỜ", 85, 13.2)                       # → dòng 4
c98 = SignalRecord("C98", "DC4", STATUS_WAIT, "⏳ CHỜ XÁC NHẬN", 110, 5.6)              # → dòng 4
noflow = SignalRecord("NOFLOW", "DC1", STATUS_WAIT, "⏳ CHỜ", 99, 0.0)                  # không dòng tiền → không
dupe = SignalRecord("WIDEOK", "DC4", STATUS_WAIT, "⏳ CHỜ XÁC NHẬN", 90, 50.0)          # đã ở Top 3 → không lặp

buf = io.StringIO()
with redirect_stdout(buf):
    res = generate_summary_board([lumia_real, arpa, ok, wide_ok, fatal, lpt, c98, noflow, dupe],
                                 super_wave_results=waves)
out = buf.getvalue()
print(out[out.find("🔥"):out.find("### 🥅")])
print(out[out.find("👀"):])
mf_syms = [s.symbol for s in res.get("top_money_flow", [])]
check("Top 3 Bạo phát chỉ còn mã được phép bóp cò", mf_syms == ["WIDEOK"], str(mf_syms))
check("DEAD (GÃY MA7 3D) không ở đâu cả", "DEAD" not in out)
wl = res.get("watchlist", {})
check("Watchlist XA RAY = MAGIC, STRK", [x["symbol"] for x in wl.get("overextended", [])] == ["MAGIC", "STRK"])
check("Watchlist BREAKOUT = ARPA", [x["symbol"] for x in wl.get("breakout_pending", [])] == ["ARPA"])
check("Watchlist SL RỘNG = LUMIA -19.6%", wl.get("sl_wide") == [{"symbol": "LUMIA", "score": 110, "sl_pct": -19.6}])
check("Định dạng 3 dòng Watchlist",
      "• XA RAY (Chờ pullback về MA7): MAGIC (+76%), STRK (+44%)" in out
      and "• BREAKOUT (Chờ đóng nến 4H): ARPA (90đ)" in out
      and "• SL RỘNG (Chờ siết nền): LUMIA (SL -19.6%)" in out)
check("Không còn dòng 🚫 Ẩn mã FOMO", "🚫 Ẩn mã" not in out)
check("Dòng 4 = LPT, C98 (xếp theo 💥)", [x["symbol"] for x in wl.get("flow_pending", [])] == ["LPT", "C98"],
      str(wl.get("flow_pending")))
check("Định dạng dòng 4", "• DÒNG TIỀN CHỜ DUYỆT (DC1/DC4 ⏳ CHỜ): LPT (DC1 💥13.2x), C98 (DC4 💥5.6x)" in out)
check("Watchlist nằm cuối Bảng Tổng Kết", out.find("👀") > out.find("### 🦅"))
check("Không có Render Error", "Render Error" not in out)

print(f"\n{'🎉 ALL PASS' if not fails else f'❌ {fails} FAIL'}")
sys.exit(1 if fails else 0)
