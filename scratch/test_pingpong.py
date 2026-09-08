import sys
import os

# Đảm bảo đường dẫn thư mục gốc để import core và strategies
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.coin_filter import get_filtered_symbols
from strategies.grid_pingpong import GridPingpongScorer
from core.market_data_repo import MarketDataRepository
from core.cache_service import CacheService

def main():
    print("Khởi động CoinFilter để lấy live_data_map...")
    repo = MarketDataRepository()
    cache = CacheService(repo)
    live_data_map_cache = cache.get_live_data_map()
    watchlist, safety_map, early_list, df_summary, current_time_str = get_filtered_symbols(live_data_map_cache)
    
    # Chúng ta dùng live_data_map sau khi lọc
    # Filtered symbols logic inside get_filtered_symbols updates live_data_map_cache or we just use live_data_map_cache
    live_data_map = live_data_map_cache
    
    
    print(f"Đã lấy được {len(live_data_map)} mã. Bắt đầu chạy Grid Pingpong Scorer...")
    
    # Chạy scan Pingpong
    results = GridPingpongScorer.run_scan(live_data_map)
    
    print("\n" + "="*80)
    print("🏆 KẾT QUẢ QUÉT GRID PINGPONG (TOP 5) 🏆")
    print("="*80)
    
    if not results:
        print("Không có mã nào thỏa mãn 4 điều kiện chốt chặn (Hard Filters)!")
        return

    for i, res in enumerate(results, 1):
        print(f"#{i} | Symbol: {res['symbol']} | Giá: {res['price']}")
        print(f"   | Xếp hạng: {res['rank']} | Điểm PingPong: {res['pingpong_score']}")
        print(f"   | Tần suất (Bounces 24h): {res['bounces_24h']} lần")
        print(f"   | Biên độ trung bình: {res['avg_range']}%")
        print(f"   | [Grid Setup]:")
        setup = res['grid_setup']
        print(f"       + Center Line: {setup['center_line']}")
        print(f"       + Lưới: {setup['grids']} lưới")
        print(f"       + Upper Bound: {setup['upper_bound']} | Lower Bound: {setup['lower_bound']}")
        print(f"       + Trigger Price: {setup['trigger_price']}")
        print(f"       + Stop Loss Sell All: {setup['stop_loss_sell_all']}")
        print("-" * 50)

if __name__ == "__main__":
    main()
