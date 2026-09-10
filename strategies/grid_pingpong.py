"""
Module: grid_pingpong.py
Dự án: BinaC4
Mục đích: Chiến lược Grid Pingpong
          Chạy bot Spot Grid tinh gọn, tập trung vào nhịp dao động qua lại (Ping Pong) quanh trục giữa (Center Line = MA25).
          Đầu ra sinh tín hiệu cấu hình bot lưới, không trực tiếp đặt lệnh.
"""

import time
import pandas as pd
import numpy as np
from scipy import stats
from concurrent.futures import ThreadPoolExecutor

from core.coin_filter import get_klines_live, EXCLUDE
from core.grid_calculator import GridCalculator
from core.anti_pump_filter import apply_anti_pump_filter

# ─── Tham Số Cấu Hình ──────────────────────────────────────────────────────────
PP_MIN_VOL_USDT = 2_000_000   # Vol 24h tối thiểu hạ xuống 2M USDT
PP_MIN_BOUNCES  = 3.0         # Tần suất tối thiểu 24h
PP_MIN_RANGE    = 1.5         # Biên độ tối thiểu (%)
PP_TOP_N        = 200         # Số mã quét tối đa
PP_WORKERS      = 20
PP_RESULT_TOP   = 5           # Lấy Top 5

class GridPingpongScorer:
    """
    Động Cơ Đánh Giá & Sinh Tín Hiệu Grid Pingpong.
    """

    @staticmethod
    def get_candidates(live_data_map: dict, top_n: int = PP_TOP_N) -> list:
        candidates = []
        for symbol, info in live_data_map.items():
            if not symbol.endswith('USDT') or symbol in EXCLUDE:
                continue
            quote_vol = info.get('quote_vol', 0)
            if quote_vol < PP_MIN_VOL_USDT:
                continue
            
            high = info.get('high', 0)
            low = info.get('low', 0)
            spread = ((high - low) / low * 100) if low > 0 else 0
            
            candidates.append((symbol, quote_vol, spread))
            
        # Sort by 24h Spread (mã dao động mạnh nhất lên đầu)
        candidates.sort(key=lambda x: x[2], reverse=True)
        # Chỉ return (symbol, quote_vol) cho phân tích
        return [(s[0], s[1]) for s in candidates[:top_n]]

    @staticmethod
    def analyze_symbol(args) -> dict:
        symbol, quote_vol = args
        try:
            time.sleep(0.03)

            # ── Hard Filter 0: Anti-Pump/Dump (Bộ Lọc Bơm Xả 1D) ───────────────
            # Lấy nến 1D trước — chi phí thấp, loại bỏ sớm các mã nguy hiểm
            # trước khi tốn tài nguyên tính toán các chỉ báo phức tạp hơn.
            df_1d = get_klines_live(symbol, '1d', limit=20)
            close_live_ticker = 0.0  # Placeholder, sẽ được gán lại từ 1H bên dưới

            # Tạm dùng close nến 1D cuối để filter sơ bộ (giá realtime sẽ dùng sau)
            if df_1d is not None and len(df_1d) >= 14:
                _close_1d = pd.to_numeric(df_1d['Close'], errors='coerce').iloc[-1]
                pump_result = apply_anti_pump_filter(df_1d, float(_close_1d))
                if not pump_result['is_safe']:
                    # Trả về dict rejected để run_scan thu thập vào blacklist
                    return {
                        '_rejected': True,
                        'symbol': symbol,
                        'danger_score': pump_result['danger_score'],
                        'dump_pct': pump_result['dump_pct'],
                        'reason': pump_result['reason']
                    }

            # Lấy data 1H (100 nến để tính MA25 và MA99)
            df_1h = get_klines_live(symbol, '1h', limit=100)
            if df_1h is None or len(df_1h) < 100:
                return None
            
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df_1h[col] = pd.to_numeric(df_1h[col], errors='coerce')
                
            close_live = float(df_1h['Close'].iloc[-1])

            # [CHỐNG NHIỄU] Dùng Trimmed Mean (cắt 10% đầu+đuôi) thay SMA thuần.
            # Lý do: SMA bị kéo lệch bởi 1 cây nến bơm xả dựng đứng (kim tiêm).
            # Trimmed Mean loại bỏ ~2-3 nến cực đoan trước khi tính trục,
            # giúp center_line bám đúng vùng tích lũy thực tế của thị trường.
            ma25 = df_1h['Close'].rolling(window=25).apply(
                lambda x: stats.trim_mean(x, proportiontocut=0.1), raw=True
            )
            ma99 = df_1h['Close'].rolling(window=99).mean()
            ma99_val = float(ma99.iloc[-1])
            
            # Hard Filter 2: Trend Filter (Chống Dao Rơi)
            if close_live < ma99_val:
                return None
            
            # ── 1. Tính toán Bounces & Avg Range ────────────────────────────────
            # Phân tích trong 48 nến gần nhất (2 ngày) để xem độ ping pong
            df_recent = df_1h.iloc[-48:]
            ma25_recent = ma25.iloc[-48:]
            
            n_up = 0
            n_down = 0
            swing_ranges = []
            
            # State machine đơn giản để đếm số nhịp Ping Pong hoàn chỉnh
            # Nhịp ping pong là: tạo Swing High (High > MA25) -> xuyên qua MA25 -> tạo Swing Low (Low < MA25)
            # hoặc ngược lại.
            current_state = None  # 1: Above, -1: Below
            last_extreme = None   # High if state=1, Low if state=-1
            
            for i in range(len(df_recent)):
                h = float(df_recent['High'].iloc[i])
                l = float(df_recent['Low'].iloc[i])
                m = float(ma25_recent.iloc[i])
                
                if pd.isna(m): continue
                
                if current_state is None:
                    if h > m and l > m:
                        current_state = 1
                        last_extreme = h
                    elif h < m and l < m:
                        current_state = -1
                        last_extreme = l
                else:
                    if current_state == 1:
                        if h > last_extreme:
                            last_extreme = h
                        if l < m: # Cross down
                            # Tính swing range của nhịp vừa rồi
                            current_state = -1
                            new_extreme = l
                            swing_ranges.append(abs(last_extreme - new_extreme) / new_extreme * 100)
                            n_up += 1
                            last_extreme = new_extreme
                    elif current_state == -1:
                        if l < last_extreme:
                            last_extreme = l
                        if h > m: # Cross up
                            current_state = 1
                            new_extreme = h
                            swing_ranges.append(abs(new_extreme - last_extreme) / last_extreme * 100)
                            n_down += 1
                            last_extreme = new_extreme
                            
            # Lọc các nhịp trong 24 nến gần nhất để tính bounce rate cho 24h
            # Tuy nhiên, ta đã quét 48 nến để có context, chia đôi số bounce để xấp xỉ 24h
            n_cycles_24h = min(n_up, n_down) / 2.0 
            
            # Dùng median thay mean để tránh avg_range bị kéo ảo bởi
            # các cú pump/dump đột biến (outlier swing 15-17%) trong 48 nến.
            # Median phản ánh đúng biên độ dao động THỰC TẾ của đa số nhịp.
            avg_range = float(np.median(swing_ranges)) if swing_ranges else 0.0
            
            # P_core: Điểm Lõi Dao Động
            p_core = n_cycles_24h * avg_range
            
            # P_support: Điểm Nền Cứng & Test Hỗ Trợ
            # Đếm số nến test rút chân trên MA25 trong 48 nến
            test_candles_wicks = []
            for i in range(len(df_recent)):
                o = float(df_recent['Open'].iloc[i])
                c = float(df_recent['Close'].iloc[i])
                l = float(df_recent['Low'].iloc[i])
                m = float(ma25_recent.iloc[i])
                
                # Test support: Nhúng xuống chạm/xuyên MA25 nhưng đóng cửa trên MA25
                if l <= m and c > m:
                    lower_wick_pct = (min(o, c) - l) / l * 100
                    test_candles_wicks.append(lower_wick_pct)
            
            n_test = len(test_candles_wicks)
            w_lower_avg = np.mean(test_candles_wicks) if n_test > 0 else 0.0
            p_support = n_test * w_lower_avg
            
            # P_risk: Điểm Phạt Chênh Vênh
            ma25_val = float(ma25.iloc[-1])
            delta_base = (ma25_val - ma99_val) / ma99_val * 100
            p_risk = max(0.0, delta_base - 6.0)
            
            # V_tiebreaker: Hệ Số Phá Vỡ Trùng Lặp
            v_tiebreaker = quote_vol / 10_000_000_000.0
            
            # TỔNG ĐIỂM (V2)
            pingpong_score = p_core + p_support - p_risk + v_tiebreaker
            
            
            # Hard Filter 3: Lọc Biên Độ Tối Thiểu
            # Nhóm 2M - 10M yêu cầu biên độ >= 4.0%
            required_range = 4.0 if quote_vol < 10_000_000 else PP_MIN_RANGE
            if avg_range < required_range:
                return None
                
            # Hard Filter 4: Lọc Tần Suất Bắt Buộc
            if (n_cycles_24h * 2.0) < PP_MIN_BOUNCES:
                return None
            
            if pingpong_score >= 30:
                rank = "Hạng S - Siêu phẩm"
            elif pingpong_score >= 15:
                rank = "Hạng A - Đạt chuẩn"
            else:
                rank = "Hạng B - Đạt chuẩn (Thấp)"
                
            # ── 2. Các Mốc Giá Quản Trị Rủi Ro ────────────────────────────────
            # Stoploss: MIN(MA99, Swing Low gần nhất)
            recent_lows = df_1h['Low'].iloc[-24:] # Swing low trong 24h
            swing_low_24h = float(recent_lows.min())
            stop_loss = min(ma99_val, swing_low_24h)
            
            # Trigger Price: Chạm vùng MA25
            ma25_val = float(ma25.iloc[-1])
            trigger_price = ma25_val
            
            # Grid Parameters thông qua GridCalculator
            calc = GridCalculator()
            grid_params = calc.calculate_grid_pingpong(close_live, ma25_val, avg_range, quote_vol)
            
            return {
                'symbol': symbol,
                'price': close_live,
                'pingpong_score': round(pingpong_score, 4),
                'bounces_24h': round(n_cycles_24h, 1),
                'avg_range': round(avg_range, 2),
                'rank': rank,
                'components': {
                    'p_core': round(p_core, 2),
                    'p_support': round(p_support, 2),
                    'p_risk': round(p_risk, 2),
                    'n_test': n_test
                },
                'grid_setup': {
                    'center_line': round(ma25_val, 5),
                    'upper_bound': grid_params['upper_bound'],
                    'lower_bound': grid_params['lower_bound'],
                    'grids': grid_params['num_grids'],
                    'trailing_up': True,
                    'trigger_price': round(trigger_price, 5),
                    'stop_loss_sell_all': round(stop_loss, 5)
                }
            }
        except Exception as e:
            print(f"Error {symbol}: {e}")
            return None

    @classmethod
    def run_scan(cls, live_data_map: dict, top_n: int = PP_TOP_N, result_top: int = PP_RESULT_TOP) -> list:
        candidates = cls.get_candidates(live_data_map, top_n=top_n)
        if not candidates:
            return []

        safe_results    = []  # Mã vượt qua tất cả filter → vào pool lưới
        pump_blacklist  = []  # Mã bị Anti-Pump Filter từ chối

        with ThreadPoolExecutor(max_workers=PP_WORKERS) as pool:
            for res in pool.map(cls.analyze_symbol, candidates):
                if res is None:
                    continue
                if res.get('_rejected'):
                    # Gom vào blacklist để in log cảnh báo
                    pump_blacklist.append(res)
                else:
                    safe_results.append(res)

        # ── In Danh Sách Đen theo Danger Score giảm dần ─────────────────────────
        if pump_blacklist:
            pump_blacklist.sort(key=lambda x: x['danger_score'], reverse=True)
            _bl_str = ", ".join(
                f"{item['symbol'].replace('USDT', '')} ({item['danger_score']:.0f})"
                for item in pump_blacklist
            )
            print(f"🚨 [DC5] Từ chối bơm/xả: {_bl_str}")

        safe_results.sort(key=lambda x: x['pingpong_score'], reverse=True)
        return safe_results[:result_top]
