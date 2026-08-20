import os
import sys
import io
import time
import json
import logging
from dotenv import load_dotenv

from strategies.macro_pullback.pullback_sniper import PullbackSniper
from strategies.macro_grid_darvas import MacroGridDarvas
from strategies.momentum_breakout import MomentumBreakout
from execution.hybrid_executor import HybridExecutor
from execution.grid_manager import GridManager
from core.coin_filter import get_filtered_symbols
from core.early_warning import EarlyWarningMatrix

# Thiết lập logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("CN4-Controller")

def load_settings():
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'settings.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Không tìm thấy file cấu hình tại {config_path}")
        return {}

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

    # Khởi tạo các module cốt lõi
    sniper = PullbackSniper()
    darvas = MacroGridDarvas()
    breakout = MomentumBreakout()
    executor = HybridExecutor(api_key=api_key, secret_key=secret_key)
    grid_manager = GridManager(api_key=api_key, secret_key=secret_key)
    
    # Chạy 1 lần (Manual Scan Mode)
    if True:
        try:
            logger.info("Đang chạy module coin_filter để quét các mã tiềm năng...")
            
            # ── Bắt đầu chụp stdout của coin_filter để in sau ──────────────
            _coin_filter_buf = io.StringIO()
            _real_stdout = sys.stdout
            sys.stdout = _coin_filter_buf
            try:
                watchlist, safety_map = get_filtered_symbols()
            finally:
                sys.stdout = _real_stdout
            _coin_filter_output = _coin_filter_buf.getvalue()
            # ────────────────────────────────────────────────────────────────
            
            if not watchlist:
                logger.warning("Không tìm thấy mã nào đạt điều kiện từ coin_filter.")
            else:
                logger.info(f"Đã lọc được {len(watchlist)} mã: {watchlist}")
            
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

            if watchlist:
                # =========================================================
                # 0. HỆ THỐNG CẢNH BÁO SỚM & RỦI RO SẬP (Early Warning Matrix)
                # =========================================================
                early_warning = EarlyWarningMatrix()
                warning_results = []
                safe_watchlist = []
                
                logger.info("Đang kích hoạt Hệ thống Cảnh báo Sớm (Early Warning Matrix)...")
                
                # Cần fetch dữ liệu 1H để kiểm tra warning
                import ccxt
                import pandas as pd
                from core.exchange_factory import get_working_exchange
                
                exchange = get_working_exchange()
                
                for symbol in watchlist:
                    try:
                        candles = exchange.fetch_ohlcv(symbol, '1h', limit=50)
                        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        warn_res = early_warning.check_warning_level(df)
                        warn_res['symbol'] = symbol
                        warning_results.append(warn_res)
                        
                        # Chỉ giữ lại các mã không bị dính Cấp 2 hoặc Cấp 3
                        if warn_res['level'] < 2:
                            safe_watchlist.append(symbol)
                    except Exception as e:
                        logger.error(f"Lỗi kiểm tra Cảnh báo sớm cho {symbol}: {e}")
                        safe_watchlist.append(symbol) # Tạm thời cho qua nếu API lỗi
                
                # In Bảng Cảnh Báo
                print("\n" + "!" * 135)
                print(f"🚨 HỆ THỐNG CẢNH BÁO SỚM & RỦI RO SẬP (EARLY WARNING MATRIX)")
                print("!" * 135)
                print(f"{'Mã (Symbol)':<15} | {'Cấp Độ':<45} | {'Tín Hiệu (Trigger)':<45}")
                print("-" * 135)
                
                # Sắp xếp theo cấp độ từ lớn đến nhỏ (Cấp 3 -> Cấp 0)
                # Vì Python sort là stable nên thứ hạng gốc (theo điểm từ coin_filter) vẫn được giữ nguyên trong cùng 1 Cấp
                warning_results.sort(key=lambda x: x.get('level', 0), reverse=True)
                
                filtered_warnings = [r for r in warning_results if r.get('level') in (1, 3)]
                if filtered_warnings:
                    for res in filtered_warnings:
                        sym = res.get('symbol', '')
                        lbl = res.get('label', '')
                        trig = res.get('trigger', '')
                        print(f"{sym:<15} | {lbl:<45} | {trig:<45}")
                else:
                    print(f"{'(Không có mã nào ở Cấp 1 hoặc Cấp 3)':<15}")
                print("!" * 135 + "\n")
                
                # Cập nhật watchlist thành safe_watchlist
                watchlist = safe_watchlist
                
                if not watchlist:
                    logger.warning("Toàn bộ danh mục bị BẤT HOẠT do rủi ro sập. Nghỉ ngơi chu kỳ này.")
                    
            if watchlist:
                # =========================================================
                # 1. Chạy Động Cơ 1 (Macro Grid Darvas)
                # =========================================================
                darvas_results = []
                for symbol in watchlist:
                    result = darvas.scan_grid_candidate(symbol, timeframe)
                    darvas_results.append(result)
                    
                # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 10
                darvas_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
                darvas_results = darvas_results[:10]
                
                print("\n" + "=" * 135)
                print(f"📦 BẢNG CHẤM ĐIỂM MACRO GRID DARVAS (ĐỘNG CƠ 1 - TÌM KIẾM SÀN BÊ TÔNG)")
                print("=" * 135)
                print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'Trạng Thái Bảng 1':<35} | {'Hành Động'}")
                print("-" * 135)
                for res in darvas_results:
                    sym = res.get('symbol', '')
                    score = res.get('total_score', 0)
                    act = res.get('action', '')
                    safe_tag = safety_map.get(sym, "⚠️ CHƯA XÉT")
                    print(f"{sym:<15} | {score:<10} | {safe_tag:<35} | {act}")
                print("=" * 135 + "\n")

                
                # Kích hoạt GridManager
                for res in darvas_results:
                    score = res.get('total_score', 0)
                    if score >= 80:
                        symbol = res.get('symbol')
                        g_setup = res.get('grid_setup', {})
                        
                        lower_price = g_setup.get('lower_price')
                        upper_price = g_setup.get('upper_price')
                        grids = g_setup.get('grid_quantity')
                        
                        if lower_price and upper_price and grids:
                            amount_per_grid = 10.0 # Test amount
                            logger.warning(f"🤖 [GRID TÍN HIỆU] Khởi tạo Bot Grid cho {symbol}")
                            if api_key:
                                grid_manager.launch_grid(
                                    symbol=symbol,
                                    upper_price=upper_price,
                                    lower_price=lower_price,
                                    grids=grids,
                                    amount_per_grid=amount_per_grid
                                )
                            else:
                                logger.info(f"[MOCK MODE] Sẽ kích hoạt Bot Grid {symbol}: Lower={lower_price}, Upper={upper_price}, Lưới={grids}")

                # =========================================================
                # 2. Chạy Động Cơ 2 (Pullback Sniper)
                # =========================================================
                sniper_results = []
                for symbol in watchlist:
                    # Động cơ 2 chỉ lấy các mã is_safe (Bảng 1 An Toàn)
                    if "✅" not in safety_map.get(symbol, "⚠️"):
                        continue
                        
                    # Gọi bộ não chấm điểm Pullback Sniper
                    result = sniper.evaluate_candidate(symbol, timeframe)
                    sniper_results.append(result)
                    
                # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 10
                sniper_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
                sniper_results = sniper_results[:10]
                
                # In bảng dữ liệu cho PullbackSniper
                # In bảng dữ liệu cho PullbackSniper
                print("\n" + "=" * 175)
                print(f"🎯 BẢNG CHẤM ĐIỂM PULLBACK SNIPER (ĐỘNG CƠ 2 - TÌM LỆNH THỰC THI CHÍNH XÁC)")
                print("=" * 175)
                print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'Trạng Thái Bảng 1':<35} | {'C1 (Hội Tụ)':<12} | {'C2 (Vol)':<10} | {'C3 (Sổ Lệnh)':<12} | {'C4 (R/R)':<10} | {'Hành Động'}")
                print("-" * 175)
                
                for res in sniper_results:
                    sym = res.get('symbol', '')
                    score = res.get('total_score', 0)
                    act = res.get('action', '')
                    dt = res.get('details', {})
                    
                    c1 = dt.get('Gate_1_Confluence', {}).get('score', 0)
                    c2 = dt.get('Gate_2_Volume', {}).get('score', 0)
                    c3 = dt.get('Gate_3_OrderBook', {}).get('score', 0)
                    c4 = dt.get('Gate_4_RR', {}).get('score', 0)
                    safe_tag = safety_map.get(sym, "⚠️ CHƯA XÉT")
                    
                    print(f"{sym:<15} | {score:<10} | {safe_tag:<35} | {c1:<12} | {c2:<10} | {c3:<12} | {c4:<10} | {act}")

                    
                    # In thông số Setup nếu là LOẠI A hoặc LOẠI B
                    if score >= 70:
                        setup = res.get('trade_setup', {})
                        entry = setup.get('entry')
                        sl = setup.get('stop_loss')
                        tp = setup.get('take_profit')
                        if entry and sl and tp:
                            sl_pct = (entry - sl) / entry * 100
                            tp_pct = (tp - entry) / entry * 100
                            rr_ratio = tp_pct / sl_pct if sl_pct > 0 else 0
                            print(f"   ↳ ⚙️ SETUP: Limit Buy = {entry:.6f} | Chốt Lời (TP) = {tp:.6f} (+{tp_pct:.1f}%) | Cắt Lỗ (SL) = {sl:.6f} (-{sl_pct:.1f}%) | R/R = 1:{rr_ratio:.1f}")
                            print("-" * 175)
                
                print("=" * 175 + "\n")
                
                # Kích hoạt thực thi cho các mã đạt điểm
                for res in sniper_results:
                    score = res.get('total_score', 0)
                    if score >= 85:
                        symbol = res.get('symbol')
                        setup = res.get('trade_setup', {})
                        entry = setup.get('entry')
                        sl = setup.get('stop_loss')
                        tp = setup.get('take_profit')
                        
                        if entry is not None and sl is not None and tp is not None:
                            test_amount = 0.01
                            logger.warning(f"🚀 [TÍN HIỆU] Kích hoạt Executor cho {symbol} tại giá {entry}")
                            if api_key:
                                executor.execute_trade(
                                    symbol=symbol,
                                    entry=entry,
                                    sl=sl,
                                    tp=tp,
                                    amount=test_amount
                                )
                            else:
                                logger.info(f"[MOCK MODE] Sẽ đặt lệnh Buy Limit {symbol} tại {entry}, SL: {sl}, TP: {tp}")

                # =========================================================
                # 3. Chạy Động Cơ 3 (Momentum Breakout)
                # =========================================================
                breakout_results = []
                for symbol in watchlist:
                    result = breakout.evaluate_breakout(symbol, timeframe)
                    breakout_results.append(result)
                    
                # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 10
                breakout_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
                breakout_results = breakout_results[:10]
                
                # In bảng dữ liệu cho MomentumBreakout
                print("\n" + "=" * 135)
                print(f"🚀 BẢNG CHẤM ĐIỂM MOMENTUM BREAKOUT (ĐỘNG CƠ 3 - SĂN BỨT PHÁ ĐỘNG LƯỢNG)")
                print("=" * 135)
                print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'C1 (PriceAct)':<25} | {'C2 (Volume)':<25} | {'C3 (OrderBook)':<25} | {'C4 (R/R)':<25}")
                print("-" * 135)
                
                for res in breakout_results:
                    sym = res.get('symbol', '')
                    score = res.get('total_score', 0)
                    act = res.get('action', '')
                    dt = res.get('details', {})
                    
                    c1 = dt.get('Gate_1_PriceAction', '')[:25]
                    c2 = dt.get('Gate_2_Volume', '')[:25]
                    c3 = dt.get('Gate_3_OrderBook', '')[:25]
                    c4 = dt.get('Gate_4_RR', '')[:25]
                    
                    print(f"{sym:<15} | {score:<10} | {c1:<25} | {c2:<25} | {c3:<25} | {c4:<25}")
                    print(f"   ↳ Hành Động: {act}")
                    print("-" * 135)
                
                print("=" * 135 + "\n")
                
                # ── 6-8. In các bảng từ coin_filter sau cùng ────────────────
                if _coin_filter_output:
                    print("\n" + "=" * 80)
                    print("📊 KẾT QUẢ PHÂN TÍCH THỊ TRƯỜNG (COIN FILTER)")
                    print("=" * 80)
                    # Lọc bỏ các dòng thông tin ngắn (đã in ở đầu) để tránh trùng
                    _skip_prefixes = ("🔍 Đang gọi", "🎯 TỔNG CỘNG", "🌱 Đang quét", "🚀 Đang quét")
                    _filtered_lines = []
                    for _line in _coin_filter_output.splitlines():
                        if any(_line.strip().startswith(p) for p in _skip_prefixes):
                            continue
                        _filtered_lines.append(_line)
                    print("\n".join(_filtered_lines))
                
                # Kích hoạt thực thi cho các mã đạt điểm
                for res in breakout_results:
                    score = res.get('total_score', 0)
                    if score >= 85:
                        symbol = res.get('symbol')
                        setup = res.get('trade_setup', {})
                        entry = setup.get('entry')
                        sl = setup.get('stop_loss')
                        tp = setup.get('take_profit')
                        
                        if entry is not None and sl is not None and tp is not None:
                            test_amount = 0.01
                            logger.warning(f"🚀 [TÍN HIỆU ĐỘNG CƠ 3] Kích hoạt Executor cho {symbol} tại giá {entry}")
                            if api_key:
                                executor.execute_trade(
                                    symbol=symbol,
                                    entry=entry,
                                    sl=sl,
                                    tp=tp,
                                    amount=test_amount
                                )
                            else:
                                logger.info(f"[MOCK MODE] Sẽ đặt lệnh Buy Limit {symbol} tại {entry}, SL: {sl}, TP: {tp}")

            
                # =========================================================
                # 1. Chạy Động Cơ 1 (Macro Grid Darvas)
                # =========================================================
                darvas_results = []
                for symbol in watchlist:
                    result = darvas.scan_grid_candidate(symbol, timeframe)
                    darvas_results.append(result)
                    
                # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 10 mã xuất sắc nhất
                darvas_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
                darvas_results = darvas_results[:10]
                
                print("\n" + "=" * 135)
                print(f"📦 BẢNG CHẤM ĐIỂM MACRO GRID DARVAS (ĐỘNG CƠ 1 - TÌM KIẾM SÀN BÊ TÔNG)")
                print("=" * 135)
                print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'Trạng Thái Bảng 1':<35} | {'Hành Động'}")
                print("-" * 135)
                for res in darvas_results:
                    sym = res.get('symbol', '')
                    score = res.get('total_score', 0)
                    act = res.get('action', '')
                    safe_tag = safety_map.get(sym, "⚠️ CHƯA XÉT")
                    print(f"{sym:<15} | {score:<10} | {safe_tag:<35} | {act}")
                print("=" * 135 + "\n")

                
                # Kích hoạt GridManager
                for res in darvas_results:
                    score = res.get('total_score', 0)
                    if score >= 80:
                        symbol = res.get('symbol')
                        g_setup = res.get('grid_setup', {})
                        
                        lower_price = g_setup.get('lower_price')
                        upper_price = g_setup.get('upper_price')
                        grids = g_setup.get('grid_quantity')
                        
                        if lower_price and upper_price and grids:
                            amount_per_grid = 10.0 # Test amount
                            logger.warning(f"🤖 [GRID TÍN HIỆU] Khởi tạo Bot Grid cho {symbol}")
                            if api_key:
                                grid_manager.launch_grid(
                                    symbol=symbol,
                                    upper_price=upper_price,
                                    lower_price=lower_price,
                                    grids=grids,
                                    amount_per_grid=amount_per_grid
                                )
                            else:
                                logger.info(f"[MOCK MODE] Sẽ kích hoạt Bot Grid {symbol}: Lower={lower_price}, Upper={upper_price}, Lưới={grids}")

                # =========================================================
            logger.info("Hoàn tất quét thị trường. Chương trình kết thúc.")
            
        except Exception as e:
            logger.error(f"Lỗi hệ thống trong quá trình quét: {e}")

if __name__ == "__main__":
    main()
