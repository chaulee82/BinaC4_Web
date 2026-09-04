import os
import sys
import logging
import time
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from core.macro_early_scanner import MacroEarlyScanner
from core.coin_filter import get_filtered_symbols

# Thiết lập logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("CN4-BackgroundLoop")

# Tắt log rác
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

scanner = MacroEarlyScanner()

def job_update_macro_watchlist():
    logger.info("🕒 Đang chạy Job Update Macro Watchlist (1D)...")
    try:
        # Lấy danh sách all symbols từ coin_filter
        watchlist, _ = get_filtered_symbols()
        if not watchlist:
            logger.warning("Không có mã nào từ filter ban đầu.")
            return
            
        new_watchlist = scanner.scan_macro_1d(watchlist)
        logger.info(f"✅ Đã update Watchlist Vĩ Mô. Số lượng: {len(new_watchlist)} mã.")
        for item in new_watchlist:
            logger.info(f"  - {item['symbol']}: Chiết khấu {item['drop_pct']:.1%}, Nén BB {item['box']['amplitude']:.1%}, MA99 dốc {item['ma99_slope']:.2%}")
    except Exception as e:
        logger.error(f"Lỗi trong job_update_macro_watchlist: {e}")

def job_scan_micro_15m():
    if not scanner.watchlist:
        logger.info("Watchlist rỗng, bỏ qua scan 15M. Chờ job vĩ mô cập nhật...")
        return
        
    logger.info(f"🔍 Đang quét Vi Mô 15M cho {len(scanner.watchlist)} mã...")
    try:
        results = scanner.scan_micro_15m()
        for res in results:
            sym = res["symbol"]
            status = res["status"]
            
            # Format UI Console BẢNG 3 mới
            if status == "APPROVED":
                logger.warning(f"🚀 [BẢNG 3] BẮT SỚM NỀN TĂNG: {sym} | Trạng thái: {status}")
                logger.info(f"   ↳ Giá: {res['current_price_15m']} | Squeeze BB: {res['bb_bandwidth']:.2%} | Pocket Pivots: {res['pocket_pivots']} | No Supply: {res['micro_no_supply']}")
            else:
                logger.debug(f"  - {sym} đang PENDING...")
                
    except Exception as e:
        logger.error(f"Lỗi trong job_scan_micro_15m: {e}")

def main():
    logger.info("Khởi động hệ thống Background Task CN4 (BẢNG 3)...")
    load_dotenv()
    
    # Khởi động lần đầu ngay lập tức
    job_update_macro_watchlist()
    job_scan_micro_15m()
    
    scheduler = BlockingScheduler()
    # Job 1D: Chạy mỗi 12 giờ
    scheduler.add_job(job_update_macro_watchlist, 'interval', hours=12, id='macro_1d')
    # Job 15M: Chạy mỗi 3 phút
    scheduler.add_job(job_scan_micro_15m, 'interval', minutes=3, id='micro_15m')
    
    logger.info("Đã đặt lịch: Macro(12h), Micro(3m). Tiến trình ngầm đang chạy...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
