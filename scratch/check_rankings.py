import sqlite3
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/binac4_history.db')
c = conn.cursor()
c.execute("""
    SELECT symbol, rebalance_score, grid_rank, grid_status, strict_3d_score, raw_base_score, optimal_tier, dc2_score, dc3_score, money_flow_score
    FROM daily_metrics
    WHERE date = '2026-10-15'
    ORDER BY grid_rank ASC LIMIT 15
""")
print(f"{'Mã':<8} | {'RebScore':<9} | {'Rank':<5} | {'Trạng Thái Grid':<35} | {'Móng':<5} | {'Hội Tụ':<25}")
print("-" * 100)
for r in c.fetchall():
    print(f"{r[0]:<8} | {r[1]:<9.1f} | #{r[2]:<4} | {r[3]:<35} | {r[5]:<5.1f} | {r[6] or 'None'}")
