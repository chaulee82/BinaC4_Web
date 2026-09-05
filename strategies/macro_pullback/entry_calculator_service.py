"""
Module: entry_calculator_service.py
Dự án: CN4-Platform
Động Cơ: 2 — Pullback Sniper (Spot Hold)
Loại:    SNIPER_SPOT

Vai Trò Kiến Trúc:
  Lớp Thực Thi (Execution Core) — chạy SAU pullback_sniper.py (Lớp Tiền Trạm).
  Chỉ nhận mã đã vượt qua Gate 0-4, dồn CPU tính toán nặng:
    Bước 1: Dynamic Wick Deviation       → Toạ độ Entry tối ưu (15m)
    Bước 2: Macro TP Resolver            → TP vĩ mô (4H/1D) + TP trung gian (1H)
    Bước 3: Dynamic SL Routing           → SL phân luồng theo hệ số nhiễu động
    Bước 4: R/R Gate Validation          → Loại bỏ setup có R/R < 1.5
    Bước 5: Split Position Payload Build → Đóng gói OCO dual-payload JSON

Nguồn dữ liệu:
  - OHLCV: ccxt exchange (Binance / fallback)
  - avg_vola_24h: coin_filter.get_avg_vola_24h()
  - coin_vola_24h: coin_filter.live_data_map[symbol]['daily_vola']
"""

import logging
import math
import time
from typing import Optional

import numpy as np
import pandas as pd

from core.indicator_engine import IndicatorEngine
from core.early_warning import EarlyWarningMatrix

logger = logging.getLogger("CN4.Engine2.EntryCalc")

# ── Hằng Số Cấu Hình ──────────────────────────────────────────────────────────
ENGINE_TYPE     = "SNIPER_SPOT"
WICK_SCAN_N     = 20        # Số nến 15m quét độ lệch râu (Dynamic Wick)
EMA_ENTRY_SPAN  = 25        # EMA làm neo điểm Entry
BOLL_PERIOD     = 20        # Chu kỳ Bollinger Bands (Circuit Breaker)
BOLL_STD        = 2.0       # Hệ số độ lệch chuẩn BB
MA_MACRO_PERIOD = 99        # MA99 khung macro — SL Conservative anchor
ST_PERIOD       = 10        # Chu kỳ Supertrend
ST_MULTIPLIER   = 3.0       # Hệ số ATR Supertrend
SL_AGG_BUFFER   = 0.005     # 0.5% buffer SL Aggressive
SL_CON_BUFFER   = 0.015     # 1.5% buffer SL Conservative
MIN_RR_RATIO    = 1.5       # Ngưỡng R/R tối thiểu để APPROVED


# ── Custom Exception ───────────────────────────────────────────────────────────
class EntryCalculatorServiceError(Exception):
    """
    Raised khi setup không đạt tiêu chuẩn vào lệnh:
      - R/R < MIN_RR_RATIO (1.5)
      - Chưa đủ điều kiện Pullback (giá chưa từng chạm EMA25)
      - engine_type không hợp lệ
    """
    pass


# ══════════════════════════════════════════════════════════════════════════════
# EntryCalculatorService
# ══════════════════════════════════════════════════════════════════════════════

class EntryCalculatorService:
    """
    Lõi tính toán Entry/SL/TP cho Động Cơ 2 — Pullback Sniper.

    Thiết kế để chạy song song với PullbackSniper (không thay thế).
    PullbackSniper (Gate 0-4): Sàng lọc thô, loại mã rác sớm.
    EntryCalculatorService:   Tính toán chính xác trên mã đã qua vòng gửi xe.

    Ví dụ sử dụng:
        from core.coin_filter import get_avg_vola_24h, live_data_map
        from strategies.macro_pullback.entry_calculator_service import (
            EntryCalculatorService, EntryCalculatorServiceError
        )

        service = EntryCalculatorService()
        try:
            result = service.calculate({
                "symbol":             "FETUSDT",
                "timeframe_entry":    "15m",
                "timeframe_macro":    "4h",
                "engine_type":        "SNIPER_SPOT",
                "capital_allocation": 0.30,
                "avg_vola_24h":       get_avg_vola_24h(),
                "coin_vola_24h":      live_data_map.get("FETUSDT", {}).get("daily_vola", 0.0),
            })
            print(result)
        except EntryCalculatorServiceError as e:
            print(f"Setup bị từ chối: {e}")
    """

    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()
        self.engine   = IndicatorEngine()
        self.ew       = EarlyWarningMatrix()

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API — Điểm Vào Chính
    # ══════════════════════════════════════════════════════════════════════════

    def calculate(self, params: dict) -> dict:
        """
        Chạy toàn bộ pipeline 5 bước và trả về payload JSON-serializable.

        Args:
            params (dict):
                symbol            (str)   — VD "FETUSDT" hoặc "FET/USDT"
                timeframe_entry   (str)   — Khung vi mô, mặc định "15m"
                timeframe_macro   (str)   — Khung vĩ mô, mặc định "4h"
                engine_type       (str)   — Bắt buộc "SNIPER_SPOT"
                capital_allocation(float) — % vốn (0.0–1.0), VD 0.30 = 30%
                avg_vola_24h      (float) — Biến động TB toàn thị trường (từ coin_filter)
                coin_vola_24h     (float) — Biến động 24h của symbol (từ live_data_map)

        Returns:
            dict — Payload JSON-serializable theo cấu trúc Split Position OCO

        Raises:
            EntryCalculatorServiceError — Khi R/R < 1.5 hoặc thiếu điều kiện Pullback
        """
        t_start = time.monotonic()

        # ── Giải nén và validate params ────────────────────────────────────────
        symbol             = params.get("symbol", "").upper().replace("/", "").strip()
        timeframe_entry    = params.get("timeframe_entry", "15m")
        timeframe_macro    = params.get("timeframe_macro", "4h")
        engine_type        = params.get("engine_type", ENGINE_TYPE)
        capital_allocation = float(params.get("capital_allocation", 0.30))
        avg_vola_24h       = float(params.get("avg_vola_24h", 0.0))
        coin_vola_24h      = float(params.get("coin_vola_24h", 0.0))

        if engine_type != ENGINE_TYPE:
            raise EntryCalculatorServiceError(
                f"engine_type không hợp lệ: '{engine_type}'. Bắt buộc phải là '{ENGINE_TYPE}'"
            )
        if not symbol:
            raise EntryCalculatorServiceError("Tham số 'symbol' không được để trống")
        if not (0 < capital_allocation <= 1.0):
            raise EntryCalculatorServiceError(
                f"capital_allocation={capital_allocation} không hợp lệ. Phải là 0 < x <= 1.0"
            )

        logger.info(
            f"[{symbol}] ► Bắt đầu tính toán Entry | "
            f"TF: {timeframe_entry}/{timeframe_macro} | "
            f"Vola: {coin_vola_24h:.1f}% vs AVG: {avg_vola_24h:.1f}%"
        )

        # Xóa cache cũ để đảm bảo dữ liệu tươi cho mỗi lần gọi
        self.engine.clear_cache()

        # ── Kéo dữ liệu OHLCV ─────────────────────────────────────────────────
        fmt_sym  = self._fmt_symbol_ccxt(symbol)
        # Lấy đủ nến để EMA25 warmup + WICK_SCAN_N nến có nghĩa
        df_entry = self._fetch_ohlcv(fmt_sym, timeframe_entry, limit=max(WICK_SCAN_N + 40, 80))
        df_macro = self._fetch_ohlcv(fmt_sym, timeframe_macro, limit=150)
        df_mid   = self._fetch_ohlcv(fmt_sym, "1h",            limit=60)
        df_1d    = self._fetch_ohlcv(fmt_sym, "1d",            limit=60)

        current_price = float(df_entry['close'].iloc[-1])

        # ── Bộ Giáp Sniper: scan_pullback_ew() ──────────────────────────────
        # Chạy TRƯỚC Bước 1. EW Cấp 1 → REJECT ngay. EW Cấp 2 → SL Conservative bắt buộc.
        ew_result = self.ew.scan_pullback_ew(
            df_15m        = df_entry,
            df_4h         = df_macro,
            df_1h         = df_mid,
            df_1d         = df_1d,
            current_price = current_price,
            coin_vola_24h = coin_vola_24h,
            avg_vola_24h  = avg_vola_24h,
            symbol        = symbol,
        )
        ew_level = ew_result.get('ew_level', 3)

        if ew_level == 1:
            # Fatal Risk — REJECT hoàn toàn, bảo toàn vốn
            triggers_str = " | ".join(ew_result.get('ew_triggers', []))
            raise EntryCalculatorServiceError(
                f"[{symbol}] {ew_result.get('ew_label', 'EW CẤP 1')} — {triggers_str}"
            )

        # EW Cấp 2 → bắt buộc SL Conservative (ghi đè coin_vola để force routing)
        force_conservative_mode = ew_result.get('force_conservative', False)
        if force_conservative_mode:
            logger.warning(
                "[%s] EW CẤP 2 kích hoạt — Bắt buộc SL Conservative. Tắt Payload Aggressive.",
                symbol
            )

        # ── Bước 1: Dynamic Wick Deviation → Entry ────────────────────────────
        entry_result = self._calc_entry_dynamic_wick(df_entry, symbol)
        # Nếu chưa đủ điều kiện Pullback, raise ngay
        if entry_result['status'] == 'SKIP':
            raise EntryCalculatorServiceError(
                f"[{symbol}] {entry_result['reason']}"
            )
        final_entry = entry_result['final_entry']

        # ── Bước 2: Macro TP + Mid TP ─────────────────────────────────────────
        tp_result = self._calc_tp_targets(df_macro, df_mid, current_price, final_entry, symbol)
        macro_tp  = tp_result['macro_tp']
        mid_tp    = tp_result['mid_tp']

        # ── Bước 3: Dynamic SL Routing ────────────────────────────────────────
        # Nếu EW Cấp 2 kích hoạt, giả lập coin_vola rất cao để force Conservative routing
        _effective_coin_vola = coin_vola_24h * 10 if force_conservative_mode else coin_vola_24h
        sl_result       = self._route_stop_loss(
            df_entry, df_macro, final_entry, _effective_coin_vola, avg_vola_24h, symbol
        )
        sl_aggressive   = sl_result['sl_aggressive']
        sl_conservative = sl_result['sl_conservative']
        risk_mode       = sl_result['risk_mode']
        # Nếu EW Cấp 2, override risk_mode để log rõ hơn
        if force_conservative_mode and risk_mode != 'CONSERVATIVE':
            risk_mode = 'CONSERVATIVE (EW CẤP 2)'

        # ── Bước 4: R/R Gate Validation ───────────────────────────────────────
        # Raises EntryCalculatorServiceError nếu bất kỳ payload nào có R/R < 1.5
        rr_result = self._validate_rr(
            final_entry, sl_aggressive, sl_conservative, mid_tp, macro_tp, symbol
        )

        # ── Bước 5: Build Split Position Payload ──────────────────────────────
        payload = self._build_payload(
            symbol             = symbol,
            entry              = final_entry,
            sl_aggressive      = sl_aggressive,
            sl_conservative    = sl_conservative,
            mid_tp             = mid_tp,
            macro_tp           = macro_tp,
            capital_allocation = capital_allocation,
            rr_result          = rr_result,
            risk_mode          = risk_mode,
            entry_meta         = entry_result,
        )

        # Đính kèm kết quả EW và Pullback Score vào validation để hiển thị
        if 'validation' in payload:
            payload['validation']['ew_level']       = ew_level
            payload['validation']['ew_label']       = ew_result.get('ew_label', '')
            payload['validation']['ew_triggers']    = ew_result.get('ew_triggers', [])
            payload['validation']['pullback_score'] = ew_result.get('pullback_score', 0)
            payload['validation']['pullback_detail'] = ew_result.get('pullback_detail', {})

        elapsed = (time.monotonic() - t_start) * 1000
        logger.info(
            f"[{symbol}] ✅ APPROVED | Entry: {self._fmt(final_entry)} | "
            f"MidTP: {self._fmt(mid_tp)} | MacroTP: {self._fmt(macro_tp)} | "
            f"SL_Agg: {self._fmt(sl_aggressive)} | SL_Con: {self._fmt(sl_conservative)} | "
            f"R/R P1: {rr_result['rr_payload1']:.2f}R | P2: {rr_result['rr_payload2']:.2f}R | "
            f"Mode: {risk_mode} | EW={ew_level} | PB_Score={ew_result.get('pullback_score',0)} | {elapsed:.0f}ms"
        )
        return payload

    # ══════════════════════════════════════════════════════════════════════════
    # BƯỚC 1 — Dynamic Wick Deviation Algorithm
    # ══════════════════════════════════════════════════════════════════════════

    def _calc_entry_dynamic_wick(self, df: pd.DataFrame, symbol: str = "") -> dict:
        """
        Thuật Toán Dynamic Wick Deviation (hoàn toàn trên khung 15m):

          1. Neo giá = EMA(25) nến hiện tại
          2. Quét N=20 nến gần nhất (không tính nến đang hình thành)
          3. Lọc tập hợp nến bị đạp thủng EMA(25): Low < EMA25 tại thời điểm đó
          4. Nếu tập hợp rỗng → SKIP (chưa đủ điều kiện Pullback)
          5. Wick_Dev(i) = EMA25(i) - Low(i)
             Avg_Wick_Dev = mean(Wick_Dev)
          6. base_entry = EMA25_current - Avg_Wick_Dev
          7. Circuit Breaker: final_entry = min(base_entry, BOLL_DN_15M)
             (Entry bắt buộc nằm từ BOLL_DN trở xuống)

        Returns:
            dict với keys:
                status        : 'OK' | 'SKIP'
                reason        : str (chỉ có khi SKIP)
                final_entry   : float
                base_entry    : float
                ema25         : float (giá trị EMA25 nến hiện tại)
                avg_wick_dev  : float
                boll_dn       : float
                pierced_count : int (số nến đã thủng EMA25)
        """
        ema25 = self.engine.get_ema(df, EMA_ENTRY_SPAN)
        bb    = self.engine.get_bollinger_bands(df, BOLL_PERIOD, BOLL_STD)

        ema25_current = float(ema25.iloc[-1])
        boll_dn       = float(bb['lower'].iloc[-1])

        # Scan window: WICK_SCAN_N nến kết thúc (loại trừ nến đang hình thành hiện tại)
        scan_slice    = slice(-(WICK_SCAN_N + 1), -1)
        lows_scan     = df['low'].iloc[scan_slice].values
        ema25_scan    = ema25.iloc[scan_slice].values

        # Tập hợp nến bị đạp thủng EMA25
        pierced_mask = lows_scan < ema25_scan

        if not pierced_mask.any():
            return {
                'status':        'SKIP',
                'reason':        (
                    f"Trong {WICK_SCAN_N} nến {df.name if hasattr(df, 'name') else '15m'} gần nhất, "
                    f"giá chưa từng chạm EMA{EMA_ENTRY_SPAN} — chưa đủ điều kiện Pullback"
                ),
                'final_entry':   None,
                'base_entry':    None,
                'ema25':         ema25_current,
                'avg_wick_dev':  0.0,
                'boll_dn':       boll_dn,
                'pierced_count': 0,
            }

        # Tính độ lệch râu nến (Wick Deviation) cho từng nến bị đạp thủng
        wick_devs    = ema25_scan[pierced_mask] - lows_scan[pierced_mask]
        avg_wick_dev = float(wick_devs.mean())
        base_entry   = ema25_current - avg_wick_dev

        # ── Circuit Breaker ────────────────────────────────────────────────────
        # Entry bắt buộc phải nằm tại BOLL_DN hoặc thấp hơn
        if base_entry > boll_dn:
            final_entry = boll_dn
            cb_triggered = True
            logger.debug(
                f"  [{symbol}] Circuit Breaker kích hoạt: "
                f"base_entry {self._fmt(base_entry)} > BOLL_DN {self._fmt(boll_dn)} "
                f"→ final_entry = {self._fmt(boll_dn)}"
            )
        else:
            final_entry  = base_entry
            cb_triggered = False

        logger.debug(
            f"  [{symbol}] Wick Dev | EMA25: {self._fmt(ema25_current)} | "
            f"Pierced: {int(pierced_mask.sum())}/{WICK_SCAN_N} | "
            f"Avg Wick: {self._fmt(avg_wick_dev)} | "
            f"Base: {self._fmt(base_entry)} | BOLL_DN: {self._fmt(boll_dn)} | "
            f"Final Entry: {self._fmt(final_entry)} | CB: {cb_triggered}"
        )

        return {
            'status':          'OK',
            'final_entry':     final_entry,
            'base_entry':      base_entry,
            'ema25':           ema25_current,
            'avg_wick_dev':    avg_wick_dev,
            'boll_dn':         boll_dn,
            'pierced_count':   int(pierced_mask.sum()),
            'cb_triggered':    cb_triggered,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # BƯỚC 2 — Macro TP + Mid TP Resolver
    # ══════════════════════════════════════════════════════════════════════════

    def _calc_tp_targets(
        self,
        df_macro: pd.DataFrame,
        df_mid:   pd.DataFrame,
        current_price: float,
        entry_price:   float,
        symbol: str = "",
    ) -> dict:
        """
        Xác định 2 mục tiêu chốt lời:

          macro_tp: Kháng cự gần nhất trên khung vĩ mô (4H/1D)
                    Nguồn: Swing High, MA99, Supertrend resistance
                    → Dành cho Payload 2 Conservative (gồng sóng dài)

          mid_tp  : Kháng cự trung gian trên khung 1H
                    Nguồn: Swing High 1H, MA50 1H
                    → Dành cho Payload 1 Aggressive (tạo đệm lãi nhanh)

        Tuyệt đối không dùng TP ngắn hạn của khung 15m.
        """
        macro_tp = self._resolve_resistance(df_macro, entry_price, label=f"{symbol}/Macro")
        mid_tp   = self._resolve_resistance(df_mid,   entry_price, label=f"{symbol}/Mid-1H",
                                            fallback_scan_bars=25)

        # Sanity check: mid_tp không được vượt quá macro_tp (xảy ra khi 4H data ít)
        if mid_tp >= macro_tp:
            mid_tp_adjusted = entry_price + (macro_tp - entry_price) * 0.5
            logger.warning(
                f"  [{symbol}] mid_tp ({self._fmt(mid_tp)}) >= macro_tp ({self._fmt(macro_tp)}). "
                f"Điều chỉnh mid_tp về 50% khoảng cách: {self._fmt(mid_tp_adjusted)}"
            )
            mid_tp = mid_tp_adjusted

        logger.debug(
            f"  [{symbol}] TP → Mid(1H): {self._fmt(mid_tp)} | Macro: {self._fmt(macro_tp)}"
        )
        return {'macro_tp': macro_tp, 'mid_tp': mid_tp}

    def _resolve_resistance(
        self,
        df: pd.DataFrame,
        entry_price: float,
        label: str = "",
        fallback_scan_bars: int = 30,
    ) -> float:
        """
        Tìm kháng cự gần nhất phía trên entry_price từ 3 nguồn:
          1. Swing High: max của recent 50 nến có giá > entry * 1.005
          2. MA99: nếu MA99 > entry * 1.005
          3. Supertrend resistance: nếu đang downtrend trên khung này

        Trả về min của các mốc tìm được (kháng cự gần nhất, dễ chạm nhất).
        """
        min_tp_threshold = entry_price * 1.005  # TP tối thiểu cao hơn entry 0.5%
        candidates = []

        # 1. Swing High (50 nến gần nhất)
        recent_highs = df['high'].iloc[-50:]
        valid_highs  = recent_highs[recent_highs > min_tp_threshold]
        if not valid_highs.empty:
            candidates.append(float(valid_highs.max()))

        # 2. MA99
        ma99     = self.engine.get_ma(df, MA_MACRO_PERIOD)
        ma99_val = float(ma99.iloc[-1])
        if not math.isnan(ma99_val) and ma99_val > min_tp_threshold:
            candidates.append(ma99_val)

        # 3. Supertrend resistance (chỉ lấy nếu đang downtrend = kháng cự phía trên)
        st = self.engine.get_supertrend(df, ST_PERIOD, ST_MULTIPLIER)
        if st['resistance'] is not None and st['resistance'] > min_tp_threshold:
            candidates.append(float(st['resistance']))

        if candidates:
            return float(min(candidates))

        # Fallback: swing high trong phạm vi hẹp hơn, dù thấp hơn 0.5%
        fallback_high = float(df['high'].iloc[-fallback_scan_bars:].max())
        if fallback_high > entry_price:
            logger.warning(
                f"  [{label}] Không có kháng cự rõ ràng > entry+0.5%, "
                f"dùng fallback swing high {fallback_scan_bars} nến: {self._fmt(fallback_high)}"
            )
            return fallback_high

        # Absolute fallback: entry + 3% (tránh crash)
        absolute_fallback = entry_price * 1.03
        logger.error(
            f"  [{label}] Không tìm được kháng cự hợp lệ. Dùng absolute fallback +3%: "
            f"{self._fmt(absolute_fallback)}"
        )
        return absolute_fallback

    # ══════════════════════════════════════════════════════════════════════════
    # BƯỚC 3 — Dynamic SL Routing
    # ══════════════════════════════════════════════════════════════════════════

    def _route_stop_loss(
        self,
        df_entry:     pd.DataFrame,
        df_macro:     pd.DataFrame,
        entry:        float,
        coin_vola:    float,
        avg_vola:     float,
        symbol:       str = "",
    ) -> dict:
        """
        Phân luồng SL dựa trên hệ số nhiễu động của tài sản.

        Luôn tính ĐỒNG THỜI cả hai SL cho Split Position OCO:

          SL Aggressive (Payload 1):
            Điều kiện áp dụng: coin_vola_24h <= avg_vola_24h (Tài sản đi êm)
            Tọa độ: min(đáy cụm cluster 15m, BOLL_DN_15M) - 0.5% buffer
            Mục đích: Xoay vòng vốn nhanh, SL sát để nếu thắng lãi ròng cao

          SL Conservative (Payload 2):
            Điều kiện áp dụng: coin_vola_24h > avg_vola_24h (Tài sản giật xóc)
            Tọa độ: max(MA99_4H, Supertrend_4H_support) - 1.5% buffer
            Mục đích: Né Stop Hunt, chịu được nhiễu lớn để gồng sóng vĩ mô

        risk_mode phản ánh trạng thái tài sản hiện tại (xác định routing chính),
        nhưng KHÔNG ảnh hưởng đến việc tính toán — cả hai SL luôn được tính.
        """
        # ── SL Aggressive ─────────────────────────────────────────────────────
        # Đáy cluster 10 nến gần nhất của khung entry (15m)
        recent_low_cluster = float(df_entry['low'].iloc[-10:].min())
        boll_dn_15m        = float(self.engine.get_bollinger_bands(df_entry, BOLL_PERIOD, BOLL_STD)['lower'].iloc[-1])
        # Lấy ngưỡng thấp hơn trong 2 mốc để bảo đảm SL bên dưới mọi hỗ trợ
        sl_agg_base        = min(recent_low_cluster, boll_dn_15m)
        sl_aggressive      = sl_agg_base * (1.0 - SL_AGG_BUFFER)

        # ── SL Conservative ───────────────────────────────────────────────────
        ma99_macro   = float(self.engine.get_ma(df_macro, MA_MACRO_PERIOD).iloc[-1])
        st_macro     = self.engine.get_supertrend(df_macro, ST_PERIOD, ST_MULTIPLIER)
        # Supertrend support chỉ có giá trị khi đang uptrend trên khung macro
        st_support   = st_macro.get('support')

        con_anchors = []
        if not math.isnan(ma99_macro) and 0 < ma99_macro < entry:
            con_anchors.append(ma99_macro)
        if st_support is not None and 0 < st_support < entry:
            con_anchors.append(float(st_support))

        if con_anchors:
            # Anchor = mốc cao nhất (gần Entry nhất) → trừ buffer = SL thực tế
            sl_con_anchor   = max(con_anchors)
            sl_conservative = sl_con_anchor * (1.0 - SL_CON_BUFFER)
        else:
            # Fallback: dùng SL Aggressive mở rộng thêm buffer conservative
            sl_conservative = sl_aggressive * (1.0 - SL_CON_BUFFER)
            logger.warning(
                f"  [{symbol}] SL Conservative fallback — MA99/Supertrend không hợp lệ "
                f"(MA99: {self._fmt(ma99_macro)}, ST: {st_support})"
            )

        # ── Phân Luồng Risk Mode ───────────────────────────────────────────────
        if coin_vola <= avg_vola:
            risk_mode = "AGGRESSIVE"
        else:
            risk_mode = "CONSERVATIVE"

        logger.debug(
            f"  [{symbol}] SL Routing | Mode: {risk_mode} "
            f"(Vola {coin_vola:.1f}% {'<=' if risk_mode == 'AGGRESSIVE' else '>'} AVG {avg_vola:.1f}%) | "
            f"SL_Agg: {self._fmt(sl_aggressive)} (cluster: {self._fmt(recent_low_cluster)}, "
            f"BOLL_DN: {self._fmt(boll_dn_15m)}) | "
            f"SL_Con: {self._fmt(sl_conservative)} (MA99: {self._fmt(ma99_macro)}, "
            f"ST: {self._fmt(st_support) if st_support else 'N/A'})"
        )

        return {
            'sl_aggressive':   sl_aggressive,
            'sl_conservative': sl_conservative,
            'risk_mode':       risk_mode,
            # Debug info
            'sl_agg_base':     sl_agg_base,
            'ma99_macro':      ma99_macro,
            'st_support':      st_support,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # BƯỚC 4 — R/R Gate Validation
    # ══════════════════════════════════════════════════════════════════════════

    def _validate_rr(
        self,
        entry:     float,
        sl_agg:    float,
        sl_con:    float,
        mid_tp:    float,
        macro_tp:  float,
        symbol:    str = "",
    ) -> dict:
        """
        Trạm kiểm duyệt R/R (Risk/Reward Gate).

        Kiểm tra ĐỒng THỜI cả 2 payload:
          Payload 1: R/R = (mid_tp   - entry) / (entry - sl_aggressive)
          Payload 2: R/R = (macro_tp - entry) / (entry - sl_conservative)

        Cả hai đều phải >= MIN_RR_RATIO = 1.5.
        Chỉ cần 1 payload thất bại → REJECT toàn bộ setup.

        Raises:
            EntryCalculatorServiceError khi bất kỳ payload nào có R/R < 1.5
        """
        risk_p1   = entry - sl_agg
        reward_p1 = mid_tp - entry
        rr_p1     = (reward_p1 / risk_p1) if risk_p1 > 0 else 0.0

        risk_p2   = entry - sl_con
        reward_p2 = macro_tp - entry
        rr_p2     = (reward_p2 / risk_p2) if risk_p2 > 0 else 0.0

        failures = []
        if rr_p1 < MIN_RR_RATIO:
            failures.append(
                f"Payload 1 R/R = {rr_p1:.2f}R < {MIN_RR_RATIO}R "
                f"(Entry: {self._fmt(entry)} → MidTP: {self._fmt(mid_tp)}, SL_Agg: {self._fmt(sl_agg)})"
            )
        if rr_p2 < MIN_RR_RATIO:
            failures.append(
                f"Payload 2 R/R = {rr_p2:.2f}R < {MIN_RR_RATIO}R "
                f"(Entry: {self._fmt(entry)} → MacroTP: {self._fmt(macro_tp)}, SL_Con: {self._fmt(sl_con)})"
            )

        if failures:
            raise EntryCalculatorServiceError(
                f"[{symbol}] R/R Gate REJECTED — {' | '.join(failures)}"
            )

        overall_rr = round((rr_p1 + rr_p2) / 2.0, 2)

        logger.debug(
            f"  [{symbol}] R/R Gate PASSED | "
            f"P1: {rr_p1:.2f}R (risk: {self._fmt(risk_p1)}, reward: {self._fmt(reward_p1)}) | "
            f"P2: {rr_p2:.2f}R (risk: {self._fmt(risk_p2)}, reward: {self._fmt(reward_p2)}) | "
            f"Overall: {overall_rr}R"
        )

        return {
            'rr_payload1': round(rr_p1, 2),
            'rr_payload2': round(rr_p2, 2),
            'rr_overall':  overall_rr,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # BƯỚC 5 — Build Split Position OCO Payload
    # ══════════════════════════════════════════════════════════════════════════

    def _build_payload(
        self,
        symbol:             str,
        entry:              float,
        sl_aggressive:      float,
        sl_conservative:    float,
        mid_tp:             float,
        macro_tp:           float,
        capital_allocation: float,
        rr_result:          dict,
        risk_mode:          str,
        entry_meta:         dict,
    ) -> dict:
        """
        Đóng gói Payload Split Position OCO theo cấu trúc PRD.

        Chia đôi capital_allocation:
          Payload 1 — Aggressive (50%):
            SL: sl_aggressive  (ngắn, khung 15m)
            TP: mid_tp         (kháng cự trung gian 1H)
            Mục tiêu: Tạo đệm lãi nhanh, giải phóng tâm lý gồng Payload 2

          Payload 2 — Conservative (50%):
            SL: sl_conservative (dài, MA99/Supertrend 4H)
            TP: macro_tp        (kháng cự vĩ mô 4H/1D)
            Mục tiêu: Gồng sóng dài, tối đa hóa biên độ lợi nhuận
            Trigger: Bật TRAILING_STOP_BREAKEVEN khi Payload 1 chạm TP
        """
        r = self._round_price

        return {
            "engine":  ENGINE_TYPE,
            "symbol":  symbol,
            "status":  "APPROVED",
            "validation": {
                "rr_ratio":       rr_result['rr_overall'],
                "rr_payload1":    rr_result['rr_payload1'],
                "rr_payload2":    rr_result['rr_payload2'],
                "entry_price":    r(entry),
                "risk_mode":      risk_mode,
                "capital_pct":    round(capital_allocation * 100, 1),
                "ema25":          r(entry_meta.get('ema25', 0)),
                "avg_wick_dev":   r(entry_meta.get('avg_wick_dev', 0)),
                "boll_dn":        r(entry_meta.get('boll_dn', 0)),
                "cb_triggered":   entry_meta.get('cb_triggered', False),
                "pierced_count":  entry_meta.get('pierced_count', 0),
            },
            "payload": [
                {
                    "id":             "payload_1_aggressive",
                    "strategy":       "Risk Buffer (Tạo đệm lãi)",
                    "order_type":     "LIMIT",
                    "side":           "BUY",
                    "allocation_pct": 50,
                    "parameters": {
                        "price":   r(entry),
                        "oco_sl":  r(sl_aggressive),
                        "oco_tp":  r(mid_tp),
                    },
                },
                {
                    "id":             "payload_2_conservative",
                    "strategy":       "Macro Trend Hold (Gồng sóng)",
                    "order_type":     "LIMIT",
                    "side":           "BUY",
                    "allocation_pct": 50,
                    "parameters": {
                        "price":   r(entry),
                        "oco_sl":  r(sl_conservative),
                        "oco_tp":  r(macro_tp),
                    },
                    "post_execution": {
                        "webhook_trigger": "TRAILING_STOP_BREAKEVEN",
                        "condition":       "Trigger khi payload_1_aggressive khớp TP",
                    },
                },
            ],
        }

    # ══════════════════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """Kéo dữ liệu OHLCV từ exchange và trả về DataFrame chuẩn."""
        candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(
            candles,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
        )
        return df

    def _fmt_symbol_ccxt(self, symbol: str) -> str:
        """
        Chuyển đổi symbol sang định dạng ccxt (FETUSDT → FET/USDT).
        Giữ nguyên nếu đã có '/' hoặc không nhận dạng được quote currency.
        """
        if '/' in symbol:
            return symbol
        for q in ['USDT', 'BUSD', 'BTC', 'ETH', 'BNB', 'USDC']:
            if symbol.endswith(q):
                base = symbol[:-len(q)]
                return f"{base}/{q}"
        return symbol  # Trả về nguyên nếu không parse được

    def _round_price(self, price: float) -> float:
        """
        Làm tròn giá theo số chữ số có nghĩa phù hợp với từng mức giá:
          >= 1000 USDT : 2 chữ số thập phân (BTC, ETH)
          >= 1    USDT : 4 chữ số thập phân (SOL, BNB...)
          < 1     USDT : first_significant_digit + 4 (coin nhỏ như FET, SHIB...)
        """
        if price is None or price <= 0:
            return price or 0.0
        if price >= 1000:
            return round(price, 2)
        if price >= 1:
            return round(price, 4)
        # Coin nhỏ: tính số chữ số thập phân cần thiết
        first_sig = -math.floor(math.log10(abs(price)))
        return round(price, first_sig + 4)

    def _fmt(self, value) -> str:
        """Format giá trị để in log — trả về '—' nếu None."""
        if value is None:
            return "—"
        try:
            return str(self._round_price(float(value)))
        except (TypeError, ValueError):
            return str(value)


# ══════════════════════════════════════════════════════════════════════════════
# KHỐI THỰC THI KIỂM THỬ ĐỘC LẬP
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )

    try:
        from core.coin_filter import get_avg_vola_24h, live_data_map, update_live_data
        print("⚡ Đang cập nhật live_data_map...")
        update_live_data()
        avg_vola = get_avg_vola_24h()
    except Exception as e:
        print(f"⚠️ Không thể load coin_filter: {e}. Dùng giá trị mock.")
        avg_vola = 8.5
        live_data_map = {}

    # Danh sách test symbols
    test_cases = [
        {"symbol": "FETUSDT",  "coin_vola_24h": live_data_map.get("FETUSDT", {}).get("daily_vola", 9.2)},
        {"symbol": "ENAUSDT",  "coin_vola_24h": live_data_map.get("ENAUSDT", {}).get("daily_vola", 7.1)},
        {"symbol": "SOLUSDT",  "coin_vola_24h": live_data_map.get("SOLUSDT", {}).get("daily_vola", 6.5)},
    ]

    service = EntryCalculatorService()

    print("\n" + "=" * 80)
    print("🎯 ĐỘNG CƠ 2 — PULLBACK SNIPER | EntryCalculatorService Test")
    print(f"   avg_vola_24h (toàn thị trường): {avg_vola:.2f}%")
    print("=" * 80)

    for tc in test_cases:
        symbol = tc["symbol"]
        print(f"\n📌 Đang xử lý: {symbol} (vola: {tc['coin_vola_24h']:.1f}%)")
        print("-" * 60)
        try:
            result = service.calculate({
                "symbol":             symbol,
                "timeframe_entry":    "15m",
                "timeframe_macro":    "4h",
                "engine_type":        "SNIPER_SPOT",
                "capital_allocation": 0.30,
                "avg_vola_24h":       avg_vola,
                "coin_vola_24h":      tc["coin_vola_24h"],
            })
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except EntryCalculatorServiceError as e:
            print(f"🚫 REJECTED: {e}")
        except Exception as e:
            print(f"❌ LỖI HỆ THỐNG: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 80)
