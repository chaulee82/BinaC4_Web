"""
Module: grid_pingpong_uptrend.py
Dự án: BinaC4
Mục đích: Chiến lược Grid Pingpong Uptrend (Động cơ 5)
          Tập trung vào coin có cấu trúc tăng trưởng vĩ mô (Supertrend 4H xanh, EMA20 > EMA50).
"""

import time
import json
import pandas as pd
import numpy as np
import os
from scipy import stats
from concurrent.futures import ThreadPoolExecutor

from core.coin_filter import get_klines_live, EXCLUDE
from core.grid_calculator import GridCalculator
from core.indicator_engine import IndicatorEngine

# Load Config
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'settings.json')
try:
    with open(config_path, 'r') as f:
        settings = json.load(f)
    e5_cfg = settings.get('trading', {}).get('engine5', {})
    GRID_MIN = e5_cfg.get('grid_count_min', 12)
    GRID_MAX = e5_cfg.get('grid_count_max', 24)
    MACRO_BOX_RANGE_MIN = e5_cfg.get('macro_box_range_min', -20.0)
    MACRO_BOX_RANGE_MAX = e5_cfg.get('macro_box_range_max', -16.0)
    BOUNCE_THRESHOLD = e5_cfg.get('bounce_threshold', 1.69)
    RANGE_THRESHOLD = e5_cfg.get('range_threshold', 3.5)
    LOWER_BUFFER = e5_cfg.get('lower_price_buffer', 1.5)
    CLIMAX_PENALTY = e5_cfg.get('climax_penalty_threshold', 25.0)
except Exception:
    GRID_MIN = 12
    GRID_MAX = 24
    MACRO_BOX_RANGE_MIN = -20.0
    MACRO_BOX_RANGE_MAX = -16.0
    BOUNCE_THRESHOLD = 1.69
    RANGE_THRESHOLD = 3.5
    LOWER_BUFFER = 1.5
    CLIMAX_PENALTY = 25.0

PP_MIN_VOL_USDT = 2_000_000
PP_TOP_N = 200
PP_WORKERS = 20
PP_RESULT_TOP = 5

class GridPingpongUptrendScorer:
    """
    Động Cơ Đánh Giá & Sinh Tín Hiệu Grid Pingpong Uptrend.
    """
    indicator_engine = IndicatorEngine()

    @staticmethod
    def get_candidates(live_data_map: dict, top_n: int = PP_TOP_N,
                       priority_symbols: list = None) -> list:
        priority_set = set()
        priority_list = []
        if priority_symbols:
            for raw in priority_symbols:
                sym = raw.replace('/', '') if '/' in raw else raw
                if not sym.endswith('USDT'): sym = sym + 'USDT'
                if sym in EXCLUDE or sym not in live_data_map: continue
                info = live_data_map[sym]
                quote_vol = info.get('quote_vol', 0)
                if quote_vol < PP_MIN_VOL_USDT: continue
                priority_list.append((sym, quote_vol))
                priority_set.add(sym)

        spread_candidates = []
        for symbol, info in live_data_map.items():
            if not symbol.endswith('USDT') or symbol in EXCLUDE: continue
            if symbol in priority_set: continue
            quote_vol = info.get('quote_vol', 0)
            if quote_vol < PP_MIN_VOL_USDT: continue
            high = info.get('high', 0)
            low = info.get('low', 0)
            spread = ((high - low) / low * 100) if low > 0 else 0
            spread_candidates.append((symbol, quote_vol, spread))

        spread_candidates.sort(key=lambda x: x[2], reverse=True)
        supplement = [(s[0], s[1]) for s in spread_candidates]

        combined = priority_list + supplement
        return combined[:top_n]

    @classmethod
    def analyze_symbol(cls, args) -> dict:
        symbol, quote_vol = args
        try:
            time.sleep(0.03)

            # Lấy data 4H
            df_4h = get_klines_live(symbol, '4h', limit=100)
            if df_4h is None or len(df_4h) < 100:
                return None
            
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df_4h[col] = pd.to_numeric(df_4h[col], errors='coerce')
                
            # Đổi tên cột chuẩn cho indicator_engine
            df_4h_calc = df_4h.copy()
            df_4h_calc.rename(columns={'Open':'open', 'High':'high', 'Low':'low', 'Close':'close'}, inplace=True)
            
            ema20_4h = cls.indicator_engine.get_ema(df_4h_calc, 20).iloc[-1]
            ema50_4h = cls.indicator_engine.get_ema(df_4h_calc, 50).iloc[-1]
            st_4h = cls.indicator_engine.get_supertrend(df_4h_calc, period=10, multiplier=3.0)
            
            # BƯỚC 1: Sàng Lọc Vĩ Mô Khung 4H
            if ema20_4h <= ema50_4h or st_4h['direction'] != 1:
                return None
            
            hard_sl = st_4h['support']
            if not hard_sl:
                return None
                
            # Climax check 4H
            close_4h = df_4h_calc['close'].iloc[-1]
            dist_to_ema50 = (close_4h - ema50_4h) / ema50_4h * 100
            if dist_to_ema50 > 50.0:  # Hard reject nếu rướn quá phi lý (> 50%)
                return None

            # BƯỚC 2: Kiểm Tra Vi Mô & Lọc Đỉnh (1H)
            df_1h = get_klines_live(symbol, '1h', limit=100)
            if df_1h is None or len(df_1h) < 100:
                return None
            
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df_1h[col] = pd.to_numeric(df_1h[col], errors='coerce')
                
            df_1h_calc = df_1h.copy()
            df_1h_calc.rename(columns={'Open':'open', 'High':'high', 'Low':'low', 'Close':'close'}, inplace=True)
            
            rsi_1h = cls.indicator_engine.get_rsi(df_1h_calc, 14).iloc[-1]
            if rsi_1h >= 85.0:  # Đỉnh điểm hưng phấn -> nguy hiểm
                return None
                
            score_rsi = 5.0
            if rsi_1h > 65:
                score_rsi -= (rsi_1h - 65) * 0.5  # Bị phạt nếu quá mua nhẹ
            elif rsi_1h < 40:
                score_rsi -= (40 - rsi_1h) * 0.2  # Phạt nhẹ nếu RSI quá thấp bất thường
            score_rsi = max(-5.0, score_rsi)

            # BƯỚC 3: Đo lường Biên Độ Hộp Vĩ Mô
            close_live = float(df_1h['Close'].iloc[-1])
            ma25 = df_1h['Close'].rolling(window=25).apply(lambda x: stats.trim_mean(x, proportiontocut=0.1), raw=True)
            center_line = float(ma25.iloc[-1])
            
            dist_center_sl = (hard_sl - center_line) / center_line * 100
            
            # Lọc cực đoan: SL quá hẹp (> -5%) hoặc quá sâu (< -50%) thì bỏ
            if dist_center_sl > -5.0 or dist_center_sl < -50.0:
                return None
                
            # Chấm điểm khoảng cách hộp (Vùng vàng -16% đến -20% thưởng tối đa 10 điểm)
            score_box_range = 10.0
            if dist_center_sl > MACRO_BOX_RANGE_MAX:
                # Ví dụ: -10% lệch 6% so với -16%
                deviation = dist_center_sl - MACRO_BOX_RANGE_MAX
                score_box_range -= abs(deviation) * 1.5  # Phạt nặng hơn nếu SL mỏng
            elif dist_center_sl < MACRO_BOX_RANGE_MIN:
                # Ví dụ: -30% lệch 10% so với -20%
                deviation = MACRO_BOX_RANGE_MIN - dist_center_sl
                score_box_range -= abs(deviation) * 0.5  # Phạt nhẹ hơn nếu SL sâu (ít rủi ro hơn)
                
            score_box_range = max(-10.0, score_box_range)
                
            # BƯỚC 4: Đếm Vòng Đập Nhả
            df_recent = df_1h.iloc[-50:]
            ma25_recent = ma25.iloc[-50:]
            
            n_bounces = 0
            swing_ranges = []
            
            current_state = None  # 1: Above, -1: Below
            last_extreme = None
            
            for i in range(len(df_recent)):
                h = float(df_recent['High'].iloc[i])
                l = float(df_recent['Low'].iloc[i])
                c = float(df_recent['Close'].iloc[i])
                m = float(ma25_recent.iloc[i])
                
                if pd.isna(m): continue
                
                if current_state is None:
                    if c > m:
                        current_state = 1
                        last_extreme = h
                    elif c < m:
                        current_state = -1
                        last_extreme = l
                else:
                    if current_state == 1:
                        if h > last_extreme:
                            last_extreme = h
                        if c < m: # Cross down (Close below MA)
                            current_state = -1
                            new_extreme = l
                            sw = abs(last_extreme - new_extreme) / new_extreme * 100
                            swing_ranges.append(sw)
                            if sw >= BOUNCE_THRESHOLD:
                                n_bounces += 1
                            last_extreme = new_extreme
                    elif current_state == -1:
                        if l < last_extreme:
                            last_extreme = l
                        if c > m: # Cross up (Close above MA)
                            current_state = 1
                            new_extreme = h
                            sw = abs(new_extreme - last_extreme) / last_extreme * 100
                            swing_ranges.append(sw)
                            if sw >= BOUNCE_THRESHOLD:
                                n_bounces += 1
                            last_extreme = new_extreme
                            
            avg_range = float(np.median(swing_ranges)) if swing_ranges else 0.0
            
            # Bỏ hard filter biên độ 3.5%, chỉ chặn biên độ vô dụng (< 1.5%)
            if avg_range < 1.5:
                return None
                
            # Điểm Biên Độ (Range)
            # Khống chế sức mạnh của Range để nhường vị trí ưu tiên cho Tần suất (Frequency)
            score_range = (avg_range - RANGE_THRESHOLD) * 1.0
            score_range = min(10.0, max(-5.0, score_range))
                
            # BƯỚC 5: Chấm Điểm Tổng Hợp 3D
            # Tần suất (score_freq) + Gia tốc an toàn (score_accel) + Hộp (score_box_range) + RSI (score_rsi) + Biên độ (score_range)
            score_freq = n_bounces * 2.0
            
            # Điểm Gia Tốc An Toàn (Bám sát EMA50 được thưởng, rướn xa bị phạt)
            score_accel = 5.0
            if dist_to_ema50 > 15.0:
                score_accel -= (dist_to_ema50 - 15.0) * 1.5
            elif dist_to_ema50 < 5.0:
                score_accel += (5.0 - dist_to_ema50) * 0.5
            
            pingpong_score = score_freq + score_accel + score_box_range + score_rsi + score_range + (quote_vol / 10_000_000_000.0)
            
            if pingpong_score >= 30:
                rank = "Hạng S - Siêu phẩm"
            elif pingpong_score >= 15:
                rank = "Hạng A - Đạt chuẩn"
            else:
                rank = "Hạng B - Đạt chuẩn (Thấp)"

            calc = GridCalculator()
            grid_params = calc.calculate_grid_pingpong_uptrend(
                current_price=close_live, 
                center_line=center_line, 
                hard_sl=hard_sl, 
                buffer_pct=LOWER_BUFFER, 
                num_grids=GRID_MAX
            )

            return {
                'symbol': symbol,
                'price': close_live,
                'pingpong_score': round(pingpong_score, 4),
                'bounces_24h': round(n_bounces / 2.0, 1),
                'avg_range': round(avg_range, 2),
                'rank': rank,
                'components': {
                    'p_freq': round(score_freq, 2),
                    'p_accel': round(score_accel, 2),
                    'p_box': round(score_box_range, 2),
                    'p_rsi': round(score_rsi, 2),
                    'p_range': round(score_range, 2),
                    'n_test': n_bounces
                },
                'grid_setup': {
                    'center_line': round(center_line, 5),
                    'upper_bound': grid_params.get('upper_bound', 0),
                    'lower_bound': grid_params.get('lower_bound', 0),
                    'grids': grid_params.get('num_grids', GRID_MAX),
                    'trailing_up': True,
                    'trigger_price': round(center_line, 5),
                    'stop_loss_sell_all': round(hard_sl, 5)
                }
            }
        except Exception as e:
            print(f"Error {symbol}: {e}")
            return None

    @classmethod
    def run_scan(cls, live_data_map: dict, top_n: int = PP_TOP_N,
                 result_top: int = PP_RESULT_TOP,
                 priority_symbols: list = None) -> list:
        cls.indicator_engine.clear_cache()
        candidates = cls.get_candidates(live_data_map, top_n=top_n,
                                        priority_symbols=priority_symbols)
        if not candidates:
            return []

        safe_results = []
        with ThreadPoolExecutor(max_workers=PP_WORKERS) as pool:
            for res in pool.map(cls.analyze_symbol, candidates):
                if res is not None:
                    safe_results.append(res)

        safe_results.sort(key=lambda x: x['pingpong_score'], reverse=True)
        return safe_results[:result_top]
