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

# ─── Tham Số Cấu Hình ──────────────────────────────────────────────────────────
PP_MIN_VOL_USDT = 2_000_000   # Vol 24h tối thiểu hạ xuống 2M USDT
PP_MIN_BOUNCES  = 4.0         # Tần suất tối thiểu: tăng lên 4 để bù trừ range hạ xuống
# [DC5-v3] Hạ sàn xuống 3.5% — bắt pingpong trong thị trường sideways/chậm
# Bù trừ bằng PP_MIN_BOUNCES=4 (cần nhiều nhịp hơn để xác nhận pattern)
PP_MIN_RANGE    = 3.5         # Biên độ tối thiểu (%) — phí Maker/Taker ~0.1%x2 + slippage ~0.1%
                               # Range 3.5% ≈ TP thực ~3.1% sau phí — vẫn có lãi
PP_TOP_N        = 200         # Số mã quét tối đa
PP_WORKERS      = 20
PP_RESULT_TOP   = 5           # Lấy Top 5

class GridPingpongScorer:
    """
    Động Cơ Đánh Giá & Sinh Tín Hiệu Grid Pingpong.
    """

    @staticmethod
    def get_candidates(live_data_map: dict, top_n: int = PP_TOP_N,
                       priority_symbols: list = None) -> list:
        """
        Xây dựng pool ứng viên cho DC5.
        priority_symbols: danh sách mã ưu tiên từ watchlist bảng 1
          (đã qua F2 Trend nội bộ — đang trên cấu trúc MA99 4H/1D).
          Đưa lên đầu để DC5 quét trước, bổ sung top spread cho đủ pool.
        """
        # ── Bước 1: Ưu tiên watchlist bảng 1 (đã xác nhận trend tốt) ──
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

        # ── Bước 2: Bổ sung top spread từ live_data_map ──
        spread_candidates = []
        for symbol, info in live_data_map.items():
            if not symbol.endswith('USDT') or symbol in EXCLUDE: continue
            if symbol in priority_set: continue  # Đã có trong priority
            quote_vol = info.get('quote_vol', 0)
            if quote_vol < PP_MIN_VOL_USDT: continue
            high = info.get('high', 0)
            low = info.get('low', 0)
            spread = ((high - low) / low * 100) if low > 0 else 0
            spread_candidates.append((symbol, quote_vol, spread))

        spread_candidates.sort(key=lambda x: x[2], reverse=True)
        supplement = [(s[0], s[1]) for s in spread_candidates]

        # Ưu tiên chạy trước, sau đó bổ sung đến top_n
        combined = priority_list + supplement
        return combined[:top_n]

    @staticmethod
    def analyze_symbol(args) -> dict:
        symbol, quote_vol = args
        try:
            time.sleep(0.03)

            # ── Hard Filter 0: Anti-Pump nhẹ riêng cho DC5 ──────────────────────
            # DC5 cần mã dao động mạnh (≥5%) — những mã này có lịch sử râu/bơm tự nhiên.
            # [DC5-v2] Chỉ chặn Climax CỰC ĐOAN (>8x ATR) — pump thẳng đứng 1 ngày.
            # Không chặn theo dump_ratio hay râu xả: MA99 Trend Filter đã bảo vệ.
            df_1d = get_klines_live(symbol, '1d', limit=20)

            if df_1d is not None and len(df_1d) >= 14:
                _col_map = {}
                for _t in ['high', 'low', 'open', 'close']:
                    for _c in df_1d.columns:
                        if _c.lower() == _t: _col_map[_t] = _c; break
                if len(_col_map) == 4:
                    _dfc = df_1d.tail(14).copy().rename(columns={v: k for k, v in _col_map.items()})
                    for _c in ['high', 'low', 'open', 'close']:
                        _dfc[_c] = pd.to_numeric(_dfc[_c], errors='coerce')
                    _tr0 = abs(_dfc['high'] - _dfc['low'])
                    _tr1 = abs(_dfc['high'] - _dfc['close'].shift(1))
                    _tr2 = abs(_dfc['low']  - _dfc['close'].shift(1))
                    _atr14 = pd.concat([_tr0, _tr1, _tr2], axis=1).max(axis=1).mean()
                    _is_extreme = False
                    if _atr14 > 0:
                        for _, _row in _dfc.tail(5).iterrows():
                            _body = abs(float(_row['close']) - float(_row['open']))
                            # Climax cực đoan: thân nến > 8x ATR — pump thẳng đứng 1 phiên
                            if _body > 8.0 * _atr14:
                                _is_extreme = True; break
                    if _is_extreme:
                        return {
                            '_rejected': True,
                            'symbol': symbol,
                            'danger_score': 100.0,
                            'dump_pct': 0.0,
                            'reason': 'DC5 Anti-Pump: Climax cuc doan (>8x ATR)'
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
            # [DC5-v2] Hạ timeframe: quét hộp Pingpong trên 30 nến 1H (thay vì 48 nến 4H)
            # → len lỏi vùng tích lũy ngắn hạn trong ngày, tăng tần suất ra vào lệnh
            df_recent = df_1h.iloc[-30:]
            ma25_recent = ma25.iloc[-30:]
            
            n_up = 0
            n_down = 0
            swing_ranges = []   # Toàn bộ swing (dùng cho avg_range)
            swings_up   = []    # Swing từ đáy lên đỉnh (pump — bot SPOT bán được đỉnh → ít nguy hiểm)
            swings_down = []    # Swing từ đỉnh xuống đáy (dump — bot mua vào đáy → rủi ro hơn)
            
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
                        if l < m: # Cross down — swing Đỉnh→Đáy (dump)
                            current_state = -1
                            new_extreme = l
                            sw = abs(last_extreme - new_extreme) / new_extreme * 100
                            swing_ranges.append(sw)
                            swings_down.append(sw)   # Spike XUỐNG: bot mua vào → rủi ro
                            n_up += 1
                            last_extreme = new_extreme
                    elif current_state == -1:
                        if l < last_extreme:
                            last_extreme = l
                        if h > m: # Cross up — swing Đáy→Đỉnh (pump)
                            current_state = 1
                            new_extreme = h
                            sw = abs(new_extreme - last_extreme) / last_extreme * 100
                            swing_ranges.append(sw)
                            swings_up.append(sw)     # Spike LÊN: bot bán được đỉnh → có lợi
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
            # [DC5-v2] Giữ cứng sàn 5% mọi nhóm vol — dưới 5% sau phí+slippage gần như hoà vốn
            required_range = PP_MIN_RANGE  # 5.0% — cố định, không phân biệt vol
            if avg_range < required_range:
                return None
                
            # Hard Filter 4: Lọc Tần Suất Bắt Buộc
            if (n_cycles_24h * 2.0) < PP_MIN_BOUNCES:
                return None

            # ── Hard Filter 5: Độ Mượt Swing (Swing Outlier Guard) ───────────────
            # Bắt bẫy NEWT-style: 1 cú pump kim tiêm tạo ra 1 swing cực đại ảo,
            # ── Hard Filter 5: Độ Mượt Swing — Phân Biệt Hướng (Directional Outlier Guard) ──
            # Với SPOT Grid: spike LÊN (pump) → bot BÁN được đỉnh → CÓ LỢI
            #               spike XUỐNG (dump) → bot MUA vào đáy → RỦI RO nếu không hồi
            # → Phân biệt ngưỡng: dump outlier nghiêm hơn pump outlier
            if len(swing_ranges) >= 3:
                _sw_med = float(np.median(np.array(swing_ranges)))

                # (a) Dump outlier — spike XUỐNG: ngưỡng nghiêm 2.5x
                if swings_down and _sw_med > 0:
                    _dump_max = float(max(swings_down))
                    if _dump_max / _sw_med > 2.5:
                        return None  # Bẫy dump kim tiêm — bot sẽ mua vào đáy không hồi

                # (b) Pump outlier — spike LÊN: ngưỡng lỏng 4.0x (bot bán được đỉnh → ok)
                if swings_up and _sw_med > 0:
                    _pump_max = float(max(swings_up))
                    if _pump_max / _sw_med > 4.0:
                        return None  # Pump quá cực đoan — khó tái lập, hộp pingpong ảo

                # (c) CV tổng thể: dao động quá loạn (áp dụng toàn bộ swing_ranges)
                _sw_std = float(np.array(swing_ranges).std())
                if _sw_med > 0 and (_sw_std / _sw_med) > 0.9:
                    return None  # Pingpong không đều — không đủ độ mượt để lướt sóng
            
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
    def run_scan(cls, live_data_map: dict, top_n: int = PP_TOP_N,
                 result_top: int = PP_RESULT_TOP,
                 priority_symbols: list = None) -> list:
        candidates = cls.get_candidates(live_data_map, top_n=top_n,
                                        priority_symbols=priority_symbols)
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
