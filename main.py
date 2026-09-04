import os
import sys
import io
import time
import json
import logging
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from strategies.macro_pullback.pullback_sniper import PullbackSniper
from strategies.macro_pullback.entry_calculator_service import (
    EntryCalculatorService, EntryCalculatorServiceError
)
from strategies.macro_grid_darvas import MacroGridDarvas
from strategies.momentum_breakout import MomentumBreakout
from strategies.hot_trend_pullback import HotTrendPullback
from core.coin_filter import get_filtered_symbols
from core.early_warning import EarlyWarningMatrix
from views.console_renderer import ConsoleRenderer
from models.market_state import SymbolState, ScoreContext, GridContext, MacroState, EarlyWarningContext, EntrySetupContext
from core.market_data_repo import MarketDataRepository
from core.cache_service import CacheService
from engines.dc1_darvas_engine import DC1DarvasEngine
from engines.dc2_sniper_engine import DC2SniperEngine
from engines.dc3_breakout_engine import DC3BreakoutEngine
from engines.dc4_hot_trend_engine import DC4HotTrendEngine
from core.exchange_factory import get_working_exchange
from execution.trade_execution_service import TradeExecutionService
from engines.dc3_breakout_engine import DC3BreakoutEngine
from engines.dc4_hot_trend_engine import DC4HotTrendEngine
from core.grid_calculator import GridCalculator

# Thiết lập logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("CN4-Controller")

# Tắt log spam từ thư viện ngoài (urllib3 pool full, ccxt verbose, v.v.)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)
logging.getLogger("ccxt").setLevel(logging.ERROR)
logging.getLogger("asyncio").setLevel(logging.ERROR)
logging.getLogger("CN4.Engine2.EntryCalc").setLevel(logging.ERROR)  # Tắt INFO/WARNING với EntryCalc — chỉ hiện kết quả trên console

def load_settings():
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'settings.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Không tìm thấy file cấu hình tại {config_path}")
        return {}

def fmt_price(price: float) -> str:
    """Format giá tự động: đủ số lẻ cho MEME coin (PEPE, SHIB...) mà không thừa số 0.
    Ví dụ: 0.00001234 → '0.00001234', 0.1234 → '0.1234', 65000.5 → '65000.5'
    """
    if price is None:
        return "N/A"
    if price == 0:
        return "0"
    # Tìm số chữ số thập phân cần thiết để hiện tối thiểu 4 chữ số có nghĩa
    import math
    if price >= 1:
        # Coin lớn: tối đa 4 số lẻ
        decimals = 4
    else:
        # Tính vị trí chữ số có nghĩa đầu tiên
        first_sig = -math.floor(math.log10(abs(price)))
        # Hiển thị đủ 4 chữ số có nghĩa sau chữ số 0
        decimals = first_sig + 3  # 4 chữ số có nghĩa
        decimals = min(decimals, 12)  # tối đa 12 số lẻ
    formatted = f"{price:.{decimals}f}"
    # Bỏ số 0 thừa cuối (nhưng giữ lại ít nhất 1 số lẻ)
    if '.' in formatted:
        formatted = formatted.rstrip('0').rstrip('.')
        if '.' not in formatted:
            formatted += '.0'
    return formatted

def main():
    logger.info("Khởi động hệ thống giao dịch CN4-Platform...")
    
    # Load biến môi trường
    load_dotenv()
    api_key = os.getenv("BINANCE_API_KEY")
    secret_key = os.getenv("BINANCE_SECRET_KEY")
    
    if not api_key or not secret_key:
        logger.warning("Chưa cấu hình API Key và Secret Key trong file .env! Hệ thống sẽ chạy ở chế độ MOCK (Chỉ quét, không vào lệnh thực).")
        api_key = ""
        secret_key = ""

    # Load cấu hình
    settings = load_settings()
    timeframe = settings.get("trading", {}).get("default_timeframe", "4h")
    
    logger.info(f"Timeframe: {timeframe}")

    # Khởi tạo DI Container
    repo = MarketDataRepository()
    cache = CacheService(repo)
    
    exchange = get_working_exchange(api_key, secret_key) if (api_key and secret_key) else None
    executor = TradeExecutionService(exchange)
    
    # Khởi tạo các module cốt lõi
    sniper         = PullbackSniper()
    entry_calc     = EntryCalculatorService()   # DC2 Lõi Tính Toán (chạy sau PullbackSniper)
    darvas         = MacroGridDarvas()
    breakout       = MomentumBreakout()
    hot_trend      = HotTrendPullback()
    grid_calc      = GridCalculator()
    
    # Khởi tạo Engines (Controller Layer)
    dc1_engine = DC1DarvasEngine(strategy=darvas)
    dc2_engine = DC2SniperEngine(strategy=sniper, early_warning=EarlyWarningMatrix(), entry_calc=entry_calc, repo=repo)
    dc3_engine = DC3BreakoutEngine(strategy=breakout)
    dc4_engine = DC4HotTrendEngine(strategy=hot_trend, grid_calc=grid_calc)
    
    # Chạy 1 lần (Manual Scan Mode)
    if True:
        try:
            logger.info("Đang chạy module coin_filter để quét các mã tiềm năng...")
            
            # ── Bắt đầu chụp stdout của coin_filter để in sau ──────────────
            _coin_filter_buf = io.StringIO()
            _real_stdout = sys.stdout
            sys.stdout = _coin_filter_buf
            try:
                # Cập nhật Cache trước khi chạy các bộ lọc
                live_data_map = cache.get_live_data_map()
                watchlist, safety_map = get_filtered_symbols(live_data_map)
            finally:
                sys.stdout = _real_stdout
            _coin_filter_output = _coin_filter_buf.getvalue()
            # ────────────────────────────────────────────────────────────────
            
            if not watchlist:
                logger.warning("Không tìm thấy mã nào đạt điều kiện từ coin_filter.")
            else:
                logger.info(f"Đã lọc được {len(watchlist)} mã tiềm năng.")
            
            # ── 1. In ⏰ Thời điểm + số lượng mã trước tiên ─────────────────
            _timestamp_line = ""
            _count_line = ""
            for _line in _coin_filter_output.splitlines():
                if "⏰ Thời điểm cập nhật" in _line and not _timestamp_line:
                    _timestamp_line = _line.strip()
                if "MÃ ĐƯỢC ĐƯA VÀO BẢNG CHẤM ĐIỂM" in _line and not _count_line:
                    _count_line = _line.strip()
            
            print()
            if _timestamp_line:
                print(_timestamp_line)
            if _count_line:
                print(_count_line)
            if watchlist:
                print(f"📋 Danh mục watchlist: {len(watchlist)} mã đủ điều kiện vào động cơ phân tích.")
            print()

            ENGINE_TOP_N = settings.get("trading", {}).get("engine_top_n", 20)

            if watchlist:
                pre_ew_limit = ENGINE_TOP_N * 2
                if len(watchlist) > pre_ew_limit:
                    logger.info(f"⚡ Tối ưu hiệu suất: Giảm danh sách quét EW từ {len(watchlist)} xuống Top {pre_ew_limit} mã.")
                    watchlist = watchlist[:pre_ew_limit]
                    
                # =========================================================
                # 0. HỆ THỐNG CẢNH BÁO SỚM & RỦI RO SẬP (Early Warning Matrix)
                # =========================================================
                early_warning = EarlyWarningMatrix()
                from concurrent.futures import ThreadPoolExecutor
                from core.exchange_factory import get_working_exchange
                import pandas as pd
                
                exchange = get_working_exchange()
                logger.info("Dang kich hoat He thong Canh bao Som (Early Warning Matrix)...")
                warning_results = []
                safe_watchlist = []
                
                repo = MarketDataRepository()
                def _check_ew(sym):
                    try:
                        sym_api = sym.replace('/', '')
                        df_1h = repo.get_klines_df(sym_api, '1h', 50)
                        df_4h = repo.get_klines_df(sym_api, '4h', 50)
                        df_1d = repo.get_klines_df(sym_api, '1d', 50)
                        
                        res = early_warning.check_warning_level(df_1h, df_4h, df_1d)
                        res['symbol'] = sym
                        return res
                    except Exception as e:
                        logger.error(f"Loi EW cho {sym}: {e}")
                        return {'symbol': sym, 'level': 0, 'label': '', 'trigger': ''}

                with ThreadPoolExecutor(max_workers=10) as _ew_pool:
                    warning_results = list(_ew_pool.map(_check_ew, watchlist))
                safe_watchlist = [r['symbol'] for r in warning_results if r.get('level', 0) < 2]
                
                
                # In Bảng Cảnh Báo
                renderer = ConsoleRenderer()
                renderer.render_early_warning_matrix(warning_results, len(watchlist))
                
                # Cập nhật watchlist thành safe_watchlist
                watchlist = safe_watchlist
                
                # ── Giới hạn watchlist xuống Top N mã trước khi chạy các bước tốn kém ──
                # Lấy Top các mã An toàn tốt nhất sau khi đã qua màng lọc Early Warning.
                if watchlist and len(watchlist) > ENGINE_TOP_N:
                    logger.info(f"⚡ Giới hạn phân tích sâu: {len(watchlist)} → Top {ENGINE_TOP_N} mã (cấu hình engine_top_n).")
                    watchlist = watchlist[:ENGINE_TOP_N]
                
                if not watchlist:
                    logger.warning("Toàn bộ danh mục bị BẤT HOẠT do rủi ro sập. Nghỉ ngơi chu kỳ này.")
                    
            if watchlist:
                # =========================================================
                # 1. Chạy Động Cơ 1 (Macro Grid Darvas)
                # =========================================================
                dc1_states = dc1_engine.run(watchlist, live_data_map, timeframe=timeframe, safety_map=safety_map)
                renderer.render_darvas_grid(dc1_states)
                
                # Kích hoạt GridManager
                for state in dc1_states:
                    score = state.scores["DC1"].total_score
                    if score >= 80:
                        symbol = state.symbol
                        g_setup = state.scores["DC1"].grid_setup
                        if not g_setup: continue
                        
                        is_dual = g_setup.is_dual_grid
                        
                        if is_dual:
                            g1_lower = g_setup.g1_lower
                            g1_upper = g_setup.g1_upper
                            g1_grids = g_setup.g1_grids
                            g2_lower = g_setup.g2_lower
                            g2_upper = g_setup.g2_upper
                            g2_grids = g_setup.g2_grids
                            
                            if g1_lower and g1_upper and g1_grids and g2_lower and g2_upper and g2_grids:
                                logger.debug(f"🤖 [DUAL GRID TÍN HIỆU] Khởi tạo hệ thống Lưới Kép cho {symbol}")
                                if api_key:
                                    grid_manager.launch_grid(symbol=symbol, upper_price=g1_upper, lower_price=g1_lower, grids=g1_grids, amount_per_grid=14.0)
                                    grid_manager.launch_grid(symbol=symbol, upper_price=g2_upper, lower_price=g2_lower, grids=g2_grids, amount_per_grid=6.0)
                                else:
                                    logger.debug(f"[MOCK MODE] Sẽ kích hoạt Lưới Kép {symbol} -> G1(L:{g1_lower}, U:{g1_upper}, {g1_grids}L) | G2(L:{g2_lower}, U:{g2_upper}, {g2_grids}L)")
                        else:
                            lower_price = g_setup.lower_price
                            upper_price = g_setup.upper_price
                            grids = g_setup.grid_quantity
                            
                            if lower_price and upper_price and grids:
                                amount_per_grid = 10.0
                                logger.debug(f"🤖 [GRID TÍN HIỆU] Khởi tạo Bot Grid cho {symbol}")
                                if api_key:
                                    grid_manager.launch_grid(
                                        symbol=symbol,
                                        upper_price=upper_price,
                                        lower_price=lower_price,
                                        grids=grids,
                                        amount_per_grid=amount_per_grid
                                    )
                                else:
                                    logger.debug(f"[MOCK MODE] Sẽ kích hoạt Bot Grid {symbol}: Lower={lower_price}, Upper={upper_price}, Lưới={grids}")

                # =========================================================
                # 2. Chạy Động Cơ 2 (Pullback Sniper)
                # =========================================================
                logger.info("[DC2] Kiem tra Macro Trend 1D truoc khi cham diem Pullback...")
                avg_vola_24h = cache.get_avg_vola_24h()
                
                dc2_states = dc2_engine.run(watchlist, live_data_map, avg_vola_24h, timeframe=timeframe, safety_map=safety_map)
                renderer.render_pullback_sniper(dc2_states)

                # Kích hoạt thực thi cho các mã đạt điểm
                for state in dc2_states:
                    score = state.scores["DC2"].total_score
                    if score >= 85:
                        symbol = state.symbol
                        setup1 = state.scores["DC2"].entry_setup1
                        setup2 = state.scores["DC2"].entry_setup2
                        
                        if setup1 and setup2:
                            logger.warning(f"[TIN HIEU DC2] {symbol} APPROVED | "
                                           f"Entry={fmt_price(setup1.entry_price)} "
                                           f"| OCO-1 SL={fmt_price(setup1.sl_price)} TP={fmt_price(setup1.tp1_price)} "
                                           f"| OCO-2 SL={fmt_price(setup2.sl_price)} TP={fmt_price(setup2.tp1_price)}")
                            if not api_key:
                                logger.info(f"[MOCK MODE] {symbol}: OCO-1 Buy Limit @ {fmt_price(setup1.entry_price)} "
                                            f"SL={fmt_price(setup1.sl_price)} TP={fmt_price(setup1.tp1_price)} | "
                                            f"OCO-2 Buy Limit @ {fmt_price(setup2.entry_price)} "
                                            f"SL={fmt_price(setup2.sl_price)} TP={fmt_price(setup2.tp1_price)}")

                # =========================================================
                # 3. Chạy Động Cơ 3 (Momentum Breakout)

                
                # =========================================================
                # 3. Chạy Động Cơ 3 (Momentum Breakout)
                # =========================================================
                dc3_states, btc_gate_label = dc3_engine.run(watchlist, live_data_map, timeframe=timeframe, safety_map=safety_map)
                renderer.render_momentum_breakout(dc3_states, btc_gate_label)
                
                # =========================================================
                # 4. Chạy Động Cơ 4 (Hot Trend Pullback) — Độc lập với watchlist
                # =========================================================
                dc4_states, htb_count, htb_change_threshold, tracking_list = dc4_engine.run(watchlist, live_data_map, safety_map=safety_map)
                renderer.render_hot_trend_pullback(dc4_states, htb_count, htb_change_threshold, tracking_list)
                
                # ── 6-8. In các bảng từ coin_filter sau cùng ────────────────
                if _coin_filter_output:
                    _skip_prefixes = ("🔍 Đang gọi", "🎯 TỔNG CỘNG", "🌱 Đang quét", "🚀 Đang quét")
                    _filtered_lines = [_line for _line in _coin_filter_output.splitlines() if not any(_line.strip().startswith(p) for p in _skip_prefixes)]
                    renderer.render_coin_filter_results(_filtered_lines)
                
                # =========================================================
                # 5. KÍCH HOẠT THỰC THI (EXECUTION LAYER)
                # =========================================================
                # Dò tìm lệnh Breakout
                for state in dc3_states:
                    score = state.scores["DC3"].total_score
                    if score >= 85:
                        setup = state.scores["DC3"].entry_setup1
                        if setup:
                            executor.execute_entry_setup(symbol=state.symbol, amount=0.01, setup=setup)
                            
                # Dò tìm lệnh Sniper
                for state in dc2_states:
                    score = state.scores["DC2"].total_score
                    if score >= 70:
                        setup = state.scores["DC2"].entry_setup1
                        if setup:
                            executor.execute_entry_setup(symbol=state.symbol, amount=0.01, setup=setup)

                # Dò tìm lệnh Grid
                for state in dc1_states:
                    score = state.scores["DC1"].total_score
                    if score >= 55:
                        setup = state.scores["DC1"].grid_setup
                        if setup:
                            executor.execute_grid_setup(symbol=state.symbol, amount_per_grid=0.01, setup=setup)
            logger.info("Hoàn tất quét thị trường. Chương trình kết thúc.")
            
        except Exception as e:
            logger.error(f"Lỗi hệ thống trong quá trình quét: {e}")

if __name__ == "__main__":
    main()
