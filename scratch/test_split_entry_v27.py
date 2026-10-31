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


if __name__ == "__main__":
    unittest.main(verbosity=2)
