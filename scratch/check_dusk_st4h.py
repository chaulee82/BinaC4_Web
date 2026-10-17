import pandas as pd
from core.klines_cache import get_klines_cached
from core.macro_levels import _supertrend_val_dir

df_4h = get_klines_cached("DUSKUSDT", "4h", limit=50)
if df_4h is not None and not df_4h.empty:
    st_val, st_dir = _supertrend_val_dir(df_4h)
    print("DUSK 4H Close:", df_4h.iloc[-1]['close'])
    print(f"DUSK 4H Supertrend: val={st_val}, dir={st_dir} (1=Green, -1=Red)")
else:
    print("No 4h klines for DUSK")
