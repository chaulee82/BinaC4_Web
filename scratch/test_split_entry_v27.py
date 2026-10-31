"""Test TRD v2.7 — Optimal Trigger (Split-Entry) & Bảng Rải Đinh. Chạy: python scratch/test_split_entry_v27.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from core.split_entry_trigger import (
    calculate_optimal_trigger, calculate_optimal_trigger_detail, find_swing_low_15m,
)
from views.split_entry_board import (
    collect_candidates, build_split_entry_rows, render_split_entry_table, status_icon,
)


def base(**kw):
    d = {"live_price": 100.0, "btc_rsi_1h": 55.0, "tick_size": 0.01}
    d.update(kw)
    return d


class TestOptimalTrigger(unittest.TestCase):
    def test_step1_dump_switches_ma7_to_ma25(self):
        det = calculate_optimal_trigger_detail(base(ma7_3d=95.5, ma25_3d=90.0, btc_rsi_1h=35))
        self.assertTrue(det["is_market_dump"])
        self.assertIn("MA25 3D", det["supports"])
        self.assertNotIn("MA7 3D", det["supports"])

    def test_step3_pick_nearest_to_95pct(self):
        # 95 là tham chiếu: BB 94.8 gần nhất (OCO 92, Swing 97.5)
        trig = calculate_optimal_trigger(base(oco_buy_limit_price=92, swing_low_15m=97.5, bb_mid_4h=94.8))
        self.assertAlmostEqual(trig, 94.8)

    def test_step3_buffer_forced_when_too_close(self):
        # Mốc duy nhất 98 (-2%) > -3% → ép 96.5; MA7 3D = 99 KHÔNG thấp hơn → giữ 96.5
        det = calculate_optimal_trigger_detail(base(swing_low_15m=98.0, ma7_3d=99.0))
        # MA7 99 cũng là mốc; mốc gần 95 nhất là 98 → Distance -2% → đệm
        self.assertIn("BUFFER_FORCED", det["flags"])
        self.assertAlmostEqual(det["optimal_trigger"], 96.5)

    def test_step3_buffer_pushes_deeper_to_ma7(self):
        # Mốc gần 95 nhất = 98 (BB) → đệm 96.5 → MA7 3D 93 < 96.5 → lùi sâu 93
        # (MA7 93 cách 95 là 2.0, BB 98 cách 3.0 → MA7 được chọn ngay từ đầu; dùng 92 để BB thắng)
        det = calculate_optimal_trigger_detail(base(bb_mid_4h=97.5, ma7_3d=92.0))
        self.assertIn("BUFFER_FORCED", det["flags"])
        self.assertIn("PUSHED_TO_MA7_3D", det["flags"])
        self.assertAlmostEqual(det["optimal_trigger"], 92.0)

    def test_step3_support_above_live_forced(self):
        det = calculate_optimal_trigger_detail(base(oco_buy_limit_price=101.0))
        self.assertAlmostEqual(det["optimal_trigger"], 96.5)

    def test_step4_supertrend_guard_green(self):
        trig = calculate_optimal_trigger(base(bb_mid_4h=94.0, supertrend_1d=94.5, supertrend_dir_1d=1))
        self.assertAlmostEqual(trig, round(94.5 * 1.015, 2))

    def test_step4_supertrend_red_ignored(self):
        trig = calculate_optimal_trigger(base(bb_mid_4h=94.0, supertrend_1d=110.0, supertrend_dir_1d=-1))
        self.assertAlmostEqual(trig, 94.0)

    def test_step5_tick_rounding(self):
        trig = calculate_optimal_trigger(base(bb_mid_4h=94.123456, tick_size=0.001))
        self.assertAlmostEqual(trig, 94.123)

    def test_no_live_price(self):
        self.assertIsNone(calculate_optimal_trigger({"btc_rsi_1h": 50}))

    def test_swing_low_wick_filter(self):
        df = pd.DataFrame([
            {"open": 100, "high": 101, "low": 90, "close": 100},   # wick 10/11 > 0.3 ✓ low 90
            {"open": 95, "high": 100, "low": 80, "close": 99},     # wick 15/20 ✓ low 80
            {"open": 81, "high": 100, "low": 70, "close": 99},     # wick 11/30 = 0.37 ✓ low 70
            {"open": 72, "high": 100, "low": 60, "close": 99},     # wick 12/40 = 0.3 ✗ (không > 0.3)
        ])
        self.assertEqual(find_swing_low_15m(df), 70.0)


class TestBoard(unittest.TestCase):
    def test_icons(self):
        self.assertTrue(status_icon(-1.73).startswith("🚨"))
        self.assertTrue(status_icon(-4.0).startswith("🚨"))
        self.assertTrue(status_icon(-5.2).startswith("👀"))
        self.assertTrue(status_icon(-6.01).startswith("💤"))

    def test_collect_and_sort(self):
        ranks = {"AAA": "🥇 [HẠNG A1] SIÊU PULLBACK", "AAAUSDT": "🥇 [HẠNG A1] SIÊU PULLBACK",
                 "ZZZ": "🏆 [HẠNG S] VỤ NỔ VĨ MÔ"}
        pre = [{"symbol": "BBB", "tier": "Tier A", "delta_72h": 42.0},
               {"symbol": "CCC", "tier": "Tier B", "delta_72h": 40.0}]   # = 40 → loại
        prof = [{"symbol": "DDD", "tier": "💎 Tier S", "total_score": 80},
                {"symbol": "EEE", "tier": "Tier A-", "total_score": 70},  # A- loại
                {"symbol": "AAA", "tier": "Tier A", "total_score": 65}]
        cands = collect_candidates(ranks, pre, prof)
        self.assertEqual(set(cands), {"AAA", "BBB", "DDD"})
        self.assertEqual(len(cands["AAA"]), 2)

        triggers = {"AAA": 96.39, "BBB": 98.27, "DDD": 92.0}   # -3.61%, -1.73%, -8%
        def fake_builder(sym, live, rsi, oco):
            return {"live_price": 100.0, "btc_rsi_1h": rsi, "tick_size": 0.01, "oco_buy_limit_price": triggers[sym],
                    "supertrend_1d": triggers[sym] / 1.015, "supertrend_dir_1d": 1}
        rows = build_split_entry_rows(cands, None, None, 55.0, data_builder=fake_builder)
        self.assertEqual([r["symbol"] for r in rows], ["BBB", "AAA", "DDD"])
        print(render_split_entry_table(rows, 55.0))


    def test_collect_stream4_smart_money_flow(self):
        signals = [
            {"engine_source": "DC3", "symbol": "FLOW1/USDT", "money_flow": 2.8, "rr_ratio": 3.0},
            {"engine_source": "DC4", "symbol": "FLOW2/USDT", "money_flow": 2.1, "rr_ratio": 2.5},
            {"engine_source": "DC3", "symbol": "FAIL_VOL/USDT", "money_flow": 2.0, "rr_ratio": 4.0}, # mf not > 2.0
            {"engine_source": "DC4", "symbol": "FAIL_RR/USDT", "money_flow": 3.5, "rr_ratio": 2.4},  # rr not >= 2.5
            {"engine_source": "DC1", "symbol": "OTHER_ENG/USDT", "money_flow": 5.0, "rr_ratio": 5.0}, # not DC3/DC4
        ]
        cands = collect_candidates(signals_pool=signals)
        self.assertIn("FLOW1", cands)
        self.assertIn("FLOW2", cands)
        self.assertNotIn("FAIL_VOL", cands)
        self.assertNotIn("FAIL_RR", cands)
        self.assertNotIn("OTHER_ENG", cands)
        self.assertIn("Tiền Bạo Phát 2.8x + R:R 1:3.0", cands["FLOW1"])

    def test_table_v28_rendering_no_bold_rows(self):
        signals = [
            {"engine_source": "DC3", "symbol": "BTC/USDT", "money_flow": 3.2, "rr_ratio": 4.5},
        ]
        cands = collect_candidates(signals_pool=signals)
        def fake_builder(sym, live, rsi, oco):
            return {
                "live_price": 50000.0, "btc_rsi_1h": 50.0, "tick_size": 1.0,
                "bb_mid_4h": 47500.0, "supertrend_1d": 40000.0, "supertrend_dir_1d": 1,
            }
        rows = build_split_entry_rows(cands, None, None, 50.0, data_builder=fake_builder)
        tbl = render_split_entry_table(rows, 50.0)
        self.assertIn("v2.8", tbl)
        self.assertIn("24 Lưới", tbl)
        self.assertIn("Setup Grid & Thông Số Chi Tiết (Tự động Đòn bẩy)", tbl)
        self.assertIn("Isolated x5 | TP: +10% | SL: -30%", tbl)
        for line in tbl.splitlines():
            if line.startswith("|") and not line.startswith("| :---"):
                self.assertNotIn("**", line, "Data row must not contain bold '**'")


    def test_top_money_flow_from_summary_board_with_grid_preset(self):
        beamx_sig = {
            "symbol": "BEAMX/USDT",
            "engine_source": "DC4",
            "money_flow": 2.5,
            "rr_ratio": 6.7,
            "grid_tp2_line": "🎯 [GRID TP2 - BEAMX] 0.004486 - 0.005839 | 24L | Trig: 0.004866 | SL: 0.004282 | TP: 0.005839",
        }
        grid_presets = {}
        cands = collect_candidates(top_money_flow=[beamx_sig], grid_presets_out=grid_presets)
        self.assertIn("BEAMX", cands)
        self.assertIn("BEAMX", grid_presets)
        self.assertEqual(grid_presets["BEAMX"][0], 0.004486) # lower
        self.assertEqual(grid_presets["BEAMX"][1], 0.005839) # upper
        self.assertEqual(grid_presets["BEAMX"][2], 0.004866) # trig
        self.assertEqual(grid_presets["BEAMX"][3], 0.004282) # sl
        self.assertEqual(grid_presets["BEAMX"][4], 0.005839) # tp

        def fake_builder(sym, live, rsi, oco):
            return {
                "live_price": 0.0050, "btc_rsi_1h": 50.0, "tick_size": 0.000001,
                "bb_mid_4h": 0.004866, "supertrend_1d": 0.0040, "supertrend_dir_1d": 1,
            }
        rows = build_split_entry_rows(cands, None, None, 50.0, data_builder=fake_builder, grid_presets=grid_presets)
        tbl = render_split_entry_table(rows, 50.0)
        self.assertIn("BEAMX/USDT", tbl)
        self.assertIn("Dải L=0.004486 - U=0.005839 (24 Lưới)", tbl)
        self.assertIn("Isolated x5 | TP: +10% | SL: -30%", tbl)
        self.assertIn("Tiền Bạo Phát 2.5x + R:R 1:6.7", tbl)

    def test_bottom_fishing_leverage_x12(self):
        ranks = {"PEPE": "🥇 [HẠNG A1] SIÊU PULLBACK"}
        cands = collect_candidates(convergence_ranks=ranks)
        def fake_builder(sym, live, rsi, oco):
            return {
                "live_price": 0.000005, "btc_rsi_1h": 50.0, "tick_size": 0.00000001,
                "bb_mid_4h": 0.0000048, "supertrend_1d": 0.0000040, "supertrend_dir_1d": 1,
            }
        rows = build_split_entry_rows(cands, None, None, 50.0, data_builder=fake_builder)
        tbl = render_split_entry_table(rows, 50.0)
        self.assertIn("PEPE/USDT", tbl)
        self.assertIn("Isolated x12 | TP: +10% | SL: -30%", tbl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
