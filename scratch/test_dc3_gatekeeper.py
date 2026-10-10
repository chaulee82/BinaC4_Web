import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import core.macro_levels as ml
ml.get_3d_profile = lambda *a, **k: None          # cô lập: không bonus 3D, không gọi mạng
from strategies.momentum_breakout import MomentumBreakout
from core.indicator_engine import IndicatorEngine

H = 3600 * 1000
NOW = int(time.time() * 1000)

class FakeExchange:
    def __init__(self, candles): self.candles = candles
    def fetch_ohlcv(self, *a, **k): return self.candles
    def publicGetKlines(self, *a, **k):            # Taker Buy 70% → 20đ
        return [[0, 0, 0, 0, 0, 0, 0, "1000", 0, 0, "700", 0]]

def build(closed, running):
    rows = []
    for i in range(140):
        lo = 5.0 if i == 100 else 9.9              # 1 nến đáy sâu (trong cửa sổ 100 nến) → hộp lớn → R/R tốt
        rows.append([NOW - (150 - i) * 4 * H, 9.95, 10.0, lo, 9.95, 100.0])
    rows.append([NOW - 5 * H, *closed])
    rows.append([NOW - 1 * H, *running])           # nến 4H đang chạy 1h → Vol ×4 dự phóng
    return rows

def run(closed, running):
    s = MomentumBreakout.__new__(MomentumBreakout)
    s.exchange, s.engine = FakeExchange(build(closed, running)), IndicatorEngine()
    return s.evaluate_breakout("TEST/USDT", "4h", btc_gate={"ok": True, "reason": "test", "rsi": 60})

flat = [9.95, 10.0, 9.9, 9.95, 100.0]
# Kịch bản A: chỉ nến ĐANG CHẠY vượt cản (15) + Vol dự phóng 4x (25) + Taker (20) + R/R (25) = 85
a = run(flat, [9.95, 10.35, 9.95, 10.3, 100.0])
print("A:", a["total_score"], "|", a["details"]["Gate_1_PriceAction"], "|", a["action"], "| exec =", a["execution_allowed"])
print("   ", a["details"])
assert a["total_score"] >= 85 and not a["execution_allowed"] and a["action"].startswith("🟡 THEO DÕI: Chờ đóng nến")

# Kịch bản B: nến ĐÃ ĐÓNG breakout sạch (30) → được bóp cò
b = run([9.95, 10.32, 9.95, 10.3, 100.0], [10.3, 10.4, 10.28, 10.35, 100.0])
print("B:", b["total_score"], "|", b["details"]["Gate_1_PriceAction"], "|", b["action"], "| exec =", b["execution_allowed"])
assert b["total_score"] >= 85 and b["execution_allowed"] and b["action"].startswith("🟢")

# Kịch bản C: giống B nhưng Gate 4 trả SL sâu → phủ quyết theo chốt SL ≥ 15% (điểm radar giữ nguyên)
def run_sl(sl_ratio):
    orig = MomentumBreakout.evaluate_risk
    def fake_risk(self, df, entry):
        r = orig(self, df, entry)
        r["sl"] = entry * sl_ratio
        return r
    MomentumBreakout.evaluate_risk = fake_risk
    try:
        return run([9.95, 10.32, 9.95, 10.3, 100.0], [10.3, 10.4, 10.28, 10.35, 100.0])
    finally:
        MomentumBreakout.evaluate_risk = orig

c = run_sl(1 - 0.196)
print("C:", c["total_score"], "|", c["action"], "| exec =", c["execution_allowed"], "|", c["execution_veto_reason"])
assert c["total_score"] == b["total_score"] and not c["execution_allowed"]
assert c["action"].startswith("🔴 TỪ CHỐI — SL RỘNG 19.6%") and "SL quá rộng" in c["execution_veto_reason"]
c15 = run_sl(0.85)
assert not c15["execution_allowed"], "SL đúng 15% phải bị chặn (≥ 15%)"
c149 = run_sl(1 - 0.149)
print("C 14.9%:", c149["action"], "| exec =", c149["execution_allowed"])
assert c149["execution_allowed"], "SL 14.9% phải được bóp cò"
print("GATEKEEPER OK")
