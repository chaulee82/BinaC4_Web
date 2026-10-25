"""
Module: macro_grid_darvas.py
Dự án: CN4-Platform
Mục đích: Động cơ 1 — Bộ Giáp Lưới Kép Darvas (Bê Tông Cốt Thép)
Triết lý: Đo độ cứng Móng Bê Tông, không tìm đà tăng.

BƯỚC 0 — CỔNG "CÓ HỘP DARVAS HAY KHÔNG?" (trượt → 0 điểm, TỪ CHỐI):
  • Chỉ dùng nến 4H ĐÃ ĐÓNG (bỏ nến đang chạy) để dựng hộp.
  • Trần = Close cao nhất; mốc trần = lần ĐẦU giá chạm vùng trần (±1.5%), phải cũ ≥ 12 nến (48h).
  • Sàn  = Low thấp nhất HÌNH THÀNH SAU trần; mốc sàn phải cũ ≥ 6 nến (24h) không bị thủng mới.
  • Hộp tồn tại ≥ 30 nến (5 ngày) tính từ mốc trần.
  • Biên độ 4% ≤ (Trần − Sàn)/Sàn ≤ 25%  (hẹp quá → phí ăn lưới; rộng quá → trend/bơm-xả).
  • Giá dao động qua lại giữa hộp ≥ 2 lần cắt đường giữa (loại kiểu xả trượt dài một chiều).
  • Giá hiện tại phải nằm TRONG hộp (vượt trần → breakout, thủng sàn → phân phối).

Thang điểm 100 điểm (5 tiêu chí định lượng, chỉ chấm khi đã qua cổng hộp):
  C1 (25đ) - Tần Suất Đỡ Đáy  : Đáy test nhiều lần mà không thủng → Lực kê của tạo lập uy tín
  C2 (25đ) - Cạn Cung Tại Nền  : Vol TB nến đỏ ở nửa dưới hộp so với Vol TRUNG VỊ nến xanh trong hộp
  C3 (15đ) - Đồng Thuận Vĩ Mô  : MA99 1D đi ngang/ngóc lên + giá trên MA99 → Không chống trend
  C4 (15đ) - Biên Độ Ổn Định   : ATR 14 đang thu hẹp hoặc flat → Bình yên, không sắp bão
  C5 (20đ) - Độ Chặt Của Hộp   : Biên độ hộp càng hẹp → tích lũy càng chặt

Kill-Switch tự động (bất kể điểm tổng cao đến đâu):
  KS-1: Supertrend 1D DOWNTREND  → REJECT (Gãy cấu trúc vĩ mô)
  KS-2: Marubozu đỏ 4H + Vol>3×  → REJECT (Tay to tháo chạy — Black Swan)
  KS-3: Close xuyên đáy Darvas    → REJECT (Chuyển sang phân phối)
"""

import logging
import pandas as pd
import numpy as np
from core.indicator_engine import IndicatorEngine

logger = logging.getLogger("CN4.MacroGridDarvas")


class MacroGridDarvas:
    """Bộ máy quét cấu trúc tích lũy Darvas và chấm điểm độ bền móng."""

    # ── Hằng số cấu hình ──────────────────────────────────────────────────────
    DARVAS_LOOKBACK_4H   = 90      # ~15 ngày dữ liệu 4H để dựng hộp Darvas
    DARVAS_1D_LIMIT      = 120     # Số nến 1D để tính MA99 + Supertrend
    FLOOR_TEST_ZONE_PCT  = 0.02    # ±2% mép dưới hộp để đếm lần test đáy
    # ── Cổng xác nhận Hộp Darvas ──
    BOX_MIN_CANDLES      = 60      # Tối thiểu số nến đã đóng để xét hộp (mã mới list → bỏ)
    BOX_EDGE_TOL_PCT     = 0.015   # Dung sai ±1.5% khi xác định lần chạm trần/sàn
    CEIL_CONFIRM_BARS    = 12      # Trần phải đứng vững ≥ 12 nến 4H (48h)
    FLOOR_CONFIRM_BARS   = 6       # Sàn phải đứng vững ≥ 6 nến 4H (24h)
    BOX_MIN_BARS         = 30      # Hộp tồn tại ≥ 30 nến 4H (5 ngày)
    BOX_MIN_AMP          = 0.04    # Biên độ hộp tối thiểu 4%
    BOX_MAX_AMP          = 0.25    # Biên độ hộp tối đa 25%
    BOX_MIN_MID_CROSSES  = 2       # Số lần Close cắt đường giữa hộp tối thiểu
    MA99_SLOPE_MAX_PCT   = 0.01    # Độ dốc MA99 ≤ ±1% → Đi ngang
    ATR_PERIOD           = 14      # Chu kỳ ATR
    ATR_SQUEEZE_COMPARE  = 7       # So sánh ATR vs trung bình 7 phiên trước
    MARUBOZU_BODY_PCT    = 0.80    # Thân nến Marubozu chiếm ≥ 80% tổng độ dài
    MARUBOZU_VOL_MULT    = 3.0     # + Vol > 3× MA → Black Swan
    MIN_SCORE_FOR_GRID   = 60      # Ngưỡng điểm tối thiểu để tính thông số Grid
    MAX_ATR_PCT_OF_PRICE = 0.12    # [BUGFIX] ATR dùng dựng lưới không vượt 12% thị giá
    MAX_GRIDS            = 35      # [BUGFIX] Trần số lưới mỗi tầng

    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()
        self.engine   = IndicatorEngine()

    # =========================================================================
    # BƯỚC 0: DỰNG HỘP DARVAS (Nội bộ)
    # =========================================================================
    def _build_darvas_box(self, df: pd.DataFrame) -> dict:
        """
        Dựng & XÁC NHẬN Hộp Darvas từ nến 4H ĐÃ ĐÓNG (~90 nến = 15 ngày).
        - Trần: Close cao nhất (tránh nhiễu râu nến); mốc = lần đầu chạm vùng trần (±tol)
        - Sàn:  Low thấp nhất hình thành SAU mốc trần; mốc = lần đầu chạm vùng sàn (±tol)

        Trả về dict luôn có khóa "valid" (bool) và "reason" (str).
        Khi valid=True: floor, ceiling, mid, amplitude, top_idx, df_box (đoạn nến thuộc hộp).
        """
        df = df.reset_index(drop=True)
        n = len(df)
        if n < self.BOX_MIN_CANDLES:
            return {"valid": False, "reason": f"Thiếu dữ liệu ({n} < {self.BOX_MIN_CANDLES} nến 4H)"}

        tol     = self.BOX_EDGE_TOL_PCT
        closes  = df['close'].to_numpy(dtype=float)
        lows    = df['low'].to_numpy(dtype=float)
        ceiling = float(closes.max())

        # Mốc trần: lần ĐẦU Close chạm vùng trần → các lần sau chỉ là test lại trần, không reset hộp
        top_idx = int(np.argmax(closes >= ceiling * (1.0 - tol)))
        bars_since_top = n - 1 - top_idx
        if bars_since_top < self.CEIL_CONFIRM_BARS:
            return {"valid": False,
                    "reason": f"Trần chưa xác nhận (đang tạo đỉnh mới, {bars_since_top}/{self.CEIL_CONFIRM_BARS} nến)"}
        if bars_since_top + 1 < self.BOX_MIN_BARS:
            return {"valid": False,
                    "reason": f"Hộp quá non ({bars_since_top + 1}/{self.BOX_MIN_BARS} nến từ mốc trần)"}

        df_box   = df.iloc[top_idx:].reset_index(drop=True)
        box_lows = lows[top_idx:]
        floor    = float(box_lows.min())
        if floor <= 0 or ceiling <= floor:
            return {"valid": False, "reason": f"Hộp bất thường (floor={floor}, ceiling={ceiling})"}

        # Mốc sàn: lần ĐẦU Low chạm vùng sàn sau mốc trần — phải đứng vững đủ lâu
        floor_idx = int(np.argmax(box_lows <= floor * (1.0 + tol)))
        bars_since_floor = len(box_lows) - 1 - floor_idx
        if bars_since_floor < self.FLOOR_CONFIRM_BARS:
            return {"valid": False,
                    "reason": f"Sàn chưa xác nhận (vừa tạo đáy mới, {bars_since_floor}/{self.FLOOR_CONFIRM_BARS} nến)"}

        amplitude = (ceiling - floor) / floor
        if amplitude < self.BOX_MIN_AMP:
            return {"valid": False,
                    "reason": f"Hộp quá hẹp ({amplitude*100:.1f}% < {self.BOX_MIN_AMP*100:.0f}%) — phí ăn hết lưới"}
        if amplitude > self.BOX_MAX_AMP:
            return {"valid": False,
                    "reason": f"Biên độ quá rộng ({amplitude*100:.1f}% > {self.BOX_MAX_AMP*100:.0f}%) — trend/bơm-xả, không phải tích lũy"}

        # Giá phải dao động qua lại trong hộp, không trượt một chiều
        mid   = (ceiling + floor) / 2.0
        side  = np.sign(closes[top_idx:] - mid)
        side  = side[side != 0]
        mid_crosses = int(np.count_nonzero(np.diff(side))) if len(side) > 1 else 0
        if mid_crosses < self.BOX_MIN_MID_CROSSES:
            return {"valid": False,
                    "reason": f"Giá không dao động trong hộp ({mid_crosses} lần cắt giữa hộp) — xu hướng một chiều"}

        return {
            "valid":       True,
            "reason":      "✅ Hộp Darvas đã xác nhận",
            "floor":       floor,
            "ceiling":     ceiling,
            "mid":         mid,
            "amplitude":   amplitude,
            "top_idx":     top_idx,
            "box_bars":    len(df_box),
            "mid_crosses": mid_crosses,
            "df_box":      df_box,
        }

    # =========================================================================
    # C1 — TẦN SUẤT ĐỠ ĐÁY (30 ĐIỂM)
    # =========================================================================
    def _score_c1_floor_testing(self, df: pd.DataFrame, box: dict) -> dict:
        """
        Đếm số lần giá nhúng vào vùng ±2% mép dưới Hộp Darvas nhưng
        đóng cửa bên trong hộp (rút chân thành công), với khoảng cách tối thiểu
        12 nến (48h) giữa các lần test hợp lệ.

        Thang điểm:
          ≥ 3 lần → 25đ  (Sàn bê tông cốt thép)
            2 lần → 17đ  (Sàn bê tông vững)
            1 lần →  8đ  (Sàn cơ bản — cần thêm xác nhận)
            0 lần →  0đ  (Chưa định hình đáy)
        """
        floor   = box["floor"]
        ceiling = box["ceiling"]
        test_zone_upper = floor * (1.0 + self.FLOOR_TEST_ZONE_PCT)
        MIN_TEST_SPACING = 12

        mask = (
            (df['low']   <= test_zone_upper) &
            (df['close'] >  test_zone_upper) &
            (df['close'] <  ceiling)
        )
        
        test_indices = np.where(mask)[0]
        test_count = 0
        last_test_idx = -999

        for idx in test_indices:
            if idx - last_test_idx >= MIN_TEST_SPACING:
                test_count += 1
                last_test_idx = idx

        if test_count >= 3:
            score  = 25
            status = f"🧱 Sàn bê tông cốt thép ({test_count} lần test vững không thủng)"
        elif test_count == 2:
            score  = 17
            status = f"🧱 Sàn bê tông vững ({test_count} lần test thành công)"
        elif test_count == 1:
            score  = 8
            status = f"⚠️ Sàn cơ bản (1 lần test — cần xác nhận thêm)"
        else:
            score  = 0
            status = "⛔ Chưa định hình đáy rõ ràng (Rủi ro thủng nền)"

        return {"score": score, "test_count": test_count, "status": status,
                "floor_price": floor}

    # =========================================================================
    # C2 — CẠN CUNG TẠI NỀN (30 ĐIỂM)
    # =========================================================================
    def _score_c2_volume_dryup(self, df: pd.DataFrame, box: dict) -> dict:
        """
        Khi giá điều chỉnh về nửa dưới Hộp Darvas, Vol TB nến đỏ phải nhỏ hơn
        Vol TRUNG VỊ của nến xanh trong hộp (trung vị → miễn nhiễm nến bơm đột biến).
        `df` là đoạn nến thuộc hộp (sau mốc trần).

        Thang điểm:
          Vol đỏ < 60%  Trung vị Vol Xanh → 25đ  (Phe bán hoàn toàn kiệt sức)
          Vol đỏ < 80%  Trung vị Vol Xanh → 17đ  (Cung đang cạn)
          Vol đỏ < 100% Trung vị Vol Xanh →  8đ  (Tín hiệu yếu)
          Vol đỏ ≥ 100% Trung vị Vol Xanh →  0đ  (Phe bán vẫn mạnh — Nguy hiểm)
          Giá chưa về nửa dưới hộp        →  0đ  (Nền chưa được kiểm chứng)
        """
        floor = box["floor"]
        mid   = box["mid"]

        green_candles = df[df['close'] > df['open']]
        med_green_vol = float(green_candles['volume'].median()) if not green_candles.empty else 0.0

        if med_green_vol <= 0 or np.isnan(med_green_vol):
            return {"score": 0, "status": "⛔ Không có nến xanh hợp lệ để đối chiếu Volume"}

        lower_half_red = df[
            (df['close'] >= floor) &
            (df['close'] <= mid)   &
            (df['close'] <  df['open'])
        ]

        if lower_half_red.empty:
            return {
                "score":  0,
                "status": "⚠️ Chưa có dữ liệu test vùng nền (giá chưa về nửa dưới hộp)"
            }

        ratio = float(lower_half_red['volume'].mean()) / med_green_vol

        if ratio < 0.60:
            score  = 25
            status = f"💧 Cung cạn kiệt hoàn toàn (Vol đỏ chỉ {ratio*100:.0f}% Trung vị Vol Xanh)"
        elif ratio < 0.80:
            score  = 17
            status = f"💧 Cung đang cạn tại nền (Vol đỏ = {ratio*100:.0f}% Trung vị Vol Xanh)"
        elif ratio < 1.00:
            score  = 8
            status = f"⚠️ Tín hiệu cạn cung yếu (Vol đỏ = {ratio*100:.0f}% Trung vị Vol Xanh)"
        else:
            score  = 0
            status = f"⛔ Phe bán vẫn mạnh (Vol đỏ = {ratio*100:.0f}% Trung vị Vol Xanh — Nguy hiểm)"

        return {"score": score, "vol_ratio": round(ratio, 3), "status": status}

    # =========================================================================
    # C3 — ĐỒNG THUẬN VĨ MÔ — MA99 1D (20 ĐIỂM)
    # =========================================================================
    def _score_c3_macro_alignment(self, df_1d: pd.DataFrame, current_price: float) -> dict:
        """
        MA99 của khung 1D đang đi ngang (slope ≤ ±1%) hoặc ngóc lên.
        Giá hiện tại nằm trên MA99.

        Thang điểm:
          MA99 tăng  + giá trên MA99 → 15đ
          MA99 ngang + giá trên MA99 → 11đ
          MA99 ngang + giá dưới MA99 →  4đ
          MA99 giảm                  →  0đ (Downtrend vĩ mô — cấm lưới)
        """
        if len(df_1d) < 100:
            return {"score": 4, "status": "⚠️ Thiếu dữ liệu 1D để tính MA99"}

        ma99_series  = self.engine.get_ma(df_1d, 99)
        ma99_current = float(ma99_series.iloc[-1])
        # So sánh MA99 hiện tại vs cách đây 10 phiên 1D (~2 tuần)
        ma99_past    = float(ma99_series.iloc[-11]) if len(ma99_series) >= 11 else ma99_current
        slope_pct    = (ma99_current - ma99_past) / ma99_past if ma99_past > 0 else 0.0
        above_ma99   = current_price >= ma99_current

        if slope_pct > self.MA99_SLOPE_MAX_PCT and above_ma99:
            score  = 15
            status = f"📈 MA99 1D ngóc lên (+{slope_pct*100:.2f}%) & giá trên MA99 → Lý tưởng"
        elif abs(slope_pct) <= self.MA99_SLOPE_MAX_PCT and above_ma99:
            score  = 11
            status = f"➡️ MA99 1D đi ngang ({slope_pct*100:+.2f}%) & giá trên MA99 → Ổn định"
        elif abs(slope_pct) <= self.MA99_SLOPE_MAX_PCT and not above_ma99:
            score  = 4
            status = f"⚠️ MA99 1D đi ngang nhưng giá dưới MA99 → Trọng lực kéo xuống"
        else:
            score  = 0
            status = f"⛔ MA99 1D cắm đầu ({slope_pct*100:.2f}%) → Downtrend vĩ mô"

        return {
            "score": score, "ma99": ma99_current,
            "slope_pct": slope_pct, "above_ma99": above_ma99, "status": status
        }

    # =========================================================================
    # C4 — BIÊN ĐỘ ỔN ĐỊNH — ATR SQUEEZE (20 ĐIỂM)
    # =========================================================================
    def _score_c4_atr_squeeze(self, df: pd.DataFrame) -> dict:
        """
        ATR 14 phiên đang thu hẹp (so với trung bình 7 phiên trước).

        Thang điểm:
          ATR giảm > 20%   → 15đ  (Bão đã tan, sóng lặng)
          ATR giảm 10-20%  → 11đ  (Biến động đang dịu)
          ATR giảm 0-10%   →  8đ  (Ổn định nhẹ)
          ATR tăng 0-15%   →  4đ  (Bão đang đến gần)
          ATR tăng > 15%   →  0đ  (Bão đang nổi — cấm lưới)
        """
        min_len = self.ATR_PERIOD + self.ATR_SQUEEZE_COMPARE + 2
        if len(df) < min_len:
            return {"score": 4, "status": "⚠️ Thiếu dữ liệu để tính ATR Squeeze"}

        atr_series   = self.engine.get_atr(df, self.ATR_PERIOD)
        atr_now      = float(atr_series.iloc[-1])
        atr_past_avg = float(atr_series.iloc[-(self.ATR_SQUEEZE_COMPARE + 1):-1].mean())

        if atr_past_avg <= 0 or pd.isna(atr_past_avg):
            return {"score": 4, "status": "⚠️ ATR quá nhỏ, không đánh giá được"}

        change_pct = (atr_now - atr_past_avg) / atr_past_avg  # Âm = ATR thu hẹp = tốt

        if change_pct < -0.20:
            score  = 15
            status = f"🌅 ATR thu hẹp mạnh ({change_pct*100:+.1f}%) → Sóng lặng, vào lưới tuyệt vời"
        elif change_pct < -0.10:
            score  = 11
            status = f"😌 ATR đang dịu ({change_pct*100:+.1f}%) → Biến động giảm dần"
        elif change_pct < 0:
            score  = 8
            status = f"🔔 ATR ổn định nhẹ ({change_pct*100:+.1f}%)"
        elif change_pct <= 0.15:
            score  = 4
            status = f"⚠️ ATR đang nở ({change_pct*100:+.1f}%) → Bão đang đến gần"
        else:
            score  = 0
            status = f"⛔ ATR bùng nổ (+{change_pct*100:.1f}%) → Bão đang nổi — Cấm lưới"

        return {
            "score": score, "atr_now": atr_now,
            "atr_past": atr_past_avg, "change_pct": change_pct, "status": status
        }

    # =========================================================================
    # C5 — ĐỘ CHẶT CỦA HỘP (20 ĐIỂM)
    # =========================================================================
    def _score_c5_box_tightness(self, box: dict) -> dict:
        """
        Biên độ hộp (đã qua cổng 4%–25%) càng hẹp → tích lũy càng chặt.

        Thang điểm:
          4–6%   → 10đ  (Quá mỏng — lãi mỗi ô lưới sát phí)
          6–12%  → 20đ  (Nén chặt — vùng tối ưu cho lưới)
          12–16% → 15đ  (Khá chặt)
          16–20% → 10đ  (Trung bình)
          20–25% →  5đ  (Lỏng — sát ngưỡng loại)
        """
        amp = box["amplitude"]
        if amp < 0.06:
            score, label = 10, "⚠️ Quá mỏng — lãi mỗi ô lưới sát phí"
        elif amp <= 0.12:
            score, label = 20, "🎯 Nén chặt — vùng tối ưu cho lưới"
        elif amp <= 0.16:
            score, label = 15, "✅ Khá chặt"
        elif amp <= 0.20:
            score, label = 10, "⚠️ Trung bình"
        else:
            score, label = 5, "⚠️ Lỏng — sát ngưỡng loại"
        return {"score": score, "status": f"{label} (biên độ {amp*100:.1f}%)"}

    # =========================================================================
    # KILL-SWITCH — CẦU DAO TỰ ĐỘNG (Bất kể điểm tổng)
    # =========================================================================
    def _check_kill_switches(
        self,
        df_4h:         pd.DataFrame,
        df_1d:         pd.DataFrame,
        box:           dict,
        current_price: float,
    ) -> dict:
        """
        3 Cờ Đỏ ngắt lưới tự động — ưu tiên tuyệt đối trước mọi tiêu chí điểm.
        """
        floor = box.get("floor", 0)

        # KS-1: Supertrend 1D thủng ─────────────────────────────────────────
        if len(df_1d) >= 12:
            st_1d = self.engine.get_supertrend(df_1d, period=10, multiplier=3.0)
            if st_1d['direction'] == -1:
                return {
                    "triggered": True,
                    "reason":    "⛔ KS-1: Supertrend 1D DOWNTREND — Gãy cấu trúc vĩ mô"
                }

        # KS-2: Black Swan — Marubozu đỏ 4H + Vol > 3× MA ─────────────────
        if len(df_4h) >= 21:
            last_4h   = df_4h.iloc[-1]
            total_rng = last_4h['high'] - last_4h['low']
            body      = abs(last_4h['close'] - last_4h['open'])
            is_red    = last_4h['close'] < last_4h['open']
            is_marubozu = (
                total_rng > 0 and
                (body / total_rng) >= self.MARUBOZU_BODY_PCT and
                is_red
            )
            ma20_vol = float(df_4h['volume'].tail(21).iloc[:-1].mean())
            if is_marubozu and ma20_vol > 0 and last_4h['volume'] > ma20_vol * self.MARUBOZU_VOL_MULT:
                vol_x = last_4h['volume'] / ma20_vol
                return {
                    "triggered": True,
                    "reason":    f"⛔ KS-2: Marubozu đỏ 4H + Vol {vol_x:.1f}× MA — Tay to tháo chạy (Black Swan)"
                }

        # KS-3: Close xuyên đáy Hộp Darvas ──────────────────────────────────
        # (Hộp dựng từ nến ĐÃ ĐÓNG, giá hiện tại là nến đang chạy → KS-3 giờ mới thực sự kích hoạt được)
        if floor > 0 and current_price < floor:
            pct_below = (floor - current_price) / floor * 100
            return {
                "triggered": True,
                "reason":    f"⛔ KS-3: Giá phá đáy Darvas -{pct_below:.2f}% — Chuyển sang phân phối"
            }

        return {"triggered": False, "reason": "✅ Kill-Switch an toàn"}

    def _round_price(self, price: float) -> float:
        import math
        if price is None or price <= 0:
            return price or 0.0
        if price >= 1000:
            return round(price, 2)
        if price >= 1:
            return round(price, 4)
        first_sig = -math.floor(math.log10(abs(price)))
        decimals = min(max(first_sig + 4, 8), 10)
        return round(price, decimals)

    # =========================================================================
    # ĐIỀU PHỐI TỔNG HỢP — scan_grid_candidate()
    # =========================================================================
    def scan_grid_candidate(self, symbol: str, timeframe: str = '4h') -> dict:
        """
        Điểm vào chính. Quét và chấm điểm một mã theo Bộ Giáp Darvas.

        Thứ tự xử lý:
          1. Kéo dữ liệu 4H (90 nến đã đóng) + 1D (120 nến)
          2. Cổng Hộp Darvas — không có hộp xác nhận / giá đã vượt trần → REJECT
          3. Kill-Switch (ưu tiên tuyệt đối) — nếu triggered → REJECT ngay
          4. Chấm điểm C1 + C2 + C3 + C4 + C5
          5. Tính thông số Lưới Kép (nếu tổng ≥ 60)
        """
        try:
            # ── 1. Kéo dữ liệu ────────────────────────────────────────────────
            candles_4h = self.exchange.fetch_ohlcv(symbol, '4h', limit=self.DARVAS_LOOKBACK_4H + 10)
            df_4h = pd.DataFrame(candles_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            candles_1d = self.exchange.fetch_ohlcv(symbol, '1d', limit=self.DARVAS_1D_LIMIT)
            df_1d = pd.DataFrame(candles_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            current_price = float(df_4h['close'].iloc[-1])
            # Chỉ dùng nến ĐÃ ĐÓNG để dựng hộp (nến cuối của fetch_ohlcv là nến đang chạy)
            df_darvas     = df_4h.iloc[:-1].tail(self.DARVAS_LOOKBACK_4H).copy()

            # ── 2. Cổng Hộp Darvas ────────────────────────────────────────────
            box = self._build_darvas_box(df_darvas)
            if box.get("valid") and current_price > box["ceiling"] * (1.0 + self.BOX_EDGE_TOL_PCT):
                pct_above = (current_price / box["ceiling"] - 1.0) * 100
                box = {**box, "valid": False,
                       "reason": f"Giá đã vượt trần hộp +{pct_above:.2f}% — Breakout (thuộc DC3, không chạy lưới)"}
            if not box.get("valid"):
                box_info = {}
                if "floor" in box:
                    box_info = {
                        "Box_Floor":         self._round_price(box['floor']),
                        "Box_Ceiling":       self._round_price(box['ceiling']),
                        "Box_Amplitude_pct": round(box['amplitude'] * 100, 2),
                    }
                return {
                    "symbol": symbol, "price": current_price,
                    "total_score": 0,
                    "action":      f"🔴 TỪ CHỐI (Không có Hộp Darvas): {box.get('reason', '')}",
                    "kill_switch": {"triggered": False, "reason": ""},
                    "details": {"Box_Gate": box.get("reason", ""), **box_info}, "grid_setup": {}
                }

            # ── 3. Kill-Switch (ưu tiên tuyệt đối) ───────────────────────────
            ks = self._check_kill_switches(df_4h, df_1d, box, current_price)
            if ks["triggered"]:
                return {
                    "symbol":      symbol,
                    "price":       current_price,
                    "total_score": 0,
                    "action":      f"🔴 TỪ CHỐI (Kill-Switch): {ks['reason']}",
                    "kill_switch": ks,
                    "details":     {"kill_switch_reason": ks["reason"]},
                    "grid_setup":  {}
                }

            # ── 4. Chấm điểm 5 tiêu chí (C1/C2 chỉ trên đoạn nến thuộc hộp) ──────
            df_box = box["df_box"]
            c1 = self._score_c1_floor_testing(df_box, box)
            c2 = self._score_c2_volume_dryup(df_box, box)
            c3 = self._score_c3_macro_alignment(df_1d, current_price)
            c4 = self._score_c4_atr_squeeze(df_4h)
            c5 = self._score_c5_box_tightness(box)

            total_score = c1['score'] + c2['score'] + c3['score'] + c4['score'] + c5['score']

            # ── 5. Phân loại hành động ────────────────────────────────────────
            if total_score >= 80:
                action = "🟢 LƯỚI TỐI ƯU: Móng bê tông cốt thép — Khởi tạo ngay"
            elif total_score >= self.MIN_SCORE_FOR_GRID:
                action = "🟡 THEO DÕI: Móng đủ cứng — Chờ giá về sát nền hơn"
            else:
                action = "🔴 TỪ CHỐI: Móng xốp hoặc cấu trúc chưa đủ điều kiện"

            # ── 6. Tính thông số Lưới Kép (chỉ khi đủ điều kiện) ─────────────
            grid_setup = {}
            if total_score >= self.MIN_SCORE_FOR_GRID:
                atr_series = self.engine.get_atr(df_4h, self.ATR_PERIOD)
                atr_4h     = float(atr_series.iloc[-1])
                if not atr_4h or np.isnan(atr_4h):
                    atr_4h = (box['ceiling'] - box['floor']) * 0.10
                # [BUGFIX] Khống chế ATR dị thường (râu nến lịch sử/listing) → tối đa 12% thị giá
                atr_4h = min(atr_4h, current_price * self.MAX_ATR_PCT_OF_PRICE)

                floor_p   = box['floor']
                ceiling_p = box['ceiling']

                g1_lower = floor_p   * 0.99
                g1_upper = ceiling_p * 0.99
                g1_amp   = (g1_upper - g1_lower) / g1_lower if g1_lower > 0 else 0.15

                g2_lower = ceiling_p
                g2_upper = ceiling_p + (3.0 * atr_4h)
                g2_amp   = (g2_upper - g2_lower) / g2_lower if g2_lower > 0 else 0.05

                # [BUGFIX] SL luôn dương & hợp lý (không sâu hơn 15% dưới đáy hộp)
                stop_loss = max(floor_p - (1.5 * atr_4h), floor_p * 0.85)

                grid_setup = {
                    "is_dual_grid":   True,
                    "g1_lower":       self._round_price(g1_lower),
                    "g1_upper":       self._round_price(g1_upper),
                    "g1_grids":       min(self.MAX_GRIDS, max(8, int((g1_amp * 100) / 0.8))),
                    "g1_capital_pct": 70,
                    "g2_lower":       self._round_price(g2_lower),
                    "g2_upper":       self._round_price(g2_upper),
                    "g2_grids":       min(self.MAX_GRIDS, max(5, int((g2_amp * 100) / 0.8))),
                    "g2_capital_pct": 30,
                    "stop_loss":      self._round_price(stop_loss),
                    "take_profit":    self._round_price(g2_upper + (1.0 * atr_4h)),
                    # Thông tin hộp để hiển thị ở coin_filter.py
                    "lower_price":    self._round_price(floor_p),
                    "upper_price":    self._round_price(ceiling_p),
                    "amplitude_pct":  round(box['amplitude'] * 100, 2),
                    "c4_score":       c4['score'],
                }

            return {
                "symbol":      symbol,
                "price":       current_price,
                "total_score": total_score,
                "action":      action,
                "kill_switch": ks,
                "details": {
                    "C1_Floor_Testing":   c1['status'],
                    "C2_Volume_Dryup":    c2['status'],
                    "C3_Macro_Alignment": c3['status'],
                    "C4_ATR_Squeeze":     c4['status'],
                    "C5_Box_Tightness":   c5['status'],
                    "Box_Gate":           box['reason'],
                    "Box_Bars":           box['box_bars'],
                    "Box_Floor":          self._round_price(box['floor']),
                    "Box_Ceiling":        self._round_price(box['ceiling']),
                    "Box_Amplitude_pct":  round(box['amplitude'] * 100, 2),
                    "C1_score":           c1['score'],
                    "C2_score":           c2['score'],
                    "C3_score":           c3['score'],
                    "C4_score":           c4['score'],
                    "C5_score":           c5['score'],
                },
                "grid_setup": grid_setup,
            }

        except Exception as e:
            logger.debug("Bỏ qua %s do lỗi CCXT (thường là BadSymbol): %s", symbol, str(e))
            return {
                "symbol":      symbol,
                "error":       str(e),
                "total_score": 0,
                "action":      "🔴 TỪ CHỐI: Lỗi xử lý",
                "kill_switch": {"triggered": False},
                "details":     {},
                "grid_setup":  {}
            }
