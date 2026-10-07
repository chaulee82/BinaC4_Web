import pandas as pd
from typing import List, Dict, Any
from models.market_state import SymbolState, ScoreContext, GridContext, EarlyWarningContext, EntrySetupContext, MacroState
from core.grid_tp2_builder import build_macro_grid_payload
from views.summary_board import (
    SignalRecord, clean_line, classify_action, extract_money_flow, extract_bounces,
)

class ConsoleRenderer:
    """
    Lớp chuyên trách xử lý hiển thị ra màn hình Console.
    Đảm bảo Fail-Safe bằng cách bọc toàn bộ trong try-except.
    """

    def __init__(self):
        # global_signals_pool — thu thập trong lúc in bảng, dùng cho BẢNG TỔNG KẾT TỐI ƯU
        self.signals_pool: list = []

    def _collect(self, state: SymbolState, engine: str, score_ctx: ScoreContext,
                 setup=None, setup_line: str = "", grid_tp2_line: str = "", wide_grid_line: str = ""):
        """Đưa 1 tín hiệu (mã × Động cơ) vào signals_pool. Fail-safe: lỗi không làm vỡ bảng."""
        try:
            from core.grid_tp2_builder import _clean_symbol
            d3 = self._get_d3(state) or {}
            gates = (score_ctx.c1_score, score_ctx.c2_score, score_ctx.c3_score,
                     score_ctx.c4_score, score_ctx.bonus_score)
            sl_pct = None
            rr = 0.0
            if setup is not None and setup.entry_price:
                sl_pct = -(setup.entry_price - setup.sl_price) / setup.entry_price * 100
                rr = float(setup.rr_ratio or 0.0)
            expl = (d3.get("explosive") or {}).get("ratio", 0.0) or 0.0
            bounces, b_range = extract_bounces(score_ctx.c1_score) if engine == "DC5" else (0.0, 0.0)
            try:
                score = float(score_ctx.total_score or 0)
            except (TypeError, ValueError):
                score = 0.0
            self.signals_pool.append(SignalRecord(
                symbol=_clean_symbol(state.symbol),
                engine_source=engine,
                action_status=classify_action(engine, score_ctx.action_label, gates),
                action_label=score_ctx.action_label or "",
                target_score=score,
                money_flow=max(extract_money_flow(score_ctx.action_label, state.money_flow_tag), float(expl)),
                rr_ratio=rr,
                sl_percent=sl_pct,
                macro_3d_signal=d3.get("strategy_tag_3d", "") or "",
                setup_line=clean_line(setup_line),
                grid_tp2_line=clean_line(grid_tp2_line),
                wide_grid_line=clean_line(wide_grid_line),
                bounces=bounces,
                bounce_range=b_range,
            ))
        except Exception:
            pass
    
    @staticmethod
    def fmt_price(price: float) -> str:
        """Định dạng giá thông minh, hiển thị chính xác để khớp với R/R"""
        if price is None:
            return "—"
        
        # Sử dụng 10 chữ số thập phân để cover giá trị của các coin nhỏ,
        # tránh bị chuyển sang số mũ khoa học (e.g. 1e-05)
        s = f"{price:.10f}"
        
        # Xóa các số 0 vô nghĩa ở đuôi
        s = s.rstrip('0')
        
        # Nếu cắt hết số 0 mà dư dấu chấm thì thêm '0' (ví dụ "12." -> "12.0")
        if s.endswith('.'):
            s += '0'
            
        return s

    @staticmethod
    def _get_d3(state: SymbolState):
        """Hồ sơ Khung 3D: ưu tiên từ MacroLevels, thiếu thì tính từ klines_cache (memo, 0 API call)."""
        try:
            d3 = state.macro_levels.d3 if state.macro_levels is not None else None
            if d3:
                return d3
            from core.macro_levels import get_3d_profile
            return get_3d_profile(state.symbol)
        except Exception:
            return None

    def _d3_suffix(self, state: SymbolState) -> str:
        """Tag tóm tắt 3D nối vào cuối dòng [Macro 4H]."""
        try:
            from core.macro_levels import format_3d_summary
            return format_3d_summary(self._get_d3(state), self.fmt_price)
        except Exception:
            return ""

    @staticmethod
    def _strict_tag(state: SymbolState, engine: str, kind: str) -> str:
        """🛡️ Cổng kiểm dịch Strict 3D: trả về tag điểm gắn cuối dòng lưới + tự ghi sổ nếu < 75đ.
        ✅ ≥75đ (cấp phép) | 🟡 50–74đ (Watchlist) | ⛔ <50đ (loại). Lý do chi tiết → BẢNG TỔNG KẾT TỐI ƯU (mục 4)."""
        try:
            from core.macro_levels import strict_3d_gate, strict_3d_short_tag
            res = strict_3d_gate(state.symbol, engine=engine or "?", kind=kind, d3=ConsoleRenderer._get_d3(state))
            return strict_3d_short_tag(res)
        except Exception:
            return "⛔ [3D STRICT]"

    @staticmethod
    def _print_wide_grid(state: SymbolState, engine: str = "") -> str:
        """In `🦅 [WIDE GRID 3D - {symbol}]` (28L, vốn 1.000 USDT) cho mã close > MA7 3D & vol 24h ≥ 12M,
        gắn tag Strict 3D (✅ / ⛔) cuối dòng. Trả về chuỗi đã in (rỗng nếu không in) để đưa vào signals_pool."""
        try:
            d3 = ConsoleRenderer._get_d3(state)
            wg = (d3 or {}).get("wide_grid")
            if not wg or not wg.get("valid"):
                return ""
            tag = ConsoleRenderer._strict_tag(state, engine, "WIDE GRID 3D")
            from core.grid_tp2_builder import _make_formatter, _clean_symbol
            f = _make_formatter(d3.get("tick_size") or None)
            sym = _clean_symbol(state.symbol)
            line = (f"   ↳ 🦅 [WIDE GRID 3D - {sym}] {f(wg['wide_low'])} - {f(wg['wide_up'])} | {wg['wide_grids']}L | "
                    f"Trig: {f(wg['wide_trig'])} | SL: {f(wg['wide_sl'])} | TP: {f(wg['wide_up'])} "
                    f"(Vốn {wg['capital']:.0f}$ ≈ {wg['per_grid_usdt']}$/lưới) | {tag}")
            print(line)
            return line
        except Exception as e:
            print(f"   ↳ 🦅 [WIDE GRID 3D] Render Error: {e}")
            return ""

    @staticmethod
    def _print_grid_tp2(state: SymbolState, entry: float, sl_short: float, tp2_target: float, fallback_ratio: float,
                        engine: str = "") -> str:
        """In dòng cài đặt nhanh Spot Grid `🎯 [GRID TP2 - {symbol}]`. Fail-safe: lỗi không làm vỡ bảng.
        Gắn tag Strict 3D (✅ / ⛔) cuối dòng; mã ⛔ bị Bảng Tổng Kết loại khỏi danh sách dựng lưới.
        Trả về chuỗi đã in (rỗng nếu không dựng được lưới) để đưa vào signals_pool."""
        try:
            strict_tag = ConsoleRenderer._strict_tag(state, engine, "GRID TP2")
            tick_size = None
            try:
                from core.exchange_info_cache import ExchangeInfoCache
                cache = ExchangeInfoCache()
                if cache.is_ready():
                    tick_size = cache.get_tick_size(state.symbol.replace('/', ''), default=0.0) or None
            except Exception:
                tick_size = None

            # [3D ROUTER] Chiến thuật 3 — Khóa cứng trần TP2 ≤ Cản vĩ mô 3D × 1.015
            d3 = ConsoleRenderer._get_d3(state)
            cap_note = ""
            try:
                from core.macro_levels import apply_3d_tp2_cap
                capped = apply_3d_tp2_cap(tp2_target, d3)
                if capped and tp2_target and capped < tp2_target:
                    cap_note = f" [🔒 Khóa TP2 ≤ {d3['overhead_cap_name']}×1.015]"
                    tp2_target = capped
            except Exception:
                pass

            macro_sl = state.macro_levels.sl_4h if state.macro_levels else None
            payload = build_macro_grid_payload(
                symbol=state.symbol,
                entry=entry,
                sl_short=sl_short,
                tp_target=tp2_target,
                macro_sl=macro_sl,
                tick_size=tick_size,
                fallback_ratio=fallback_ratio,
            )
            if payload:
                line = payload["display_line"] + cap_note + f" | {strict_tag}"
                print(line)
                return line
            elif cap_note:
                print(f"   ↳ 🎯 [GRID TP2] ⚠️ Trần TP2 bị Cản 3D khóa ({ConsoleRenderer.fmt_price(tp2_target)}) ≤ Trig — KHÔNG dựng lưới")
        except Exception as e:
            print(f"   ↳ 🎯 [GRID TP2] Render Error: {e}")
        return ""

    def render_early_warning_matrix(self, warning_results: List[Dict[str, Any]], total_scanned: int):
        """Render Bảng Cảnh Báo Sớm"""
        try:
            print("\n" + "!" * 80)
            print(f"🚨 HỆ THỐNG CẢNH BÁO SỚM & RỦI RO SẬP")
            print("!" * 80)
            print(f"| {'Mức Độ (Level)':<30} | {'Tín Hiệu':<25} | {'Danh Sách Mã'}")
            print(f"|{'-'*32}|{'-'*27}|{'-'*20}")
            
            filtered_warnings = [r for r in warning_results if r.get('level') in (1, 2, 3)]
            if filtered_warnings:
                from collections import defaultdict
                grouped = defaultdict(list)
                for res in filtered_warnings:
                    key = (res.get('label', ''), res.get('trigger', ''))
                    grouped[key].append(res.get('symbol', '').replace('/USDT', ''))
                
                for (lbl, trig), symbols in grouped.items():
                    sym_str = ", ".join(symbols)
                    count = len(symbols)
                    lbl_with_count = f"{lbl} ({count}/{total_scanned})"
                    print(f"| {lbl_with_count:<30} | {trig:<25} | {sym_str}")
            else:
                print(f"| {'(Không có mã nào)':<30} | {'-':<25} | {'-'}")
            print("!" * 80)
        except Exception as e:
            print(f"Render Error (Early Warning): {e}")

    def render_darvas_grid(self, symbol_states: List[SymbolState]):
        """Render Động Cơ 1: Darvas Grid"""
        try:
            print("\n" + "=" * 80)
            print(f"📦 ĐỘNG CƠ 1: DARVAS GRID")
            print("=" * 80)
            print(f"{'Mã':<10} | {'Điểm':<6} | {'Trạng Thái':<20} | {'Hành Động'}")
            print("| --- | --- | --- | --- |")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC1")
                if not score_ctx:
                    continue
                
                sym = state.symbol.replace('/USDT', '')
                score = score_ctx.total_score
                act = score_ctx.action_label
                safe_tag = state.safety_tag[:20] if state.safety_tag else ""
                
                print(f"{sym:<10} | {score:<6} | {safe_tag:<20} | {act}")
                if score >= 60 and score_ctx.grid_setup:
                    g_setup = score_ctx.grid_setup
                    sl = g_setup.stop_loss
                    tp = g_setup.take_profit
                    strict_tag = self._strict_tag(state, "DC1", "DARVAS")
                    if g_setup.is_dual_grid:
                        print(f"  ↳ ⚙️ DUAL: SL={sl}|TP={tp} | G1:{g_setup.g1_lower}-{g_setup.g1_upper}({g_setup.g1_grids}L) | G2:{g_setup.g2_lower}-{g_setup.g2_upper}({g_setup.g2_grids}L) | {strict_tag}")
                    else:
                        print(f"  ↳ ⚙️ SETUP: L={g_setup.lower_price}|U={g_setup.upper_price}|G={g_setup.grid_quantity}|SL={sl}|TP={tp} | {strict_tag}")
                        
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {macro.entry_4h:<10} | SL Cứng: {macro.sl_4h:<10} | TP 1H: {macro.tp_1h:<10} | TP 4H: {macro.tp_4h:<10}{self._d3_suffix(state)}")

                # 🦅 WIDE GRID 3D (DC1)
                wide_line = self._print_wide_grid(state, "DC1")
                self._collect(state, "DC1", score_ctx, wide_grid_line=wide_line)

            print("=" * 80)
        except Exception as e:
            print(f"Render Error (Darvas Grid): {e}")

    def render_pullback_sniper(self, symbol_states: List[SymbolState]):
        """Render Động Cơ 2: Pullback Sniper"""
        try:
            print("\n" + "=" * 100)
            print(f"🎯 PULLBACK SNIPER (ĐỘNG CƠ 2)")
            print("=" * 100)
            print(f"{'Mã':<8} | {'Điểm':<5} | {'Trạng Thái':<15} | {'C0':<4} | {'C1':<4} | {'C2':<4} | {'C3':<4} | {'C4':<4} | {'Hành Động'}")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC2")
                if not score_ctx:
                    continue
                    
                sym = state.symbol.replace('/USDT', '')
                score = score_ctx.total_score
                act = score_ctx.action_label
                safe_tag = state.safety_tag[:15] if state.safety_tag else ""
                
                c1 = score_ctx.c1_score
                c2 = score_ctx.c2_score
                c3 = score_ctx.c3_score
                c4 = score_ctx.c4_score
                c0 = score_ctx.bonus_score
                c0_str = f"+{c0}" if isinstance(c0, (int, float)) and c0 > 0 else str(c0 if c0 != '-' else 0)
                
                print(f"{sym:<8} | {score:<5} | {safe_tag:<15} | {c0_str:<4} | {c1:<4} | {c2:<4} | {c3:<4} | {c4:<4} | {act}")
                
                # In Early Warning
                if score_ctx.early_warning:
                    ew = score_ctx.early_warning
                    force_tag = " [⚠️ F-CON]" if ew.force_conservative else ""
                    print(f"   ↳ [{sym}] 🛡️ EW: {ew.ew_label}{force_tag} | PB={ew.pullback_score} | W={ew.c1_wick_score} D={ew.c2_micro_dryup_score} M={ew.c3_macro_momentum_score} TB={ew.c4_taker_buy_score}")
                    if ew.ew_level == 1:
                        triggers = " | ".join(ew.triggers)
                        print(f"   ↳ [{sym}] ⛔ [EW1 REJ] {triggers}")

                # In Entry Setup (OCO)
                if score_ctx.entry_setup1:
                    s1 = score_ctx.entry_setup1
                    sl1_pct = (s1.entry_price - s1.sl_price) / s1.entry_price * 100 if s1.entry_price else 0
                    tp1_pct = (s1.tp1_price - s1.entry_price) / s1.entry_price * 100 if s1.entry_price else 0
                    print(f"   ↳ [{sym}] OCO-1: Buy={self.fmt_price(s1.entry_price)} | SL={self.fmt_price(s1.sl_price)}(-{sl1_pct:.1f}%) | TP={self.fmt_price(s1.tp1_price)}(+{tp1_pct:.1f}%) | R/R=1:{s1.rr_ratio:.1f}")
                
                if score_ctx.entry_setup2:
                    s2 = score_ctx.entry_setup2
                    sl2_pct = (s2.entry_price - s2.sl_price) / s2.entry_price * 100 if s2.entry_price else 0
                    tp2_pct = (s2.tp1_price - s2.entry_price) / s2.entry_price * 100 if s2.entry_price else 0
                    print(f"   ↳ [{sym}] OCO-2: Buy={self.fmt_price(s2.entry_price)} | SL={self.fmt_price(s2.sl_price)}(-{sl2_pct:.1f}%) | TP={self.fmt_price(s2.tp1_price)}(+{tp2_pct:.1f}%) | R/R=1:{s2.rr_ratio:.1f} | Trail={s2.trailing_trigger}")
                
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [{sym}] [Macro 4H] Entry: {macro.entry_4h:<10} | SL Cứng: {macro.sl_4h:<10} | TP 1H: {macro.tp_1h:<10} | TP 4H: {macro.tp_4h:<10}{self._d3_suffix(state)}")

                # GRID TP2 (DC2): Trig=OCO-2 Buy | SL ngắn=OCO-2 SL | Trần=OCO-2 TP | Fallback 0.96
                # OCO-2 bị ẩn → fallback OCO-1 để 100% mã có setup đều in GRID TP2
                s_grid = score_ctx.entry_setup2 or score_ctx.entry_setup1
                grid_line = ""
                if s_grid:
                    grid_line = self._print_grid_tp2(state, s_grid.entry_price, s_grid.sl_price, s_grid.tp1_price, fallback_ratio=0.96, engine="DC2")

                # 🦅 WIDE GRID 3D (DC2)
                wide_line = self._print_wide_grid(state, "DC2")

                # Summary Board: DC2 Spot dùng OCO-1 → chuẩn hóa về định dạng `⚙️ SETUP:`
                setup_line = ""
                if score_ctx.entry_setup1:
                    s1 = score_ctx.entry_setup1
                    setup_line = f"⚙️ SETUP: In={self.fmt_price(s1.entry_price)} | SL={self.fmt_price(s1.sl_price)}(-{sl1_pct:.1f}%) | TP={self.fmt_price(s1.tp1_price)}(+{tp1_pct:.1f}%) | R/R=1:{s1.rr_ratio:.1f}"
                self._collect(state, "DC2", score_ctx, setup=score_ctx.entry_setup1, setup_line=setup_line,
                              grid_tp2_line=grid_line, wide_grid_line=wide_line)

                if score >= 70:
                    print("-" * 100)
            
            print("=" * 100)
        except Exception as e:
            print(f"Render Error (Pullback Sniper): {e}")

    def render_momentum_breakout(self, symbol_states: List[SymbolState], btc_gate_label: str):
        """Render Động Cơ 3: Momentum Breakout"""
        try:
            print("\n" + "=" * 100)
            print(f"🚀 MOMENTUM BREAKOUT (ĐỘNG CƠ 3) | BTC: {btc_gate_label}")
            print("=" * 100)
            print(f"{'Mã':<8} | {'Điểm':<5} | {'C1(PA)':<10} | {'C2(Vol)':<10} | {'C3(OB)':<10} | {'C4(R/R)':<10} | {'Bonus':<6} | {'Hành Động'}")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC3")
                if not score_ctx:
                    continue
                
                sym = state.symbol.replace('/USDT', '')
                score = score_ctx.total_score
                act = score_ctx.action_label
                c1 = score_ctx.c1_score
                c2 = score_ctx.c2_score
                c3 = score_ctx.c3_score
                c4 = score_ctx.c4_score
                bonus = score_ctx.bonus_score
                
                flow = f" {state.money_flow_tag}" if state.money_flow_tag else ""
                print(f"{sym:<8}{flow} | {score:<5} | {c1:<10} | {c2:<10} | {c3:<10} | {c4:<10} | {bonus:<6} | {act}")
                
                # In Entry Setup
                setup_line = ""
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    if setup.setup_type == "MOCK_SCALE_OUT":
                        sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp2_pct = (setup.tp2_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        setup_line = f"   ↳ ⚙️ SETUP: In={self.fmt_price(setup.entry_price)} | SL={self.fmt_price(setup.sl_price)}(-{sl_pct:.1f}%) | R/R=1:{setup.rr_ratio:.1f}"
                        print(setup_line)
                        print(f"       📄 [MOCK] TP1={self.fmt_price(setup.tp1_price)}(+{tp1_pct:.1f}%) | TP2={self.fmt_price(setup.tp2_price)}(+{tp2_pct:.1f}%)")
                    else:
                        sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        setup_line = f"   ↳ ⚙️ SETUP: In={self.fmt_price(setup.entry_price)} | TP1={self.fmt_price(setup.tp1_price)}(+{tp1_pct:.1f}%) | SL={self.fmt_price(setup.sl_price)}(-{sl_pct:.1f}%) | R/R=1:{setup.rr_ratio:.1f}"
                        print(setup_line)
                
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {self.fmt_price(macro.entry_4h):<10} | SL Cứng: {self.fmt_price(macro.sl_4h):<10} | TP 1H: {self.fmt_price(macro.tp_1h):<10} | TP 4H: {self.fmt_price(macro.tp_4h):<10}{self._d3_suffix(state)}")
                else:
                    _sfx = self._d3_suffix(state)
                    if _sfx:
                        print(f"    ↳ [Macro 4H] ⚠️ Không có dữ liệu Vĩ mô 4H{_sfx}")

                # GRID TP2 (DC3): Trig=In | SL ngắn=SL | Trần=TP2 (rỗng → TP1×1.05) | Fallback 0.95
                grid_line = ""
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    tp2_target = setup.tp2_price if setup.tp2_price and setup.tp2_price > 0 else setup.tp1_price * 1.05
                    grid_line = self._print_grid_tp2(state, setup.entry_price, setup.sl_price, tp2_target, fallback_ratio=0.95, engine="DC3")

                self._collect(state, "DC3", score_ctx, setup=score_ctx.entry_setup1, setup_line=setup_line, grid_tp2_line=grid_line)

                if score > 0:
                    print("-" * 100)
            print("=" * 100)
        except Exception as e:
            print(f"Render Error (Momentum Breakout): {e}")

    def render_hot_trend_pullback(self, symbol_states: List[SymbolState], htb_count: int, htb_threshold: float, tracking_list: List[str]):
        """Render Động Cơ 4: Hot Trend Pullback"""
        try:
            print("\n" + "=" * 100)
            print(f"🔥 HOT TREND PULLBACK (ĐỘNG CƠ 4) | Nguồn: {htb_count} mã (>={htb_threshold}%, vol>=3M)")
            print("=" * 100)
            
            if not symbol_states:
                print("⚠️ KHÔNG TÌM THẤY MÃ NÀO ĐỦ ĐIỀU KIỆN HOT TREND PULLBACK HIỆN TẠI.")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC4")
                if not score_ctx:
                    continue
                
                sym = state.symbol.replace('/USDT', '')
                score = score_ctx.total_score
                act = score_ctx.action_label
                c1 = score_ctx.c1_score
                c2 = score_ctx.c2_score
                c3 = score_ctx.c3_score
                c4 = score_ctx.c4_score
                c5 = score_ctx.bonus_score
                
                flow = f" {state.money_flow_tag}" if state.money_flow_tag else ""
                print(f"[{sym:<6}]{flow} Điểm: {score:<6} | RSI1H: {score_ctx.rsi_1h:<4.1f} | Pull%: {score_ctx.pullback_pct:<5.1f} | 🎯 {act}")
                print(f"   ↳ C1: {c1} | C2: {c2} | C3: {c3} | C4: {c4} | C5: {c5}")
                
                macro = state.macro_state
                if macro:
                    print(f"   ↳ 🌀 Macro: {macro.trend_label} | {macro.drop_180d_pct}% | {macro.ma_status}")
                
                setup_line = ""
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                    tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                    entry_lbl = "Trig" if setup.setup_type == "TRIGGER_DIP" else "In"
                    setup_line = f"   ↳ ⚙️ SETUP: {entry_lbl}={self.fmt_price(setup.entry_price)} | SL={self.fmt_price(setup.sl_price)}(-{sl_pct:.1f}%) | TP={self.fmt_price(setup.tp1_price)}(+{tp1_pct:.1f}%) | R/R=1:{setup.rr_ratio:.1f}"
                    print(setup_line)
                
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {self.fmt_price(macro.entry_4h):<10} | SL Cứng: {self.fmt_price(macro.sl_4h):<10} | TP 1H: {self.fmt_price(macro.tp_1h):<10} | TP 4H: {self.fmt_price(macro.tp_4h):<10}{self._d3_suffix(state)}")
                else:
                    print(f"    ↳ [Macro 4H] ⚠️ Không có dữ liệu Vĩ mô (Do API Rate Limit hoặc mã mới){self._d3_suffix(state)}")

                # GRID TP2 (DC4): Trig=In | SL ngắn=SL | Trần=max(TP, TP 4H) (khuyết 4H → TP×1.04) | Fallback 0.95
                grid_line = ""
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    macro_tp = state.macro_levels.tp_4h if state.macro_levels else None
                    if macro_tp and macro_tp > 0:
                        tp2_target = max(setup.tp1_price, macro_tp)
                    else:
                        tp2_target = setup.tp1_price * 1.04
                    grid_line = self._print_grid_tp2(state, setup.entry_price, setup.sl_price, tp2_target, fallback_ratio=0.95, engine="DC4")

                # 🦅 WIDE GRID 3D (DC4)
                wide_line = self._print_wide_grid(state, "DC4")
                self._collect(state, "DC4", score_ctx, setup=score_ctx.entry_setup1, setup_line=setup_line,
                              grid_tp2_line=grid_line, wide_grid_line=wide_line)

                print("-" * 100)
                
            if tracking_list:
                print("👀 THEO DÕI THÊM: " + ", ".join(tracking_list))
                
            print("=" * 100)
        except Exception as e:
            print(f"Render Error (Hot Trend Pullback): {e}")

    def render_coin_filter_results(self, filtered_lines: List[str]):
        """Render kết quả quét tổng quan (Coin Filter)"""
        try:
            if filtered_lines:
                print("\n" + "=" * 80)
                print("📊 KẾT QUẢ PHÂN TÍCH THỊ TRƯỜNG (COIN FILTER)")
                print("=" * 80)
                print("\n".join(filtered_lines))
        except Exception as e:
            print(f"Render Error (Coin Filter Results): {e}")

    def render_grid_pingpong(self, symbol_states: List[SymbolState]):
        """Render Động Cơ 5: Grid Pingpong"""
        try:
            print("\n" + "=" * 100)
            print(f"🏓 GRID PINGPONG (ĐỘNG CƠ 5)")
            print("=" * 100)
            
            if not symbol_states:
                print("⚠️ KHÔNG TÌM THẤY MÃ NÀO ĐỦ ĐIỀU KIỆN GRID PINGPONG UPTREND HIỆN TẠI.")
                print("=" * 100)
                return

            print(f"{'Mã':<8} | {'Điểm':<8} | {'Action':<15} | {'Bounces/Range':<20} | {'Center/Trigger/SL'}")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC5_UPTREND") or state.scores.get("DC5")
                if not score_ctx:
                    continue
                
                sym = state.symbol.replace('/USDT', '')
                score = score_ctx.total_score
                act = score_ctx.action_label
                c1 = score_ctx.c1_score # Bounces / Avg Range
                c2 = score_ctx.c2_score # Center
                c3 = score_ctx.c3_score # Trigger
                c4 = score_ctx.c4_score # SL
                
                info = f"{c2} | {c3} | {c4}"
                print(f"{sym:<8} | {score:<8.4f} | {act:<15} | {c1:<20} | {info}")
                
                # In Grid Setup (+ tag Strict 3D ✅ / ⛔)
                g_setup = score_ctx.grid_setup
                if g_setup:
                    grid_label = "GRID ⚡" if g_setup.grid_quantity == 2 else "GRID"
                    strict_tag = self._strict_tag(state, "DC5", "PINGPONG")
                    print(f"  ↳ ⚙️ {grid_label}: Low={self.fmt_price(g_setup.lower_price)} | Up={self.fmt_price(g_setup.upper_price)} | Lưới={g_setup.grid_quantity} | SL Sell={self.fmt_price(g_setup.stop_loss)} | {strict_tag}")
                    
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {self.fmt_price(macro.entry_4h):<10} | SL Cứng: {self.fmt_price(macro.sl_4h):<10} | TP 1H: {self.fmt_price(macro.tp_1h):<10} | TP 4H: {self.fmt_price(macro.tp_4h):<10}{self._d3_suffix(state)}")
                else:
                    print(f"    ↳ [Macro 4H] ⚠️ Không có dữ liệu Vĩ mô (Do API Rate Limit hoặc mã mới){self._d3_suffix(state)}")

                # 🦅 WIDE GRID 3D (DC5)
                wide_line = self._print_wide_grid(state, "DC5")
                self._collect(state, "DC5", score_ctx, wide_grid_line=wide_line)

            print("=" * 100)
        except Exception as e:
            print(f"Render Error (Grid Pingpong): {e}")
