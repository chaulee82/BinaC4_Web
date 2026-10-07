import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from views.summary_board import SignalRecord, classify_pool, sort_grid_tp2, print_formatted_table

ok_line = "🎯 [GRID TP2 - AAA] 1 - 2 | 10L | Trig: 1.5 | SL: 0.9 | 🛡️ [3D STRICT ✅] 81đ (✗T3)"
top_line = "🎯 [GRID TP2 - TOP] 1 - 2 | 10L | Trig: 1.5 | SL: 0.9 | 🛡️ [3D STRICT ✅] 100đ"
watch_line = "🎯 [GRID TP2 - WWW] 1 - 2 | 10L | Trig: 1.5 | SL: 0.9 | 🟡 [3D STRICT ⚠️] 58đ (✗T3,T4)"
bad_line = "🎯 [GRID TP2 - BBB] 1 - 2 | 10L | Trig: 1.5 | SL: 0.9 | ⛔ [3D STRICT] 25đ"
w_ok = "🦅 [WIDE GRID 3D - AAA] 1 - 2 | 28L | Trig: 1.5 | SL: 0.9 | 🛡️ [3D STRICT ✅] 81đ (✗T3)"
w_watch = "🦅 [WIDE GRID 3D - WWW] 1 - 2 | 28L | Trig: 1.5 | SL: 0.9 | 🟡 [3D STRICT ⚠️] 58đ (✗T3,T4)"
w_bad = "🦅 [WIDE GRID 3D - BBB] 1 - 2 | 28L | Trig: 1.5 | SL: 0.9 | ⛔ [3D STRICT] 25đ"
pool = [SignalRecord("AAA", "DC4", money_flow=9.0, grid_tp2_line=ok_line, wide_grid_line=w_ok),
        SignalRecord("TOP", "DC3", money_flow=1.0, grid_tp2_line=top_line),
        SignalRecord("WWW", "DC2", grid_tp2_line=watch_line, wide_grid_line=w_watch),
        SignalRecord("BBB", "DC4", grid_tp2_line=bad_line, wide_grid_line=w_bad)]
b = classify_pool(pool)
results = []

# 1. ⛔ loại hẳn, 🟡 chỉ vào dự phòng
tp2_p = {s.symbol for s in b["tp2_primary"]}
tp2_b = {s.symbol for s in b["tp2_backup"]}
wide_all = {s.symbol for s in b["wide_primary"] + b["wide_backup"]}
wide_p = {s.symbol for s in b["wide_primary"]}
ok = tp2_p == {"AAA", "TOP"} and tp2_b == {"WWW"} and wide_all == {"AAA", "WWW"} and "WWW" not in wide_p
print(f"[{'OK ' if ok else 'FAIL'}] Phân loại: TP2 primary={tp2_p} backup={tp2_b} | WIDE={wide_all} (BBB ⛔ bị loại, WWW 🟡 → dự phòng)")
results.append(ok)

# 2. Điểm Strict 3D là ưu tiên số 1 (TOP 100đ vượt AAA 81đ dù tiền bạo phát thấp hơn)
order = [s.symbol for s in sort_grid_tp2(b["tp2_primary"])]
ok = order == ["TOP", "AAA"]
print(f"[{'OK ' if ok else 'FAIL'}] Xếp hạng TP2 theo Điểm Strict 3D: {order}")
results.append(ok)

print_formatted_table(sort_grid_tp2(b["tp2_primary"]) + b["tp2_backup"], "GRID_TP2", {"WWW"})
sys.exit(0 if all(results) else 1)
