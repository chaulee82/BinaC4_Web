from typing import List, Dict, Any, Tuple
from engines.base_engine import BaseEngine
from models.market_state import SymbolState, ScoreContext, EntrySetupContext
from strategies.momentum_breakout import MomentumBreakout
import logging

logger = logging.getLogger("DC3Engine")

class DC3BreakoutEngine(BaseEngine):
    def __init__(self, strategy: MomentumBreakout):
        self.strategy = strategy

    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], timeframe: str = '4h', safety_map: Dict[str, str] = None, **kwargs) -> Tuple[List[SymbolState], str]:
        if safety_map is None:
            safety_map = {}
            
        print("\n🔍 [DC3] Kiểm tra BTC 1H Gate trước khi quét Breakout...")
        btc_gate = self.strategy.check_btc_trend_1h()
        btc_gate_label = btc_gate.get('reason', '')
        if not btc_gate.get('ok', True):
            print(f"🚱 [DC3] BTC Gate đóng: {btc_gate_label}")
        else:
            print(f"✅ [DC3] BTC Gate mở: {btc_gate_label}")

        breakout_results = []
        for symbol in watchlist:
            result = self.strategy.evaluate_breakout(symbol, timeframe, btc_gate=btc_gate)
            breakout_results.append(result)
            
        # Sắp xếp theo sort_score (total_score + rr_ratio) giảm dần, chỉ lấy Top 5
        breakout_results.sort(key=lambda x: x.get('sort_score', x.get('total_score', 0)), reverse=True)
        breakout_results = breakout_results[:5]
        
        dc3_states = []
        for res in breakout_results:
            sym = res.get('symbol', '')
            score = res.get('total_score', 0)
            act = res.get('action', '')
            dt = res.get('details', {})

            c1 = dt.get('Gate_1_PriceAction', '')[:25]
            c2 = dt.get('Gate_2_Volume', '')[:25]
            c3 = dt.get('Gate_3_OrderBook', '')[:25]
            c4 = dt.get('Gate_4_RR', '')[:25]
            bonus = dt.get('Bonus_TakerBuy', '')[:25]
            
            setup1 = None
            setup = res.get('trade_setup', {})
            if setup:
                entry = setup.get('entry')
                sl = setup.get('stop_loss')
                tp1 = setup.get('tp1', setup.get('take_profit'))
                tp2 = setup.get('tp2')
                tp_trail = setup.get('tp_trail')
                if entry and sl and tp1:
                    sl_pct = (entry - sl) / entry * 100
                    tp1_pct = (tp1 - entry) / entry * 100
                    rr_ratio = res.get('rr_ratio', (tp1_pct / sl_pct if sl_pct > 0 else 0))
                    
                    setup_type = "MOCK_SCALE_OUT" if tp2 and tp_trail else "BREAKOUT"
                    setup1 = EntrySetupContext(
                        setup_type=setup_type,
                        entry_price=entry,
                        sl_price=sl,
                        tp1_price=tp1,
                        tp2_price=tp2 or 0.0,
                        rr_ratio=rr_ratio
                    )
                    
            score_ctx = ScoreContext(
                engine_name="DC3",
                total_score=score,
                action_label=act,
                c1_score=c1, c2_score=c2, c3_score=c3, c4_score=c4, bonus_score=bonus,
                entry_setup1=setup1
            )
            
            state = SymbolState(
                symbol=sym, current_price=0.0, volume_24h=0.0, avg_vola_24h=0.0, coin_vola_24h=0.0,
                safety_tag=safety_map.get(sym, "⚠️ CHƯA XÉT"),
                scores={"DC3": score_ctx}
            )
            dc3_states.append(state)
            
        return dc3_states, btc_gate_label
