import ccxt
import logging
import math
import os
import time
from typing import Optional, List
from models.market_state import EntrySetupContext, GridContext, TradeLeg
from core.price_formatter import price_formatter

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
            
        # Cache markets for tickSize formatting
        if self.exchange:
            price_formatter.load_markets(self.exchange)

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
        logger.debug(f"📱 [ZALO ZNS] Đã gửi thông báo: {message}")

    def execute_entry_setup(self, symbol: str, amount: float, setup: EntrySetupContext):
        """
        Định tuyến & Thực thi lệnh cho Động cơ Darvas, Sniper, Breakout, Hot Trend...
        """
        if not setup or not getattr(setup, 'entry_price', None):
            logger.error(f"❌ [Execution] Thiếu thông tin hoặc giá entry cho {symbol}")
            return

        order_type = getattr(setup, 'setup_type', 'UNKNOWN').upper()
        
        # Hỗ trợ cả TradeLeg (tp_price) và EntrySetupContext (tp1_price)
        tp_price = getattr(setup, 'tp1_price', getattr(setup, 'tp_price', None))
        
        msg = f"🚀 [EXECUTE] BUY {order_type} {symbol} @ {fmt_price(setup.entry_price)} | SL: {fmt_price(setup.sl_price)} | TP1: {fmt_price(tp_price)}"
        
        if self.dry_run:
            logger.debug(f"[DRY-RUN] {msg}")
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
            if setup.sl_price and tp_price:
                try:
                    params = {
                        'stopPrice': setup.sl_price,
                        'stopLimitPrice': setup.sl_price,
                        'stopLimitTimeInForce': 'GTC'
                    }
                    oco_order = self.exchange.create_order(
                        symbol, 'limit', 'sell', amount, tp_price, params
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

    def execute_grid_setup(self, symbol: str, amount_per_grid: float, legs: List[TradeLeg] = None, setup=None):
        """
        Thực thi thiết lập lưới Grid bằng DTO chuẩn hóa TradeLeg hoặc GridContext.
        """
        if not legs and setup:
            legs = []
            if not getattr(setup, 'is_dual_grid', False):
                grids = getattr(setup, 'grid_quantity', 0)
                lower = getattr(setup, 'lower_price', 0)
                upper = getattr(setup, 'upper_price', 0)
                sl = getattr(setup, 'stop_loss', 0)
                tp = getattr(setup, 'take_profit', 0)
                if grids > 1 and upper > lower:
                    step = (upper - lower) / (grids - 1)
                    for i in range(grids):
                        price = lower + i * step
                        legs.append(TradeLeg(symbol=symbol, entry_price=price, tp_price=tp, sl_price=sl))

        if not legs:
            # logger.error(f"❌ [Execution] Danh sách Grid rỗng hoặc không thể tạo legs cho {symbol}")
            return
            
        grids = len(legs)
        lower_p = legs[0].entry_price
        upper_p = legs[-1].entry_price
        
        msg = f"🕸️ [EXECUTE GRID] {symbol} | Grids: {grids} | Range: {fmt_price(lower_p)} - {fmt_price(upper_p)}"
        
        if self.dry_run:
            logger.debug(f"[DRY-RUN] {msg}")
            self._send_zalo_notification(f"[MOCK GRID] Rải lưới {symbol} thành công!")
            return

        # LIVE TRADING MODE
        total_required = amount_per_grid * grids
        if not self._check_balance(total_required):
            return

        try:
            logger.info(f"⚙️ Bắt đầu rải {grids} lệnh Grid cho {symbol}...")
            for i, leg in enumerate(legs):
                # 1. Limit Buy cho mắt lưới
                buy_order = self.exchange.create_limit_buy_order(symbol, amount_per_grid, leg.entry_price)
                logger.debug(f"  👉 Lưới {i+1}/{grids}: Buy Limit @ {fmt_price(leg.entry_price)}")
                
                # Tránh Spam API (HTTP 429)
                time.sleep(0.05)
                
                # 2. Tạo OCO cho mắt lưới
                if leg.sl_price and leg.tp_price:
                    try:
                        params = {
                            'stopPrice': leg.sl_price,
                            'stopLimitPrice': leg.sl_price,
                            'stopLimitTimeInForce': 'GTC'
                        }
                        oco_order = self.exchange.create_order(
                            symbol, 'limit', 'sell', amount_per_grid, leg.tp_price, params
                        )
                    except Exception as oco_err:
                        logger.error(f"    ❌ Lỗi OCO lưới {i+1}: {oco_err}")
                
                # Tránh Spam API (HTTP 429)
                time.sleep(0.05)

            self._send_zalo_notification(f"✅ Rải lưới {symbol} ({grids} grids) thành công!")
            
        except ccxt.InsufficientFunds as e:
            logger.error(f"❌ [Grid Execution] Không đủ tiền: {e}")
        except ccxt.NetworkError as e:
            logger.error(f"❌ [Grid Execution] Lỗi mạng: {e}")
        except ccxt.ExchangeError as e:
            logger.error(f"❌ [Grid Execution] Lỗi sàn: {e}")
        except Exception as e:
            logger.error(f"❌ [Grid Execution] Lỗi: {e}")
