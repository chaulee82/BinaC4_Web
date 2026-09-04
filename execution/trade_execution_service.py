import ccxt
import logging
import math
import os
from typing import Optional
from models.market_state import EntrySetupContext, GridContext

logger = logging.getLogger("TradeExecutionService")

def fmt_price(price: float) -> str:
    if price is None: return "N/A"
    if price == 0: return "0"
    if price >= 1:
        decimals = 4
    else:
        first_sig = -math.floor(math.log10(abs(price)))
        decimals = first_sig + 3
    return f"{price:.{decimals}f}".rstrip('0').rstrip('.')

class TradeExecutionService:
    def __init__(self, exchange: Optional[ccxt.Exchange] = None):
        """
        Khởi tạo TradeExecutionService. 
        Tự động fallback về DRY_RUN nếu không có exchange (không có API Key) hoặc DRY_RUN=True trong .env
        """
        self.exchange = exchange
        
        # Đọc biến môi trường DRY_RUN. Mặc định là True để an toàn.
        env_dry_run = os.environ.get("DRY_RUN", "True").lower() in ("true", "1", "yes")
        
        if env_dry_run or not self.exchange:
            self.dry_run = True
            logger.info("🛡️ [Execution] Đang chạy ở chế độ DRY-RUN (Mock Mode). Lệnh sẽ không được gửi lên sàn.")
        else:
            self.dry_run = False
            logger.warning("🚨 [Execution] CHẾ ĐỘ LIVE TRADING ĐƯỢC KÍCH HOẠT. LỆNH SẼ ĐƯỢC ĐẶT BẰNG TIỀN THẬT!")

    def _check_balance(self, required_amount: float = 10.0) -> bool:
        """
        Kiểm tra số dư khả dụng (Free USDT). 
        Nếu nhỏ hơn required_amount (mặc định 10 USDT) -> Chặn lệnh.
        """
        if self.dry_run:
            return True # Dry-run thì luôn pass
            
        try:
            balance = self.exchange.fetch_balance()
            free_usdt = balance.get('USDT', {}).get('free', 0.0)
            
            if free_usdt < required_amount:
                logger.error(f"❌ [Risk] Số dư USDT khả dụng ({free_usdt:.2f}) dưới mức tối thiểu ({required_amount}). CHẶN LỆNH!")
                return False
                
            return True
        except Exception as e:
            logger.error(f"❌ [Risk] Không thể kiểm tra số dư: {e}")
            return False

    def _send_zalo_notification(self, message: str):
        """
        Mock hàm gửi thông báo qua Zalo ZNS.
        Sau này có thể tích hợp API Zalo OA thực sự vào đây.
        """
        logger.info(f"📱 [ZALO ZNS] Đã gửi thông báo: {message}")

    def execute_entry_setup(self, symbol: str, amount: float, setup: EntrySetupContext):
        """
        Định tuyến & Thực thi lệnh cho Động cơ Darvas, Sniper, Breakout, Hot Trend...
        """
        if not setup or not setup.entry_price:
            logger.error(f"❌ [Execution] Thiếu EntrySetupContext hoặc giá entry cho {symbol}")
            return

        order_type = setup.setup_type.upper()
        
        msg = f"🚀 [EXECUTE] BUY {order_type} {symbol} @ {fmt_price(setup.entry_price)} | SL: {fmt_price(setup.sl_price)} | TP1: {fmt_price(setup.tp1_price)}"
        
        if self.dry_run:
            logger.info(f"[DRY-RUN] {msg}")
            self._send_zalo_notification(f"[MOCK] Đã lên đạn {symbol} @ {fmt_price(setup.entry_price)}")
            return

        # LIVE TRADING MODE
        if not self._check_balance(amount):
            return

        try:
            # 1. Tạo Limit Buy Order trước
            buy_order = self.exchange.create_limit_buy_order(symbol, amount, setup.entry_price)
            logger.info(f"✅ Đã đặt Limit Buy {symbol}: ID {buy_order.get('id')}")
            
            # 2. Xử lý OCO nếu được yêu cầu và sàn hỗ trợ
            if setup.is_oco and setup.sl_price and setup.tp1_price:
                # Chú ý: CCXT với Binance hỗ trợ create_order ocoOrder
                # Cần try/except riêng vì không phải sàn nào cũng hỗ trợ
                try:
                    params = {
                        'stopPrice': setup.sl_price,
                        'stopLimitPrice': setup.sl_price,
                        'stopLimitTimeInForce': 'GTC'
                    }
                    oco_order = self.exchange.create_order(
                        symbol, 'limit', 'sell', amount, setup.tp1_price, params
                    )
                    logger.info(f"✅ Đã đặt lệnh OCO cho {symbol}: {oco_order.get('id')}")
                except Exception as oco_err:
                    logger.error(f"❌ [Execution] Lỗi đặt lệnh OCO cho {symbol}: {oco_err}")
            
            self._send_zalo_notification(f"✅ Khớp lệnh {symbol} @ {fmt_price(setup.entry_price)}")

        except ccxt.InsufficientFunds as e:
            logger.error(f"❌ [Execution] Không đủ tiền đặt lệnh {symbol}: {e}")
        except ccxt.NetworkError as e:
            logger.error(f"❌ [Execution] Lỗi mạng khi đặt lệnh {symbol}: {e}")
        except ccxt.ExchangeError as e:
            logger.error(f"❌ [Execution] Lỗi từ sàn khi đặt lệnh {symbol}: {e}")
        except Exception as e:
            logger.error(f"❌ [Execution] Lỗi không xác định: {e}")

    def execute_grid_setup(self, symbol: str, amount_per_grid: float, setup: GridContext):
        """
        Thực thi thiết lập lưới Grid.
        """
        if not setup or (not setup.lower_price and not setup.g1_lower):
            logger.error(f"❌ [Execution] Thiếu thông số GridContext cho {symbol}")
            return
            
        lower_p = setup.lower_price if not setup.is_dual_grid else setup.g1_lower
        upper_p = setup.upper_price if not setup.is_dual_grid else (setup.g2_upper if setup.g2_upper > 0 else setup.g1_upper)
        grids = setup.grid_quantity if not setup.is_dual_grid else (setup.g1_grids + setup.g2_grids)
        
        msg = f"🕸️ [EXECUTE GRID] {symbol} | Grids: {grids} | Range: {fmt_price(lower_p)} - {fmt_price(upper_p)}"
        
        if self.dry_run:
            logger.info(f"[DRY-RUN] {msg}")
            self._send_zalo_notification(f"[MOCK GRID] Rải lưới {symbol} thành công!")
            return

        # LIVE TRADING MODE
        # Phải check đủ tiền cho n lưới
        total_required = amount_per_grid * grids
        if not self._check_balance(total_required):
            return

        try:
            # TODO: Triển khai rải n lệnh limit theo GridContext
            # Dùng vòng lặp for i in range(setup.num_grids) ...
            logger.warning("🚧 Chức năng rải lưới Live (Real Grid) đang được phát triển.")
            self._send_zalo_notification(f"✅ Rải lưới {symbol} thành công (Dummy)!")
            
        except ccxt.InsufficientFunds as e:
            logger.error(f"❌ [Grid Execution] Không đủ tiền: {e}")
        except ccxt.NetworkError as e:
            logger.error(f"❌ [Grid Execution] Lỗi mạng: {e}")
        except ccxt.ExchangeError as e:
            logger.error(f"❌ [Grid Execution] Lỗi sàn: {e}")
        except Exception as e:
            logger.error(f"❌ [Grid Execution] Lỗi: {e}")
