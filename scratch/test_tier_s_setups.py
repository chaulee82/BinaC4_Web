import sys, os
sys.path.insert(0, os.path.abspath("."))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from core.macro_levels import get_3d_profile, _build_wide_grid
from core.grid_tp2_builder import build_macro_grid_payload, _make_formatter
from core.exchange_info_cache import ExchangeInfoCache

def calculate_tier_s_setups(symbol: str):
    d3 = get_3d_profile(symbol) or {}
    raw = d3.get('_raw') or {}
    close = float(raw.get('close_3d', 0.0) or 0.0)
    ma7 = float(raw.get('ma7_3d', 0.0) or 0.0)
    boll_up = float(raw.get('boll_up_3d', 0.0) or 0.0)
    
    tick_size = 0.0001
    try:
        cache = ExchangeInfoCache()
        t = cache.get_tick_size(f"{symbol}USDT")
        if t and t > 0:
            tick_size = t
    except Exception:
        pass
        
    f = _make_formatter(tick_size)
    if close <= 0:
        return None

    # 1. OCO Setup (Spot Swing)
    # Entry: quanh MA7 3D (đón nhúng) hoặc sát giá hiện tại
    entry = min(close, max(ma7 * 1.01, close * 0.98)) if ma7 > 0 else close
    sl = ma7 * 0.96 if ma7 > 0 else entry * 0.95
    tp = boll_up if boll_up > entry * 1.05 else entry * 1.15
    sl_pct = (entry - sl) / entry * 100 if entry > 0 else 0
    tp_pct = (tp - entry) / entry * 100 if entry > 0 else 0
    rr = tp_pct / sl_pct if sl_pct > 0 else 2.5
    oco_line = f"⚙️ SETUP OCO: Buy={f(entry)} | SL={f(sl)}(-{sl_pct:.1f}%) | TP={f(tp)}(+{tp_pct:.1f}%) | R:R=1:{rr:.1f}"

    # 2. Grid TP2
    grid_payload = build_macro_grid_payload(
        symbol=symbol,
        entry=entry,
        sl_short=sl,
        tp_target=tp,
        macro_sl=sl * 0.96,
        tick_size=tick_size
    )
    grid_line = grid_payload["display_line"].strip() if grid_payload else ""

    # 3. Wide Grid 3D
    wg = _build_wide_grid(close, ma7 if ma7 > 0 else close * 0.95, boll_up if boll_up > 0 else close * 1.15, tick_size)
    wide_line = ""
    if wg and wg.get("valid"):
        wide_line = (
            f"🦅 [WIDE GRID 3D - {symbol}] {f(wg['wide_low'])} - {f(wg['wide_up'])} | {wg['wide_grids']}L | "
            f"Trig: {f(wg['wide_trig'])} | SL: {f(wg['wide_sl'])} | TP: {f(wg['wide_up'])} "
            f"(Vốn {wg['capital']:.0f}$ ≈ {wg['per_grid_usdt']}$/lưới)"
        )

    return {
        "symbol": symbol,
        "close": close,
        "oco": oco_line,
        "grid_tp2": grid_line,
        "wide_grid": wide_line
    }

if __name__ == '__main__':
    res = calculate_tier_s_setups('BERA')
    print("=" * 90)
    print("💎 THIẾT LẬP LỆNH CHO MÃ TIER S (KIM CƯƠNG): BERA (88.2đ)")
    print("=" * 90)
    print("  ↳", res["oco"])
    print(" ", res["grid_tp2"])
    print("  ↳", res["wide_grid"])
    print("=" * 90)
