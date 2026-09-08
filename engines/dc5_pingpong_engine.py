from typing import List, Dict, Any, Tuple
from engines.base_engine import BaseEngine
from models.market_state import SymbolState, ScoreContext, GridContext
from strategies.grid_pingpong import GridPingpongScorer
import logging

logger = logging.getLogger("DC5Engine")

class DC5PingpongEngine(BaseEngine):
    def __init__(self, strategy: GridPingpongScorer):
        self.strategy = strategy

    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], safety_map: Dict[str, str] = None, **kwargs) -> List[SymbolState]:
        if safety_map is None:
            safety_map = {}
            
        pingpong_results = self.strategy.run_scan(live_data_map)

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
                        take_profit=0.0, # Not explicitly defined for pingpong
                        lower_price=grid_setup_data.get('lower_bound', 0.0),
                        upper_price=grid_setup_data.get('upper_bound', 0.0),
                        grid_quantity=grid_setup_data.get('grids', 0)
                    )
                
                # Encode bounds and extra info into the action_label or c1_score for renderer
                comps = res.get('components', {})
                p_core = comps.get('p_core', 0)
                p_support = comps.get('p_support', 0)
                p_risk = comps.get('p_risk', 0)
                n_test = comps.get('n_test', 0)
                extra_info = f"C:{p_core} S:{p_support} R:-{p_risk} | B:{bounces}x{avg_range}%"
                
                
                score_ctx = ScoreContext(
                    engine_name="DC5",
                    total_score=score,
                    action_label=rank,
                    c1_score=extra_info, 
                    c2_score=f"Center: {grid_setup_data.get('center_line', 0)}", 
                    c3_score=f"Trigger: {grid_setup_data.get('trigger_price', 0)}",
                    c4_score=f"SL: {grid_setup_data.get('stop_loss_sell_all', 0)}",
                    grid_setup=grid_ctx
                )
                
                state = SymbolState(
                    symbol=sym, 
                    current_price=price, 
                    volume_24h=0.0, 
                    avg_vola_24h=0.0, 
                    coin_vola_24h=0.0,
                    safety_tag=safety_map.get(sym, "⚠️ CHƯA XÉT"),
                    scores={"DC5": score_ctx}
                )
                dc5_states.append(state)
                
        return dc5_states
