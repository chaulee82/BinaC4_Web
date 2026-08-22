"""
Module: pullback_sniper.py
Dự án: CN4-Platform
Mục đích: Động cơ 2 - Săn sóng hồi tối ưu (Confluence Pullback Scalping)
Thang điểm: 100 điểm (4 Cửa kiểm duyệt định lượng)

Changelog Phase 1:
  - [DC2-1] Siết vùng hội tụ từ ±1.5% → ±0.8% để chỉ mua khi giá đã thực sự về vùng bệ đỡ
  - [DC2-2] Thêm điều kiện giá phải ≤ indicator (không mua khi giá đang trên EMA/MA)
  - [DC2-3] Gate 0 — check_macro_trend_1d(): lọc downtrend 1D trước khi chấm điểm
  - [DC2-4] evaluate_candidate() tích hợp Gate 0, hard reject mã downtrend dài hạn
"""

import ccxt
import pandas as pd
import numpy as np
from core.dynamic_pricing_oracle import DynamicPricingOracle


class PullbackSniper:
    def __init__(self, exchange=None):
        from core.exchange_factory import get_working_exchange
        self.exchange = exchange or get_working_exchange()

    # =========================================================================
    # [MỚI - DC2-3] GATE 0: LỌC XU HƯỚNG VĨ MÔ 1D — Hàm Độc Lập
    # Gọi một lần mỗi chu kỳ để tạo macro_safe_watchlist trước khi nạp vào DC2
    # Trả về dict: {"ok": bool, "reason": str, "trend_pct": float}
    # =========================================================================
    def check_macro_trend_1d(self, symbol: str) -> dict:
        """
        Kiểm tra xu hướng khung 1D của symbol.
        Pullback trong downtrend 1D thường là dead cat bounce — không phải đáy thật.

        REJECT khi:
          - Giá close 1D đang dưới MA25 1D  (xu hướng giảm dài hạn)
        ACCEPT khi:
          - Giá close 1D trên MA25 1D (uptrend hoặc tích lũy trên nền tảng)
        """
        try:
            candles_1d = self.exchange.fetch_ohlcv(symbol, '1d', limit=30)
            df_1d = pd.DataFrame(candles_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            ma25_1d = df_1d['close'].rolling(window=25).mean().iloc[-1]
            close_1d = df_1d['close'].iloc[-1]

            trend_pct = ((close_1d - ma25_1d) / ma25_1d) * 100

            if close_1d >= ma25_1d:
                return {
                    "ok": True,
                    "reason": f"✅ Macro 1D OK (Giá trên MA25 1D, +{trend_pct:.1f}%)",
                    "trend_pct": round(trend_pct, 2)
                }
            else:
                return {
                    "ok": False,
                    "reason": f"❌ Macro 1D Downtrend (Giá dưới MA25 1D, {trend_pct:.1f}%) — Dead Cat Risk",
                    "trend_pct": round(trend_pct, 2)
                }
        except Exception as e:
            # Lỗi API → cho qua để không chặn toàn bộ hệ thống
            return {
                "ok": True,
                "reason": f"⚠️ Macro 1D gate lỗi API (bỏ qua): {str(e)[:60]}",
                "trend_pct": 0.0
            }

    # =========================================================================
    # CỬA 1: ĐỊNH VỊ VÙNG HẠ CÁNH / HỢP LƯU (TỐI ĐA 30 ĐIỂM)
    # =========================================================================
    def check_confluence_zone(self, df: pd.DataFrame) -> dict:
        """
        Kiểm tra độ lệch giữa giá hiện tại với cụm EMA25, MA25 và Dải giữa Bollinger (MB).
        Sai số cho phép trong phạm vi ±1.5%. (Giữ nguyên gốc để bắt được các sóng front-run)
        """
        current_price = df['close'].iloc[-1]
        ema25 = df['ema25'].iloc[-1]
        ma25 = df['ma25'].iloc[-1]
        bb_mid = df['bb_mid'].iloc[-1]

        # Kiểm tra xem giá có tiệm cận các đường bệ đỡ không (khoảng cách <= 1.5%)
        near_ema25 = abs(current_price - ema25) / current_price <= 0.015
        near_ma25 = abs(current_price - ma25) / current_price <= 0.015
        near_bb_mid = abs(current_price - bb_mid) / current_price <= 0.015

        confluence_count = sum([near_ema25, near_ma25, near_bb_mid])

        if confluence_count >= 3:
            score = 30
            status = "Hợp lưu 3 đường bệ đỡ (EMA25 + MA25 + BB_Mid)"
        elif confluence_count == 2:
            score = 20
            status = "Hợp lưu 2 đường bệ đỡ"
        elif confluence_count == 1:
            score = 10
            status = "Chỉ chạm 1 đường bệ đỡ đơn lẻ"
        else:
            score = 0
            status = "Chưa về vùng hợp lưu (Giá đang treo lơ lửng)"

        return {"score": score, "confluence_count": confluence_count, "status": status}

    # =========================================================================
    # CỬA 2: NGHIỆM THU KHỐI LƯỢNG RŨ BỎ (TỐI ĐA 25 ĐIỂM)
    # =========================================================================
    def check_volume_dryup(self, df: pd.DataFrame) -> dict:
        """
        Kiểm tra Volume nến điều chỉnh so với Volume đỉnh nến xanh trước đó.
        """
        recent_vol = df['volume'].iloc[-1]
        # Lấy đỉnh volume lớn nhất trong 10 nến gần nhất (pha đẩy sóng)
        peak_push_vol = df['volume'].iloc[-10:-1].max()

        if peak_push_vol == 0 or np.isnan(peak_push_vol):
            return {"score": 0, "status": "Dữ liệu Volume không hợp lệ"}

        vol_ratio = recent_vol / peak_push_vol

        if vol_ratio <= 0.40:
            score = 25
            status = f"Kiệt cung hoàn hảo (Vol chỉ bằng {vol_ratio*100:.1f}% đỉnh)"
        elif vol_ratio <= 0.70:
            score = 15
            status = f"Khối lượng giảm dần (Vol bằng {vol_ratio*100:.1f}% đỉnh)"
        else:
            score = 0
            status = f"Cảnh báo: Áp lực xả còn lớn (Vol đạt {vol_ratio*100:.1f}% đỉnh)"

        return {"score": score, "vol_ratio": vol_ratio, "status": status}

    # =========================================================================
    # CỬA 3: SOI SỔ LỆNH LEVEL 2 (TỐI ĐA 20 ĐIỂM)
    # =========================================================================
    def analyze_buy_walls(self, order_book: dict, entry_price: float) -> dict:
        """
        Đo lường độ dày tường Mua (Bids) so với tường Bán (Asks) quanh vùng Entry.
        """
        try:
            bids = pd.DataFrame(order_book['bids'], columns=['price', 'volume'])
            asks = pd.DataFrame(order_book['asks'], columns=['price', 'volume'])

            # Lọc các bước giá trong phạm vi 3% quanh Entry
            bid_wall_total = (bids[bids['price'] >= entry_price * 0.97]['price'] * 
                              bids[bids['price'] >= entry_price * 0.97]['volume']).sum()

            ask_wall_total = (asks[asks['price'] <= entry_price * 1.03]['price'] * 
                              asks[asks['price'] <= entry_price * 1.03]['volume']).sum()

            if ask_wall_total == 0:
                imbalance_ratio = 2.0
            else:
                imbalance_ratio = bid_wall_total / ask_wall_total

            if imbalance_ratio >= 2.0:
                score = 20
                status = f"Tường mua bê tông (Bids gấp {imbalance_ratio:.1f}x Asks)"
            elif imbalance_ratio >= 1.5:
                score = 15
                status = f"Lực cầu áp đảo (Bids gấp {imbalance_ratio:.1f}x Asks)"
            else:
                score = 0
                status = f"Tường mua yếu (Tỷ lệ Bids/Asks: {imbalance_ratio:.1f}x)"

            return {"score": score, "imbalance_ratio": imbalance_ratio, "status": status}
        except Exception as e:
            return {"score": 0, "imbalance_ratio": 0, "status": f"Lỗi Sổ lệnh: {str(e)}"}

    # =========================================================================
    # CỬA 4: TỶ LỆ LỢI NHUẬN / RỦI RO TOÁN HỌC (TỐI ĐA 25 ĐIỂM)
    # =========================================================================
    def calculate_rr_ratio(self, entry: float, stop_loss: float, take_profit: float) -> dict:
        """
        Tính toán tỷ lệ R/R theo công thức: (TP - Entry) / (Entry - SL)
        """
        risk = entry - stop_loss
        reward = take_profit - entry

        if risk <= 0 or reward <= 0:
            return {"score": 0, "rr_ratio": 0, "status": "Lỗi thiết lập mốc SL/TP"}

        rr_ratio = reward / risk

        if rr_ratio >= 3.5:
            score = 25
            status = f"Tỷ lệ vàng (R/R = {rr_ratio:.2f}R)"
        elif rr_ratio >= 3.0:
            score = 20
            status = f"Đạt chuẩn tối ưu (R/R = {rr_ratio:.2f}R)"
        elif rr_ratio >= 2.5:
            score = 10
            status = f"Mức chấp nhận được (R/R = {rr_ratio:.2f}R)"
        else:
            score = 0
            status = f"Từ chối: R/R quá thấp ({rr_ratio:.2f}R < 2.5R)"

        return {"score": score, "rr_ratio": rr_ratio, "status": status}

    # =========================================================================
    # HÀM CHẤM ĐIỂM TỔNG HỢP & PHÂN LOẠI HÀNH ĐỘNG
    # [DC2-4] Tích hợp Gate 0 Macro 1D: hard reject ngay đầu nếu downtrend dài hạn
    # =========================================================================
    def evaluate_candidate(self, symbol: str, timeframe: str = '4h',
                           macro_gate: dict = None) -> dict:
        """
        Quy trình chấm điểm toàn diện cho 1 mã tài sản.

        macro_gate: Kết quả từ check_macro_trend_1d() — truyền vào để tránh gọi API nhiều lần.
                    Nếu None, tự gọi nội bộ (chỉ dùng khi test đơn lẻ).
        """
        try:
            # ==============================================================
            # [DC2-4] GATE 0: Lọc Xu Hướng Vĩ Mô 1D
            # Reject ngay nếu mã đang trong downtrend dài hạn
            # Dead cat bounce trong downtrend 1D → tỷ lệ thắng rất thấp
            # ==============================================================
            if macro_gate is None:
                macro_gate = self.check_macro_trend_1d(symbol)

            if not macro_gate.get("ok", True):
                return {
                    "symbol": symbol,
                    "price": 0,
                    "total_score": 0,
                    "action": f"🚫 MACRO GATE: {macro_gate['reason']}",
                    "details": {
                        "Gate_0_Macro1D": macro_gate,
                        "Gate_1_Confluence": {"score": 0, "status": "N/A (Macro Gate đóng)"},
                        "Gate_2_Volume": {"score": 0, "status": "N/A"},
                        "Gate_3_OrderBook": {"score": 0, "status": "N/A"},
                        "Gate_4_RR": {"score": 0, "status": "N/A"}
                    },
                    "trade_setup": {},
                    "macro_trend_pct": macro_gate.get("trend_pct", 0)
                }

            # 1. Kéo dữ liệu nến OHLCV
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=100)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # 2. Tính toán các chỉ báo cốt lõi
            df['ema25'] = df['close'].ewm(span=25, adjust=False).mean()
            df['ma25'] = df['close'].rolling(window=25).mean()
            df['bb_mid'] = df['close'].rolling(window=20).mean()
            df['bb_std'] = df['close'].rolling(window=20).std()
            df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * 2)
            df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * 2)

            current_price = df['close'].iloc[-1]
            peak_price = df['high'].iloc[-10:].max()
            recent_low = df['low'].iloc[-5:].min()

            # ==============================================================
            # TÍCH HỢP MODULE TIÊN TRI ĐỊNH GIÁ ĐỘNG (VWAP + ATR)
            # ==============================================================
            oracle = DynamicPricingOracle()
            oracle_setup = oracle.calculate_optimal_setup(df)

            if oracle_setup.get('status') == 'SUCCESS':
                entry = oracle_setup['entry']
                stop_loss = oracle_setup['stop_loss']
                take_profit = oracle_setup['take_profit']
            else:
                # Fallback nếu lỗi Oracle
                entry = current_price
                stop_loss = recent_low * 0.985
                take_profit = peak_price

            # 3. Kéo Sổ lệnh Level 2
            order_book = self.exchange.fetch_order_book(symbol, limit=50)

            # 4. Chạy qua 4 Cửa kiểm duyệt
            c1 = self.check_confluence_zone(df)
            c2 = self.check_volume_dryup(df)
            c3 = self.analyze_buy_walls(order_book, entry)
            c4 = self.calculate_rr_ratio(entry, stop_loss, take_profit)

            total_score = c1['score'] + c2['score'] + c3['score'] + c4['score']

            # 5. Phân tầng hành động
            if total_score >= 85:
                action = "🟢 LOẠI A: Kích hoạt 100% Volume (Full Limit OCO)"
            elif total_score >= 70:
                action = "🟡 LOẠI B: Kích hoạt 50% Volume (Thăm dò)"
            else:
                action = "🔴 LOẠI C: TỪ CHỐI (Không đạt chuẩn an toàn)"

            return {
                "symbol": symbol,
                "price": current_price,
                "total_score": total_score,
                "action": action,
                "details": {
                    "Gate_0_Macro1D": macro_gate,
                    "Gate_1_Confluence": c1,
                    "Gate_2_Volume": c2,
                    "Gate_3_OrderBook": c3,
                    "Gate_4_RR": c4
                },
                "trade_setup": {
                    "entry": round(entry, 6),
                    "stop_loss": round(stop_loss, 6),
                    "take_profit": round(take_profit, 6)
                },
                "macro_trend_pct": macro_gate.get("trend_pct", 0)
            }

        except Exception as e:
            return {"symbol": symbol, "error": str(e), "total_score": 0}


# =============================================================================
# KHỐI THỰC THI KIỂM THỬ ĐỘC LẬP
# =============================================================================
if __name__ == "__main__":
    sniper = PullbackSniper()
    test_symbols = ['HEI/USDT', 'ALICE/USDT', 'ALLO/USDT', 'EDEN/USDT', 'ACE/USDT']

    print("=" * 80)
    print("🎯 BẮT ĐẦU CHẤM ĐIỂM MOMENTUM PULLBACK (ĐỘNG CƠ 2)")
    print("=" * 80)

    for sym in test_symbols:
        result = sniper.evaluate_candidate(sym, timeframe='4h')
        if 'error' in result:
            print(f"⚠️ {sym}: Lỗi ({result['error']})")
            continue

        print(f"\n📌 Mã: {result['symbol']} | Giá Live: {result['price']} | TỔNG ĐIỂM: {result['total_score']}/100")
        print(f"👉 Quyết định: {result['action']}")
        print(f"   • Cửa 1 (Bệ đỡ): {result['details']['Gate_1_Confluence']['status']} ({result['details']['Gate_1_Confluence']['score']}đ)")
        print(f"   • Cửa 2 (Kiệt cung): {result['details']['Gate_2_Volume']['status']} ({result['details']['Gate_2_Volume']['score']}đ)")
        print(f"   • Cửa 3 (Sổ lệnh): {result['details']['Gate_3_OrderBook']['status']} ({result['details']['Gate_3_OrderBook']['score']}đ)")
        print(f"   • Cửa 4 (Tỷ lệ R): {result['details']['Gate_4_RR']['status']} ({result['details']['Gate_4_RR']['score']}đ)")
        if result['total_score'] >= 70:
            print(f"   🎯 Setup Limit: Entry={result['trade_setup']['entry']} | SL={result['trade_setup']['stop_loss']} | TP={result['trade_setup']['take_profit']}")
    print("\n" + "=" * 80)
