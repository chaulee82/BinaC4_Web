import os
import sys
import io
import time
import json
import logging
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    # line_buffering=True: flush từng dòng → print() và logging (stderr) không bị đảo thứ tự / rơi dòng tiêu đề
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

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
from models.market_state import SymbolState, ScoreContext, GridContext, MacroState, EarlyWarningContext, EntrySetupContext, MacroLevels
from core.market_data_repo import MarketDataRepository
from core.cache_service import CacheService
from engines.dc1_darvas_engine import DC1DarvasEngine
from engines.dc2_sniper_engine import DC2SniperEngine
from engines.dc3_breakout_engine import DC3BreakoutEngine
from engines.dc4_hot_trend_engine import DC4HotTrendEngine
from engines.dc5_pingpong_uptrend_engine import DC5PingpongUptrendEngine
from strategies.grid_pingpong_uptrend import GridPingpongUptrendScorer
from core.exchange_factory import get_working_exchange
from execution.trade_execution_service import TradeExecutionService
from engines.dc3_breakout_engine import DC3BreakoutEngine
from engines.dc4_hot_trend_engine import DC4HotTrendEngine
from core.grid_calculator import GridCalculator
from core.exchange_info_cache import ExchangeInfoCache
from core.macro_levels import calculate_universal_macro_levels, get_3d_profile
from core.history_logger import HistoryLogger
from core.klines_cache import clear_klines_cache
import atexit
import gc

LOCK_FILE = os.path.join(os.path.dirname(__file__), 'data', 'binac4_main.lock')

def get_concurrent_main_pids() -> list:
    """Trả về danh sách PID của các tiến trình main.py thực sự khác đang chạy song song.
    
    Tự động lọc bỏ:
    - Chính tiến trình hiện tại (os.getpid())
    - Tiến trình cha gọi nó (os.getppid()) như sh/bash/cron/auto_runner wrapper
    - Tiến trình Zombie/Defunct (đã kết thúc nhưng chưa được giải phóng)
    - Các script quản lý khác (auto_runner.py, web_app.py, v.v.)
    """
    my_pid = os.getpid()
    parent_pid = os.getppid() if hasattr(os, 'getppid') else None
    ignored_pids = {my_pid}
    if parent_pid:
        ignored_pids.add(parent_pid)

    other_pids = []

    def is_target_main_cmd(cmdline_list: list) -> bool:
        if not cmdline_list:
            return False
        cmd_str = " ".join(cmdline_list)
        # Bỏ qua các wrapper quản lý
        if "auto_runner.py" in cmd_str or "web_app.py" in cmd_str:
            return False
        # Phải là lệnh python
        has_python = any("python" in str(arg).lower() or "venv" in str(arg).lower() for arg in cmdline_list[:2])
        if not has_python:
            return False
        # Phải có đối số là main.py đích thực (tránh test_main.py hay chuỗi con)
        has_main = any(
            arg == "main.py" or arg.endswith("/main.py") or arg.endswith("\\main.py")
            for arg in cmdline_list
        )
        return has_main

    # 1. Quét bằng psutil (chuẩn xác nhất, có sẵn trong requirements.txt)
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status']):
            try:
                pid = proc.info.get('pid')
                if not pid or pid in ignored_pids:
                    continue
                # Bỏ qua Zombie / Defunct process
                if proc.info.get('status') == psutil.STATUS_ZOMBIE:
                    continue
                cmdline = proc.info.get('cmdline') or []
                if is_target_main_cmd(cmdline):
                    other_pids.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return other_pids
    except ImportError:
        pass

    # 2. Fallback quét /proc trên Linux VPS (khi chưa cài psutil)
    if os.path.exists('/proc'):
        try:
            for entry in os.listdir('/proc'):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid in ignored_pids:
                    continue
                try:
                    # Kiểm tra trạng thái để loại trừ zombie
                    status_path = f"/proc/{pid}/status"
                    if os.path.exists(status_path):
                        with open(status_path, "r", encoding="utf-8", errors="ignore") as f:
                            stat_text = f.read(256)
                            if "State:\tZ" in stat_text or "State: Z" in stat_text:
                                continue

                    cmdline_path = f"/proc/{pid}/cmdline"
                    if os.path.exists(cmdline_path):
                        with open(cmdline_path, "rb") as f:
                            raw = f.read().decode("utf-8", errors="ignore")
                            args = [a for a in raw.split("\0") if a]
                            if is_target_main_cmd(args):
                                other_pids.append(pid)
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    continue
            return other_pids
        except Exception:
            pass

    # 3. Fallback bằng lệnh pgrep trên Unix
    if sys.platform != 'win32':
        try:
            import subprocess
            out = subprocess.check_output(['pgrep', '-f', 'main.py'], text=True)
            for line in out.strip().splitlines():
                if line.isdigit():
                    p = int(line)
                    if p not in ignored_pids and p not in other_pids:
                        try:
                            state = subprocess.check_output(['ps', '-o', 'stat=', '-p', str(p)], text=True).strip()
                            if state.startswith('Z'):
                                continue
                        except Exception:
                            pass
                        other_pids.append(p)
        except Exception:
            pass

    return other_pids


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
    # ── Kiểm tra và cảnh báo tiến trình song song (vẫn ưu tiên chạy tiếp) ──
    other_pids = get_concurrent_main_pids()
    if other_pids:
        warn_msg = (
            f"\n"
            f"╔══════════════════════════════════════════════════════════════════════════════╗\n"
            f"║ ⚠️  CẢNH BÁO TÀI NGUYÊN: CÓ {len(other_pids)} TIẾN TRÌNH main.py ĐANG CHẠY SONG SONG           ║\n"
            f"╠══════════════════════════════════════════════════════════════════════════════╣\n"
            f"║ • PIDs đang chạy ngầm: {other_pids}\n"
            f"║ • Khuyến nghị: Bạn nên tắt bớt trên VPS (chạy: kill -9 {' '.join(map(str, other_pids))})\n"
            f"║   để giải phóng RAM cho VPS và tránh bị nghẽn hệ thống.\n"
            f"║ ⚡ ƯU TIÊN TIẾP TỤC: Bot vẫn tiếp tục phiên quét này theo yêu cầu...         ║\n"
            f"╚══════════════════════════════════════════════════════════════════════════════╝\n"
        )
        print(warn_msg)
        logger.warning(f"Phát hiện {len(other_pids)} tiến trình main.py khác đang chạy (PIDs: {other_pids}). Tiếp tục phiên quét.")

    start_time = time.time()
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

    # ── Đọc tham số Macro Levels từ settings.json ──────────────────────────
    _ml_cfg = settings.get("macro_levels", {})
    _sl_atr_mult  = float(_ml_cfg.get("sl_atr_multiplier",   1.5))
    _sl_margin    = float(_ml_cfg.get("sl_margin_pct",        0.03))

    # ── Khởi tạo Exchange Info Cache (1 lần duy nhất khi startup) ──────────
    exc_info = ExchangeInfoCache()
    _exc_loaded = exc_info.load()
    if not _exc_loaded:
        logger.warning("[ExchangeInfoCache] Không load được tick_size — Macro Levels sẽ dùng giá trị mặc định.")

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
    pingpong       = GridPingpongUptrendScorer()
    
    # Khởi tạo Engines (Controller Layer)
    dc1_engine = DC1DarvasEngine(strategy=darvas)
    dc2_engine = DC2SniperEngine(strategy=sniper, early_warning=EarlyWarningMatrix(), entry_calc=entry_calc, repo=repo)
    dc3_engine = DC3BreakoutEngine(strategy=breakout)
    dc4_engine = DC4HotTrendEngine(strategy=hot_trend, grid_calc=grid_calc)
    dc5_engine = DC5PingpongUptrendEngine(strategy=pingpong)
    
    # Khởi tạo Bộ nhớ lịch sử
    history_logger = HistoryLogger()
    history_logger.clean_old_records(30)
    
    # Chạy 1 lần (Manual Scan Mode)
    if True:
        try:
            logger.info("Đang chạy module coin_filter để quét các mã tiềm năng...")
            
            # Cập nhật Cache trước khi chạy các bộ lọc
            live_data_map = cache.get_live_data_map()
            
            # Chạy lọc mã trực tiếp ra màn hình (không ẩn log) để thấy tiến độ
            watchlist, safety_map, early_list, df_summary, current_time_str = get_filtered_symbols(live_data_map)
            gc.collect() # Giải phóng RAM ngay sau bước lọc mã toàn thị trường
            
            # Gán rỗng vì output đã được in trực tiếp
            _coin_filter_output = ""
            
            if not watchlist:
                logger.warning("Không tìm thấy mã nào đạt điều kiện từ coin_filter.")
            else:
                pass # logger.info(f"Đã lọc được {len(watchlist)} mã tiềm năng.")
            
            print()
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
                        df_15m = repo.get_klines_df(sym_api, '15m', 250)

                        # ── Hồ sơ Khung 3D (gộp từ 300 nến 1D trong RAM — 0 API call khi cache ấm) ──
                        tick_size = exc_info.get_tick_size(sym_api)
                        d3 = get_3d_profile(sym_api, tick_size)

                        res = early_warning.check_warning_level(df_1h, df_4h, df_1d, d3=d3)
                        res['symbol'] = sym
                        res['d3'] = d3

                        # ── Tính Macro Levels ngay trong luồng EW ──────────
                        # df_4h & df_1h đã có sẵn trong RAM (klines_cache hit)
                        # → Zero extra API calls
                        ml_result = calculate_universal_macro_levels(
                            klines_4h_df=df_4h,
                            klines_1h_df=df_1h,
                            tick_size=tick_size,
                            klines_15m_df=df_15m,
                            sl_atr_multiplier=_sl_atr_mult,
                            sl_margin_pct=_sl_margin,
                        )
                        if ml_result.get('status') == 'SUCCESS':
                            res['macro_levels'] = MacroLevels(
                                entry_4h=ml_result['entry_4h'],
                                tp_1h   =ml_result['tp_1h'],
                                tp_4h   =ml_result['tp_4h'],
                                sl_4h   =ml_result['sl_4h'],
                                d3      =d3,
                            )
                        else:
                            res['macro_levels'] = None
                            logger.debug(f"[MacroLevels] {sym}: {ml_result.get('message', 'error')}")

                        return res
                    except Exception as e:
                        logger.error(f"Loi EW cho {sym}: {e}")
                        return {'symbol': sym, 'level': 0, 'label': '', 'trigger': '', 'macro_levels': None}

                with ThreadPoolExecutor(max_workers=10) as _ew_pool:
                    warning_results = list(_ew_pool.map(_check_ew, watchlist))
                gc.collect() # Dọn dẹp RAM sau ma trận cảnh báo sớm
                original_watchlist = list(watchlist)
                safe_watchlist = [r['symbol'] for r in warning_results if r.get('level', 0) < 2]
                
                
                # In Bảng Cảnh Báo
                renderer = ConsoleRenderer()
                renderer.render_early_warning_matrix(warning_results, len(watchlist))
                
                # Cập nhật safety_map đồng bộ cho tất cả các động cơ (DC1, DC2, DC3, DC4, DC5)
                for r in warning_results:
                    if "Miễn Cấp 3" in r.get("label", ""):
                        _sym = r.get("symbol", "")
                        _tag = "🛡️ EW CẤP 1 (Miễn Cấp 3 - Uptrend 3D)"
                        safety_map[_sym] = _tag
                        safety_map[_sym.replace("/USDT", "")] = _tag
                        safety_map[_sym.replace("/", "")] = _tag

                # Cập nhật watchlist thành safe_watchlist
                watchlist = safe_watchlist
                
                # ── Giới hạn watchlist xuống Top N mã trước khi chạy các bước tốn kém ──
                # Lấy Top các mã An toàn tốt nhất sau khi đã qua màng lọc Early Warning.
                if watchlist and len(watchlist) > ENGINE_TOP_N:
                    logger.info(f"⚡ Giới hạn phân tích sâu: {len(watchlist)} → Top {ENGINE_TOP_N} mã (cấu hình engine_top_n).")
                    watchlist = watchlist[:ENGINE_TOP_N]
                
                dc2_watchlist = original_watchlist
                if dc2_watchlist and len(dc2_watchlist) > ENGINE_TOP_N:
                    dc2_watchlist = dc2_watchlist[:ENGINE_TOP_N]

                if not watchlist and not dc2_watchlist:
                    logger.warning("Toàn bộ danh mục bị BẤT HOẠT do rủi ro sập. Nghỉ ngơi chu kỳ này.")
                    
            if watchlist:
                # ── Build macro_levels_map: sym → MacroLevels (lookup O(1) cho engine) ──
                # warning_results đã chứa macro_levels cho mỗi mã từ bước _check_ew
                macro_levels_map = {
                    r['symbol']: r.get('macro_levels')
                    for r in warning_results
                    if r.get('macro_levels') is not None
                }

                # 🛡️ Reset sổ mã trượt cổng Strict 3D cho lượt quét mới
                from core.macro_levels import reset_strict_3d_rejections
                reset_strict_3d_rejections()

                # =========================================================
                # 1. Chạy Động Cơ 1 (Macro Grid Darvas)
                # =========================================================
                dc1_states = dc1_engine.run(watchlist, live_data_map, timeframe=timeframe, safety_map=safety_map, macro_levels_map=macro_levels_map)
                renderer.render_darvas_grid(dc1_states)
                
                # Kích hoạt GridManager — CHỈ mã qua cổng Strict 3D (mã ⛔ [3D STRICT] vẫn hiển thị nhưng không mở bot)
                from core.macro_levels import validate_strict_3d_wave
                for state in dc1_states:
                    score = state.scores["DC1"].total_score
                    if score >= 80:
                        symbol = state.symbol
                        g_setup = state.scores["DC1"].grid_setup
                        if not g_setup: continue
                        if not validate_strict_3d_wave(symbol).get("is_valid"): continue
                        
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
                ew_label_map = {r.get('symbol', ''): r.get('label', '') for r in warning_results}
                dc2_states = dc2_engine.run(dc2_watchlist, live_data_map, avg_vola_24h, timeframe=timeframe, safety_map=safety_map, macro_levels_map=macro_levels_map, ew_label_map=ew_label_map)
                renderer.render_pullback_sniper(dc2_states)

                # Kích hoạt thực thi cho các mã đạt điểm
                for state in dc2_states:
                    score = state.scores["DC2"].total_score
                    if score >= 85:
                        symbol = state.symbol
                        setup1 = state.scores["DC2"].entry_setup1
                        setup2 = state.scores["DC2"].entry_setup2
                        
                        if setup1 and setup2 and "GÃY MA7 3D" not in (state.scores["DC2"].action_label or ""):
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
                dc3_states, btc_gate_label = dc3_engine.run(watchlist, live_data_map, timeframe=timeframe, safety_map=safety_map, macro_levels_map=macro_levels_map)
                renderer.render_momentum_breakout(dc3_states, btc_gate_label)
                
                # =========================================================
                # 4. Chạy Động Cơ 4 (Hot Trend Pullback) — Độc lập với watchlist
                # =========================================================
                dc4_states, htb_count, htb_change_threshold, tracking_list = dc4_engine.run(watchlist, live_data_map, safety_map=safety_map, macro_levels_map=macro_levels_map)
                renderer.render_hot_trend_pullback(dc4_states, htb_count, htb_change_threshold, tracking_list)
                
                # =========================================================
                # 5. Chạy Động Cơ 5 (Grid Pingpong)
                # =========================================================
                dc5_states = dc5_engine.run(watchlist, live_data_map, safety_map=safety_map, macro_levels_map=macro_levels_map)
                renderer.render_grid_pingpong(dc5_states)
                
                # ── 6-8. In các bảng từ coin_filter sau cùng ────────────────
                if _coin_filter_output:
                    _skip_prefixes = ("🔍 Đang gọi", "🎯 TỔNG CỘNG", "🌱 Đang quét", "🚀 Đang quét")
                    _filtered_lines = [_line for _line in _coin_filter_output.splitlines() if not any(_line.strip().startswith(p) for p in _skip_prefixes)]
                    renderer.render_coin_filter_results(_filtered_lines)
                
                # =========================================================
                # 5. KÍCH HOẠT THỰC THI (EXECUTION LAYER)
                # =========================================================
                # Dò tìm lệnh Breakout — Execution Gatekeeper: điểm ≥ 85 VÀ Breakout trên nến 4H ĐÃ ĐÓNG VÀ SL < 15%
                from strategies.momentum_breakout import DC3_MAX_SL_PCT
                for state in dc3_states:
                    ctx = state.scores["DC3"]
                    if ctx.total_score >= 85:
                        if not ctx.execution_allowed:
                            logger.warning(f"[DC3 VETO] {state.symbol}: {ctx.total_score}đ nhưng không bóp cò — "
                                           f"{ctx.execution_veto_reason or 'Chưa xác nhận Breakout nến đã đóng'}")
                            continue
                        setup = ctx.entry_setup1
                        if setup:
                            # Phòng thủ lớp 2: kiểm lại độ rộng SL ngay trên setup sắp gửi sàn
                            _e, _sl = getattr(setup, "entry_price", 0) or 0, getattr(setup, "sl_price", 0) or 0
                            _sl_pct = (_e - _sl) / _e * 100 if _e > 0 and 0 < _sl < _e else None
                            if _sl_pct is not None and round(_sl_pct, 6) >= DC3_MAX_SL_PCT:
                                logger.warning(f"[DC3 VETO] {state.symbol}: Không bóp cò — SL quá rộng "
                                               f"({_sl_pct:.1f}% ≥ {DC3_MAX_SL_PCT:.0f}%)")
                                continue
                            executor.execute_entry_setup(symbol=state.symbol, amount=0.01, setup=setup)
                            
                # Dò tìm lệnh Sniper
                for state in dc2_states:
                    score = state.scores["DC2"].total_score
                    if score >= 70:
                        setup = state.scores["DC2"].entry_setup1
                        if setup and "GÃY MA7 3D" not in (state.scores["DC2"].action_label or ""):
                            executor.execute_entry_setup(symbol=state.symbol, amount=0.01, setup=setup)

                # Dò tìm lệnh Grid — chỉ mã qua cổng Strict 3D (DC1 engine đã lọc, kiểm lại 1 lớp phòng thủ)
                from core.macro_levels import validate_strict_3d_wave
                for state in dc1_states:
                    score = state.scores["DC1"].total_score
                    if score >= 55:
                        setup = state.scores["DC1"].grid_setup
                        if setup and validate_strict_3d_wave(state.symbol).get("is_valid"):
                            executor.execute_grid_setup(symbol=state.symbol, amount_per_grid=0.01, setup=setup)
            # =========================================================
            # 🏁 BẢNG TỔNG KẾT TỐI ƯU (SUMMARY_BOARD) — in ngay TRƯỚC Bảng Rebalance / Spot Grid
            # =========================================================
            _renderer = locals().get('renderer')
            _btc_status = locals().get('btc_gate_label', '') or ''
            _warning_results = locals().get('warning_results')
            
            def _summary_hook():
                if _renderer is not None:
                    # Lưu tín hiệu vào DB trước khi lên bảng tổng kết
                    history_logger.save_signals(_renderer.signals_pool)
                    
                    from views.summary_board import generate_summary_board
                    from core.macro_levels import get_strict_3d_rejections
                    from strategies.super_trend_3d import get_last_super_wave_results
                    _summary_board_res = generate_summary_board(
                        global_signals_pool=_renderer.signals_pool,
                        btc_status=_btc_status,
                        live_data_map=live_data_map,
                        df_summary=df_summary,
                        warning_results=_warning_results,
                        config=settings.get("summary_board"),
                        strict_3d_rejections=get_strict_3d_rejections(),
                        super_wave_results=get_last_super_wave_results(),
                        history_logger=history_logger,
                    )
                
                # 📊 BẢNG TỔNG HỢP HỒ SƠ TÍN NHIỆM 14 NGÀY (HISTORICAL RATER) — in ngay TRƯỚC Bảng Rebalance
                try:
                    from core.historical_rater import print_14_day_summary_board
                    from strategies.super_trend_3d import get_last_super_wave_results
                    _conv_ranks = _summary_board_res.get('convergence_ranks') if '_summary_board_res' in locals() and isinstance(_summary_board_res, dict) else None
                    _v2_profiles = print_14_day_summary_board(
                        db_path="data/binac4_history.db",
                        df_summary=df_summary,
                        early_list=early_list,
                        super_wave_results=get_last_super_wave_results(),
                        warning_results=_warning_results,
                        convergence_ranks=_conv_ranks,
                        top_n=25,
                        scan_time=current_time_str if 'current_time_str' in locals() and current_time_str else None
                    )

                    # -------------------------------------------------------------------------
                    # BƯỚC TÍCH HỢP: LƯU VẾT & PHÂN TÍCH DELTA AUDIT (MOD-TRUST-HIST-01)
                    # -------------------------------------------------------------------------
                    _grid_gom_items = []
                    if _v2_profiles:
                        try:
                            from trust_history import record_and_analyze, print_delta_tables
                            top_gainers, top_losers = record_and_analyze(_v2_profiles, keep_snapshots=24)
                            _grid_gom_items = print_delta_tables(
                                top_gainers,
                                top_losers,
                                live_data_map=locals().get('live_data_map'),
                                df_summary=locals().get('df_summary'),
                                global_signals_pool=_renderer.signals_pool if _renderer else None,
                                warning_results=_warning_results if '_warning_results' in locals() else None
                            ) or []
                        except Exception as err:
                            import logging
                            logging.getLogger("CN4-Controller").error(f"Lỗi phân tích Trust History (Multi-Day Delta Audit): {err}", exc_info=True)

                        # 🗂️ SNAPSHOT TĨNH CHO WEB UI (Tái sử dụng _v2_profiles trong RAM, 0 query thừa)
                        try:
                            from core.symbol_inspector import save_web_snapshot
                            save_web_snapshot(db_path="data/binac4_history.db", profiles=_v2_profiles)
                        except Exception as e_snap:
                            logger.debug(f"Không thể lưu web snapshot từ main.py: {e_snap}")
                except Exception as e:
                    logger.error(f"Render Error (Bảng 14 Ngày / Delta Audit): {e}")

                # -------------------------------------------------------------------------
                # 📌 BẢNG CHIẾN LƯỢC RẢI ĐINH PINGPONG (SPLIT-ENTRY v2.7) — In trước Vùng 1
                # -------------------------------------------------------------------------
                try:
                    from views.split_entry_board import generate_split_entry_board
                    generate_split_entry_board(
                        convergence_ranks=_summary_board_res.get('convergence_ranks') if '_summary_board_res' in locals() and isinstance(_summary_board_res, dict) else None,
                        pre_breakout_pool=top_gainers if 'top_gainers' in locals() else None,
                        trust_profiles=_v2_profiles if '_v2_profiles' in locals() else None,
                        dc2_states=dc2_states if 'dc2_states' in locals() else None,
                        live_data_map=live_data_map,
                    )
                except Exception as e:
                    logger.error(f"Render Error (Bảng Rải Đinh): {e}", exc_info=True)

                # -------------------------------------------------------------------------
                # 🛡️ BẢNG DUYỆT LỆNH TỐI ƯU (OPTIMIZED GATEKEEPER - GIAI ĐOẠN 1)
                # -------------------------------------------------------------------------
                try:
                    from execution.order_gatekeeper import (
                        extract_clean_summary_orders,
                        optimized_execution_geometry_module,
                        render_gatekeeper_board
                    )
                    if '_summary_board_res' in locals() and _summary_board_res:
                        clean_orders = extract_clean_summary_orders(
                            summary_board_res=_summary_board_res,
                            v2_profiles=_v2_profiles if '_v2_profiles' in locals() else None,
                            grid_gom_hang_items=_grid_gom_items if '_grid_gom_items' in locals() else None
                        )
                        final_orders = optimized_execution_geometry_module(clean_orders) if clean_orders else []
                        gatekeeper_board_str = render_gatekeeper_board(final_orders)
                        if gatekeeper_board_str is not None:
                            print(gatekeeper_board_str)
                except Exception as e:
                    logger.error(f"Render Error (Bảng Duyệt Lệnh Tối Ưu): {e}", exc_info=True)


            from core.coin_filter import print_final_tables
            print_final_tables(early_list, df_summary, current_time_str,
                               macro_levels_map=(macro_levels_map if 'macro_levels_map' in locals() else None),
                               before_rebalance_hook=_summary_hook,
                               live_data_map=live_data_map)
            
            logger.info("Hoàn tất quét thị trường. Chương trình kết thúc.")
            elapsed = time.time() - start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            logger.info(f"⏱️ Thời gian chạy script: {mins} phút {secs} giây ({elapsed:.2f}s).")
            
        except Exception as e:
            logger.error(f"Lỗi hệ thống trong quá trình quét: {e}")
        finally:
            clear_klines_cache()
            gc.collect()

if __name__ == "__main__":
    main()
