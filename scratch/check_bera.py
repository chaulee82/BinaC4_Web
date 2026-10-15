import sqlite3
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/binac4_history.db')
c = conn.cursor()
c.execute("SELECT timestamp, engine, score, action FROM signal_logs WHERE symbol='BERA' ORDER BY timestamp DESC LIMIT 30")
rows = c.fetchall()
print(f"Total signal_logs for BERA: {len(rows)}")
for r in rows:
    print(r)
