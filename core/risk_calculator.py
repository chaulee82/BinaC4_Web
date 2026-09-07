import logging
from typing import Optional

logger = logging.getLogger("RiskCalculator")

class RiskCalculator:
    """
    Trạm Tính Toán Rủi Ro (Bước 2 trong Pipeline)
    Nhiệm vụ: Hàm Pure Function xử lý toán học (nhận float, nhả float hoặc None).
    Tuyệt đối không nhúng PriceFormatter hay DTO vào đây để dễ dàng Unit Test.
    """

    @staticmethod
    def calculate_dynamic_tp(
        raw_entry: float, 
        raw_sl: float, 
        raw_tp_initial: Optional[float] = None, 
        min_rr: float = 1.5, 
        max_tp_pct: float = 0.10
    ) -> Optional[float]:
        """
        Tính toán R/R động cho lệnh Sniper/Pullback/Breakout.
        Nếu R/R < min_rr, nới TP. Nới quá max_tp_pct -> Reject.
        Trả về raw_tp lý tưởng (float) hoặc None nếu rủi ro quá cao.
        """
        if raw_entry <= 0 or raw_sl <= 0:
            return None
            
        risk = raw_entry - raw_sl
        if risk <= 0:
            logger.warning(f"❌ [RiskCalculator] SL ({raw_sl}) >= Entry ({raw_entry}). Bỏ qua.")
            return None
            
        # Nếu không có TP ban đầu, tự sinh TP theo min_rr
        target_tp = raw_tp_initial if raw_tp_initial else raw_entry + (risk * min_rr)
        
        reward = target_tp - raw_entry
        rr = reward / risk if risk > 0 else 0
        
        if rr < min_rr:
            # Dynamic TP: Force R/R = min_rr
            target_tp = raw_entry + (risk * min_rr)
            logger.info(f"🔄 [RiskCalculator] R/R ban đầu {rr:.2f} < {min_rr}. Điều chỉnh TP lên {target_tp:.4f}")
            
        # Check Maximum TP Distance
        tp_distance_pct = (target_tp - raw_entry) / raw_entry
        if tp_distance_pct > max_tp_pct:
            logger.warning(f"❌ [RiskCalculator] BỊ TỪ CHỐI. TP mới yêu cầu tăng {tp_distance_pct*100:.2f}% (> {max_tp_pct*100:.0f}% Max). Rủi ro quá cao!")
            return None
            
        return target_tp
