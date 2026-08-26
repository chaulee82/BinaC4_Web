import pandas as pd
import pandas_ta as ta
from enum import Enum

class GridType(Enum):
    DARVAS_BASE = "4H_Accumulation"
    SNIPER_TACTICAL = "1H_Pullback"

class GridCalculator:
    def __init__(self):
        # Configuration for 4H Grid (Engine 1)
        self.e1_min_lower_bound_pct = -0.15
        self.e1_max_lower_bound_pct = -0.25
        self.e1_warning_volatility_pct = -0.30
        
        self.e1_min_upper_bound_pct = 0.20
        self.e1_max_upper_bound_pct = 0.35
        
        self.e1_min_grids = 24
        self.e1_max_grids = 30
        
        # Configuration for 1H/15M Grid (Engine 2)
        self.e2_min_grids = 15
        self.e2_max_grids = 20

    @staticmethod
    def calculate_4h_grid_params(lower_bound: float, upper_bound: float) -> tuple[int, float]:
        price_range_pct = (upper_bound - lower_bound) / lower_bound
        target_step = 0.02 
        raw_grids = int(price_range_pct / target_step) if target_step > 0 else 0
        final_grids = max(24, min(30, raw_grids))
        actual_step_pct = (price_range_pct / final_grids) * 100
        return final_grids, round(actual_step_pct, 2)

    @staticmethod
    def calculate_1h_grid_params(sl_price: float, entry_price: float) -> tuple[int, float]:
        price_range_pct = (entry_price - sl_price) / sl_price
        target_step = 0.01 
        raw_grids = int(price_range_pct / target_step) if target_step > 0 else 0
        final_grids = max(12, min(20, raw_grids))
        actual_step_pct = (price_range_pct / final_grids) * 100
        return final_grids, round(actual_step_pct, 2)

    def calculate_grid_4h(self, df_4h: pd.DataFrame, current_price: float) -> dict:
        """
        Calculates the Long-term Accumulation Grid (Engine 1).
        """
        # Ensure sufficient data
        if len(df_4h) < 100:
            return {"status": "ERROR", "message": "Not enough data for 4H grid calculation (needs 100+ candles)"}
            
        try:
            # Calculate Indicators
            df = df_4h.copy()
            df.ta.sma(length=99, append=True)
            df.ta.atr(length=14, append=True)
            df.ta.bbands(length=20, std=2, append=True)
            
            # Extract latest values
            ma99 = df['SMA_99'].iloc[-1]
            atr4h = df['ATRr_14'].iloc[-1]
            
            # Find BB columns dynamically based on pandas-ta naming convention
            bb_lower_col = [c for c in df.columns if c.startswith('BBL_20')][0]
            bb_upper_col = [c for c in df.columns if c.startswith('BBU_20')][0]
            bb_lower = df[bb_lower_col].iloc[-1]
            bb_upper = df[bb_upper_col].iloc[-1]
            
            highest_high_20 = df['High'].tail(20).max()
            
            # --- Lower Bound Calculation ---
            raw_lower = min(ma99, bb_lower) - atr4h
            pct_diff_lower = (raw_lower - current_price) / current_price
            
            status = "SUCCESS"
            if pct_diff_lower < self.e1_warning_volatility_pct:
                status = "WARNING_VOLATILE"
                
            # Clip Lower Bound (Cap at max_lower_bound_pct, which is -25%)
            # We use max() because we are dealing with negative numbers (e.g. max(-0.35, -0.25) = -0.25)
            target_pct_lower = max(pct_diff_lower, self.e1_max_lower_bound_pct)
            target_pct_lower = min(target_pct_lower, self.e1_min_lower_bound_pct)
            lower_bound = current_price * (1 + target_pct_lower)
            
            # --- Upper Bound Calculation ---
            raw_upper = max(highest_high_20, bb_upper)
            pct_diff_upper = (raw_upper - current_price) / current_price
            
            # Clip Upper Bound
            target_pct_upper = max(pct_diff_upper, self.e1_min_upper_bound_pct)
            target_pct_upper = min(target_pct_upper, self.e1_max_upper_bound_pct)
            upper_bound = current_price * (1 + target_pct_upper)
            
            # --- Grid Density (Self-Adaptive) ---
            num_grids, step_pct = self.calculate_4h_grid_params(lower_bound, upper_bound)
            tp_buffer_pct = 0.025
            hard_take_profit = upper_bound * (1 + tp_buffer_pct)
            
            return {
                "status": status,
                "engine": GridType.DARVAS_BASE.value,
                "current_price": current_price,
                "lower_bound": round(lower_bound, 5),
                "upper_bound": round(upper_bound, 5),
                "num_grids": num_grids,
                "hard_stop_loss": round(lower_bound * 0.97, 5),
                "hard_take_profit": round(hard_take_profit, 5),
                "tp_buffer_pct": tp_buffer_pct,
                "metrics": {
                    "raw_lower_pct": round(pct_diff_lower * 100, 2),
                    "raw_upper_pct": round(pct_diff_upper * 100, 2),
                    "step_pct": round(step_pct, 2)
                }
            }

        except Exception as e:
             return {"status": "ERROR", "message": f"Grid 4H calc error: {str(e)}"}


    def calculate_grid_1h(self, current_price: float, entry: float, stop_loss: float, tp1: float) -> dict:
        """
        Calculates the Short-term Pullback Sniper Grid (Engine 2).
        Independent of Oracle implementation, just needs the key levels.
        """
        try:
            # Lower Bound: Anchor directly to Stoploss
            lower_bound = stop_loss
            
            # Upper Bound: Anchor to Entry
            upper_bound = entry
            
            pct_diff_lower = (lower_bound - current_price) / current_price
            pct_diff_upper = (upper_bound - current_price) / current_price
            
            # Density (Self-Adaptive)
            num_grids, step_pct = self.calculate_1h_grid_params(sl_price=lower_bound, entry_price=upper_bound)
            
            # 1H Buffer
            buffer_1h_pct = 0.015
            hard_stop_loss = lower_bound * (1 - buffer_1h_pct)
            hard_take_profit = tp1 * (1 + buffer_1h_pct)

            return {
                "status": "SUCCESS",
                "engine": GridType.SNIPER_TACTICAL.value,
                "current_price": current_price,
                "lower_bound": round(lower_bound, 5),
                "upper_bound": round(upper_bound, 5),
                "num_grids": num_grids,
                "hard_stop_loss": round(hard_stop_loss, 5),
                "hard_take_profit": round(hard_take_profit, 5),
                "tp_buffer_pct": buffer_1h_pct,
                "metrics": {
                    "lower_diff_pct": round(pct_diff_lower * 100, 2),
                    "upper_diff_pct": round(pct_diff_upper * 100, 2),
                    "step_pct": round(step_pct, 2)
                }
            }
        except Exception as e:
            return {"status": "ERROR", "message": f"Grid 1H calc error: {str(e)}"}
