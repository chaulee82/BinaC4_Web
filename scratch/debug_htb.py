import sys, traceback
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd, numpy as np
from core.coin_filter import get_klines_live
from strategies.hot_trend_pullback import _calc_rsi, _swing_high, _swing_low, _check_c0_cycle, HotTrendPullback

symbol = 'SOLUSDT'
print(f"Testing {symbol}...")

df_1h  = get_klines_live(symbol, '1h',  limit=100)
df_15m = get_klines_live(symbol, '15m', limit=10)
df_1d  = get_klines_live(symbol, '1d',  limit=180)

print(f"Data: 1H={len(df_1h)} 15M={len(df_15m)} 1D={len(df_1d)}")

try:
    close_live = float(df_1h['Close'].iloc[-1])
    ema20_1h   = float(df_1h['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
    ema50_1h   = float(df_1h['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
    rsi_1h     = _calc_rsi(df_1h['Close'], period=14)
    sh_48      = _swing_high(df_1h, lookback=48)

    print(f"close={close_live:.4f}  EMA20={ema20_1h:.4f}  EMA50={ema50_1h:.4f}  RSI={rsi_1h:.1f}")
    print(f"ema_ok={(ema20_1h > ema50_1h) and (close_live > ema20_1h)}")
    print(f"sh_48={sh_48:.4f}  pullback={(sh_48-close_live)/sh_48*100:.2f}%")

    c0 = _check_c0_cycle(df_1d)
    print(f"C0 score={c0['score']}  label={c0['label']}")

    # Full call
    result = HotTrendPullback.analyze_symbol(symbol)
    if result:
        print(f"RESULT OK - Diem={result.get('Diem')}  Action={result.get('Hanh Dong', result.get('Hành Động'))}")
    else:
        print("RESULT = None")

except Exception as e:
    traceback.print_exc()
