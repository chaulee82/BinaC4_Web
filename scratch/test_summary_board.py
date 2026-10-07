import sys, os
sys.path.insert(0, r"c:\DData\Source\wwwScr\BinaC4")
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
from views.summary_board import SignalRecord, generate_summary_board, classify_action, extract_money_flow, extract_bounces

BREAK = "🚀 [3D: CHÂN SÓNG BỨT PHÁ - SIÊU SÓNG]"
FLAG = "🦅 [3D: CỘT CỜ CAO - WIDE GRID 28L]"
CAP = "⚠️ [3D: ĐỤNG CẢN VĨ MÔ - KHÓA TRẦN TP2]"

assert classify_action("DC3", "🟢 BREAKOUT HÀNG THẬT | 💥 TIỀN BẠO PHÁT (6.9x)") == "APPROVED 🟢"
assert classify_action("DC3", "🟢 BREAKOUT", ["🚫 HỦY SETUP (R/R 1.2"]) == "🔴 TỪ CHỐI"
assert classify_action("DC4", "🚀 VÀO LỆNH PULLBACK") == "🎯 🚀 VÀO LỆNH PULLBACK"
assert classify_action("DC4", "⏳ CHỜ XÁC NHẬN", ["C2: 🔴 Bơm xả — TỪ CHỐI"]) == "🔴 TỪ CHỐI"
assert extract_money_flow("x | 💥 TIỀN BẠO PHÁT (29.1x)", "💥💰x5.5") == 29.1
assert extract_bounces("🦅 CỘT CỜ 3D +15 | F:8.0 | B:2.0x15.16%") == (2.0, 15.16)

pool = [
    SignalRecord("CHIP", "DC4", "🎯 🚀 VÀO LỆNH PULLBACK", target_score=100, money_flow=3.4, rr_ratio=11.4, sl_percent=-4.0,
                 macro_3d_signal=BREAK, setup_line="⚙️ SETUP: Trig=0.05207 | SL=0.0499875(-4.0%) | TP=0.075823(+45.6%) | R/R=1:11.4",
                 grid_tp2_line="🎯 [GRID TP2 - CHIP] 0.044034 - 0.075823 | 24L | Trig: 0.052070 | SL: 0.038080 | TP: 0.075823"),
    SignalRecord("VTHO", "DC4", "🎯 🚀 VÀO LỆNH PULLBACK", target_score=95, money_flow=29.1, rr_ratio=12.7, sl_percent=-3.7,
                 macro_3d_signal=BREAK, setup_line="⚙️ SETUP: Trig=0.000727 | SL=0.00070029(-3.7%) | TP=0.00106628(+46.7%) | R/R=1:12.7",
                 grid_tp2_line="🎯 [GRID TP2 - VTHO] 0.000647 - 0.001066 | 24L | Trig: 0.000727 | SL: 0.000594 | TP: 0.001066"),
    SignalRecord("S", "DC4", "🎯 🚀 VÀO LỆNH PULLBACK", target_score=110, rr_ratio=4.6, sl_percent=-5.9, macro_3d_signal=BREAK,
                 setup_line="⚙️ SETUP: In=0.04384 | SL=0.0412574(-5.9%) | TP=0.0557418(+27.1%) | R/R=1:4.6",
                 grid_tp2_line="🎯 [GRID TP2 - S] 0.037859 - 0.055742 | 24L | Trig: 0.043840 | SL: 0.034460 | TP: 0.055742"),
    SignalRecord("RAD", "DC3", "APPROVED 🟢", target_score=95, money_flow=6.9, rr_ratio=2.6, sl_percent=-11.9, macro_3d_signal=BREAK,
                 setup_line="⚙️ SETUP: In=0.385 | SL=0.3391428571(-11.9%) | R/R=1:2.6"),
    SignalRecord("RAD", "DC4", "🎯 🚀 VÀO LỆNH PULLBACK", target_score=85, money_flow=6.9, rr_ratio=6.7, sl_percent=-3.7, macro_3d_signal=BREAK,
                 setup_line="⚙️ SETUP: Trig=0.389 | SL=0.37479(-3.7%) | TP=0.4836(+24.3%) | R/R=1:6.7",
                 wide_grid_line="🦅 [WIDE GRID 3D - RAD] 0.303 - 0.476 | 28L | Trig: 0.322 | SL: 0.262 | TP: 0.476 (Vốn 1000$ ≈ 35.71$/lưới)"),
    SignalRecord("ENA", "DC2", "APPROVED 🟢", target_score=85, rr_ratio=17.1, sl_percent=-0.5, macro_3d_signal=BREAK,
                 setup_line="⚙️ SETUP: In=0.24040496 | SL=0.2390985(-0.5%) | TP=0.2627(+9.3%) | R/R=1:17.1",
                 grid_tp2_line="🎯 [GRID TP2 - ENA] 0.2253 - 0.2811 | 24L | Trig: 0.2404 | SL: 0.2146 | TP: 0.2811"),
    SignalRecord("TON", "DC2", "APPROVED 🟢", target_score=90, rr_ratio=30, sl_percent=-1.0, macro_3d_signal=BREAK,
                 setup_line="⚙️ SETUP: In=1 | SL=0.99(-1.0%) | TP=1.3(+30%) | R/R=1:30.0"),   # CẤP 3 → loại
    SignalRecord("MUBARAK", "DC5", target_score=53.0, macro_3d_signal=FLAG, bounces=2.0, bounce_range=15.16,
                 wide_grid_line="🦅 [WIDE GRID 3D - MUBARAK] 0.05964 - 0.08582 | 28L | Trig: 0.06640 | SL: 0.05088 | TP: 0.08582 (Vốn 1000$ ≈ 35.71$/lưới)"),
    SignalRecord("QNT", "DC5", target_score=50.8, macro_3d_signal=FLAG, bounces=5.0, bounce_range=4.56,
                 wide_grid_line="🦅 [WIDE GRID 3D - QNT] 208.62 - 304.97 | 28L | Trig: 247.29 | SL: 178.97 | TP: 304.97 (Vốn 1000$ ≈ 35.71$/lưới)"),
    SignalRecord("XRP", "DC1", target_score=85, macro_3d_signal=BREAK,
                 wide_grid_line="🦅 [WIDE GRID 3D - XRP] 1.3673 - 1.9145 | 28L | Trig: 1.4546 | SL: 1.2990 | TP: 1.9145 (Vốn 1000$ ≈ 35.71$/lưới)"),
    SignalRecord("OP", "DC5", target_score=60, macro_3d_signal=CAP, bounces=9,
                 wide_grid_line="🦅 [WIDE GRID 3D - OP] 1 - 2 | 28L | Trig: 1.2 | SL: 0.9 | TP: 2"),  # Đụng cản → loại
]
live = {f"{s}USDT": {"daily_vola": v} for s, v in
        {"CHIP": 12, "VTHO": 20, "S": 8, "RAD": 30, "ENA": 9.3, "TON": 5, "MUBARAK": 12.1, "QNT": 6, "XRP": 2.6, "OP": 9}.items()}
df = pd.DataFrame([{"Symbol": "S", "Phân Loại Grid": "⛔ NÉ GRID (Bơm Xả Râu Dài)"}])
ew = [{"symbol": "TON/USDT", "level": 3, "label": "💀 CẤP 3: KHẨN CẤP (Gãy Trend 1D)"}]

LEGACY = {"top_spot": 2, "top_grid_tp2": 2, "top_wide_grid": 2, "fill_with_backup": False}
r = generate_summary_board(pool, "✅ BTC ổn định", live, df, ew, config=LEGACY)
assert [s.symbol for s in r["grid_tp2"]] == ["VTHO", "CHIP"], r["grid_tp2"]   # 29.1x > 3.4x (Money Flow trước Điểm)
assert [s.symbol for s in r["spot"]] == ["RAD", "ENA"], [s.symbol for s in r["spot"]]   # 💥 6.9x trước, rồi R:R
assert [s.symbol for s in r["wide_grid"]] == ["QNT", "MUBARAK"], [s.symbol for s in r["wide_grid"]]

r2 = generate_summary_board(pool, "⚠️ CẢNH BÁO: BTC gãy MA25 1H (RSI=41.1) — Fakeout Risk Cao", live, df, ew, config=LEGACY)
assert r2["spot"] == [] and len(r2["grid_tp2"]) == 2
assert r["spot_is_backup"] is False and r["grid_tp2_is_backup"] is False

# ───────── FALLBACK: thị trường yếu, không mã nào đạt chuẩn vàng ─────────
BROKEN = "⛔ [3D: GÃY ĐƯỜNG RAY MA7 3D - CẤM VÀO]"
NEUTRAL = "➖ [3D: TRUNG TÍNH]"
weak = [
    # Spot backup: DC4 chờ xác nhận, SL -8%
    SignalRecord("UMA", "DC4", "⏳ CHỜ", action_label="⏳ CHỜ XÁC NHẬN (Pullback nông)", target_score=95, rr_ratio=3.8,
                 sl_percent=-8.0, macro_3d_signal=NEUTRAL, setup_line="⚙️ SETUP: Trig=0.434 | SL=0.4(-8.0%) | TP=0.493(+13.6%) | R/R=1:3.8",
                 grid_tp2_line="🎯 [GRID TP2 - UMA] 0.39 - 0.47 | 23L | Trig: 0.434 | SL: 0.362 | TP: 0.47"),
    # Spot backup: DC2 EW CẤP 1 điểm cao
    SignalRecord("NEAR", "DC2", "⏳ CHỜ", action_label="🟡 LOẠI B", target_score=80, rr_ratio=3.0, sl_percent=-2.0,
                 macro_3d_signal=NEUTRAL, setup_line="⚙️ SETUP: In=5 | SL=4.9(-2.0%) | TP=5.5(+10%) | R/R=1:3.0"),
    # Spot: approved nhưng SL -12% → loại cả backup
    SignalRecord("DEEP", "DC3", "APPROVED 🟢", target_score=99, rr_ratio=5, sl_percent=-12.0, macro_3d_signal=NEUTRAL,
                 setup_line="⚙️ SETUP: In=1 | SL=0.88(-12.0%) | R/R=1:5.0"),
    # Spot: TỪ CHỐI → loại
    SignalRecord("REJ", "DC4", "🔴 TỪ CHỐI", action_label="⏳ CHỜ XÁC NHẬN", target_score=120, sl_percent=-3, macro_3d_signal=NEUTRAL,
                 setup_line="⚙️ SETUP: Trig=1 | SL=0.97(-3.0%) | TP=1.2 | R/R=1:6.0"),
    # Fatal: GÃY MA7 3D → loại mọi bảng dù điểm cao
    SignalRecord("ORDI", "DC4", "⏳ CHỜ", action_label="⏳ CHỜ XÁC NHẬN", target_score=150, sl_percent=-2, macro_3d_signal=BROKEN,
                 setup_line="⚙️ SETUP: Trig=4.5 | SL=4.4(-2.0%) | TP=5.7 | R/R=1:9.0",
                 grid_tp2_line="🎯 [GRID TP2 - ORDI] 4.376 - 5.714 | 24L | Trig: 4.547 | SL: 4.278 | TP: 5.714",
                 wide_grid_line="🦅 [WIDE GRID 3D - ORDI] 4 - 6 | 28L | Trig: 4.5 | SL: 3.9 | TP: 6"),
    # Grid TP2 backup: NÉ GRID
    SignalRecord("SKL", "DC2", "⏳ CHỜ", target_score=60, macro_3d_signal=NEUTRAL,
                 grid_tp2_line="🎯 [GRID TP2 - SKL] 0.0043 - 0.0049 | 16L | Trig: 0.00483 | SL: 0.00423 | TP: 0.00493"),
    # Wide backup: vola thấp / trung tính
    SignalRecord("XRP", "DC1", target_score=85, macro_3d_signal=BREAK,
                 wide_grid_line="🦅 [WIDE GRID 3D - XRP] 1.3673 - 1.9145 | 28L | Trig: 1.4546 | SL: 1.2990 | TP: 1.9145"),
    SignalRecord("AVAX", "DC1", target_score=75, macro_3d_signal=NEUTRAL,
                 wide_grid_line="🦅 [WIDE GRID 3D - AVAX] 10.448 - 13.314 | 28L | Trig: 10.948 | SL: 9.925 | TP: 13.314"),
    SignalRecord("TON", "DC5", target_score=99, macro_3d_signal=FLAG, bounces=9,   # CẤP 3 → fatal
                 wide_grid_line="🦅 [WIDE GRID 3D - TON] 1 - 2 | 28L | Trig: 1.2 | SL: 0.9 | TP: 2"),
]
live2 = {f"{s}USDT": {"daily_vola": v} for s, v in {"XRP": 2.6, "AVAX": 5.6, "TON": 9}.items()}
df2 = pd.DataFrame([{"Symbol": "UMA", "Phân Loại Grid": "⛔ NÉ GRID (Tiền Sử Xả Dốc 7D)", "TỔNG": 40},
                    {"Symbol": "SKL", "Phân Loại Grid": "⛔ NÉ GRID (Bơm Xả Râu Dài)", "TỔNG": 30},
                    {"Symbol": "XRP", "Phân Loại Grid": "🛡️ GRID ĐÓN PULLBACK", "TỔNG": 53.3},
                    {"Symbol": "AVAX", "Phân Loại Grid": "🌊 GRID TÍCH LŨY", "TỔNG": 70.1}])
ew2 = ew + [{"symbol": "NEAR/USDT", "level": 1, "label": "⚠️ CẤP 1: THEO DÕI"}]
r3 = generate_summary_board(weak, "✅ BTC ổn định", live2, df2, ew2)
assert r3["grid_tp2_is_backup"] and [s.symbol for s in r3["grid_tp2"]] == ["UMA", "SKL"], r3["grid_tp2"]
# UMA đã vào Bảng 2 → chống trùng → Spot chỉ còn NEAR
assert r3["spot_is_backup"] and [s.symbol for s in r3["spot"]] == ["NEAR"], [s.symbol for s in r3["spot"]]
assert r3["wide_grid_is_backup"] and [s.symbol for s in r3["wide_grid"]] == ["AVAX", "XRP"], [s.symbol for s in r3["wide_grid"]]

r4 = generate_summary_board(weak, "Fakeout Risk Cao", live2, df2, ew2)
assert r4["spot"] == []
r5 = generate_summary_board([], "", {}, None, [])
assert r5["spot"] == [] and r5["grid_tp2"] == [] and r5["wide_grid"] == []

# ───────── FILL-UP (mặc định 2 / 3 / 2): primary thiếu slot → lấp bằng backup ─────────
mixed = weak + [
    SignalRecord("CHIP", "DC4", "🎯 🚀 VÀO LỆNH PULLBACK", target_score=100, rr_ratio=11.4, sl_percent=-4.0, macro_3d_signal=BREAK,
                 setup_line="⚙️ SETUP: Trig=0.05207 | SL=0.0499875(-4.0%) | TP=0.075823(+45.6%) | R/R=1:11.4",
                 grid_tp2_line="🎯 [GRID TP2 - CHIP] 0.044034 - 0.075823 | 24L | Trig: 0.052070 | SL: 0.038080 | TP: 0.075823"),
    SignalRecord("SEI", "DC2", "APPROVED 🟢", target_score=85, rr_ratio=5.0, sl_percent=-2.0, macro_3d_signal=BREAK,
                 setup_line="⚙️ SETUP: In=0.07 | SL=0.0686(-2.0%) | TP=0.077(+10%) | R/R=1:5.0"),
    SignalRecord("PUMP", "DC5", target_score=61.12, macro_3d_signal=FLAG, bounces=8.5, bounce_range=3.82,
                 wide_grid_line="🦅 [WIDE GRID 3D - PUMP] 0.005474 - 0.007834 | 28L | Trig: 0.006453 | SL: 0.005053 | TP: 0.007834"),
]
live3 = dict(live2, PUMPUSDT={"daily_vola": 11.3})
r6 = generate_summary_board(mixed, "✅ BTC ổn định", live3, df2, ew2)
assert [s.symbol for s in r6["grid_tp2"]] == ["CHIP", "UMA", "SKL"] and r6["grid_tp2_backup_symbols"] == {"UMA", "SKL"}
assert [s.symbol for s in r6["spot"]] == ["SEI", "NEAR"] and r6["spot_backup_symbols"] == {"NEAR"}
assert [s.symbol for s in r6["wide_grid"]] == ["PUMP", "AVAX"] and r6["wide_grid_backup_symbols"] == {"AVAX"}

# fill_with_backup=False → primary có hàng thì KHÔNG lấp
r7 = generate_summary_board(mixed, "", live3, df2, ew2, config={"fill_with_backup": False})
assert [s.symbol for s in r7["grid_tp2"]] == ["CHIP"] and not r7["grid_tp2_backup_symbols"]

# ───────── Công bằng thang điểm: DC3 75đ / 4.5x phải đứng trên DC4 100đ / 3.2x ─────────
def _g(sym, eng, score, mf):
    return SignalRecord(sym, eng, target_score=score, money_flow=mf, macro_3d_signal=BREAK,
                        grid_tp2_line=f"🎯 [GRID TP2 - {sym}] 1 - 2 | 24L | Trig: 1.5 | SL: 0.9 | TP: 2")
fair = [_g("HOT", "DC4", 100, 3.2), _g("BRK", "DC3", 75, 4.5), _g("SNP", "DC2", 90, 0), _g("HOT2", "DC4", 110, 0)]
r8 = generate_summary_board(fair, "", {}, None, [])
assert [s.symbol for s in r8["grid_tp2"]] == ["BRK", "HOT", "HOT2"], [s.symbol for s in r8["grid_tp2"]]
print("\nALL TESTS PASSED")
