import sys
sys.path.insert(0, r"c:\DData\Source\wwwScr\BinaC4")
sys.stdout.reconfigure(encoding='utf-8')
import io, contextlib
import pandas as pd
import main  # noqa: F401  (đảm bảo import main OK)
from core.coin_filter import print_final_tables

df = pd.DataFrame([{"Symbol": "ZRO", "is_safe": True, "TỔNG": 79.1}])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try:
        print_final_tables([], df, "now", before_rebalance_hook=lambda: print("<<SUMMARY_BOARD>>"))
    except Exception as e:
        print(f"(rebalance render stopped: {e})")
out = buf.getvalue()
i_hook = out.find("<<SUMMARY_BOARD>>")
i_reb = out.find("BẢNG CHẤM ĐIỂM REBALANCE")
i_b3 = out.find("BẢNG 3: MỎ VÀNG")
print("B3:", i_b3, "HOOK:", i_hook, "REBALANCE:", i_reb)
assert 0 <= i_b3 < i_hook < i_reb, "Thứ tự sai"
print("ORDER OK")
