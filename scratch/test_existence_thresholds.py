import os
import sys
import sqlite3
import tempfile
import gc

# Configure stdout utf-8
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.history_logger import HistoryLogger
from core.historical_rater import HistoricalDataQuery

def test_existence_with_min_score():
    print("--- KIỂM THỬ RÀNG BUỘC ĐIỂM TỐI THIỂU KHI XÉT TỒN TẠI ---")
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        test_db = f.name
    
    try:
        logger = HistoryLogger(test_db)
        rater = HistoricalDataQuery(test_db)
        
        # 1. Test get_symbol_history
        # Insert signals:
        # COIN_WEAK: DC1 30đ, DC1 45đ, DC1 50đ (tất cả < 60đ)
        # COIN_REJECT: DC1 75đ nhưng action REJECT (CẤP 3)
        # COIN_STRONG: DC1 65đ, DC1 70đ, DC1 80đ (hợp lệ)
        records = [
            {"timestamp": "2026-10-25 09:00:00", "symbol": "COIN_WEAK", "engine": "DC1", "score": 30.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:05:00", "symbol": "COIN_WEAK", "engine": "DC1", "score": 45.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:10:00", "symbol": "COIN_WEAK", "engine": "DC1", "score": 50.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:00:00", "symbol": "COIN_REJECT", "engine": "DC1", "score": 75.0, "action": "REJECT (CẤP 3)"},
            {"timestamp": "2026-10-25 09:00:00", "symbol": "COIN_STRONG", "engine": "DC1", "score": 65.0, "action": "BUY"},
            {"timestamp": "2026-10-25 09:05:00", "symbol": "COIN_STRONG", "engine": "DC1", "score": 70.0, "action": "BUY"},
            {"timestamp": "2026-10-25 09:10:00", "symbol": "COIN_STRONG", "engine": "DC1", "score": 80.0, "action": "BUY"},
        ]
        logger.save_signals(records)
        
        hist_weak = logger.get_symbol_history("COIN_WEAK", days=14, min_score=60.0)
        hist_reject = logger.get_symbol_history("COIN_REJECT", days=14, min_score=60.0)
        hist_strong = logger.get_symbol_history("COIN_STRONG", days=14, min_score=60.0)
        
        assert len(hist_weak) == 0, f"COIN_WEAK phải có 0 bản ghi đủ chuẩn, nhưng có {len(hist_weak)}"
        assert len(hist_reject) == 0, f"COIN_REJECT phải bị loại do action, nhưng có {len(hist_reject)}"
        assert len(hist_strong) == 3, f"COIN_STRONG phải có 3 bản ghi, nhưng có {len(hist_strong)}"
        print("✅ PASS: get_symbol_history() chặn chuẩn xác các mã < 60đ hoặc dính REJECT/CẤP 3!")

        # 2. Test sync_from_signal_logs to daily_metrics
        # Thêm các mã DC2, DC3, DC4, DC5 với điểm thấp và điểm cao
        extra_records = [
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_LOW_PUSH", "engine": "DC2", "score": 40.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_LOW_PUSH", "engine": "DC3", "score": 50.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_LOW_PUSH", "engine": "DC4", "score": 35.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_LOW_PUSH", "engine": "DC5", "score": 15.0, "action": "OK"},
            
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_HIGH_PUSH", "engine": "DC2", "score": 70.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_HIGH_PUSH", "engine": "DC3", "score": 80.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_HIGH_PUSH", "engine": "DC4", "score": 75.0, "action": "OK"},
            {"timestamp": "2026-10-25 09:15:00", "symbol": "COIN_HIGH_PUSH", "engine": "DC5", "score": 35.0, "action": "OK"},
        ]
        logger.save_signals(extra_records)
        rater.sync_from_signal_logs()
        
        with sqlite3.connect(test_db) as conn:
            cur = conn.cursor()
            low_m = cur.execute("SELECT dc2_sniper_count, dc3_breakout_count, dc4_hottrend_count, dc5_pingpong_score FROM daily_metrics WHERE symbol = 'COIN_LOW_PUSH'").fetchone()
            high_m = cur.execute("SELECT dc2_sniper_count, dc3_breakout_count, dc4_hottrend_count, dc5_pingpong_score FROM daily_metrics WHERE symbol = 'COIN_HIGH_PUSH'").fetchone()
            
        assert low_m == (0, 0, 0, 0), f"COIN_LOW_PUSH phải có toàn bộ count = 0, got: {low_m}"
        assert high_m == (1, 1, 1, 1), f"COIN_HIGH_PUSH phải có toàn bộ count = 1, got: {high_m}"
        print("✅ PASS: sync_from_signal_logs() chỉ đếm các tín hiệu đạt ngưỡng sàn (>=60đ cho DC2/3/4 và >=25đ cho DC5)!")

        # 3. Test _calc_scores_from_daily_metric_row
        with sqlite3.connect(test_db) as conn:
            cur = conn.cursor()
            cols = [
                "date", "raw_base_score", "b3a_score", "b3b_score", "rebalance_score", "strict_3d_score",
                "dc2_sniper_count", "dc3_breakout_count", "dc4_hottrend_count", "dc5_pingpong_score",
                "penalty_level_3", "penalty_xaray", "penalty_fakeout",
                "grid_rank", "grid_status", "in_top_3a", "in_top_3b", "optimal_tier",
                "price_above_ma7_3d", "ma7_gt_ma99_3d", "st_4h_green",
                "money_flow_score", "dc2_score", "dc3_score", "dc4_score", "dc5_score",
                "dc5_rank"
            ]
            row_low = cur.execute(f"SELECT {', '.join(cols)} FROM daily_metrics WHERE symbol = 'COIN_LOW_PUSH'").fetchone()
            row_high = cur.execute(f"SELECT {', '.join(cols)} FROM daily_metrics WHERE symbol = 'COIN_HIGH_PUSH'").fetchone()
            
        base_low, push_low, cash_low = rater._calc_scores_from_daily_metric_row(row_low)
        assert push_low == 0.0, f"push_low phải là 0.0đ do điểm < 60, got: {push_low}"
        
        base_high, push_high, cash_high = rater._calc_scores_from_daily_metric_row(row_high)
        assert push_high > 0.0, f"push_high phải > 0đ, got: {push_high}"
        print(f"✅ PASS: _calc_scores_from_daily_metric_row() tính chuẩn: Low Push = {push_low}đ, High Push = {push_high}đ!")

        print("\n🎉 TẤT CẢ KIỂM THỬ RÀNG BUỘC ĐIỂM TỐI THIỂU ĐỀU THÀNH CÔNG RỰC RỠ!")
    finally:
        gc.collect()
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except Exception:
                pass

if __name__ == "__main__":
    test_existence_with_min_score()
