from typing import List, Dict, Any, Tuple, Optional
from engines.base_engine import BaseEngine
from models.market_state import SymbolState, ScoreContext, GridContext, MacroLevels
from strategies.grid_pingpong_uptrend import GridPingpongUptrendScorer
import logging

logger = logging.getLogger("DC5UptrendEngine")

class DC5PingpongUptrendEngine(BaseEngine):
    def __init__(self, strategy: GridPingpongUptrendScorer):
        self.strategy = strategy

    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], safety_map: Dict[str, str] = None, macro_levels_map: Optional[Dict[str, MacroLevels]] = None, **kwargs) -> List[SymbolState]:
        if safety_map is None:
            safety_map = {}
            
        # Truyền watchlist từ bảng 1 làm pool ưu tiên
        pingpong_results = self.strategy.run_scan(
            live_data_map,
            priority_symbols=watchlist
        )

        dc5_states = []
        
        if pingpong_results:
            for res in pingpong_results:
                sym = res.get('symbol', '')
                score = res.get('pingpong_score', 0)
                rank = res.get('rank', '')
                bounces = res.get('bounces_24h', 0)
                avg_range = res.get('avg_range', 0)
                price = res.get('price', 0)
                
                grid_setup_data = res.get('grid_setup', {})
                grid_ctx = None
                if grid_setup_data:
                    grid_ctx = GridContext(
                        is_dual_grid=False,
                        stop_loss=grid_setup_data.get('stop_loss_sell_all', 0.0),
                        take_profit=0.0, 
                        lower_price=grid_setup_data.get('lower_bound', 0.0),
                        upper_price=grid_setup_data.get('upper_bound', 0.0),
                        grid_quantity=grid_setup_data.get('grids', 0)
                    )
                
                comps = res.get('components', {})
                p_freq = comps.get('p_freq', 0)
                p_accel = comps.get('p_accel', 0)
                p_box = comps.get('p_box', 0)
                p_rsi = comps.get('p_rsi', 0)
                p_range = comps.get('p_range', 0)
                extra_info = f"F:{p_freq} A:{p_accel} Bx:{p_box} R:{p_range} RSI:{p_rsi} | B:{bounces}x{avg_range}%"
                
                score_ctx = ScoreContext(
                    engine_name="DC5_UPTREND",
                    total_score=score,
                    action_label=rank,
                    c1_score=extra_info, 
                    c2_score=f"Center: {grid_setup_data.get('center_line', 0)}", 
                    c3_score=f"Trigger: {grid_setup_data.get('trigger_price', 0)}",
                    c4_score=f"SL: {grid_setup_data.get('stop_loss_sell_all', 0)}",
                    grid_setup=grid_ctx
                )
                
                sym_ccxt = sym if "/" in sym else sym.replace("USDT", "/USDT")
                macro = (macro_levels_map or {}).get(sym_ccxt)

                if not macro:
                    try:
                        from core.macro_levels import calculate_universal_macro_levels
                        from core.exchange_info_cache import ExchangeInfoCache
                        from core.klines_cache import get_klines_cached
                        from models.market_state import MacroLevels
                        
                        sym_api = sym_ccxt.replace('/', '')
                        df_1h = get_klines_cached(sym_api, '1h', limit=50)
                        df_4h = get_klines_cached(sym_api, '4h', limit=50)
                        df_15m = get_klines_cached(sym_api, '15m', limit=250)
                        if df_1h is not None and df_4h is not None and df_15m is not None and len(df_1h) >= 24 and len(df_4h) >= 30:
                            cache = ExchangeInfoCache()
                            tick_size = cache.get_tick_size(sym_api)
                            m_dict = calculate_universal_macro_levels(df_4h, df_1h, tick_size, klines_15m_df=df_15m)
                            if m_dict['status'] == 'SUCCESS':
                                macro = MacroLevels(
                                    entry_4h=m_dict['entry_4h'],
                                    sl_4h=m_dict['sl_4h'],
                                    tp_1h=m_dict['tp_1h'],
                                    tp_4h=m_dict['tp_4h']
                                )
                    except Exception as e:
                        print(f"!!! Error calculating macro for {sym_api}: {e}")
                        pass

                state = SymbolState(
                    symbol=sym, 
                    current_price=price, 
                    volume_24h=0.0, 
                    avg_vola_24h=0.0, 
                    coin_vola_24h=0.0,
                    safety_tag=safety_map.get(sym, "⚠️ CHƯA XÉT"),
                    macro_levels=macro,
                    scores={"DC5_UPTREND": score_ctx}
                )
                dc5_states.append(state)
                
        return dc5_states
