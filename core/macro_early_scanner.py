import logging
import time
import pandas as pd
import numpy as np
from typing import List, Dict

from core.exchange_factory import get_working_exchange
from core.indicator_engine import IndicatorEngine

logger = logging.getLogger("CN4.MacroEarlyScanner")

class MacroEarlyScanner:
    def __init__(self):
        self.exchange = get_working_exchange()
        self.engine = IndicatorEngine()
        self.watchlist = []
        self.last_macro_update = 0
        self.MACRO_TTL = 12 * 3600  # 12 hours
        
        # 1D Cache per symbol
        self._1d_cache = {}

    def _fetch_1d_klines(self, symbol: str, limit: int = 180) -> pd.DataFrame:
        """Fetch 1D klines with TTL caching"""
        now = time.time()
        if symbol in self._1d_cache:
            cache_time, df = self._1d_cache[symbol]
            if now - cache_time < self.MACRO_TTL:
                return df
                
        try:
            from core.coin_filter import fetch_binance_api
            sym_api = symbol.replace('/', '')
            data = fetch_binance_api(f"/api/v3/klines?symbol={sym_api}&interval=1d&limit={limit}")
            if not data:
                return pd.DataFrame()
            candles = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in data]
            df = pd.DataFrame(candles, columns=['timestamp','open','high','low','close','volume'])
            self._1d_cache[symbol] = (now, df)
            return df
        except Exception as e:
            logger.error(f"Error fetching 1D data for {symbol}: {e}")
            return pd.DataFrame()

    def _fetch_15m_klines(self, symbol: str, limit: int = 288) -> pd.DataFrame:
        """Fetch 15M klines (no cache, always fresh)"""
        try:
            from core.coin_filter import fetch_binance_api
            sym_api = symbol.replace('/', '')
            data = fetch_binance_api(f"/api/v3/klines?symbol={sym_api}&interval=15m&limit={limit}")
            if not data:
                return pd.DataFrame()
            candles = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in data]
            df = pd.DataFrame(candles, columns=['timestamp','open','high','low','close','volume'])
            return df
        except Exception as e:
            logger.error(f"Error fetching 15M data for {symbol}: {e}")
            return pd.DataFrame()

    def build_darvas_box_1d(self, df: pd.DataFrame, lookback: int = 60) -> dict:
        """Xây dựng Hộp Darvas từ khung 1D"""
        if len(df) < lookback:
            df_lookback = df
        else:
            df_lookback = df.tail(lookback)
            
        if df_lookback.empty:
            return {}
            
        ceiling = float(df_lookback['high'].max())
        floor = float(df_lookback['low'].min())
        
        if floor <= 0: return {}
        
        amplitude = (ceiling - floor) / floor
        
        return {
            "floor": floor,
            "ceiling": ceiling,
            "mid": (ceiling + floor) / 2.0,
            "amplitude": amplitude
        }

    def scan_macro_1d(self, symbols: List[str]) -> List[Dict]:
        """Phần 1: Móng Vĩ Mô (Điều kiện cần - Khung 1D)"""
        results = []
        
        for sym in symbols:
            df = self._fetch_1d_klines(sym, 180)
            if df.empty or len(df) < 60:
                continue
                
            current_price = float(df['close'].iloc[-1])
            max_high_180d = float(df['high'].max())
            
            # 1. Độ Sâu Chiết Khấu (Drop 180D)
            drop_pct = (max_high_180d - current_price) / max_high_180d
            if not (0.60 <= drop_pct <= 0.90):
                continue
                
            # 2. Nền Nén Thời Gian (Darvas 1D 60 days)
            box = self.build_darvas_box_1d(df, 60)
            if not box or box["amplitude"] >= 0.30:
                continue
                
            # 3. MA99 Nằm Ngang (Độ dốc -1% đến +1% trong 15 ngày qua)
            if len(df) >= 100:
                ma99_series = self.engine.get_ma(df, 99)
                ma99_current = float(ma99_series.iloc[-1])
                ma99_past = float(ma99_series.iloc[-15]) if len(ma99_series) >= 15 else ma99_current
                slope_pct = (ma99_current - ma99_past) / ma99_past if ma99_past > 0 else 0
                if not (-0.01 <= slope_pct <= 0.01):
                    continue
            else:
                continue
                
            results.append({
                "symbol": sym,
                "current_price": current_price,
                "drop_pct": drop_pct,
                "box": box,
                "ma99_slope": slope_pct
            })
            
        # Lấy top 15 mã (ưu tiên theo độ nén chặt nhất)
        results.sort(key=lambda x: x["box"]["amplitude"])
        self.watchlist = results[:15]
        self.last_macro_update = time.time()
        return self.watchlist

    def scan_micro_15m(self) -> List[Dict]:
        """Phần 2: Tín Hiệu Ngòi Nổ Vi Mô (Điều kiện đủ - Khung 15M)"""
        results = []
        
        for item in self.watchlist:
            sym = item["symbol"]
            box = item["box"]
            df_15m = self._fetch_15m_klines(sym, 288)  # 3 days of 15M
            if df_15m.empty or len(df_15m) < 40:
                continue
                
            df_last_40 = df_15m.tail(40)
            
            # Calculate MA20 Vol
            df_last_40 = df_last_40.copy()
            df_last_40['ma20_vol'] = df_last_40['volume'].rolling(20).mean()
            
            # Tính BB Squeeze & ATR 14
            bb = self.engine.get_bollinger_bands(df_15m, 20, 2.0)
            bb_bandwidth = (bb['upper'].iloc[-1] - bb['lower'].iloc[-1]) / bb['lower'].iloc[-1]
            
            atr = self.engine.get_atr(df_15m, 14)
            atr_current = float(atr.iloc[-1])
            atr_past = float(atr.iloc[-3])
            atr_decreasing = atr_current < atr_past
            
            # Điều kiện Micro Squeeze (BB Bandwidth < 2.5% + ATR decreasing)
            is_squeeze = bb_bandwidth < 0.025 and atr_decreasing
            
            # Tìm Pocket Pivots & Micro No Supply trong 40 nến
            pocket_pivots_count = 0
            micro_no_supply_count = 0
            
            # Lặp qua 40 nến (bo qua cac nen nan vi chua du ma20_vol)
            for i in range(len(df_last_40)):
                row = df_last_40.iloc[i]
                if pd.isna(row['ma20_vol']): continue
                
                is_green = row['close'] > row['open']
                if is_green and row['volume'] > 3 * row['ma20_vol']:
                    pocket_pivots_count += 1
                elif not is_green and row['volume'] < 0.5 * row['ma20_vol']:
                    micro_no_supply_count += 1
                    
            # Tìm Creeping HLs (Đáy cao dần áp sát nắp Darvas)
            # Kiểm tra 5 nến 15M gần nhất xem có HL không
            lows = df_15m['low'].tail(5).values
            creeping_hl = all(lows[i] >= lows[i-1] * 0.999 for i in range(1, len(lows)))
            current_price = float(df_15m['close'].iloc[-1])
            near_ceiling = (box['ceiling'] - current_price) / current_price < 0.03
            
            status = "PENDING"
            if is_squeeze and pocket_pivots_count >= 2 and micro_no_supply_count >= 1 and creeping_hl and near_ceiling:
                status = "APPROVED"
                
            res = item.copy()
            res.update({
                "bb_bandwidth": bb_bandwidth,
                "pocket_pivots": pocket_pivots_count,
                "micro_no_supply": micro_no_supply_count,
                "status": status,
                "current_price_15m": current_price
            })
            results.append(res)
            
        return results

