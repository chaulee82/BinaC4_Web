import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from views.summary_board import print_strict_3d_rejections

T1 = "Vi phạm Tầng 1 - Mất Supertrend 3D (đỏ)"
T2 = "Vi phạm Tầng 2 - MA 3D dốc xuống (MA7,MA25)"
T3a = "Vi phạm Tầng 3 - Không có thân cờ nổ Vol 3D"
T3b = "Vi phạm Tầng 3 - Vol chưa cạn (0.91x thân cờ)"
T4 = "Vi phạm Tầng 4 - Dưới nửa trên BOLL 3D (%B 0.31)"

def r(reasons, layers):
    return {"engines": {"DC2"}, "kinds": {"GRID TP2"}, "reasons": reasons, "failed_layers": layers}

rej = {
    "ARB": r([T1, T2, T3a, T4], [1, 2, 3, 4]), "OP": r([T1, T2, T3a, T4], [1, 2, 3, 4]),
    "UNI": r([T1, T2, T3a], [1, 2, 3]), "TRUMP": r([T1, T3a, T4], [1, 3, 4]),
    "TON": r([T2, T3a], [2, 3]), "SAND": r([T2], [2]),
    "NMR": r([T3a], [3]), "ORCA": r([T3b], [3]), "GTC": r([T3b.replace("0.91", "0.85")], [3]),
    "API3": r([T3a, T4], [3, 4]), "PARTI": r([T4], [4]),
    "NEWCOIN": r(["Thiếu dữ liệu nến 3D"], []),
}
print_strict_3d_rejections(rej)
