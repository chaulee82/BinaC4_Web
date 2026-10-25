import os
import sys
import sqlite3
import pandas as pd

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8')
        except Exception:
            pass

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from models.market_state import SymbolState, ScoreContext, GridContext, EntrySetupContext
from views.console_renderer import ConsoleRenderer
from core.history_logger import HistoryLogger

TEST_DB = "scratch/test_pipeline_history.db"
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

def test_console_renderer_data_pool():
    print("--- 1. KIỂM THỬ CONSOLE RENDERER DATA POOL (Top 5 Display + 100% Persistence) ---")
    renderer = ConsoleRenderer()

    # Mock _strict_tag, _print_wide_grid và _get_d3 để chạy offline 100% không gọi Binance API
    ConsoleRenderer._strict_tag = staticmethod(lambda state, engine, kind: "✅ [3D STRICT: PASS]")
    ConsoleRenderer._print_wide_grid = staticmethod(lambda state, engine="", quiet=False: "")
    ConsoleRenderer._get_d3 = staticmethod(lambda state: {})

    # Tạo danh sách giả lập gồm 12 mã cho DC1, DC2, DC3, DC4
    dc1_states = []
    dc2_states = []
    dc3_states = []
    dc4_states = []

    for i in range(12):
        sym = f"COIN{i:02d}"
        
        # DC1
        score_ctx1 = ScoreContext(engine_name="DC1", total_score=90 - i * 5, action_label=f"Action DC1 {i}")
        dc1_states.append(SymbolState(symbol=sym, current_price=1.0, volume_24h=1000.0, avg_vola_24h=3.0, coin_vola_24h=3.5, safety_tag="OK", scores={"DC1": score_ctx1}))

        # DC2
        score_ctx2 = ScoreContext(engine_name="DC2", total_score=95 - i * 5, action_label=f"Action DC2 {i}")
        dc2_states.append(SymbolState(symbol=sym, current_price=1.0, volume_24h=1000.0, avg_vola_24h=3.0, coin_vola_24h=3.5, safety_tag="OK", scores={"DC2": score_ctx2}))

        # DC3
        score_ctx3 = ScoreContext(engine_name="DC3", total_score=85 - i * 5, action_label=f"Action DC3 {i}")
        dc3_states.append(SymbolState(symbol=sym, current_price=1.0, volume_24h=1000.0, avg_vola_24h=3.0, coin_vola_24h=3.5, safety_tag="OK", scores={"DC3": score_ctx3}))

        # DC4
        score_ctx4 = ScoreContext(engine_name="DC4", total_score=88 - i * 5, action_label=f"Action DC4 {i}")
        dc4_states.append(SymbolState(symbol=sym, current_price=1.0, volume_24h=1000.0, avg_vola_24h=3.0, coin_vola_24h=3.5, safety_tag="OK", scores={"DC4": score_ctx4}))

    # Chạy render
    print("\n>>> Render DC1:")
    renderer.render_darvas_grid(dc1_states)
    
    print("\n>>> Render DC2:")
    renderer.render_pullback_sniper(dc2_states)

    print("\n>>> Render DC3:")
    renderer.render_momentum_breakout(dc3_states, btc_gate_label="Bình thường")

    print("\n>>> Render DC4:")
    renderer.render_hot_trend_pullback(dc4_states, htb_count=12, htb_threshold=5.0, tracking_list=[])

    # Kiểm tra signals_pool
    print(f"\nTổng số tín hiệu trong signals_pool: {len(renderer.signals_pool)}")
    assert len(renderer.signals_pool) == 12 * 4, f"Phải có đủ 48 tín hiệu, nhận được: {len(renderer.signals_pool)}"

    # Kiểm tra cờ is_displayed
    dc1_records = [s for s in renderer.signals_pool if s.engine_source == "DC1"]
    displayed_dc1 = [s for s in dc1_records if s.is_displayed]
    quiet_dc1 = [s for s in dc1_records if not s.is_displayed]
    assert len(displayed_dc1) == 5, f"Phải có đúng 5 mã hiển thị Terminal ở DC1, nhận được {len(displayed_dc1)}"
    assert len(quiet_dc1) == 7, f"Phải có 7 mã data pool yên lặng ở DC1, nhận được {len(quiet_dc1)}"
    print(f"✅ signals_pool phân bổ chuẩn: 5 displayed + 7 data pool (tổng {len(dc1_records)})")

    # Kiểm tra lưu vào Database
    print("\n--- 2. KIỂM THỬ LƯU TRỮ VÀO SQLITE DATABASE ---")
    logger = HistoryLogger(db_path=TEST_DB)
    logger.save_signals(renderer.signals_pool)

    with sqlite3.connect(TEST_DB) as conn:
        counts = dict(conn.execute("SELECT engine, count(*) FROM signal_logs GROUP BY engine").fetchall())
        print("Bản ghi trong signal_logs sau Ingestion Filter:", counts)
        # DC1 có 11 mã >= 40đ (mã 12 là 35đ bị lọc)
        # DC2 có 11 mã >= 45đ (mã 12 là 40đ bị lọc)
        # DC3 có 8 mã >= 50đ (các mã < 50đ bị lọc)
        # DC4 có 9 mã >= 45đ (các mã < 45đ bị lọc)
        assert counts.get("DC1") == 11, f"DC1 expected 11, got {counts.get('DC1')}"
        assert counts.get("DC2") == 11, f"DC2 expected 11, got {counts.get('DC2')}"
        assert counts.get("DC3") == 8, f"DC3 expected 8, got {counts.get('DC3')}"
        assert counts.get("DC4") == 9, f"DC4 expected 9, got {counts.get('DC4')}"

    print("✅ Database signal_logs đã lưu toàn bộ mã tiềm năng và lọc sạch 100% mã rác dưới sàn Ingestion Filter!")

if __name__ == "__main__":
    test_console_renderer_data_pool()
    import gc
    gc.collect()
    try:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
    except Exception:
        pass
    print("\n🎉 KIỂM THỬ NÂNG CẤP THÀNH CÔNG VƯỢT TRỘI!")
