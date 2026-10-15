"""Kiểm tra: print_14_day_summary_board phải ghi nhận Điểm 3D Strict (cùng nguồn tag [3D STRICT] ở Động cơ)."""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
from core.historical_rater import print_14_day_summary_board
from core.macro_levels import validate_strict_3d_wave, strict_3d_short_tag

db = "scratch/test_strict_14d.db"
if os.path.exists(db):
    os.remove(db)

df = pd.DataFrame([{"Symbol": "BTC", "TỔNG": 70.0}, {"Symbol": "ETHUSDT", "TỔNG": 60.0}])
sw = [{"symbol": "SOL", "score": 85.0}]
for s in ("BTC", "ETH", "SOL"):
    print(s, "→ tag động cơ:", strict_3d_short_tag(validate_strict_3d_wave(s)))
print_14_day_summary_board(db_path=db, df_summary=df, early_list=[], super_wave_results=sw, top_n=10)
