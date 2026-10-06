from typing import List, Dict, Any, Tuple, Optional
from engines.base_engine import BaseEngine
from models.market_state import SymbolState, ScoreContext, EntrySetupContext, MacroState, MacroLevels
from strategies.hot_trend_pullback import HotTrendPullback
from core.grid_calculator import GridCalculator
from core.money_flow import money_flow_tag
import logging

logger = logging.getLogger("DC4Engine")

class DC4HotTrendEngine(BaseEngine):
    def __init__(self, strategy: HotTrendPullback, grid_calc: GridCalculator):
        self.strategy = strategy
        self.grid_calc = grid_calc

    @staticmethod
    def _calc_macro_levels(sym_ccxt: str) -> Optional[MacroLevels]:
        """Tính Macro Levels 4H cho 1 mã (fallback khi không có trong macro_levels_map)."""
        sym_api = sym_ccxt.replace('/', '')
        try:
            from core.macro_levels import calculate_universal_macro_levels
            from core.exchange_info_cache import ExchangeInfoCache
            from core.klines_cache import get_klines_cached

            df_4h = get_klines_cached(sym_api, '4h', limit=50)
            df_1h = get_klines_cached(sym_api, '1h', limit=50)
            try:
                df_15m = get_klines_cached(sym_api, '15m', limit=250)
            except Exception:
                df_15m = None

            if df_4h is None or df_1h is None:
                logger.debug(f"[DC4 Macro] {sym_api}: thiếu dữ liệu nến 4H/1H")
                return None

            tick_size = ExchangeInfoCache().get_tick_size(sym_api)
            m = calculate_universal_macro_levels(df_4h, df_1h, tick_size, klines_15m_df=df_15m, symbol=sym_api)
            if m.get('status') == 'SUCCESS':
                return MacroLevels(
                    entry_4h=m['entry_4h'],
                    sl_4h=m['sl_4h'],
                    tp_1h=m['tp_1h'],
                    tp_4h=m['tp_4h'],
                    d3=m.get('d3'),
                )
            logger.debug(f"[DC4 Macro] {sym_api}: {m.get('message', 'error')}")
        except Exception as e:
            logger.warning(f"[DC4 Macro] Lỗi tính Macro 4H cho {sym_api}: {e}")
        return None

    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], safety_map: Dict[str, str] = None, macro_levels_map: Optional[Dict[str, MacroLevels]] = None, **kwargs) -> Tuple[List[SymbolState], int, float, List[str]]:
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
                if res.get('explosive_tag'):
                    act = f"{act} | {res['explosive_tag']}"
                
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
                        setup_type="TRIGGER_DIP" if setup.get('is_trigger') else "LIMIT",
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
                
                if "/" in sym:
                    sym_ccxt = sym
                elif sym.endswith("USDT"):
                    sym_ccxt = f"{sym[:-4]}/USDT"
                else:
                    sym_ccxt = f"{sym}/USDT"   # DC4 trả về dạng 'PARTI' (thiếu quote)
                macro = (macro_levels_map or {}).get(sym_ccxt)
                
                if not macro:
                    # DC4 quét độc lập với watchlist → mã Hot Trend thường KHÔNG có trong macro_levels_map.
                    # Tự tính Macro 4H (chỉ Top 5, dùng klines_cache để hạn chế gọi API).
                    macro = self._calc_macro_levels(sym_ccxt)
                    if macro is not None and macro_levels_map is not None:
                        macro_levels_map[sym_ccxt] = macro

                state = SymbolState(
                    symbol=sym, current_price=0.0, volume_24h=0.0, avg_vola_24h=0.0, coin_vola_24h=0.0,
                    safety_tag=safety_map.get(sym, "⚠️ CHƯA XÉT"),
                    macro_state=macro_state,
                    macro_levels=macro,
                    money_flow_tag=money_flow_tag(sym_ccxt),
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
