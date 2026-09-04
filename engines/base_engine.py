from typing import List, Dict, Any
from abc import ABC, abstractmethod
from models.market_state import SymbolState

class BaseEngine(ABC):
    """
    Lớp cơ sở cho tất cả các Động Cơ (Engines/Controllers).
    Động cơ nhận đầu vào là danh sách theo dõi và dữ liệu thị trường, 
    trả về danh sách các DTO SymbolState để Renderer hiển thị và Executor thực thi.
    """
    
    @abstractmethod
    def run(self, watchlist: List[str], live_data_map: Dict[str, Any], **kwargs) -> List[SymbolState]:
        """
        Khởi chạy chiến lược và trả về danh sách các trạng thái thị trường.
        """
        pass
