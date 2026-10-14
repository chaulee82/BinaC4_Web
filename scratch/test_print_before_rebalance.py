import sys
import os
import io
import contextlib
import pandas as pd

sys.path.insert(0, r"c:\DData\Source\wwwScr\BinaC4")
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from core.coin_filter import print_final_tables
from core.historical_rater import print_14_day_summary_board

def test_order():
    df = pd.DataFrame([
        {"Symbol": "ZK", "is_safe": True, "TỔNG": 78.5, "Trạng Thái": "Uptrend", "Phân Loại Grid": "Tích lũy",
         "Chế Độ": "Lưới", "Cảnh Báo": "-", "Đ.Nến15M": 80, "Đ.Râu24H": 80, "Đ.Entry": 80,
         "Đ.RSI": 70, "RSI1H": 55, "Giá Live": "0.15", "Giá Mua Limit": "0.14", "Cần Giảm": 2.5,
         "Vol24H(M)": 10.5, "Vola24H%": 5.2}
    ])
    
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        def _hook():
            print("<<BẢNG TỔNG KẾT TỐI ƯU (SUMMARY_BOARD)>>")
            print_14_day_summary_board(
                db_path="data/binac4_history.db",
                df_summary=df,
                early_list=[],
                super_wave_results=[],
                top_n=5
            )

        print_final_tables([], df, "2026-10-14 16:00:00", before_rebalance_hook=_hook)

    out = buf.getvalue()
    
    i_b3a = out.find("BẢNG 3A: MỎ VÀNG NGỦ SAY")
    i_summary = out.find("<<BẢNG TỔNG KẾT TỐI ƯU (SUMMARY_BOARD)>>")
    i_14day = out.find("BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY")
    i_reb = out.find("🏆 BẢNG CHẤM ĐIỂM REBALANCE / SPOT GRID")

    print(f"Index B3A: {i_b3a}")
    print(f"Index Summary Board: {i_summary}")
    print(f"Index 14-Day Table: {i_14day}")
    print(f"Index Rebalance: {i_reb}")

    assert 0 <= i_b3a < i_summary < i_14day < i_reb, "Thứ tự in bảng không chính xác!"
    print("\n✅ KIỂM TRA THỨ TỰ THÀNH CÔNG: BẢNG 14 NGÀY IN NGAY TRƯỚC BẢNG REBALANCE!")

if __name__ == "__main__":
    test_order()
