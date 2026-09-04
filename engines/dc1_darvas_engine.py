from typing import List, Dict, Any
from engines.base_engine import BaseEngine
from models.market_state import SymbolState, ScoreContext, GridContext
from strategies.macro_grid_darvas import MacroGridDarvas

class DC1DarvasEngine(BaseEngine):
    def __init__(self, strategy: MacroGridDarvas):
        self.strategy = strategy

    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], timeframe: str = '4h', safety_map: Dict[str, str] = None, **kwargs) -> List[SymbolState]:
        if safety_map is None:
            safety_map = {}
            
        darvas_results = []
        for symbol in watchlist:
            result = self.strategy.scan_grid_candidate(symbol, timeframe)
            darvas_results.append(result)
            
        # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 5
        darvas_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
        darvas_results = darvas_results[:5]
        
        dc1_states = []
        for res in darvas_results:
            sym = res.get('symbol', '')
            g_setup_dict = res.get('grid_setup', {})
            g_setup = None
            if g_setup_dict:
                g_setup = GridContext(
                    is_dual_grid=g_setup_dict.get('is_dual_grid', False),
                    stop_loss=g_setup_dict.get('stop_loss', 0.0),
                    take_profit=g_setup_dict.get('take_profit', 0.0),
                    lower_price=g_setup_dict.get('lower_price', 0.0),
                    upper_price=g_setup_dict.get('upper_price', 0.0),
                    grid_quantity=g_setup_dict.get('grid_quantity', 0),
                    g1_lower=g_setup_dict.get('g1_lower', 0.0),
                    g1_upper=g_setup_dict.get('g1_upper', 0.0),
                    g1_grids=g_setup_dict.get('g1_grids', 0),
                    g2_lower=g_setup_dict.get('g2_lower', 0.0),
                    g2_upper=g_setup_dict.get('g2_upper', 0.0),
                    g2_grids=g_setup_dict.get('g2_grids', 0)
                )
            
            score_ctx = ScoreContext(
                engine_name="DC1",
                total_score=res.get('total_score', 0),
                action_label=res.get('action', ''),
                grid_setup=g_setup
            )
            
            state = SymbolState(
                symbol=sym,
                current_price=0.0,
                volume_24h=0.0,
                avg_vola_24h=0.0,
                coin_vola_24h=0.0,
                safety_tag=safety_map.get(sym, "⚠️ CHƯA XÉT"),
                scores={"DC1": score_ctx}
            )
            dc1_states.append(state)
            
        return dc1_states
