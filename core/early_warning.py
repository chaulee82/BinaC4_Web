"""
Module: early_warning.py
Dự án: CN4-Platform
Mục đích: Hệ Thống Cảnh Báo Sớm Đa Động Cơ

Chứa 2 hàm độc lập:
  check_warning_level()  — EW tổng quát cho toàn watchlist (Bộ lọc đầu vào)
  scan_sniper_safety()   — EW chuyên biệt cho Động Cơ 2 Pullback Sniper (Bộ giáp vào lệnh)

Ma Trận EW Sniper (scan_sniper_safety):
  EW CẤP 1 — Fatal Risk    : REJECT ngay, raise lên EntryCalculatorService
  EW CẤP 2 — High Vola     : Bắt buộc chế độ SL Conservative, tắt Payload Aggressive
  EW CẤP 3 — Safe (default): APPROVED, tiếp tục pipeline bình thường

Bảng Chấm Điểm Cấu Trúc Pullback (trả về cùng kết quả EW):
  C1 (Wick Purity)     : 30đ — Chất lượng râu nến EMA25 trong 20 nến 15M
  C2 (Micro Dry-up)    : 25đ — Vol nến đỏ hiện tại vs đỉnh xả trước
  C3 (Macro Momentum)  : 25đ — Độ dốc MA99 khung 4H > +1.5%
  C4 (Taker Buy Ratio) : 20đ — Tỷ lệ Taker Buy >= 50% khung 15M
"""

import logging
import pandas as pd
import numpy as np
import pandas_ta as ta

logger = logging.getLogger("CN4.EarlyWarning")


class EarlyWarningMatrix:
    """Hệ thống cảnh báo sớm đa lớp cho tất cả các Động Cơ."""

    def __init__(self):
        from core.indicator_engine import IndicatorEngine
        self.engine = IndicatorEngine()

    # =========================================================================
    # HÀM 1: CHECK_WARNING_LEVEL — EW Tổng Quát (dùng cho toàn watchlist)
    # =========================================================================
    def check_warning_level(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> dict:
        """
        Nạp DataFrame chứa nến OHLCV 1H, 4H, 1D.
        Trả về cấp độ cảnh báo rủi ro (0: An toàn, 1: Theo dõi, 2: Nguy hiểm, 3: Khẩn cấp)
        """
        try:
            # Helper to preprocess dataframe
            def prep_df(df, length_sma=7, length_ema=7, length_bb=20, length_st=10, st_mult=3.0):
                if not isinstance(df.index, pd.DatetimeIndex):
                    if 'timestamp' in df.columns:
                        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('datetime', inplace=True)

                df.ta.sma(length=length_sma, append=True)
                df.ta.ema(length=length_ema, append=True)
                df.ta.bbands(length=length_bb, std=2, append=True)
                df.ta.sma(close='volume', length=length_bb, append=True)
                df.ta.supertrend(length=length_st, multiplier=st_mult, append=True)
                return df

            df_1h = prep_df(df_1h.copy())
            df_4h = prep_df(df_4h.copy())
            df_1d = prep_df(df_1d.copy())

            if df_1d.empty or len(df_1d) < 20 or df_4h.empty or len(df_4h) < 20 or df_1h.empty or len(df_1h) < 20:
                return {"level": 0, "status": "Không đủ dữ liệu"}

            latest_1d = df_1d.iloc[-1]
            latest_4h = df_4h.iloc[-1]
            latest_1h = df_1h.iloc[-1]

            # CẤP 3: KHẨN CẤP (Gãy Trend 1D)
            supertrend_col_1d = [col for col in df_1d.columns if 'SUPERT_' in col]
            st_1d = latest_1d[supertrend_col_1d[0]] if supertrend_col_1d else 0
            if st_1d > 0 and latest_1d['close'] < st_1d:
                return {
                    "level": 3,
                    "label": "💀 CẤP 3: KHẨN CẤP (Gãy Trend 1D)",
                    "trigger": "Giá thủng Supertrend mốc 1D",
                    "action": "Kích hoạt cắt lỗ. Hủy mọi lệnh mua."
                }

            # CẤP 2: NGUY HIỂM (Thủng BB Dưới 4H kèm Vol xả)
            bbl_col_4h = [col for col in df_4h.columns if col.startswith('BBL_20_')]
            bb_lower_4h = latest_4h[bbl_col_4h[0]] if bbl_col_4h else 0
            vol_sma20_col_4h = [col for col in df_4h.columns if col.startswith('SMA_20')]
            vol_sma20_4h = latest_4h[vol_sma20_col_4h[0]] if vol_sma20_col_4h else 0
            if latest_4h['close'] < bb_lower_4h and latest_4h['volume'] > vol_sma20_4h:
                return {
                    "level": 2,
                    "label": "🛑 CẤP 2: NGUY HIỂM (Cảnh Báo 4H)",
                    "trigger": "Xuyên thủng Đáy BB(20) 4H kèm Volume xả",
                    "action": "Hủy ngay lệnh Limit sóng hồi. Cấm bắt dao rơi."
                }

            # CẤP 1: THEO DÕI (Gãy MA ngắn hạn 1H)
            sma7_col_1h = [col for col in df_1h.columns if col.startswith('SMA_7')]
            sma7_1h = latest_1h[sma7_col_1h[0]] if sma7_col_1h else 0
            ema7_col_1h = [col for col in df_1h.columns if col.startswith('EMA_7')]
            ema7_1h = latest_1h[ema7_col_1h[0]] if ema7_col_1h else 0

            if latest_1h['close'] < sma7_1h and latest_1h['close'] < ema7_1h:
                return {
                    "level": 1,
                    "label": "⚠️ CẤP 1: THEO DÕI (Vùng Nhạy Cảm 1H)",
                    "trigger": "Giá cắt xuống dưới cụm MA(7) & EMA(7) 1H",
                    "action": "Dừng mua đuổi. Đưa vào tầm ngắm giám sát."
                }

            # CẤP 0: AN TOÀN
            return {
                "level": 0,
                "label": "✅ AN TOÀN",
                "trigger": "Cấu trúc ổn định đa khung",
                "action": "Hoạt động bình thường."
            }

        except Exception as e:
            return {"level": 0, "status": f"Lỗi tính toán EW: {str(e)}"}

    # =========================================================================
    # =========================================================================
    # HÀM 2: SCAN_PULLBACK_EW — EW Chuyên Biệt Cho Động Cơ 2 Pullback Sniper
    # =========================================================================
    def scan_pullback_ew(
        self,
        df_15m:        pd.DataFrame,
        df_1h:         pd.DataFrame,
        df_4h:         pd.DataFrame,
        df_1d:         pd.DataFrame,
        current_price: float,
        coin_vola_24h: float = 0.0,
        avg_vola_24h:  float = 0.0,
        symbol:        str   = "",
    ) -> dict:
        """
        Bộ Giáp Vào Lệnh cho Động Cơ 2 — Pullback Sniper.

        Chạy TRƯỚC EntryCalculatorService.calculate() để bảo vệ 30% vốn.
        Bất kỳ EW Cấp 1 nào → caller phải raise EntryCalculatorServiceError.

        Args:
        Args:
            df_15m:        DataFrame OHLCV khung 15M
            df_1h:         DataFrame OHLCV khung 1H
            df_4h:         DataFrame OHLCV khung 4H
            df_1d:         DataFrame OHLCV khung 1D
            current_price: Giá live hiện tại
            coin_vola_24h: Biến động 24h của coin (%)
            avg_vola_24h:  Biến động 24h trung bình toàn thị trường (%)
            symbol:        Tên mã (để log)

        Returns:
            dict:
                ew_level         (int)  — 1: Fatal/REJECT | 2: High Vola | 3: Safe/APPROVED
                ew_label         (str)  — Mô tả cấp độ
                ew_triggers      (list) — Danh sách lý do kích hoạt
                force_conservative (bool) — True khi EW Cấp 2 → bắt buộc SL Conservative
                pullback_score   (int)  — Tổng điểm C1+C2+C3+C4 (0-100)
                pullback_detail  (dict) — Chi tiết từng tiêu chí
        """
        sym = symbol or "UNKNOWN"
        ew_level   = 3       # Mặc định: An toàn
        ew_triggers = []
        force_conservative = False

        # ── Kiểm tra dữ liệu tối thiểu ────────────────────────────────────────
        if df_15m is None or len(df_15m) < 25:
            return self._ew_safe_default(sym, "Thiếu dữ liệu 15M")
        if df_4h is None or len(df_4h) < 30:
            return self._ew_safe_default(sym, "Thiếu dữ liệu 4H")

        try:
            # ══════════════════════════════════════════════════════════════════
            # MA TRẬN RỦI RO — EW CẤP 1 (Fatal Risk / REJECT)
            # ══════════════════════════════════════════════════════════════════

            # ── KS-1A: Thủng MA99 4H hoặc Supertrend 1D ─────────
            if len(df_4h) >= 100:
                ma99_4h = df_4h.ta.sma(length=99)
                if ma99_4h is not None and not ma99_4h.empty:
                    if float(df_4h['close'].iloc[-1]) < float(ma99_4h.iloc[-1]):
                        ew_triggers.append(
                            "⛔ EW1-A: Close 4H thủng MA99 — Gãy cấu trúc vĩ mô 4H"
                        )
                        ew_level = min(ew_level, 1)

            if df_1d is not None and len(df_1d) >= 12:
                st_1d = self.engine.get_supertrend(df_1d, period=10, multiplier=3.0)
                if st_1d['direction'] == -1:
                    ew_triggers.append(
                        "⛔ EW1-A: Supertrend 1D DOWNTREND — Gãy xu hướng dài hạn"
                    )
                    ew_level = min(ew_level, 1)

            # ── KS-1B: Vol xả đỏ 1H > 3× MA20 ────────────────────
            if df_1h is not None and len(df_1h) >= 21:
                last_1h = df_1h.iloc[-1]
                ma20_vol_1h = float(df_1h['volume'].tail(21).iloc[:-1].mean())
                is_red = last_1h['close'] < last_1h['open']
                if is_red and ma20_vol_1h > 0 and last_1h['volume'] > ma20_vol_1h * 3.0:
                    vol_x = last_1h['volume'] / ma20_vol_1h
                    ew_triggers.append(
                        f"⛔ EW1-B: Vol xả đỏ 1H = {vol_x:.1f}× MA20 — Cá mập xả hàng"
                    )
                    ew_level = min(ew_level, 1)

            # ── TÍN HIỆU TỐT: Close 4H xuyên dưới BOLL_DN 4H ────────────────
            if len(df_4h) >= 22:
                bb_4h   = self.engine.get_bollinger_bands(df_4h, period=20, std_dev=2.0)
                boll_dn = float(bb_4h['lower'].iloc[-1])
                last_close_4h = float(df_4h['close'].iloc[-1])
                if last_close_4h < boll_dn:
                    ew_triggers.append(
                        f"🟢 TÍN HIỆU TỐT: Giá 4H ({last_close_4h:.4f}) đâm thủng BB(20) đáy ({boll_dn:.4f}) -> Quá bán (Oversold)"
                    )

            # ══════════════════════════════════════════════════════════════════
            # MA TRẬN RỦI RO — EW CẤP 2 (High Vola / Chuyển Chế Độ)
            # ══════════════════════════════════════════════════════════════════
            if ew_level > 1:   # Chỉ kiểm tra Cấp 2 nếu chưa bị Cấp 1

                # ── EW2-A: Vola coin > 1.5× avg_vola toàn thị trường ─────────
                if avg_vola_24h > 0 and coin_vola_24h > avg_vola_24h * 1.5:
                    ew_triggers.append(
                        f"⚠️ EW2-A: Coin Vola ({coin_vola_24h:.1f}%) = {coin_vola_24h/avg_vola_24h:.1f}× AVG — Bất ổn cao"
                    )
                    force_conservative = True
                    ew_level = min(ew_level, 2)

                # ── EW2-B: Râu trên cực dài 15M (Upper Wick > 60% total range) ─
                if len(df_15m) >= 3:
                    # Kiểm tra 3 nến 15M gần nhất đã đóng
                    recent_3 = df_15m.iloc[-4:-1]   # -4 đến -2 (đã đóng chắc)
                    long_wick_count = 0
                    for _, row in recent_3.iterrows():
                        total_rng = row['high'] - row['low']
                        if total_rng <= 0:
                            continue
                        body_top = max(row['open'], row['close'])
                        upper_wick = (row['high'] - body_top) / total_rng
                        if upper_wick >= 0.60:   # Râu trên chiếm ≥ 60% cây nến
                            long_wick_count += 1

                    if long_wick_count >= 2:
                        ew_triggers.append(
                            f"⚠️ EW2-B: {long_wick_count}/3 nến 15M có râu trên cực dài (≥60%) — Bán chặn đầu mạnh"
                        )
                        force_conservative = True
                        ew_level = min(ew_level, 2)

            # ══════════════════════════════════════════════════════════════════
            # BẢNG CHẤM ĐIỂM CẤU TRÚC PULLBACK (C1-C4)
            # Chạy song song với EW, không block — chỉ cung cấp context
            # ══════════════════════════════════════════════════════════════════
            c1 = self._score_c1_wick_purity(df_15m)
            c2 = self._score_c2_micro_dryup(df_15m)
            c3 = self._score_c3_macro_momentum(df_4h)
            c4 = self._score_c4_taker_buy(df_15m)

            pullback_score  = c1['score'] + c2['score'] + c3['score'] + c4['score']
            pullback_detail = {
                "C1_Wick_Purity":    c1,
                "C2_Micro_Dryup":    c2,
                "C3_Macro_Momentum": c3,
                "C4_Taker_Buy":      c4,
            }

            # ── Đặt label và trigger cuối cùng ────────────────────────────────
            if ew_level == 1:
                ew_label = "🔴 EW CẤP 1 — FATAL RISK: REJECT LỆNH"
            elif ew_level == 2:
                ew_label = "🟡 EW CẤP 2 — HIGH VOLA: Chỉ SL Conservative"
            else:
                ew_label  = "🟢 EW CẤP 3 — AN TOÀN: APPROVED"
                ew_triggers.append("✅ Không có cờ đỏ — Cấu trúc Pullback hợp lệ")

            logger.debug(
                "[%s] scan_sniper_safety: EW=%d | Pullback Score=%d | Triggers=%s",
                sym, ew_level, pullback_score, ew_triggers
            )

            return {
                "ew_level":          ew_level,
                "ew_label":          ew_label,
                "ew_triggers":       ew_triggers,
                "force_conservative": force_conservative,
                "pullback_score":    pullback_score,
                "pullback_detail":   pullback_detail,
            }

        except Exception as e:
            logger.exception("[%s] Lỗi scan_sniper_safety: %s", sym, e)
            # Fail-safe: trả về EW Cấp 3 (an toàn) để không chặn nhầm
            return self._ew_safe_default(sym, f"Lỗi xử lý: {e}")

    # ── Nội Bộ: Fallback an toàn ──────────────────────────────────────────────
    def _ew_safe_default(self, symbol: str, reason: str) -> dict:
        """Trả về kết quả EW Cấp 3 (an toàn) khi không đủ data hoặc lỗi."""
        return {
            "ew_level":          3,
            "ew_label":          f"🟢 EW CẤP 3 — SAFE (Fallback: {reason})",
            "ew_triggers":       [f"⚠️ {reason}"],
            "force_conservative": False,
            "pullback_score":    0,
            "pullback_detail":   {},
        }

    # =========================================================================
    # C1 — CHẤT LƯỢNG RÂU NẾN (WICK PURITY) — 30 ĐIỂM
    # =========================================================================
    def _score_c1_wick_purity(self, df_15m: pd.DataFrame) -> dict:
        """
        Trong 20 nến 15M gần nhất: mỗi lần giá đâm thủng EMA25, kiểm tra
        đó là RÂU nến (close > EMA25) hay NẾN ĐỎ ĐẶC (close < EMA25).

        Thang điểm:
          Tỷ lệ râu sạch ≥ 80% → 30đ  (Phe Mua chầu chực rất uy tín)
          Tỷ lệ râu sạch ≥ 60% → 20đ  (Ổn)
          Tỷ lệ râu sạch ≥ 40% → 10đ  (Yếu — cần chú ý)
          Tỷ lệ râu sạch < 40% →  0đ  (Lực cầu bỏ cuộc — NGUY HIỂM)
          Chưa có lần nào thủng →  0đ  (Chưa đủ dữ liệu test)
        """
        if len(df_15m) < 22:
            return {"score": 0, "status": "⚠️ Thiếu dữ liệu 15M", "ratio": 0}

        ema25 = self.engine.get_ema(df_15m, 25)
        # Scan 20 nến đã đóng (bỏ nến đang hình thành)
        scan = df_15m.iloc[-21:-1].copy()
        ema_scan = ema25.iloc[-21:-1].values

        # Tập hợp nến có Low < EMA25 (đâm thủng bên dưới)
        pierced_mask = scan['low'].values < ema_scan
        pierced_count = int(pierced_mask.sum())

        if pierced_count == 0:
            return {
                "score":  0,
                "status": "⚠️ Chưa có lần nào giá test EMA25 — Chưa đủ dữ liệu",
                "ratio":  0,
                "pierced": 0
            }

        # Trong các nến thủng: bao nhiêu lần rút chân lên trên EMA25 (râu sạch)?
        pierced_closes = scan['close'].values[pierced_mask]
        pierced_ema25  = ema_scan[pierced_mask]
        clean_wick_count = int((pierced_closes > pierced_ema25).sum())
        ratio = clean_wick_count / pierced_count

        if ratio >= 0.80:
            score  = 30
            status = f"💪 Phe Mua chầu chực rất uy tín ({clean_wick_count}/{pierced_count} râu sạch — {ratio*100:.0f}%)"
        elif ratio >= 0.60:
            score  = 20
            status = f"👍 Lực cầu tốt tại EMA25 ({clean_wick_count}/{pierced_count} râu sạch — {ratio*100:.0f}%)"
        elif ratio >= 0.40:
            score  = 10
            status = f"⚠️ Lực cầu yếu ({clean_wick_count}/{pierced_count} râu sạch — {ratio*100:.0f}%)"
        else:
            score  = 0
            status = f"⛔ Lực cầu bỏ cuộc ({clean_wick_count}/{pierced_count} râu sạch — {ratio*100:.0f}%)"

        return {
            "score":   score,
            "status":  status,
            "ratio":   round(ratio, 3),
            "pierced": pierced_count,
            "clean":   clean_wick_count,
        }

    # =========================================================================
    # C2 — CẠN CUNG VI MÔ (MICRO DRY-UP) — 25 ĐIỂM
    # =========================================================================
    def _score_c2_micro_dryup(self, df_15m: pd.DataFrame) -> dict:
        """
        Vol nến đỏ 15M hiện tại (nến cuối) < 50% trung bình các nến đỏ xả đỉnh
        trước đó (các nến đỏ có Vol > MA20 trong 20 nến gần nhất).

        Thang điểm:
          Vol hiện tại < 30% vol xả đỉnh → 25đ  (Phe bán kiệt sức hoàn toàn)
          Vol hiện tại < 50% vol xả đỉnh → 18đ  (Cung đang cạn kiệt)
          Vol hiện tại < 70% vol xả đỉnh → 10đ  (Cạn cung yếu)
          Vol hiện tại ≥ 70% vol xả đỉnh →  0đ  (Phe bán vẫn mạnh)
        """
        if len(df_15m) < 22:
            return {"score": 0, "status": "⚠️ Thiếu dữ liệu 15M"}

        # Xác định Vol "xả đỉnh" = các nến đỏ có vol > MA20
        scan_df  = df_15m.iloc[-21:-1]
        ma20_vol = float(scan_df['volume'].mean())
        if ma20_vol <= 0:
            return {"score": 0, "status": "⚠️ MA20 Vol = 0"}

        red_heavy = scan_df[
            (scan_df['close'] < scan_df['open']) &
            (scan_df['volume'] > ma20_vol)
        ]

        if red_heavy.empty:
            # Không có nến xả đỉnh → tích cực, cho điểm tối đa
            return {
                "score":  25,
                "status": "💧 Không có nến xả đỉnh trong 20 nến — Phe bán im lặng",
                "ratio":  0.0
            }

        avg_heavy_vol = float(red_heavy['volume'].mean())
        current_vol   = float(df_15m['volume'].iloc[-1])
        ratio         = current_vol / avg_heavy_vol if avg_heavy_vol > 0 else 1.0

        if ratio < 0.30:
            score  = 25
            status = f"💧 Cung cạn kiệt vi mô (Vol hiện tại chỉ {ratio*100:.0f}% vol xả đỉnh)"
        elif ratio < 0.50:
            score  = 18
            status = f"💧 Cung đang cạn ({ratio*100:.0f}% vol xả đỉnh)"
        elif ratio < 0.70:
            score  = 10
            status = f"⚠️ Tín hiệu cạn cung yếu ({ratio*100:.0f}% vol xả đỉnh)"
        else:
            score  = 0
            status = f"⛔ Phe bán vẫn mạnh ({ratio*100:.0f}% vol xả đỉnh — Chưa kiệt)"

        return {"score": score, "ratio": round(ratio, 3), "status": status}

    # =========================================================================
    # C3 — GIA TỐC VĨ MÔ (MACRO MOMENTUM) — 25 ĐIỂM
    # =========================================================================
    def _score_c3_macro_momentum(self, df_4h: pd.DataFrame) -> dict:
        """
        Độ dốc MA99 khung 4H bắt buộc > +1.5% (dốc lên rõ rệt).
        Khác Động Cơ 1 (chấp nhận MA đi ngang), DC2 bắt buộc phải có upslope vĩ mô.

        Thang điểm:
          Slope MA99 > +3.0% → 25đ  (Momentum rất mạnh — Pullback V đẹp)
          Slope MA99 > +1.5% → 18đ  (Đạt ngưỡng tối thiểu)
          Slope MA99 0~+1.5% →  8đ  (Đi ngang — Cảnh báo, thiếu gia tốc)
          Slope MA99 < 0     →  0đ  (Downslope — NGUY HIỂM, không phù hợp DC2)
        """
        if len(df_4h) < 101:
            return {"score": 8, "status": "⚠️ Thiếu data 4H để tính MA99 (cần ≥ 101 nến)"}

        ma99_series  = self.engine.get_ma(df_4h, 99)
        ma99_current = float(ma99_series.iloc[-1])
        # So sánh MA99 hiện tại vs cách đây 10 phiên 4H (~40 giờ)
        ma99_past    = float(ma99_series.iloc[-11]) if len(ma99_series) >= 11 else ma99_current
        slope_pct    = (ma99_current - ma99_past) / ma99_past * 100 if ma99_past > 0 else 0.0

        if slope_pct > 3.0:
            score  = 25
            status = f"🚀 MA99 4H tăng rất mạnh (+{slope_pct:.2f}%) — Momentum đỉnh cao, V-Shape xác suất cao"
        elif slope_pct > 1.5:
            score  = 18
            status = f"📈 MA99 4H đạt ngưỡng DC2 (+{slope_pct:.2f}%) — Gia tốc vĩ mô hợp lệ"
        elif slope_pct >= 0:
            score  = 8
            status = f"➡️ MA99 4H đi ngang (+{slope_pct:.2f}%) — Thiếu gia tốc, V-Shape kém chắc"
        else:
            score  = 0
            status = f"⛔ MA99 4H cắm đầu ({slope_pct:.2f}%) — Downslope vĩ mô, DC2 không phù hợp"

        return {
            "score":     score,
            "slope_pct": round(slope_pct, 3),
            "ma99":      ma99_current,
            "status":    status
        }

    # =========================================================================
    # C4 — TỶ LỆ TAKER BUY (CÁ MẬP ĐỠ GIÁ) — 20 ĐIỂM
    # =========================================================================
    def _score_c4_taker_buy(self, df_15m: pd.DataFrame) -> dict:
        """
        Tỷ lệ Taker Buy Quote (USDT) / Quote Volume của nến 15M gần nhất ≥ 50%.
        Yêu cầu df_15m có cột 'Taker_Buy_Quote' và 'Quote_Volume'.

        Thang điểm:
          Ratio ≥ 60% → 20đ  (Cá mập đang chủ động đỡ giá)
          Ratio ≥ 50% → 14đ  (Cân bằng / hơi nghiêng về mua)
          Ratio ≥ 40% →  7đ  (Hơi áp bán — cẩn thận)
          Ratio < 40% →  0đ  (Phe bán áp đảo — không vào lệnh)
          Không có data → 10đ (Fallback trung lập)
        """
        # Cột Taker Buy có thể có nhiều tên khác nhau
        taker_col = None
        vol_col   = None
        for c in df_15m.columns:
            if 'Taker_Buy' in c or 'taker_buy' in c.lower():
                taker_col = c
            if 'Quote_Volume' in c or 'quote_vol' in c.lower():
                vol_col = c

        if taker_col is None or vol_col is None:
            return {
                "score":  10,
                "status": "⚠️ Không có dữ liệu Taker Buy — Dùng điểm trung lập (10đ)",
                "ratio":  None
            }

        last = df_15m.iloc[-1]
        taker_buy = float(last.get(taker_col, 0) or 0)
        total_vol = float(last.get(vol_col,   0) or 0)

        if total_vol <= 0:
            return {"score": 10, "status": "⚠️ Quote Volume = 0", "ratio": None}

        ratio = taker_buy / total_vol

        if ratio >= 0.60:
            score  = 20
            status = f"🐳 Cá mập đang chủ động đỡ giá (Taker Buy = {ratio*100:.1f}%)"
        elif ratio >= 0.50:
            score  = 14
            status = f"👍 Dòng tiền mua cân bằng/hơi áp đảo ({ratio*100:.1f}%)"
        elif ratio >= 0.40:
            score  = 7
            status = f"⚠️ Phe bán hơi áp đảo ({ratio*100:.1f}%) — Cẩn thận"
        else:
            score  = 0
            status = f"⛔ Phe bán áp đảo hoàn toàn ({ratio*100:.1f}%) — Không vào lệnh"

        return {"score": score, "ratio": round(ratio, 3), "status": status}
