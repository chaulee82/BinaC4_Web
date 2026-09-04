from typing import List, Dict, Any, Tuple
from engines.base_engine import BaseEngine
from models.market_state import SymbolState, ScoreContext, EntrySetupContext, MacroState
from strategies.hot_trend_pullback import HotTrendPullback
from core.grid_calculator import GridCalculator
import logging

logger = logging.getLogger("DC4Engine")

class DC4HotTrendEngine(BaseEngine):
    def __init__(self, strategy: HotTrendPullback, grid_calc: GridCalculator):
        self.strategy = strategy
        self.grid_calc = grid_calc

    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], safety_map: Dict[str, str] = None, **kwargs) -> Tuple[List[SymbolState], int, float, List[str]]:
        if safety_map is None:
            safety_map = {}
            
        # Đếm số mã Hot Trend đủ điều kiện trước khi quét
        htb_symbols = self.strategy.get_hot_trend_symbols(live_data_map)
        htb_count   = len(htb_symbols)
        htb_change_threshold = 5.0  # HTB_MIN_CHANGE_24H

        hot_trend_results = self.strategy.run_scan(live_data_map)

        dc4_states = []
        tracking_list = []
        
        if hot_trend_results:
            for res in hot_trend_results[:5]:
                sym = res.get('symbol', '')
                score = res.get('Điểm', 0)
                score_c5 = res.get('Điểm C1-C5', score)
                c0_sc = res.get('C0 Score', 0)
                rsi = res.get('RSI 1H', 0)
                pull = res.get('Pullback%', 0)
                act = res.get('Hành Động', '')
                
                c1 = res.get('C1 Trend', '')
                c2 = res.get('C2 Pullback', '')
                c3 = res.get('C3 Volume', '')
                c4 = res.get('C4 Bệ Đỡ', '')
                c5 = res.get('C5 Taker', '')
                
                c0_label = res.get('C0 Chu Kỳ', '')
                macro_state = None
                if c0_label:
                    macro_state = MacroState(
                        trend_label=c0_label,
                        drop_180d_pct=0.0,
                        ma_status=res.get('C0 Detail', {}).get('C0.4 MA99 Slope', '')
                    )
                
                setup1 = None
                setup = res.get('trade_setup', {})
                if setup and setup.get('entry'):
                    entry = setup['entry']
                    sl = setup['stop_loss']
                    tp1 = setup['take_profit']
                    rr = setup.get('rr_ratio', 0)
                    setup1 = EntrySetupContext(
                        setup_type="LIMIT",
                        entry_price=entry,
                        sl_price=sl,
                        tp1_price=tp1,
                        rr_ratio=rr
                    )
                
                score_ctx = ScoreContext(
                    engine_name="DC4",
                    total_score=score,
                    action_label=act,
                    c1_score=c1, c2_score=c2, c3_score=c3, c4_score=c4, bonus_score=c5,
                    rsi_1h=rsi, pullback_pct=pull,
                    entry_setup1=setup1
                )
                
                state = SymbolState(
                    symbol=sym, current_price=0.0, volume_24h=0.0, avg_vola_24h=0.0, coin_vola_24h=0.0,
                    safety_tag=safety_map.get(sym, "⚠️ CHƯA XÉT"),
                    macro_state=macro_state,
                    scores={"DC4": score_ctx}
                )
                dc4_states.append(state)
                
            if len(hot_trend_results) > 5:
                for res in hot_trend_results[5:]:
                    sym = res.get('symbol', '')
                    score = res.get('Điểm', 0)
                    act = res.get('Hành Động', '')
                    if "VÀO LỆNH" in act: act_short = "🚀"
                    elif "CHỜ XÁC NHẬN" in act: act_short = "⏳"
                    elif "TỪ CHỐI" in act: act_short = "🔥"
                    else: act_short = "🔴"
                    tracking_list.append(f"{sym} ({score}đ {act_short})")
                    
        return dc4_states, htb_count, htb_change_threshold, tracking_list
