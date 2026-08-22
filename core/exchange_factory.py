import ccxt
import os
import logging

logger = logging.getLogger(__name__)

def get_working_exchange(api_key=None, secret_key=None):
    """
    Khởi tạo exchange. Ưu tiên Binance, nếu lỗi (do chặn IP ở Colab/US) thì tự động fallback sang Bybit, MEXC, OKX.
    """
    config = {'enableRateLimit': True}
    proxy = os.getenv('PROXY_URL')
    if proxy:
        config['proxies'] = {'http': proxy, 'https': proxy}
        
    if api_key and secret_key:
        config['apiKey'] = api_key
        config['secret'] = secret_key
        # Nếu có truyền API key (dùng cho Executor), chỉ dùng Binance vì API keys là của Binance.
        logger.info("Khởi tạo sàn giao dịch Binance (Trading mode).")
        return ccxt.binance(config)

    exchanges_to_try = [
        ('binance', ccxt.binance),
        ('bybit', ccxt.bybit),
        ('mexc', ccxt.mexc),
        ('okx', ccxt.okx)
    ]

    for name, ExchangeClass in exchanges_to_try:
        try:
            exchange = ExchangeClass(config)
            logger.debug(f"Đang kiểm tra kết nối tới sàn: {name.upper()}...")
            # Kiểm tra kết nối bằng cách lấy thời gian server
            exchange.fetch_time()
            logger.debug(f"✅ Đã kết nối thành công tới {name.upper()}")
            return exchange
        except Exception as e:
            logger.warning(f"❌ Không thể kết nối tới {name.upper()}: {e}. Đang thử sàn khác...")
    
    logger.error("Không thể kết nối tới bất kỳ sàn nào! Sử dụng Binance làm mặc định.")
    return ccxt.binance(config)
