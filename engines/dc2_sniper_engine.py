import pandas as pd
import logging
from typing import List, Dict, Any
from engines.base_engine import BaseEngine
from models.market_state import SymbolState, ScoreContext, EarlyWarningContext, EntrySetupContext
from strategies.macro_pullback.pullback_sniper import PullbackSniper
from core.early_warning import EarlyWarningMatrix
from strategies.macro_pullback.entry_calculator_service import EntryCalculatorService
from core.market_data_repo import MarketDataRepository

logger = logging.getLogger("DC2Engine")

class DC2SniperEngine(BaseEngine):
    def __init__(self, strategy: PullbackSniper, early_warning: EarlyWarningMatrix, entry_calc: EntryCalculatorService, repo: MarketDataRepository):
        self.strategy = strategy
        self.early_warning = early_warning
        self.entry_calc = entry_calc
        self.repo = repo

    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], avg_vola_24h: float, timeframe: str = '4h', safety_map: Dict[str, str] = None, **kwargs) -> List[SymbolState]:
        if safety_map is None:
            safety_map = {}
            
        sniper_results = []
        for symbol in watchlist:
            macro_gate = self.strategy.check_macro_trend_1d(symbol)
            result = self.strategy.evaluate_candidate(symbol, timeframe, macro_gate=macro_gate)
            sniper_results.append(result)

        # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 5
        sniper_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
        sniper_results = sniper_results[:5]
        
        dc2_states = []
        for res in sniper_results:
            sym = res.get('symbol', '')
            score = res.get('total_score', 0)
            act = res.get('action', '')
            dt = res.get('details', {})

            c1 = dt.get('Gate_1_Confluence', {}).get('score', 0)
            c2 = dt.get('Gate_2_Volume', {}).get('score', 0)
            c3 = dt.get('Gate_3_OrderBook', {}).get('score', 0)
            c4 = dt.get('Gate_4_RR', {}).get('score', 0)
            
            ew_ctx = None
            setup1 = None
            setup2 = None
            
            if score >= 70:
                coin_vola = live_data_map.get(sym.replace('/', ''), {}).get('daily_vola', avg_vola_24h)
                try:
                    sym_api = sym.replace('/', '')
                    _df_15m = self.repo.get_klines_df(sym_api, '15m', 60)
                    _df_4h  = self.repo.get_klines_df(sym_api, '4h', 120)
                    _df_1h  = self.repo.get_klines_df(sym_api, '1h', 50)
                    _price  = float(_df_15m['close'].iloc[-1]) if not _df_15m.empty else 0.0

                    ew_res = self.early_warning.scan_sniper_safety(
                        df_15m=_df_15m, df_4h=_df_4h, df_1h=_df_1h,
                        current_price=_price, coin_vola_24h=coin_vola,
                        avg_vola_24h=avg_vola_24h, symbol=sym_api,
                    )
                    pb_dt  = ew_res.get('pullback_detail', {})
                    
                    ew_ctx = EarlyWarningContext(
                        ew_level=ew_res.get('ew_level', 3),
                        ew_label=ew_res.get('ew_label', ''),
                        pullback_score=ew_res.get('pullback_score', 0),
                        force_conservative=ew_res.get('force_conservative', False),
                        c1_wick_score=pb_dt.get('C1_Wick_Purity', {}).get('score', '-'),
                        c2_micro_dryup_score=pb_dt.get('C2_Micro_Dryup', {}).get('score', '-'),
                        c3_macro_momentum_score=pb_dt.get('C3_Macro_Momentum', {}).get('score', '-'),
                        c4_taker_buy_score=pb_dt.get('C4_Taker_Buy', {}).get('score', '-'),
                        triggers=ew_res.get('ew_triggers', [])
                    )
                except Exception as _ew_err:
                    logger.debug(f"[DC2-EW] {sym}: Không thể chạy scan_sniper_safety: {_ew_err}")
                    ew_ctx = EarlyWarningContext(ew_level=3, ew_label="Lỗi", pullback_score=0, force_conservative=False)

                if ew_ctx.ew_level > 1:
                    try:
                        oco_payload = self.entry_calc.calculate({
                            "symbol": sym.replace('/', ''),
                            "timeframe_entry": "15m",
                            "timeframe_macro": "4h",
                            "engine_type": "SNIPER_SPOT",
                            "capital_allocation": 0.30,
                            "avg_vola_24h": avg_vola_24h,
                            "coin_vola_24h": coin_vola,
                        })
                        v = oco_payload['validation']
                        p1 = oco_payload['payload'][0]['parameters']
                        p2 = oco_payload['payload'][1]['parameters']
                        
                        setup1 = EntrySetupContext(
                            setup_type="OCO_SPLIT",
                            entry_price=p1['price'],
                            sl_price=p1['oco_sl'],
                            tp1_price=p1['oco_tp'],
                            rr_ratio=v['rr_payload1'],
                            is_oco=True
                        )
                        setup2 = EntrySetupContext(
                            setup_type="OCO_SPLIT",
                            entry_price=p2['price'],
                            sl_price=p2['oco_sl'],
                            tp1_price=p2['oco_tp'],
                            rr_ratio=v['rr_payload2'],
                            is_oco=True,
                            trailing_trigger="ON->BE khi OCO-1 TP"
                        )
                    except Exception as e:
                        pass
            
            score_ctx = ScoreContext(
                engine_name="DC2",
                total_score=score,
                action_label=act,
                c1_score=c1, c2_score=c2, c3_score=c3, c4_score=c4,
                early_warning=ew_ctx,
                entry_setup1=setup1,
                entry_setup2=setup2
            )
            
            state = SymbolState(
                symbol=sym, current_price=0.0, volume_24h=0.0, avg_vola_24h=0.0, coin_vola_24h=0.0,
                safety_tag=safety_map.get(sym, "⚠️ CHƯA XÉT"),
                scores={"DC2": score_ctx}
            )
            dc2_states.append(state)
        
        return dc2_states
