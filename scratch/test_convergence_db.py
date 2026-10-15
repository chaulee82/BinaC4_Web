import os
import sqlite3
import tempfile
from core.history_logger import HistoryLogger
from core.historical_rater import HistoricalDataQuery, calculate_raw_base_score

def test_optimal_conclusion_persistence():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db = tf.name

    try:
        # 1. Initialize HistoryLogger and save convergence signals
        logger = HistoryLogger(db_path=temp_db)
        
        ranks = {
            "BTC": "🏆 [HẠNG S] VỤ NỔ VĨ MÔ",
            "ETH": "🥇 [HẠNG A1] SIÊU PULLBACK",
            "SOL": "🥉 [HẠNG B] CỖ MÁY CASHFLOW"
        }
        logger.save_convergence_signals(ranks)
        
        # Verify signal_logs entries
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT symbol, engine, score, action FROM signal_logs WHERE engine = 'OPTIMAL_CONVERGENCE'").fetchall()
            assert len(rows) == 3, f"Expected 3 rows in signal_logs, got {len(rows)}"
            
            btc_row = [r for r in rows if r[0] == "BTC"][0]
            assert btc_row[2] == 10.0, f"BTC score expected 10.0, got {btc_row[2]}"
            assert "HẠNG S" in btc_row[3], f"BTC action expected HẠNG S, got {btc_row[3]}"
            
            eth_row = [r for r in rows if r[0] == "ETH"][0]
            assert eth_row[2] == 5.0, f"ETH score expected 5.0, got {eth_row[2]}"
            
        print("✅ Step 1: save_convergence_signals saved correctly to signal_logs")

        # 2. Test get_latest_convergence_tier from HistoryLogger
        btc_tier = logger.get_latest_convergence_tier("BTCUSDT")
        assert "HẠNG S" in btc_tier, f"Expected HẠNG S, got {btc_tier}"
        print(f"✅ Step 2: HistoryLogger.get_latest_convergence_tier('BTCUSDT') -> {btc_tier}")

        # 3. Test sync_from_signal_logs to daily_metrics
        rater = HistoricalDataQuery(db_path=temp_db)
        synced_count = rater.sync_from_signal_logs()
        assert synced_count >= 3, f"Expected >= 3 records synced, got {synced_count}"

        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            metrics = cursor.execute("SELECT symbol, optimal_tier FROM daily_metrics").fetchall()
            metrics_dict = {m[0]: m[1] for m in metrics}
            assert "HẠNG S" in metrics_dict.get("BTC", ""), f"BTC optimal_tier in daily_metrics: {metrics_dict.get('BTC')}"
            assert "HẠNG A1" in metrics_dict.get("ETH", ""), f"ETH optimal_tier in daily_metrics: {metrics_dict.get('ETH')}"
            assert "HẠNG B" in metrics_dict.get("SOL", ""), f"SOL optimal_tier in daily_metrics: {metrics_dict.get('SOL')}"

        print(f"✅ Step 3: sync_from_signal_logs populated optimal_tier into daily_metrics: {metrics_dict}")

        # 4. Test HistoricalDataQuery.get_latest_convergence_tier
        sol_tier = rater.get_latest_convergence_tier("SOL")
        assert "HẠNG B" in sol_tier, f"Expected HẠNG B, got {sol_tier}"
        print(f"✅ Step 4: rater.get_latest_convergence_tier('SOL') -> {sol_tier}")

        # 5. Test calculate_raw_base_score with convergence premium
        base_s = calculate_raw_base_score({
            "Price": 100, "MA7_3D": 90, "MA99_3D": 80, "ST_4H": "Green", # 25đ 3D Strict
            "Grid_Rank": 1, "Grid_Status": "AN TOÀN",                      # 10đ Rebalance
            "Optimal_Conclusion_Tier": "🏆 [HẠNG S] VỤ NỔ VĨ MÔ"           # +10đ Hội tụ -> Total 45 -> Capped at 40
        })
        assert base_s == 40.0, f"Expected 40.0, got {base_s}"

        base_a = calculate_raw_base_score({
            "Price": 100, "MA7_3D": 90, "MA99_3D": 110, "ST_4H": "Green", # 15đ 3D Strict
            "Grid_Rank": 20, "Grid_Status": "AN TOÀN",                     # 0đ Rebalance
            "Optimal_Conclusion_Tier": "🥇 [HẠNG A1] SIÊU PULLBACK"        # +5đ Hội tụ -> Total 20
        })
        assert base_a == 20.0, f"Expected 20.0, got {base_a}"
        print(f"✅ Step 5: calculate_raw_base_score correctly rewards +10đ / +5đ for optimal tiers")

        print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")

    finally:
        if os.path.exists(temp_db):
            try:
                os.remove(temp_db)
            except Exception:
                pass

if __name__ == "__main__":
    test_optimal_conclusion_persistence()
