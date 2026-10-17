import sqlite3

conn = sqlite3.connect('data/binac4_history.db')
cur = conn.cursor()
row_ts = cur.execute('SELECT MAX(timestamp) FROM signal_logs').fetchone()
print("Max ts:", row_ts)
if row_ts and row_ts[0]:
    ts = row_ts[0]
    # Exact match count:
    c_exact = cur.execute('SELECT COUNT(*) FROM signal_logs WHERE timestamp = ?', (ts,)).fetchone()[0]
    print(f"Exact match on {ts}: {c_exact} rows")

    # Window match (15 mins):
    c_window = cur.execute("SELECT COUNT(*) FROM signal_logs WHERE timestamp >= datetime(?, '-15 minutes') AND timestamp <= datetime(?, '+1 minute')", (ts, ts)).fetchone()[0]
    print(f"Window match: {c_window} rows")
