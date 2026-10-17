import sys
sys.path.insert(0, '.')
from core.klines_cache import get_klines_cached
import pandas as pd
import pandas_ta as ta

d4 = get_klines_cached('DUSKUSDT', '4h', 50)
print("d4 shape:", d4.shape)
print("d4 columns:", d4.columns.tolist())

# Check what prep_df does:
df = d4.copy()
# Note: d4 columns from klines_cache are ['Open_Time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Quote_Volume', 'Taker_Buy_Quote']
df.ta.supertrend(length=10, multiplier=3.0, append=True)
print("df after supertrend columns:", df.columns.tolist())
st_cols = [c for c in df.columns if 'SUPERT' in c]
print("st_cols:", st_cols)
if st_cols:
    print("latest row values:")
    print(df[st_cols].iloc[-1])
    print("latest close:", df['Close'].iloc[-1])
