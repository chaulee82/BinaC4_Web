import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from views.summary_board import SignalRecord, classify_pool

ok_line = "🎯 [GRID TP2 - AAA] 1 - 2 | 10L | Trig: 1.5 | SL: 0.9 | 🛡️ [3D STRICT ✅]"
bad_line = "🎯 [GRID TP2 - BBB] 1 - 2 | 10L | Trig: 1.5 | SL: 0.9 | ⛔ [3D STRICT]"
w_ok = "🦅 [WIDE GRID 3D - AAA] 1 - 2 | 28L | Trig: 1.5 | SL: 0.9 | 🛡️ [3D STRICT ✅]"
w_bad = "🦅 [WIDE GRID 3D - BBB] 1 - 2 | 28L | Trig: 1.5 | SL: 0.9 | ⛔ [3D STRICT]"
pool = [SignalRecord("AAA", "DC4", grid_tp2_line=ok_line, wide_grid_line=w_ok),
        SignalRecord("BBB", "DC4", grid_tp2_line=bad_line, wide_grid_line=w_bad)]
b = classify_pool(pool)
tp2 = {s.symbol for s in b["tp2_primary"] + b["tp2_backup"]}
wide = {s.symbol for s in b["wide_primary"] + b["wide_backup"]}
ok = tp2 == {"AAA"} and wide == {"AAA"}
print(f"[{'OK ' if ok else 'FAIL'}] Bảng Tổng Kết: TP2={tp2} WIDE={wide} (BBB ⛔ phải bị loại)")
sys.exit(0 if ok else 1)
