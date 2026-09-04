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
from execution.hybrid_executor import HybridExecutor
from execution.grid_manager import GridManager
from core.coin_filter import get_filtered_symbols, live_data_map, get_avg_vola_24h
from core.early_warning import EarlyWarningMatrix

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

    # Khởi tạo các module cốt lõi
    sniper         = PullbackSniper()
    entry_calc     = EntryCalculatorService()   # DC2 Lõi Tính Toán (chạy sau PullbackSniper)
    darvas         = MacroGridDarvas()
    breakout       = MomentumBreakout()
    # DC4 (HotTrendPullback) dùng class methods, không cần khởi tạo instance ở đây
    executor       = HybridExecutor(api_key=api_key, secret_key=secret_key)
    grid_manager   = GridManager(api_key=api_key, secret_key=secret_key)
    
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
                
                def _check_ew(sym):
                    try:
                        from core.coin_filter import fetch_binance_api
                        sym_api = sym.replace('/', '')
                        
                        def fetch_df(interval, limit=50):
                            data = fetch_binance_api(f"/api/v3/klines?symbol={sym_api}&interval={interval}&limit={limit}")
                            if not data:
                                raise Exception(f"Khong the lay du lieu {interval} tu Binance API")
                            
                            candles = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in data]
                            return pd.DataFrame(candles, columns=['timestamp','open','high','low','close','volume'])
                        
                        df_1h = fetch_df('1h')
                        df_4h = fetch_df('4h')
                        df_1d = fetch_df('1d')
                        
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
                print("\n" + "!" * 125)
                print(f"🚨 HỆ THỐNG CẢNH BÁO SỚM & RỦI RO SẬP (EARLY WARNING MATRIX)")
                print("!" * 125)
                # In bảng theo chuẩn Markdown
                print(f"| {'Mức Độ (Level)':<50} | {'Tín Hiệu (Trigger)':<40} | {'Danh Sách Mã (Symbols)'}")
                print(f"|{'-'*52}|{'-'*42}|{'-'*60}")
                
                filtered_warnings = [r for r in warning_results if r.get('level') in (1, 2, 3)]
                if filtered_warnings:
                    from collections import defaultdict
                    grouped = defaultdict(list)
                    for res in filtered_warnings:
                        # Tách riêng label và trigger để đưa vào cột
                        key = (res.get('label', ''), res.get('trigger', ''))
                        grouped[key].append(res.get('symbol', '').replace('/USDT', ''))
                    
                    total_scanned = len(watchlist)
                    for (lbl, trig), symbols in grouped.items():
                        # Nối danh sách các mã
                        sym_str = ", ".join(symbols)
                        count = len(symbols)
                        lbl_with_count = f"{lbl} ({count}/{total_scanned})"
                        # In thành 1 hàng markdown
                        print(f"| {lbl_with_count:<50} | {trig:<40} | {sym_str}")
                else:
                    print(f"| {'(Không có mã nào)':<50} | {'-':<40} | {'-'}")
                print("!" * 125 + "\n")
                
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
                darvas_results = []
                for symbol in watchlist:
                    result = darvas.scan_grid_candidate(symbol, timeframe)
                    darvas_results.append(result)
                    
                # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 5
                darvas_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
                darvas_results = darvas_results[:5]
                
                print("\n" + "=" * 115)
                print(f"📦 ĐỘNG CƠ 1: DARVAS GRID (Dành cho Chiến lược Phòng thủ Móng nhà)")
                print("=" * 115)
                print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'Trạng Thái Bảng 1':<35} | {'Hành Động'}")
                print("| --- | --- | --- | --- |")
                for res in darvas_results:
                    sym = res.get('symbol', '')
                    score = res.get('total_score', 0)
                    act = res.get('action', '')
                    safe_tag = safety_map.get(sym, "⚠️ CHƯA XÉT")
                    
                    print(f"{sym:<15} | {score:<10} | {safe_tag:<35} | {act}")
                    if score >= 60:
                        g_setup = res.get('grid_setup', {})
                        if g_setup:
                            is_dual = g_setup.get('is_dual_grid', False)
                            sl = g_setup.get('stop_loss', 0)
                            tp = g_setup.get('take_profit', 0)
                            if is_dual:
                                g1_lower = g_setup.get('g1_lower', 0)
                                g1_upper = g_setup.get('g1_upper', 0)
                                g1_grids = g_setup.get('g1_grids', 0)
                                g2_lower = g_setup.get('g2_lower', 0)
                                g2_upper = g_setup.get('g2_upper', 0)
                                g2_grids = g_setup.get('g2_grids', 0)
                                print(f"  ↳ ⚙️ DUAL GRID: [{sym}] SL = {sl} | TP = {tp}")
                                print(f"     ├── G1 (Bắt đáy): {g1_lower} - {g1_upper} ({g1_grids} Lưới) [70% Vốn]")
                                print(f"     └── G2 (Đột phá): {g2_lower} - {g2_upper} ({g2_grids} Lưới) [30% Vốn]")
                            else:
                                lower = g_setup.get('lower_price', 0)
                                upper = g_setup.get('upper_price', 0)
                                qty = g_setup.get('grid_quantity', 0)
                                print(f"  ↳ ⚙️ SETUP GRID: [{sym}] Lower = {lower} | Uper = {upper} | Grids = {qty}| SL = {sl} | TP = {tp}")
                            
                print("=" * 115 + "\n")

                
                # Kích hoạt GridManager
                for res in darvas_results:
                    score = res.get('total_score', 0)
                    if score >= 80:
                        symbol = res.get('symbol')
                        g_setup = res.get('grid_setup', {})
                        
                        is_dual = g_setup.get('is_dual_grid', False)
                        
                        if is_dual:
                            g1_lower = g_setup.get('g1_lower')
                            g1_upper = g_setup.get('g1_upper')
                            g1_grids = g_setup.get('g1_grids')
                            g2_lower = g_setup.get('g2_lower')
                            g2_upper = g_setup.get('g2_upper')
                            g2_grids = g_setup.get('g2_grids')
                            
                            if g1_lower and g1_upper and g1_grids and g2_lower and g2_upper and g2_grids:
                                logger.warning(f"🤖 [DUAL GRID TÍN HIỆU] Khởi tạo hệ thống Lưới Kép cho {symbol}")
                                if api_key:
                                    # Lưới 1 (70% vốn)
                                    grid_manager.launch_grid(symbol=symbol, upper_price=g1_upper, lower_price=g1_lower, grids=g1_grids, amount_per_grid=14.0) # Ví dụ chia vốn
                                    # Lưới 2 (30% vốn)
                                    grid_manager.launch_grid(symbol=symbol, upper_price=g2_upper, lower_price=g2_lower, grids=g2_grids, amount_per_grid=6.0)
                                else:
                                    logger.info(f"[MOCK MODE] Sẽ kích hoạt Lưới Kép {symbol} -> G1(L:{g1_lower}, U:{g1_upper}, {g1_grids}L) | G2(L:{g2_lower}, U:{g2_upper}, {g2_grids}L)")
                        else:
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
                logger.info("[DC2] Kiem tra Macro Trend 1D truoc khi cham diem Pullback...")

                # Tính avg_vola_24h MỘT LẦN từ live_data_map đã có sẵn
                avg_vola_24h = get_avg_vola_24h()

                for symbol in watchlist:
                    macro_gate = sniper.check_macro_trend_1d(symbol)
                    if not macro_gate.get("ok", True):
                        logger.debug(f"[DC2] {symbol} bi Macro Gate loai: {macro_gate['reason']}")

                    result = sniper.evaluate_candidate(symbol, timeframe, macro_gate=macro_gate)
                    sniper_results.append(result)

                # Sắp xếp theo điểm tổng giảm dần và chỉ lấy Top 5
                sniper_results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
                sniper_results = sniper_results[:5]
                
                # In bảng dữ liệu cho PullbackSniper
                print("\n" + "=" * 175)
                print(f"🎯 BẢNG CHẤM ĐIỂM PULLBACK SNIPER (ĐỘNG CƠ 2 - TÌM LỆNH THỰC THI CHÍNH XÁC)")
                print("=" * 175)
                print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'Trạng Thái Bảng 1':<35} | {'C1 (Hội Tụ)':<12} | {'C2 (Vol)':<10} | {'C3 (Sổ Lệnh)':<12} | {'C4 (R/R)':<10} | {'Hành Động'}")
                print("| --- | --- | --- | --- | --- | --- | --- | --- |")

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

                    # Chạy EntryCalculatorService (Lõi Thực Thi) nếu đạt điểm >= 70
                    if score >= 70:
                        coin_vola = live_data_map.get(
                            sym.replace('/', ''), {}
                        ).get('daily_vola', avg_vola_24h)

                        # ── Bộ Giáp Sniper: Chạy scan_sniper_safety() để lấy EW + Pullback Score ──
                        try:
                            from core.coin_filter import fetch_binance_api
                            sym_api = sym.replace('/', '')
                            def _fetch_df_dc2(interval, limit):
                                data = fetch_binance_api(
                                    f"/api/v3/klines?symbol={sym_api}&interval={interval}&limit={limit}"
                                )
                                if not data:
                                    return pd.DataFrame()
                                rows = [[int(x[0]), float(x[1]), float(x[2]),
                                         float(x[3]), float(x[4]), float(x[5])] for x in data]
                                return pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])

                            _df_15m = _fetch_df_dc2('15m', 60)
                            _df_4h  = _fetch_df_dc2('4h',  120)
                            _df_1h  = _fetch_df_dc2('1h',  50)
                            _price  = float(_df_15m['close'].iloc[-1]) if not _df_15m.empty else 0.0

                            ew_res = early_warning.scan_sniper_safety(
                                df_15m        = _df_15m,
                                df_4h         = _df_4h,
                                df_1h         = _df_1h,
                                current_price = _price,
                                coin_vola_24h = coin_vola,
                                avg_vola_24h  = avg_vola_24h,
                                symbol        = sym_api,
                            )
                            ew_lv  = ew_res.get('ew_level', 3)
                            ew_lbl = ew_res.get('ew_label', '')
                            pb_sc  = ew_res.get('pullback_score', 0)
                            pb_dt  = ew_res.get('pullback_detail', {})
                            force_con = ew_res.get('force_conservative', False)

                            # Tóm tắt C1-C4 cho dòng hiển thị
                            c1_pb = pb_dt.get('C1_Wick_Purity',    {}).get('score', '-')
                            c2_pb = pb_dt.get('C2_Micro_Dryup',    {}).get('score', '-')
                            c3_pb = pb_dt.get('C3_Macro_Momentum', {}).get('score', '-')
                            c4_pb = pb_dt.get('C4_Taker_Buy',      {}).get('score', '-')
                            force_tag = " [⚠️ FORCE-CON]" if force_con else ""

                            print(f"   ↳ 🛡️ EW Sniper [{sym}]: {ew_lbl}{force_tag}")
                            print(f"      PB Score={pb_sc}/100 "
                                  f"| C1-Wick={c1_pb}đ C2-DryUp={c2_pb}đ "
                                  f"C3-Momentum={c3_pb}đ C4-TakerBuy={c4_pb}đ")

                            # Nếu EW Cấp 1 bắn cờ → hiện trigger và bỏ qua EntryCalc
                            if ew_lv == 1:
                                triggers = " | ".join(ew_res.get('ew_triggers', []))
                                print(f"   ↳ ⛔ [EW CẤP 1 REJECT] {triggers}")
                                print("-" * 175)
                                continue

                        except Exception as _ew_err:
                            logger.debug(f"[DC2-EW] {sym}: Không thể chạy scan_sniper_safety: {_ew_err}")
                            ew_lv = 3

                        try:
                            oco_payload = entry_calc.calculate({
                                "symbol":             sym.replace('/', ''),
                                "timeframe_entry":    "15m",
                                "timeframe_macro":    "4h",
                                "engine_type":        "SNIPER_SPOT",
                                "capital_allocation": 0.30,
                                "avg_vola_24h":       avg_vola_24h,
                                "coin_vola_24h":      coin_vola,
                            })

                            v   = oco_payload['validation']
                            p1  = oco_payload['payload'][0]['parameters']
                            p2  = oco_payload['payload'][1]['parameters']
                            rr1 = v['rr_payload1']
                            rr2 = v['rr_payload2']
                            mode = v['risk_mode']

                            entry   = p1['price']
                            sl1_pct = (entry - p1['oco_sl']) / entry * 100
                            tp1_pct = (p1['oco_tp'] - entry) / entry * 100
                            sl2_pct = (entry - p2['oco_sl']) / entry * 100
                            tp2_pct = (p2['oco_tp'] - entry) / entry * 100
                            cb_tag  = 'CB:ON' if v.get('cb_triggered') else 'CB:OFF'

                            print(f"   ↳ OCO-1 [{sym}] Buy={fmt_price(entry)} "
                                  f"| SL={fmt_price(p1['oco_sl'])}(-{sl1_pct:.1f}%) "
                                  f"| TP={fmt_price(p1['oco_tp'])}(+{tp1_pct:.1f}%) "
                                  f"| R/R=1:{rr1:.1f} "
                                  f"| EMA25={fmt_price(v.get('ema25',0))} "
                                  f"| {cb_tag} Pierced={v.get('pierced_count',0)}/20")
                            print(f"   ↳ OCO-2 [{sym}] Buy={fmt_price(entry)} "
                                  f"| SL={fmt_price(p2['oco_sl'])}(-{sl2_pct:.1f}%) "
                                  f"| TP={fmt_price(p2['oco_tp'])}(+{tp2_pct:.1f}%) "
                                  f"| R/R=1:{rr2:.1f} "
                                  f"| [{mode}] Trailing=ON->BE khi OCO-1 TP")

                        except EntryCalculatorServiceError as e:
                            err_str = str(e)
                            # Phân biệt: EW Cấp 1 bên trong EntryCalc hay R/R Gate
                            if 'EW CẤP 1' in err_str or 'FATAL RISK' in err_str:
                                print(f"   ↳ ⛔ [REJECT - EW CẤP 1] {err_str}")
                            elif 'R/R Gate' in err_str or 'R/R' in err_str:
                                print(f"   ↳ 📊 [REJECT - R/R Gate] {err_str}")
                            else:
                                print(f"   ↳ 🔴 [EntryCalc REJECTED] {err_str}")
                        except Exception as e:
                            print(f"   ↳ [EntryCalc ERROR] {e}")

                    print("-" * 175)

                print("=" * 175 + "\n")


                # Kích hoạt thực thi cho các mã đạt điểm
                for res in sniper_results:
                    score = res.get('total_score', 0)
                    if score >= 85:
                        symbol = res.get('symbol')
                        coin_vola = live_data_map.get(
                            symbol.replace('/', ''), {}
                        ).get('daily_vola', avg_vola_24h)

                        try:
                            oco_payload = entry_calc.calculate({
                                "symbol":             symbol.replace('/', ''),
                                "timeframe_entry":    "15m",
                                "timeframe_macro":    "4h",
                                "engine_type":        "SNIPER_SPOT",
                                "capital_allocation": 0.30,
                                "avg_vola_24h":       avg_vola_24h,
                                "coin_vola_24h":      coin_vola,
                            })
                            p1 = oco_payload['payload'][0]['parameters']
                            p2 = oco_payload['payload'][1]['parameters']

                            logger.warning(f"[TIN HIEU DC2] {symbol} APPROVED | "
                                           f"Entry={fmt_price(p1['price'])} "
                                           f"| OCO-1 SL={fmt_price(p1['oco_sl'])} TP={fmt_price(p1['oco_tp'])} "
                                           f"| OCO-2 SL={fmt_price(p2['oco_sl'])} TP={fmt_price(p2['oco_tp'])}")
                            if not api_key:
                                logger.info(f"[MOCK MODE] {symbol}: OCO-1 Buy Limit @ {fmt_price(p1['price'])} "
                                            f"SL={fmt_price(p1['oco_sl'])} TP={fmt_price(p1['oco_tp'])} | "
                                            f"OCO-2 Buy Limit @ {fmt_price(p2['price'])} "
                                            f"SL={fmt_price(p2['oco_sl'])} TP={fmt_price(p2['oco_tp'])}")
                        except (EntryCalculatorServiceError, Exception) as e:
                            logger.warning(f"[DC2] {symbol} score={score} nhung EntryCalc tu choi: {e}")

                # =========================================================
                # 3. Chạy Động Cơ 3 (Momentum Breakout)
                # =========================================================
                # [DC3-3] Gọi BTC 1H Gate MỘT LẦN, truyền kết quả vào từng mã — tiết kiệm API
                print("\n🔍 [DC3] Kiểm tra BTC 1H Gate trước khi quét Breakout...")
                btc_gate = breakout.check_btc_trend_1h()
                btc_gate_label = btc_gate.get('reason', '')
                if not btc_gate.get('ok', True):
                    print(f"🚱 [DC3] BTC Gate đóng: {btc_gate_label}")
                else:
                    print(f"✅ [DC3] BTC Gate mở: {btc_gate_label}")

                breakout_results = []
                for symbol in watchlist:
                    result = breakout.evaluate_breakout(symbol, timeframe, btc_gate=btc_gate)
                    breakout_results.append(result)
                    
                # Sắp xếp theo sort_score (total_score + rr_ratio) giảm dần, chỉ lấy Top 5
                breakout_results.sort(key=lambda x: x.get('sort_score', x.get('total_score', 0)), reverse=True)
                breakout_results = breakout_results[:5]
                
                # In bảng dữ liệu cho MomentumBreakout
                print("\n" + "=" * 175)
                print(f"🚀 BẢNG CHẤM ĐIỂM MOMENTUM BREAKOUT (ĐỘNG CƠ 3 - SĂN BỨT PHÁ ĐỘNG LƯỢNG)")
                # [DC3-3] Hiển thị trạng thái BTC Gate ngay trong header bảng
                print(f"   📡 BTC 1H Gate: {btc_gate_label}")
                print("=" * 175)
                print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'C1 (PriceAct)':<25} | {'C2 (Volume)':<25} | {'C3 (OrderBook)':<25} | {'C4 (R/R)':<25} | {'Bonus (Taker)':<25} | {'Hành Động'}")
                print("| --- | --- | --- | --- | --- | --- | --- | --- |")

                for res in breakout_results:
                    sym = res.get('symbol', '')
                    score = res.get('total_score', 0)
                    act = res.get('action', '')
                    dt = res.get('details', {})

                    c1 = dt.get('Gate_1_PriceAction', '')[:25]
                    c2 = dt.get('Gate_2_Volume', '')[:25]
                    c3 = dt.get('Gate_3_OrderBook', '')[:25]
                    c4 = dt.get('Gate_4_RR', '')[:25]
                    bonus = dt.get('Bonus_TakerBuy', '')[:25]

                    print(f"{sym:<15} | {score:<10} | {c1:<25} | {c2:<25} | {c3:<25} | {c4:<25} | {bonus:<25} | {act}")

                    # In thông số Setup nếu Động cơ 3 có cung cấp
                    setup = res.get('trade_setup', {})
                    if setup:
                        entry = setup.get('entry')
                        sl = setup.get('stop_loss')
                        tp1 = setup.get('tp1', setup.get('take_profit'))
                        tp2 = setup.get('tp2')
                        tp_trail = setup.get('tp_trail')
                        if entry and sl and tp1:
                            sl_pct = (entry - sl) / entry * 100
                            tp1_pct = (tp1 - entry) / entry * 100
                            rr_ratio = res.get('rr_ratio', (tp1_pct / sl_pct if sl_pct > 0 else 0))
                            # [DC3-5] Scale-out Mock Log
                            print(f"   ↳ ⚙️ SETUP [{sym}] Entry = {fmt_price(entry)} | SL = {fmt_price(sl)} (-{sl_pct:.1f}%) | R/R = 1:{rr_ratio:.1f}")
                            if tp2 and tp_trail:
                                tp2_pct = (tp2 - entry) / entry * 100
                                trail_pct = (tp_trail - entry) / entry * 100
                                print(f"       📄 [MOCK SCALE-OUT] TP1={fmt_price(tp1)} (+{tp1_pct:.1f}%) | TP2={fmt_price(tp2)} (+{tp2_pct:.1f}%) | Trail={fmt_price(tp_trail)} (+{trail_pct:.1f}%)")
                            else:
                                print(f"   ↳ ⚙️ SETUP: [{sym}] Buy Market = {fmt_price(entry)} | Chốt Lời (TP1) = {fmt_price(tp1)} (+{tp1_pct:.1f}%) | Cắt Lỗ (SL) = {fmt_price(sl)} (-{sl_pct:.1f}%) | R/R = 1:{rr_ratio:.1f}")
                    print("-" * 175)

                print("=" * 175 + "\n")
                
                # =========================================================
                # 4. Chạy Động Cơ 4 (Hot Trend Pullback) — Độc lập với watchlist
                # Tự quét Top 60 mã tăng mạnh nhất từ live_data_map
                # Không phụ thuộc vào màng lọc tích lũy của coin_filter
                # =========================================================
                from strategies.hot_trend_pullback import HotTrendPullback as _HTB

                # Đếm số mã Hot Trend đủ điều kiện trước khi quét
                _htb_symbols = _HTB.get_hot_trend_symbols(live_data_map)
                _htb_count   = len(_htb_symbols)
                _htb_change_threshold = 5.0  # HTB_MIN_CHANGE_24H

                print("\n" + "=" * 175)
                print(f"🔥 BẢNG CHẤM ĐIỂM HOT TREND PULLBACK (ĐỘNG CƠ 4 - SĂN ĐIỂM VÀO LỆNH PULLBACK)")
                print(f"   📡 Nguồn: {_htb_count} mã Hot Trend (change_24h ≥ {_htb_change_threshold}%, vol ≥ 3M USDT) — Độc lập với watchlist tích lũy")
                print("=" * 175)

                hot_trend_results = _HTB.run_scan(live_data_map)

                if not hot_trend_results:
                    print("⚠️  KHÔNG TÌM THẤY MÃ NÀO ĐỦ ĐIỀU KIỆN HOT TREND PULLBACK HIỆN TẠI.")
                    print("    → Thị trường chưa có nhịp pullback rõ ràng, hoặc các mã tăng đang vẫn ở đỉnh.")
                else:
                    print("-" * 175)

                    from core.grid_calculator import GridCalculator as _GC
                    _gc = _GC()

                    for res in hot_trend_results[:5]:
                        sym      = res.get('symbol', '')
                        score    = res.get('Điểm', 0)
                        score_c5 = res.get('Điểm C1-C5', score)
                        c0_sc    = res.get('C0 Score', 0)
                        rsi      = res.get('RSI 1H', 0)
                        pull     = res.get('Pullback%', 0)
                        act      = res.get('Hành Động', '')

                        c1 = res.get('C1 Trend',    '')
                        c2 = res.get('C2 Pullback', '')
                        c3 = res.get('C3 Volume',   '')
                        c4 = res.get('C4 Bệ Đỡ',   '')
                        c5 = res.get('C5 Taker',    '')

                        # Cột điểm: hiển thị "tổng (C1-C5 ± C0)"
                        score_fmt = f"{score}({score_c5}{c0_sc:+d})"

                        print(f"[{sym:<6}] Điểm: {score_fmt:<12} | RSI1H: {rsi:<5.1f} | Pull%: {pull:<6.1f} | 🎯 Hành Động: {act}")
                        print(f"   ↳ 📈 C1 Trend  : {c1}")
                        print(f"   ↳ 📉 C2 Pullbck: {c2}")
                        print(f"   ↳ 📊 C3 Volume : {c3}")
                        print(f"   ↳ 🧱 C4 Bệ Đỡ  : {c4}")
                        print(f"   ↳ 💸 C5 Taker  : {c5}")

                        # In dòng C0 Macro Cycle Detector
                        c0_label  = res.get('C0 Chu Kỳ', '')
                        c0_detail = res.get('C0 Detail', {})
                        if c0_label:
                            d180 = c0_detail.get('C0.1 Drawdown180D', '')
                            d60  = c0_detail.get('C0.2 NgâmMóng60D',  '')
                            d_ns = c0_detail.get('C0.3 NoSupply VSA', '')
                            d_ma = c0_detail.get('C0.4 MA99 Slope',   '')
                            print(f"   ↳ 🌀 C0 Macro  : {c0_label} | {d180} | {d60} | {d_ns} | {d_ma}")

                        setup = res.get('trade_setup', {})
                        if setup and setup.get('entry'):
                            entry  = setup['entry']
                            sl     = setup['stop_loss']
                            tp1    = setup['take_profit']
                            sl_pct = setup.get('sl_pct',  (entry - sl)  / entry * 100)
                            tp_pct = setup.get('tp1_pct', (tp1 - entry) / entry * 100)
                            rr     = setup.get('rr_ratio', 0)
                            ema20  = setup.get('ema20', 0)
                            ema50  = setup.get('ema50', 0)

                            print(f"   ↳ ⚙️ SETUP [{sym}]"
                                  f" | Buy Limit = {fmt_price(entry)}"
                                  f" | TP = {fmt_price(tp1)} (+{tp_pct:.1f}%)"
                                  f" | SL = {fmt_price(sl)} (-{sl_pct:.1f}%)"
                                  f" | R/R = 1:{rr:.1f}"
                                  f" | EMA20 = {fmt_price(ema20)}"
                                  f" | EMA50 = {fmt_price(ema50)}")

                            # Grid 1H bổ sung nếu setup hợp lệ
                            try:
                                setup1h = _gc.calculate_grid_1h(
                                    current_price=entry, entry=entry,
                                    stop_loss=sl, tp1=tp1
                                )
                                if setup1h.get('status') == 'SUCCESS':
                                    hard_sl  = setup1h.get('hard_stop_loss')
                                    hard_tp  = setup1h.get('hard_take_profit')
                                    grids    = setup1h.get('num_grids')
                                    buf_pct  = setup1h.get('tp_buffer_pct', 0.015) * 100
                                    trig_1h  = entry * 1.003  # +0.3% đón lõng pullback
                                    print(f"   ↳ ⚙️ GRID 1H [{sym}]"
                                          f" | Trig: {fmt_price(trig_1h)}"
                                          f" | Lưới: {fmt_price(hard_sl)} - {fmt_price(entry)} ({grids}L)"
                                          f" | SL: {fmt_price(hard_sl)} (-{buf_pct:.1f}%)"
                                          f" | TP: {fmt_price(hard_tp)} (+{buf_pct:.1f}%)")
                            except Exception:
                                pass

                        print("-" * 175)

                    if len(hot_trend_results) > 5:
                        tracking_list = []
                        for res in hot_trend_results[5:]:
                            sym = res.get('symbol', '')
                            score = res.get('Điểm', 0)
                            act = res.get('Hành Động', '')
                            
                            if "VÀO LỆNH" in act: act_short = "🚀"
                            elif "CHỜ XÁC NHẬN" in act: act_short = "⏳"
                            elif "TỪ CHỐI" in act: act_short = "🔥"
                            else: act_short = "🔴"
                            
                            tracking_list.append(f"{sym} ({score}đ {act_short})")
                            
                        print("👀 THEO DÕI THÊM: " + ", ".join(tracking_list))
                        print("-" * 175)

                print("=" * 175 + "\n")
                
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
                
                # Kích hoạt thực thi cho các mã đạt điểm (chỉ khi có API key thật)
                if api_key:
                    for res in breakout_results:
                        score = res.get('total_score', 0)
                        if score >= 85:
                            symbol = res.get('symbol')
                            setup = res.get('trade_setup', {})
                            entry = setup.get('entry')
                            sl = setup.get('stop_loss')
                            tp = setup.get('take_profit')
                            
                            if entry is not None and sl is not None:
                                o_type = setup.get('order_type', 'market').upper()
                                logger.warning(f"🚀 [EXECUTOR] Bắn lệnh BUY {o_type} {symbol} @ {entry}")
                                executor.execute_trade(
                                    symbol=symbol,
                                    amount=0.01,
                                    setup=setup
                                )

                # =========================================================
            logger.info("Hoàn tất quét thị trường. Chương trình kết thúc.")
            
        except Exception as e:
            logger.error(f"Lỗi hệ thống trong quá trình quét: {e}")

if __name__ == "__main__":
    main()
