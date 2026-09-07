import sys
import os
import logging

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.entry_calculator import EntryCalculator
from core.risk_calculator import RiskCalculator
from core.price_formatter import price_formatter
from models.market_state import TradeLeg
import ccxt

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

print("="*60)
print("TESTING 3-STEP PIPELINE WITH DYNAMIC INTERPOLATION")
print("="*60)

# Mock CCXT Exchange
exchange = ccxt.binance()
price_formatter._markets = {
    'BONK/USDT': {'precision': {'price': 0.00000001}},
    'SOL/USDT': {'precision': {'price': 0.01}}
}

print("\n--- TEST 1: PULLBACK (DC2/4) WITH RSI WEIGHT ---")
symbol = 'SOL/USDT'
# Mốc hỗ trợ giả định
upper_supp = 150.0 # Hỗ trợ nông (MA25)
lower_supp = 140.0 # Hỗ trợ sâu (BB Bottom)

# Test với RSI yếu (bán tháo)
rsi_weak = 35.0
entry_weak = EntryCalculator.get_pullback_entry(upper_support=upper_supp, lower_support=lower_supp, rsi_1h=rsi_weak)
print(f"RSI {rsi_weak} -> Entry (Expected near Lower): {entry_weak:.2f}")

# Test với RSI cân bằng
rsi_mid = 50.0
entry_mid = EntryCalculator.get_pullback_entry(upper_support=upper_supp, lower_support=lower_supp, rsi_1h=rsi_mid)
print(f"RSI {rsi_mid} -> Entry (Expected Mid): {entry_mid:.2f}")

# Test với RSI khỏe
rsi_strong = 65.0
entry_strong = EntryCalculator.get_pullback_entry(upper_support=upper_supp, lower_support=lower_supp, rsi_1h=rsi_strong)
print(f"RSI {rsi_strong} -> Entry (Expected near Upper): {entry_strong:.2f}")

print("\n--- TEST 2: GRID (DC1) - 70/30 SPLIT WITH LINEAR INTERPOLATION ---")
darvas_top = 0.00000350
darvas_bottom = 0.00000300
grid_res = EntryCalculator.get_grid_entries(
    darvas_top=darvas_top, 
    darvas_bottom=darvas_bottom, 
    num_grids=10, 
    mode='LINEAR'
)

print(f"Gom Day (70%): {len(grid_res['gom_day'])} grids")
for i, e in enumerate(grid_res['gom_day']):
    print(f"  Gom_Day {i+1}: {e:.10f}")
    
print(f"Breakout (30%): {len(grid_res['breakout'])} grids")
for i, e in enumerate(grid_res['breakout']):
    print(f"  Breakout {i+1}: {e:.10f}")

print("\n--- TEST 3: FULL PIPELINE (PULLBACK) ---")
# Tiếp tục test 1 với rsi_mid (Entry = 145.0)
raw_sl = entry_mid - 5.0 # SL = 140.0
raw_tp = RiskCalculator.calculate_dynamic_tp(
    raw_entry=entry_mid, 
    raw_sl=raw_sl, 
    min_rr=1.5, 
    max_tp_pct=0.10
)
if raw_tp:
    leg = TradeLeg(
        symbol=symbol,
        entry_price=price_formatter.format_price(symbol, entry_mid),
        sl_price=price_formatter.format_price(symbol, raw_sl),
        tp_price=price_formatter.format_price(symbol, raw_tp),
        setup_type="PULLBACK"
    )
    if leg.is_valid():
        print(f"[Success] TradeLeg: Entry={leg.entry_price}, SL={leg.sl_price}, TP={leg.tp_price}")
else:
    print("Trade Rejected")
