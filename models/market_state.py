from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass(slots=True, frozen=True)
class MacroState:
    """Chứa các chỉ số Vĩ mô & Xu hướng (Bao gồm Bảng 3 Radar)"""
    trend_label: str                # UP, DOWN, SIDEWAY
    drop_180d_pct: float            # % giảm từ đỉnh 180 ngày
    ma_status: str                  # Tình trạng MA (ví dụ: Nằm trên MA200)
    
    # Bổ sung Bảng 3
    darvas_amplitude_pct: float = 0.0     # Độ nén hộp
    volume_dryup_pct: float = 0.0         # Cạn cung
    accumulation_spikes_count: int = 0    # Số nến gom

@dataclass(slots=True, frozen=True)
class GridContext:
    """Thông số cấu hình cho Động cơ 1: Darvas Grid"""
    is_dual_grid: bool
    stop_loss: float
    take_profit: float
    
    # Single Grid parameters
    lower_price: float = 0.0
    upper_price: float = 0.0
    grid_quantity: int = 0
    
    # Dual Grid parameters
    g1_lower: float = 0.0
    g1_upper: float = 0.0
    g1_grids: int = 0
    g2_lower: float = 0.0
    g2_upper: float = 0.0
    g2_grids: int = 0

@dataclass(slots=True, frozen=True)
class EarlyWarningContext:
    """Cảnh báo rủi ro & Chấm điểm Pullback (Dùng cho DC2)"""
    ew_level: int                  # 1 (Nguy hiểm), 2 (Cảnh báo), 3 (An toàn)
    ew_label: str                  # Label hiển thị (VD: "🔴 HỒI GIẢ")
    pullback_score: float          # Điểm Pullback / 100
    force_conservative: bool       # Bắt buộc chuyển sang Mode an toàn
    c1_wick_score: Any = '-'
    c2_micro_dryup_score: Any = '-'
    c3_macro_momentum_score: Any = '-'
    c4_taker_buy_score: Any = '-'
    triggers: List[str] = field(default_factory=list)

@dataclass(slots=True, frozen=True)
class EntrySetupContext:
    """Thông số lệnh thực thi (Dành cho OCO, Breakout, Limit)"""
    setup_type: str                # Phân loại: OCO_SPLIT, GRID_70, BREAKOUT, v.v.
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float = 0.0
    rr_ratio: float = 0.0
    is_oco: bool = False
    trailing_trigger: str = "OFF"

@dataclass(slots=True, frozen=True)
class ScoreContext:
    """Điểm số tổng hợp của các Động cơ (DC2, DC3, DC4)"""
    engine_name: str               # Tên động cơ (DC2, DC3, DC4)
    total_score: float
    action_label: str              # Hành động (VD: 'BUY LIMIT', 'WAIT')
    
    # Chi tiết các Gate
    c1_score: Any = '-'            # Price Action / Hội Tụ / Trend
    c2_score: Any = '-'            # Volume / Pullback
    c3_score: Any = '-'            # OrderBook / Volume
    c4_score: Any = '-'            # R/R / Bệ Đỡ
    bonus_score: Any = '-'         # Taker / Extra
    
    # Các chỉ số vi mô bổ sung (Thường dùng trong hiển thị)
    rsi_1h: float = 0.0
    pullback_pct: float = 0.0
    
    # Cấu trúc phụ thuộc Động cơ
    early_warning: Optional[EarlyWarningContext] = None
    entry_setup1: Optional[EntrySetupContext] = None
    entry_setup2: Optional[EntrySetupContext] = None
    grid_setup: Optional[GridContext] = None

@dataclass(slots=True, frozen=True)
class SymbolState:
    """Object tối cao bao bọc toàn bộ trạng thái của một đồng coin"""
    symbol: str
    current_price: float
    volume_24h: float
    avg_vola_24h: float            # Volatility trung bình thị trường
    coin_vola_24h: float           # Volatility riêng của coin
    safety_tag: str                # Tag an toàn từ coin_filter
    
    # Biến trạng thái xu hướng (HTB)
    is_hot_trend: bool = False
    htb_change_pct: float = 0.0
    
    macro_state: Optional[MacroState] = None
    
    # Kết quả sau khi chạy qua các Động cơ
    scores: Dict[str, ScoreContext] = field(default_factory=dict)
