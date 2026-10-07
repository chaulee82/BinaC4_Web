"""Smoke test: Bảng 3 (Tĩnh 65 + Awakening 35) & DC2 C0 Macro Bonus."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from core.coin_filter import get_klines_live, _calc_awakening_bonus, analyze_early

print("=== BẢNG 3: Awakening Bonus ===")
for sym in ["MEGAUSDT", "PORTALUSDT", "ALICEUSDT", "NEARUSDT", "AAVEUSDT"]:
    try:
        df = get_klines_live(sym, "1d", limit=180)
        if df is None or df.empty:
            print(f"{sym}: no data"); continue
        a = _calc_awakening_bonus(df)
        r = analyze_early(sym, {'quote_vol': 5_000_000, 'change_24h': 0})
        tot = f"Điểm={r['Điểm']} (Tĩnh {r['Đ.Tĩnh']}/65)" if r else "(không qua màng lọc cứng Bảng 3)"
        print(f"{sym:<12} Awake={a['score']:>4}/35 MA={a['ma_score']} Vol={a['pocket_score']} RSI={a['rsi_score']} "
              f"RSI1D={a['rsi_1d']} PP={a['pocket_pivot']} | {tot}")
    except Exception as e:
        print(f"{sym}: ERR {e}")

print("\n=== DC2: Macro Momentum Bonus C0 ===")
from strategies.macro_pullback.pullback_sniper import PullbackSniper
sn = PullbackSniper()
for sym in ["BNB/USDT", "AAVE/USDT", "NEAR/USDT"]:
    gate = sn.check_macro_trend_1d(sym)
    res = sn.evaluate_candidate(sym, '4h', macro_gate=gate)
    if 'error' in res:
        print(f"{sym}: ERR {res['error']}"); continue
    c0 = res.get('details', {}).get('Gate_0_MacroBonus', {})
    print(f"{sym:<10} total={res['total_score']} base={res.get('base_score')} C0=+{res.get('macro_bonus', 0)} | {c0.get('status', gate.get('reason'))}")
