"""
Module: core/symbol_inspector.py
Mục đích: Tra cứu và tạo 2 bảng dữ liệu quét chuyên sâu cho từng mã theo yêu cầu:
  - Bảng 1: Tín hiệu kỹ thuật & Động cơ phiên mới nhất (Live/Latest Scan Signals)
  - Bảng 2: Phân rã lịch sử 4 phiên & Tính toán suy hao Aggressive Decay (4-Day Historical Decay)
"""

import os
import sqlite3
import pandas as pd
from typing import Tuple, List, Dict, Any, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "binac4_history.db")

def get_available_symbols(db_path: str = DEFAULT_DB_PATH) -> List[str]:
    """
    Lấy danh sách mã coin đã quét, ưu tiên các mã trong phiên mới nhất lên đầu,
    sau đó sắp xếp theo bảng chữ cái.
    """
    if not os.path.exists(db_path):
        return ["BERA", "INJ", "PYTH", "TIA", "RAD"]

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            # 1. Lấy ngày quét gần nhất
            cursor.execute("SELECT DISTINCT date FROM daily_metrics ORDER BY date DESC LIMIT 1")
            latest_date_row = cursor.fetchone()
            latest_date = latest_date_row[0] if latest_date_row else None

            latest_symbols = []
            if latest_date:
                cursor.execute("""
                    SELECT symbol FROM daily_metrics 
                    WHERE date = ? 
                    ORDER BY raw_base_score DESC, rebalance_score DESC
                """, (latest_date,))
                latest_symbols = [r[0] for r in cursor.fetchall() if r[0]]

            # 2. Lấy toàn bộ các mã còn lại trong DB
            cursor.execute("SELECT DISTINCT symbol FROM daily_metrics ORDER BY symbol ASC")
            all_symbols = [r[0] for r in cursor.fetchall() if r[0]]

            # Gộp và loại bỏ trùng lặp, giữ thứ tự ưu tiên
            seen = set()
            ordered = []
            for s in latest_symbols + all_symbols:
                s_clean = s.replace("/USDT", "").replace("USDT", "").strip().upper()
                if s_clean and s_clean not in seen:
                    seen.add(s_clean)
                    ordered.append(s_clean)

            return ordered if ordered else ["BERA", "INJ", "PYTH"]
    except Exception:
        return ["BERA", "INJ", "PYTH"]


def get_symbol_dossier(symbol: Optional[str] = None, db_path: str = DEFAULT_DB_PATH) -> Tuple[str, pd.DataFrame, pd.DataFrame]:
    """
    Tạo hồ sơ phân tích 2 bảng cho một mã cụ thể.
    Trả về: (summary_markdown, df_table1, df_table2)
    """
    if not symbol:
        empty_df1 = pd.DataFrame([{"Chỉ Số / Hạng Mục": "Thông báo", "Giá Trị": "Vui lòng chọn một mã", "Ghi Chú": ""}])
        empty_df2 = pd.DataFrame([{"Thành Phần Điểm": "Thông báo", "Giá Trị": "Vui lòng chọn một mã"}])
        return "⚠️ Vui lòng chọn một mã để xem hồ sơ chi tiết.", empty_df1, empty_df2

    clean_sym = str(symbol).replace("/USDT", "").replace("USDT", "").strip().upper()

    try:
        from core.historical_rater import HistoricalDataQuery
        hr = HistoricalDataQuery(db_path=db_path)
        profile = hr.query_14_day_profile(clean_sym)
    except Exception as e:
        profile = {
            "symbol": clean_sym,
            "total_score": 0.0,
            "tier": "Lỗi đọc dữ liệu",
            "action_recommendation": f"Lỗi: {e}",
            "multiplier": 1.0,
            "breakdown": {"foundation_40": 0.0, "momentum_40": 0.0, "cashflow_20": 0.0, "history_mong": [], "history_day": [], "history_tien": []}
        }

    # Sử dụng chuỗi 4 ngày chuẩn hóa từ query_14_day_profile (Single Source of Truth)
    recent_dates = profile.get("target_dates", [])
    if not recent_dates:
        ref_d = datetime.now().date()
        recent_dates = [(ref_d - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(4)]

    # Truy vấn thông tin chi tiết phiên gần nhất từ daily_metrics để hiển thị BẢNG 1
    latest_row = None
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM daily_metrics 
                    WHERE symbol = ? 
                    ORDER BY date DESC 
                    LIMIT 1
                """, (clean_sym,))
                latest_row = cursor.fetchone()
        except Exception:
            pass

    # =========================================================================
    # 🕒 Lấy mốc thời gian chi tiết (YYYY-MM-DD HH:MM:SS) cho từng ngày & từng mã
    # =========================================================================
    date_timestamps = {}
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT substr(timestamp, 1, 10) as d, MAX(timestamp) 
                    FROM signal_logs 
                    GROUP BY d 
                    ORDER BY d DESC 
                    LIMIT 10
                """)
                date_timestamps = dict(c.fetchall())
        except Exception:
            pass

    trust_db = "trust_score_history.db"
    if os.path.exists(trust_db):
        try:
            with sqlite3.connect(trust_db) as conn_t:
                c_t = conn_t.cursor()
                c_t.execute("""
                    SELECT substr(scan_time, 1, 10) as d, MAX(scan_time) 
                    FROM score_history 
                    GROUP BY d 
                    ORDER BY d DESC 
                    LIMIT 10
                """)
                for d, ts in c_t.fetchall():
                    if d not in date_timestamps or ts > date_timestamps[d]:
                        date_timestamps[d] = ts
        except Exception:
            pass

    target_live_date = recent_dates[0]
    latest_live_ts = date_timestamps.get(target_live_date, target_live_date)

    record_date = str(latest_row["date"]) if latest_row else "—"
    # Đồng bộ cờ is_expired với kết quả tính điểm của HistoricalDataQuery
    is_expired = not profile.get("is_live", False) or (record_date < target_live_date)

    # Lấy timestamp chính xác của mã đang xét
    sym_ts = None
    if os.path.exists(db_path) and record_date and record_date != "—":
        try:
            with sqlite3.connect(db_path) as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT MAX(timestamp) FROM signal_logs 
                    WHERE symbol = ? AND substr(timestamp, 1, 10) = ?
                """, (clean_sym, record_date))
                r = c.fetchone()
                if r and r[0]:
                    sym_ts = r[0]
        except Exception:
            pass

    if not sym_ts:
        sym_ts = date_timestamps.get(record_date, record_date)

    # =========================================================================
    # 📊 BẢNG 1: Tín hiệu & Thông số kỹ thuật phiên mới nhất (Latest Scan Signals)
    # Áp dụng cơ chế Data TTL: Nếu mã không có mặt trong phiên quét mới nhất -> Đã hết hạn (0đ)
    # =========================================================================
    table1_data = []
    if latest_row and not is_expired:
        lr = dict(latest_row)
        sc_st = lr.get("strict_3d_score", 0.0) or 0.0
        p_ma7 = lr.get("price_above_ma7_3d", 0)
        st_4h = lr.get("st_4h_green", 0)
        
        status_3d = f"{sc_st:.0f}đ"
        note_3d = "✅ Đạt chuẩn vĩ mô 3D" if sc_st >= 70 else ("🟡 Tiệm cận chuẩn 3D" if sc_st >= 50 else "⛔ Dưới chuẩn vĩ mô 3D")
        
        g_rank = lr.get("grid_rank", 999) or 999
        g_rank_str = f"#{g_rank}" if g_rank < 999 else "Chưa xếp hạng"
        
        pen_sum = (lr.get('penalty_level_3', 0) or 0)*15 + (lr.get('penalty_xaray', 0) or 0)*5 + (lr.get('penalty_fakeout', 0) or 0)*10
        pen_str = f"🔴 Trừ -{pen_sum}đ" if pen_sum > 0 else "🟢 Không dính phạt"

        table1_data = [
            {"Chỉ Số / Hạng Mục": "📅 Thời Điểm Ghi Nhận Dữ Liệu", "Giá Trị": str(sym_ts), "Ghi Chú": "Dữ liệu phiên mới nhất đang hoạt động"},
            {"Chỉ Số / Hạng Mục": "🛡️ Trạng Thái 3D Strict", "Giá Trị": status_3d, "Ghi Chú": note_3d},
            {"Chỉ Số / Hạng Mục": "🌊 Cấu Trúc Đa Khung (P>MA7_3D & ST_4H)", "Giá Trị": f"P>MA7: {'✅ Có' if p_ma7 else '❌ Không'} | ST_4H: {'🟢 Xanh' if st_4h else '🔴 Đỏ'}", "Ghi Chú": "Kiểm định chân sóng xu hướng trung hạn"},
            {"Chỉ Số / Hạng Mục": "📦 Động Cơ 1 (Darvas Grid)", "Giá Trị": str(lr.get("grid_status", "—") or "—"), "Ghi Chú": f"Thứ hạng Grid: {g_rank_str}"},
            {"Chỉ Số / Hạng Mục": "🎯 Động Cơ 2 (Pullback Sniper)", "Giá Trị": f"{float(lr.get('dc2_score', 0) or 0):.1f}đ", "Ghi Chú": "Xung lực đón điểm hồi quy (Hạn mức 10đ)"},
            {"Chỉ Số / Hạng Mục": "🚀 Động Cơ 3 (Breakout)", "Giá Trị": f"{float(lr.get('dc3_score', 0) or 0):.1f}đ", "Ghi Chú": "Xung lực nổ Volume bứt phá (Hạn mức 15đ)"},
            {"Chỉ Số / Hạng Mục": "🔥 Động Cơ 4 (Hot Trend)", "Giá Trị": f"{float(lr.get('dc4_score', 0) or 0):.1f}đ", "Ghi Chú": "Đà tăng nóng & phục hồi đáy 24h (Hạn mức 15đ)"},
            {"Chỉ Số / Hạng Mục": "🏓 Động Cơ 5 (Pingpong)", "Giá Trị": f"{float(lr.get('dc5_score', 0) or 0):.1f}đ", "Ghi Chú": "Sóng giật biên độ ngắn"},
            {"Chỉ Số / Hạng Mục": "🌱 Bảng 3A (Mỏ Vàng) / 3B (Sóng 3D)", "Giá Trị": f"3A: {'✅ Top' if lr.get('in_top_3a') else '—'} | 3B: {'✅ Top' if lr.get('in_top_3b') else '—'}", "Ghi Chú": "+5đ Móng nếu lọt Top 5 của 3A hoặc 3B"},
            {"Chỉ Số / Hạng Mục": "🏆 Hội Tụ Tối Ưu (Summary Board)", "Giá Trị": str(lr.get("optimal_tier", "—") or "—"), "Ghi Chú": "+10đ (Hạng S) hoặc +5đ (Hạng A/B)"},
            {"Chỉ Số / Hạng Mục": "💰 Dòng Tiền & Điểm Phạt", "Giá Trị": f"Dòng tiền: {'+10đ' if lr.get('money_flow_score') else '0đ'} | {pen_str}", "Ghi Chú": "Quyết định hệ số x1.2 thưởng hoặc x0.5 phạt"}
        ]
    else:
        # Đã hết hạn hoặc rớt top trong phiên mới nhất -> Đưa toàn bộ động cơ về 0đ để chống ảo giác
        old_note = f" (Phiên cũ: {sym_ts})" if sym_ts != "—" else ""
        table1_data = [
            {"Chỉ Số / Hạng Mục": "📅 Thời Điểm Ghi Nhận Dữ Liệu", "Giá Trị": f"⚠️ {sym_ts} (Đã hết hạn / Mất Live)", "Ghi Chú": f"Mã không lọt top trong phiên mới nhất [{latest_live_ts}]"},
            {"Chỉ Số / Hạng Mục": "🛡️ Trạng Thái 3D Strict", "Giá Trị": "0đ", "Ghi Chú": "⛔ Dưới chuẩn vĩ mô phiên hiện tại"},
            {"Chỉ Số / Hạng Mục": "🌊 Cấu Trúc Đa Khung (P>MA7_3D & ST_4H)", "Giá Trị": "P>MA7: ❌ Không | ST_4H: 🔴 Đỏ", "Ghi Chú": "Mất cấu trúc chân sóng"},
            {"Chỉ Số / Hạng Mục": "📦 Động Cơ 1 (Darvas Grid)", "Giá Trị": f"⛔ N/A - Rớt Top{old_note}", "Ghi Chú": "Không có trong top quét hiện tại"},
            {"Chỉ Số / Hạng Mục": "🎯 Động Cơ 2 (Pullback Sniper)", "Giá Trị": "0.0đ", "Ghi Chú": "0đ (Không có xung lực đón điểm)"},
            {"Chỉ Số / Hạng Mục": "🚀 Động Cơ 3 (Breakout)", "Giá Trị": "0.0đ", "Ghi Chú": "0đ (Không có nổ Volume)"},
            {"Chỉ Số / Hạng Mục": "🔥 Động Cơ 4 (Hot Trend)", "Giá Trị": "0.0đ", "Ghi Chú": "0đ (Không có sóng tăng nóng)"},
            {"Chỉ Số / Hạng Mục": "🏓 Động Cơ 5 (Pingpong)", "Giá Trị": "0.0đ", "Ghi Chú": "0đ (Không có sóng ngắn)"},
            {"Chỉ Số / Hạng Mục": "🌱 Bảng 3A (Mỏ Vàng) / 3B (Sóng 3D)", "Giá Trị": "3A: — | 3B: —", "Ghi Chú": "Không nằm trong Top 3A / 3B"},
            {"Chỉ Số / Hạng Mục": "🏆 Hội Tụ Tối Ưu (Summary Board)", "Giá Trị": "N/A (Chưa hội tụ)", "Ghi Chú": "0đ (Không lọt danh sách ưu tiên)"},
            {"Chỉ Số / Hạng Mục": "💰 Dòng Tiền & Điểm Phạt", "Giá Trị": "Dòng tiền: 0đ | 🟢 Không phạt", "Ghi Chú": "Không có dòng tiền bạo phát phiên hiện tại"}
        ]

    df_table1 = pd.DataFrame(table1_data)

    # =========================================================================
    # 📈 BẢNG 2: Phân rã lịch sử 4 ngày & Suy hao Aggressive Decay
    # =========================================================================
    weights = [0.50, 0.25, 0.15, 0.10]
    date_labels = ["T-0 (Mới nhất)", "T-1 (Hôm qua)", "T-2 (2 ngày trước)", "T-3 (3 ngày trước)"]

    h_mong = profile.get("breakdown", {}).get("history_mong", [])
    h_day = profile.get("breakdown", {}).get("history_day", [])
    h_tien = profile.get("breakdown", {}).get("history_tien", [])

    row_date = {"Thành Phần Điểm": "📅 Thời Điểm Quét"}
    row_weight = {"Thành Phần Điểm": "⚖️ Trọng Số Suy Hao"}
    row_mong = {"Thành Phần Điểm": "🧱 Điểm MÓNG (Tối đa 40đ)"}
    row_day = {"Thành Phần Điểm": "🚀 Điểm ĐẨY (Tối đa 40đ)"}
    row_tien = {"Thành Phần Điểm": "💰 Điểm TIỀN (Tối đa 20đ)"}
    row_raw_sum = {"Thành Phần Điểm": "∑ Tổng Thô Từng Ngày"}

    for i in range(min(4, len(recent_dates))):
        d_str = recent_dates[i]
        ts_str = date_timestamps.get(d_str, d_str)
        col_name = f"{date_labels[i]} [{d_str}]"
        row_date[col_name] = ts_str
        row_weight[col_name] = f"{int(weights[i]*100)}%"

        m_val = h_mong[i] if i < len(h_mong) else 0.0
        d_val = h_day[i] if i < len(h_day) else 0.0
        t_val = h_tien[i] if i < len(h_tien) else 0.0

        row_mong[col_name] = f"{m_val:.1f}đ"
        row_day[col_name] = f"{d_val:.1f}đ"
        row_tien[col_name] = f"{t_val:.1f}đ"
        row_raw_sum[col_name] = f"{(m_val + d_val + t_val):.1f}đ"

    # Cột tổng kết sau suy hao
    decay_mong = profile.get('breakdown', {}).get('foundation_40', 0.0)
    decay_day = profile.get('breakdown', {}).get('momentum_40', 0.0)
    decay_tien = profile.get('breakdown', {}).get('cashflow_20', 0.0)
    decay_sum = decay_mong + decay_day + decay_tien

    target_col = "🎯 BÌNH QUÂN SUY HAO"
    row_date[target_col] = f"{len(recent_dates[:4])} Ngày Gần Nhất"
    row_weight[target_col] = "100%"
    row_mong[target_col] = f"{decay_mong:.1f}đ"
    row_day[target_col] = f"{decay_day:.1f}đ"
    row_tien[target_col] = f"{decay_tien:.1f}đ"
    row_raw_sum[target_col] = f"{decay_sum:.1f}đ"

    table2_rows = [row_date, row_weight, row_mong, row_day, row_tien, row_raw_sum]
    df_table2 = pd.DataFrame(table2_rows)

    # =========================================================================
    # 📋 Thẻ tóm tắt tổng quan (Markdown Card)
    # =========================================================================
    total_score = profile.get("total_score", 0.0)
    tier = profile.get("tier", "—")
    action = profile.get("action_recommendation", "—")
    mult = profile.get("multiplier", 1.0)

    if mult > 1.0:
        mult_badge = f"🚀 **x{mult:.1f}** *(Thưởng dòng tiền mạnh)*"
    elif mult < 1.0:
        mult_badge = f"🔻 **x{mult:.1f}** *(Trừng phạt cạn dòng tiền)*"
    else:
        mult_badge = f"⚪ **x{mult:.1f}** *(Chuẩn)*"

    expired_alert = ""
    if is_expired:
        expired_alert = f"""
> ⚠️ **CẢNH BÁO TÍN HIỆU CŨ (HẾT HẠN):** Phiên quét mới nhất `[{latest_live_ts}]` không ghi nhận mã **{clean_sym}** (đã rớt top / mất tín hiệu Live). Điểm số BẢNG 1 được đưa về **0đ**, điểm tín nhiệm BẢNG 2 chỉ còn giá trị suy hao từ dữ liệu cũ lúc `{sym_ts}`.
"""

    summary_md = f"""
### 🔎 Hồ Sơ Chi Tiết: **{clean_sym}**
{expired_alert}
* 🏆 **Tổng Điểm Tín Nhiệm:** <span class="score-badge" style="font-size: 20px; font-weight: bold; color: #ff9800;">{total_score:.1f} / 100đ</span>
* 🏷️ **Phân Hạng:** **{tier}**
* 🎯 **Khuyến Nghị:** **{action}**
* ⚡ **Hệ Số Dòng Tiền (Multiplier):** {mult_badge} *(Điểm Tiền bình quân: `{decay_tien:.1f}đ`)*
* 📐 **Công Thức Chốt Điểm:** `({decay_mong:.1f} Móng + {decay_day:.1f} Đẩy + {decay_tien:.1f} Tiền) × {mult:.1f} = {total_score:.1f}đ`
"""

    return summary_md, df_table1, df_table2
