import pandas as pd
from typing import List, Dict, Any
from models.market_state import SymbolState, ScoreContext, GridContext, EarlyWarningContext, EntrySetupContext, MacroState

class ConsoleRenderer:
    """
    Lớp chuyên trách xử lý hiển thị ra màn hình Console.
    Đảm bảo Fail-Safe bằng cách bọc toàn bộ trong try-except.
    """
    
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
                    if g_setup.is_dual_grid:
                        print(f"  ↳ ⚙️ DUAL: SL={sl}|TP={tp} | G1:{g_setup.g1_lower}-{g_setup.g1_upper}({g_setup.g1_grids}L) | G2:{g_setup.g2_lower}-{g_setup.g2_upper}({g_setup.g2_grids}L)")
                    else:
                        print(f"  ↳ ⚙️ SETUP: L={g_setup.lower_price}|U={g_setup.upper_price}|G={g_setup.grid_quantity}|SL={sl}|TP={tp}")
                        
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {macro.entry_4h:<10} | SL Cứng: {macro.sl_4h:<10} | TP 1H: {macro.tp_1h:<10} | TP 4H: {macro.tp_4h:<10}")

            print("=" * 80)
        except Exception as e:
            print(f"Render Error (Darvas Grid): {e}")

    def render_pullback_sniper(self, symbol_states: List[SymbolState]):
        """Render Động Cơ 2: Pullback Sniper"""
        try:
            print("\n" + "=" * 100)
            print(f"🎯 PULLBACK SNIPER (ĐỘNG CƠ 2)")
            print("=" * 100)
            print(f"{'Mã':<8} | {'Điểm':<5} | {'Trạng Thái':<15} | {'C1':<4} | {'C2':<4} | {'C3':<4} | {'C4':<4} | {'Hành Động'}")
            
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
                
                print(f"{sym:<8} | {score:<5} | {safe_tag:<15} | {c1:<4} | {c2:<4} | {c3:<4} | {c4:<4} | {act}")
                
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
                    print(f"    ↳ [{sym}] [Macro 4H] Entry: {macro.entry_4h:<10} | SL Cứng: {macro.sl_4h:<10} | TP 1H: {macro.tp_1h:<10} | TP 4H: {macro.tp_4h:<10}")

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
                
                print(f"{sym:<8} | {score:<5} | {c1:<10} | {c2:<10} | {c3:<10} | {c4:<10} | {bonus:<6} | {act}")
                
                # In Entry Setup
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    if setup.setup_type == "MOCK_SCALE_OUT":
                        sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp2_pct = (setup.tp2_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        print(f"   ↳ ⚙️ SETUP: In={self.fmt_price(setup.entry_price)} | SL={self.fmt_price(setup.sl_price)}(-{sl_pct:.1f}%) | R/R=1:{setup.rr_ratio:.1f}")
                        print(f"       📄 [MOCK] TP1={self.fmt_price(setup.tp1_price)}(+{tp1_pct:.1f}%) | TP2={self.fmt_price(setup.tp2_price)}(+{tp2_pct:.1f}%)")
                    else:
                        sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        print(f"   ↳ ⚙️ SETUP: In={self.fmt_price(setup.entry_price)} | TP1={self.fmt_price(setup.tp1_price)}(+{tp1_pct:.1f}%) | SL={self.fmt_price(setup.sl_price)}(-{sl_pct:.1f}%) | R/R=1:{setup.rr_ratio:.1f}")
                
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {self.fmt_price(macro.entry_4h):<10} | SL Cứng: {self.fmt_price(macro.sl_4h):<10} | TP 1H: {self.fmt_price(macro.tp_1h):<10} | TP 4H: {self.fmt_price(macro.tp_4h):<10}")

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
                
                print(f"[{sym:<6}] Điểm: {score:<6} | RSI1H: {score_ctx.rsi_1h:<4.1f} | Pull%: {score_ctx.pullback_pct:<5.1f} | 🎯 {act}")
                print(f"   ↳ C1: {c1} | C2: {c2} | C3: {c3} | C4: {c4} | C5: {c5}")
                
                macro = state.macro_state
                if macro:
                    print(f"   ↳ 🌀 Macro: {macro.trend_label} | {macro.drop_180d_pct}% | {macro.ma_status}")
                
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                    tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                    print(f"   ↳ ⚙️ SETUP: In={self.fmt_price(setup.entry_price)} | SL={self.fmt_price(setup.sl_price)}(-{sl_pct:.1f}%) | TP={self.fmt_price(setup.tp1_price)}(+{tp1_pct:.1f}%) | R/R=1:{setup.rr_ratio:.1f}")
                
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {self.fmt_price(macro.entry_4h):<10} | SL Cứng: {self.fmt_price(macro.sl_4h):<10} | TP 1H: {self.fmt_price(macro.tp_1h):<10} | TP 4H: {self.fmt_price(macro.tp_4h):<10}")
                else:
                    print(f"    ↳ [Macro 4H] ⚠️ Không có dữ liệu Vĩ mô (Do API Rate Limit hoặc mã mới)")

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
                
                # In Grid Setup
                g_setup = score_ctx.grid_setup
                if g_setup:
                    grid_label = "GRID ⚡" if g_setup.grid_quantity == 2 else "GRID"
                    print(f"  ↳ ⚙️ {grid_label}: Low={self.fmt_price(g_setup.lower_price)} | Up={self.fmt_price(g_setup.upper_price)} | Lưới={g_setup.grid_quantity} | SL Sell={self.fmt_price(g_setup.stop_loss)}")
                    
                if state.macro_levels:
                    macro = state.macro_levels
                    print(f"    ↳ [Macro 4H] Entry: {self.fmt_price(macro.entry_4h):<10} | SL Cứng: {self.fmt_price(macro.sl_4h):<10} | TP 1H: {self.fmt_price(macro.tp_1h):<10} | TP 4H: {self.fmt_price(macro.tp_4h):<10}")
                else:
                    print(f"    ↳ [Macro 4H] ⚠️ Không có dữ liệu Vĩ mô (Do API Rate Limit hoặc mã mới)")

            print("=" * 100)
        except Exception as e:
            print(f"Render Error (Grid Pingpong): {e}")
