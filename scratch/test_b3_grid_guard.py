"""Test chốt chặn lưới Bảng 3 — tái hiện lỗi PARTI (SL âm, 2143 lưới)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

import core.coin_filter as cf

def show(tag, gs):
    if gs.get('is_dual_grid'):
        print(f"{tag}: G1 {cf.smart_price(gs['g1_lower'])}-{cf.smart_price(gs['g1_upper'])} ({gs['g1_grids']}L) | "
              f"G2 {cf.smart_price(gs['g2_lower'])}-{cf.smart_price(gs['g2_upper'])} ({gs['g2_grids']}L) | "
              f"SL {cf.smart_price(gs['hard_stop_loss'])} | TP {cf.smart_price(gs['hard_take_profit'])}")
    else:
        print(f"{tag}: {gs.get('status')} {cf.smart_price(gs.get('lower_bound'))}-{cf.smart_price(gs.get('upper_bound'))} "
              f"({gs.get('num_grids')}L) | SL {cf.smart_price(gs.get('hard_stop_loss'))} | TP {cf.smart_price(gs.get('hard_take_profit'))} {gs.get('message','')}")
    if gs.get('b3_guard_note'):
        print("     🛡️", gs['b3_guard_note'])

# 1) Replica log lỗi PARTI: giá 0.0323, SL -0.2525, Upper 0.5863, 2143L, TP 0.7709
bad = {
    'Symbol': 'PARTI', '_raw_symbol': 'PARTIUSDT', 'Giá': 0.0323, 'Box_Floor': 0.0270, 'Box_Ceiling': 0.0360,
    'grid_setup': {"status": "SUCCESS", "engine": "GRID Darvas 4H (Lưới Kép)", "is_dual_grid": True,
                   "g1_lower": 0.02673, "g1_upper": 0.03564, "g1_grids": 41, "g1_capital_pct": 70,
                   "g2_lower": 0.0323, "g2_upper": 0.5863, "g2_grids": 2143, "g2_capital_pct": 30,
                   "hard_stop_loss": -0.2525, "hard_take_profit": 0.7709, "tp_buffer_pct": 0.05},
}
show("TRƯỚC", bad['grid_setup'])
fixed = cf._sanitize_b3_grid(dict(bad))
show("SAU  ", fixed['grid_setup'])
gs = fixed['grid_setup']
assert gs['hard_stop_loss'] > 0 and gs['hard_stop_loss'] >= 0.0323 * 0.75
assert gs['hard_take_profit'] <= 0.0323 * 1.35 + 1e-12
for k in ('g1_grids', 'g2_grids', 'num_grids'):
    if k in gs and gs.get('is_dual_grid', k == 'num_grids') is not None:
        assert gs[k] <= 35, (k, gs[k])
print("✅ Replica PARTI: PASS\n")

# 2) Live: chạy full enrich (Darvas + guard) trên vài mã Bảng 3 tiềm năng
for sym in ["PARTIUSDT", "MEGAUSDT", "PORTALUSDT"]:
    df = cf.get_klines_live(sym, "1d", limit=60)
    if df is None or df.empty:
        print(f"{sym}: no data"); continue
    close = float(df['Close'].iloc[-1])
    item = {'Symbol': sym.replace('USDT', ''), '_raw_symbol': sym, 'Giá': close,
            'Box_Floor': float(df['Low'].min()), 'Box_Ceiling': float(df['High'].max())}
    out = cf._enrich_early_with_darvas(item)
    print(f"{sym} giá {cf.smart_price(close)}")
    show("  LIVE", out.get('grid_setup', {}))
